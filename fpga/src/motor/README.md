# fpga/src/motor —— AXI4-Lite 电机控制器

**文件**：[`motor_axi.v`](motor_axi.v)（275 行，可综合）
**作用**：把「速度」从 RTL 常量变成 PS 可写寄存器，让 Python 侧能实时改速度、并且**PS 死机时硬件自己停车**。

## 为什么不能只用软件 PWM

| 项 | 软件 PWM（Python/线程） | 本模块 |
|---|---|---|
| 载波 | 毫秒级抖动 | 20 kHz 固定（`CLK_HZ/PWM_HZ = 2500` 周期） |
| PS 卡死 | 电机保持最后一个速度继续跑 | 300 ms 没收到心跳 → **硬件急停** |
| 标定 | 每次改速度要改代码 | 写寄存器即可（`0x04/0x08`） |

## 寄存器（与 `docs/interface.md` §6 一致，改一处必须同步另一处）

| 偏移 | 名称 | 读写 | 说明 |
|---|---|---|---|
| `0x00` | `CTRL` | W | bit0 使能（0 = 强制停车）；bit1 清急停 |
| `0x04` | `SPEED_L` | W | 有符号 16 位，−1000 ~ +1000（负 = 反转） |
| `0x08` | `SPEED_R` | W | 同上 |
| `0x0C` | `HEARTBEAT` | W | 每 100 ms 写一次递增数；300 ms 不变 → 急停 |
| `0x10` | `ENC_L` | R | 左编码器计数（未接恒 0） |
| `0x14` | `ENC_R` | R | 右编码器计数 |
| `0x18` | `STATUS` | R | bit0 看门狗超时；bit1 急停锁死；bit2 电机使能中 |

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `CLK_HZ` | 50_000_000 | **必须与实际 AXI 时钟一致**，否则 PWM 频率与看门狗时间一起偏 |
| `PWM_HZ` | 20_000 | 超出声，电机不啸叫 |
| `WDT_MS` | 300 | 看门狗超时（与 `board_main.py --watchdog-ms` 的软件侧 500 ms 配合：硬件兜底更严） |

## 接入步骤（Vivado）

1. `Create and Package New IP` → `Create AXI4 Peripheral` → 选 **Edit IP**，把 `motor_axi.v` 内容贴进生成的 wrapper（或直接例化 + 手工连 AXI Interconnect）。
2. 时钟接 PS `FCLK_CLK0`（50 MHz），复位接 `peripheral_aresetn`（低有效）。
3. 电机引脚沿用 `motor_pins.xdc`（本仓库未包含：该约束在原工程 `xu/project_1`，**待补**到 `fpga/src/motor/`）。
4. PS 侧对应代码：`host/app/integration/run_on_board.py::MotorBridge`（`pynq.MMIO` 读写）。

## 验证（做之前先看这条）

- 目前**没有 Testbench**：`motor_axi.v` 只有在完整工程里综合过（原工程里是一个 IP import 的 `.dcp`），**没有仿真与示波器证据**。
- 报「FPGA 承担了实时控制」之前必须补：① 仿真（心跳超时 → PWM 归零）；② 示波器截图（20 kHz 占空比）+ 停写心跳后的急停波形，放进 `metrics/evidence/`。
- **待补**：`motor_pins.xdc`、`tb_motor_axi.v`、`.xsa`/`.hwh`。
