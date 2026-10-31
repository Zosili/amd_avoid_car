# report/ —— 设计报告

> 对应指南 3.2.5.4。**正文还没写**，本文件是提纲 + 素材索引 + 完成度自检。
> 写完放 `report/design_report.md`（或导出 `design_report.pdf`），文件名保持小写英文。

## 1. 章节与素材对照（3.2.5.4 八条，一条不漏）

| # | 3.2.5.4 要求 | 写到哪一章 | 已有素材 | 状态 |
|---|---|---|---|---|
| 1 | 选题背景与创新点：解决什么实际问题，前人做过什么 | §1 背景 | 具身智能赛道命题（指南 3.2.1）、避障小车的通用做法 vs 本队「雷达+视觉双确认 + 失效即停」 | 待写 |
| 2 | 系统架构与软硬件划分依据 | §2 架构 | [`docs/architecture.md`](../docs/architecture.md)（已有完整划分依据表） | **素材齐** |
| 3 | AI PC 端：模型选择理由、端侧收益、被放弃的候选方案及原因 | §3 端侧 AI | [`host/model/MODEL.md`](../host/model/MODEL.md)（yolo11n vs yolov8n、INT8 vs FP32、NPU vs CPU） | 待写：需补精度与延迟数字 |
| 4 | FPGA/Zynq 端：硬件功能、实现方法、为何不能由转接板/纯软件替代 | §4 FPGA | [`docs/architecture.md`](../docs/architecture.md) §7 + [`fpga/report`](../fpga/report)（资源/时序） | **素材齐** |
| 5 | 两端协同：通信协议、数据流、时间同步、异常处理 | §5 协同 | [`docs/interface.md`](../docs/interface.md)（协议+寄存器+时序）、架构 §6 异常表 | **素材齐** |
| 6 | 实测结果（须与 `metrics/` 汇总表一致） | §6 结果 | [`metrics/metrics.csv`](../metrics/metrics.csv) | 部分：12 项已实测，端到端/成功率待测 |
| 7 | 失败分析：哪些场景仍做不好，原因是什么 | §7 失败分析 | 已知：桌面环境假近距、弱回波点、0° 零点、2 Hz 视觉周期性停车、雷达到 12 m 后无回波 | 待写（材料够） |
| 8 | 复现说明：从零可执行的完整步骤 | §8 复现 | [`README.md`](../README.md) §4/§5、[`docs/hardware_setup.md`](../docs/hardware_setup.md) §8（L0–L3） | **素材齐** |

## 2. 评分权重与自评（3.2.5.6）

| 评测项 | 权重 | 现在的把握 | 靠什么拿分 |
|---|---|---|---|
| 具身智能系统完整度 | 20 | 中 | 闭环链路已通（假硬件）、失效即停设计；**缺真车场地数据** |
| AI PC 端侧 AI 能力 | 20 | 低 | 推理已在 NPU 跑通，但**缺 ROCm 栈**、缺 mAP/延迟数字 |
| FPGA/Zynq 设计质量 | 25 | 中高 | 视觉链路已上板 + 时序收敛 + 资源报告齐全；**电机 AXI 未集成、无仿真/示波器证据** |
| AI PC 与 FPGA/Zynq 协同 | 20 | 中 | 三端口协议 + 时间戳口径 + 断链降级写得清；**缺 RTT/吞吐实测** |
| 工程完成度与创新 | 15 | 中 | 文档与可复现性强（CI 查命名/结构/单测）、Skill 有真实复用记录 |

> 自评不是谦虚，是排优先级：**ROCm 路径 + 端到端延迟 + 真车 20 次试验** 这三件做完，权重 60 分的三项才站得住。

## 3. 写作规矩

1. **数字只能来自 `metrics/`**，正文与汇总表不一致 = 直接扣分（评审会对着看）。
2. 每个数字后面跟一句「测量条件」（硬件型号/软件版本/时钟/次数）。
3. 「为什么不用 XX」必须写具体理由（延迟/精度/工程量），不要写「因为不方便」。
4. 失败分析写真问题：哪些场景做不好、根因是什么、下一步怎么改。**这一节写得好比吹成功率高可信**。
5. 图必须可追溯：架构图/mermaid 源码入库，截图放 `docs/assets/`，报告里用相对路径引用。

## 4. 提交打包（3.2.5「所有材料一次性打包」）

| 项 | 位置 | 备注 |
|---|---|---|
| 工程包 | 本仓库（tag `submission-YYYYMMDD`） | 打 tag 便于复现「提交那一刻」的代码 |
| 指标报告 + 原始证据 | `metrics/` | 含 `metrics.csv`、`logs/`、`scripts/`、`evidence/` |
| Skill 与工作流 | `skill/README.md` | 加分项 |
| 设计报告 | `report/design_report.md` | 或 PDF 导出 |
| 演示视频 | 另交，不入库 | 体积大 |

打包前跑：

```bash
python tools/check_repo_layout.py
cd host/app && python -m pytest car -q
python metrics/scripts/latency_stats.py
git tag submission-$(date +%Y%m%d) && git push --tags
```
