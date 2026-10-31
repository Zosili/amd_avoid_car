# 三人分工详细任务清单

> 2026-10-24 20:40　｜　配合 `README_整合方案.md`（架构）和 `改造方案_三人.md`（改动说明）使用
> 本文回答两个问题：**每个人具体做什么**、**什么时候必须合起来做**

---

# 零、先看这一页

## 三台机器，三份代码，三条线

```
┌─ PC（Windows） ────────────┐   ┌─ 板子 PL（Verilog）─┐   ┌─ 板子 PS（Linux）─┐
│                            │   │                     │   │                   │
│  欧阳：OV5640 收图          │   │  欧阳：摄像头采集    │   │  郑：雷达解析      │
│        YOLO 推理            │   │        UDP 发图      │   │      视觉接收      │
│        发视觉结果 ──────────┼───┼─────────────────────┼──▶│      五规则决策    │
│                            │   │  徐：电机 PWM        │   │      写 AXI 寄存器 │
│                            │   │      AXI4-Lite 从机 ◀┼───┼──────┘            │
└────────────────────────────┘   └─────────────────────┘   └───────────────────┘
        眼睛 + 大脑的一部分              手脚 + 眼睛的搬运工           大脑
```

**谁不需要等谁**：

| 人 | 现在能独立开工吗 | 卡在哪 |
|---|---|---|
| 欧阳 | ✅ 能 | 无（PC 上自己跑） |
| 徐 | ✅ 能 | 无（Vivado 自己跑） |
| 郑 | ✅ 能 | 无（mock 模式跑通全链路） |

三个人**只有两个时间点必须凑在一起**：节点 B（合工程）和节点 D（实车标定）。

---

# 一、合起来做什么（四个集成节点）

这四件事**一个人干不了**，必须按顺序过。每过一个节点，系统才多一个能力。

| 节点 | 名字 | 谁必须在场 | 需要多久 | 过了之后能干什么 |
|---|---|---|---|---|
| **A** | PC 单机联调 | 欧阳 + 郑 | 半天 | 视觉结果能进决策程序（不用板子） |
| **B** | PL 工程合并 | 徐（主导）+ 欧阳 | 1~2 天 | 一个 bit 同时驱动摄像头和电机 |
| **C** | 板子联调 | 郑 + 徐 | 1 天 | 雷达 + 视觉 + 电机在板子上跑通 |
| **D** | 实车标定 | 三人 | 2~3 天 | 车能自己跑 |

---

## 节点 A：PC 单机联调（欧阳 + 郑）

**目的**：在碰板子之前，先把"视觉结果 → 决策"这条链路在 PC 上跑通。板子出问题的时候，你能立刻判断是"视觉错了"还是"板子错了"。

**前置条件**：
- 欧阳改完 `main_ryzenai.py`（见 O-1 ~ O-5）
- 郑的 `vision_receiver.py` 已在 `integration/` 里（已有）

**两个人分别干什么**：

| 谁 | 动作 |
|---|---|
| 郑 | 在自己 PC 上跑 `python vision_receiver.py --port 5000`，把 `192.168.1.102` 告诉欧阳 |
| 欧阳 | 改 `vision_sender.py` 里的 `target_ip` 指向郑的机器，跑推理，人站在摄像头前左右走动 |
| 两人一起 | 盯接收器的输出，对不上就一起看 |

**验收（三条，必须同时满足）**：

```
[   10s] 收包  120 12.0Hz 坏JSON  0 拒收  0 | 最新: person (25ms 前)
```

1. 频率 ≥ 7Hz（低于会打印告警）
2. 拒收 = 0（schema 全对）
3. 人从左走到右，`最新:` 后面的类别跟着变；把 `--verbose` 打开，`conf` 是 0.25~0.99 的浮点，不是固定值

**失败回退**：
- 收不到包 → 先 `ping`，再查防火墙（Windows 默认拦入站 UDP）
- 有包但全拒收 → 拿 `integration/contract.py` 的 `detections_to_vision()` 当参照，逐字段对
- 频率不到 7Hz → 别急着改接收器，是欧阳那边的推理慢，回去优化（见 O-4）

---

## 节点 B：PL 工程合并（徐主导 + 欧阳）

**目的**：把欧阳的摄像头工程和徐的电机控制器合成**一个** Vivado 工程，出一个 bit。

**前置条件**：
- 徐已交 `motor_diff.v`（X-1，**阻塞级**）
- 欧阳的 RTL 已能作为子模块例化（O-6）

**谁干什么**：

| 谁 | 动作 |
|---|---|
| 徐 | 建工程、搭 Block Design、挂 AXI、分配地址、出 bit（X-2 ~ X-6） |
| 欧阳 | 提供 `ov5640_udp_pc` 的顶层端口清单，配合把 `sys_clk/sys_rst_n` 改成由外部传入（O-6） |
| 郑 | **不用到场**，但要提供地址确认：电机控制器基地址必须是 `0x43C00000` |

**关键冲突（必须当场解决）**：

