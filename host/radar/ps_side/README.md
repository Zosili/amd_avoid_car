# PS 侧雷达桥（最终上车形态）

雷达**插在板子 PS 端的 USB**、由 Linux 读串口、写 AXI GPIO 给 PL 的那条路。
这是最终要让车自己带着雷达跑时的形态；现在调试阶段雷达仍插 PC，别搞混。

## 什么时候用这个

- 现在（调试）：**不用**。雷达插 PC，走 `run_radar_bridge.bat` + `--lidar udp`。
- 最终（上车）：雷达插板子 USB，跑本目录的 `radar_ps_bridge.py`。

## 它做什么

```
COIN-D6 ──PS USB(/dev/ttyUSBx, 230400)──> radar_ps_bridge.py
   └─ 解析 D6 帧 → 0.5° 分箱 → 前方 ±35° 最近障碍
   └─ 写 AXI GPIO 0x41200000： [15:0]=距离mm  [23:16]=心跳(每次+1)
                                          └──────> PL motor_decision 第二路
```

## 依赖（零第三方，板子没网没 pip 也能跑）

只用 Python 标准库：`os` 直接读串口、`termios` 设 8N1、`mmap` 写 `/dev/mem`。
**不 import serial，不依赖 devmem**（mmap 失败才回退 devmem）。

## 板子上怎么跑

前提：板子 SD 卡里是带 `/dev/ttyUSB*` 和 Python3 的 Linux（正点原子 PetaLinux 一般有）。

```bash
# 传到板子（无网时用 U 盘 / zmodem，见 HANDOVER 文档）
python3 radar_ps_bridge.py                  # 自动找 /dev/ttyUSB0
python3 radar_ps_bridge.py --dry            # 只打印解析结果，不写寄存器（先调这个）
python3 radar_ps_bridge.py --corridor 35    # 前方走廊半宽
```

先 `--dry` 看有没有稳定输出「前方最近 x.xx m」，确认串口通了再正式跑。

## 位域约定（和 PL 严格对齐，别改）

| 位 | 含义 |
|---|---|
| `[15:0]` | 最近障碍距离 mm，`0xFFFF` = 无回波 |
| `[23:16]` | 心跳计数，每次刷新 +1 |

PL 的 `motor_decision` 需要**再加第二路输入 + 看门狗**（现在还只有相机那一路）。
融合规则：两路都有效取 min；只有一路有效用那一路；都超时 → SAFE。

## 语义提醒

本脚本算的是**最近障碍**（不分人/墙/椅子），不是"最近的人"。
所以空房间里有墙时，它会持续报墙的距离让车停——这是**避障**语义，跟相机那路
"最近的人"不一样。最终行为由 PL 里的融合规则决定（取 min = 墙比人近时躲墙）。

## 无硬件验证

解析/分箱/心跳位域的逻辑已经用构造帧单测过（校验篡改能被捕获、前方取最近、
心跳组装正确）。要在 PC 上完整跑通串口那一段，可以用假雷达喂一个伪终端：

```bash
# PC 上（需 socat 或 socat 替代）：
# socat -d -d pty,raw,echo=0 pty,raw,echo=0   # 生成一对 /dev/pty/XX
# 一个终端往 pty 写 D6 帧，另一个跑 radar_ps_bridge.py --port <那个pty> --dry
```

> 真正验证还是等板子回来：雷达插 PS USB，`--dry` 跑通，再正式写寄存器，
> 用 `devmem 0x41200000` 读回确认 `[15:0]` 是合理距离。
