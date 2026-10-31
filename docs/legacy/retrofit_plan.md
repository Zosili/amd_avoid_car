# 三人改造方案

> 2026-10-24 20:30　｜　contract v2　｜　架构：雷达接板子 · 决策在 PS · YOLO 在 PC
> 本次新增：抗误检机制（分扇区连续确认 + R2 置信度门槛）已落地进 `zheng/fusion/machine.py`

## 总览

```
欧阳（PC）  ──视觉结果 UDP──▶  郑（板子 PS）  ──AXI 寄存器──▶  徐（PL）
   YOLO 推理                     五规则决策                    电机 PWM
```

三人可以**并行开工**，因为契约（`integration/contract.py`）已经把接口定死了。

> 📋 **要按任务清单执行看 `三人分工_详细任务清单.md`** —— 那份把每个人拆到 O-1/O-2/X-1/X-2/Z-1/Z-2 这种编号任务，带文件路径、命令和验收标准。本文只讲"改什么、为什么改"。

| 人 | 现在卡在哪 | 要改成什么 | 谁能立刻开工 |
|---|---|---|---|
| 欧阳 | 输出是分类、没框、2Hz | 输出检测框、**真实置信度**、≥7Hz、发给 PS | 是（不依赖别人） |
| 徐 | 无源码、无 AXI | 交源码 + 换 `motor_axi.v` | 是（不依赖别人） |
| 郑 | 串口是 COM5、电机走 TCP | `/dev/ttyUSB0` + AXI；**抗误检已改完** | 是（mock 模式） |

⚠️ **本次新增的一条隐性要求**：郑的决策已经用上"置信度门槛"（`vision_single_conf=0.60`）。欧阳必须发**真实的 YOLO score**，不能填死 `1.0` 或 `0.99` —— 填死等于把这道防线拆了。

---

# 一、欧阳的改造（PC 侧）

## 现状

文件：`ouyang/42_ov5640_udp_pc/42_ov5640_udp_pc/ryzenai/main_ryzenai.py`

```python
MODEL_PATH = r"C:\Program Files\RyzenAI\1.6.0\quicktest\test_model.onnx"   # 32×32 分类模板
INPUT_H = INPUT_W = 32
CLASS_NAMES = [f"class{i}" for i in range(10)]                              # 假类别
...
if now - last_infer >= 0.5:          # ← 2Hz，会导致 70% 时间停车
    ...
    send_result(s, last_class, last_score)   # ← 发分类结果给 PL 网口，郑收不到
```

`yolo_test.py` 里有**正确的 YOLO 后处理**（`letterbox` + `postprocess`），但只跑 CPU 单张图，没进实时循环。

## 要改的五处

### ① 换模型和输入尺寸

```python
MODEL_PATH = r"D:\FPGA\42_ov5640_udp_pc\yolo11n.onnx"     # 用你 yolo_test.py 里的那个
INPUT_H = INPUT_W = 640
CLASS_NAMES = COCO_NAMES      # 直接 import yolo_test 里的那个 80 类列表
```

### ② 把后处理搬进循环

从 `yolo_test.py` 复制这两个函数到 `main_ryzenai.py`（一行不用改）：
- `letterbox(img, size=640, stride=32)`
- `postprocess(pred, r, pad, conf_th, nms_th, img_h, img_w)`

推理部分改成：

```python
img, r, pad = letterbox(bgr, 640, 32)
x = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
out = npu.run({npu.input_names[0]: x[None]})[0]
boxes, classes, scores = postprocess(out, r, pad, 0.25, 0.45, 480, 640)
```

⚠️ `postprocess` 返回的框**已经是原图 640×480 像素坐标**（它内部做了 letterbox 还原），直接传给发送器，不要再转。

### ③ 换掉发送逻辑

```python
# 删掉这个
def send_result(sock, class_id, score): ...

# 换成这个
from vision_sender import VisionSender
sender = VisionSender("192.168.1.20", 5000, class_names=CLASS_NAMES)

# 循环里：
sender.send(boxes, classes, scores, 640, 480)
```