| 冲突 | 现象 | 解法 |
|---|---|---|
| 引脚撞车 | 欧阳和徐都用 `U18`（clk）、`N16`（rst_n） | 这两个引脚只在**顶层**约束一次，两个子模块都用外部传入的时钟复位 |
| 时钟域 | 欧阳的模块自带 50MHz？徐的 `motor_axi` 参数 `CLK_HZ=50_000_000` | 统一用 PS 的 `FCLK_CLK0` = 50MHz，两个模块都接它 |
| Vivado 版本 | 欧阳是 2020.2 建的工程 | **不要直接打开升级**，新建工程手工搬 RTL |

**验收（三件事同时成立）**：

```python
# 板上验证（不需要 Overlay，直接 MMIO 就行）
from pynq import MMIO
m = MMIO(0x43C00000, 0x1000)
m.write(0x00, 1)       # 使能
m.write(0x04, 500)     # 左轮 50%
m.write(0x0C, 1)       # 心跳
print(hex(m.read(0x18)))   # 期望 0x4（bit2=使能中）
```

1. 摄像头能出图（PC 上 `view.py` 收到画面）
2. 电机能转，写 `0x04=500` 左轮动，写 `0` 停
3. **停止写心跳 300ms → 电机自动停**（看门狗生效）

**失败回退**：
- 综合过不了 → 先单独综合两个子模块，确认各自干净，再合并
- 出图但电机不动 → 查 Address Editor 里地址是不是 `0x43C00000`
- 电机转但方向反 → 改 `motor_axi.v` 里 `in1/in2` 的赋值顺序，不用改 xdc

---

## 节点 C：板子联调（郑 + 徐）

**目的**：把郑的 Python 搬上 PS，接上真实雷达和真实视觉。

**前置条件**：
- 节点 B 通过（有 bit + xsa）
- 郑的 `run_on_board.py` 已在 mock 模式下跑通

**谁干什么**：

| 谁 | 动作 |
|---|---|
| 徐 | 烧 bit、确认 PS 启动、告诉郑 AXI 基地址和寄存器行为 |
| 郑 | 传代码、配 PS 网口 IP、起 `vision_receiver.py`、跑 `run_on_board.py` |

**分三步走，每步都要停一下确认**：

```bash
# 第 1 步：只验雷达（电机不动）
python3 run_on_board.py --port /dev/ttyUSB0 --motor mock
#   期望：[  20] OK  FORWARD  前=2.31   ← 前= 不是 None 就说明雷达通了

# 第 2 步：只验视觉（雷达用 mock）
python3 run_on_board.py --radar mock --vision udp --motor mock
#   期望：人走近时规则号变 R2

# 第 3 步：全真（电机接上，车架空！）
python3 run_on_board.py --port /dev/ttyUSB0 --vision udp --motor axi
#   期望：电机随规则变化，Ctrl-C 立刻停
```

⚠️ **第 3 步必须把车架空**（轮子离地）。第一次转电机永远不要放地上。

**验收**：
1. `前=` 有真实数值，不是 `None`
2. 规则号随障碍变化，不是一直 R5
3. Ctrl-C 后电机立刻停（不是滑行）
4. 日志写到 `integration/logs/board_*.jsonl`

**失败回退**：
- 一直 R5 → 视觉没到。先在 PC 上 `ping 192.168.1.20`，再查 PS 的 `ip addr`
- 雷达 `前=None` → `ls /dev/ttyUSB*` 有没有设备；有设备但无数据就是线序反了（雷达 TXD 要接 PYNQ RXD）
- 电机不动 → 先 `m.read(0x18)` 看 bit2，再单独写 `0x04` 测

---

## 节点 D：实车标定（三人）

**目的**：车从"能动"变成"能跑"。

**这一阶段没有代码改动，全是调参数**。所以三个人必须同时在场，因为一个人看不到全部现象。

| 谁 | 负责什么 | 为什么是他 |
|---|---|---|
| 郑 | 雷达 0° 偏角、`TURN_GAIN`、危险距离、抗误检三参数 | 决策逻辑是他写的 |
| 徐 | PWM 满量程、电机死区、编码器方向 | 硬件是他做的 |
| 欧阳 | 视觉置信度门槛、识别距离、光照影响 | 模型是他训的 |

**标定顺序（不能颠倒）**：

| 步 | 标什么 | 怎么标 | 判据 |
|---|---|---|---|
| 1 | 雷达 0° 装车偏角 | `python calibrate_zero.py --survey 20`，车头正对墙 | 前扇区读数最小 |
| 2 | 电机死区 + 满量程 | 从 0 慢慢加 PWM，记下轮子刚转的数和跑最快不丢步的数 | 写进 `contract.py` 的 `DEADBAND_PWM` / `PWM_MAX` |
| 3 | `TURN_GAIN` | 原地打满方向，看转弯半径 | 不甩尾、能过最窄通道 |
| 4 | 视觉置信度 | 站远/站近、正脸/侧身，看 score 分布 | 定 `vision_single_conf` |
| 5 | 抗误检三参数 | 固定障碍跑一遍，再挥手制造杂波 | 不误停、不抽风 |
| 6 | 危险距离 | 全速冲墙，看刹车距离 | `danger_m` 略大于刹车距离 |

**验收（这是最终验收）**：
- 连续跑 5 分钟不撞、不抽风、不停死
- 三种场景全过：正面来障碍、侧向来障碍、低矮障碍（<26.9mm，雷达看不见，只能靠视觉）
- Ctrl-C / 拔网线 / 拔雷达，三种故障都能安全停车

