# tools/ —— 自检与辅助脚本

| 脚本 | 作用 | 用法 |
|---|---|---|
| `check_repo_layout.py` | 仓库自检：① 路径命名（小写英文）② 3.2.5.5 目录齐全 ③ `metrics.csv` 表头 7 列 ④ 单文件 ≤ 20 MB | `python tools/check_repo_layout.py` |
| `figures/draw_car_geometry.py` | 画车体几何与 12 扇区示意图（产出 `docs/assets/car_geometry_sectors.png`） | `python tools/figures/draw_car_geometry.py` |
| `figures/mount_compare_shape.py` | 画雷达安装方式对比（椭圆 vs 方形） | `python tools/figures/mount_compare_shape.py` |

## check_repo_layout.py 的判定口径

- **命名**：目录名/文件名只允许 `a-z 0-9 . _ -`；例外是 `README.md`、`MODEL.md`、`CONTRIBUTING.md` 等指南自身规定的标准名（保持大写）。
  - 为什么：指南 3.2.5 明确「不得出现中文、空格或特殊字符」，否则跨平台脚本会挂。
- **结构**：3.2.5.5 推荐项缺失只报 **warning**（指南允许自定义结构，但必须在 `README.md` 写目录对照）；命名/表头/体积问题报 **error** 并返回退出码 1。
- 通过标准：`0 个 error`。CI（`.github/workflows/repo_check.yml`）每次 push 都跑一次。

## 加新工具的规矩

1. 文件名小写英文，参数用 `argparse`。
2. 不写本机绝对路径（用 `os.path.dirname(os.path.abspath(__file__))` 推导）。
3. 能离线复现的优先（不许依赖「我机器的某个目录」）。
4. 加完更新本 README，并在 CI 里挂上（如果它是门禁）。
