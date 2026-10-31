# 三人代码整合方案

> 更新：2026-10-24 20:30　｜　契约版本：contract v2（决策侧含抗误检）
> 三个目录：`xu/`（徐）、`ouyang/`（欧阳）、`zheng/`（郑）

---

## 0. 架构已定（用户 2026-10-24 拍板）

1. **激光雷达接板子**（不是 PC 的 COM5）
2. **决策在板子的 PS 上跑**（郑的 Python）
3. **YOLO 在 PC 上跑**（欧阳的 RyzenAI）

由此确定：PC 只做"眼睛"，板子做"大脑和手脚"。视觉结果必须从 PC 回传到板子 PS。

**好消息**：板子是**正点原子领航者 ZYNQ V2，有两个千兆网口**（欧阳文档里写着"PL 网口靠近摄像头那个 RJ45，PS 网口不行"）。
所以视觉回传**零 Verilog 改动**就能打通 —— PC 双发，图像走 PL 口，结果走 PS 口。

⚠️ **开工前请看一眼板子背面有几个 RJ45**。只有一个 → 本方案不成立，退回断点 ④ 的方案 B。

---

## 1. 现状盘点（按实际文件核对）

| 人 | 工程 | 顶层模块 | 是否有 PS | 代码形态 | 能干什么 |
|---|---|---|---|---|---|
| 欧阳 | `ouyang/42_ov5640_udp_pc/` | `ov5640_udp_pc` | ❌ 纯 PL | 20 个 `.v`（~4.5k 行）+ 10 个 Python | OV5640 → UDP 发 640×480 RGB565 到 PC |
| 徐 | `xu/project_1/` | `motor_diff` | ❌ 纯 PL | **只有 `.bit` + `.dcp`，无 RTL 源码** | 双路 PWM 差速驱动（速度写死） |
| 郑 | `zheng/` | `run.py` | 需 PS | 1120 行 Python | 雷达解析 + 五规则决策 + TCP 下发 |

### 引脚核对

| 信号 | 欧阳 | 徐 | 冲突 |
|---|---|---|---|
| 系统时钟 | `U18` | `U18` | ⚠️ 同引脚 |
| 复位 | `N16` | `N16` | ⚠️ 同引脚 |
| 以太网 | `K17/B20/…` GMII | — | 无 |
| 摄像头 | `W14/R14…` | — | 无 |
| 电机 | — | `U7/T9/Y7/V8/V6/Y9` | 无 |

### 关键参数

| 项 | 值 | 来源 |
|---|---|---|
| 芯片 | `xc7z020clg400-2` | 欧阳 `.xpr` |
| PL 板子 IP | `192.168.1.10` | `ov5640_udp_pc.v` 的 `BOARD_IP` |
| PC IP | `192.168.1.102` | 同上 `DES_IP_DEFAULT` |
| 图像 UDP 端口 | `1234` | `view.py` |
| 图像帧头 | `f0 5a a5 0f` + 4B 宽高 + 614400B 像素 | `view.py` |
| 启动命令 | 单字节 `'1'`(0x31) 开 / `'0'`(0x30) 停 | `start_transfer_ctrl.v` |
| Vivado 版本 | 2020.2（欧阳）/ 未知（徐） | 本机 2023.2 |

---

## 2. 目标架构（两个网口是关键）

```
┌──────────────── PC 192.168.1.102 ────────────────┐
│  yolo_infer.py                                   │
│   ① 收图 UDP:1234  ← PL                          │
│   ② YOLO 推理（RyzenAI NPU）                      │
│   ③ vision_sender.py → UDP:5000 → PS             │
└───────┬───────────────────────────┬──────────────┘
        │ 图像（614KB/帧）           │ 结果（~200B/包）
        ↓                           ↓
┌── 千兆交换机 ──────────────────────────────────┐
│                                                │
│  ┌─ ZYNQ XC7Z020 ─────────────────────────┐   │
│  │  PL 网口 192.168.1.10                  │   │
│  │    OV5640 采集 + UDP 发图（欧阳，不动） │   │
│  │                                         │   │
│  │  PS 网口 192.168.1.20                  │   │
│  │    Linux + Python                      │   │
│  │      vision_receiver.py  ← 视觉结果     │   │
│  │      雷达 /dev/ttyUSB0   ← D6          │   │
│  │      DecisionMachine     ← 五规则       │   │
│  │      mmio.write()        ← 电机指令     │   │
│  │         ↓ AXI-Lite                     │   │
│  │  PL: 电机 PWM（徐的 motor_diff + AXI 壳）│   │
│  └────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘
```

