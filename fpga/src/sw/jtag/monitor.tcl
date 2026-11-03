# ============================================================
#  monitor.tcl  --  盯固件在干什么（不改任何状态）
#
#  1) 每秒采样 AXI GPIO，打印固件写进去的占空比
#  2) 用 readjtaguart 抓 DCC 输出（固件的心跳/温度/扫描日志）
#
#  参数：SECS 默认 25 秒
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set SECS 25
if {[info exists ::env(SECS)]} { set SECS $::env(SECS) }

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 不可访问，先断电重上电"
    exit 1
}

# 先确认 AXI 通（此时 PL 应当已由 bringup.tcl 配置好）
if {[catch {set g0 [rd32 0x41200000]} e]} {
    puts "!!! AXI 读失败，PL 未配置？先跑 bringup.tcl"
    exit 1
}
puts "AXI OK, 初始 GPIO = [format 0x%08X $g0]"
# 实测成功 -> 证明 PL 已配置，落标记供 motor.tcl 使用
pl_mark_ok

# 启动 DCC 抓取
if {[catch {readjtaguart -start} e]} { puts "readjtaguart -start 失败: $e" }

puts ""
puts " t(s)  duty_l  duty_r  dir_l dir_r   GPIO"
puts "-------------------------------------------------"
set t0 [clock milliseconds]
set lastw -1
while {1} {
    set el [expr {([clock milliseconds] - $t0) / 1000.0}]
    if {$el >= $SECS} { break }
    if {[catch {set g [rd32 0x41200000]} e]} {
        puts "  读 GPIO 失败: $e"; break
    }
    set dl [expr {$g & 0x7F}]
    set dr [expr {($g >> 8) & 0x7F}]
    set xl [expr {($g >> 16) & 1}]
    set xr [expr {($g >> 17) & 1}]
    if {$g != $lastw} {
        puts [format "%5.1f   %3d     %3d      %d     %d     0x%08X  <" $el $dl $dr $xl $xr $g]
        set lastw $g
    } else {
        puts [format "%5.1f   %3d     %3d      %d     %d     0x%08X" $el $dl $dr $xl $xr $g]
    }
    after 1000
}

puts ""
puts "===== DCC 输出（固件自己打印的）====="
if {[catch {readjtaguart -stop} e]} { puts "readjtaguart -stop 失败: $e" }
puts "===== DCC 结束 ====="
exit
