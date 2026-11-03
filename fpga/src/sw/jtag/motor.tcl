# ============================================================
#  motor.tcl  --  直接写电机寄存器（CPU 停机保持）
#
#  因为 PS 侧 UART 一直没输出，这里绕开固件，直接改 AXI GPIO：
#    [6:0]  duty_l 0~100     [14:8] duty_r 0~100
#    [16]   dir_l  0正 1反   [17]   dir_r  0正 1反
#
#  必须先保证 PL 已配置（先跑 bringup.tcl）。
#  halt=1 时会把 A9 停住，否则固件的扫描会覆盖你写的值。
#
#  参数（环境变量）：
#    DUTY_L  默认 0      DUTY_R  默认 0
#    DIR_L   默认 0      DIR_R   默认 0
#    HALT    默认 1      1=停核保持  0=只写不停核
#    HOLDMS  默认 0      >0 则保持这么多毫秒后再停（配合脚本观察）
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DUTY_L 0
set DUTY_R 0
set DIR_L  0
set DIR_R  0
set HALT   1
foreach {v d} {DUTY_L DUTY_L DUTY_R DUTY_R DIR_L DIR_L DIR_R DIR_R HALT HALT} { }
if {[info exists ::env(DUTY_L)]} { set DUTY_L $::env(DUTY_L) }
if {[info exists ::env(DUTY_R)]} { set DUTY_R $::env(DUTY_R) }
if {[info exists ::env(DIR_L)]}  { set DIR_L  $::env(DIR_L)  }
if {[info exists ::env(DIR_R)]}  { set DIR_R  $::env(DIR_R)  }
if {[info exists ::env(HALT)]}   { set HALT   $::env(HALT)   }

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 不可访问，先断电重上电，再跑 bringup.tcl"
    exit 1
}

# PL 必须已配置，否则写 AXI 会把 DAP 弄死
# 判据用标记文件（由 bringup.tcl / monitor.tcl 实测成功后落盘），
# 不用 DEVCFG_STATUS —— 实测证明 PCFG_DONE 不在那里。
set FORCE 0
if {[info exists ::env(FORCE)]} { set FORCE $::env(FORCE) }
# 读 PS 侧 DEVCFG_STATUS 判断，必要时自动重下 bit 流。
# 绝不能用"标记文件"当判据：断电会清空 PL 但标记还在，照写就是锁死 DAP。
if {$FORCE} {
    puts "  [pl] FORCE=1 跳过检查"
} else {
    pl_ensure
}

if {$HALT} {
    if {[catch {stop} e]} { puts "stop 失败: $e" } else { puts "A9 halted" }
}

set w [motor_word $DUTY_L $DUTY_R $DIR_L $DIR_R]
puts [format "写入 GPIO = 0x%08X  (L=%d%% dir=%d, R=%d%% dir=%d)" $w $DUTY_L $DIR_L $DUTY_R $DIR_R]
wr32 0x41200000 $w

if {[catch {
    set g [rd32 0x41200000]
    puts [format "回读 GPIO = 0x%08X  -> duty_l=%d duty_r=%d" $g [expr {$g & 0x7F}] [expr {($g >> 8) & 0x7F}]]
    pl_mark_ok
} e]} { puts "回读失败: $e" }

exit
