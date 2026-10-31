# docs/legacy —— 历史资料索引（正文中文，文件名已改英文）

这些是项目推进过程中写的方案/交接/操作手册，**保留原文**（只改文件名以满足 3.2.5 命名要求）。它们记录的是「当时怎么想的」，规范定义以 `docs/architecture.md` / `docs/interface.md` / `docs/hardware_setup.md` 为准。

| 文件 | 原来叫什么 | 内容 | 还有效吗 |
|---|---|---|---|
| [`joint_debug_manual.md`](../joint_debug_manual.md) | 联调手册.md | 三端联调分步操作、9 条验收、故障排查表（**上板必读**） | ✅ 有效 |
| [`integration_overview.md`](integration_overview.md) | README_整合方案.md | 2026-10 整合方案：为什么有 `joint/` 这一层 | 部分（端口以 interface.md 为准） |
| [`retrofit_plan.md`](retrofit_plan.md) | 改造方案_三人.md | 三人代码如何拼到一起的改造记录 | 历史参考 |
| [`task_breakdown.md`](task_breakdown.md) | 三人分工_详细任务清单.md | 分工与任务拆解 | 历史参考 |
| [`on_board_test_manual.md`](on_board_test_manual.md) | 上板测试_现场操作手册.md | **L0–L3 分层测试**（不需要 bit 也能验）| ✅ 有效 |
| [`board_flash_checklist.md`](board_flash_checklist.md) | SD卡制作与首次启动_操作清单.md | 写卡、首次启动、串口/网口 | ✅ 有效 |
| [`flash_method_20261031.md`](flash_method_20261031.md) | 烧录方法_20261031.md | 2026-10-31 的烧录方法与坑 | ✅ 有效 |
| [`radar_health_check_20261031.md`](radar_health_check_20261031.md) | 雷达体检_20261031.md | 雷达帧率/坏帧/有效点/扇区实测（**指标证据**） | ✅ 有效 |
| [`single_nic_plan.md`](single_nic_plan.md) | 方案1_单网口_实施步骤.md | 单网口（PS 与 PL 共用）实施步骤 | 未采用 |
| [`image_selection.md`](image_selection.md) | 镜像选型对比.md | PYNQ 镜像选型比较 | 历史参考 |
| [`handoff_board_debug_20261031.md`](handoff_board_debug_20261031.md) | HANDOFF_郑_板上联调_20261031.md | 板上联调交接（现状/坐标/坑位） | ✅ 有效 |
| [`handoff_radar_20261020.md`](handoff_radar_20261020.md) | HANDOFF_郑_雷达选型与融合决策_20261020.md | 雷达选型与融合决策交接 | 历史参考 |
| [`exec_plan_20261020.md`](exec_plan_20261020.md) | 计划_郑_执行清单_20261020.md | 当时的执行清单 | 历史参考 |

> 规则：新写的规范化文档放 `docs/` 根下；写进历史区的文档不再改内容（只在需要时加一行「已被 XXX 取代」）。
