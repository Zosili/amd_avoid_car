# host/deploy —— 部署与启动脚本

对应 3.2.5.1(3)/(4)「部署与启动脚本 + 依赖清单（环境可从零重建）」。
**这里只放启动与烧写相关的东西**，代码在 `host/app/`，模型说明在 `host/model/`。

## 目录

| 文件 | 用途 | 在哪跑 |
|---|---|---|
| `requirements-host.txt` | PC/开发机依赖（pyserial/numpy/matplotlib/Pillow） | PC |
| `requirements-board.txt` | 板端依赖（pynq/pyserial/numpy==1.19.5，Python 3.6） | 板子 |
| `start_board.sh` | 板端一键起决策（等价于 `python3 joint/board_main.py …`） | 板子 |
| `start_pc_view.sh` / `start_pc_view.bat` | PC 端起雷达云图 + 决策视图（连 5002） | PC |
| `write_sd.ps1` / `write_sd.bat` | 把 PYNQ 镜像裸写进 SD 卡（需管理员，带校验） | Windows PC |
| `fix_ftdi_driver.bat` | CH340/FTDI 驱动异常时的一键处理 | Windows PC |

SD 镜像与模型**不进仓库**（体积大）：镜像放本机任意位置，用 `write_sd.ps1 -Image <path>` 指定。

## 从零部署（三步）

```bash
# ① PC：装依赖
python -m pip install -r host/deploy/requirements-host.txt

# ② 板子：写卡 → 上电 → 装板端依赖 → 起决策
sudo pip3 install -r requirements-board.txt
bash host/deploy/start_board.sh --motor 192.168.1.50:5001

# ③ PC：连板子的监控端口看云图（只读，断连不影响车）
bash host/deploy/start_pc_view.sh 192.168.1.10:5002
```

## 板上启动参数（`joint/board_main.py`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--radar {live,fake}` | `live` | `fake` 时用 `--fake-front/left/right` 造数据，无硬件也能跑 |
| `--radar-port` | `/dev/ttyUSB0` | 雷达串口（PC 上是 `COM5`） |
| `--motor HOST:PORT` | 空（只写日志不外发） | 电机侧监听地址；留空 = 同进程模式 |
| `--vision-port` | 5000 | 视觉 TCP 监听端口 |
| `--monitor-port` | 5002 | 监控广播端口（0 = 关掉） |
| `--secs` | 0（一直跑） | 定时退出，便于脚本化测试 |
| `--log` | 自动 | 决策日志 JSONL（进 `metrics/logs/` 的原料） |

## 验证部署是否成功

```bash
python tools/check_repo_layout.py          # 仓库自检
cd host/app && python -m pytest car -q     # 32 passed
cd host/app && bash joint/_run_it.sh person_left   # 三端链路（不需要硬件）
```

出问题先看 [`docs/hardware_setup.md`](../../docs/hardware_setup.md) §8 的 L0–L3 分层排查表。
