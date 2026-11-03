# ============================================================
# 7020 合并工程顶层约束
#   ① 系统时钟/复位      —— 来自 PL 50MHz 晶振（U18）
#   ② 以太网 RGMII       —— 正点原子领航者 ZYNQ7020
#   ③ OV5640 摄像头 DVP
#   ④ L298N 双路电机     —— BANK13
#
#   PS 的 DDR3 / MIO 是专用引脚，由 PS 配置决定，这里不需要约束。
#   原来两个工程的 xdc 已备份在 constraints/ref/ 下。
# ============================================================

# ---------------- ① 时钟与复位 ----------------
create_clock -period 20.000 -name sys_clk [get_ports sys_clk]
set_property -dict {PACKAGE_PIN U18 IOSTANDARD LVCMOS33} [get_ports sys_clk]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports sys_rst_n]

# ---------------- ② 以太网 RGMII ----------------
create_clock -period 8.000 -name eth_rxc [get_ports eth_rxc]
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS33} [get_ports eth_rst_n]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD LVCMOS33} [get_ports eth_rxc]
set_property -dict {PACKAGE_PIN E17 IOSTANDARD LVCMOS33} [get_ports eth_rx_ctl]
set_property -dict {PACKAGE_PIN B19 IOSTANDARD LVCMOS33} [get_ports {eth_rxd[0]}]
set_property -dict {PACKAGE_PIN A20 IOSTANDARD LVCMOS33} [get_ports {eth_rxd[1]}]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {eth_rxd[2]}]
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports {eth_rxd[3]}]
set_property -dict {PACKAGE_PIN B20 IOSTANDARD LVCMOS33} [get_ports eth_txc]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS33} [get_ports eth_tx_ctl]
set_property -dict {PACKAGE_PIN D18 IOSTANDARD LVCMOS33} [get_ports {eth_txd[0]}]
set_property -dict {PACKAGE_PIN C20 IOSTANDARD LVCMOS33} [get_ports {eth_txd[1]}]
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports {eth_txd[2]}]
set_property -dict {PACKAGE_PIN D20 IOSTANDARD LVCMOS33} [get_ports {eth_txd[3]}]

# ---------------- ③ OV5640 摄像头 ----------------
create_clock -period 40.000 -name cmos_pclk [get_ports cam_pclk]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets cam_pclk_IBUF]
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33} [get_ports cam_pclk]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports cam_rst_n]
set_property -dict {PACKAGE_PIN V15 IOSTANDARD LVCMOS33} [get_ports cam_pwdn]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[0]}]
set_property -dict {PACKAGE_PIN U13 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[1]}]
set_property -dict {PACKAGE_PIN V13 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[2]}]
set_property -dict {PACKAGE_PIN U15 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[3]}]
set_property -dict {PACKAGE_PIN U14 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[4]}]
# cam_data[5] = W13 —— 2026-10-31 实测判定：D5 这根数据线是【断路】的。
#   证据：加 PULLTYPE PULLDOWN 后，bit5 由恒 1 变成恒 0（断路悬空才会跟随内部下拉；
#         若短到 3V3，20k 下拉拉不动，仍会是 1）。
#   影响：D5 在 RGB565 里对应 R 的 bit2 与 G 的 bit0，R 被强置 1 -> 画面偏红约 13%。
#   现改为 PULLUP，让悬空电平稳定为 1（悬空漂移受温度/干扰影响，不稳定），
#   配合 PC 端 colorfix.py 的 "d5" 模式做确定性补偿。
#   ★ 修好 D5 排线后，把 PULLTYPE PULLUP 去掉，并把 CFIX 设为 off。
set_property -dict {PACKAGE_PIN W13 IOSTANDARD LVCMOS33 IOB TRUE PULLTYPE PULLUP} [get_ports {cam_data[5]}]
set_property -dict {PACKAGE_PIN V12 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[6]}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33 IOB TRUE} [get_ports {cam_data[7]}]
set_property -dict {PACKAGE_PIN U12 IOSTANDARD LVCMOS33} [get_ports cam_vsync]
set_property -dict {PACKAGE_PIN T12 IOSTANDARD LVCMOS33} [get_ports cam_href]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports cam_scl]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports cam_sda]

# ---------------- ④ 双路电机（L298N）----------------
#   左路（A 桥 OUT1/OUT2）：pwm_l->ENA U7 / in1 T9 / in2 Y7
#   右路（B 桥 OUT3/OUT4）：pwm_r->ENB V8 / in3 V6 / in4 Y9
set_property -dict {PACKAGE_PIN U7 IOSTANDARD LVCMOS33} [get_ports pwm_l]
set_property -dict {PACKAGE_PIN T9 IOSTANDARD LVCMOS33} [get_ports in1]
set_property -dict {PACKAGE_PIN Y7 IOSTANDARD LVCMOS33} [get_ports in2]
set_property -dict {PACKAGE_PIN V8 IOSTANDARD LVCMOS33} [get_ports pwm_r]
set_property -dict {PACKAGE_PIN V6 IOSTANDARD LVCMOS33} [get_ports in3]
set_property -dict {PACKAGE_PIN Y9 IOSTANDARD LVCMOS33} [get_ports in4]

