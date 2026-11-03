# 更新日志

## 2026-11-03（晚）—— 全开环 + 原地转向 + 协议补 0xFFFE

### 功能变化

| 项 | 改前 | 改后 |
|---|---|---|
| 直行占空比 | 80% | **70%** |
| 转向 | 外侧 80% / 内侧 50% | **外侧 80% / 内侧反向 70%**（原地转） |
| 转向换向保护 | 无 | **300ms 最小保持**（防内轮高频正反转打坏 H 桥） |
| 雷达避障区间 | 15~50cm，±35° | **15~100cm，±70°** |
| 雷达急停 | <10cm，回波 >20cm 解除 | **<15cm，>25cm 解除** |
| 雷达急停鸣叫 | 连续长鸣 | **中速 3 声**（与“人贴脸”的长鸣区分开） |
| 有人但测不出距离 | 发 `0xFFFF` → PL 当无人放行 | **发 `0xFFFE` → PL 按 WARN 鸣叫、不停车** |

### 修复的问题

1. **“PC 显示 STOP、轮子却还在转”** —— `res.dist=None` 时被编码成 `0xFFFF`（=无人），PL 就放行。
   新增 `0xFFFE` 表示“有人但测不出距离”，PL 不再误放行。
2. **“人一消失车立刻动、没有 1s 暂停”** —— 同源：车从未真正进过 STOP，所以没有可释放的保持。
3. **“没检测到人却长鸣”** —— 其实是雷达急停（<5cm 锁存，要回波 >50cm 才解除）。
   现在阈值 15cm、解除 25cm，且改中速 3 声，不再和“人贴脸长鸣”混淆。
4. **转向方向左右颠倒** —— 通道与物理轮是交叉的，改为“左转降右通道、右转降左通道”。

### 架构变化：闭环 → 开环

右编码器实测失效（J4-25 / J4-4 恒 3.3V 不跳变，左轮正常 0↔3.3V），闭环拿不到反馈，
右轮一直在“助推→停→重试”里打转。为让车先能跑，改成**固定占空比开环**：

```verilog
parameter OPENLOOP_ALL = 1'b1;    // 1 = 全开环
parameter [7:0] DUTY_CRUISE   = 8'd70;
parameter [7:0] DUTY_TURN_OUT = 8'd80;
parameter [7:0] DUTY_TURN_IN  = 8'd70;   // 反向
parameter TURN_IN_REVERSE = 1'b1;
```

`speed_pid` / `encoder_counter` 代码保留，右编码器修好后把 `OPENLOOP_ALL` 改回 `0` 即可恢复闭环。

### 可移植性

- `run.bat` 改成**薄壳**，转调 `host/deploy/run_fusion.bat`，配置只存一处；
  去掉了写死的 `D:\miniconda\...` 解释器路径。
- `host/app/detect_fusion_live.py` 去掉了 `D:\FPGA\...best.onnx` 的开发机兜底路径；
  模型优先级改为 `--model` > 环境变量 `CART_MODEL` > 同目录 `models/best.onnx`。
