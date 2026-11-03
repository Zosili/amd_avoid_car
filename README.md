# Car7020 —— 行人感知 + 雷达避障小车

Zynq-7020（PL 决策）+ Windows PC（YOLO 感知）+ COIN-D6 雷达（板载 PS）三端方案：

- **相机**：OV5640 图像经 PL 采集，UDP 推到 PC，PC 只回答"最近的人有多远 + 往哪绕"（5 字节帧）
- **雷达**：COIN-D6 杜邦线接板子 J4，PS 的 PYNQ 读串口 → 算前方障碍距离/方向 → 写 AXI GPIO → PL
- **决策**：全在 PL 的 `motor_decision` 硬件状态机，两路距离取更近，断流 400ms 自动停车静音

当前状态：**相机视觉停车 + 雷达避障转向都已实测通过**，雷达已上板并配置开机自启。

---

## 架构总览

```
相机 OV5640 ──PL采集──> UDP(1234) ──> PC YOLO ──> UDP(5000) 5字节帧 ──┐
                                                                        ├──> PL motor_decision ──> 电机/蜂鸣
雷达 COIN-D6 ──J4(U8/U9)──> AXI UART Lite ──> PS PYNQ ──> AXI GPIO ──┘
```

| 链路 | 通路 | 说明 |
|---|---|---|
| 视觉 | 相机→PL→PC→UDP→PL | PC 只当感知前端，跑 YOLO + 单目测距 |
| 雷达 | 雷达→J4→PS→GPIO→PL | 板载独立，**不走 PC、不走网线**，开机自启 |
| 决策 | PL `motor_decision` | 两路距离取更近，硬件状态机，看门狗 |

## 决策规则（PL 端，`motor_decision.v`）

| 场景 | 行为 |
|---|---|
| 人 > 3m | 前进 **70%**，静音 |
| 人 1~3m | 前进 + 每秒一声短鸣（120ms） |
| 人 < 1m | **停车** + 连续蜂鸣 |
| 看到人但**测不出距离** | 按 WARN 处理：**鸣叫、不停车**（不误判为“无人”） |
| 障碍 **< 15cm**（前方 ±60°） | **急停** + 中速 3 声（>25cm 解除） |
| 障碍 15~100cm | **差速原地转绕行**（不停车、不响） |
| 人和障碍同时 | 取更近；人 <1m 停车优先 |
| 雷达掉线（500ms 无心跳） | 停车 + 中速 3 声 |
| 断流 400ms | 停车 + 静音（看门狗 SAFE） |
| 上电/从未收到帧 | 停车 + 静音（不会自己跑） |

**转向（开环，2026-11-03 起）**：外侧 **80%**、内侧**反向 70%**（真·原地转）。
方向按“通道交叉”修正过：左转降右通道、右转降左通道。
`eff_steer` **直连**占空比与方向位，无最小保持时间（曾加过 300ms，实测转向发木，已移除）。

**雷达左右判据**：`ps_radar_fusion.py` 的 `--flip-lr`，默认 `0`（正装）。
雷达若倒置安装才设 `1`；**正装却设成 1 会让转向整体反掉**（现象：该左转却一个劲右转）。

> 为什么改成开环：右编码器实测失效（J4-25/J4-4 恒 3.3V 不跳变），闭环拿不到反馈。
> 修好后把 `OPENLOOP_ALL` 改回 0 即可恢复双路闭环（`speed_pid` / `encoder_counter` 代码都在）。

## 协议

**视觉距离帧（PC → PL，UDP 5000，5 字节）：**

```
0xAA 0x55 | 距离(u16 大端, mm) | 转向(1 字节)
   0xFFFF = 无人
   0xFFFE = 有人但测不出距离（PL 按 WARN 处理：鸣叫、不停车）
   其余   = 距离 mm
                                           转向 0=直行 1=左转 2=右转
```

> `0xFFFE` 是 2026-11-03 新增的：原来“有人但测不出距离”和“无人”都发 `0xFFFF`，
> PL 就会当没人放行，出现“PC 显示 STOP、轮子还在转”。

**图传启动命令（PC → PL，UDP 1234）：** 单字节 `'1'`（0x31），`'0'` 停止。

**雷达 GPIO（PS → PL，`axi_gpio_0 @ 0x41200000`）：**

```
[15:0]  障碍距离 mm（0xFFFF = 无）
[23:16] 心跳（每次刷新 +1，PL 判新鲜度）
[25:24] 转向方向（0=直行 1=左转 2=右转）
```

---

## 目录

