# COIN-D6 雷达工具（可移植版）

把 **国科光芯 COIN-D6** 激光雷达接进 `car7020` 融合管线的最小工具集。
**整包拷走即可运行，不含任何绝对路径。**

```
.
├── d6_stream.py            主程序：串口 → 0.5° 分箱 → UDP 扫描 JSON（+ 可选 4 字节距离帧）
├── radar_view.py           云图：车体坐标系实时点云 / 12 扇区 / 距离帧 / PL 状态（可无窗口出图）
├── d6_protocol.py          D6 协议解析（85 字节 AA55 帧 + 双字节异或校验）
├── d6_config.json          配置（port 默认 auto，自动找 CH340）
├── requirements.txt        依赖
├── run.bat                Windows 一键菜单
├── run.sh                  Linux / macOS 入口
└── d6_bridge_notes.md      ★ 交接手册（给接手的人/agent）
```

## 3 分钟上手

```bash
python -m pip install -r requirements.txt

python d6_stream.py --list-ports          # ① 看雷达在哪个口
python d6_stream.py --selftest --secs 3   # ② 自检（帧率/圈数/12 扇区）
python d6_stream.py                       # ③ 发扫描给融合（UDP 127.0.0.1:15001）
python radar_view.py                      # ④ 云图窗口
```

没有雷达想先看界面：

```bash
python radar_view.py --demo
python radar_view.py --demo --save demo.png
```

## 和 car7020 的接口（不要改）

| 方向 | 端口 | 内容 |
|---|---|---|
| 我 → PC 融合 | UDP **15001** | `{"angles":[720], "ranges":[米 或 null]}`，10 Hz，0.5° 一格 |
| 我 → 板子 PL（可选） | UDP **5000** | `AA 55` + 距离 mm（大端），20 Hz，`0xFFFF`=无目标 |

队友那边原样跑：`python detect_fusion_live.py --lidar udp --lidar-port 15001`

## 角度约定

`theta = theta0 + sign × d6_angle`（与 `fusion_core.cam_to_lidar()` 同式）

- 正装：`--sign -1`（默认）＋ 对方 `ang_sign=-1`
- **倒装**（翻过来装）：`--mount inverted`，桥**内部**自动把 sign 取反，输出仍是「0°=车头、逆时针为正」，所以对方 `ang_sign` **仍是 -1，不要改 +1**
- **两边只能改一处**，否则左右镜像

> 注意：`--mount inverted` 的取反发生在桥这一侧（`d6_stream.py` 里 `a.sign = -a.sign`），
> 转换后的角度已经是正向坐标，融合侧看不到"倒装"这回事。判断依据是自检那一行
> 「角度按 sign=+1 theta0=0.0 转换」——桥已经把倒装吃掉了。因此本工程 `run_fusion.bat` 里
> `SIGN=-1` 恒成立，只有纸箱法实测发现方向确实镜像时才改成 +1。

## 可移植性说明

| 项 | 处理 |
|---|---|
| 串口 | `port: "auto"` 自动识别 CH340（`1A86:7523`）；也可写 `COM5` / `/dev/ttyUSB0` |
| 路径 | 全部相对本文件目录（`os.path.dirname(__file__)`），配置就在旁边 |
| 依赖 | 只有 `pyserial` + `numpy`（云图另需 `matplotlib`）；没装 pyserial 也能跑 `--demo`/离线解析 |
| 编码 | 启动时把 stdout 切到 UTF-8，Windows GBK 控制台不再乱码 |
| 无图形环境 | `radar_view.py --save x.png` 自动用 Agg 后端，不需要 X11/桌面 |
| Python | 3.7+；Windows / Linux / macOS 同一套代码 |

## 详细文档

`d6_bridge_notes.md` —— 协议来源、接口契约、参数表、标定流程、故障排查、倒装与车底安装的工程结论、
以及给接手 agent 的边界约束。
