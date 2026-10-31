# AGENTS.md · amd_avoid_car

> 给「接手的 AI agent / 队友」看的操作手册。**先读完 §0–§3，再动任何代码。**
> 人看的完整说明在 [`README.md`](README.md)；协议细节在 [`docs/interface.md`](docs/interface.md)。

## 0. 三条铁律

1. **不许编数据**。`metrics/metrics.csv` 里没测的项就写「待测」，不要填估计值/理论值（评审要顺着证据文件复算，这是硬要求 3.2.5.2）。
2. **不许改契约而不升版本**。`host/app/car/protocol.py`、`host/app/integration/contract.py`、`fpga/src/motor/motor_axi.v` 三处任一改动，必须 `CONTRACT_VERSION += 1` 并同步 `docs/interface.md`。
3. **不许把大文件、密钥、个人绝对路径提交进来**。模型走 `host/model/MODEL.md` 的下载/导出命令，bit/xsa 走 Git LFS，路径一律相对脚本位置推导。

---

## 1. 这是什么（30 秒）

AMD FPGA + Ryzen AI PC 的**避障小车**（2026 嵌入式竞赛 · 具身智能赛道 3.2）：

```
Ryzen AI PC：OV5640 UDP 收图 → YOLO 推理 → Vision JSON（TCP 5000，≥7 Hz）
ZYNQ-7020  ：D6 激光雷达（UART 230400）→ 三扇区 → 融合决策 10 Hz（R1–R5）→ AXI 电机 PWM → 监控广播（TCP 5002）
执行机构    ：TB6612/L298N → 左右直流减速电机
```

- 仓库：`github.com/Zosili/amd_avoid_car`（**private**，仅所有者 + 被邀请的协作者）
- 本地：`D:\dasanshang\fpga竞赛\amd_avoid_car`
- 板卡：正点原子领航者 V2 / `xc7z020clg400-2` / PYNQ 2.7.0（Ubuntu 18.04 + **Python 3.6.9**）
- 工具链：Vivado **2025.2**

---

## 2. 目录地图（先看这张表再动手）

| 路径 | 是什么 | 能改吗 |
|---|---|---|
| `host/app/car/` | 决策核心包（`protocol.py` 消息/阈值、`fusion/machine.py` R1–R5、`radar/d6_protocol.py` 解帧、`comm/`、`vision/`、`sim/`、`tests/`） | ✅ 改完必须跑 pytest |
| `host/app/joint/` | 板端入口 `board_main.py`（雷达→视觉→决策→控制→监控）+ mock_vision/mock_motor + `_run_*.sh` | ✅ |
| `host/app/integration/` | 集成层：`contract.py`（唯一映射）、`run_on_board.py`（AXI 桥）、`radar_map_view.py`（PC 云图）、`preflight.py` | ✅ 改 contract 要升版本 |
| `host/app/radar_console/` | Tk 控制台 + `radar_helper.py probe`（雷达体检） | ✅ |
| `host/app/d6_tools/` | D6 协议验证工具 + `d6_capture.bin`（97 KB 真实抓包） | ✅ |
| `host/app/vision_pc/` | PC 侧视觉：OV5640 收图、YOLO、`ryzenai/`（NPU） | ✅ |
| `fpga/src/vision/` | OV5640→UDP 视觉链路 RTL（17 个 `.v`）+ `.xdc` + 2 个 IP `.xci` | ✅ 改 RTL 必须重跑综合并更新 `fpga/report` |
| `fpga/src/motor/` | `motor_axi.v`（20 kHz PWM + 300 ms 看门狗） | ✅ 同步 `run_on_board.py` |
| `fpga/build/` | `build_project.tcl`（**尚未在新机器完整回归**） | ✅ |
| `fpga/report/` | 综合实现原始报告（资源/时序/DRC/功耗） | ❌ 只由 Vivado 覆盖 |
| `docs/` | `architecture.md / interface.md / hardware_setup.md / migration_notes.md` + `legacy/` + `refs/` + `assets/` | ✅（`legacy/` 不改内容） |
| `metrics/` | `metrics.csv`（21 行）+ `logs/`（15 份原始日志）+ `scripts/latency_stats.py` + `evidence/` | ✅ 加日志/脚本；改口径要写清 |
| `skill/` | 三条已复用的能力封装（加分项） | ✅ 只登记跑通过的 |
| `report/` | 设计报告大纲（正文未写） | ✅ |
| `tools/` | `check_repo_layout.py`（门禁）+ 画图脚本 | ✅ |
| `.github/workflows/` | CI：命名/结构自检 + pytest + 指标复算 | ✅ |

**只读参考**：`docs/refs/`（D6 手册抽取、官方帧样本、板卡引脚表）、`docs/assets/`（截图）、`docs/legacy/`（历史方案，索引见 `docs/legacy/README.md`）。

---

