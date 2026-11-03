# 综合与实现报告摘要

> 原始报告：Vivado 工程 `impl_1` 下的
> `top_utilization_placed.rpt`、`top_timing_summary_routed.rpt`、`runme.log`
> （日志副本见 `metrics/logs/impl_runme.log`）。
> 对应 `metrics/metrics.csv` 的 M19~M25。

## 1 器件与工具

| 项 | 值 |
|---|---|
| 器件 | `xc7z020clg400-2` |
| 工具 | Vivado 2025.2 |
| 位流 | `fpga/build/top.bit`（4045679 B，md5 `1fc79ab458bab284e746f6e62b96fa6b`，2026-11-03 22:27 构建） |

## 2 资源占用

| 资源 | 用量 | 可用 | 占比 |
|---|---|---|---|
| Slice LUT | 2332 | 53200 | 4.38 % |
| Slice Register | 2736 | 106400 | 2.57 % |
| Block RAM Tile | 0.5 | 140 | 0.36 % |
| DSP | 0 | 220 | 0.00 % |
| Bonded IOB | 46 | 125 | 36.80 % |

**解读**：决策逻辑几乎不占资源（DSP 为零、BRAM 半个 tile），主要开销在以太网（CRC、FIFO）和 IO 上。这意味着后续接雷达、加第二路输入时，逻辑资源不是瓶颈。

> 2026-11-03 注：LUT/FF 比 11-01 那版（1702 / 1943）多了约 40%，是加了雷达急停 3 声计数、
> 开环占空比选择、内轮反转方向控制这几块逻辑；DSP 仍为 0，因为全开环后 PID 输出
> 不再驱动占空比，那条乘法链被综合优化掉了。
> 22:27 这版删掉了 300ms 换向保持（`steer_held` + 32 位计时器），比 21:10 那版少 23 个 LUT、21 个 FF。

## 3 时序

```
WNS = +1.356 ns    TNS = 0.000 ns    失败端点 0
WHS = +0.010 ns    THS = 0.000 ns    失败端点 0
WPWS = +0.264 ns   TPWS = 0.000 ns   失败端点 0
"All user specified timing constraints are met."
```

保持裕量从 11-01 那版的 57 ps 掉到 **10 ps**，仍为正但已经非常薄。WHS 与 IO / 时钟树有关；
若后续再加 `eth_rxc` 域的寄存器，务必重新检查（必要时在 IO 上加 `set_output_delay`，
或把跨域前的组合逻辑再拆一级）。

## 4 时钟域与 CDC 约束

| 时钟 | 频率 | 来源 |
|---|---|---|
| `sys_clk` | 50 MHz | PL 晶振 U18 |
| `eth_rxc` | 125 MHz | PHY 恢复时钟 |
| `cam_pclk` | ~24 MHz | OV5640 |
| `clk_200m` | 200 MHz | `clk_wiz_0` |

关键约束（`fpga/src/constraints/7020_top.xdc`）：

```tcl
# 决策模块在 eth_rxc 域，输出经两级同步器跨到 sys_clk 域
set_false_path -from [get_clocks eth_rxc] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_rxc]
```

> 早期这两行写的是 `clk_fpga_0`（PS 的 FCLK）。改成 `sys_clk` 不只是约束调整，而是执行链路脱离 PS 依赖的一部分——否则只烧 PL 位流时 FCLK 不跑，电机与蜂鸣器整条链路的寄存器不翻转。

## 5 重建方法

```bash
vivado -mode batch -source fpga/src/tcl/01_project.tcl   # 重建工程
vivado -mode batch -source fpga/src/tcl/02_build.tcl     # 综合 + 实现 + 出位流
```

两个脚本都用 `[info script]` 推算根目录，**不含任何绝对路径**，整个目录搬到别的机器也能直接跑。

工程建在 `fpga/src/prj/`，位流产物为 `fpga/src/prj/7020.runs/impl_1/top.bit`；
需要交付时手动覆盖到 `fpga/build/top.bit`（交付的这份是现场验证过的版本）。

`prj/` 目录没有放进交付包：里面的 `.xpr` / `.dcp` 写死了开发机的绝对路径，换机器必报错，而用上面两个脚本三分钟就能重建。
