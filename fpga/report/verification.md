# 功能正确性验证报告

目的：说明**怎么确认硬件功能是对的**，而不只是"编过了"。

## 1 三层验证策略

| 层 | 手段 | 覆盖什么 | 局限 |
|---|---|---|---|
| L1 单元仿真 | ModelSim / Vivado Simulator 跑 `fpga/src/rtl/**/tb_*.v` | 电机时序、差速、停锁、PID 环路 | 只覆盖纯逻辑，不含 IO/时序 |
| L2 板级抓取 | Vivado ILA（`fpga/src/tcl/insert_ila.tcl` 可复现插入） | UDP 帧解析、跨时钟域、真实时序 | 需要重新综合，仅调试期使用 |
| L3 端到端 | 发真实帧 + 观察轮子/蜂鸣器/雷达日志 | 整条链路的功能正确性 | 人工观察，非仪器化 |

## 2 L1：仿真

| Testbench | 位置 | 验证内容 |
|---|---|---|
| `tb_motor_actions.v` | `fpga/src/rtl/motor/` | 前进/后退/转向/停止的动作组合 |
| `tb_motor_diff.v` | 同上 | 差速转向：两侧占空比关系 |
| `tb_motor_seq.v` | 同上 | 指令序列与状态迁移 |
| `tb_stop_lock.v` | 同上 | `stop_lock` 硬钳位（置 1 时不出 PWM） |

运行方式（Vivado 自带仿真器）：

```bash
vivado -mode batch -source fpga/src/tcl/02_build.tcl   # 先确保工程存在
# 或在 Vivado GUI 里把对应 tb 设为 simulation top 后 Run Simulation
```

> 交付包内**未附仿真波形文件**。这些 tb 用于回归改逻辑时自检，不属于本次现场验证证据。

## 3 L2：ILA 板级抓取

调试期用 ILA 抓过 UDP 帧解析链路，这是定位"帧全收到但 `dist_valid` 永不置位"那个
状态机 bug 的关键——靠现象和打印永远查不出来。

证据（随包提供）：

| 文件 | 内容 |
|---|---|
| `metrics/evidence/ila_udp_frame.csv` | ILA 导出的 `udp_rec_data` / `udp_rec_en` / `rx_state` / `dist_valid` 波形数据 |
| `metrics/evidence/frame_decoded.png` | 抓到的帧在 PC 端解码出的图像 |

复现插入方法：`fpga/src/tcl/insert_ila.tcl`。

> 该 bug 的根因与修复写在 `skill/README.md` 的 S1 一节：状态数必须等于字节数，
> 多写一个状态等"下一拍组装"，包已被 `udp_rec_pkt_done` 打回 IDLE，距离永远提交不了。

## 4 L3：端到端功能验证

| 验证项 | 判据 | 结果 | 证据 |
|---|---|---|---|
| 四档动作（CLEAR/WARN/STOP/断流） | 轮子与蜂鸣器符合预期 | 4/4 通过 | `metrics/evidence/four_state_check.md` |
| 图传帧格式 | 单帧 614408 B | 通过 | `metrics/evidence/frame_decoded.png`、`metrics/logs/fusion_live_tune.log` |
| 雷达急停/避障/掉线 | 见文档 | 通过 | `metrics/evidence/radar_behaviour_check.md` |
| 真实雷达扫描 + 人框配对 | `source=lidar` 且距离合理 | 1.93 m 命中 | `metrics/scripts/match_check.py` |
| 时序收敛 | WNS/WHS 均为正、0 违例 | WNS +1.071 / WHS +0.014 | `fpga/report/timing.md` |
| 位流完整性 | 板端 md5 与构建产物一致 | 一致（`cf922bec…`） | 部署日志 |

## 5 已知未覆盖项（诚实说明）

| 项 | 现状 | 影响 |
|---|---|---|
| 右编码器闭环 | 编码器失效，当前全开环 | 无速度反馈，车速随电压漂移；无堵转过流保护 |
| 单目测距精度 | 未做像素级标定 | 测不出距离时按 WARN 处理，近距离靠雷达 15 cm 兜底 |
| 长时间稳定性 | 未做 ≥1 h 连续跑车 | 电池衰减后的行为未量化 |
| 多目标场景 | 只取最近的人 | 多人同框时不做身份跟踪 |
| 转向换向电流冲击 | 内轮反向瞬间电流未测（300 ms 保持已移除） | 极端情况下可能触发 L298N 过流保护 |

## 6 结论

- **数字逻辑正确性**：L1 仿真覆盖电机动作/差速/停锁；L2 ILA 抓包已实证 UDP 帧解析链路；
- **系统功能正确性**：L3 端到端四档 + 雷达行为全部通过；
- **时序**：0 违例，WNS 余量 1.071 ns，可稳定运行；
- **未闭环的部分**（编码器、单目标定）已在 `docs/hardware_setup.md` §5 与
  `README.md` 已知限制表中明确标注，不影响当前交付形态的可用性。
