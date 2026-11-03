# ============================================================
#  abtest.tcl  --  A/B 两组方向组合对比，用"车怎么动"判方向
#
#  盯着轮子看容易搞混（两个电机镜像安装），改成看整车运动：
#    两轮都前 -> 车前进    两轮都后 -> 车后退    一前一后 -> 原地转
#
#  注意：右轮 in3 断线，dir_r=0 时右轮会刹车不转，
#        所以只有下面这两种"双轮都主动"的组合有意义。
#
#  A 相： dir_l=0  dir_r=1
#  B 相： dir_l=1  dir_r=1
#  重复 ROUNDS 轮，每相 DWELL 秒
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DWELL  "6"
set ROUNDS "2"
set DUTY   "60"
foreach v {DWELL ROUNDS DUTY} {
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
puts [format "DUTY=%s%%  DWELL=%ss  ROUNDS=%s" $DUTY $DWELL $ROUNDS]
puts "A 相: dir_l=0 dir_r=1        B 相: dir_l=1 dir_r=1"
puts "=================================================="

set t0 [clock milliseconds]
for {set n 1} {$n <= $ROUNDS} {incr n} {
    foreach {tag xl xr} {A 0 1 B 1 1} {
        set w [motor_word $DUTY $DUTY $xl $xr]
        wr32 0x41200000 $w
        set g [rd32 0x41200000]
        set el [expr {([clock milliseconds] - $t0) / 1000.0}]
        puts [format "%6.1f  第%d轮 %s相  dir_l=%d dir_r=%d  GPIO=0x%08X" \
            $el $n $tag $xl $xr $g]
        set tend [expr {[clock milliseconds] + $DWELL * 1000}]
        while {[clock milliseconds] < $tend} { after 100 }
    }
}

wr32 0x41200000 0
puts [format "%6.1f  归零结束" [expr {([clock milliseconds] - $t0) / 1000.0}]]
puts "=================================================="
exit
