# ============================================================
#  uartpoke.tcl  --  绕开固件，直接从 JTAG 往 UART1 发一串字节
#
#  目的：把"软件配错"和"引脚/接线没通"这两件事分开。
#
#  原理：UART 是 PS 外设，A9 停住它照样在跑。所以 halt 掉 A9 之后
#        直接写 TX FIFO (0xE0001030)，硬件会按已配好的波特率把字节
#        从 MIO48 移出去。同时 PC 端开着 COM 口听：
#          听到 HELLO_XXXX -> MIO48 -> CH340 这段路是通的
#          一个字都没听到 -> 板子上的串口根本不是接在 UART1/MIO48/49
#
#  参数（环境变量）：
#    MSG      要发的字符串，默认 "HELLO_FROM_UART1"
#    CONBACK  1=测完恢复运行（默认 1）
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set MSG     "HELLO_FROM_UART1"
set CONBACK 1
if {[info exists ::env(MSG)]}     { set MSG [string trim $::env(MSG)] }
if {[info exists ::env(CONBACK)]} { set CONBACK [string trim $::env(CONBACK)] }

set UART  0xE0001000
set FIFO  0xE0001030

connect
sel_a9

# 停核：避免固件同时写 TX FIFO（它每 10s 打一次 HB）
catch {stop}
after 200
puts "A9 state: [a9_state]"

# 把 UART 摆到已知状态：8N1 + 115200 + 收发使能
wr32 [expr {$UART + 0x04}] 0x00000020        ;# MR  : 8N1
wr32 [expr {$UART + 0x18}] 124               ;# BAUDGEN : 100MHz/(124*7)=115207
wr32 [expr {$UART + 0x34}] 6                 ;# BAUDDIV
wr32 [expr {$UART + 0x00}] 0x00000017        ;# CR  : TXRST|RXRST|RXEN|TXEN
after 100

puts [format "  CR      = 0x%08X" [rd32 [expr {$UART + 0x00}]]]
puts [format "  MR      = 0x%08X" [rd32 [expr {$UART + 0x04}]]]
puts [format "  BAUDGEN = 0x%08X" [rd32 [expr {$UART + 0x18}]]]
puts [format "  BAUDDIV = 0x%08X" [rd32 [expr {$UART + 0x34}]]]
puts [format "  SR(before) = 0x%08X" [rd32 [expr {$UART + 0x2C}]]]

puts "  发送: \"$MSG\\r\\n\""
foreach ch [split "$MSG\r\n" ""] {
    wr32 $FIFO [scan $ch %c]
    after 3
}

# 等 FIFO 排空（SR bit2 TEMPTY=1 表示发送 FIFO 空）
for {set i 0} {$i < 40} {incr i} {
    set sr [rd32 [expr {$UART + 0x2C}]]
    if {$sr & 0x04} { break }
    after 20
}
puts [format "  SR(after)  = 0x%08X   (bit2 TEMPTY=%d)" \
      $sr [expr {($sr >> 2) & 1}]]

if {$CONBACK} {
    con
    after 300
    puts "  A9 state: [a9_state]"
}
puts "########## UARTPOKE DONE ##########"
exit
