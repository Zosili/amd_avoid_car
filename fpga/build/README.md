# fpga/build —— 可复现构建

## 一键构建（批处理）

```bash
# Windows（Vivado 2025.2，命令行里先 source 环境或用 Vivado Shell）
vivado -mode batch -source fpga/build/build_project.tcl -tclargs build_out

# Linux
vivado -mode batch -source fpga/build/build_project.tcl -tclargs build_out
```

产物在 `build_out/`：`ov5640_udp_pc.bit`、`utilization_placed.rpt`、`timing_summary_routed.rpt`、`drc_routed.rpt`。

**只用 RTL + 约束 + IP 配置（`fpga/src/vision/ip/*.xci`）重建**，不依赖原工程的 `.xpr` 与 `runs/`（那些是中间产物，已在 `.gitignore`）。

## ⚠️ 状态：脚本未经完整回归

- 脚本按原工程（Vivado 2025.2 / `xc7z020clg400-2`）的文件组织写成，**还没在新机器上从零跑通一遍**。
- 首次跑通后请：把 Vivado 版本与日志摘要写回本文件、把新报告覆盖到 `fpga/report/`、并 commit。
- 已知差异风险：不同 Vivado 版本对 `clk_wiz`/`fifo_generator` 的 `.xci` 需要 `upgrade_ip`；若报 IP 版本错误，在 tcl 里加 `upgrade_ip [get_ips]`。

## 手工步骤（等价，出问题时照这个走）

1. `create_project -part xc7z020clg400-2`
2. Add Sources：`fpga/src/vision/rtl/**/*.v`（17 个）、`fpga/src/vision/ip/*.xci`（2 个 IP）
3. Add Constraints：`fpga/src/vision/constrs/ov5640_udp_pc.xdc`
4. Set Top：`ov5640_udp_pc`
5. Generate Bitstream（默认流程即可）
6. 导出报告：`report_utilization` / `report_timing_summary` / `report_drc` → 放 `fpga/report/`

## 烧板

```bash
# JTAG（Vivado Hardware Manager）或 PYNQ 的 overlay 方式
# 视觉链路本身不依赖 PS 程序：上电后先出图，再起 board_main.py
```

## 与 PS 的接口

视觉链路**不通过 AXI**：PL 直接往网口发 UDP 到 PC。AXI 只用于 `motor_axi`（见 [`../src/motor/README.md`](../src/motor/README.md)），二者可以分别烧、分别验证。
