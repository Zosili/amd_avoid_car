# AGENT 交接文档：郑的模块 · 板上联调

> **用途**：把 2026-10-25 ~ 10-26 完成的「板上入口 + 三端联调脚手架 + 雷达云图/控制台」打包给下一个接手的 AI Agent。
> **生成时间**：2026-10-31
> **适用项目**：2026 全国大学生嵌入式芯片与系统设计竞赛 · FPGA 创新设计赛道 · AMD 方向
> **本文档地位**：`HANDOFF_郑_雷达选型与融合决策_20261020.md`（下称"前一份"）的续篇。前一份讲**选型与接口设计**，本文讲**已经落地的代码怎么跑、坑在哪、下一步做什么**。冲突时以本文为准。
> **操作手册**：`joint/联调手册.md`（给人看的分步操作 + 验收清单 + 故障表）。本文是给 Agent 看的**全貌与决策依据**。

---

## 0. 阅读顺序（给接手 Agent）

1. §1–§2：现在到哪一步了、机器和板子在哪
2. §4–§5：数据流与三份协议（**改动前必须理解，这是三方接口**）
3. §6：三种场景怎么跑（**接手后第一件事就是跑场景 A**）
4. §8–§9：已实测基线 + 🔴 坑位（**改代码前必读，踩过的坑不要重踩**）
5. §10：待办与阻塞（**其中 3 项要用户/队友拍板，不要自己决定**）

---

## 1. 一句话状态

**软件侧全部打通，可以无硬件全链路验证；硬件侧只差"把代码拷到板子上、接上真雷达"这一步。**

| 模块 | 状态 | 位置 |
|---|---|---|
| 雷达协议逆向 + 解析 | ✅ 完成，实测 169 帧/s 零坏帧 | `zheng/radar/d6_protocol.py` |
| 融合决策状态机 R0~R5 | ✅ 完成 | `zheng/fusion/machine.py` |
| 板上入口（串起雷达/视觉/决策/控制/监控） | ✅ 完成并自检 | `joint/board_main.py`（768 行） |
| 视觉 TCP 接收 | ✅ 完成 | `joint/vision_server.py`（168 行） |
| 假电机 / 假视觉（无硬件联调） | ✅ 完成 | `joint/mock_motor.py`、`mock_vision.py` |
| 差速映射（steering → pwm_l/pwm_r） | ✅ 接入 | `integration/contract.py`（唯一真源） |
| PC 端雷达云图 | ✅ 完成，支持 `--tcp` 远程数据源 | `integration/radar_map_view.py`（941 行） |
| 图形控制台（一键起停） | ✅ 完成 | `radar_console/radar_console.py`（613 行） |
| **真雷达 + 真板子 + 真队友** | ❌ **未验证** | 等硬件与队友 |

**三人分工不变**：欧阳 = 视觉（YOLO → Vision JSON）／郑 = 雷达 + 融合 + 决策 + 通信／徐 = PYNQ 底层 + 电机驱动。

---

## 2. 硬件与环境坐标

### 2.1 硬件
- **板子**：正点原子**领航者 V2**（xc7z020clg400-2）。⚠️ **不是 PYNQ-Z2 / 启明星**，网上 PYNQ-Z2 的教程不能照抄。
- **SD 卡**：已写官方 **PYNQ 2.7.0**（7020 版）。底子是 **Ubuntu 18.04 + Python 3.6.9** → 板上代码必须 3.6 兼容（已扫描：无 dataclass / 海象运算符 / PEP585 泛型 / f-string 调试语法）。
- **雷达**：国科光芯 **D6**，USB 转串口 **CH340**，Windows 下是 **COM5**，板上应为 `/dev/ttyUSB0`。
- **车体**：35 × 18 cm，超椭圆变体（`|x/b|^n + |y/a|^n = 1`，n≈2.2）。雷达**前伸约 3cm**（待实测确认）。

