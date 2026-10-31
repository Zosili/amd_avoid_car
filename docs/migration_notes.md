# 迁移说明：从 `D:\dasanshang\fpga竞赛` 到本仓库

> 本仓库首版是 **2026-10-31 对旧工作区的一次性快照导入**，之后以本仓库为准。
> 旧工作区（`D:\dasanshang\fpga竞赛`）保留为草稿区：里面有中文目录名、几十 GB 厂商资料、雷达原始录包，**不能当 git 仓库根**。

## 1. 为什么不能直接把旧目录变成仓库

| 问题 | 例子 | 处理 |
|---|---|---|
| 中文/空格目录名（3.2.5 禁止） | `正点原子领航者(V2)ZYNQ开发板资料盘(A盘)`、`joint/联调手册.md` | 只导源码，中文名文档改名进 `docs/` |
| 体积巨大 | `_sd_image/ZYNQ-7020-2.7.0.img` 8.1 GB、`国科光芯.zip` 46 MB、`国科光芯D6说明书.docx` 59 MB | 不进 git（镜像/模型/录包走本机或 LFS） |
| 工程中间产物 | `prj/*.runs`、`*.cache`、`.Xil` | `.gitignore` 排除，只留 RTL/约束/IP 配置与报告 |
| 本机绝对路径 | `D:\dasanshang\...\d6_capture.bin`、`D:\桌面\专业工具` | 改成相对路径（见 §3） |

## 2. 目录映射（旧 → 新）

| 旧位置 | 新位置 | 说明 |
|---|---|---|
| `zheng/`（包：protocol/fusion/radar/comm/vision/sim/tests） | `host/app/car/` | **包名 `zheng` → `car`**（避免用人名当包名，且全 ASCII 小写） |
| `joint/`（board_main 等） | `host/app/joint/` | 板上入口 |
| `integration/`（contract、run_on_board、radar_map_view…） | `host/app/integration/` | 集成层 |
| `radar_console/` | `host/app/radar_console/` | Tk 控制台 + 命令行助手 |
| `d6_test/`（协议逆向脚本、`d6_capture.bin`） | `host/app/d6_tools/` | 抓包（97 KB）一起入库，离线可复现 |
| `ouyang/42_ov5640_udp_pc/.../rtl` | `fpga/src/vision/rtl/` | 17 个 `.v`，SHA256 与原件一致 |
| 同上 `prj/.../constraints/ov5640_udp_pc.xdc` | `fpga/src/vision/constrs/` | |
| 同上 `prj/.../sources_1/ip/*.xci` | `fpga/src/vision/ip/` | 2 个 IP 配置（否则别人综合不出来） |
| 同上 `prj/.../runs/impl_1/*.rpt` | `fpga/report/` | 去掉文件名前缀 |
| `integration/motor_axi.v` | `fpga/src/motor/motor_axi.v` | RTL 放 FPGA 侧 |
| 同上 `ryzenai/*.py`、`view.py`、`yolo_test.py`… | `host/app/vision_pc/` | PC 侧视觉 |
| `zheng/logs/*.jsonl`、`joint/logs/_selftest.jsonl`、`integration/logs/board_*.jsonl` | `metrics/logs/` | 统一加前缀 `fusion_run_` / `joint_` / `board_run_` |
| `*.md`（中文名） | `docs/legacy/*.md`（英文名） | 正文不变，索引见 [`legacy/README.md`](legacy/README.md) |
| `hw_ref/_sch_p*.png`、`_board_*.png` | `docs/assets/` | 板卡/原理图截图 |
| `docs/d6_manual_en.txt`、`official_frames.txt`、`_io_pins.txt` | `docs/refs/` | 手册抽取与官方帧样本 |

## 3. 对代码做的改动（全部为可移植性，不改逻辑）

| 改动 | 影响文件 | 验证 |
|---|---|---|
| 包名 `zheng` → `car`（含 `sys.path` 拼接、变量 `ZHENG`→`CAR`、注释） | `car/**`、`joint/board_main.py`、`integration/*.py`、`radar_console/*.py` | `python -m pytest car -q` → **32 passed** |
| `D:\dasanshang\...` 绝对路径 → 相对脚本位置 | `d6_tools/*`（抓包路径）、`radar_map_view.py`、`radar_decision_view.py`、`radar_console.py`、`radar_helper.py`、`_smoke_*.py`、`_gen_bat.py` | 编译 + 实跑 `d6_frame_analyze.py`、`contract.py` |
| 启动脚本 `.sh` 去掉硬编码解释器与根目录 | `joint/_run_*.sh` | 逻辑未改，路径改为 `BASH_SOURCE` 推导 |
| `.bat` 重写为 ASCII + `%~dp0`；删除 `_gen_bat.py`（生成机器专用 bat 的脚本，已被可移植 bat 取代） | `radar_console*.bat`、`host/app/radar_console/` | `.bat` 本次未在 Windows 双击实测（待验证） |
| `radar_console.py` 生成快捷方式时用当前解释器推 `pythonw.exe`（原来是硬编码个人 Python312 路径） | `host/app/radar_console/radar_console.py` | `py_compile` 通过 |
| `write_sd.ps1` 镜像路径改为参数 `-Image` | `host/deploy/write_sd.ps1` | 语法未跑（需管理员+SD 卡） |
| 空目录补齐 `README.md`（每个目录都有说明） | `fpga/*`、`host/*`、`metrics/*`、`skill/`、`report/` | — |

**没改**：任何解帧公式、阈值、映射系数、端口号（`CONTRACT_VERSION` 仍是 2）。

## 4. 导入后仍然存在的差异（需要在旧工作区继续做的事）

1. 旧工作区里的 `__pycache__`、`.pytest_cache`、`_backup_*` 没进仓库（正常）。
2. `xu/project_1` 的 Vivado 工程与 `motor_pins.xdc` **没导入**（只有 IP import 的 `.dcp`，缺源码级约束）→ 见 `fpga/src/motor/README.md` 的「待补」。
3. 旧工作区的 `_official/ug585.pdf`、`_raw_compare.py` 等一次性诊断脚本没导入。
4. 模型权重（`*.pt/*.onnx`）不进仓库；用 `host/model/MODEL.md` 的命令重新导出。
5. 旧工作区的中文文档继续在 `docs/legacy/` 维护（改名但内容不动）；**新文档一律写在 `docs/` 根下**。

## 5. 之后的工作方式

```
D:\dasanshang\fpga竞赛\      草稿区：录包、厂商资料、临时脚本、试验代码（不进 git）
D:\dasanshang\fpga竞赛\amd_avoid_car\   本仓库：可提交、可复现、命名合规（进 git，两人共用）
```
结论：**要长期留的东西进仓库，一次性的东西留草稿区**。每次提交前跑 `python tools/check_repo_layout.py`。