### 网络规划

| 设备 | IP | 说明 |
|---|---|---|
| PC | `192.168.1.102` | 网卡手动配静态 IP |
| PL 网口 | `192.168.1.10` | **Verilog 里写死，不能改** |
| PS 网口 | `192.168.1.20` | 在 PYNQ 的 Linux 里配 |

PC 只有一个网口 → 加一个千兆交换机，PC / PL 口 / PS 口 全插上去，同网段互通。

PS 侧配 IP（PYNQ 上执行）：
```bash
sudo ip addr add 192.168.1.20/24 dev eth0
sudo ip link set eth0 up
ping 192.168.1.102          # 验证通
```

⚠️ PL 的裸 UDP（自己实现的 ARP + UDP，无 IP 协议栈）和 PS 的 Linux 网络栈在同一二层网络里不会冲突，各用各的 IP。

---

## 3. 视觉频率契约（实测踩出来的坑，务必看）

郑的 `DecisionMachine` 里 `vision_timeout_ms=300`：视觉超过 300ms 没更新 → **R5 强制停车**。
欧阳现在的推理循环是 `now - last_infer >= 0.5`，即 **2Hz**。

**实测后果**（`integration` 里跑过）：2Hz 发送 + 300ms 超时 → **70% 的决策周期在停车**，车一顿一顿地走。

约束公式：`vision_timeout_ms >= 2 × (1000 / 发送频率Hz)`

| 发送频率 | 最小 timeout |
|---|---|
| 2 Hz | 1000 ms |
| 5 Hz | 400 ms |
| **10 Hz** | **200 ms** ← 郑现在的 300ms 要求 ≥ 6.7Hz |

**两条出路，二选一**：
- **A. 欧阳把推理提到 ≥ 7Hz**（推荐。RyzenAI NPU 跑 yolo11n 到 20~30FPS 没问题，瓶颈在 UDP 收图 + RGB565 解码，可以优化）
- B. 郑把 `CFG["vision_timeout_ms"]` 调到 1000（简单，但视觉真挂了要 1 秒才发现）

❌ **不要走"2Hz 推理 + 10Hz 重复发旧结果"**：时间戳会骗人，人走了车还在躲。

`vision_receiver.py` 会自动测频率并在低于 7Hz 时告警。

---

## 4. 抗误检机制（已落地进 `zheng/fusion/machine.py`）

### 4.1 为什么需要它

五规则里有三条是**单帧单源**就能触发动作的，在实车上全是隐患：

| 规则 | 触发条件（改造前） | 误检后果 |
|---|---|---|
| R1 双确认 | 前向 + 侧向同时堵 | 一帧噪声 → 急停 |
| R2 视觉优先 | 视觉报障碍 → 转向 | 一帧误检 → **车突然甩一下** |
| R3 雷达单源 | 侧向单侧回波 → 停 | 一帧杂波 → 顿一下 |

D6 的 400 点/圈里有相当比例是**低置信点**（黑色吸光物、玻璃、远处地面反射），YOLO 也有 0.25 门槛边缘的抖动框。原来"一帧就动"的写法在实验室看着正常，上车就是随机抽风。

### 4.2 两条改进

**① 分扇区独立确认计数**

三个扇区各自维护一个计数器，**每帧无条件更新**（跟后面走哪条规则无关）：

```python
self.confirm = {"front": 0, "left": 0, "right": 0}

# 每帧：该扇区堵 → 累加；不堵 → 立即归零
self.confirm["left"] = (self.confirm["left"] + 1
                        if (left is not None and left <= c["passable_m"]) else 0)
```

判定时改成"持续堵才算堵"：

```python
left_ok = (left is not None) and (left > c["passable_m"]
                                  or self.confirm["left"] < c["side_confirm_frames"])
```

`side_confirm_frames = 2` → 连续 2 帧（约 0.2 秒）都堵才承认。单帧杂波被吃掉。