### 2.2 本机开发环境（Windows）
| 用途 | 路径 |
|---|---|
| **GUI/绘图专用解释器** | `C:\Users\ASUS\.workbuddy-ai\binaries\python\envs\gui312\Scripts\python.exe` |
| 默认解释器（**不能跑 GUI**） | `...\envs\default\Scripts\python.exe`（py3.13，**无 tkinter/Qt**） |
| 项目根 | `D:\dasanshang\fpga竞赛\` |
| 云图与控制台 | `integration\radar_map_view.py`、`radar_console\` |
| 记忆与坑位库 | `D:\agent-workdata\workbuddyai2\.workbuddy-ai\memory\`（`MEMORY.md` / `PITFALLS.md`） |

🔴 **所有跑 GUI 的命令必须用 `gui312`**，理由见 §9.1。

---

## 3. 文件清单

### 3.1 本次新增（`joint/`）
| 文件 | 行数 | 作用 | 谁用 |
|---|---|---|---|
| `board_main.py` | 768 | **板上唯一入口**。雷达 → 视觉 TCP → 决策 10Hz → 控制 TCP → 监控广播 | 板上跑 |
| `vision_server.py` | 168 | 视觉 TCP 接收，按行切分喂给 `VisionProvider.push()` | 被 board_main import |
| `mock_motor.py` | 198 | 扮演徐：收 Control、回 Status（seq 原样回显）、可 `--drop-after` 模拟断链 | 郑本地联调 |
| `mock_vision.py` | 106 | 扮演欧阳：6 个场景 + `flicker`（断帧）+ `bad`（畸形帧） | 郑本地联调 |
| `联调手册.md` | — | 分步操作 + 9 条验收 + 故障排查表 | 三人共用 |
| `_run_it.sh` / `_run_net.sh` / `_run_monitor.sh` / `_run_live.sh` | — | 一键跑四种场景 | 郑 |

### 3.2 本次修改
| 文件 | 改了什么 |
|---|---|
| `integration/radar_map_view.py` | 新增 `--tcp HOST:PORT` 数据源（PC 显示板上数据）；右栏优先显示**板上真实的 R1~R5 与动作**；12 扇区直接用板上算好的值 |
| `radar_console/radar_console.py` | 新增「上车联调」区：一键起 假电机 + 板入口（假雷达）+ 假视觉，日志合并显示 |

### 3.3 未改动（只读分析）
`zheng/` 全目录**一行未改**。`run.py` 的 live 模式仍在，但**不再推荐在板上用**（理由见 §7.1）。

---

## 4. 数据流与端口

```
        ┌─────────────── PYNQ 板子（192.168.x.x，待定）───────────────┐
        │                                                            │
欧阳 ──TCP:5000──► vision_server ──► VisionProvider ─┐               │
（笔记本）         （按行 JSON）                        │               │
                                                       ▼               │
雷达 ──USB:230400──► LiveRadarProvider ──► DecisionMachine（10Hz）     │
（D6）                                    │  R0~R5                   │
                                          ▼                          │
                                    MotorLink ──TCP:5001──► 徐（电机驱动）
                                    （断线每 1s 重连）      ◄─Status──┘
                                          │
                                          └──► MonitorBroadcaster ──TCP:5002──► PC 云图
                                               （12 扇区 + 3 数 + 点云 + 规则 + 动作）
```

| 端口 | 方向 | 谁监听 | 内容 |
|---|---|---|---|
| **5000** | 欧阳 → 板 | 板 | Vision JSON，每行一个 |
| **5001** | 板 ↔ 徐 | **徐** | 板发 Control，徐回 Status（`seq` 原样带回） |
| **5002** | 板 → PC | 板 | 只读监控广播，给云图 |

**启动顺序不能乱**：徐（5001 先起来）→ 板子（`board_main`）→ 欧阳（5000 推第一帧）→ 云图（5002）。
理由：`machine.py` R5 第一条就是 `vision is None → STOP`，**欧阳不推帧车永远不会动**；徐没起来板子会一直报链路故障。

---

## 5. 三份协议（已冻结，改动必须三方同意）

### 5.1 Vision（欧阳 → 板，5000）
```json
{"version":1,"timestamp":1750000000000,"obstacle":true,
 "nearest":{"class":"person","confidence":0.82,
            "cx":0.30,"cy":0.55,"width":0.18,"height":0.42}}
