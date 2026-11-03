# ============================================================
#  pairs.tcl  --  按指定顺序驱动若干"方向组合"，每组 DWELL 秒
#
#  环境变量：
#    DIRS    "dl:dr,dl:dr,..."   方向组合序列，默认 0:0,1:1
#    DUTY    占空比（两侧相同）  默认 80
#    DWELL   每组秒数            默认 10
#    GAP     组间停留秒数        默认 3
#
#  已确认映射：duty_l -> 物理右轮，duty_r -> 物理左轮
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DIRS  "0:0,1:1"
set DUTY  "80"
set DWELL "10"
set GAP   "3"
foreach v {DIRS DUTY DWELL GAP} {
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
puts "映射: duty_l=物理右轮  duty_r=物理左轮"
puts "======================================================"

set t0 [clock milliseconds]
set seg 0
foreach pr [split $DIRS ","] {
    set pr [string trim $pr]
    if {$pr eq ""} continue
    incr seg
    set xl [lindex [split $pr ":"] 0]
    set xr [lindex [split $pr ":"] 1]
    set w [motor_word $DUTY $DUTY $xl $xr]
    wr32 0x41200000 $w
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts [format "%6.1f  >>> 第%d组  dir_l=%d(右轮) dir_r=%d(左轮)  GPIO=0x%08X" \
        $el $seg $xl $xr [rd32 0x41200000]]
    set tend [expr {[clock milliseconds] + $DWELL * 1000}]
    while {[clock milliseconds] < $tend} { after 100 }

    if {$seg < [llength [split $DIRS ","]]} {
        wr32 0x41200000 0
        puts [format "%6.1f      （停 %s 秒）" [expr {([clock milliseconds] - $t0) / 1000.0}] $GAP]
        set tend [expr {[clock milliseconds] + $GAP * 1000}]
        while {[clock milliseconds] < $tend} { after 100 }
    }
}

wr32 0x41200000 0
puts [format "%6.1f  归零结束" [expr {([clock milliseconds] - $t0) / 1000.0}]]
puts "======================================================"
exit