⚠️ 注意是**归零**不是**递减**。递减的话，杂波间隔出现会慢慢攒够阈值；归零保证"必须真的连续"。

⚠️ **三个扇区必须独立计数**，不能共用一个。共用一个的话"前向堵一帧 + 左侧堵一帧"会凑成 2 次确认，凭空造出一个双确认急停。

**② R2 视觉单源转向加置信度门槛**

R2 是唯一"只靠视觉就能让车转向"的规则，风险最高。拆成三条分支：

| 视觉障碍位置 | 置信度 | 连续帧 | 动作 |
|---|---|---|---|
| `center`（正前方） | 任意 | 任意 | **立即停车** |
| `left` / `right` | `< 0.60` | 任意 | 减速直行，不转向 |
| `left` / `right` | `>= 0.60` | `>= 2` 帧 | 才允许转向避让 |

设计依据是**动作的危险程度不同**：

- **停车是安全动作** —— 误停的代价只是慢一点，所以不设门槛，宁可错停
- **转向是危险动作** —— 误转可能撞上旁边的东西，所以必须"高置信 + 连续多帧"双重确认

视觉一旦没触发 R2，`vision_confirm` 立刻清零（不跨场景累积）。

### 4.3 参数在哪调

全在 `zheng/protocol.py` 的 `CFG` 里，四个键：

| 键 | 默认 | 含义 | 调大的后果 |
|---|---|---|---|
| `side_confirm_frames` | `2` | 侧向"堵死"需连续帧数 | 反应变慢，杂波更不敏感 |
| `r2_confirm_frames` | `2` | R2 转向需连续帧数 | 避障更犹豫，但更稳 |
| `vision_single_conf` | `0.60` | 视觉单源转向最低置信度 | 只对很确定的目标避让 |
| `r3_confirm_frames` | `3` | 雷达单源报警需连续帧数（原有） | R3 更迟钝 |

⚠️ 这几个参数**和视觉频率强耦合**：`side_confirm_frames=2` 在 10Hz 下是 0.2 秒，在 2Hz 下就是 1 秒。所以第 3 节的"≥7Hz"是这套机制能用的前提。

⚠️ `r3_confirm_frames=3` 比另外两个更严（3 帧），因为 R3 是"视觉什么都没看到、只有雷达说前面有东西"，属于最容易误判的情形。

### 4.4 验证

`zheng/tests/test_fusion.py` 新增 4 个针对性测试（另有 `test_radar_filter.py` 的 11 个，见 4.5），**总计 32 个全绿**：

| 测试 | 验证什么 |
|---|---|
| `test_r2_single_frame_noise_not_turn` | R2 单帧噪声不转向 |
| `test_r2_low_confidence_never_turns` | 低置信度哪怕连续 10 帧也不转向 |
| `test_side_single_frame_noise_not_blocked` | 侧向单帧杂波不判堵 |
| `test_side_persistent_blocked_stops` | 侧向持续堵 2 帧后正常停车 |

`run_integration_demo.py` 改成**每场景连跑 5 帧**（原来只跑 1 帧，看不出连续确认的效果）。现在场景 3 的输出是：

```
[决策] R2  RIGHT    speed= 35 steer= 45   vision obs left conf=0.81 x5, radar no echo, turn away
```

`conf=0.81 x5` 说明置信度检查和连续确认都在工作。

### 4.5 点云级抗噪（雷达侧，同一批改动）

上面 4.2 的确认是**帧级**的 —— 挡的是"整帧是错的"。挡不住"这一帧里有一个点是错的"：错点让整帧都看起来有障碍，连续 2 帧都是它，确认照样通过。

所以雷达侧还有两层**点级**过滤（`zheng/radar/d6_protocol.py`）：

| 层 | 做法 | 参数 |
|---|---|---|
| 逐点置信度 | D6 的 `Si_L` bit0 是远距/弱回波标志（实测该位=1 的点距离中位 9.7m、强度 91；正常点 0.28m/220）。以前解包时**直接扔了** | `DROP_WEAK_ECHO` |
| 扇区稳健统计 | 不再取 `min()`，取第 2 小；点太少（<4）时退回取最小 | `SECTOR_ROBUST_K` / `SECTOR_ROBUST_MIN_PTS` |