---

# 二、欧阳的具体任务（PC 侧）

> 全部工作在 `ouyang/42_ov5640_udp_pc/42_ov5640_udp_pc/ryzenai/main_ryzenai.py`
> 参考文件：同目录的 `yolo_test.py`（有正确的后处理）

## 任务清单

| 编号 | 任务 | 改哪里 | 验收 |
|---|---|---|---|
| **O-1** | 换模型和输入尺寸 | `MODEL_PATH` / `INPUT_H` / `INPUT_W` / `CLASS_NAMES` | 能加载 yolo11n.onnx，不报 shape 错 |
| **O-2** | 后处理搬进实时循环 | 从 `yolo_test.py` 复制 `letterbox()` + `postprocess()` | 输出 `boxes/classes/scores` 三个数组 |
| **O-3** | 换成 `VisionSender` | 删 `send_result()`，改用 `vision_sender.send()` | 郑那边拒收 = 0 |
| **O-4** | 推理频率提到 ≥7Hz | 删掉 `if now - last_infer >= 0.5:` 门限 | `vision_receiver.py` 显示 ≥7Hz |
| **O-5** | 保证置信度真实 | `scores` 原样传，不写死、不缩放 | score 在 0.25~0.99 之间浮动 |
| **O-6** | RTL 改造成子模块（节点 B 前） | `ov5640_udp_pc.v` 顶层端口 | 能被别人例化，端口不含 clk/rst |

## O-1 换模型和输入尺寸

```python
# 原来（32×32 分类模板，是假的）
MODEL_PATH = r"C:\Program Files\RyzenAI\1.6.0\quicktest\test_model.onnx"
INPUT_H = INPUT_W = 32
CLASS_NAMES = [f"class{i}" for i in range(10)]

# 改成
MODEL_PATH = r"D:\FPGA\42_ov5640_udp_pc\yolo11n.onnx"    # 用你 yolo_test.py 里那个
INPUT_H = INPUT_W = 640
CLASS_NAMES = COCO_NAMES          # 直接从 yolo_test.py import
```

⚠️ 换成 640 输入后推理会变慢，这是正常的。O-4 就是为这个准备的。

## O-2 后处理搬进实时循环

从 `yolo_test.py` 原样复制这两个函数，**一行都不用改**：

- `letterbox(img, size=640, stride=32)` → 返回 `(img, r, pad)`
- `postprocess(pred, r, pad, conf_th, nms_th, img_h, img_w)` → 返回 `(boxes, classes, scores)`

循环里改成：

```python
img, r, pad = letterbox(bgr, 640, 32)
x = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
out = npu.run({npu.input_names[0]: x[None]})[0]
boxes, classes, scores = postprocess(out, r, pad, 0.25, 0.45, 480, 640)
```

⚠️ `postprocess` 返回的框**已经是原图 640×480 像素坐标**（它内部做了 letterbox 还原），直接传给发送器，**不要再转一次**。转了就是双重还原，框会偏到画面外。

## O-3 换成 VisionSender

```python
# 删掉这个
def send_result(sock, class_id, score): ...

# 换成
import sys
sys.path.insert(0, r"D:\dasanshang\fpga竞赛\integration")
from vision_sender import VisionSender

sender = VisionSender("192.168.1.20", 5000, class_names=CLASS_NAMES)
# 循环里：
sender.send(boxes, classes, scores, 640, 480)
```

`vision_sender.py` 只依赖标准库，直接复制到你的目录也行。

## O-4 推理频率提到 ≥7Hz

**删掉 `if now - last_infer >= 0.5:` 这个门限**，每帧都推理。

跑不到 7Hz 就按这个顺序优化（从收益最大开始）：

| 顺序 | 优化 | 预期收益 |
|---|---|---|
| 1 | 确认 `npu.run()` 真走 `VitisAIExecutionProvider` 而不是 CPU | 可能 5~10 倍 |
| 2 | `rgb565_to_bgr` 的 numpy 逐位运算改成查表（256×2 张表） | 2~3 倍 |
| 3 | `cv2.resize` 前先降采样（640×480 → 320×240 再 letterbox） | 1.5 倍 |
| 4 | 只在有变化时推理（帧差 > 阈值）—— **不推荐**，会漏掉慢速移动 | — |

## O-5 置信度必须真实

郑的决策里有一道门槛：视觉单源（雷达没回波）时只有 `score >= 0.60` 才允许转向。

**三个不能做**：

| 做法 | 后果 |
|---|---|
| `scores = [1.0] * len(boxes)` | 门槛失效，低质量框也能触发转向 |
| `conf_th` 提到 0.6 再发 | 和门槛重复过滤，等于没有分级 |
| 对 score 做归一化/缩放 | 语义漂移 |

**验证办法**：拿一个只露半边的物体（或远处小目标），它的 score 应该明显低于画面中央完整目标。如果都是 `0.99`，说明你写死了。

## O-6 RTL 改造成子模块（节点 B 之前）

欧阳现在的 `ov5640_udp_pc.v` 是**顶层模块**，自带 `sys_clk` / `sys_rst_n` 输入。合并工程时它必须变成子模块：