## 3. 不变量（改之前先确认你改的是哪个方向）

| 项 | 值 | 定义位置 |
|---|---|---|
| 决策循环 | **10 Hz**（`CYCLE_MS = 100`） | `joint/board_main.py` |
| 视觉输入 | **TCP 5000**，行分隔 JSON，**≥ 7 Hz** | `joint/vision_server.py` + `contract.VISION_HZ_MIN` |
| 控制/状态 | **TCP 5001**，`pwm_l/pwm_r/in1..in4`，Status 回显 `seq`，>500 ms 无 Status → STOP | `car/comm/tcp_client.py` |
| 监控 | **TCP 5002**，只读，卡了只丢客户端 | `joint/board_main.py::MonitorBroadcaster` |
| 相机 | **UDP 1234**，`f0 5a a5 0f` + 640×480 RGB565 小端 | `fpga/src/vision/rtl/img_data_pkt.v` |
| 雷达 | **UART 230400 8N1**，85 B 帧，`AA 55` 起，双字节异或 CS | `car/radar/d6_protocol.py` |
| 决策阈值 | `danger 0.50 / warn 1.20 / passable 0.60 m`；`radar_timeout 200 ms`；`vision_timeout 300 ms`；确认帧数 3/2/2 | `car/protocol.py::CFG` |
| 差速映射 | `PWM_MAX 1000`、`TURN_GAIN 0.6`、`DEADBAND_PWM 20`；`steering 90`=直行 | `integration/contract.py` |
| AXI 寄存器 | `0x00 CTRL / 0x04 SPEED_L / 0x08 SPEED_R / 0x0C HEARTBEAT / 0x10 ENC_L / 0x14 ENC_R / 0x18 STATUS` | `fpga/src/motor/motor_axi.v` |
| 契约版本 | `CONTRACT_VERSION = 2` | `integration/contract.py` |
| 命名 | 目录/文件名只用 `a-z 0-9 . _ -`（`README.md` 等标准名例外） | `tools/check_repo_layout.py` |

**频率公式**（最容易踩）：`vision_timeout_ms >= 2 × 1000 / 视觉发送频率`。2 Hz 发送 + 300 ms 超时 = 70 % 周期在停车，车一顿一顿走。

---

## 4. 怎么跑

### 4.1 无硬件（任何机器，2 分钟）

```bash
python -m pip install -r host/deploy/requirements-host.txt
cd host/app && python -m pytest car -q            # 期望 32 passed
cd host/app/integration && python contract.py     # 契约自检（差速映射 + 归一化）
cd host/app && bash joint/_run_it.sh person_left  # 三进程：假电机 5001 + 板入口 + 假视觉 5000
```

### 4.2 有硬件

```bash
# 板端（雷达走 USB: /dev/ttyUSB0）
bash host/deploy/start_board.sh --motor <电机侧IP>:5001
# PC 端看云图（只读）
bash host/deploy/start_pc_view.sh <板子IP>:5002
```

L0–L3 分层排查见 `docs/hardware_setup.md` §8 与 `docs/legacy/on_board_test_manual.md`。

---

## 5. 验证（改完必跑这四条，全绿才算完）

```bash
python tools/check_repo_layout.py            # 期望：0 error 0 warning
cd host/app && python -m pytest car -q       # 期望：32 passed
cd host/app/integration && python contract.py
python metrics/scripts/latency_stats.py      # 期望：能打印 15 份日志的周期统计
```

CI（`.github/workflows/repo_check.yml`）在 push 后会跑其中三条，**绿了再合并**。

---

## 6. 真实数据 vs 未测（别混）

| 已实测（可复算） | 数值 | 证据 |
|---|---|---|
| 决策周期 P99 | 101.0 ms（97 周期，未命中 0/97） | `metrics/logs/board_run_20261024_202323.jsonl` |
| FPGA 资源/时序 | LUT 2.19 %、FF 1.07 %、BRAM 0.5 tile、DSP 0、WNS +1.096 ns | `fpga/report/*.rpt` |
| 雷达体检 | 169.5 帧/s、0 坏帧、有效点 92.9 %（手举 98.1 %） | `docs/legacy/radar_health_check_20261031.md` |
| D6 解帧 | 抓包 1107 帧×85 B，官方 CS 命中 1109/1110 | `host/app/d6_tools` |

**未测（`metrics.csv` 里是「待测」）**：端到端响应延迟、任务闭环成功率、mAP、推理延迟、通信 RTT、连续运行时长、Skill 部署时间。

⚠️ 现有决策日志产自**开发机 Windows + 假雷达**，不是板端 —— 报数时必须写这个测量条件。

---

## 7. 代码约定

