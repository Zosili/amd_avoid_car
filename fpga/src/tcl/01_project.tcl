# =====================================================================
#  01_project.tcl —— 建工程 + 源码 + IP + block design + wrapper
#  器件：xc7z020clg400-2   Vivado 2025.2
#
#  设计结构：
#    top (rtl/top.v)
#     ├─ design_1_wrapper     ← PS7 + AXI GPIO（PS 写寄存器控电机）
#     │    ├─ processing_system7_0   FCLK_CLK0=100MHz / FCLK_RESET0_N
#     │    └─ axi_gpio_0             32bit 全输出 -> gpio_io_o
#     ├─ ov5640_udp_pc        ← 图像采集 + UDP 发送（纯 PL，独立 50M/200M 时钟）
#     └─ motor_axi_top        ← gpio_out -> 双路 PWM/方向
#
#  BD 采用手工连线（不用 apply_bd_automation），
#  因为 automation 会先把 FCLK_CLK0 连到 interconnect，
#  之后 make_bd_pins_external 就引不出来了。
# =====================================================================

set root [file normalize [file dirname [file dirname [info script]]]]
puts "== ROOT = $root"

set part xc7z020clg400-2
set prjdir $root/prj

# ---------------------------------------------------------------------
# 1. 建工程
# ---------------------------------------------------------------------
if {[file exists $prjdir]} { file delete -force $prjdir }
create_project 7020 $prjdir -part $part -force
set_property target_language Verilog [current_project]
set_property source_mgmt_mode None [current_project]

# ---------------------------------------------------------------------
# 2. RTL
# ---------------------------------------------------------------------
set rtl_list [list \
    $root/rtl/ov5640_udp_pc.v \
    $root/rtl/eth_top.v \
    $root/rtl/eth_ctrl.v \
    $root/rtl/img_data_pkt.v \
    $root/rtl/start_transfer_ctrl.v \
    $root/rtl/arp/arp.v \
    $root/rtl/arp/arp_rx.v \
    $root/rtl/arp/arp_tx.v \
    $root/rtl/arp/crc32_d8.v \
    $root/rtl/udp/udp.v \
    $root/rtl/udp/udp_rx.v \
    $root/rtl/udp/udp_tx.v \
    $root/rtl/gmii_to_rgmii/gmii_to_rgmii.v \
    $root/rtl/gmii_to_rgmii/rgmii_rx.v \
    $root/rtl/gmii_to_rgmii/rgmii_tx.v \
    $root/rtl/ov5640/i2c_dri.v \
    $root/rtl/ov5640/i2c_ov5640_rgb565_cfg.v \
    $root/rtl/motor/motor_axi_top.v \
    $root/rtl/motor/motor_diff_ctrl.v \
]
if {[file exists $root/rtl/top.v]} {
    lappend rtl_list $root/rtl/top.v
} else {
    puts "== 注意：rtl/top.v 还不存在（首轮建 BD 时正常）"
}
add_files -norecurse -fileset sources_1 $rtl_list
puts "== 已加入 [llength $rtl_list] 个 RTL 文件"

# ---------------------------------------------------------------------
# 3. IP（每个 IP 独立目录，否则 Vivado 会把 IP 判为 locked）
# ---------------------------------------------------------------------
add_files -norecurse -fileset sources_1 [list \
    $root/ip/clk_wiz_0/clk_wiz_0.xci \
    $root/ip/async_fifo_2048x8b/async_fifo_2048x8b.xci ]
set ip_files [get_files -quiet *.xci]
if {[llength $ip_files]} {
    puts "== generate_target: $ip_files"
    generate_target all $ip_files
}

# ---------------------------------------------------------------------
# 4. 约束
# ---------------------------------------------------------------------
add_files -norecurse -fileset constrs_1 [list $root/constraints/7020_top.xdc]

# ---------------------------------------------------------------------
# 5. Block Design
# ---------------------------------------------------------------------
create_bd_design "design_1"
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0