⚠️ `K=2` 的代价：1mm 的细线只产生 1 个点，会被当噪声忽略；3cm 的杆子产生 2~3 个点，仍能检出。

顺带把雷达线程的负载降了 78%（16.2 → 3.5 ms/s）：

| 优化 | 效果 |
|---|---|
| 扇区统计按 `RECOMPUTE_HZ=30` 限流（原来每帧 163 次/秒都算） | 主要收益 |
| 三遍 `sector_min` → 单遍 `scan_stats` + 整度查表 | 89 → 43 µs |

⚠️ **踩到的坑**：第一版用 `sorted(vals)[k-1]` 取第 k 小，排序是 O(n log n)，**比原来的 `min()` 还慢一倍**（89 → 156µs）。改成维护长度 k 的升序小表（O(n·k)）才对。`for ang, d, *_ in scan` 每轮建一个 list，比下标访问慢 20%。

**关于"用不到的角度能不能省"**：串口字节和解码**省不掉**（雷达机械旋转，整圈都会发，230400 波特固定）。能省的是扇区统计 —— 三个扇区只覆盖 150°，剩下 210° 的点查一次表就跳过。真要再省只能降低统计频率，那就是 `RECOMPUTE_HZ`。

---

## 5. 四处断点

### 断点 ① 徐的电机没有控制接口　【阻塞级】

`motor_diff` 只有 8 个端口（`clk/rst_n/pwm_l/in1/in2/pwm_r/in3/in4`），**无 AXI，速度是 RTL 常量**。

**而且 RTL 源码不在 `xu/` 目录里** —— xdc 的 `SRC_FILE_INFO` 指向
`C:/Users/Acer/WorkBuddy/2026-10-05-14-38-25/motor_pwm/motor_pins.xdc`，源码在徐另一台机器上。

**必须做**：
1. 徐交出 `motor_diff.v` + `motor_pins.xdc`
2. 包一层 AXI4-Lite slave（Vivado 里 `Create AXI4-Lite IP` 自动生成骨架，填中间 20 行）
3. 寄存器：`CTRL(0x00)` / `SPEED_L(0x04)` / `SPEED_R(0x08)`

### 断点 ② 转向语义不一致　【已解决】

- 郑的 `steering` 0~180（90 中位）= **舵机语义**
- 徐的 `pwm_l/pwm_r` = **差速语义**

`integration/contract.py::control_to_diff()` 已做转换。`TURN_GAIN=0.6` 需实车标定。

### 断点 ③ 视觉输出格式对不上　【阻塞级】

| 郑需要（`parse_vision` 强校验） | 欧阳现在给 |
|---|---|
| 检测框 `cx/cy/width/height` 归一化 0~1 | **分类**：`a5 0f f0 5a + class_id + score`，**没有框** |
| — | `main_ryzenai.py` 用 32×32、10 类的**模板模型** |

`yolo_test.py` 有完整 YOLO 后处理（letterbox + NMS + 解码），但是 **CPU 单张图测试**，没进实时循环。

**必须做**：欧阳把 `yolo_test.py` 的 `letterbox` + `postprocess` 搬进 UDP 收图循环，然后一行调 `VisionSender.send(boxes, classes, scores)`。

### 断点 ④ 视觉结果回不到决策程序　【已解决，走 PS 网口】

原问题：PC 发 UDP 到 `192.168.1.10:1234`（PL 口），但 PL 的 `udp_rx` 只解析 1 字节启动命令，**没有转给 PS 的通路**。

**解法**：不发 PL，改发 PS 的 `192.168.1.20:5000`。PS 的 Linux 原生收 UDP，零 Verilog 改动。

| 方案 | 做法 | 代价 | 状态 |
|---|---|---|---|
| **A. PS 独立网口** | PC 双发，结果发 `192.168.1.20:5000` | **零 Verilog** | ✅ 采用（推荐） |
| B. PL 转发 | 改 `udp_rx` 识别结果包 → BRAM → AXI → PS | 中（~100 行 Verilog） | 备选（单网口时） |
| C. 决策搬 PC | 郑的 Python 跑 PC | 最小 | ❌ 车不自主，用户已否决 |
| **D. 结果走串口线** | PC 用两个 USB-TTL 背靠背连板子 USB 口 | 零 Verilog，但**没有 SSH** | ✅ 已实现，见 `方案1_单网口_实施步骤.md` |

