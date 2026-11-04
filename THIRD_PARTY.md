# 第三方组件与许可说明

根目录 [`LICENSE`](LICENSE) 的 MIT 授权**只覆盖本项目自研部分**。下列第三方内容仍受其原有许可约束，MIT 不适用。

| 组件 | 位置 | 权利方 / 许可 | 说明 |
|---|---|---|---|
| 以太网 + OV5640 图传 RTL 例程（17 个 `.v`） | `fpga/src/rtl/arp/`、`udp/`、`ov5640/`、`gmii_to_rgmii/`、`eth_ctrl.v`、`eth_top.v`、`img_data_pkt.v`、`start_transfer_ctrl.v`、`ov5640_udp_pc.v` | 正点原子（ALIENTEK）；文件头标注 `Copyright(C) 正点原子 2023-2033` | 随开发板资料提供，本项目在其基础上接入自有顶层与决策模块；**未修改其版权头** |
| 检测模型权重 `best.onnx` | `host/model/best.onnx` | 由 **Ultralytics YOLO11n** 训练得到，受 **AGPL-3.0** 约束 | 模型为 YOLO11n 的微调产物；对外分发模型或其衍生作品时须遵守 AGPL-3.0。本项目仅用于竞赛演示与自测 |
| Vivado IP 配置 | `fpga/src/ip/clk_wiz_0.xci`、`fpga/src/ip/async_fifo_2048x8b.xci` | AMD / Xilinx 工具许可 | 仅含 IP 配置文件（参数），不含 IP 网表或加密源 |
| Python 依赖 | `host/deploy/requirements.txt`、`skill/requirements.txt` | 各自上游许可（`onnxruntime-directml`、`opencv-python`、`numpy`、`pyserial`、`pynq`） | 未在本仓库内置其源码，仅列依赖 |
| 开发板资料与手册 | `docs/refs/`（如 COIN-D6 手册抽取、引脚表） | 各原始厂商 | 仅作技术参考引用，不构成再分发授权 |

## 本项目自研部分（按 MIT 授权）

- `fpga/src/rtl/top.v`、`motor_decision.v`、`speed_pid.v`、`encoder_counter.v`
- `fpga/src/rtl/motor/`（`motor_axi_top`、`motor_back`、`motor_diff`、`motor_diff_ctrl`、`motor_left`、`motor_right`、`motor_seq` 及 4 个 testbench）
- `fpga/src/constraints/`、`fpga/src/tcl/`、`fpga/pynq/`、`fpga/report/`
- `host/app/`、`host/radar/`、`host/deploy/`
- `metrics/`、`skill/`、`training/`
- `docs/`、`report/`、根目录文档

## 备注

- 仓库当前为**私有**；MIT 的对外效力在仓库转为公开后才产生。
- 若要把本项目用于商业用途，请先自行核对上表第三方条款（尤其是 AGPL-3.0 的模型部分）。
- 本文件是事实说明，不构成法律意见；对外发布前建议自行复核第三方许可原文。
