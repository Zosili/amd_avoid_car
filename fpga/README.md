# fpga/ —— FPGA 侧工程

| 子目录 | 内容 | 对应 3.2.5.1(2) |
|---|---|---|
| `src/vision/` | OV5640 → RGB565 → 千兆 UDP 视觉链路（RTL + 约束 + IP 配置 `xci`） | RTL/HLS 源码 |
| `src/motor/` | `motor_axi.v`：AXI4-Lite 电机控制器（20 kHz PWM + 300 ms 看门狗） | RTL 源码 |
| `build/` | Vivado 批处理构建脚本（tcl） | 可复现构建脚本 |
| `report/` | 综合实现原始报告（资源、时序、DRC、功耗） | 资源/时序/WNS 证据 |

## 平台

| 项 | 值 |
|---|---|
| 板卡 | 正点原子 领航者 V2 |
| 器件 | `xc7z020clg400-2`（Speed grade −2） |
| 工具 | **Vivado 2025.2**（报告里的版本；旧版本需重新生成 IP） |
| 时钟 | `sys_clk` 50 MHz（报告实测 WNS +1.096 ns） |
| 端口 | 板载千兆以太网（RGMII）、DVP 摄像头接口、ATK MODULE 扩展口 |

## 快速看结论

- 资源：LUT 1164 / 53200（2.19 %）、FF 1137 / 106400（1.07 %）、BRAM 0.5 tile、DSP 0 → 余量极大，后续可加 AXI 外设/加速模块。
- 时序：**全部约束满足**，WNS +1.096 ns、WHS +0.087 ns。
- 已上板验证：OV5640 出图 → UDP 送 PC → PC 侧 `view.py` 能看到画面（见 `host/app/vision_pc/`）。

## 还没做的（别当成已完成）

1. **电机 AXI 未在完整工程里例化**：`motor_axi.v` 是可综合模块，需要 Create and Package IP 或手工挂 AXI Interconnect，然后把 PS 的 `mmio.write` 接上（步骤见 `src/motor/README.md`）。
2. **视觉结果回传**（PC → PL 的 `a5 0f f0 5a` 帧）尚无解析模块；当前决策走 PS 的 TCP 5000，不依赖该回路。
3. **雷达 UART 若走 PL**（杜邦线方案）：需要自己写 UART 接收 + overlay，当前默认走 USB/PS。
