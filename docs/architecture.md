# 系统架构

## 1 数据流

```
OV5640 (DVP 8bit, 640x480 RGB565)
   │ cam_pclk / vsync / href / data[7:0]
   ▼
┌──────────────────────────── PL ────────────────────────────┐
│ 图像采集 ──异步FIFO──> UDP 分片(1288B) ──> RGMII/MAC ──┐   │
│                                                       │   │
│  UDP 收帧(5000) ──> 帧解析 ──> 四态决策机 ──> PWM/蜂鸣器 │   │
│        ▲                    (eth_rxc 域)      (sys_clk) │   │
└────────┼──────────────────────────────────────────────┼───┘
         │                                              │
         │ UDP 4B 距离帧                        614408B/帧 │
         │                                              ▼
         └──────────────── PC(Windows) ◄────────────── 网线
                    YOLO11n(person) + 单目测距
```

## 2 时钟域

| 时钟 | 来源 | 频率 | 用途 |
|---|---|---|---|
| `sys_clk` | PL 晶振 U18 | 50 MHz | PWM、蜂鸣器、CDC 目标端 |
| `clk_200m` | `clk_wiz_0` | 200 MHz | RGMII 内部（TX 时钟整形） |
| `eth_rxc` | PHY 恢复时钟 | 125 MHz | 以太网收、UDP 收帧、**决策状态机** |
| `cam_pclk` | OV5640 | ~24 MHz | 图像采集，经异步 FIFO 跨到 200 MHz |

**关键点：决策与执行链路全部不依赖 PS 的 `FCLK_CLK0`。** 早期版本挂在 FCLK 上，只烧 PL 位流时 FCLK 不跑，导致电机蜂鸣器全死而以太网正常——这个坑已修，并在 `constraints/7020_top.xdc` 里把 CDC 约束改为 `eth_rxc ↔ sys_clk`。

## 3 决策状态机（`rtl/motor_decision.v`）

```
                    dist 有效?
   ┌────────┐  >3m  ┌────────┐  1~3m  ┌────────┐  <=1m  ┌────────┐
   │ SAFE   │──────>│ CLEAR  │───────>│  WARN  │───────>│  STOP  │
   │停车静音│       │70%前进 │        │70%+间歇│        │停车长鸣│
   └────────┘       └────────┘        └────────┘        └────────┘
        ▲                                                   │
        └──────────────── 400ms 无新帧（看门狗）─────────────┘
```

| 状态 | 条件 | 电机 | 蜂鸣器 |
|---|---|---|---|
| CLEAR | 无人 / >3 m | 70 % 前进 | 静音 |
| WARN | 1~3 m | 70 % 前进 | 120 ms 响 / 1 s 周期 |
| STOP | ≤1 m | 占空比清零 | 连续响 |
| SAFE | 400 ms 无帧 / 上电 | 占空比清零 | 静音 |

> 上电默认 `SAFE`（靠 `ever_rx` 标志），避免"还没收到第一帧就先往前冲 400 ms"。

## 4 帧解析状态机（易错点，务必保持 4 态）

```
IDLE --AA--> HDR1 --55--> HDR2 --高字节--> DHI --低字节--> (提交 dist_mm) --> IDLE
```

**状态数必须等于字节数。** 帧只有 4 字节，若多写一个状态等"下一拍组装"，包已结束会被 `udp_rec_pkt_done` 打回 IDLE，距离永远提交不了（历史 bug，ILA 抓到的）。

## 5 模块清单

| 文件 | 作用 |
|---|---|
| `rtl/top.v` | 顶层：时钟、CDC、决策模块与 PWM/蜂鸣器接线 |
| `rtl/motor_decision.v` | 距离帧解析 + 四态决策 + 看门狗 |
| `rtl/ov5640_udp_pc.v` | 采集/图传子系统，并引出 UDP 收帧信号给决策模块 |
| `rtl/udp/*` | UDP 收发（正点原子参考设计） |
| `rtl/eth_top.v`, `rtl/eth_ctrl.v` | 以太网顶层与 ARP/UDP 协议切换 |
| `rtl/gmii_to_rgmii/*` | RGMII 接口 IP |
| `rtl/ov5640/*` | OV5640 的 I2C 配置与 DVP 采集 |
| `rtl/motor/motor_diff_ctrl.v` | 双路 PWM（20 kHz） |
| `rtl/start_transfer_ctrl.v` | 收单字节 `"1"` 启动图传 |

## 6 PC 端结构

```
host/app/
├── detect_fusion_live.py   主程序：收图 → 推理 → 测距 → 发距离帧
├── colorfix.py             RGB565 解码 + D5 数据线补偿（默认关闭）
├── view.py                 纯看图（不含模型）
├── gpu_detect_live.py      只检测不发指令
└── fusion/
    ├── fusion_core.py      角度映射、窗口匹配、状态机（PC 侧一份影子实现，仅用于显示）
    ├── lidar_ld06.py       **仅 LD06 协议**；COIN-D6 用不了（见 docs/interface.md §5）
    ├── calib_yaw.py        theta0 / sign 标定
    ├── match_check.py      角度映射离线自检（无需硬件）
    └── fake_radar_sender.py UDP 假雷达

host/radar/                  COIN-D6 雷达桥（队友提供，旁挂不改动本工程）
├── d6_stream.py             串口 → 0.5° 分箱 → UDP 15001 JSON（主程序）
├── d6_protocol.py           D6 帧解析（85B AA55 帧 + 异或校验）
├── d6_config.json           端口/角度/掩膜/目标地址
├── radar_view.py            点云视图（12 扇区、1m/3m 阈值圈）
└── run_radar_bridge.bat     一键启动（ASCII + 相对路径）
```

> PC 侧的 `fusion_core` 状态机只用于**画面显示**，真正决定车行为的是 PL 里的状态机。改 PC 侧阈值不会改变车的动作。
