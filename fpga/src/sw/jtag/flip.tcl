# ============================================================
#  flip.tcl  --  方向来回翻转，用来看清"哪个方向是前进"
#
#  环境变量：
#    SIDE   L / R / BOTH   默认 L
#    DUTY   占空比 0~100   默认 60
#    DWELL  每段秒数       默认 6
#    TIMES  来回轮数       默认 2
#
#  输出每段的方向定义：
#    dir=0  -> in1=1 in2=0
#    dir=1  -> in1=0 in2=1
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set SIDE  "L"
set DUTY  "60"
set DWELL "6"
set TIMES "2"
foreach v {SIDE DUTY DWELL TIMES} {
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
puts [format "SIDE=%s DUTY=%s%% DWELL=%ss TIMES=%s" $SIDE $DUTY $DWELL $TIMES]
puts "=================================================="

set t0 [clock milliseconds]
set seg 0
for {set n 0} {$n < $TIMES} {incr n} {
    foreach d {0 1} {
        incr seg
        set dl 0; set dr 0; set xl 0; set xr 0
        if {$SIDE eq "BOTH" || $SIDE eq "L"} {
            set dl $DUTY
            set xl $d
        }
        if {$SIDE eq "BOTH" || $SIDE eq "R"} {
            set dr $DUTY
            set xr $d
        }
        set w [motor_word $dl $dr $xl $xr]
        wr32 0x41200000 $w
        set g [rd32 0x41200000]
        set el [expr {([clock milliseconds] - $t0) / 1000.0}]
        if {$d == 0} { set nm "A段 dir=0 正转" } else { set nm "B段 dir=1 反转" }
        puts [format "%6.1f  第%d段  %s   GPIO=0x%08X" $el $seg $nm $g]
        set tend [expr {[clock milliseconds] + $DWELL * 1000}]
        while {[clock milliseconds] < $tend} { after 100 }
    }
}

wr32 0x41200000 0
puts [format "%6.1f  归零结束" [expr {([clock milliseconds] - $t0) / 1000.0}]]
puts "=================================================="
exit
