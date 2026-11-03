# ============================================================
#  bringup.tcl  --  纯 JTAG 全流程启动（不需要 Vitis IDE）
#
#  顺序（与 Vitis standalone launch 一致）：
#    0. jtag targets       先枚举原始链路
#    1. rst -system        清 PS + 清 PL
#    2. ps7_init           配 PLL/时钟/DDR/MIO
#    3. ps7_post_config    打开 PCAP 时钟与电平转换（必须早于配 PL）
#    4. fpga -f top.bit    配 PL
#    5. 读 GPIO/温度        此时 AXI 才安全
#    6. dow motor_app.elf  下程序到 DDR
#    7. con                运行
#
#  !! 绝不可以在 fpga -f 之前读 AXI 地址 !!
#     未配置的 PL 会让总线超时，并把 A9 调试口锁死在
#     DAP 粘滞错误 0xF0000021，只能物理断电恢复。
#
#  参数走环境变量（bat 里 set 即可）：
#    BITFILE  默认 <repo>/prj/7020.runs/impl_1/top.bit
#    ELFFILE  默认 <repo>/sw/ws/motor_app/build/motor_app.elf
#    RUNSTA   1=下载后直接运行（默认 1），0=停在入口
# ============================================================

source [file join [file dirname [info script]] common.tcl]

# 仓库根目录 = sw/jtag/../..   这样整个工程搬到别的盘/别的机器也能跑
set REPO [file normalize [file join [file dirname [info script]] .. ..]]

set BITFILE "$REPO/prj/7020.runs/impl_1/top.bit"
set ELFFILE "$REPO/sw/ws/motor_app/build/motor_app.elf"
set RUNSTA  1

# 便携包里没有 prj\（51MB、路径绑死），bit 与 elf 放在 artifacts\ 下
if {![file exists $BITFILE] && [file exists "$REPO/artifacts/top.bit"]} {
    set BITFILE "$REPO/artifacts/top.bit"
}
if {![file exists $ELFFILE] && [file exists "$REPO/artifacts/motor_app.elf"]} {
    set ELFFILE "$REPO/artifacts/motor_app.elf"
}
if {[info exists ::env(BITFILE)]} { set BITFILE $::env(BITFILE) }
if {[info exists ::env(ELFFILE)]} { set ELFFILE $::env(ELFFILE) }
if {[info exists ::env(RUNSTA)]}  { set RUNSTA  $::env(RUNSTA)  }

puts "BITFILE = $BITFILE"
puts "ELFFILE = $ELFFILE"
puts "RUNSTA  = $RUNSTA"

connect

# ---------- 0. 链路 ----------
puts ""
puts "======== 0. JTAG 链路 ========"
if {![jtag_chain_ok]} {
    puts "  !!! 看不到 JTAG 链路：检查 USB 线 / 板子电源 / 跳线"
    exit 1
}
jtag targets
set devcnt [llength [targets]]
puts "  debug target 数 = $devcnt"

# ---------- 1. 系统复位 ----------
puts ""
puts "======== 1. 系统复位 ========"
pl_mark_clear
sel_apu
if {[catch {rst -system -stop} e]} {
    puts "  rst -system FAILED: $e"
    puts "  尝试 rst -dap ..."
    catch {rst -dap}
    after 500
    if {[catch {rst -system -stop} e2]} {
        puts "  rst -system 仍失败: $e2"
        puts ""
        puts "  !!! DAP 卡死 -> 请【断电重上电】后再跑一次"
        exit 1
    }
}
puts "  rst -system OK"
after 1500

sel_a9
set st [a9_state]
puts "  A9 state: $st"
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "  !!! A9 不可访问 -> 请【断电重上电】"
    exit 1
}

# ---------- 2. PS 初始化 ----------
puts ""
puts "======== 2. ps7_init ========"
source "$REPO/sw/ws/7020_plat/hw/sdt/ps7_init.tcl"
if {[catch {ps7_init} e]} { puts "  ps7_init FAILED: $e"; exit 1 }
puts "  ps7_init OK"
if {[catch {ps7_post_config} e]} { puts "  ps7_post_config FAILED: $e"; exit 1 }
puts "  ps7_post_config OK"

# ---------- 3. 启动模式（PS 起来后才读得到） ----------
puts ""
puts "======== 3. 启动模式 ========"
catch {
    set w [rd32 0xF8000024]
    puts [format "  BOOT_MODE_REG = 0x%08X (低4位 0=JTAG 1=QSPI 4=SD)" $w]
}

# ---------- 4. 配置 PL ----------
puts ""
puts "======== 4. 配置 PL ========"
sel_pl
if {[catch {fpga -f $BITFILE} e]} { puts "  fpga FAILED: $e"; exit 1 }
puts "  fpga OK"
pl_mark_ok

# ---------- 5. 验证 ----------
puts ""
puts "======== 5. 验证 PL + 读温度 ========"
sel_a9
catch {
    set d [rd32 0xF8007014]
    puts [format "  DEVCFG_STATUS = 0x%08X  PCFG_DONE=%d" $d [expr {($d >> 1) & 1}]]
}
if {[catch {
    set g [rd32 0x41200000]
    puts [format "  GPIO_DATA = 0x%08X (duty_l=%d duty_r=%d)" $g [expr {$g & 0x7F}] [expr {($g >> 8) & 0x7F}]]
    puts "  AXI 通路正常"
} e]} {
    puts "  !!! AXI 访问失败: $e"
}
catch {
    set raw [expr {[rd32 0xF8007100] & 0xFFFF}]
    puts [format "  XADC T = %.2f C" [expr {$raw * 503.975 / 65536.0 - 273.15}]]
}

# ---------- 6. 下载 ELF ----------
puts ""
puts "======== 6. 下载 ELF ========"
if {[catch {dow $ELFFILE} e]} { puts "  dow FAILED: $e"; exit 1 }
puts "  dow OK"

# ---------- 7. 运行 ----------
puts ""
if {$RUNSTA} {
    puts "======== 7. con（程序开跑）========"
    con
} else {
    puts "======== 7. 停在入口（未 con）========"
}
after 300
puts "  A9 state: [a9_state]"

puts ""
puts "########## BRINGUP DONE ##########"
exit
