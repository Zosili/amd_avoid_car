# COIN-D6 雷达桥 · 代码说明（交给接手 Agent）

> 版本：2026-10-31 · 作者侧：郑（雷达/融合）
> 位置：`D:\dasanshang\fpga竞赛\zheng\radar\`
> 目标工程：`D:\dasanshang\fpga竞赛\car7020\car7020\`（**只读，任何情况下不要修改**）

---

## 0. 一句话

把 **国科光芯 COIN-D6** 的串口数据，按 `car7020` 融合管线**已经定好的接口契约**送进去：
**UDP 15001 送整圈扫描 JSON**（给 PC 上的 YOLO 融合），**可选 UDP 5000 送 4 字节距离帧**（给板子 PL）。
不修改队友任何文件，纯旁挂。

---

## 1. 为什么需要这个桥（必读背景）

队友的 `host/app/fusion/lidar_ld06.py` 假设 COIN-D6 走 **LD06 协议**：

```
0x54 | 0x2C | 转速(2B) | 起始角(2B) | 12×(距离2B+置信度1B) | 结束角(2B) | 时间戳(2B) | CRC8   ← 47 字节
```

但 D6 实测是：**85 字节 `AA 55` 帧 + 双字节异或校验**，包头都不同：

```
AA 55 | M&T | LSN | FSA(u16LE) | LSA(u16LE) | CS(u16) | 25×(Si_L, Si_2nd, Si_H)
  dist_mm = Si_H*64 + (Si_2nd>>2)      (14bit)
  Angle   = (raw>>1)/64                (0.9° 步进)
```

**结论：他们那条 `--lidar serial` 路径对 D6 永远锁不上包**（`SerialLidar.poll()` 一圈都凑不出来）。
他们的 `--lidar udp` 路径只认 JSON 扫描，与雷达协议无关 —— 所以本桥走 UDP，**不用改他们一行代码**。

D6 协议逆向的完整依据在 `zheng/radar/d6_protocol.py` 文件头（官方 PDF + STM32 例程 + ROS2 SDK 三源交叉验证，
实测 1110 帧零误帧）。

---

## 2. 文件清单

| 文件 | 作用 | 谁用 |
|---|---|---|
| `d6_stream.py` | **主程序**：串口 → 分箱 → UDP JSON（+ 可选距离帧） | 雷达那台 PC |
| `radar_view.py` | **云图**：车体坐标系实时点云 / 12 扇区 / 距离帧 / PL 状态；可无窗口存 PNG | 现场排障、报告配图 |
| `d6_config.json` | 配置默认值（端口/角度/掩膜/目标地址），**两个程序共用**；`port: "auto"` 自动找 CH340 | 同上 |
| `d6_protocol.py` | D6 帧解析 + 校验 + 扇区统计（**只读复用，别改**）；没装 pyserial 也能导入 | 被上面两个导入 |
| `README.md` | 3 分钟上手 + 接口表 + 可移植性说明 | 收到压缩包的人 |
| `requirements.txt` | `pyserial` / `numpy` / `matplotlib` | 装依赖 |
| `run.bat` | Windows 一键菜单（自动找 Python，找不到给提示） | 现场双击 |
| `run.sh` | Linux / macOS 同功能入口 | 同上 |
| `demo_cloud.png` | 云图样例（合成数据） | 报告/交接参考 |
| `d6_bridge_notes.md` | 本文 | 接手 Agent |

> `radar_provider.py` **不在分发包里**：它 `import protocol`（joint 工程的模块），只用于板上
> `board_main.py` 那一侧，拿出去缺依赖。分发时请按 §15 的清单打包。

依赖：`pyserial`、`numpy`、`matplotlib`（雷达那侧的 gui312 环境已有；**默认的 py3.13 环境没有 tkinter/Qt，跑不了窗口**，
无窗口存图 `--save` 在任何环境都行）。

---

## 3. 数据流

```
        ┌────────────────────── 雷达那台 PC ──────────────────────┐