```verilog
// 改之前（顶层）
module ov5640_udp_pc(
    input  wire sys_clk,      // ← 来自 U18 引脚
    input  wire sys_rst_n,    // ← 来自 N16 引脚
    ...
);

// 改之后（子模块）
module ov5640_udp_pc(
    input  wire clk,          // ← 由顶层接 FCLK_CLK0
    input  wire rst_n,        // ← 由顶层接 peripheral_aresetn
    ...
);
```

**只是改端口名，内部逻辑一行不动**。引脚约束 `U18`/`N16` 从你的 xdc 里删掉，交给顶层统一约束。

---

# 三、徐的具体任务（PL 侧）

> 工作目录 `xu/project_1/`，交付目录 `integration/motor_axi.v`（已写好）

## 任务清单

| 编号 | 任务 | 卡不卡别人 | 验收 |
|---|---|---|---|
| **X-1** | 交出 `motor_diff.v` + `motor_pins.xdc` | 🔴 **卡节点 B** | 文件出现在 `xu/` 目录 |
| **X-2** | 建 Vivado 工程 + Block Design | 卡节点 B | PS 配置对（M_AXI_GP0 / FCLK 50M / ENET0 / UART1） |
| **X-3** | 挂 `motor_axi` 到 AXI 总线 | 卡节点 C | 地址 `0x43C00000`，写 `0x04` 能改 PWM |
| **X-4** | 引脚约束（电机 6 根） | 卡节点 C | 综合无 unconstrained |
| **X-5** | 出 bit + xsa | 卡节点 C | 烧进去 PS 能启动 |
| **X-6** | 编码器接上（可选） | 不卡 | `0x10`/`0x14` 读数随轮子变 |

## X-1 交出源码（最高优先级）

`xu/project_1/` 里**只有 `.bit` 和 `.dcp`**，没有 RTL。xdc 的 `SRC_FILE_INFO` 指向
`C:/Users/Acer/WorkBuddy/2026-10-05-14-38-25/motor_pwm/` —— 源码在另一台机器上。

需要复制到 `xu/` 的：
- `motor_diff.v`（或你实际的顶层文件名）
- `motor_pins.xdc`

**这一步不做，节点 B 开不了工。** 如果源码找不到了，用 `integration/motor_axi.v` 直接替代（它已经包含了同样的 PWM 逻辑，只是多了 AXI 接口）。

## X-2 建 Vivado 工程

1. 新建工程，Part 选 `xc7z020clg400-2`
2. `Create Block Design` → 加 `ZYNQ7 Processing System`
3. 双击 PS 配置：

| 配置项 | 位置 | 设成 |
|---|---|---|
| `M_AXI_GP0` | PS-PL Configuration → AXI Non Secure Enablement → GP Master | ✅ 使能 |
| `FCLK_CLK0` | Clock Configuration → PL Fabric Clocks | **50 MHz**（`motor_axi` 的 `CLK_HZ` 必须一致） |
| `ENET0` | Peripheral I/O Pins | ✅ 使能（郑收视觉结果用） |
| `UART1` | Peripheral I/O Pins | ✅ 使能（如果雷达走 PS 串口而不是 USB） |
| `SD0` | Peripheral I/O Pins | ✅ 使能（启动必须） |

4. `Run Connection Automation` → `motor_axi` 挂 `M_AXI_GP0`，时钟接 `FCLK_CLK0`，复位接 `peripheral_aresetn`
5. Address Editor → 给 `motor_axi` 分配 `0x43C00000`，**记下来告诉郑**

## X-3 寄存器行为对照表（给郑看）

| 偏移 | 名称 | 方向 | 写什么 | 读什么 |
|---|---|---|---|---|
| `0x00` | `CTRL` | W | bit0=1 使能，bit0=0 强制停车 | — |
| `0x04` | `SPEED_L` | W | 有符号 16 位，`-1000~+1000`，负数=反转 | — |
| `0x08` | `SPEED_R` | W | 同上 | — |
| `0x0C` | `HEARTBEAT` | W | 每 100ms 写一次**变化的值**（递增即可） | — |
| `0x10` | `ENC_L` | R | — | 左编码器计数（4 倍频） |
| `0x14` | `ENC_R` | R | — | 右编码器计数 |
| `0x18` | `STATUS` | R | — | bit0=看门狗超时 bit1=急停锁死 bit2=使能中 |

⚠️ **心跳必须是变化的值**。`motor_axi.v` 里是 `hb_reg != hb_prev` 判活，一直写同一个数会被当成没心跳，300ms 后锁死。

## X-4 引脚约束

```tcl
# motor_axi 的电机输出（沿用你原来的引脚）
set_property -dict {PACKAGE_PIN U7 IOSTANDARD LVCMOS33} [get_ports pwm_l]
set_property -dict {PACKAGE_PIN T9 IOSTANDARD LVCMOS33} [get_ports in1]
set_property -dict {PACKAGE_PIN Y7 IOSTANDARD LVCMOS33} [get_ports in2]
set_property -dict {PACKAGE_PIN V8 IOSTANDARD LVCMOS33} [get_ports pwm_r]
set_property -dict {PACKAGE_PIN V6 IOSTANDARD LVCMOS33} [get_ports in3]
set_property -dict {PACKAGE_PIN Y9 IOSTANDARD LVCMOS33} [get_ports in4]

# 编码器（按实际接线改；没接就接 0）
# set_property -dict {PACKAGE_PIN ?? IOSTANDARD LVCMOS33} [get_ports enc_l_a]
# set_property -dict {PACKAGE_PIN ?? IOSTANDARD LVCMOS33} [get_ports enc_l_b]
# set_property -dict {PACKAGE_PIN ?? IOSTANDARD LVCMOS33} [get_ports enc_r_a]
# set_property -dict {PACKAGE_PIN ?? IOSTANDARD LVCMOS33} [get_ports enc_r_b]
```