`vision_sender.py` 已经放在 `integration/` 里，复制到你的目录即可（只依赖标准库）。

### ④ 推理频率提到 ≥7Hz

**删掉 `if now - last_infer >= 0.5:` 这个门限**，改成每帧都推理。

如果 NPU 跑不到 7Hz，按这个顺序优化：
1. RGB565 解码（`rgb565_to_bgr`）现在用 numpy 逐位运算，可以改成查表
2. `cv2.resize` 到 640 前先降采样
3. 确认 `npu.run()` 走的是 `VitisAIExecutionProvider` 而不是 CPU

### ⑤ 置信度必须是真实的

郑的决策里有一道**置信度门槛**：视觉单源（雷达没回波）时，只有 `score >= 0.60` 才允许转向避让；低于这个值只减速不转向。

所以 `scores` 必须是 `postprocess` 输出的原始置信度：

```python
boxes, classes, scores = postprocess(out, r, pad, 0.25, 0.45, 480, 640)
#                          ↑ conf_th 就设 0.25，不要为了"看起来准"往上调
sender.send(boxes, classes, scores, 640, 480)   # scores 原样传，别乘系数、别截断
```

三个**不能做**的事：

| 做法 | 后果 |
|---|---|
| `scores = [1.0] * len(boxes)` | 门槛失效，低质量框也能触发转向 |
| 把 `conf_th` 提到 0.6 再发 | 和门槛重复过滤，等于把阈值变成 0.6 且没有分级 |
| 对 score 做归一化/缩放 | 语义漂移，郑那边按 0~1 原始概率判断 |

`vision_sender.py` 里对 `score` 做了 `0~1` 范围检查，超范围会抛 `ValueError` —— 这是故意的，别去改它。

## 验收标准

在 PC 上跑：
```bash
cd D:/dasanshang/fpga竞赛/integration
python vision_receiver.py --port 5000
```

看到这样的输出才算过：

```
[   10s] 收包  120 12.0Hz 坏JSON  0 拒收  0 | 最新: person (25ms 前)
```

四条硬指标：
- **频率 ≥ 7Hz**（低于会打印告警）
- **拒收 = 0**（说明 schema 全对）
- 画面里人走动时，`最新:` 的类别跟着变
- **置信度是真实浮点值**：把接收器打到 `--verbose`，`conf` 字段应该在 `0.25~0.99` 之间浮动，**不是一个固定的数**

验证置信度是否真实的最快办法：拿一个只露半边的物体（或者远处的小目标），它的 score 应该明显低于画面中央完整目标。如果两者都是 `0.99`，说明你在某处写死了。

---

# 二、徐的改造（PL 侧）

## 现状

`xu/project_1/` 里**只有 `motor_diff.bit` 和 `.dcp`，没有 RTL 源码**。
xdc 里的 `SRC_FILE_INFO` 指向 `C:/Users/Acer/WorkBuddy/2026-10-05-14-38-25/motor_pwm/`，源码在你另一台机器上。

现有引脚（从综合后的 xdc 提取）：

| 端口 | 引脚 |
|---|---|
| `clk` | `U18` |
| `rst_n` | `N16` |
| `pwm_l` | `U7` |
| `in1` | `T9` |
| `in2` | `Y7` |
| `pwm_r` | `V8` |
| `in3` | `V6` |
| `in4` | `Y9` |

## 要做的三步

### ① 交出源码

`motor_diff.v` + `motor_pins.xdc` 复制到 `xu/` 目录下。**这一步不做，阶段 2 无法开始。**

### ② 用 `motor_axi.v` 替代

已经写好并**通过 Vivado 2025.2 的 xvlog + xelab 双重检查**：
`integration/motor_axi.v`

它做了三件 `motor_diff` 没做的事：

