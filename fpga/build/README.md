# 构建产物与重建方法

## 1 产物清单

| 文件 | 大小 | md5 | 说明 |
|---|---|---|---|
| `top.bit` | 4,045,679 B | `cf922bec1ea1f8a09ebabf4e1b434d3d` | **当前验证版**位流（2026-11-03 21:10） |
| `motor.hwh` | 181,861 B | — | 与 `top.bit` 配套的硬件描述，PYNQ `Overlay()` 加载 overlay 时必需 |
| `7020.xsa` | 326,781 B | — | 硬件平台导出，用于重建 FSBL / Vitis 工程（当前架构不依赖） |
| `motor_app.elf` | 352,728 B | — | 旧方案 PS 侧程序。当前 PS 跑 PYNQ Python，此文件仅为兼容保留 |


> 旧方案的 `BOOT.BIN`（FSBL + 位流 + PS 程序的裸机启动镜像）**已从仓库移除**：
> 当前架构走 SD 卡 + PYNQ overlay，不需要它；若日后修好 QSPI 固化，按 §4 重新生成
> （`bootgen` 用法见本文后半）。移除原因是文件名含大写，违反指南 3.2.5 的命名规则。
| `build.bat` / `build.sh` | — | — | 一键重建脚本（Windows / Linux-macOS），见 §3 |

## 2 怎么部署位流（当前方式：SD 卡 + PYNQ overlay）

1. 把 `top.bit` 改名为 `motor.bit`，连同 `motor.hwh` 放到板子 `/home/xilinx/radar/`；
2. `sudo systemctl restart radar` —— `systemd` 会自动 `Overlay("motor.bit")` 加载并开始读雷达；
3. 断电重启也无需重烧：开机自启会再加载一次。

> 早期版本的"每次上电都要 JTAG 烧一次"已经不适用。现在位流存在 SD 卡上，由 PYNQ 自己加载。

### 备用：JTAG 直接烧 PL

```tcl
open_hw_manager; connect_hw_server -url localhost:3121; open_hw_target
set dev [get_hw_devices xc7z020_1]; current_hw_device $dev
set_property PROGRAM.FILE {top.bit} $dev
program_hw_devices $dev
```

只烧 `xc7z020` 那个器件，不要选 `arm_dap_0`。

## 3 重建位流

```bash
# Windows
fpga\build\build.bat

# Linux / macOS（Vivado 2025.2 需在 PATH，或 export VIVADO=/path/to/vivado）
bash fpga/build/build.sh
```

脚本内部就是两步：

```bash
vivado -mode batch -source fpga/src/tcl/01_project.tcl   # 建工程 + 源码 + BD
vivado -mode batch -source fpga/src/tcl/02_build.tcl     # 综合 + 实现 + 出位流
```

两个 tcl 都用 `[info script]` 推算根目录，**不含绝对路径**，整包搬到别的机器也能直接跑。
产物在 `fpga/src/prj/7020.runs/impl_1/top.bit`，需手动覆盖到 `fpga/build/top.bit`
和 `fpga/pynq/motor.bit`（交付的这份是现场验证过的版本）。

> `fpga/src/prj/` 没有放进交付包：`.xpr` / `.dcp` 会写死开发机的绝对路径，换机器必报错，
> 而用上面两个脚本几分钟就能重建。

## 4 关于 QSPI 固化（当前不可用）

板上有 Winbond **W25Q256JVEQ**（32 MB SPI NOR，U7），实测无法烧写：

```
Zynq> sf probe 0 0 0
zynq_qspi_irq_poll: Timeout!
jedec_spi_nor flash@0: unrecognized JEDEC id bytes: 90, d8, ff
```

x4 / x1 两种模式返回同一串垃圾值且 IRQ 超时，判断是 **PS7 配置里 QSPI 未使能**
（`ps7_init` 没有把对应 MIO 配成 QSPI 功能）。修复路径：在 block design 里打开 PS7 的 QSPI
外设并重编 FSBL，再重新生成 `BOOT.BIN` 烧 QSPI。

在修好之前，走 SD 卡 + PYNQ overlay 这条路即可，不影响使用。