D6 ──USB(CH340)──► d6_stream.py                                    │
230400 8N1          │  read_frame / parse_frame_ex（170 帧/s）      │
                    │  RevBuilder：按 D6 原生角切圈 + 0.5° 中位数   │
                    │                                              │
                    ├──UDP 15001 JSON──► detect_fusion_live.py --lidar udp
                    │   {"angles":[720], "ranges":[米/None]}        │
                    │                                              │
                    └──UDP 5000 4字节──► 板子 PL（可选，默认关）   │
                        AA 55 | dist_mm(大端)                     │
        └──────────────────────────────────────────────────────────┘
```

板子那侧（`car7020`）负责：收距离 → 阈值决策 → PWM/蜂鸣器。

---

## 4. 对外契约（**不要改**，改了要三方同步）

### 4.1 扫描（UDP 15001，10 Hz）

```json
{"angles":[0.0,0.5,1.0, ... 359.5], "ranges":[1.234, null, ...]}
```

- `angles`：720 个，**0~360° 单调递增**，步长 0.5°，0° = 车头，**逆时针为正**
- `ranges`：米（float），**无回波用 `null`**（对方 `Scan` 会转成 `nan`）
- 报文约 9.2 KB/帧
- 与 `fusion_core.Scan(angles, ranges)` 的字段完全一致

### 4.2 距离帧（UDP 5000，20 Hz，**默认关闭**）

| 偏移 | 长度 | 内容 |
|---|---|---|
| 0 | 2 | `AA 55` |
| 2 | 2 | 距离 mm，**大端**，`0xFFFF` = 无目标 |

语义：**前方 ±`dist_corridor`（默认 35°，对齐他们 `corridor_deg`）内最近障碍距离**。

> ⚠️ 队友 PL 的注释把这帧叫"最近**人**的距离"。雷达给的是"最近**障碍**"，
> 空房间里也会因为墙而停车。所以默认关；要开必须与队友确认语义。

---

## 5. 角度约定（最容易踩的坑）

统一公式（与队友 `fusion_core.cam_to_lidar()` 同式）：

```
theta = (theta0 + sign * d6_angle) % 360
```

| 情况 | 驱动侧 | 队友 `DecisionConfig` | 备注 |
|---|---|---|---|
| 正装 | `--sign -1`（默认） | `ang_sign=-1`（默认） | ✅ 实测 |
| 倒装（翻过来装） | `--mount inverted` → sign 变 +1 | `ang_sign=+1` | ✅ 实测，扇区左右对调 |
| 混搭 | 任意 | 与驱动不同 | ❌ 左右镜像，表现为"人在左、车往右躲" |

**规则：`sign` 与 `ang_sign` 必须成对；`theta0` 只在一边补，不要两边都加。**

优先级（实测过）：
1. `--sign` 显式给出 → 以它为准，**不再叠加**倒装取反
2. 否则 `--mount inverted` / `--invert` → 把 `d6_config.json` 里的 `sign` 取反
3. 否则用配置/默认值

---

## 6. 参数表（`d6_config.json` 或命令行）

| 参数 | 默认 | 含义 |
|---|---|---|
| `port` | `COM5` | 雷达串口（板上是 `/dev/ttyUSB0`） |
| `baud` | `230400` | D6 固定 8N1 |
| `udp` | `127.0.0.1:15001` | 扫描 JSON 目标（队友 `--lidar-port`） |
| `hz` | `10.0` | 扫描发送上限（受雷达 10.6 圈/s 限制） |
| `bin_deg` | `0.5` | 角度分箱（与队友 `SerialLidar.bin_deg` 一致） |
| `mount` | `normal` | `inverted` = 倒装 |
| `invert` | 关 | 等价于 `mount=inverted` |
| `sign` | `-1` | 角度方向（见 §5） |
| `theta0` | `0.0` | 装车偏航补偿 |
| `mask` | 空 | 屏蔽遮挡扇区，如 `137:223` 或 `137:223,300:330`（支持跨 0） |
| `dist_udp` | `null` | 距离帧目标，如 `192.168.1.10:5000`；留空=不发 |
| `dist_hz` | `20.0` | 距离帧频率 |
| `dist_corridor` | `35.0` | 距离用的前方走廊半宽（必须 = 队友 `corridor_deg`） |

命令行参数 **覆盖** 配置文件。`sign` 的优先级特别注意：
**命令行显式给 `--sign` 时以它为准，不再叠加倒装取反**（避免"改两处"导致左右镜像）。

实现细节：距离帧由一个**独立线程 `DistSender`** 按"下一个截止时刻"推进，稳定 20 Hz。
主循环受串口帧率牵制（170 帧/s、单帧 5.9 ms），在里面按 50 ms 判发实测只有 16 Hz 且抖动，
会让队友 PL 的 WARN/STOP 时间戳不准，所以拆出去了。

---

## 7. 怎么跑（现场）

```powershell
cd D:\dasanshang\fpga竞赛\zheng\radar