- 全包扫描确认：无任何 `D:\` / `C:\Users` 绝对路径（`fpga/pynq/*.sh` 里的
  `/home/xilinx/radar` 是板端目标路径，属正常）。

### 位流

`fpga/build/top.bit` = `fpga/pynq/motor.bit`，4045679 B，
md5 `cf922bec1ea1f8a09ebabf4e1b434d3d`，2026-11-03 21:10 构建。
时序 WNS +1.071 ns / WHS +0.014 ns，0 违例（详见 `fpga/report/timing.md`）。

---

## 2026-11-03（夜）—— 按竞赛提交规范整改

依据《全国大学生嵌入式芯片与系统设计竞赛 2026》3.2.5 节逐条对齐。

### 文件名英文化（硬性要求）

仓库内不得出现中文/空格/特殊字符。已重命名：

| 原名 | 新名 |
|---|---|
| `启动融合检测.bat` | `run.bat` |
| `host/radar/启动.bat` | `host/radar/run.bat` |
| `host/radar/代码说明_D6雷达桥.md` | `host/radar/d6_bridge_notes.md` |
| `report/设计报告.md` | `report/design_report.md` |

全包已扫描确认无中文文件名；引用处（README / interface.md / 雷达文档）已同步更新。

### 指标表整改

`metrics/metrics.csv` 表头改为规范要求的列：

```
指标名称, 类别, 数值, 单位, 测量条件, 测试次数或时长, 证据文件
```

原表头（编号/口径/数据来源）不符合要求，已全部重写；「测量条件」列补全了
硬件型号、模型精度、输入规格、软件版本、FPGA 时钟频率。FPGA 相关指标同步更新为
最新一轮实现的数值（LUT 2355 / FF 2757 / WNS 1.071 / WHS 0.014）。

新增 `metrics/README.md`：表格列含义、脚本重跑方法、**未提供证据的如实说明**。

### 新增文档

| 文件 | 补的是哪条要求 |
|---|---|
| `docs/environment.md` | 3.2.5.2 运行环境说明（OS/驱动/依赖版本/板卡型号） |
| `fpga/report/verification.md` | 3.2.5.1(2) 仿真或板级验证结果 |
| `metrics/evidence/four_state_check.md` | 四档动作的原始验证记录 |
| `metrics/evidence/radar_behaviour_check.md` | 雷达急停/避障/掉线的原始记录 |
| `metrics/README.md` | 指标表说明 + 证据索引 |

### `fpga/build/` 补齐

要求「构建脚本 + .bit / .xsa / .hwh」同目录。原来 tcl 在 `src/tcl/`，已补：

- `build.bat` / `build.sh` —— 一键重建入口（转发到 `src/tcl/` 的两个 tcl）
- `motor.hwh` —— 从 `pynq/` 复制一份，与该目录的 `.bit` 配套
- 重写 `README.md`：产物 md5、部署方式、重建命令、QSPI 现状

### `docs/hardware_setup.md` 重写

补齐 3.2.5.1(4) 要求：

- **BOM 表**：主控/传感器/执行器/驱动板/供电，含型号与数量
- **双网口说明**：PL 口（图传+指令）与 PS 口（PYNQ SSH）的地址与用途
- **供电与共地**要求
- 修正过时描述：位流不再"每次上电都要 JTAG 烧"，改为 SD 卡 PYNQ + overlay 自启
- 新增「右编码器失效」「雷达离地太近」两个当前实际踩到的坑

### `skill/README.md` 补齐

3.2.5.3 要求每项说明「适用范围、使用方法、失效条件、已验证的复用结果」。
已在文首加**总览表**，9 条 skill 逐条覆盖这四项。

### README 补齐

- 目录树更新（去掉不存在的 `启动GPU检测.bat`，补 `CHANGELOG.md` / `docs/environment.md` /
  `fpga/report/verification.md` / `metrics/README.md`）
- 新增**「与推荐目录结构的对照」**表（3.2.5.5 要求：非推荐结构须给出对照说明）
- 文档导航改为表格，一行一个问题

---

## 2026-11-03 22:27 —— 移除换向保持，回退雷达左右取反

上一版（21:10）实车出现两个明显问题：**转向发木**（挡住障碍要等一会儿才转）、
**车一个劲右转，该左转时也右转**。定位后各自修复：

### 1. 删除 300 ms 换向最小保持（PL）

`motor_decision.v` 里原来的 `steer_held` 锁 + `STEER_HOLD_US` 计数器整段删除，
`eff_steer` 直接驱动占空比和 `dir` 位（占空比与方向仍用同一个信号，保证同步）。

- 代价：雷达 10 Hz / 视觉 20 Hz 读数抖动时，内侧轮换向会更频繁；
- 若实车听到连续"咔咔"声，应从源头加迟滞（雷达扇区/阈值），而不是再锁时间。

### 2. 雷达 `--flip-lr` 回退为 0（PS）

`--flip-lr` 是给**倒置安装**准备的左右取反，但雷达最终仍是正装，参数却开着 1，
导致左右判定整体反掉：障碍在左却命令右转，于是车一直往右偏。

- `start_radar.sh`：`--corridor 70 --flip-lr 1` → **`--corridor 60 --flip-lr 0`**
- `ps_radar_fusion.py`：`--flip-lr` **默认值 1 → 0**（不写该参数即为正装行为，避免再次默默反掉）
- 扇区同时从 ±70° 收回 ±60°：±70° 会把边缘杂波也纳入判据，误触发更多

> 排查要点：`--flip-lr` 与转向方向是一对一的关系，改它**不需要重编位流**，
> 改 `start_radar.sh` 后 `systemctl restart radar` 即可。

### 3. 数值更新

| 项 | 21:10 版 | 22:27 版 |
|---|---|---|
| 位流 md5 | `cf922bec…` | **`1fc79ab4…`** |
| Slice LUT | 2355 | **2332** |
| Slice Register | 2757 | **2736** |
| WNS | +1.071 ns | **+1.356 ns** |
| WHS | +0.014 ns | +0.010 ns |

同步更新：`fpga/report/timing.md`、`metrics/metrics.csv`（M19/M20/M24/M25）、
`metrics/evidence/radar_behaviour_check.md`、`metrics/evidence/four_state_check.md`、
`README.md`（决策表扇区、转向说明新增 `--flip-lr` 注意事项）。

板端备份：`/home/xilinx/radar/motor_prev.bit`、`ps_prev.py`、`sh_prev.sh`（21:10 版）。

---

## 2026-11-04 凌晨 —— 并入 `skill/` 可复用能力包

把独立的 `skill_2026-11-03.zip` 并入本仓库，并解决合并时的三处冲突。

### 1. 结构：三份文档分工

原来 `skill/` 只有一份 `README.md`（9 条踩坑经验）。新包自带一份工具手册，同名冲突，处理如下：

| 文件 | 来源 | 定位 |
|---|---|---|
| `skill/README.md` | **新包**（替换） | 五步定位阶梯 + 5 个工具的适用范围/用法/失效条件/复用结果 |
| `skill/overview.md` | 新包 `功能介绍.md` **改名** | 这套 skill 是什么、为什么算可复用（写报告可引用） |
| `skill/lessons.md` | **原 `README.md` 改名** | 9 条实战坑，内容未动，仅加了三份文档的分工说明 |
| `skill/*.py`（5 个） | 新包 | `hwh_probe` / `radar_probe` / `bench_infer` / `latency_stats` / `check_timing` |
| `skill/requirements.txt` | 新包（去 BOM） | 依赖清单 |

`README.md` 里新增第 10 节指向另两份；仓库根 `README.md` 的目录树与导航同步更新。

### 2. 让文档里的命令真能跑

新手册写的是 `python skill/check_timing.py --reports ./fpga/report`，但 `fpga/report/`
原本只有两份 `.md` 摘要，**没有脚本要读的 `.rpt`**，照抄必失败。已补入原始报告：

- `fpga/report/timing_summary_routed.rpt`（1.19 MB）
- `fpga/report/utilization_placed.rpt`（13 KB）

实测（22:27 位流）：`WNS +1.356 / WHS +0.010 / 违例 0`，
`LUT 2332 (4.38%) / FF 2736 (2.57%) / IOB 46 (36.80%)`，输出 `PASS`。

### 3. 数值与措辞校正

| 位置 | 原内容 | 改为 |
|---|---|---|
| `skill/README.md` §7 | WNS +1.071 / WHS +0.014 / LUT 4.43% / FF 2.59% | **+1.356 / +0.010 / 4.38% / 2.57%**（对齐 22:27 位流） |
| `skill/README.md` §1 | 提到 `robot/overlay/`（不属于本仓库的路径） | 改为"当时板子上跑的 bitstream 里没有 `axi_uartlite`" |
| `skill/overview.md` §8 | check_timing 示例输出为旧版（WNS +1.096 / LUT 1164） | 换成**真实跑出来的**当前输出 |
| `skill/overview.md` §9 | 文件清单缺 OVERVIEW/LESSONS，大小过时 | 补齐 9 项，注明三份文档分工 |
| `skill/README.md` §6 | 声称已验证 latency_stats，但包内无 `.jsonl` | 补一条**如实说明**：原始记录已删，数字来自当时实测 |
| `metrics/README.md` §4 | 缺证据只列 3 项 | 增补 `.jsonl` 缺失、`radar_probe` 未在干净环境重跑两条 |

### 4. 合规

- 中文文件名 `功能介绍.md` → **`overview.md`**（竞赛要求仓库内不得出现中文文件名）
- 5 个脚本 `py_compile` 全过；全包扫描**无硬编码绝对路径**（`hwh_probe`/`check_timing`/`latency_stats`
  只用标准库，可直接在别的机器跑）
- `requirements.txt` 去掉 UTF-8 BOM（老版本 pip 会读成乱码包名）

> 未改动：`report/design_report.md`（设计报告由队友负责）。

### 5. 实跑校验与两处修补（2026-11-04 01:10）

并入后逐个实跑，发现两个只有"真跑一次"才会暴露的问题：

**a) `bench_infer.py` 在非 CPU 后端直接崩溃**

```python
# 第 67 行（原）
print(f"...provider 顺序：{ort.get_session_options}")   # AttributeError
```

`ort` 没有 `get_session_options` 这个属性，只要 `--backend` 不是 cpu 就报
`AttributeError` 并退出。改为建完 session 后查 `sess.get_providers()`——
"列表里有这个 provider"和"实际用上了"本来就是两件事，后者才可信。

**b) `bench_infer` 文档里的数字与实跑差一个数量级**

| | 文档原值 | 2026-11-04 实测 |
|---|---|---|
| DirectML 纯前向 P50 | 16.5 ms | **3.6 ms**（P99 3.8） |
| CPU 纯前向 P50 | 约 165 ms | **47.0 ms** |
| 倍数 | 约 10× | **约 13×** |

差异来源是口径：文档那个 16.5 ms 其实是**检测主程序的端到端帧耗时**（含收图、
letterbox、后处理），不是纯前向。已在 `skill/README.md` §5 换成实测值，
并明确写"两个口径不能混用"——报告里混着写会被问穿。

### 6. 新增 `skill/selftest.py`

一条命令跑完所有**不需要硬件**的检查，退出码可直接进 CI：

```bash
python skill/selftest.py               # hwh 结构 + 时序/资源
python skill/selftest.py --with-infer  # 再加推理基准
```

实测两行 PASS。缺文件时打 `[SKIP]` 并算未通过，不会假装成功。
已写入 `skill/README.md` §11、`skill/overview.md` 文件清单、根 `README.md` 目录树。

### 7. 在板上实跑 `radar_probe`（2026-11-04 01:20）

补最后一条缺的实测证据，过程中撞到并修掉两个真问题。

**a) 板子上没有 `/dev/ttyUL0`**

```
ls: cannot access '/dev/ttyUL0': No such file or directory
```

PYNQ 默认**不加载 uartlite 内核驱动**，`ps_radar_fusion.py` 是直接 MMIO 读
`0x42C00000` 的。所以文档里写的"板载用 `--port /dev/ttyUL0`"在这块板上根本不存在。
给 `radar_probe.py` 加了 `--mmio` 后端（`SerialReader` / `MMIOReader` 两个类，
判据代码完全复用），顺带把 `--port` 从 required 改成二选一。

另有两个坑写进了脚本：`sudo` 下必须带 `XILINX_XRT=/usr`，否则 PYNQ 报
`No Devices Found`；MMIO 空读要 `sleep(0.2ms)`，不然空转烧 CPU。

**b) "帧长不唯一"是误判**

实测拿到 `{85:1775, 82:95, 13:119, 79:23}`，原判据"长度种类 >1 即失败"直接报 FAIL。
但 13/79/82 是 COIN-D6 的**其它包类型**（n=1/23/24），校验和都合法，PS 侧也是一律收。
判据已改为：**只判主导帧长是否等于标称 85 B**，其余包长列出来做提示。

**c) 结果（如实记录，未调阈值凑数）**

| 判据 | 实测 | 结论 |
|---|---|---|
| 帧率 | **167.7 帧/s**（标称 170 的 98.6%） | ✅ |
| 主导帧长 | 85 B | ✅ |
| 坏帧率 | 1.04% | ✗ 略超 1%（PS 侧自报 3.5%，探针反而更好） |
| 有效点率 | 43.5% | ✗ 雷达静置桌面、扫描面朝空旷处，大量光束无回波 |

原始输出存 `metrics/evidence/radar_probe_board.txt`，条件与解读写在
`metrics/evidence/radar_probe_board.md`；`metrics/metrics.csv` 新增三行板载实测。

> 没为了让结果好看去挪设备或放宽 `--min-valid-ratio`。会为结果让步的判据没有价值。

## 2026-11-04 01:50 —— 并入 `training/` 数据与训练管线

按要求把模型训练侧的代码一并纳入仓库（原来只有 `host/model/MODEL.md` 的文字说明，没有脚本）。

### 新增

```
training/
├── README.md          13 个阶段的管线说明 + 环境 + 复现步骤 + 未包含项
├── train_yolo.py      训练入口（原 D:/FPGA/ultralytics/train_yolo.py）
├── data_living.yaml   19 类配置（path 由绝对路径改为相对占位 ./living）
├── tools/             43 个脚本（采集/清洗/标注/审计/评测/后端选型）
└── reports/           living_detect.md、living_50ep_report.md、newmat_1030_report.md
```

共 505 KB，未包含数据集、`*.pt`、`runs/`、`vendor/`（107 MB 第三方运行时）。

### 澄清两处先前说错的话

| 之前说 | 实际 |
|---|---|
| `report/design_report.md` 是占位状态 | **不是**。34 个小节、12001 字，已写完 |
| 11 个训练脚本「写死了 `D:/` 路径」 | **不是**。`D:/` 只出现在文档字符串与用法示例里，赋值语句一处都没有；`sys.path` 全部用 `Path(__file__).parent`，本身是可移植的 |

唯一需要手工处理的是 `data_living.yaml` 的 `path:`（原来指向开发机数据集目录），已改为 `./living`
并在文件里注明。

### 同步更新

- 根 `README.md`：目录树新增 `training/`，对照表与文档导航各补一行
- 重新打包 `D:/car7020_code_2026-11-04.rar`

## 2026-11-04 01:54 —— 采到真实控制环数据，补齐最后一项证据

### 1. `detect_fusion_live.py` 新增 `--jsonl`

每处理一帧追加一行 `{"t": unix_ms, "frame", "fps", "infer_ms", "state", "steer", "person"}`，
直接对上 `skill/latency_stats.py` 的字段契约。文件以**行缓冲**打开，进程被杀也不丢。

### 2. 实采 78.1 s / 341 帧（桌面、无人、未开 `--send`）

| 指标 | P50 | P95 | P99 | max |
|---|---|---|---|---|
| 帧间隔 | **257 ms** | 309 | 316 | 417 |
| 推理耗时 | **16.5 ms** | 19.5 | 21.0 | 56.1 |

平均 **4.37 fps**。

### 3. 由此纠正一个容易写错的结论

```
推理 16.5 ms ÷ 帧间隔 257 ms ≈ 6%
```

**推理只占帧预算的 6%，94% 的时间在等图。**所以：

- "换小模型 / 开 FP16 / 上 NPU" 对**提帧率几乎无效**——省下的毫秒淹没在 240 ms 的等待里；
- 5 fps 的瓶颈在 **OV5640 采集 + PL 打包 + UDP 传输 + PC 收包解码**，不在 AI；
- 报告里若写成"推理慢导致帧率低"，数据是反的。

### 4. 另一处口径修正

`latency_stats` 的"未命中率"假设环有固定标称周期，只适用于**定时环**。
本项目的融合环是**帧驱动**的，用 `--period 200` 会得到 50.59% "未命中"——
按定义就会有一半样本超过 200 ms，不是故障。已在 `skill/README.md` §6 与
`metrics/evidence/latency_run.md` 写明：帧驱动的环应当用 `--period 0` 只看分位数。

### 5. 新增/更新文件

| 文件 | 内容 |
|---|---|
| `metrics/logs/fusion_run_20261104_0154.jsonl` | 341 帧原始记录（随包提交，可直接重跑） |
| `metrics/evidence/latency_run.txt` | `latency_stats` 原始输出 |
| `metrics/evidence/latency_run.md` | 测量条件、口径说明、瓶颈结论 |
| `metrics/metrics.csv` | 新增 5 行实时性指标（51 行，7 列无错列） |
| `skill/README.md` §6 | 换成实测值 + 帧驱动环的口径提醒 |
| `metrics/README.md` §4 | jsonl 一项从「无」改为「已补」 |

## 2026-11-04 02:30 —— 清掉第一版遗留，仓库只保留当前实现

第一版仓库（2026-10-31 快照）的内容已全部失效，按"全删覆盖"处理：

### 删除

| 路径 | 是什么 | 为什么删 |
|---|---|---|
| `AGENTS.md`、`CONTRIBUTING.md`、`.gitattributes` | 第一版的操作手册 | 通篇讲已删除的 `host/app/car/` 与 `motor_axi.v`，照着做会报错 |
| `tools/`、`metrics/scripts/` | 布局自检 + 决策周期统计 | 依赖第一版的 `car` 包与 R1–R5 规则，本版不存在 |
| `docs/assets/`、`docs/legacy/` | 板卡照片、交接文档 | 与当前 `docs/` 无引用关系，避免两套口径并存 |
| `host/app/{car,integration,joint,radar_console,d6_tools,vision_pc}` | 第一版并行实现（TCP） | 与当前 UDP 架构冲突 |
| `fpga/src/vision/`、`fpga/src/motor/motor_axi.v` | 旧 RTL 布局 | 已合并进 `fpga/src/rtl/` |

### 修复（删除后暴露的问题）

- `metrics/metrics.csv` 表头 `测试次数或时长` → `测试次数/时长`（规范原文写法）
- 6 个文件改小写：`lessons.md`、`overview.md`、`living_detect.md`、`living_50ep_report.md`、
  `newmat_1030_report.md`、`d6_bridge_notes.md`（规范要求纯小写）
- `BOOT.BIN` 保留大写：Xilinx 启动镜像固定名，SD 卡与 `host/app/integration` 都按这个名字检查
- `fpga/src/README.md` 按当前扁平结构重写（原先描述 `vision/` + `constrs/`）
- `host/app/detect_fusion_live.py` 的 jsonl 增加 `control.action / seq / rule` 字段
- CI 改为只跑两件不依赖第一版代码的事：全仓 `compileall` + `skill/selftest.py`

### 结果

仓库 202 个文件，结构 = 当前工作区，无重复实现、无死链。