- **板上是 Python 3.6.9**：禁 `dataclass`、海象运算符、`list[int]` 泛型、f-string `=` 调试语法。
- **RTL 注释是 GBK**（历史文件）。别用 UTF-8 整体重存，会让中文注释变乱码；新增文件写英文注释。
- **路径**：从 `__file__` 推导，禁止写 `D:\...`、`C:\Users\...`。
- **导入**：脚本按「自身目录 + 上一级 `car/`」找包（见 `host/app/README.md`），单个文件拷走会断。
- **日志字段**：`{"t","seq","rule","why","control","fault"}`，别改字段名（`latency_stats.py` 依赖）。

---

## 8. 已知坑（症状 → 原因 → 动作）

| 症状 | 原因 | 动作 |
|---|---|---|
| 接板时视觉收不到 | 端口 5000 有 **TCP**（`joint/vision_server.py`，当前）与 **UDP**（`integration/vision_receiver.py`，老方案）两套实现，同时只能起一个 | 只用 TCP 那套；UDP 那套当作历史 |
| 雷达插上完全没反应 | 板子 **P4 没跳 Host**（3-5、4-6），出厂空着时 HOST/SLAVE 都不通 | 先跳线再插 USB |
| 杜邦线接了读不到 `/dev/tty*` | 那是 **PL 引脚**，没有 overlay 就是死的 | 走 USB，或写 UART overlay |
| 车一顿一顿走 | 视觉发送 < 7 Hz，`vision_timeout=300` 一直判失效 | 提频到 ≥7 Hz，或把超时调到 1000 ms |
| 电机不动/只嗡嗡 | `pwm` 低于死区 20，或 `in1..in4` 极性与驱动相反 | 调 `DEADBAND_PWM` / 改方向定义（只改一处） |
| 拼多份日志算出几百万毫秒 | 不同 run 的 `t` 来自不同会话 | 按文件独立统计（`latency_stats.py` 就是这么做的） |
| 仿真/综合过不了 | 缺 2 个 IP 配置 | `.xci` 已在 `fpga/src/vision/ip/`，用 `build_project.tcl` 重建 |

---

## 9. 接下来该做什么（按优先级，带验收标准）

| 优先级 | 任务 | 验收标准 | 相关文件 |
|---|---|---|---|
| **P0** | 视觉接上 5000/TCP 且 ≥7 Hz | 板上 `drops=0`、`vision_age_ms<150`，连续 1 min 不停车抖动 | `host/app/vision_pc/`、`integration/vision_sender.py`（需从 UDP 改 TCP） |
| **P0** | 板端复测决策周期 | 用板上日志跑 `latency_stats.py`，P99/未命中写回 `metrics.csv` | `metrics/logs/`、`metrics/scripts/` |
| **P1** | AI PC 侧 ROCm ≥ 7.14 路径 + 版本声明 | `rocminfo` 输出 + 推理在同一模型上跑通，版本写进 `docs/hardware_setup.md` 与 `metrics.csv` | `host/model/MODEL.md` |
| **P1** | `motor_axi` 集成 + 仿真 + 示波器 | TB 覆盖「心跳超时→PWM 归零」；示波器图进 `metrics/evidence/` | `fpga/src/motor/`、`fpga/build/` |
| **P2** | 端到端延迟打点 | 逻辑分析仪两通道 → CSV → P50/P95/P99/max 进 `metrics.csv` | `metrics/evidence/README.md` |
| **P2** | 20 次场地试验 | `task_trials.csv` + 成功率 + 人工干预次数 | `metrics/scripts/`（待建 `task_trials.py`） |
| **P3** | 设计报告正文 | 按 `report/README.md` 八条写完，数字与 `metrics.csv` 一致 | `report/` |

---

## 10. 禁止事项

- ❌ 直接 push 到 `main`（走 `feat/*` 分支 + PR；`main` 永远可复现）
- ❌ 提交模型权重、bit/xsa、SD 镜像、雷达原始录包（>20 MB 会被 CI 拦）
- ❌ 把 token / 密码 / 个人路径写进代码或文档
- ❌ 为了让指标好看而改测量口径（要改就单独 PR 说明，并重算全部历史数据）
- ❌ 把「跑过一次 demo」写成「已验证稳定性」
- ❌ 公开转发本仓库内容（private，赛事提交前不要外泄）

---

## 11. 找东西

| 我想知道 | 去看 |
|---|---|
| 系统怎么划分、为什么 FPGA 不可替代 | `docs/architecture.md` |
| 报文长什么样、寄存器怎么排 | `docs/interface.md` |
| 怎么接线、跳哪个线、BOM | `docs/hardware_setup.md` |
| 上板分步操作与排障 | `docs/joint_debug_manual.md`、`docs/legacy/on_board_test_manual.md` |
| 雷达为什么这么解帧 | `host/app/car/radar/d6_protocol.py` 顶部 + `docs/refs/` |
| 指标怎么测、写到哪 | `metrics/README.md` |
| 从旧工作区怎么迁过来的 | `docs/migration_notes.md` |
| 提交清单自查 | `report/README.md` |
