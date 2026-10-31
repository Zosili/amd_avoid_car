# 硬件搭建、接线与供电

> 对应指南 3.2.5.1(4)「传感器、执行器、驱动板与机械结构清单 + 接线说明，使他人可复原同一套物理装置」。
> 标「**待补**」的项是还没实测/没记下来的，别猜；实测后回来填。

---

## 1. 物料清单（BOM）

| # | 部件 | 型号 / 规格 | 数量 | 状态 |
|---|---|---|---|---|
| 1 | FPGA 板卡 | 正点原子 **领航者 V2**，器件 `xc7z020clg400-2` | 1 | 已有 |
| 2 | 系统镜像 | **PYNQ 2.7.0**（7020 版），SD 卡 ≥ 16 GB | 1 | 已烧写 |
| 3 | 360° 激光雷达 | 国科光芯 **COIN-D6**（dToF，905 nm Class 1，0.05–12 m，0.9°，IPX4，40 g，52.7×45.2×34.4 mm） | 1 | 已有，已体检 |
| 4 | 雷达 USB 转串口 | **CH340**（`USB\VID_1A86&PID_7523`），随雷达附带 4 pin 线 | 1 | 已有 |
| 5 | 摄像头模块 | **OV5640**（DVP，RGB565 输出） | 1 | 已有（PL 链路已上板） |
| 6 | 电机驱动 | TB6612FNG（或 L298N，二选一，接法见 §5） | 1 | **待补**（实际型号未记录） |
| 7 | 直流减速电机 | 带编码器 / 不带编码器均可（不带则 `enc_l/enc_r` 恒 0） | 2 | **待补**（减速比、空载转速未记录） |
| 8 | 车体底盘 | 超椭圆变体 `|x/b|^n + |y/a|^n = 1`，n≈2.2，**35 × 18 cm** | 1 | 已有 |
| 9 | 电池 | 12 V 组（Status 示例 `vbat=12.1`） | 1 | **待补**（容量、放电曲线未记录） |
| 10 | 网络 | 板载千兆以太网（RGMII）→ PC 或交换机 | 1 | 已有 |
| 11 | 跳线帽 | P4 位置 2 个（HOST 模式必需） | 2 | **必查** |

实物与原理图截图：

| 图 | 文件 |
|---|---|
| 整板俯视 | [`docs/assets/board_overview.png`](assets/board_overview.png) |
| USB HOST 区（雷达插这里） | [`docs/assets/board_usb_area.png`](assets/board_usb_area.png) |
| ATK MODULE 六针（U4） | [`docs/assets/board_atk_module.png`](assets/board_atk_module.png) |
| P5/CH340 原理图页 | [`docs/assets/sch_p5_extio.png`](assets/sch_p5_extio.png) |
| P4 USB HOST/SLAVE 跳线原理图 | [`docs/assets/sch_p7_otg.png`](assets/sch_p7_otg.png) |
| 车体几何与 12 扇区 | [`docs/assets/car_geometry_sectors.png`](assets/car_geometry_sectors.png) |
| 雷达安装方式对比（前伸 vs 抬升 / 椭圆 vs 方形） | [`docs/assets/mount_compare_lift.png`](assets/mount_compare_lift.png)、[`docs/assets/mount_compare_shape.png`](assets/mount_compare_shape.png) |

---

## 2. 板卡资源与两条必查约束

**器件**：`xc7z020clg400-2`，PS = 双核 Cortex-A9 + Linux（PYNQ 2.7.0 = Ubuntu 18.04 + **Python 3.6.9**），PL = 85K LC 级 7020 逻辑。
⚠️ 这块是**正点原子领航者 V2**，不是 PYNQ-Z2 / 启明星，网上 PYNQ-Z2 的教程（引脚、overlay、镜像）不能照抄。

| 约束 | 现象 | 处理 |
|---|---|---|
| **P4 必须跳成 Host** | 出厂 P4 空着时，USB HOST 与 SLAVE **都不通**，雷达插上没反应 | 跳线帽 **3-5、4-6**（见 `sch_p7_otg.png`） |
| **杜邦线进 PL，没 overlay 读不到** | 线接 ATK MODULE / J3 后，PS 里看不到 `/dev/ttyXXX` | 要么走 USB（PS，`/dev/ttyUSB0` 直接可用），要么给 PL 写 UART 接收 + overlay |

---

