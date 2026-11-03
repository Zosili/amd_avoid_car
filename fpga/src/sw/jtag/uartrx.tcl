# ============================================================
#  uartrx.tcl  --  只碰 UART1 寄存器，测"RX 这根脚到底通不通"
#
#  为什么不用 GPIO 去采样 MIO49：
#    这个工程的 PS GPIO 块（0xE000A000）没在 PS7 里使能，读它会总线超时，
#    又会把 A9 调试口锁死在 DAP 0xF0000021，只能断电。所以只读 UART 自己的
#    寄存器——它的时钟确认是开着的（SR 能正常读、FIFO 能排空）。
#
#  做法：halt A9 -> 把 UART 摆成 8N1/115200/收发使能 -> 清空 RX FIFO
#        -> PC 在 COM10 上狂发 0x55 -> 数 RX FIFO 里进来多少字节
#
#  判据：
#    有字节 -> MIO49 确实接着 CH340 的 TXD（RX 方向通）
#    没字节 -> MIO48/49 跟板子的 USB 串口就没连上
#
#  参数：MS 采样时长，默认 6000；CONBACK 1=测完恢复运行
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set MS      6000
set CONBACK 1
if {[info exists ::env(MS)]}      { set MS      [string trim $::env(MS)] }
if {[info exists ::env(CONBACK)]} { set CONBACK [string trim $::env(CONBACK)] }

set UART 0xE0001000
set FIFO 0xE0001030

connect
sel_a9
catch {stop}
after 200
puts "A9 state: [a9_state]"

# 8N1 + 115200 + 收发使能
wr32 [expr {$UART + 0x04}] 0x00000020     ;# MR
wr32 [expr {$UART + 0x18}] 124            ;# BAUDGEN = 100MHz/(124*7) = 115207
wr32 [expr {$UART + 0x34}] 6              ;# BAUDDIV
wr32 [expr {$UART + 0x20}] 1              ;# RXWM = 1（FIFO 里有 1 字节就置触发位）
wr32 [expr {$UART + 0x00}] 0x00000017     ;# CR: TXRST|RXRST|RXEN|TXEN
after 100

set sr [rd32 [expr {$UART + 0x2C}]]
puts [format "  CR=0x%08X MR=0x%08X BAUDGEN=0x%08X BDIV=0x%08X SR=0x%08X" \
      [rd32 [expr {$UART + 0x00}]] [rd32 [expr {$UART + 0x04}]] \
      [rd32 [expr {$UART + 0x18}]] [rd32 [expr {$UART + 0x34}]] $sr]

# 清空 RX FIFO（把之前残留的清掉）
set flush 0
while {([rd32 [expr {$UART + 0x2C}]] & 0x02) == 0} {
    rd32 $FIFO
    incr flush
    if {$flush > 200} break
}
puts "  清掉残留字节 $flush 个，现在 SR=[format 0x%08X [rd32 [expr {$UART + 0x2C}]]]"

puts "  开始收 $MS ms（PC 侧此刻应该在发 0x55）..."
set t0     [clock milliseconds]
set got    0
set firsts {}
while {1} {
    if {([clock milliseconds] - $t0) >= $MS} { break }
    set sr [rd32 [expr {$UART + 0x2C}]]
    if {($sr & 0x02) == 0} {
        set b [expr {[rd32 $FIFO] & 0xFF}]
        incr got
        if {[llength $firsts] < 16} { lappend firsts [format %02X $b] }
    }
    after 2
}

puts "---------------------------------------------"
puts "  收到字节数 = $got"
if {$got > 0} { puts "  前 16 个   = [join $firsts { }]" }
if {$got > 0} {
    puts "  ==> RX 方向通！MIO49 确实连到 CH340"
} else {
    puts "  ==> RX 方向一个字节都没进来"
    puts "      => 板子的 USB 串口不在 MIO48/49 上（或者线/跳线没接）"
}
puts "---------------------------------------------"

if {$CONBACK} {
    con
    after 300
    puts "  A9 state: [a9_state]"
}
puts "########## UARTRX DONE ##########"
exit
