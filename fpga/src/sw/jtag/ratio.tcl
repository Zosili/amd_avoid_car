# ============================================================
#  ratio.tcl  --  左右占空比配比试验，用来找"走直线"的组合
#
#  环境变量：
#    PAIRS  "dl:dr,dl:dr,..."   默认 60:60,50:80,40:100
#    DWELL  每段秒数            默认 6
#    DIR_L / DIR_R  方向        默认 0 / 1  （两者都后退，当前唯一双轮可用的方向）
#
#  当前硬件限制：
#    右轮 in3(V6/J4-14) 断线 -> dir_r=0 会刹车，所以只能用 dir_r=1（后退）
#    左轮前进是 dir_l=1
#  => 调直线先在"双轮后退"下做，比例同样适用于前进。
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set PAIRS  "60:60,50:80,40:100"
set DWELL  "6"
set DIR_L  "0"
set DIR_R  "1"
foreach v {PAIRS DWELL DIR_L DIR_R} {
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
puts [format "PAIRS=%s  DWELL=%ss  DIR_L=%s DIR_R=%s" $PAIRS $DWELL $DIR_L $DIR_R]
puts "======================================================"

set t0 [clock milliseconds]
set seg 0
foreach pr [split $PAIRS ","] {
    set pr [string trim $pr]
    if {$pr eq ""} continue
    incr seg
    set dl [lindex [split $pr ":"] 0]
    set dr [lindex [split $pr ":"] 1]
    set w [motor_word $dl $dr $DIR_L $DIR_R]
    wr32 0x41200000 $w
    set g [rd32 0x41200000]
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts [format "%6.1f  第%d段  L=%-3s R=%-3s  GPIO=0x%08X" $el $seg $dl $dr $g]
    set tend [expr {[clock milliseconds] + $DWELL * 1000}]
    while {[clock milliseconds] < $tend} { after 100 }
}

wr32 0x41200000 0
puts [format "%6.1f  归零结束" [expr {([clock milliseconds] - $t0) / 1000.0}]]
puts "======================================================"
exit
