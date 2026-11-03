# ============================================================
#  uart0test.tcl  --  验证板子的 USB_UART(P5 接 PS 侧) 能否打通
#
#  ！！ 关键方向 ！！
#    这块板子的 PS UART 是 Zynq 的 **UART0**，但两个脚的方向和 UART1 相反：
#        MIO14 = UART0_RX  -> 引脚值 0x16E1 (bit0 TRI_ENABLE=1，输入)
#        MIO15 = UART0_TX  -> 引脚值 0x16E0 (bit0=0，输出)
#    正点原子自己的丝印把 PS 串口叫 "UART1(PS)"，那是它的编号，
#    不是 Zynq 的 UART1（Zynq UART1 在 MIO48/49，本板上走 ATK 模块口）。
#
#    之前几轮一直把 MIO14 当 TX 写，等于把真 TX(MIO15) 配成高阻输入，
#    所以怎么发都收不到。下面这些值来自 Vivado 生成的基准 ps7_init
#    （tools\gen_uart0_ps7init.tcl，同时开 UART0+UART1 后对比得出）：
#        UART_CLK_CTRL  0x1202 -> 0x1203   (+bit0  CLKACT0)
#        APER_CLK_CTRL  0x016C000D -> 0x017C000D (+bit20 UART0 APB 时钟)
#        MIO14 = 0x16E1   MIO15 = 0x16E0
#
#  参数：MS 收数据时长（默认 3000）；CONBACK 1=测完恢复运行
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set MS      3000
set CONBACK 1
if {[info exists ::env(MS)]}      { set MS      [string trim $::env(MS)] }
if {[info exists ::env(CONBACK)]} { set CONBACK [string trim $::env(CONBACK)] }

set U0      0xE0000000
set F0      0xE0000030
set CLKCTRL 0xF8000154
set APER    0xF800012C
set MIO14   0xF8000738
set MIO15   0xF800073C
set MSG     "UART0_PS_SIDE_OK"

proc slcr_unlock {} { wr32 0xF8000008 0x0000DF0D }
proc slcr_lock   {} { wr32 0xF8000004 0x0000767B }

connect
sel_a9
catch {stop}
after 200
puts "A9 state: [a9_state]"

set ck   [rd32 $CLKCTRL]
set aper [rd32 $APER]
puts [format "  改前: CLK_CTRL=0x%08X APER=0x%08X MIO14=0x%08X MIO15=0x%08X" \
      $ck $aper [rd32 $MIO14] [rd32 $MIO15]]

slcr_unlock
wr32 $CLKCTRL [expr {$ck   | 0x00000001}]   ;# CLKACT0 -> UART0 参考时钟
wr32 $APER    [expr {$aper | 0x00100000}]   ;# bit20  -> UART0 APB 时钟
wr32 $MIO14   0x000016E1                    ;# UART0_RX <- 输入
wr32 $MIO15   0x000016E0                    ;# UART0_TX -> 输出
slcr_lock
after 100

puts [format "  改后: CLK_CTRL=0x%08X APER=0x%08X MIO14=0x%08X MIO15=0x%08X" \
      [rd32 $CLKCTRL] [rd32 $APER] [rd32 $MIO14] [rd32 $MIO15]]

# 8N1 + 115200 + 收发使能
wr32 [expr {$U0 + 0x04}] 0x00000020
wr32 [expr {$U0 + 0x18}] 124
wr32 [expr {$U0 + 0x34}] 6
wr32 [expr {$U0 + 0x20}] 1
wr32 [expr {$U0 + 0x00}] 0x00000017
after 100
set cr [rd32 [expr {$U0 + 0x00}]]
puts [format "  UART0: CR=0x%08X MR=0x%08X BAUDGEN=0x%08X BDIV=0x%08X SR=0x%08X" \
      $cr [rd32 [expr {$U0 + 0x04}]] [rd32 [expr {$U0 + 0x18}]] \
      [rd32 [expr {$U0 + 0x34}]] [rd32 [expr {$U0 + 0x2C}]]]
if {$cr == 0} { puts "  !!! UART0 寄存器仍是 0，门控没开"; exit 1 }

# 清 RX FIFO
set flush 0
while {([rd32 [expr {$U0 + 0x2C}]] & 0x02) == 0 && $flush < 200} {
    rd32 $F0
    incr flush
}
puts "  清掉残留 $flush 个字节"

puts "  发送: \"$MSG\\r\\n\""
foreach ch [split "$MSG\r\n" ""] {
    wr32 $F0 [scan $ch %c]
    after 3
}
set sr 0
for {set i 0} {$i < 40} {incr i} {
    set sr [rd32 [expr {$U0 + 0x2C}]]
    if {$sr & 0x08} { break }
    after 20
}
puts [format "  SR(after tx)=0x%08X  TXEMPTY=%d" $sr [expr {($sr >> 3) & 1}]]

puts "  收 $MS ms（PC 侧应在发 0x55）..."
set t0  [clock milliseconds]
set got 0
set firsts {}
while {1} {
    if {([clock milliseconds] - $t0) >= $MS} { break }
    set s0 [rd32 [expr {$U0 + 0x2C}]]
    if {$s0 != 0 && ($s0 & 0x02) == 0} {
        set b [expr {[rd32 $F0] & 0xFF}]
        incr got
        if {[llength $firsts] < 16} { lappend firsts [format %02X $b] }
    }
    after 2
}
puts "---------------------------------------------"
puts "  UART0 收到字节数 = $got"
if {$got > 0} { puts "  前 16 个 = [join $firsts { }]" }
puts "---------------------------------------------"

if {$CONBACK} {
    slcr_unlock
    wr32 $MIO14 0x00001600
    wr32 $MIO15 0x00001600
    wr32 $CLKCTRL $ck
    wr32 $APER $aper
    slcr_lock
    after 50
    con
    after 300
    puts "  A9 state: [a9_state]"
}
puts "########## UART0TEST DONE ##########"
exit