| 功能 | 说明 |
|---|---|
| AXI4-Lite 从机 | PS 能读写 7 个寄存器 |
| 可变占空比 PWM | 20kHz 载波，1000 级分辨率，正负号表方向 |
| **硬件看门狗** | PS 300ms 不写心跳 → **自动停车**（PS 死机也刹得住） |

寄存器映射：

| 偏移 | 名称 | 方向 | 含义 |
|---|---|---|---|
| `0x00` | `CTRL` | W | bit0=使能（0=强制停车） |
| `0x04` | `SPEED_L` | W | 有符号 16 位，-1000~+1000 |
| `0x08` | `SPEED_R` | W | 同上 |
| `0x0C` | `HEARTBEAT` | W | 每 100ms 写一次递增数 |
| `0x10` | `ENC_L` | R | 左编码器计数（4 倍频） |
| `0x14` | `ENC_R` | R | 右编码器计数 |
| `0x18` | `STATUS` | R | bit0=看门狗超时 bit1=急停锁死 bit2=使能中 |

### ③ 搭 Block Design

1. 新建 Vivado 工程，Part 选 `xc7z020clg400-2`
2. `Create Block Design` → 加 `ZYNQ7 Processing System`
3. 双击 PS 配置：
   - `PS-PL Configuration` → 使能 `M_AXI_GP0`
   - `Clock Configuration` → `FCLK_CLK0` 设为 **50MHz**（`motor_axi` 的 `CLK_HZ` 参数必须和它一致）
   - `Peripheral I/O Pins` → 使能 `UART1`（如果雷达走 PS 的串口）
   - `Peripheral I/O Pins` → 使能 `ENET0`（PS 网口，给郑收视觉结果用）
4. `Run Connection Automation` → 把 `motor_axi` 挂到 `M_AXI_GP0`，时钟接 `FCLK_CLK0`
5. Address Editor 里给 `motor_axi` 分配地址（默认 `0x43C00000`，**记下来告诉郑**）
6. 引脚约束（电机 6 根）：

```tcl
# motor_axi 的电机输出（沿用你原来的引脚）
set_property -dict {PACKAGE_PIN U7 IOSTANDARD LVCMOS33} [get_ports pwm_l]
set_property -dict {PACKAGE_PIN T9 IOSTANDARD LVCMOS33} [get_ports in1]
set_property -dict {PACKAGE_PIN Y7 IOSTANDARD LVCMOS33} [get_ports in2]
set_property -dict {PACKAGE_PIN V8 IOSTANDARD LVCMOS33} [get_ports pwm_r]
set_property -dict {PACKAGE_PIN V6 IOSTANDARD LVCMOS33} [get_ports in3]
set_property -dict {PACKAGE_PIN Y9 IOSTANDARD LVCMOS33} [get_ports in4]

# 编码器（按你实际接线改，没接就接 0）
# set_property -dict {PACKAGE_PIN ?? IOSTANDARD LVCMOS33} [get_ports enc_l_a]
```

⚠️ **`clk` / `rst_n` 不再需要引脚约束**——AXI 版本的时钟复位由 PS 提供（`FCLK_CLK0` / `peripheral_aresetn`）。
原来的 `U18`/`N16` 要留给顶层（欧阳的摄像头模块也要用这两个）。

## 验收标准

在板子上跑（`run_on_board.py --motor axi` 会自动写这些寄存器），或者手动测：

```python
from pynq import MMIO
m = MMIO(0x43C00000, 0x1000)
m.write(0x00, 1)        # 使能
m.write(0x04, 500)      # 左轮 50%
m.write(0x0C, 1)        # 心跳
print(hex(m.read(0x18)))  # 应看到 bit2=1（使能中）
```

三条硬指标：
1. 写 `0x04=500` → 左轮转；写 `0` → 停
2. 写 `-500` → 左轮反转
3. **停止写心跳 300ms → 电机自动停**（看门狗生效）

---

