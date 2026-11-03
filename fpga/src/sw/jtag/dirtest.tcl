# ============================================================
#  dirtest.tcl  --  分相测试：分辨"ENA 不通" vs "IN 缺一根"
#
#  四相，每相 6 秒（DWELL 可调）：
#    第1相  右轮 100% 正转   dir_r=0  -> in3=1 in4=0
#    第2相  右轮 100% 反转   dir_r=1  -> in3=0 in4=1
#    第3相  左轮 100% 正转   dir_l=0  <- 已知能转，作对照组
#    第4相  左轮 100% 反转   dir_l=1
#
#  判读：
#    右轮只在 1 相转 -> IN4 断线/没接
#    右轮只在 2 相转 -> IN3 断线/没接
#    右轮 1、2 相都不转，左轮正常 -> ENA(V8) 或 B 桥 或 右电机 坏
#    左轮 3、4 相都正常转 -> 方向约定正确；只有一相转 -> 左侧也有 IN 断线
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set DWELL 6
if {[info exists ::env(DWELL)]} { set DWELL [string trim $::env(DWELL)] }

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 不可访问，先断电重上电"
    exit 1
}
pl_ensure

if {[catch {stop} e]} { puts "stop: $e" } else { puts "A9 halted" }

set phases [list \
    [list "第1相  右轮 100% 正转 dir_r=0 (in3=1 in4=0)" 0   100 0 0] \
    [list "第2相  右轮 100% 反转 dir_r=1 (in3=0 in4=1)" 0   100 0 1] \
    [list "第3相  左轮 100% 正转 dir_l=0 (in1=1 in2=0)" 100 0   0 0] \
    [list "第4相  左轮 100% 反转 dir_l=1 (in1=0 in2=1)" 100 0   1 0] ]

puts ""
puts [format "DWELL=%s 秒/相" $DWELL]
puts "========================================================="
set t0 [clock milliseconds]
set idx 0
foreach ph $phases {
    incr idx
    set label [lindex $ph 0]
    set dl    [lindex $ph 1]
    set dr    [lindex $ph 2]
    set xl    [lindex $ph 3]
    set xr    [lindex $ph 4]
    set w [motor_word $dl $dr $xl $xr]
    wr32 0x41200000 $w
    set g [rd32 0x41200000]
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts [format "%6.1f  >>> %s" $el $label]
    puts [format "          写入 0x%08X  回读 0x%08X" $w $g]
    set tend [expr {[clock milliseconds] + $DWELL * 1000}]
    while {[clock milliseconds] < $tend} { after 100 }
}

wr32 0x41200000 0
set el [expr {([clock milliseconds] - $t0) / 1000.0}]
puts [format "%6.1f  >>> 归零结束" $el]
puts "========================================================="
exit