# ① 自检（不连任何东西，3 秒出帧率/圈数/12 扇区/距离帧预览）
python d6_stream.py --selftest --secs 3

# ② 正常运行（发扫描给队友融合）
python d6_stream.py

# ③ 同时给板子发距离帧
python d6_stream.py --dist-udp 192.168.1.10:5000

# ④ 倒装 + 屏蔽车体遮挡（本项目实际用法）
python d6_stream.py --mount inverted --mask 137:223
```

队友那边（**原样跑，不改**）：

```powershell
cd D:\dasanshang\fpga竞赛\car7020\car7020\host\app
python detect_fusion_live.py --lidar udp --lidar-port 15001 --cmd-udp 192.168.1.10:5000
```

> ③ 和他们的 `--cmd-udp` **只能二选一**，否则同一个 5000 端口有两个发送方，PL 会收到打架的距离。

---

## 7.5 云图 `radar_view.py`（与桥共用同一份配置）

和 `d6_stream.py` 读同一份 `d6_config.json`，所以**画出来的方向/掩膜/姿态与发给队友的数据完全一致**：
0° = 车头、逆时针为正、倒装与 `--mask` 同步生效。

```powershell
python radar_view.py                            # 实时窗口
python radar_view.py --demo                     # 没雷达也能看界面
python radar_view.py --save cloud.png --secs 4  # 无窗口出图（任何环境都能跑）
python radar_view.py --mount inverted --mask 137:223   # 覆盖配置
```

窗口里画了什么：

| 元素 | 含义 |
|---|---|
| 彩色点云 | 本圈扫描，亮度=距离（viridis，0~3 m） |
| 12 个扇环 + 标签 | s0 = 345°~15°，标签是该扇区中位距离；**斜纹灰扇区 = 被 `--mask` 屏蔽**（车体遮挡） |
| 两条虚线圈 | **1 m（STOP）/ 3 m（WARN）** —— 队友 PL 的阈值，一眼看出"现在会不会停" |
| 白色超椭圆 | 车体轮廓 35 × 18 cm（n=2.2），车头朝上 |
| 红十字 + 深色射线 | 前方 ±`dist_corridor` 走廊内的**最近点**，也就是发给 PL 的那个距离 |
| 右栏 | 帧数/坏帧/圈数/圈速、有效点、前方最近距离、**距离帧十六进制 + 对应 PL 状态**、12 扇区数值表 |

快捷键：`q` 退出 · `c` 清空累积 · `a` 累积/单圈 · `z` 视野 0.8/1.5/3.0/6.0 m · `s` 存图。

> 中文字形必须显式设字体（`plt.rcParams["font.sans-serif"]=["Microsoft YaHei"]`），
> 否则满屏 `Glyph missing`；`matplotlib.animation` 也必须显式 import —— 两条都是踩过的坑。

## 8. 标定（theta0 / sign）

### 方法 A：用队友的 `calib_yaw.py`（推荐，有相机时）

`host/app/fusion/calib_yaw.py` 会录若干帧 (bbox + 扫描)，网格搜索 `theta0` 与 `sign`，
直接打印要填的值。它原本读他们 `SerialLidar` 的采集文件；用本桥时按它的 `--capture` 格式录即可
（`angles`/`ranges` 与本桥 JSON 同构）。

### 方法 B：纸箱法（2 分钟，不需要相机）

1. 车头正前方 **1 m** 放纸箱；
2. `python d6_stream.py --selftest --secs 3 --mount inverted --mask 137:223`
3. 12 扇区里找到读数 ≈1.00 的那个 `s?`，其中心角 = `(345 + 30×?) % 360`
4. `theta0 = 纸箱真实方位 − 该中心角`（纸箱在正前方时 `theta0 = −中心角`）
5. 填进 `d6_config.json` 的 `theta0`，重跑确认 `s0` ≈ 1.00

---

## 9. 倒装 + 车底安装的三个物理后果

1. **角度镜像** → `--mount inverted`（= sign 取反）
2. **俯仰翻成 −2°（朝下）**：雷达离地 `h` 米时，光束约在 `28.6·h` 处打到地面
   （h=0.15 m → 4.3 m）。队友 `warn_m=3.0` 目前安全；调大阈值时要记得这条
3. **只看得到脚踝高度**：人的脚/小腿扫得到（其实最稳），但墙脚、椅子腿、地面杂物也会 → 现场优先查误报

车体遮挡：按车体几何算，后方遮挡区间 **137.5°~222.5°**，加上轮子/底盘裙边再多一些，用 `--mask 137:223` 屏蔽。

---

## 10. 实测基线（2026-10-31，雷达在 PC 的 COM5）

| 项目 | 数值 |
|---|---|
| 帧率 / 坏帧 | **170 帧/s，0 坏帧**（多次采样 500~850 帧全对） |
| 圈速 | **10.0 圈/s** |
| 有效点 | 85~93%（视环境） |
| 扫描 JSON | 720 点，0~359.5°，步长 0.50°，约 9.2 KB/帧，**9.2~9.3 Hz** |
| 距离帧 | `AA 55 01 35` = 309 mm，**稳定 20.0 Hz**（独立线程发，不随串口帧率抖动），**0 错帧** |
| 融合集成 | 三个人框场景 `source` 全 = **lidar**（非单目兜底） |
| 倒装验证 | 扇区表左右对调（正装 s3=3.00 ↔ 倒装 s9=3.03） |

---

## 11. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 自检 0 圈 | 串口没数据 / 波特率错 | `--selftest` 看帧数；D6 固定 230400 |
| 帧 OK 但坏帧多 | 线材/接触/供电 | 换 USB 口、短一点的线；D6 上电即吐流，不用发指令 |
| 扫描角度不单调 | sign/theta0 配错 | 见 §5，成对检查 |
| 队友那边 `lidar` 一直是 `--` | 我的桥没在跑 / 端口不对 | 先跑 `_udp15001_probe.py` 类工具确认 15001 有 JSON |
| 车左右躲反了 | 驱动 sign 与 `ang_sign` 混搭 | 二选一，见 §5 |
| 空房间里就停车 | 开了 `--dist-udp` 且语义当"人"用 | 关掉；或 `--mask` 掉墙的方向 |
| 串口被占用 | 队友的控制台/上位机还开着 | 关掉其它占用 COM5 的程序 |
| 进程一崩车就停 | 他们 PL 看门狗 400 ms → SAFE | 本桥已做自动重连，别手动杀掉 |

---

## 12. 给接手 Agent 的边界（重要）

1. **不要修改 `car7020/` 下任何文件**（包括他们的 xdc、RTL、Python）。要改必须经他们同意。
2. 本桥的契约（§4）已与他们的 `detect_fusion_live.py` / `motor_decision.v` 对齐，**改字段会同时破坏两边**。
3. `d6_protocol.py` 是协议真相源，改它要先跑它的自检（`--selftest` 必须 0 坏帧、170 帧/s 量级）。
4. 板子那侧的能力边界：`motor_decision.v` 里 `dir_l/dir_r` **写死前进**，没有转向 ——
   想要转向必须改他们的 RTL，不属于本桥。
5. 他们的板子是 **JTAG 烧位流、断电即丢**；图传要先发单字节 `b"1"` 到 UDP 1234 才启动。
   调本桥时**不需要**这些（雷达链路独立于图传）。

---

## 13. 变更记录

| 日期 | 改动 |
|---|---|
| 2026-10-31 | 首版：D6 → UDP 15001 扫描桥 + UDP 5000 距离帧（可选）+ 倒装/掩膜/自动重连/配置文 |

---

## 14. 相关文档

| 想知道什么 | 看哪 |
|---|---|
| 队友的接线、决策规则、看门狗 | `02_contest\fpga_car\docs\接队友管线_20261031.md` §9 |
| 倒装/车底的工程结论 | 同上 §6 |
| D6 协议怎么逆出来的 | `d6_protocol.py` 文件头 |
| 他们工程的入口 | `car7020\README.md`、`car7020\docs\interface.md` |

## 15. 可移植 / 分发（2026-10-31 修订）

**目标：整个文件夹压成 zip 发给别人，解压就能跑，不含任何绝对路径。**

### 15.1 已经做掉的

| 项 | 做法 |
|---|---|
| 路径 | 全部用 `os.path.dirname(__file__)` 定位；配置就在脚本旁边，**与启动目录无关**（实测在 `C:\Windows` 下运行也能读到旁边的 `d6_config.json`） |
| 串口 | `d6_config.json` 里 `"port": "auto"` → 自动找 CH340（`1A86:7523`）；找不到会明确报"没有找到任何串口"。也可写 `COM5` / `/dev/ttyUSB0` |
| 依赖 | 只有 `pyserial` + `numpy`（云图另需 `matplotlib`）；`d6_protocol.py` 的 `import serial` 改成软导入，**没装 pyserial 也能跑 `--demo` 和离线解析** |
| 无图形环境 | `radar_view.py --save x.png` 自动切 Agg 后端（不需要桌面/X11） |
| 控制台编码 | 启动时 `sys.stdout.reconfigure(encoding="utf-8")`，Windows GBK 控制台不再乱码 |
| Python 定位 | `run.bat` 依次尝试：环境变量 `PYTHON` → 同目录 `python.exe` → PATH 里 `python` → `py`；都没有就给出安装提示 |
| 跨平台 | 同一套 py 代码；Linux/macOS 用 `bash run.sh`，Windows 双击 `run.bat` |

### 15.2 打包清单（这些进 zip）

```
d6_stream.py   radar_view.py   d6_protocol.py   d6_config.json
requirements.txt   README.md   run.sh   run.bat
d6_bridge_notes.md   demo_cloud.png
```

**不要放**：`radar_provider.py`（缺 `protocol` 依赖）、`__pycache__`、任何真实环境的 `real_cloud.png`。

打包命令（本机已生成一份，见下）：

```powershell
$src='D:\dasanshang\fpga竞赛\zheng\radar'
Compress-Archive -Path $src -DestinationPath radar_portable.zip
```

### 15.3 对方拿到后三步

```bash
python -m pip install -r requirements.txt   # ①
python d6_stream.py --list-ports            # ② 确认雷达端口
python d6_stream.py --selftest --secs 3     # ③ 帧率≈170/s、圈≈10/s 就正常
```

> 若机器上 `python` 不在 PATH：装 Python 时勾选 "Add to PATH"，或把 `python.exe` 丢进该文件夹，
> 或设环境变量 `PYTHON` 指向解释器。

### 15.4 已验证

| 场景 | 结果 |
|---|---|
| 从 `C:\Windows` 运行拷到 `%TEMP%` 的副本 | 正常，配置从脚本目录读到 |
| 改副本里的 `d6_config.json`（port→COM9） | 立即生效 |
| 解压 zip 后 `--list-ports` / `--demo --save` | 正常出图（160 KB PNG） |
| 雷达未插 | 友好提示 + 退出码 2，不抛 traceback |
| 全目录绝对路径扫描 | 0 处 |

| 想知道什么 | 看哪 |
|---|---|
| 队友的接线、决策规则、看门狗 | `02_contest\fpga_car\docs\接队友管线_20261031.md` §9 |
| 倒装/车底的工程结论 | 同上 §6 |
| D6 协议怎么逆出来的 | `zheng/radar/d6_protocol.py` 文件头 |
| 他们工程的入口 | `car7020\car7020\README.md`、`docs\interface.md` |
| 我们之前的雷达体检数据 | `02_contest\fpga_car\docs\雷达体检_20261031.md` |
