# ============================================================
#  miotest.tcl  --  判断"板子的 USB 串口到底接在哪个 MIO 上"
#
#  思路：把 MIO49（UART1 RX）临时改成普通 GPIO 输入，然后一边让 PC
#        在 COM10 上狂发 0x55，一边用 JTAG 连续采样 MIO49 的电平。
#
#        真接着 CH340  -> 采样值会在 0/1 之间翻转（0x55 有下降沿）
#        没接          -> 采样值恒定（靠内部上拉读 1，或悬空读 0）
#
#  采样用 GPIO BANK1 的 DATA_RO_1 (0xE000A004)，MIO49 对应 bit17。
#
#  参数（环境变量）：
#    PIN      要采样的 MIO 号，默认 49
#    MS       采样时长，默认 6000
#    RESTORE  1=测完把 MIO 恢复成 UART（默认 1）
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set PIN     49
set MS      6000
set RESTORE 1
if {[info exists ::env(PIN)]}     { set PIN     [string trim $::env(PIN)] }
if {[info exists ::env(MS)]}      { set MS      [string trim $::env(MS)] }
if {[info exists ::env(RESTORE)]} { set RESTORE [string trim $::env(RESTORE)] }

set SLCR_UNLOCK 0xF8000008
set SLCR_LOCK   0xF8000004
set GPIO_RO1    0xE000A004
set GPIO_DIRM1  0xE000A248

proc slcr_unlock {} { wr32 0xF8000008 0x0000DF0D }
proc slcr_lock   {} { wr32 0xF8000004 0x0000767B }

connect
sel_a9
catch {stop}
after 200
puts "A9 state: [a9_state]"

set pinaddr [expr {0xF8000700 + 4 * $PIN}]
set bit     [expr {$PIN - 32}]
set mask    [expr {1 << $bit}]

puts [format "  目标 MIO%d  寄存器 0x%08X   GPIO_RO1 bit%d" $PIN $pinaddr $bit]
puts [format "  改之前: MIO%d = 0x%08X" $PIN [rd32 $pinaddr]]

# --- 改成 GPIO 输入（L_SEL 全 0 = GPIO，bit0 TRI_ENABLE=1 = 输入/高阻）---
slcr_unlock
wr32 $pinaddr 0x00001601
slcr_lock
after 50
puts [format "  改之后: MIO%d = 0x%08X  (GPIO 输入)" $PIN [rd32 $pinaddr]]

# --- GPIO 方向设为输入 ---
set d [rd32 $GPIO_DIRM1]
wr32 $GPIO_DIRM1 [expr {$d & ~$mask}]
after 20
puts [format "  DIRM_1 = 0x%08X" [rd32 $GPIO_DIRM1]]

# --- 连续采样 ---
puts ""
puts "  开始采样 $MS ms ..."
set t0  [clock milliseconds]
set n1  0
set n0  0
set chg 0
set prev -1
while {1} {
    if {([clock milliseconds] - $t0) >= $MS} { break }
    set v [expr {([rd32 $GPIO_RO1] >> $bit) & 1}]
    if {$v} { incr n1 } else { incr n0 }
    if {$prev >= 0 && $v != $prev} { incr chg }
    set prev $v
}

puts "---------------------------------------------"
puts [format "  采样点数 = %d   读 1 = %d   读 0 = %d   翻转次数 = %d" \
      [expr {$n1 + $n0}] $n1 $n0 $chg]
if {$chg > 0} {
    puts "  ==> MIO$PIN 【确实】被外部驱动，板子的串口就接在这根脚上"
} else {
    puts "  ==> MIO$PIN 电平恒定，没看到外部信号"
    puts "      要么板子的 USB 串口不在这根脚上，要么 PC 侧没在发数据"
}
puts "---------------------------------------------"

# --- 恢复成 UART1 ---
if {$RESTORE} {
    slcr_unlock
    if {$PIN == 49} { wr32 $pinaddr 0x000016E1 } else { wr32 $pinaddr 0x000016E0 }
    slcr_lock
    after 50
    puts [format "  已恢复: MIO%d = 0x%08X" $PIN [rd32 $pinaddr]]
    con
    after 300
    puts "  A9 state: [a9_state]"
}
puts "########## MIOTEST DONE ##########"
exit
