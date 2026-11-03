# ============================================================
#  uart0scan.tcl  --  扫描 MIO14/15 的正确复用值
#
#  背景：
#    P5 跳线已接到 PS 侧（3-1 / 4-2），CH340 应该落在 MIO14/15。
#    而 MIO14/15 在 Zynq 里对应的是 **UART0**（板子上丝印写的
#    "UART1(PS)" 是正点原子自己的编号，不是 Zynq 的 UART1）。
#
#    之前 uart0test.tcl 用的是 UART1(MIO48/49) 的值 0x16E0/0x16E1，
#    但 MIO 复用寄存器的位定义我不确定（0x16E0 里的 0xE0 到底是
#    L_SEL 还是 Speed/IO_Type 说不清），所以直接扫。
#
#  做法：
#    每个候选值都往 MIO14/15 写一遍，然后从 UART0 TX FIFO 发一条
#    带该值编号的字符串，PC 端开着 COM10 听。哪条字符串到了，
#    就说明哪个值是对的。
#
#  参数（环境变量）：
#    CONBACK 1=测完恢复运行（默认 1）
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set CONBACK 1
if {[info exists ::env(CONBACK)]} { set CONBACK [string trim $::env(CONBACK)] }

set U0      0xE0000000
set F0      0xE0000030
set CLKCTRL 0xF8000154
set MIO14   0xF8000738
set MIO15   0xF800073C
set APER    0xF800012C

# 候选值：0x1600 是"未使用引脚"的值，0xE0 是 UART1 引脚相对它的差。
# 这里把 L_SEL 可能所在的 bit1~bit4、以及 0xE0 那几个 bit 全组合一遍。
set CANDS {
    0x1600
    0x1602 0x1604 0x1608 0x1610
    0x1620 0x1640 0x1680
    0x1660 0x16A0 0x16C0 0x16E0
    0x16E2 0x16E4 0x16E8 0x16F0
}

proc slcr_unlock {} { wr32 0xF8000008 0x0000DF0D }
proc slcr_lock   {} { wr32 0xF8000004 0x0000767B }

connect
sel_a9
catch {stop}
after 200
puts "A9 state: [a9_state]"

# ---- 打开 UART0 的三处门控 ----
set ck   [rd32 $CLKCTRL]
set aper [rd32 $APER]
slcr_unlock
wr32 $CLKCTRL [expr {$ck | 0x1}]          ;# CLKACT0 = UART0 参考时钟
wr32 $APER    [expr {$aper | 0x01FF0000}] ;# 外设 APB 时钟门控全开
slcr_lock
after 100
puts [format "  UART_CLK_CTRL 0x%08X -> 0x%08X" $ck [rd32 $CLKCTRL]]
puts [format "  APER_CLK_CTRL  0x%08X -> 0x%08X" $aper [rd32 $APER]]

# ---- 配 UART0：8N1 / 115200 / 收发使能 ----
wr32 [expr {$U0 + 0x04}] 0x00000020
wr32 [expr {$U0 + 0x18}] 124
wr32 [expr {$U0 + 0x34}] 6
wr32 [expr {$U0 + 0x00}] 0x00000017
after 100
puts [format "  UART0: CR=0x%08X MR=0x%08X BAUDGEN=0x%08X BDIV=0x%08X SR=0x%08X" \
      [rd32 [expr {$U0 + 0x00}]] [rd32 [expr {$U0 + 0x04}]] \
      [rd32 [expr {$U0 + 0x18}]] [rd32 [expr {$U0 + 0x34}]] [rd32 [expr {$U0 + 0x2C}]]]
if {[rd32 [expr {$U0 + 0x00}]] == 0} {
    puts "  !!! UART0 寄存器还是 0，门控没打开，扫描无意义"
    exit 1
}

puts ""
puts "  开始扫描 MIO14/15 复用值 ..."
foreach v $CANDS {
    set vv [expr {$v}]
    slcr_unlock
    wr32 $MIO14 $vv
    wr32 $MIO15 [expr {$vv | 1}]     ;# bit0=1 -> RX 脚设为输入
    slcr_lock
    after 20

    set msg [format "MUX%04X\r\n" $vv]
    foreach ch [split $msg ""] {
        wr32 $F0 [scan $ch %c]
        after 2
    }
    for {set i 0} {$i < 30} {incr i} {
        if {[rd32 [expr {$U0 + 0x2C}]] & 0x08} { break }
        after 10
    }
    puts [format "    MIO14=0x%04X MIO15=0x%04X  已发 \"MUX%04X\"" $vv [expr {$vv|1}] $vv]
    after 250
}

puts ""
puts "  恢复 MIO14/15 与时钟 ..."
slcr_unlock
wr32 $MIO14 0x00001600
wr32 $MIO15 0x00001600
wr32 $CLKCTRL $ck
wr32 $APER $aper
slcr_lock
after 50
puts [format "  MIO14=0x%08X MIO15=0x%08X CLK=0x%08X APER=0x%08X" \
      [rd32 $MIO14] [rd32 $MIO15] [rd32 $CLKCTRL] [rd32 $APER]]

if {$CONBACK} {
    con
    after 300
    puts "  A9 state: [a9_state]"
}
puts "########## UART0SCAN DONE ##########"
exit
