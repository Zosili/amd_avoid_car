# metrics/scripts —— 可重跑的测试脚本

| 脚本 | 作用 | 输入 | 复现命令 |
|---|---|---|---|
| `latency_stats.py` | 决策控制周期 P50/P95/P99/最大、抖动、截止期未命中率、动作/规则分布 | `metrics/logs/*.jsonl` | `python metrics/scripts/latency_stats.py --csv metrics/decision_period.csv` |

约定：

- 脚本只用标准库 + numpy 可选；**不要**依赖本机绝对路径，默认路径从脚本自身位置推。
- 每个脚本必须能在「干净环境」重跑（3.2.5.2 要求：脚本可在提交的环境中重跑）——跑之前 `pip install -r host/deploy/requirements-host.txt`。
- 新增脚本后同步：本表 + `metrics/README.md` §3 + `metrics.csv` 的「证据文件」列。

## 待建

| 脚本 | 要产出什么 |
|---|---|
| `bench_infer.py` | AI 单样本推理延迟（预处理/推理/后处理/总）、持续 FPS；`batch=1` |
| `task_trials.py` | 20 次场地试验记录 → 闭环成功率、人工干预次数、`task_trials.csv` |
| `rtt_probe.py` | 5001 端口 Control/Status RTT（P50/P95/P99）+ 丢包率 |
| `e2e_latency.py` | 读逻辑分析仪导出的两通道 CSV → 端到端延迟分布 |