# ---------------- ⑤ 蜂鸣器 ----------------
#   引脚号来自官方 NAVIGATOR_ZYNQ_IO.xdc 第 28 行：
#     set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports beep]
#   驱动来自 AXI GPIO 的 bit18（top.v 里 assign beep = gpio_out[18]）
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports beep]

# ---------------- ⑤2 调试 LED（2026-11-02，排查"转向不生效"）----------------
#   来源：领航者官方 IO 表  led[0]=H15(底板) / led[1]=L15(底板) / led=J16(核心板)
#   dbg_led[0] = eff_steer != 0   ：转向真正生效
#   dbg_led[1] = UDP steer != 0   ：PC 指令解析出来了
#   dbg_led[2] = duty_l < 前馈    ：占空比确实被降下来了
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports {dbg_led[0]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports {dbg_led[1]}]
set_property -dict {PACKAGE_PIN J16 IOSTANDARD LVCMOS33} [get_ports {dbg_led[2]}]

# ---------------- 配置电压 ----------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# ---------------- ⑥ 跨时钟域（CDC）约束 ----------------
# motor_decision 在 eth_rxc(125MHz) 域，输出 duty/dir/beep 经两级同步器
# 跨到 sys_clk(50MHz, PL 晶振) 域。这是标准异步 CDC，两级同步器已做同步，
# 不应做 setup/hold 检查，声明 false_path 消除误报的时序违例。
# 注意：不再跨到 clk_fpga_0 —— 那条路依赖 PS 初始化，只烧 PL 位流时
# fclk_clk0 不跑，电机/蜂鸣器会整条死掉。现已改用 PL 自己的 sys_clk。
set_false_path -from [get_clocks eth_rxc] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_clocks eth_rxc]
# 雷达 GPIO：PS 的 fclk_clk0(clk_fpga_0) 写 gpio_out -> eth_rxc 域 2 级同步器，
# 异步 CDC，不做时序检查（否则 radar_dist_s1 同步器会报违例）
set_false_path -from [get_clocks clk_fpga_0] -to [get_clocks eth_rxc]

# ---------------- ⑦ AXI UART Lite（雷达串口，队友 PYNQ 方案）----------------
#   雷达 COIN-D6 接 J4 扩展口：板子 TX(U9)->雷达 RX，板子 RX(U8)<-雷达 TX
#   引脚来源：领航者ZYNQ底板原理图 V3.9 第 3 页 J4，B13_L17_P=U9 / B13_L17_N=U8
set_property -dict {PACKAGE_PIN U9 IOSTANDARD LVCMOS33} [get_ports tx_0]
set_property -dict {PACKAGE_PIN U8 IOSTANDARD LVCMOS33} [get_ports rx_0]

# ---------------- ⑧ 编码器（AB 相，左右后轮）----------------
#   霍尔编码器 AB 相接 J4 扩展口 Bank13。3.3V 供电 + 直接接 FPGA。
#   引脚（J4 针脚号取自底板原理图 V3.9 第 5 页 "BANK13 Header 20X2"）：
#     左轮 A = Y12 (IO_L20P / B13_L20_P) = J4-24   ← 2026-11-02 从 W8 迁来
#     左轮 B = W10 (IO_L16P / B13_L16_P) = J4-19
#     右轮 A = Y11 (IO_L18N / B13_L18_N) = J4-25
#     右轮 B = T5  (IO_L19P / B13_L19_P) = J4-4
#   ★ 2026-11-02 关键修复（差分对冲突）：
#     原左轮 A 接 W8 = IO_L15N_T2_DQS，和右轮 PWM 的 V8 = IO_L15P_T2_DQS
#     是【同一个差分对】，而且在 J4 上就是相邻的 7/8 两脚。同一个 IO tile
#     里一侧做输入、一侧做输出会互相干扰，实测现象：拔掉 W8 右轮才开始转、
#     左轮编码器读数不可信（表现为两轮速度对不上、PID 两侧表现不一致）。
#     现把左轮 A 迁到 L20 差分对的 P 侧(Y12)：该对整对空闲、只用了 P 侧，
#     芯片内部与板级都不再与任何信号共用 tile。
#   ★ 禁止用 FPGA 内部 PULLUP：W8/W10 是 DQS 引脚(IO_L15P/N_T2_DQS_13)，
#     加 PULLUP 会触发 DQS 专用逻辑、跨 Bank 搞瘫网口(实测 3 次坐实)。
#     编码器开漏输出靠【外部 10kΩ 上拉到 3.3V】解决，不碰内部 PULLUP。
set_property -dict {PACKAGE_PIN Y12 IOSTANDARD LVCMOS33} [get_ports enc_l_a]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports enc_l_b]
set_property -dict {PACKAGE_PIN Y11 IOSTANDARD LVCMOS33} [get_ports enc_r_a]
set_property -dict {PACKAGE_PIN T5  IOSTANDARD LVCMOS33} [get_ports enc_r_b]
