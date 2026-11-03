# 指标汇总与证据索引

## 1 表格说明

`metrics.csv` 按比赛要求的表头组织：

| 列 | 含义 |
|---|---|
| 指标名称 | 指标的可读名称 |
| 类别 | 模型 / 视觉 / 决策 / 雷达 / 融合 / FPGA / 系统 / 通信 |
| 数值 | 实测值 |
| 单位 | 单位（无量纲填 `-`） |
| 测量条件 | 硬件型号、模型精度、输入规格、软件版本、FPGA 时钟频率与功耗模式 |
| 测试次数或时长 | 该数值是几次试验/多长时间得到的 |
| 证据文件 | 原始日志、脚本或截图路径（相对本包根目录） |

> 「证据文件」若是**脚本**，表示该指标可现场重跑得到；若是**日志/图片**，表示已有留存的原始记录。

## 2 目录

```
metrics/
├── metrics.csv                指标汇总表
├── logs/                      原始日志（训练曲线、联调日志、实现日志）
│   ├── train_40ep_results.csv
│   ├── fusion_live_tune.log
│   └── impl_runme.log
├── scripts/                   可重跑的测试脚本
│   ├── test_cmd_frame.py      四档动作（CLEAR/WARN/STOP/断流）
│   ├── probe_link.py          图传链路探针（发 '1' 点火、数收包）
│   ├── probe_frame_size.py    帧长/像素格式校验（必须 614408）
│   ├── match_check.py         真实雷达扫描 + 人框配对自检
│   └── fake_radar_sender.py   假雷达：向 15001 发合成扫描
└── evidence/                  证据材料
    ├── ila_udp_frame.csv      ILA 抓的 UDP 帧解析波形
    ├── frame_decoded.png      抓到的帧解码出的图像
    ├── radar_scan_sample.json 一帧真实扫描（720 点）
    ├── radar_cloud_demo.png   合成扫描点云
    ├── radar_cloud_real.png   真实扫描点云
    ├── four_state_check.md    四档动作板级验证记录
    └── radar_behaviour_check.md 雷达急停/避障/掉线验证记录
```

## 3 常用脚本怎么跑

```bash
# 四档动作（轮子务必架空）
python metrics/scripts/test_cmd_frame.py --ip 192.168.1.10 --port 5000

# 图传链路是否通
python metrics/scripts/probe_link.py --ip 192.168.1.10

# 帧长/像素格式
python metrics/scripts/probe_frame_size.py --ip 192.168.1.10

# 雷达与人框配对自检（需要先起 host/radar 桥）
python metrics/scripts/match_check.py
```

所有脚本只依赖 `host/deploy/requirements.txt` 里列的包，不写死任何绝对路径。

## 4 未提供的证据（如实说明）

| 项 | 状态 | 原因 |
|---|---|---|
| 示波器/逻辑分析仪波形截图 | **无** | 手头没有示波器；跨时钟域与时序正确性改用 ILA 抓取 + Vivado 时序报告代替 |
| 端到端延迟的仪器化测量 | **无** | 现为"帧间隔 + 推理耗时 + 发送周期"分段相加的估算，未做单次触发测量 |
| 长时间（≥1 h）连续运行日志 | **无** | 未做耐久测试 |
| `skill/latency_stats.py` 的 `.jsonl` 原始记录 | **已补** | 2026-11-04 01:54 采 341 帧真机数据（`logs/fusion_run_20261104_0154.jsonl`，分析见 `evidence/latency_run.md`） |
| `skill/radar_probe.py` 的完整跑通记录 | **已补** | 2026-11-04 在板上用 MMIO 后端实测 12 s（`evidence/radar_probe_board.md`）；PC 侧 CH340 那组仍为早期记录 |

以上是当前证据链的短板，后续有条件会补。**没有的数据一律标"无"，不估算、不编造。**