```
car7020/
├── README.md                  本文件（项目简介 + 复现步骤 + 目录对照）
├── CHANGELOG.md               版本变更记录
├── run.bat                    一键启动（薄壳，转调 host/deploy/run_fusion.bat）
├── docs/
│   ├── architecture.md        系统架构、时钟域、状态机、模块清单
│   ├── interface.md           AI PC ↔ FPGA 接口定义（UDP 协议、引脚、AXI 邮箱）
│   ├── hardware_setup.md      BOM、接线、供电、上电顺序、坑、排查顺序
│   └── environment.md         运行环境（OS/驱动/依赖版本/板卡型号）
├── fpga/
│   ├── src/
│   │   ├── rtl/               全部 Verilog（决策在 motor_decision.v）
│   │   ├── constraints/       引脚 + CDC 约束
│   │   ├── tcl/               重建工程 / 综合实现 / insert_ila
│   │   ├── ip/                IP 的 xci
│   │   └── sw/                PS 源码 + JTAG 脚本
│   ├── build/                 位流 / hwh / xsa / BOOT.BIN + 构建脚本
│   ├── pynq/                  ★ PYNQ 交付（雷达上板用）
│   ├── report/
│   │   ├── timing.md          资源占用 + 时序收敛
│   │   ├── verification.md    功能正确性验证（仿真 / ILA / 端到端）
│   │   ├── timing_summary_routed.rpt   ★ 原始时序报告（skill/check_timing.py 的输入）
│   │   └── utilization_placed.rpt      ★ 原始资源报告
│   └── src/xdc/               J4 针脚对照图
├── host/
│   ├── model/                 best.onnx + MODEL.md
│   ├── app/                   检测主程序、颜色补偿 + fusion/
│   ├── radar/                 COIN-D6 雷达桥 + 点云视图 + ps_side/
│   └── deploy/                run_fusion.bat + requirements.txt
├── metrics/
│   ├── metrics.csv            ★ 指标汇总表（规范表头）
│   ├── README.md              指标表说明 + 证据索引 + 未提供证据说明
│   ├── logs/                  训练曲线、联调日志、实现日志
│   ├── scripts/               四档测试、链路探针、角度自检、假雷达
│   └── evidence/              ILA 抓包、实拍帧、四档与雷达行为记录
├── training/                  ★ 数据与训练管线（模型是怎么来的）
│   ├── README.md              管线 13 个阶段 + 复现步骤
│   ├── train_yolo.py          训练入口（配置在文件顶部）
│   ├── data_living.yaml       19 类数据集配置
│   ├── tools/                 43 个脚本：采集/清洗/标注/审计/评测
│   └── reports/               三份训练与选型分析报告
├── skill/                     ★ 可复用能力包（5 个可独立运行的判定工具）
│   ├── README.md              五步定位阶梯 + 五个工具的四要素说明
│   ├── overview.md            这个目录是什么、为什么算可复用（写报告可引用）
│   ├── lessons.md             9 条实战坑（含适用范围/失效条件/复用结果）
│   ├── hwh_probe.py           只读解析 .hwh：这份 bitstream 里有没有那个外设
│   ├── radar_probe.py         串口传感链路探针（帧率/坏帧率/有效点率/帧长）
│   ├── bench_infer.py         推理基准（预热与稳态分离、报分位数）
│   ├── latency_stats.py       控制环延迟统计（按文件独立、报未命中率）
│   ├── check_timing.py        Vivado 报告判据化，直接给 PASS/FAIL
│   ├── selftest.py            一条命令跑完离线自检（CI 可用）
│   └── requirements.txt       依赖清单
└── report/design_report.md    竞赛设计报告
```

### 与推荐目录结构的对照

比赛推荐结构见 `3.2.5.5`。本包基本一致，差异与原因如下：

| 推荐位置 | 本包位置 | 说明 |
|---|---|---|
| `docs/architecture.md` | 同 | — |
| `docs/interface.md` | 同 | — |
| `docs/hardware_setup.md` | 同 | — |
| （无） | `docs/environment.md` | 补：运行环境从零重建说明（3.2.5.2 要求） |
| `fpga/src/` | 同 | RTL + 约束 + BD 生成脚本 |
| `fpga/build/`（构建脚本 + 产物） | 脚本在 `fpga/build/build.bat|sh`，实体 tcl 在 `fpga/src/tcl/` | tcl 必须与 RTL 同级才能用 `[info script]` 推算根目录；`build/` 下留薄壳入口 |
| `fpga/report/` | 同 | `timing.md` + `verification.md` + 两份**原始 .rpt**（供脚本复核） |
| （无） | `fpga/pynq/` | PYNQ overlay 交付（.bit/.hwh/雷达桥/服务），因本项目用 PYNQ 而非裸机 |
| `host/model/` | 同 | — |
| `host/app/` | 同 | — |
| `host/deploy/` | 同 | — |
| （无） | `host/radar/` | COIN-D6 雷达驱动与桥接（可独立于整车使用） |
| `metrics/` | 同 | `metrics.csv` + `logs/` + `scripts/` + `evidence/` |
| `skill/README.md` | 同 | 另有 `overview.md`（定位）与 `lessons.md`（经验），三份分工见 skill 目录 |
| （无） | `training/` | 数据集构建与训练脚本。推荐结构未规定，但报告要写"模型怎么来的"，单列更清楚 |
| `report/` | 同 | `design_report.md` |

> 所有目录名与文件名均为**纯英文小写/下划线**，符合"仓库内不得出现中文、空格或特殊字符"的要求；
> 中文只出现在文件正文内。

### fpga/pynq/ —— 雷达上板交付（重要）

雷达最终接板子（脱离 PC），由 PYNQ 的 PS 读串口。这组文件让板子开机自动跑雷达：

