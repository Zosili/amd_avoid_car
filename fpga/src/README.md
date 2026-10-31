# fpga/src —— RTL 与约束

## vision/ —— OV5640 千兆 UDP 视觉链路

```
ov5640_udp_pc.v (top)
├── clk_wiz_0 (IP, ip/clk_wiz_0.xci)         时钟：50 MHz 系统 + 200 MHz IDELAY + 相机像素时钟
├── ov5640/
│   ├── i2c_dri.v                             SCCB/I2C 时序
│   └── i2c_ov5640_rgb565_cfg.v               OV5640 寄存器配置（RGB565 640×480）
├── start_transfer_ctrl.v                     传输启动控制
├── img_data_pkt.v                            像素打包 + 帧头（f0 5a a5 0f + 宽高）
│   └── async_fifo_2048x8b (IP, ip/)          跨时钟域缓冲
├── eth_top.v                                 以太网顶层
│   ├── eth_ctrl.v                            MAC 控制
│   ├── gmii_to_rgmii/                        RGMII↔GMII（rgmii_rx/rgmii_tx）
│   ├── arp/                                  ARP（arp.v / arp_rx / arp_tx / crc32_d8）
│   └── udp/                                  UDP（udp.v / udp_rx / udp_tx）
└── constrs/ov5640_udp_pc.xdc                 引脚与时序约束
```

**帧格式（PC 侧按此解析）**

```
f0 5a a5 0f | 02 80 01 e0 | 640*480*2 = 614400 B
帧头 4B        宽 0x0280=640, 高 0x01E0=480      RGB565（小端：需 ((x&0xFF)<<8)|(x>>8)）
```

**默认网络参数**（`ov5640_udp_pc.v` 顶部 parameter，改这里要同步 PC 侧）

| 项 | 值 |
|---|---|
| 板子 MAC / IP | `00:11:22:33:44:55` / `192.168.1.10` |
| 目标 MAC / IP | `ff:ff:ff:ff:ff:ff` / `192.168.1.102`（PC） |
| 端口 | 1234 |

**IP 的 `.xci` 为什么要进仓库**：`clk_wiz_0` 与 `async_fifo_2048x8b` 是 IP Integrator 生成的，只交 RTL 别人综合不出来；`.xci` 是可复现的最小单元（`build/build_project.tcl` 用 `read_ip` + `generate_target` 重建）。

## motor/ —— AXI 电机控制器

见 [`motor/README.md`](motor/README.md)。寄存器映射见 [`docs/interface.md`](../../docs/interface.md) §6。

## 编码约定

- RTL 注释是 **GBK**（历史文件，Vivado 默认编码），**不要用 UTF-8 编辑器整体重存**，否则中文注释会乱码；新增文件建议改英文注释。
- 端口/实例名保持原样（改了 top 名要同步 `.xdc` 与 `build_project.tcl`）。