```
- `cx/cy/width/height` 是**画面归一化 0~1**，不是像素
- `obstacle:false` 时 `nearest` 可省；**没目标也要发**（不发 = 链路断 = 停车）
- 板子不信任你发的 `timestamp`，用**到达时间**自己打戳 → 两边时钟不同步不会误判
- 🔴 **频率必须 ≥7Hz**（`contract.VISION_HZ_MIN`）。2Hz 发送会让 300ms 超时下 **70% 的决策周期在停车**，车一顿一顿像程序抽风。公式 `timeout >= 2×(1000/Hz)`。**禁止「2Hz 推理 + 10Hz 重发旧结果」凑频率**。

### 5.2 Control（板 → 徐，5001）
```json
{"version":1,"seq":123,"timestamp":1750000000000,
 "action":"FORWARD","speed":60,"steering":90,
 "pwm_l":350,"pwm_r":350,"in1":1,"in2":0,"in3":1,"in4":0}
```
- `steering` 0~180，**90 = 直行**；`action` ∈ FORWARD/SLOW/LEFT/RIGHT/STOP
- 🔴 **徐是差速车不是舵机车**。`--diff 1`（默认开）会把 `pwm_l/pwm_r/in1..in4` 一起算好塞进去，
  徐**直接写寄存器**，不用再理解"90 是中位"。映射规则只在 `integration/contract.py::control_to_diff()` **一份**。
- 实测映射：`RIGHT(45)` → L=350 R=140 ✓（左轮快=右转）；`LEFT(135)` → L=140 R=350 ✓

### 5.3 Status（徐 → 板，5001 同一连接）
```json
{"version":1,"seq":123,"timestamp":1750000000000,"ok":1,"vbat":12.1,"enc_l":0,"enc_r":0}
```
- 郑只用两件事：**有没有收到**（watchdog，500ms）+ **seq 对不对**（丢包排查）
- `ok/vbat/enc_l/enc_r` 现在只记录，不接编码器也能跑，但**字段要在**
- 🔴 超 500ms 不回 → 强制 STOP。这是安全设计不是 bug

---

## 6. 怎么跑（三种场景）

### 场景 A：一个人，没板子没雷达（**接手后先跑这个**）
```bash
cd "D:/dasanshang/fpga竞赛"
bash joint/_run_it.sh person_left
```
它会起三个进程：假电机(5001) + 板入口(假雷达 front=1.5m) + 假视觉(左侧有人)。
**预期输出**：`R5(还没视觉)` → `R4 RIGHT(视觉左侧有人)` → 视觉停后回 `R5 vision stale`。
上次实测：**约 51 条 RIGHT + 40 条 STOP**（数量随视觉停发时机抖动，看动作序列对不对，别对数），日志落在 `joint/logs/_it/`。

可用场景（`mock_vision.py`）：`clear` / `person_center` / `person_left` / `person_right` / `far_small` / `low_conf` / `flicker` / `bad`。

验证 watchdog：`MOTOR_ARGS="--drop-after 20" bash joint/_run_it.sh person_center` → 20 条后电机不回 Status，板子应转 STOP。

### 场景 B：有板子，队友还没来
```bash
# 板上
cd ~/car && python3 joint/board_main.py --radar-port /dev/ttyUSB0 --motor 192.168.x.x:5001
# 笔记本：假欧阳
python joint/mock_vision.py --host <板子IP> --scenario person_left
# 笔记本：云图看板上真实数据
<gui312 python> integration/radar_map_view.py --tcp <板子IP>:5002
```

### 场景 C：三个人齐（现场）
按 §4 的顺序起：徐 → 板子 → 欧阳 → 云图。

### 场景 D：只有雷达、没有板子（研究雷达本身）
```bash
# 图形控制台（推荐，有 UI 和提示）
D:\dasanshang\fpga竞赛\radar_console\radar_console.bat
# 或直接开云图
<gui312 python> integration/radar_map_view.py --port COM5
```
控制台里：选串口 → 「体检 3 秒」→「打开实时云图」；还有「本机全套自检」一键跑场景 A。

### 板上自检（不需要任何硬件）
```bash
python3 joint/board_main.py --selftest      # 合成雷达点 + 假视觉，验 12 扇区与规则
```

---

## 7. 关键设计决策（为什么这么做）

### 7.1 为什么不直接在板上跑 `zheng/run.py` 的 live 模式
`run.py` 有两个在 Linux/PYNQ 上不成立的前提：
1. **Vision 从 stdin 进**（`--vision-stdin`）。搬到板上后没人再往 stdin 打字。
2. **`TcpClient._rx_loop` 遇异常直接 `return`，永不恢复**。徐重启一次服务，郑这边就**永远保持 STOP 且不报错** —— 最难排查的一类故障。

`board_main.py` 用 `MotorLink` 外壳补掉第 2 点（每 1s 重建连接），用 `vision_server` 补掉第 1 点，**下游决策代码零改动**（仍是 `DecisionMachine.step()`）。

### 7.2 为什么「车上产数据、PC 显示」而不是把云图搬上板
- PYNQ 板上无显示器；
- ARM 上 matplotlib 渲染 **1~2 秒/帧**，400 点/圈 10Hz 根本跟不上；
- 雷达插在板上时 PC 读不到那个串口（`/dev/ttyUSB0` 在板子的 USB 上）。

所以：**板上算 12 扇区 + 3 个数 + 规则 + 动作，TCP 发出去（约 2KB/s）**；PC 端只画。
已交叉验证：PC 用收到的点重算 12 扇区，与板上 **12/12 完全一致**。

### 7.3 为什么 `contract.py` 只留一份
`steering → 差速` 的映射规则如果在 `joint/` 再存一个副本，两份必然漂移，现场会出现"板子算出左转、轮子往右跑"。
`board_main._load_contract()` 会依次找 `joint/contract.py` → `../integration/contract.py`；**板上只需把 `integration/contract.py` 拷成 `~/car/joint/contract.py`**。

### 7.4 为什么不做 SLAM
D6 没有里程计、400 点/10Hz 特征稀疏、任务本身无全局导航需求。沿墙走用"最近墙线拟合"就够。
**不要因为"看起来高级"去加 SLAM，会把联调时间全吃掉。**

### 7.5 关于 12 扇区
12 是 3 的**严格超集**（`front = min(s11, s0, s1)`），每扇区约 33 点 > `SECTOR_ROBUST_MIN_PTS=4`，统计上站得住。
新能力：障碍方位、最宽空隙、被包围检测。成本：车上 JSON 变大 + 规则要改。
**目前 12 扇区只用于监控与研究，决策仍走 3 扇区**——先用实车数据看 12 扇区值不值得进决策。

---

## 8. 已实测基线（回归时拿这个对数）

### 8.1 雷达健康（COM5 实测）
帧 OK **542** / 坏 **0**，**169.4 帧/s**（≈10.6 圈/s），有效点 **395/420 = 94%**。

### 8.2 12 扇区（桌面环境，雷达贴着支架）
| 扇区 | s0 | s1 | s2 | s3 | s4~s8 | s9 | s10 | s11 |
|---|---|---|---|---|---|---|---|---|
| 距离 m | 1.19 | 1.10 | 1.40 | 1.34 | **0.07~0.09** | 0.34 | 0.32 | 0.80 |

s4~s8（105°~255°）连续 150° 近距 = 支架/桌面紧贴，**属正常**（这段上车后会被车体挡住）。
三扇区：前 **1.19**(33点) / 左 **1.13**(67点) / 右 **0.32**(62点)。
⚠️ 右侧 0.32m 会被旧阈值 `passable 0.60` 判成"右侧堵死"——上车后必须先重新标定阈值。

### 8.3 车体几何（超椭圆 n=2.2，前伸 3cm）
- 前方**净空 275°**、遮挡区间 **137.5°~222.5°**、扫掠半径 **20.5cm**、三扇区与遮挡**重叠 0°**
- ⚠️ 对照：方形车体 f=3 只有 **143°**；**椭圆反而前伸越多越开阔**（f=10 → 312°），和方形的直觉相反
- 🔴 **低装在几何中心 = 净空 0°**（雷达在壳内，全遮挡）。"中部最完美"只在抬到车体上沿之上时成立
- 高度封顶 = **车体上沿 + 2~3cm**（再高会漏低矮障碍，低矮的交给欧阳视觉）

---

## 9. 🔴 坑位（踩过的，别重踩）

### 9.1 GUI 解释器（最容易浪费时间的一个）
`envs\default` 是 py3.13，**没有任何 GUI 工具包**（tkinter/Qt/wx 全无）→ matplotlib 静默降级到 Agg →
`plt.show()` 立即返回、进程秒退、**返回码 0**。
⭐ **"退出码 0" ≠ "窗口起来了"。** 必须用 `envs\gui312`。
`radar_map_view.main()` 已加后端自检：非 GUI 后端直接打印原因并返回 2。

### 9.2 其他
- **`matplotlib.animation` 必须显式 import**，`import matplotlib` / `pyplot` 都不会隐式导入 → 交互模式必崩
- **Consolas 没有中文字形** → 状态面板别设 `family="Consolas"`，否则几十条 UserWarning
- **`print` 到重定向文件是块缓冲**，进程被 kill 就丢日志 → `sys.stdout.reconfigure(line_buffering=True)`
  （3.6 无此 API，用 try/except AttributeError）；CSV writer 也要逐行 flush
- **扇区边界效应**：小障碍骑在扇区边界会"报宽"（实测 0.24m 圆柱跨 15° 边界 → s0/s1 双双 0.40m）。
  这不是 bug，是稳健最小值的固有行为，**调规则时别当成噪声**
- **`_robust_min` 取第 2 小**（k=2），单点噪声打不穿，但真障碍占 2 个点以上才生效
- **`savefig(dpi=130)` 比图形 dpi=100 大 1.3 倍** → 按显示坐标裁剪 PNG 会裁到空白，要乘 1.3
- **cscript/vbs 建 `.lnk` 被安全策略拦**（Known Windows LOLBin）→ 只能在 GUI 内用按钮建
- **两套 mock 别用混**：`joint/mock_motor.py`(5001，配 `board_main`) vs `integration/mock_board.py`(8000，配 `run.py`)
- **ModelSim/中文路径**与本项目无关，但同一台机器上 `D:\dasanshang\EDA\` 有同类坑

### 9.3 D6 协议要点（改解析器前必看）
帧 85B = `AA 55 | M&T | LSN | FSA(u16LE) | LSA(u16LE) | CS(u16) | 25×(Si_L, Si_2nd, Si_H)`
- `dist = Si_H*64 + (Si_2nd>>2)`（14bit，mm）
- `Angle = (raw>>1)/64`，步进 0.9°，400 点/圈
- 230400 8N1；**上电即吐流**；**DTR 拉高会复位**
- 坐标系：左手、原点=旋转中心、**角度顺时针增大**、**0° = 外壳"单个安装耳"方向**（不是线束方向）
- ⚠️ `ZERO_OFFSET_DEG` **待装车标定**，现在 0° 是否对正前方**未确认**

---

## 10. 待办与阻塞

### 10.1 🔴 需要用户/队友拍板（**不要自己决定**）
1. **板子 IP 和网段**（文档里全是 `192.168.x.x` 占位）—— 三人必须在同一网段
2. **徐的电机驱动是否跑在同一块 PYNQ 上**。如果是（`mock_board.py` 注释写"把打印换成 `mmio.write` 就是最终实现"，看起来是），
   那**根本不需要 TCP**：同进程 `contract.control_to_diff()` 后直接写 AXI 寄存器，`--motor` 留空即可。
   用不用 TCP 取决于两人要不要独立调试。
3. **徐是否同意 Status 最小集** `seq/ok/vbat/enc_l/enc_r`
4. **`steering` 左右映射**：现为 135=左、45=右、90=直行（`protocol.CFG`）。徐那边舵机方向若相反，只改这三个数
5. **车体上沿（含云台）高度**、**雷达实际前伸 cm**、**0° 是否对正前方**

### 10.2 技术待办
- [ ] **上车实测并重新标定阈值**：桌面环境右侧 0.32m，旧 `passable 0.60` 会误判"右侧堵死"。三扇区阈值必须在真车上重测
- [ ] **`ZERO_OFFSET_DEG` 标定**：把 0° 对准车头正前方
- [ ] **板子上装 pyserial**（`pip3 install pyserial`，没网就拷 `.whl` 用 `--no-index`）
- [ ] **串口权限**：`sudo usermod -aG dialout $USER` 后**必须重新登录**才生效
- [ ] 确认欧阳的 YOLO 真能到 **≥7Hz**（这是硬指标，不是"尽量"）
- [ ] 12 扇区是否进决策（现在只做监控）
- [ ] 雷达安装高度最终定（上沿 +2~3cm 封顶）

### 10.3 板上要拷的东西（最小集）
```
~/car/
├── zheng/             ← 整个目录（protocol.py / fusion / radar / comm / vision）
├── joint/
│   ├── board_main.py
│   ├── vision_server.py
│   └── contract.py    ← 从 integration/contract.py 拷过来（board_main 会自动找）
```
`radar_map_view.py` **不用拷**（那是 PC 端的）。

---

## 11. 明确不做的事（别被带偏）
- ❌ **SLAM / 建图**：见 §7.4
- ❌ **把 matplotlib 搬到板上**：见 §7.2
- ❌ **原始 400 点全量上传**（约 40KB/s）：除非要离线分析，否则只用 12 扇区（~2KB/s）
- ❌ **为了"更安全"把阈值调大**：会让车在开阔地也不敢走，现场表现为"车不动"，比撞更难看
- ❌ **改 `zheng/` 里的决策规则而不重跑场景 A**：场景 A 是唯一能立刻验证的回归手段

---

## 12. 接手第一步（照做）

```bash
# 1) 确认环境
C:/Users/ASUS/.workbuddy-ai/binaries/python/envs/gui312/Scripts/python.exe -c "import matplotlib,serial,numpy;print('ok')"

# 2) 跑板上自检（无硬件）
cd "D:/dasanshang/fpga竞赛"
<gui312 python> joint/board_main.py --selftest

# 3) 跑三端联调（无硬件）
bash joint/_run_it.sh person_left

# 4) 有雷达时验雷达本身
<gui312 python> integration/radar_map_view.py --port COM5
```

四条全过 → 说明代码没被改坏，可以直接往硬件推进。
任何一条不过，先查 §9，再看 `PITFALLS.md` 的 **H 章节**（雷达/车体几何，约 20 条）。

---

## 附：文档地图

| 想知道什么 | 看哪 |
|---|---|
| 选型为什么选 D6、接口最初怎么设计 | `HANDOFF_郑_雷达选型与融合决策_20261020.md` |
| 分步操作、验收清单、故障表 | `joint/联调手册.md` |
| 每个坑的完整复现与解法 | `PITFALLS.md` H 章节 |
| 逐日决策过程 | `2026-10-25.md` / `2026-10-26.md`（在记忆目录） |
| 项目级索引（跨项目） | `MEMORY.md` |
