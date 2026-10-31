# host/app —— 上位机与板端应用源码

## 四块代码，各管一件事

| 目录 | 是什么 | 谁维护 | 关键入口 |
|---|---|---|---|
| `car/` | **决策核心包**（Python 包，含协议、融合状态机、雷达解析、TCP 客户端、仿真场景、单测） | 融合决策 | `car/run.py`、`car/fusion/machine.py`、`car/protocol.py` |
| `joint/` | **板端入口**：把雷达 + 视觉 TCP + 决策 + 控制 + 监控拼成一台能跑的车 | 融合决策 | `joint/board_main.py`（768 行，板上唯一入口） |
| `integration/` | **集成层**：三人接口契约、板端 motor 桥、雷达云图、预检、联调 demo | 全员 | `integration/contract.py`（唯一权威映射） |
| `radar_console/` | 图形控制台（Tk）+ 命令行雷达助手 | 融合决策 | `radar_console/radar_console.py`、`radar_helper.py probe` |
| `d6_tools/` | D6 协议逆向与验证工具（含 97 KB 真实抓包） | 融合决策 | `d6_tools/d6_frame_analyze.py` |
| `vision_pc/` | 视觉 PC 侧：OV5640 UDP 收图、YOLO、RyzenAI NPU | 视觉 | `vision_pc/ryzenai/main_ryzenai.py` |

## 导入关系（别乱动 sys.path）

```
board_main.py ──sys.path──► joint/ + car/          （拿 protocol / fusion / radar / comm / vision）
run_on_board.py ──────────► integration/ + car/
radar_map_view.py ────────► car/（只读，用来复算扇区）
```
脚本都按「自己所在目录 + 上一级的 `car/`」找包，所以**整个 `host/app/` 一起搬**才不会断；单独拷某个文件会退回相对路径失败。

## 跑之前

```bash
python -m pip install -r ../deploy/requirements-host.txt
python -m pytest car -q          # 32 passed
python joint/board_main.py --radar fake --motor 127.0.0.1:5001 --secs 5
```

## 命名与编码

- 板上是 **Python 3.6.9**：不用 dataclass、海象运算符、`list[int]` 泛型、f-string `=` 调试语法。
- 目录/文件名保持小写英文；中文只写在注释与文档里（`python tools/check_repo_layout.py` 会查）。
