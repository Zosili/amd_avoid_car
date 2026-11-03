# 运行环境说明

本文件用于"从零重建环境"。所有版本号均为实测值，未实测的已明确标注。

## 1 硬件

| 项 | 型号 / 规格 |
|---|---|
| FPGA 器件 | Xilinx `xc7z020clg400-2`（Zynq-7000，ARM Cortex-A9 双核 + Artix-7 可编程逻辑） |
| 开发板 | 正点原子 领航者 ZYNQ7020 (V2) |
| 摄像头 | OV5640（RGB565，640×480） |
| 雷达 | COIN-D6 2D 激光雷达（LD06 类协议，UART 230400 8N1） |
| 电机驱动 | L298N 双 H 桥 |
| PC（推理） | AMD Ryzen 7 8845H + Radeon 780M（RDNA3，`gfx1103`），32 GB 内存 |

## 2 PC 端（AI 推理）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11（build 26200，25H2） |
| Python | 3.12.11 |
| 推理后端 | `onnxruntime-directml` 1.23.0（`DmlExecutionProvider`，`device_id=0` → Radeon 780M） |
| 数值精度 | FP32（未量化；INT8 量化版精度下降明显，已放弃） |
| GPU 驱动 | AMD Adrenalin 32.0.31021.5001（支持 DirectML / OpenCL / Vulkan） |
| CUDA 环境 | 另有 RTX 4060 + CUDA，与 DirectML 环境**完全隔离**，本包不依赖 |

### 2.1 依赖安装（可零重建）

```bash
cd host/deploy
python -m pip install -r requirements.txt
```

`requirements.txt` 内容：

```
onnxruntime-directml>=1.20
numpy>=1.26
opencv-python>=4.9
pyserial>=3.5
```

**注意**：必须是 `onnxruntime-directml`，普通 `onnxruntime` 只能跑 CPU（单帧慢一个量级）。
DirectML 不显式指定 `device_id` 时会选"高性能"显卡，双显卡机器上会跑到独显，
脚本里已硬编码 `device_id=0`。

### 2.2 启动

```bat
run.bat
rem 等价于 host\deploy\run_fusion.bat
```

可用环境变量覆盖：`CART_PY` / `CART_MODEL` / `BOARD_IP` / `CMD_PORT` / `CAM_HEIGHT` / `HFOV` / `CONF` / `LIDAR` / `LIDAR_PORT` / `THETA0` / `SIGN`。

## 3 板端（PYNQ）

| 项 | 值 |
|---|---|
| 系统 | PYNQ 2.7 镜像（Ubuntu 20.04 base），烧在 ≥8 GB TF 卡 |
| Python | PYNQ 自带 venv：`/usr/local/share/pynq-venv/bin/python3` |
| XRT | `XILINX_XRT=/usr`（`start_radar.sh` 里已 export） |
| 服务 | `radar.service`（systemd，`Restart=always`，开机自启） |
| 板端 IP | PS `192.168.2.99` / PL `192.168.1.10` |
| PL 位流 | 由 PYNQ `Overlay("motor.bit")` 加载，**不需要 JTAG** |

板端部署（把 `fpga/pynq/` 下 4 个文件传到 `/home/xilinx/radar/`）：

```bash
scp fpga/pynq/{motor.bit,motor.hwh,ps_radar_fusion.py,start_radar.sh,radar.service} \
    xilinx@192.168.2.99:/home/xilinx/radar/
ssh xilinx@192.168.2.99
sudo cp /home/xilinx/radar/radar.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable radar && sudo systemctl restart radar
tail -f /home/xilinx/radar/radar_run.log
```

> PYNQ 默认账号 `xilinx` / 密码 `xilinx`。板端 Python 次版本号可用 `python3 -V` 现场确认，
> 本包未强制要求特定次版本（只用标准库 + pynq）。

## 4 FPGA 开发环境（重建位流才需要）

| 项 | 值 |
|---|---|
| Vivado | 2025.2 |
| 器件族 | Zynq-7000 |
| 仿真 | Vivado Simulator 或 ModelSim SE-64 10.7 |
| 时钟 | `eth_rxc` 125 MHz（PHY 恢复时钟）/ `sys_clk` 50 MHz / `cam_pclk` ~24 MHz / `clk_200m` |

重建命令见 `fpga/build/README.md`。

## 5 网络规划

| 链路 | 地址 | 用途 |
|---|---|---|
| PL 网口 | 板 `192.168.1.10` ↔ PC `192.168.1.102/24` | OV5640 图传（UDP 1234）+ 决策帧（UDP 5000） |
| PS 网口 | 板 `192.168.2.99` ↔ PC `192.168.2.1/24` | PYNQ SSH、部署 overlay、看雷达日志 |
| 雷达 | 板内 PS → AXI GPIO → PL | **不走网络** |

两个网口**不要同时插混网段**用错：跑车接 PL，改配置接 PS。