# 三、郑的改造（PS 侧）

## 现状

`zheng/run.py --source live` 是"没板子时的替身"：雷达开 `COM5`、视觉从 stdin 读、电机走 TCP。

板子上三样都不同：雷达是 `/dev/ttyUSB0`、视觉走 UDP、电机走 AXI。

## 要做的五步

> **先说结论**：前四步是部署，第五步是标定。中间的"抗误检机制"已经替你改完了，代码不用再动。

### ① 装依赖

```bash
pip3 install pyserial
```

（`pynq` 库是 PYNQ 镜像自带的，不用装）

### ② 传文件

```bash
# PC 上（Git Bash）
scp -r D:/dasanshang/fpga竞赛/zheng xilinx@192.168.2.99:/home/xilinx/
scp -r D:/dasanshang/fpga竞赛/integration xilinx@192.168.2.99:/home/xilinx/
```

或者用 Jupyter 网页（`http://192.168.2.99`）的 Upload 按钮拖上去。

### ③ 配 PS 网口 IP

```bash
sudo ip addr add 192.168.1.20/24 dev eth0
ping 192.168.1.102        # 通到 PC 就行
```

### ④ 用 `run_on_board.py` 启动

```bash
cd /home/xilinx/integration

# 第一步：mock 模式，验证雷达 + 视觉链路（不碰硬件）
python3 run_on_board.py --port /dev/ttyUSB0 --motor mock

# 第二步：确认雷达有数据、视觉有结果后，再真控电机
python3 run_on_board.py --port /dev/ttyUSB0 --motor axi --axi-base 0x43C00000
```

`run_on_board.py` 只做"接线"，决策逻辑仍然调 `zheng/fusion/machine.py`，**一行没改**。

### ⑤ 上车后标定这几个参数

**背景**（这块已经改完了，你只需要调参数）：原来三条规则是"单帧单源就能触发动作"，实车上会随机抽风 —— D6 有低置信点、YOLO 有门槛边缘的抖动框。现在改成：

| 规则 | 改造后的行为 |
|---|---|
| 侧向堵死（影响 R1 双确认与 R4 转向） | 连续 2 帧都堵才承认，单帧杂波被吃掉 |
| R2 正前方障碍 | **立即停车**（安全动作，不设门槛） |
| R2 侧向障碍 + `conf < 0.60` | 减速直行，不转向 |
| R2 侧向障碍 + `conf >= 0.60` + 连续 2 帧 | 才允许转向避让 |
| 雷达扇区距离 | 取第 2 小而不是最小（单点噪声不再能把 3m 拉成 0.2m） |
| 弱回波点 | 按无回波处理（用上 D6 自带的低置信标志位） |

核心原则：**停车是安全动作，误停只是慢；转向是危险动作，误转会撞东西。** 所以停车不等确认，转向必须双重确认。

这几个参数都在 `zheng/protocol.py` 的 `CFG` 里（`r3_confirm_frames` 是原有的，另外三个是本次新增）：

```python
"side_confirm_frames": 2,    # 侧向堵死需连续帧数
"r2_confirm_frames": 2,      # R2 转向需连续帧数
"vision_single_conf": 0.60,  # 视觉单源转向最低置信度
"r3_confirm_frames": 3,      # 雷达单源报警需连续帧数（原有，比另两个更严）
```

调法：先在场地里放**固定障碍**跑，确认不误停；再故意在雷达前面挥手、放薄纸片制造杂波，看会不会抽风。

⚠️ 这几个参数**和视觉频率强耦合**。`side_confirm_frames=2` 在 10Hz 下是 0.2 秒，在 2Hz 下就是 1 秒 —— 欧阳那边没提到 7Hz 之前，别急着调这几个数。

## 验收标准

`--motor mock` 模式下：

```
[   10] R5  STOP     前=None
[   20] OK  FORWARD  前=2.31
[   30] R2  RIGHT    前=None
```