方案 D 是"只用一个网口"的正解：图像走 PL 口，视觉结果走串口线，两件事分开跑。
⚠️ 前提是你能接受"唯一网口是 PL 口 → 不能 SSH"。开发时临时插回 PS 口即可。

### 断点 ⑤ 雷达接板子

郑的 `radar_provider.py` 写死 `COM5`（PC 串口）。上车改两处：

| 接法 | 设备节点 | 改动 |
|---|---|---|
| **USB-TTL 插 PYNQ 的 USB 口**（最省事） | `/dev/ttyUSB0` | 改 `d6_protocol.PORT` 一行 |
| 接 PS 的 UART1（MIO） | `/dev/ttyPS0` | 要在 Block Design 里开 UART1 |

⚠️ D6 的线序：**黄 5V / 白 GND / 红 TXD / 黑 RXD**，接 PYNQ 时要**交叉**（雷达 TXD → PYNQ RXD）。
⚠️ PYNQ 的 USB 口要能识别 CH340（内核一般自带 `ch341` 驱动，`ls /dev/ttyUSB*` 确认）。

---

## 6. 分四阶段

### 阶段 0：纯软件联调 ✅ 已完成

```bash
cd D:/dasanshang/fpga竞赛/integration
python contract.py                  # 契约自检
python run_integration_demo.py      # 7 场景走完整链路（每场景 5 帧）

cd ../zheng && python -m pytest tests/ -q    # 32 个决策 + 雷达测试
```

**验收**：7 个场景规则号 `OK / R1 / R2 / R4 / R1 / R5 / R4`，pytest **32 passed**。

⚠️ 第 3 个场景（视觉单源）的正确输出里必须带 `conf=0.81 x5` —— 没有这个后缀说明抗误检机制没生效。

### 阶段 1：补齐两个缺口（欧阳 + 徐，各 1~2 天）

| 谁 | 做什么 | 验收 |
|---|---|---|
| 欧阳 | YOLO 后处理进实时循环 + 调 `VisionSender.send()`；推理提到 ≥7Hz | PC 上连续 30 秒，`vision_receiver.py` 显示 ≥7Hz、0 拒收 |
| 徐 | 交出 `motor_diff.v` + xdc；加 AXI4-Lite 壳（3 个寄存器） | Vivado 综合过，PS 写 `0x04` 能看到 PWM 变化 |

### 阶段 2：合并成一个 Zynq 工程（徐主导）

1. 新建 Vivado 工程 → Block Design → 加 `ZYNQ7 Processing System`
2. 使能 `M_AXI_GP0`（给电机）、`FCLK_CLK0` 50MHz、UART1、以太网（PS 口）
3. 欧阳的 `ov5640_udp_pc` 改成**无时钟输入的 PL 子模块**（用 `FCLK_CLK0`，删掉自己的 `sys_clk/sys_rst_n` 端口）
4. 徐的 `motor_axi` 挂 AXI 总线
5. **引脚去重**：`U18/N16` 只保留在顶层
6. Generate Bitstream → 导出 `.xsa`

**验收**：一个 bit 烧进去，同时做到——摄像头出图、电机能转、`mmio.write(0x04, 500)` 能让左轮变速。

⚠️ **不要直接打开欧阳那个 2020.2 的工程升级**，新建工程手工搬 RTL 更稳。

### 阶段 3：PS 上跑 Python（郑）

1. 雷达改 `/dev/ttyUSB0`
2. 视觉：`vision_receiver.py` 起后台，`VisionProvider` 接上
3. 电机：`mock_board.py::_apply()` 里的打印换成 `mmio.write()`
4. 把 `contract.py` 一起拷到板子上（只依赖标准库）

**验收**：车上电，`python3 run.py --source live` 跑起来，`logs/*.jsonl` 有 rule 字段。

### 阶段 4：实车标定