## 3. 雷达接线（两种方案，选一个）

### 方案 A（推荐先用）：USB 转串口 → 板子 USB HOST

```
雷达 4 pin ──(随附线)── CH340 USB ──→ 板子顶部 USB_HOST1~4
```
- 前提：P4 跳成 Host（§2）。
- 板上设备名：`/dev/ttyUSB0`（PC 上是 `COM5`）。
- 走 PS Linux，`pyserial` 直接读，**不需要 bit/overlay**。
- 已验证：`python3 joint/board_main.py --radar live --radar-port /dev/ttyUSB0 ...`

### 方案 B：杜邦线 → ATK MODULE 六针（U4）

雷达 4 线颜色定义（官方手册 §3）：

| 线色 | 接 | 说明 |
|---|---|---|
| 黄 | **5 V** | 雷达供电 5 V（额定 1.2 W，峰值 1.6 W） |
| 白 | **GND** | 与板子共地 |
| 红 | **TXD**（雷达发） | → 板子 **RXD**（ATK MODULE 上是 `T19`） |
| 黑 | **RXD**（雷达收） | 本方案不接（雷达上电即吐流，不需要发指令） |

需要更多 IO 时走左边缘 **J3 EXT_IO 40P**（引脚表见 [`docs/refs/atk_7020_io_pins.txt`](refs/atk_7020_io_pins.txt)）。

> 方案 B 的线落在 **PL 引脚**上：没有 overlay 时 Linux 读不到，必须在 PL 里写 UART 接收（115200/230400 均可配，但 D6 固定 230400）或做 PS-PL 直连。**当前工程默认走方案 A。**

### 雷达参数（写进代码的常量，改前先看 `host/app/car/radar/d6_protocol.py`）

| 项 | 值 |
|---|---|
| 波特率 | **230400**，8N1，无流控 |
| 帧长 | 85 B（`AA 55` 起始，双字节异或校验） |
| 采样 | 400 点/圈，0.9°/点；实测 **169.5 帧/s**、0 坏帧 |
| 量程 | 0.05–12 m；< 6 cm 返回 0 = 无回波；弱回波标志位在 `Si_L` bit0 |
| 0° 零点 | 外壳上**单个安装耳**方向（不是线束方向），装车偏角用 `ZERO_OFFSET_DEG` 补 |

---

## 4. 相机链路（视觉输入）

```
OV5640 (DVP) ──PL: i2c 配置 + RGB565 抓帧──→ UDP 打包 ──RGMII 千兆网口──→ PC:1234
```
- 帧格式：`f0 5a a5 0f` + `02 80 01 e0`（宽 640 = 0x0280、高 480 = 0x01E0）+ 614 400 B RGB565（**小端**，需字节交换）。
- PC 侧要设静态 IP 与板子同网段（例：PC `192.168.1.102`，板子 `192.168.1.10`）。
- 验证：`host/app/vision_pc/view.py`（看画面）、`test_udp.py`（只看包率）。

---

## 5. 电机与驱动

| 信号 | 板子侧 | 驱动侧 | 说明 |
|---|---|---|---|
| 左轮 PWM | `motor_axi.pwm_l` | TB6612 `PWMA` | 20 kHz 载波（`motor_axi` 参数），人耳听不到啸叫 |
| 左轮方向 | `in1` / `in2` | `AIN1` / `AIN2` | `pwm>0 → (1,0)`，`pwm<0 → (0,1)`，`pwm=0 → (0,0)` 惰行 |
| 右轮 PWM | `pwm_r` | `PWMB` | 同上 |
| 右轮方向 | `in3` / `in4` | `BIN1` / `BIN2` | 同上 |
| 编码器（可选） | `enc_l_a/b`、`enc_r_a/b` | 电机编码器 A/B | 不接则计数恒 0，不影响闭环 |
| 共地 | GND | GND | **必须共地**，否则方向引脚随机翻转 |

- 速度来源：`contract.control_to_diff()` 把 `speed/steering` 换算成 `pwm_l/pwm_r`（满量程 1000，`TURN_GAIN=0.6`，死区 20）。**电机侧只认 pwm/in，不理解 steering。**
- 安全：PL 侧 300 ms 心跳看门狗，PS 卡死/断电自动停车（见 [`docs/interface.md`](interface.md) §6）。
- 若电机驱动板自己带 MCU 并监听 5001：它需要回 Status（`seq` 回显），否则板子 500 ms 后判链路断 → STOP。