⚠️ `clk` / `rst_n` **不再需要引脚约束** —— AXI 版本的时钟复位由 PS 提供。`U18`/`N16` 留给顶层（欧阳的摄像头模块也要用）。

## X-5 验收

```python
from pynq import MMIO
m = MMIO(0x43C00000, 0x1000)
m.write(0x00, 1)        # 使能
m.write(0x04, 500)      # 左轮 50%
m.write(0x0C, 1)        # 心跳
print(hex(m.read(0x18)))  # 期望 0x4
```

三条硬指标：
1. 写 `0x04=500` → 左轮转；写 `0` → 停
2. 写 `-500` → 左轮反转
3. **停止写心跳 300ms → 电机自动停**

---

# 四、郑的具体任务（PS 侧）

> 工作目录 `zheng/` + `integration/`

## 任务清单

| 编号 | 任务 | 状态 | 验收 |
|---|---|---|---|
| **Z-1** | 抗误检机制（帧级连续确认） | ✅ 已完成 | pytest 全绿 |
| **Z-1b** | 点云级抗噪（弱回波 + 稳健扇区） | ✅ 已完成 | 32 个测试全绿 |
| **Z-2** | 雷达端口改 `/dev/ttyUSB0` | 待做（1 行） | 板子上有数据 |
| **Z-3** | PS 网口配 IP | 待做（1 条命令） | `ping 192.168.1.102` 通 |
| **Z-4** | 传代码上板 | 待做 | `/home/xilinx/integration/` 齐了 |
| **Z-5** | mock 模式跑通 | 待做 | 规则号会变 |
| **Z-6** | 接 AXI 电机 | 等徐（X-3） | 电机随规则转 |
| **Z-7** | 实车标定 7 项 | 等节点 D | 见节点 D |
| **Z-8** | 上电自启动（systemd） | 等 Z-5 通过 | 断电重上电自动跑 |

## Z-1b 点云级抗噪（已做完，改的是 `radar/d6_protocol.py`）

**问题**：`sector_min()` 用 `min()`，一圈 25 个点里**任何一个错点**就能把 front 从 3m 拉到 0.2m。帧级连续确认挡不住这个 —— 错点让整帧都"看起来有障碍"，连续 2 帧都是它，确认照样通过。帧级确认管"整帧错"，管不了"帧内单点错"。

**两个改动**：

| 改动 | 参数 | 说明 |
|---|---|---|
| 用上弱回波标志位 | `DROP_WEAK_ECHO = True` | `Si_L` 的 bit0 是 D6 自带的低置信标志（实测该位=1 的点距离中位 9.7m、强度 91；正常点 0.28m/220）。以前解包出来只用了 `s_l >> 2`，**bit0 被直接扔了** |
| 扇区取第 k 小 | `SECTOR_ROBUST_K = 2` | 不再取最小，取第 2 小。点太少（`< SECTOR_ROBUST_MIN_PTS = 4`）时退回取最小，因为样本不够没法判断谁是噪点 |

⚠️ **`SECTOR_ROBUST_K = 2` 的代价**：一根 1mm 的细线只产生 1 个点，会被当成噪声忽略。3cm 的杆子在同一距离产生 2~3 个点，仍能检出。如果要极端保守，设回 `K = 1`。

**新增接口**：
- `parse_frame_ex()` → 4 元组 `(角度, 距离, 强度, 弱回波bool)`
- `scan_stats()` → 单遍统计三扇区，返回 `{"front": (距离, 有效点数), ...}`
- `parse_frame()` 保持不变（3 元组），官方样本回归测试不受影响

## Z-2 雷达端口（一行）

```python
# zheng/radar/d6_protocol.py
PORT = "COM5"          # 改成 "/dev/ttyUSB0"
```

或者不改文件，用 `run_on_board.py --port /dev/ttyUSB0` 传进去（推荐，PC 和板子共用一份代码）。

⚠️ 线序：D6 是 **黄 5V / 白 GND / 红 TXD / 黑 RXD**，接 PYNQ 要**交叉**（雷达 TXD → PYNQ RXD）。

## Z-3 PS 网口配 IP

```bash
sudo ip addr add 192.168.1.20/24 dev eth0
sudo ip link set eth0 up
ping 192.168.1.102          # 通到 PC 就行
```

⚠️ 这是临时生效，重启就没了。要持久化写进 `/etc/network/interfaces`。

## Z-4 传代码

⚠️ **先搞清楚：Python 不烧录。** Verilog 要综合成 bit 流烧进 PL；Python 只是文件，
拷到板子的 Linux 文件系统里就能跑。两者是两套机制。

