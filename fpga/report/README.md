# fpga/report —— 综合实现报告（原始文件，别手改）

这些是 **Vivado 2025.2** 对 `xc7z020clg400-2` 的 `impl_1` 实现结果，直接从工程 `runs/impl_1/` 拷出（去掉了文件名前缀）。

| 文件 | 内容 | 指标值 |
|---|---|---|
| `utilization_placed.rpt` | 资源占用（Fully Placed） | LUT 1164/53200（2.19 %）、FF 1137/106400（1.07 %）、BRAM 0.5 tile（0.36 %）、DSP 0/220、BUFG 6/32、MMCM 1/4 |
| `timing_summary_routed.rpt` | 时序汇总（routed） | **WNS +1.096 ns、WHS +0.087 ns**，`All user specified timing constraints are met.` |
| `drc_routed.rpt` | DRC | 无阻断性错误 |
| `clock_utilization_routed.rpt` | 时钟资源 | `sys_clk` 50 MHz |
| `power_routed.rpt` | 功耗估算 | 见文件（依 Vivado 默认 toggle 估计，非实测） |

## 怎么复算

```bash
vivado -mode batch -source fpga/build/build_project.tcl -tclargs build_out
# 然后比对 build_out/reports/*.rpt 与这里
```

## 规矩

1. **改了 RTL 就得重跑并覆盖这里**——报告是快照，不更新等于对外说谎。
2. 报指标时写清：器件、Vivado 版本、时钟频率、Design State（placed/routed）。
3. 功耗报告是**估算**，不能当「实测功耗」写进 `metrics.csv`；实测要外接功率计并写清测量范围（整机/板卡）。