# DDR3 1GB(32bit) / FCLK_CLK0=100MHz / UART1 打印
# 去掉 5 个 PS7 里不存在的参数名（NUM_RANKS / T_CKE / T_REFI / T_RTP / T_WR）
set ps_cfg [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49} \
    CONFIG.PCW_EN_DDR {1} \
    CONFIG.PCW_UIPARAM_DDR_MEMORY_TYPE {DDR 3} \
    CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16RE-125} \
    CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH {32 Bit} \
    CONFIG.PCW_UIPARAM_DDR_DRAM_WIDTH {16 Bits} \
    CONFIG.PCW_UIPARAM_DDR_DEVICE_CAPACITY {4096 MBits} \
    CONFIG.PCW_UIPARAM_DDR_SPEED_BIN {DDR3_1600K} \
    CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ {533.333333} \
    CONFIG.PCW_UIPARAM_DDR_CL {11} \
    CONFIG.PCW_UIPARAM_DDR_CWL {8} \
    CONFIG.PCW_UIPARAM_DDR_T_RCD {13.75} \
    CONFIG.PCW_UIPARAM_DDR_T_RP {13.75} \
    CONFIG.PCW_UIPARAM_DDR_T_RAS_MIN {35.0} \
    CONFIG.PCW_UIPARAM_DDR_T_RC {48.75} \
    CONFIG.PCW_UIPARAM_DDR_T_FAW {40.0} \
    CONFIG.PCW_UIPARAM_DDR_ECC {Disabled} \
]
set_property -dict $ps_cfg [get_bd_cells processing_system7_0]

# AXI GPIO：32 位全输出
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_0
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {0} \
] [get_bd_cells axi_gpio_0]

# --- AXI 互联：PS 的 M_AXI_GP0 是 AXI3，GPIO 是 AXI4-Lite，
#     协议不兼容必须过 axi_interconnect（1 主 1 从）---
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] \
    [get_bd_cells ps7_0_axi_periph]

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] \
                    [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI] \
                    [get_bd_intf_pins axi_gpio_0/S_AXI]

# --- 复位同步：FCLK_RESET0_N 相对 FCLK_CLK0 是异步的，
#     直接用会被 BD 判为 "asynchronous reset source"（CRITICAL WARNING），
#     插一级 proc_sys_reset，输出 peripheral_aresetn 给 AXI 从设备和电机用 ---
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_100M

# --- 外部端口 ---
create_bd_port -dir O -type clk -freq_hz 100000000 FCLK_CLK0_0
create_bd_port -dir O -type rst peripheral_aresetn_0
create_bd_port -dir O -from 31 -to 0 gpio_io_o_0

connect_bd_net [get_bd_ports FCLK_CLK0_0] \
               [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins rst_ps7_100M/slowest_sync_clk] \
               [get_bd_pins ps7_0_axi_periph/ACLK] \
               [get_bd_pins ps7_0_axi_periph/S00_ACLK] \
               [get_bd_pins ps7_0_axi_periph/M00_ACLK] \
               [get_bd_pins axi_gpio_0/s_axi_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] \
               [get_bd_pins rst_ps7_100M/ext_reset_in]
connect_bd_net [get_bd_ports peripheral_aresetn_0] \
               [get_bd_pins rst_ps7_100M/peripheral_aresetn] \
               [get_bd_pins ps7_0_axi_periph/ARESETN] \
               [get_bd_pins ps7_0_axi_periph/S00_ARESETN] \
               [get_bd_pins ps7_0_axi_periph/M00_ARESETN] \
               [get_bd_pins axi_gpio_0/s_axi_aresetn]
connect_bd_net [get_bd_ports gpio_io_o_0] [get_bd_pins axi_gpio_0/gpio_io_o]

# DDR / FIXED_IO 必须显式引出，否则 wrapper 里没有这些端口
make_bd_intf_pins_external [get_bd_intf_pins processing_system7_0/DDR]
make_bd_intf_pins_external [get_bd_intf_pins processing_system7_0/FIXED_IO]

foreach p [get_bd_ports] {
    puts "   BD port: [get_property NAME $p]  dir=[get_property DIR $p]  type=[get_property TYPE $p]"
}

assign_bd_address
validate_bd_design
save_bd_design

# ---------------------------------------------------------------------
# 6. 生成输出产品 + wrapper
# ---------------------------------------------------------------------
set bd_file [get_files design_1.bd]
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top -force]
add_files -norecurse -fileset sources_1 $wrapper
puts "== wrapper: $wrapper"
update_compile_order -fileset sources_1

set wf [lindex $wrapper 0]
puts "===== WRAPPER PORTS ($wf) ====="
set fh [open $wf r]
while {[gets $fh line] >= 0} {
    if {[string match "*DDR*" $line] || [string match "*FIXED_IO*" $line] || \
        [string match "*FCLK*" $line] || [string match "*gpio*" $line]} {
        puts "  [string trim $line]"
    }
}
close $fh

close_project
puts "== 01_project.tcl 完成"