| | 欧阳/徐的 Verilog | 郑的 Python |
|---|---|---|
| 产物 | `.bit` 比特流（几百 KB 二进制） | `.py` 源文件（纯文本） |
| 谁生成 | Vivado 综合 + 实现 + 生成比特流 | 不用生成，写完就是成品 |
| 放哪 | SD 卡 **BOOT 分区**，被 `BOOT.BIN` 打包 | rootfs 分区 `/home/xilinx/`（或 BOOT 分区 `/boot/code/`） |
| 怎么生效 | 上电时 FSBL 把它灌进 PL 的配置存储器 | 敲 `python3 xxx.py` 就生效 |
| 改一次要多久 | **15~40 分钟**（综合+布局布线） | 3 秒（改完存盘即可） |
| 谁做 | 徐（X-1 交源码 → 徐综合 → 出 bit） | 郑（scp 拷贝） |

**时间顺序不能颠倒**：先有徐的 bit 流烧进 PL（PL 里才有 PWM 和图像通路），
板子才能控制电机；郑的 Python 是在这个基础上跑的。所以
**徐出第一版 bit 是所有人的起跑线**（X-1 是唯一硬卡点），
郑的 Python 在此之前用 `--motor mock` 全流程能跑通，一点不耽误。

### 徐的烧录流程（郑不用动手，但要知道卡在哪）

| 方式 | 操作 | 掉电后 | 用在哪 |
|---|---|---|---|
| **JTAG** | Vivado → Open Hardware Manager → Program Device → 选 `.bit` | **丢失**，要重烧 | 调试时快速验逻辑，改一次 3 分钟 |
| **SD 卡** | Generate Bitstream → Export Hardware → 打包 `BOOT.BIN` → 拷进 BOOT 分区 → 插卡上电 | 保留 | 正式跑，答辩用这个 |

`BOOT.BIN` 里面装三样：FSBL（引导）+ `system.bit`（PL 逻辑）+ `u-boot.elf`（加载内核）。
**只要 `BOOT.BIN` 换掉，PL 的逻辑就换了，不用碰任何线。**

**怎么确认 PL 里真的有逻辑**（郑自己就能查，不用问徐）：

```bash
# 板子上，PL 配置成功会亮这个
cat /sys/class/fpga_manager/fpga0/state      # 期望 "operating"，"unknown" = 没烧进去
```

或者更直接：跑 `run_on_board.py --motor axi`，AXI 写寄存器不报错就是通了
（`/dev/mem` 映射失败或读到全 `0xFFFFFFFF` = PL 里没东西）。

```bash
# PC 上（Git Bash）—— 有网络时最省事
scp -r D:/dasanshang/fpga竞赛/zheng       xilinx@192.168.2.99:/home/xilinx/
scp -r D:/dasanshang/fpga竞赛/integration xilinx@192.168.2.99:/home/xilinx/
```

`integration/` 整个传，因为 `run_on_board.py` 依赖 `contract.py`。

**没网络时的三条路**：

| 办法 | 做法 | 麻烦程度 |
|---|---|---|
| **放 BOOT 分区** | SD 卡的 BOOT 分区是 FAT32，Windows 能直接读写。把代码放 `code/` 目录，Linux 里就是 `/boot/code/` | ⭐ 最实用 |
| Jupyter 上传 | 浏览器开 `http://板子IP:9090`，网页里点 Upload | 需要网络 |
| 拔卡 + ext4 驱动 | rootfs 是 ext4，Windows 读不了。要装 Paragon ExtFS 或用 Linux 虚拟机 | ❌ 别走这条 |

⚠️ **`/home/xilinx` 在 ext4 分区上，Windows 看不见。** 拔卡插电脑只能改 BOOT 分区，
所以"没网络就拔卡改代码"这个说法其实不成立 —— 除非把代码放 BOOT 分区。

**pyserial 要提前装好**：

```bash
# 板子上（需要网络）
pip3 install pyserial

# 没网络：PC 上下载 whl 拷过去
pip3 download pyserial -d ./whl          # PC 上（同架构也行，pyserial 是纯 Python）
scp ./whl/pyserial-*.whl xilinx@IP:/home/xilinx/
pip3 install --no-index --find-links=/home/xilinx/whl pyserial
```

`numpy` / `pynq` 是 PYNQ 镜像自带的，不用装。郑的代码除了 `pyserial` 只用标准库。

## Z-5 分三步验（每步停一下）

```bash
cd /home/xilinx/integration

# 第 1 步：只验雷达
python3 run_on_board.py --port /dev/ttyUSB0 --motor mock

# 第 2 步：只验视觉（雷达用 mock）
python3 run_on_board.py --radar mock --vision udp --motor mock

# 第 3 步：全真（车必须架空！）
python3 run_on_board.py --port /dev/ttyUSB0 --vision udp --motor axi
```

## Z-7 标定参数（全部在 `zheng/protocol.py::CFG`）

| 参数 | 默认 | 调什么 |
|---|---|---|
| `danger_m` | 0.50 | 前向危险距离，按刹车距离定 |
| `warn_m` | 1.20 | 前向警惕距离 |
| `passable_m` | 0.60 | 侧向可通行宽度 |
| `side_confirm_frames` | 2 | 侧向堵死需连续帧数 |
| `r2_confirm_frames` | 2 | R2 转向需连续帧数 |
| `r3_confirm_frames` | 3 | 雷达单源报警需连续帧数 |
| `vision_single_conf` | 0.60 | 视觉单源转向最低置信度 |