| 文件 | 作用 |
|---|---|
| `motor.bit` + `motor.hwh` | PYNQ overlay 位流（含 AXI UART Lite + 雷达融合） |
| `ps_radar_fusion.py` | PS 侧脚本：读串口 → 算前方障碍 → 写 GPIO |
| `radar.service` | systemd 服务（开机自启） |
| `start_radar.sh` | 启动脚本（等 PYNQ 就绪 → 加载 overlay → 跑雷达） |
| `load_check.py` / `loopback_test.py` | 调试：查 IP / 回环测 UART |

---

## 快速开始

### 方式 A：完整联调（相机 + 雷达，推荐）

**前提**：板子插 PYNQ 卡（SD 卡 Linux），雷达杜邦线接 J4（U8/U9 交叉 + 共地 + 单独供电），网线接 PL 网口，PC 网卡 `192.168.1.102`。

1. **板子上电**，等约 60 秒 —— systemd 自动加载 overlay + 自动跑雷达，无需 SSH
2. **双击 `run.bat`** —— 弹窗显示相机画面 + 检测框
3. 验收：
   - 视觉：人走近 **<1m → 停车 + 连续长鸣**；退到 1~3m → 边走边每秒短鸣
   - 视觉：人走出画面 → **约 1s 后才恢复行驶**
   - 雷达：障碍放车头 **15~100cm → 差速原地转绕行**（不停车、不响）
   - 雷达：障碍再靠近到 **<15cm → 急停 + 中速 3 声**
   - 雷达：拔掉雷达杜邦线 → 约 0.5s 后 **停车 + 中速 3 声**

### 方式 B：纯视觉（雷达不上板）

雷达插 PC USB（TTL 转 USB），跑 `host/radar/run_radar_bridge.bat` 起雷达桥，融合脚本 `set LIDAR=udp` 从 15001 取扫描。

### 环境

```bash
pip install -r host/deploy/requirements.txt
```

### 雷达上板（方式 A）的板子侧部署

```bash
# 把 fpga/pynq/ 传到板子（如 /home/xilinx/radar/），然后：
chmod +x start_radar.sh
sudo cp radar.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable radar.service
sudo systemctl start radar.service   # 立即启动
```

> 三个必须知道的坑（详见 `skill/lessons.md`）：
> ① overlay 加载要 `XILINX_XRT=/usr` + sudo（否则 "No Devices Found"）
> ② FCLK0 实际 55.56MHz（不是 100MHz），UART 波特率参数已按此配好
> ③ AXI UART Lite FIFO 只 16 字节，脚本已用 RX_FULL 批量读避免溢出

---

## 已知限制

| 限制 | 影响 | 现状 |
|---|---|---|
| **右编码器失效** | 只能开环跑，车速/转向量随电池电压漂移 | J4-25/J4-4 恒 3.3V；修好后 `OPENLOOP_ALL` 改 0 |
| 开环无过流/堵转/过热检测 | 内轮反向 70% 有反电动势冲击 | 靠 L298N 自身保护；可降 `DUTY_TURN_IN` |
| 测试模式 `steer=3` 绕过全部保护 | 误发一帧就裸控占空比 | 跑车务必确认 PC 发 `steer=0/1/2` |
| 相机配置只在配位流时做一次 | 软重配/时序不巧会花屏 | 判据：帧长 307200；断电重上电再配 |
| 帧率约 5~6 fps | 快速横穿可能漏检 | 瓶颈在 OV5640 配置，非推理 |
| person 样本少 | 只露一条腿/玻璃门后/只露脚会漏 | 需补数据；加轮数已证无效 |
| 单目测距依赖相机高 | `CAM_HEIGHT` 未标定时误差大 | 测不出时按 WARN 处理，近距离靠雷达 15cm 兜底 |
| 雷达装车底 | 离地 1~2cm 时会打出恒定地面回波 | 需抬升安装；`--avoid-min` 可滤下限 |

## 文档导航

| 想了解 | 看这里 |
|---|---|
| 为什么这么划分软硬件 | `report/design_report.md` 第 2 节 |
| 系统架构与数据流 | `docs/architecture.md` |
| AI PC ↔ FPGA 协议怎么定 | `docs/interface.md` |
| BOM / 接线 / 供电 / 排障 | `docs/hardware_setup.md` |
| 环境从零重建（OS/驱动/依赖版本） | `docs/environment.md` |
| 指标数值与证据在哪 | `metrics/metrics.csv` |
| 功能正确性怎么验证的 | `fpga/report/verification.md` |
| 资源占用与时序 | `fpga/report/timing.md` |
| 重建位流 | `fpga/build/README.md` |
| 模型来源与精度 | `host/model/MODEL.md` |
| 雷达协议与标定 | `host/radar/d6_bridge_notes.md` |
| 踩过哪些坑（可复用工作流） | `skill/lessons.md` |
| 有没有可复用的判定工具 | `skill/README.md`（用法）+ `skill/overview.md`（为什么） |
| 模型是怎么训出来的 | `training/README.md`（管线）+ `training/reports/`（结论） |
| 版本变更 | `CHANGELOG.md` |