---

## 6. 供电

| 域 | 电压 | 来源 | 备注 |
|---|---|---|---|
| 车体动力（电机） | 12 V | 电池组 | 与逻辑电源共地 |
| 板卡 | 5 V（板载 DC 输入，具体接口 **待补**） | 电池经 DC-DC 或独立电源 | 摄像头/雷达不要从板子 5 V 取大电流 |
| 雷达 | 5 V / 1.2–1.6 W | 板子 USB（方案 A）或 ATK MODULE 5 V（方案 B） | USB 供电时注意整板 5 V 余量 |

> ⚠️ 电机启停会造成电源跌落，实测中若出现板子重启/雷达掉线，优先怀疑供电（加电容 / 电机与逻辑分开供电）。**待补**：实测电压曲线（示波器）。

---

## 7. 车体与传感器安装

- 底盘 **35 × 18 cm**（超椭圆，n≈2.2），几何与 12 扇区定义见 [`docs/assets/car_geometry_sectors.png`](assets/car_geometry_sectors.png)。
- 雷达安装高度：发射点距底壳基准面 **26.9 mm**（官方尺寸），实际装车高度 **待补**。
- 雷达**前伸约 3 cm**（避免车体挡住 0°/±15° 前扇区），前伸/抬升两种方案的对比见 `mount_compare_lift.png`。
- 车体/支架会遮挡部分角度：体检记录里 60°–120°、210°–240° 的 0.28–0.40 m 就是手臂+线束，装车后同一位置会被车体占住 → **阈值必须装车后重标**。
- 摄像头视角：前向俯视，需保证画面底部能看到车前 0.5–1.0 m 地面（`cy` 越大 = 越近，见接口文档）。

---

## 8. 从零复原（按顺序，每步都有判据）

| 步 | 动作 | 判据 |
|---|---|---|
| 1 | SD 卡写 PYNQ 2.7.0（7020），插卡上电 | 串口/网口能 SSH 进板子，`python3 -V` = 3.6.9 |
| 2 | P4 跳 Host（3-5、4-6），雷达 USB 插 `USB_HOST1~4` | 板上 `ls /dev/ttyUSB*` 出现 `ttyUSB0` |
| 3 | **L0（PC + 雷达）**：`python3 preflight.py --port COM5` | 帧率 ~169、坏帧 0、三扇区有数 |
| 4 | **L1（板子，不需要 bit）**：`python3 preflight.py --no-radar` | Python 依赖、串口权限、网络全绿 |
| 5 | **L2（板子 + bit）**：`python3 preflight.py` | AXI 寄存器可读，心跳写入后 `STATUS` 无看门狗超时 |
| 6 | **L3（+ 视觉 PC）**：视觉端发 Vision JSON | 板上 `vision_age_ms` < 150，`drops` = 0 |
| 7 | 烧 bit + 启动决策：`python3 joint/board_main.py --motor <IP>:5001` | 日志出现 `R1–R5` 规则与 `pwm_l/pwm_r` |
| 8 | 空载试转 → 落地试跑 | 电机转向与 `in1..in4` 定义一致（反了只改 `contract.DIR_LOGIC` 一处） |

L0–L3 的详细操作与排障表见 [`docs/legacy/on_board_test_manual.md`](legacy/on_board_test_manual.md)、[`docs/joint_debug_manual.md`](joint_debug_manual.md)。

---

## 9. 已知硬件坑（都是踩过的）

1. **P4 不跳 = 雷达完全没反应**（HOST/SLAVE 都不通），别先怀疑驱动。
2. **杜邦线进 PL 读不到**：那是 PL 引脚，PS 里没有 `/dev/tty*`；要么走 USB，要么写 overlay。
3. **桌面环境读数会骗人**：桌面/墙/盒子围着时前扇区只有 0.24 m，手举空中前扇区 1.10 m。阈值必须装车后标定。
4. **雷达 0° 不是线束方向**：是单个安装耳方向；装车后必须先做 `ZERO_OFFSET_DEG` 标定（拿 1 m 处纸箱当参考物）。
5. **两套 5000 端口实现**（TCP/UDP）不能同时起，接板联调只用 TCP 那套（见 [`docs/interface.md`](interface.md) §1）。
6. **板上是 Python 3.6.9**：不能用 dataclass / 海象运算符 / `list[int]` 泛型。