**雷达点云侧参数**（在 `zheng/radar/d6_protocol.py` 顶部）：

| 参数 | 默认 | 调什么 |
|---|---|---|
| `ZERO_OFFSET_DEG` | 0.0 | 装车 0° 偏角，用 `calibrate_zero.py --survey 20` 测 |
| `DROP_WEAK_ECHO` | True | 是否丢弱回波点（近距离深色物体常误判时可关掉） |
| `SECTOR_ROBUST_K` | 2 | 扇区取第几小；细障碍常漏检时改回 1 |
| `SECTOR_ROBUST_MIN_PTS` | 4 | 少于几个点就退回取最小 |
| `RECOMPUTE_HZ` | 30.0 | 扇区统计频率；设 0 = 每帧都算（CPU 高 3 倍） |
| `SECTOR_FRONT/LEFT/RIGHT` | 见文件 | 扇区角度范围，改完要重启进程（查表在 import 时构建） |

## Z-8 上电自启动（Z-5 验通过后再做）

**为什么 Python 不能"烧进去就自启"**：上电启动是一条链，Python 排在最后：

```
上电
 ├─ FSBL（片内 ROM 里固化的引导）     读 BOOT.BIN
 ├─ BOOT.BIN = FSBL + bitstream + u-boot
 │     └─ 把 bitstream 灌进 PL   ← 徐的 Verilog 在这一步生效
 ├─ u-boot 加载 image.ub（Linux 内核 + 设备树）
 ├─ 内核挂载 rootfs（ext4 分区）
 └─ systemd / init 启动服务
       └─ 你的 python3 run_on_board.py   ← 郑的代码在这一步才跑
```

前面任何一步失败，Python 根本轮不到执行。所以"烧录"这个词对 Python 不适用 ——
它没有"被烧"的形态，只有"被启动"的时机。

**用 systemd 做自启**（比 `/etc/rc.local` 靠谱：能设依赖、能自动重启、能查日志）：

```ini
# /etc/systemd/system/car.service
[Unit]
Description=Radar+Vision Car Controller
After=network.target dev-ttyUSB0.device
Wants=dev-ttyUSB0.device

[Service]
Type=simple
User=root
WorkingDirectory=/home/xilinx/integration
ExecStartPre=/bin/sleep 8
ExecStart=/usr/bin/python3 run_on_board.py --port /dev/ttyUSB0 --vision udp --motor axi
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable car.service      # 开机自启
sudo systemctl start  car.service      # 现在就跑
journalctl -u car.service -f           # 实时看输出（答辩调试主力）
sudo systemctl disable car.service     # 关掉自启（手动演示时用）
```

**三处必须注意**：

| 坑 | 为什么 | 怎么躲 |
|---|---|---|
| `After=dev-ttyUSB0.device` | 雷达是 USB 转串口，上电后 2~5 秒才枚举出来。抢跑会 `FileNotFoundError: /dev/ttyUSB0` | 上面写了依赖，再加 `ExecStartPre=/bin/sleep 8` 兜底 |
| `User=root` | 串口设备默认属 `dialout` 组，`xilinx` 用户不一定在里面；`/dev/mem`（AXI 访问）必须 root | 直接用 root 最省事；讲究点就 `usermod -aG dialout xilinx` |
| `WorkingDirectory` | `run_on_board.py` 里 `from contract import ...` 是相对当前目录找的 | 必须写对目录，否则 `ModuleNotFoundError: contract` |

**⚠️ 答辩现场不要开自启。** 自启的好处是"断电重启就继续跑"，坏处是
"评委让你改个参数，你得先 `systemctl stop`"。流程：**平时开着方便调试，
正式演示前 `disable`，演示时手动起，出问题当场能看到报错。**

---

# 五、三人共同守的规矩

## 5.1 接口冻结表（谁都不能单方面改）

| 接口 | 定义在哪 | 谁改了要通知 |
|---|---|---|
| Vision JSON 字段 | `integration/contract.py::detections_to_vision` | 欧阳 + 郑 |
| Control JSON 字段 | `zheng/protocol.py::make_control` | 郑 + 徐 |
| 差速映射公式 | `integration/contract.py::control_to_diff` | 郑 + 徐 |
| AXI 寄存器映射 | `integration/motor_axi.v` 文件头注释 | 徐 + 郑 |
| `CFG` 里的阈值 | `zheng/protocol.py` | 郑 + 欧阳（置信度相关） |

改任何一条：**版本号 +1 → 群里说一声 → 三方各自重跑联调**。

## 5.2 三个不能碰的雷

| 雷 | 后果 |
|---|---|
| 徐改了 `motor_axi.v` 的 `CLK_HZ` 但没告诉郑 | 20kHz PWM 变 10kHz，电机啸叫、扭矩下降 |
| 欧阳把 `scores` 写死成 1.0 | 郑的置信度门槛失效，车会朝低质量框转 |
| 郑把 `vision_timeout_ms` 改大而不告诉欧阳 | 视觉真挂了要等更久才发现，掩盖问题 |

## 5.3 每次改动后的自检（三方都跑同一条命令）

```bash
cd D:/dasanshang/fpga竞赛/integration
python contract.py                  # 契约自检
python run_integration_demo.py      # 7 场景，规则号必须是 OK/R1/R2/R4/R1/R5/R4
cd ../zheng && python -m pytest tests/ -q    # 32 passed
```