| 项 | 工具 |
|---|---|
| 雷达 0° 装车偏角 | `zheng/calibrate_zero.py --survey 20` |
| 差速转向强度 `TURN_GAIN` | 改 `contract.py` 反复试 |
| PWM 满量程 `PWM_MAX` | 问徐，或实测 |
| 危险距离 `CFG["danger_m"]` | 按底盘尺寸和刹车距离调 |
| 抗误检参数 | `CFG["side_confirm_frames"]` / `r2_confirm_frames` / `r3_confirm_frames` / `vision_single_conf`，见第 4.3 节 |

---

## 7. 本目录交付了什么

| 文件 | 跑在哪 | 作用 |
|---|---|---|
| `contract.py` | 三方共用 | **语义唯一转换层**：郑 control→徐差速 PWM；欧阳检测框→郑 Vision JSON；视觉频率契约 |
| `run_integration_demo.py` | PC | 7 场景纯软件联调，暴露接缝 |
| `mock_board.py` | PC | 假板子（TCP 服务端），收 control 打印 PWM |
| `vision_sender.py` | **PC（欧阳用）** | YOLO 结果 → Vision JSON → UDP 发 PS |
| `vision_receiver.py` | **板子 PS（郑用）** | UDP 收 Vision JSON → VisionProvider，带频率告警 |
| `bench_decision.py` | PC / 板子 | 量决策链路 CPU 占用，回答"要不要改成 C" |
| `preflight.py` | PC / 板子 | **上板前自检**：环境/依赖/串口/雷达实测/PL/AXI/网络，逐项给判据和修法 |
| `vision_serial.py` | PC + 板子 | **单网口方案**：视觉结果走串口线，接口与 UDP 版完全一致 |
| `方案1_单网口_实施步骤.md` | 三人共用 | 只用一个网口的完整实施步骤（接线、改哪几行、四步验证、踩坑） |
| `三人分工_详细任务清单.md` | 三人共用 | **按人拆到任务级**：每人做什么、四个集成节点、依赖与卡点 |
| `上板测试_现场操作手册.md` | 三人共用 | **上板当天照着做**：物料清单、接线顺序、5 个阶段、故障速查、时间片、收工清单 |
| `镜像选型对比.md` | 三人共用 | **A 盘 6 套镜像逐一体检 + 六维对比**，结论用 PYNQ 2.7.0(7020)；含 apt 源修复 |
| `check_image.py` | PC | **验证任意 .img**：MBR/FAT/ext4 解析、BOOT 区文件清单、启动判据 |
| `check_sdcard.py` | PC | **验证已插的 SD 卡**：枚举盘符、找 BOOT.BIN/image.ub、判断能否启动 |

另外**改动了郑的两个文件**（原文件已备份到 `zheng/_backup_20261024/`）：

| 文件 | 改了什么 |
|---|---|
| `zheng/protocol.py` | `CFG` 新增 `side_confirm_frames` / `r2_confirm_frames` / `vision_single_conf` 三个键 |
| `zheng/fusion/machine.py` | 分扇区确认计数 + R2 三分支（见第 4 节） |
| `zheng/tests/test_fusion.py` | 新增 4 个抗误检测试（17 → 21） |

### 常用命令

```bash
cd D:/dasanshang/fpga竞赛/integration

# 1. 契约自检
python contract.py

# 2. 7 场景联调（不需要硬件）
python run_integration_demo.py

# 3. 验证下行链路（假板子）
python mock_board.py --port 8000
#   另一个终端： cd ../zheng && python run.py --source live --pynq 127.0.0.1:8000

# 4. 验证视觉链路（本机回环）
python vision_receiver.py --port 5000
#   另一个终端： python vision_sender.py --ip 127.0.0.1 --port 5000 --hz 12
```

---

## 8. 现在就该做的四件事

1. **看板子背面有几个 RJ45** —— 两个 → 按本方案走；一个 → 通知我改方案 B
2. **欧阳改输出 + 提频率** —— YOLO 后处理进循环，`VisionSender.send()` 接上，≥7Hz
3. **催徐交 `motor_diff.v`** —— 没有源码阶段 2 开不了工
4. **郑在 PYNQ 上配 PS 网口 IP `192.168.1.20`** —— 一条 `ip addr add` 的事

第 1 件最快，但它决定后面所有工作。
