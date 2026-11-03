# ============================================================
#  combo4.tcl  --  四种方向组合全测，用整车运动判定前进组合
#
#  已核实映射（maptest 实测）：
#      duty_l / dir_l  ->  物理【右】轮
#      duty_r / dir_r  ->  物理【左】轮
#
#  整车运动的判读不会出错：
#     两轮都前 -> 前进      两轮都后 -> 后退
#     一前一后 -> 原地打转
#
#  四段顺序（每段 DWELL 秒）：
#     段1  dir_l=0 dir_r=0
#     段2  dir_l=1 dir_r=1
#     段3  dir_l=0 dir_r=1
#     段4  dir_l=1 dir_r=0
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DUTY  "60"
set DWELL "6"
foreach v {DUTY DWELL} {
    if {[info exists ::env($v)]} { set $v [string trim [set ::env($v)]] }
}

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 不可访问，先断电重上电"; exit 1
}
pl_ensure
if {[catch {stop} e]} { puts "stop: $e" } else { puts "A9 halted" }

puts ""
puts [format "DUTY=%s%%  DWELL=%ss   映射: duty_l=右轮  duty_r=左轮" $DUTY $DWELL]
puts "=========================================================="
puts "  段1 dir_l=0 dir_r=0    段2 dir_l=1 dir_r=1"
puts "  段3 dir_l=0 dir_r=1    段4 dir_l=1 dir_r=0"
puts "=========================================================="

set t0 [clock milliseconds]
set seg 0
foreach c {{0 0} {1 1} {0 1} {1 0}} {
    incr seg
    set xl [lindex $c 0]
    set xr [lindex $c 1]
    set w [motor_word $DUTY $DUTY $xl $xr]
    wr32 0x41200000 $w
    set g [rd32 0x41200000]
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts [format "%6.1f  段%d  dir_l=%d(右轮) dir_r=%d(左轮)  GPIO=0x%08X" \
        $el $seg $xl $xr $g]
    set tend [expr {[clock milliseconds] + $DWELL * 1000}]
    while {[clock milliseconds] < $tend} { after 100 }
    # 段间停 2 秒，方便区分
    wr32 0x41200000 0
    set tend [expr {[clock milliseconds] + 2000}]
    while {[clock milliseconds] < $tend} { after 100 }
}

puts [format "%6.1f  归零结束" [expr {([clock milliseconds] - $t0) / 1000.0}]]
puts "=========================================================="
exit
