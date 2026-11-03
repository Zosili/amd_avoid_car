# ============================================================
#  ladder.tcl  --  占空比阶梯扫描（停核保持，用于目视找阈值）
#
#  环境变量：
#    STEPS   逗号分隔的占空比序列，默认 100,90,80,70,60,50
#    DWELL   每档停留秒数，默认 5
#    SIDE    BOTH / L / R，默认 BOTH（只测一侧时另一侧给 0）
#    DIR_L / DIR_R  方向，默认 0
#
#  输出：每一档的时间戳 + 实际写入的 GPIO，方便和目视对时间
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set STEPS "100,90,80,70,60,50"
set DWELL 5
set SIDE  "BOTH"
set DIR_L 0
set DIR_R 0
foreach v {STEPS DWELL SIDE DIR_L DIR_R} {
    if {[info exists ::env($v)]} { set $v [set ::env($v)] }
}

# cmd 的 "set X=Y && ..." 会把 && 前的空格也塞进变量值，
# 字符串比较会因此静默失败（数值比较看不出来），这里统一去掉。
set STEPS [string trim $STEPS]
set DWELL [string trim $DWELL]
set SIDE  [string trim $SIDE]
set DIR_L [string trim $DIR_L]
set DIR_R [string trim $DIR_R]

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 不可访问，先断电重上电"
    exit 1
}

pl_ensure

if {[catch {stop} e]} { puts "stop: $e" } else { puts "A9 halted" }

puts ""
puts [format "SIDE='%s'  DWELL='%s'  DIR_L='%s' DIR_R='%s'" $SIDE $DWELL $DIR_L $DIR_R]
puts "序列: '$STEPS'"
puts "--------------------------------------------------------"
puts "  t(s)   duty_l  duty_r   dirL dirR    GPIO"
puts "--------------------------------------------------------"

set t0 [clock milliseconds]
foreach d [split $STEPS ","] {
    set d [string trim $d]
    if {$d eq ""} continue
    set dl 0
    set dr 0
    if {$SIDE eq "BOTH" || $SIDE eq "L"} { set dl $d }
    if {$SIDE eq "BOTH" || $SIDE eq "R"} { set dr $d }
    set w [motor_word $dl $dr $DIR_L $DIR_R]
    wr32 0x41200000 $w
    set g [rd32 0x41200000]
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts [format "%6.1f     %3d      %3d     %d    %d     0x%08X" \
        $el [expr {$g & 0x7F}] [expr {($g >> 8) & 0x7F}] \
        [expr {($g >> 16) & 1}] [expr {($g >> 17) & 1}] $g]
    # 停留
    set tend [expr {[clock milliseconds] + $DWELL * 1000}]
    while {[clock milliseconds] < $tend} {
        after 100
    }
}

# 结束归零
wr32 0x41200000 0
set el [expr {([clock milliseconds] - $t0) / 1000.0}]
puts [format "%6.1f     %3d      %3d     -    -     0x%08X   <-- 归零结束" $el 0 0 0]
exit