---

# 六、依赖关系与卡点

```
       ┌──────────────────────────────────────────────┐
       │  现在就能并行开工（互不阻塞）                 │
       ├──────────────────────────────────────────────┤
       │  欧阳 O-1 ~ O-5（PC 上改视觉）                │
       │  徐   X-1（交源码，最急）                     │
       │  郑   Z-1 ✅ / Z-2 / Z-3 / Z-4 / Z-5          │
       └──────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   [节点 A]            [节点 B]            （郑等 X-3）
   欧阳 + 郑           徐 + 欧阳
   PC 联调             PL 合并
        │                   │
        └─────────┬─────────┘
                  ▼
             [节点 C]  郑 + 徐
             板子联调
                  │
                  ▼
             [节点 D]  三人
             实车标定
```

**唯一的硬卡点**：X-1（徐交源码）。它卡节点 B，节点 B 卡节点 C，节点 C 卡节点 D。

**建议的动作顺序**：
1. 徐今天就交源码（哪怕只是从旧机器拷个文件）
2. 欧阳改视觉，改完立刻找郑做节点 A
3. 郑同时把 Z-2 ~ Z-5 做完（mock 模式不需要任何人配合）

---

# 七、答辩材料分工

| 内容 | 谁准备 | 素材来源 |
|---|---|---|
| 硬件架构图、PS/PL 划分 | 徐 | Block Design 截图 |
| 电机控制器 + 看门狗设计 | 徐 | `motor_axi.v`，重点讲 300ms 看门狗 |
| 视觉模型 + 推理优化 | 欧阳 | YOLO 指标、RyzenAI 加速 |
| 五规则决策 + 抗误检 | 郑 | `machine.py`，重点讲"停车不等确认、转向必须确认" |
| 三人协作（接口契约） | 郑 | `contract.py`，讲"两个语言怎么对接" |
| 实车演示视频 | 三人 | 节点 D 时录 |

**答辩最容易问到的三个问题，提前想好**：

1. **"为什么不用 ROS / SLAM？"** → 单线雷达 400 点/10Hz、A9 算力不足、任务是反应式避障无目标点导航需求，加了是负收益
2. **"Python 跑在板子上不慢吗？"** → 见附录，有实测数据
3. **"误检怎么办？"** → 分扇区连续确认 + 置信度门槛，停车和转向区别对待

---

# 附录：要不要把 Python 改成 C（实测数据）

跑 `python bench_decision.py` 得到（PC 上，i7 级 CPU）：

| 函数 | 单次 | 频率 | 每秒占用 |
|---|---|---|---|
| `checksum()`（25 点） | 4.1 µs | 163/s | 0.66 ms |
| `parse_frame_ex()`（25 点） | 13.6 µs | 163/s | 2.22 ms |
| `scan_to_radar_json()`（400 点 3 扇区） | 43.0 µs | 30/s | 1.29 ms |
| `parse_vision()` | 4.1 µs | 7/s | 0.03 ms |
| `machine.step()`（五规则） | 2.0 µs | 10/s | 0.02 ms |
| `dumps(control)` | 2.0 µs | 10/s | 0.02 ms |

**两条线程各自的负载**：

| 线程 | 负载 | 单核占用 |
|---|---|---|
| 雷达线程（后台） | 3.51 ms/s | **0.35%** |
| 主线程（决策） | 0.008 ms/周期 | **0.01%** |

## 结论

**改成 C 不会让车变快。** 理由：

1. **主线程每 100ms 才动一次，只花 8 微秒。** 剩下 99.99ms 全在 `time.sleep()` 等。就算 C 快 30 倍，也只是从 8µs 变 0.3µs，对 100ms 的周期毫无影响。
2. **真正的延迟不在计算**：雷达一圈 98ms（物理限制）、YOLO 推理 30~140ms、决策周期主动等 100ms、机械惯性几十毫秒。
3. **板子 A9 比 PC 慢 20~30 倍**，0.35% 会变成 8~11% 单核 —— 双核还剩一个半，主线程只要 0.01%。

## 已经做掉的算法优化（比改 C 有效得多）

| 优化 | 效果 |
|---|---|
| 扇区统计从每帧 163 次/秒 → 30 次/秒（`RECOMPUTE_HZ`） | 雷达线程 16.2 → 5.2 ms/s |
| 三遍 `sector_min` → 单遍 `scan_stats` + 整度查表 | `scan_to_radar_json` 89 → 43 µs |
| **合计** | **16.2 → 3.51 ms/s（降 78%）** |

⚠️ 踩到的坑：一开始用 `sorted(vals)[k-1]` 取第 k 小，排序是 O(n log n)，**比原来的 `min()` 还慢一倍**（89 → 156µs）。改成维护长度 k 的升序小表（O(n·k)）才对。同理 `for ang, d, *_ in scan` 每轮都建一个 list，比下标访问慢 20%。

**比改 C 更值得做的三件事**：
1. 视觉提到 10Hz（`CYCLE_MS` 也降到 50）→ 反应快一倍
2. `danger_m` 调准 → 少撞一次
3. 抗误检 + 点云抗噪参数调准 → 少抽风一次
