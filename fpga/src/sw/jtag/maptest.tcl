# ============================================================
#  maptest.tcl  --  钉死"代码通道"到"物理左右轮"的映射
#
#  背景：代码里叫 duty_l / duty_r，但从未验证 duty_l 到底驱动
#        物理左边的轮子还是右边的。命名一旦是反的，调参必然错位。
#
#  本脚本只驱动单通道，用"哪个轮子动"来反推映射：
#    段1  duty_l=80, duty_r=0   dir_l=0   -> 观察哪个轮子动
#    段2  全部 0（分隔 3 秒）
#    段3  duty_l=0,  duty_r=80  dir_r=1   -> 观察哪个轮子动
#    归零
#
#  两段各用已知"能转"的方向（dir_l=0 / dir_r=1），避免方向问题干扰判断。
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DUTY  "80"
set DWELL "10"
set GAP   "3"
foreach v {DUTY DWELL GAP} {
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
puts [format "DUTY=%s%%  DWELL=%ss  GAP=%ss" $DUTY $DWELL $GAP]
puts "======================================================="

set t0 [clock milliseconds]

# ---- 段1：只有 duty_l 非零 ----
set w [motor_word $DUTY 0 0 0]
wr32 0x41200000 $w
puts [format "%6.1f  【段1】只给 duty_l=%s  dir_l=0   GPIO=0x%08X" \
    [expr {([clock milliseconds]-$t0)/1000.0}] $DUTY [rd32 0x41200000]]
set tend [expr {[clock milliseconds] + $DWELL * 1000}]
while {[clock milliseconds] < $tend} { after 100 }

# ---- 段2：全停，做分隔 ----
wr32 0x41200000 0
puts [format "%6.1f  （停 %s 秒）" [expr {([clock milliseconds]-$t0)/1000.0}] $GAP]
set tend [expr {[clock milliseconds] + $GAP * 1000}]
while {[clock milliseconds] < $tend} { after 100 }

# ---- 段3：只有 duty_r 非零 ----
set w [motor_word 0 $DUTY 0 1]
wr32 0x41200000 $w
puts [format "%6.1f  【段3】只给 duty_r=%s  dir_r=1   GPIO=0x%08X" \
    [expr {([clock milliseconds]-$t0)/1000.0}] $DUTY [rd32 0x41200000]]
set tend [expr {[clock milliseconds] + $DWELL * 1000}]
while {[clock milliseconds] < $tend} { after 100 }

wr32 0x41200000 0
puts [format "%6.1f  归零结束" [expr {([clock milliseconds]-$t0)/1000.0}]]
puts "======================================================="
exit