四条硬指标：
1. `前=` 不是 `None` 时，说明雷达通了
2. 规则号会随障碍变化（不是一直 R5）
3. 日志写到 `integration/logs/board_*.jsonl`
4. **单帧杂波不会让车抖**：拿手在雷达前快速挥一下（0.1 秒），规则号不应该跳 R1/R3；人站在侧前方不动，2 帧内应该稳定进 R2/R3

`--motor axi` 模式下：电机真的转，且 Ctrl-C 后立刻停。

---

# 四、协作约定

## 契约改动规则

`integration/contract.py` 是三人共用的接口定义。**改任何一条规则**：

1. 把 `CONTRACT_VERSION` +1
2. 在群里说一声
3. 三方各自重跑 `python run_integration_demo.py`，全绿才算过

**`zheng/protocol.py::CFG` 里的参数改动也要通知**（尤其是 `vision_single_conf` 和三个 `*_confirm_frames`）—— 这几个数直接决定"欧阳发来的低置信度框会不会被采纳"、"单帧杂波会不会触发动作"，欧阳调模型阈值、徐调看门狗时都需要知道。

## 谁改哪部分

| 文件 | 归谁 | 别人不能改 |
|---|---|---|
| `contract.py` | **三人共管** | 改要通知 |
| `vision_sender.py` | 欧阳 | 郑可以提需求 |
| `run_on_board.py` / `motor_axi.v` | 郑写的 / 徐维护 | 郑可以改逻辑，徐改硬件参数 |
| `zheng/*` | 郑 | — |
| `xu/*` | 徐 | — |
| `ouyang/*` | 欧阳 | — |

## 依赖顺序

```
郑（mock 模式跑通）  ─┐
                     ├─▶ 阶段 2 合并工程 ─▶ 阶段 3 板子联调 ─▶ 阶段 4 实车
欧阳（视觉改完）    ─┤
徐（源码 + AXI）    ─┘
```

三个人**现在都能各自开工**，互不阻塞。真正需要三人同时在场的是阶段 2（合并 Vivado 工程）和阶段 4（实车标定）。

---

# 五、当前已完成的部分

| 项 | 状态 |
|---|---|
| 三方语义契约（差速映射 + 检测归一化） | ✅ 已实现并自测 |
| 7 场景纯软件联调（每场景 5 帧） | ✅ 规则号 `OK/R1/R2/R4/R1/R5/R4` 全对 |
| 假板子（TCP 收指令 → PWM） | ✅ 下行链路验证通过 |
| 视觉发送器（PC 端） | ✅ 已实现，假推理自测通过 |
| 视觉接收器（PS 端） | ✅ 已实现，12Hz 端到端验证通过 |
| 板子端主程序 | ✅ 已实现（mock/axi 双后端） |
| AXI 电机控制器 | ✅ **Vivado 2025.2 xvlog + xelab 双检查零警告** |
| 抗误检：分扇区连续确认 | ✅ 已落地，`machine.py` + 2 个测试 |
| 抗误检：R2 置信度门槛 | ✅ 已落地，`machine.py` + 2 个测试 |
| 点云抗噪：弱回波标志位 + 扇区取第 2 小 | ✅ 已落地，`d6_protocol.py` + 11 个测试 |
| 雷达线程负载优化（16.2 → 3.5 ms/s） | ✅ 已落地，`RECOMPUTE_HZ` + 单遍统计 + 查表 |
| 郑的决策测试 | ✅ **32 个全绿**（21 + 新增 11） |

### 这次改动带来的一个副作用

`run_integration_demo.py` 原来每个场景只跑 1 帧，看不出连续确认的效果（R2 第一帧永远不转向）。现在改成**每场景连跑 5 帧**，输出里的 `conf=0.81 x5` 就是确认计数在涨的证据。如果你看到 R2 场景第一帧是 `R4` 或者"减速直行"，第二帧才变 `RIGHT`，那是**正常的**，不是 bug。
