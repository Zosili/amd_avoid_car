source [file join [file dirname [info script]] common.tcl]
set REPO [file normalize [file join [file dirname [info script]] .. ..]]
set BITFILE "$REPO/prj/7020.runs/impl_1/top.bit"

connect
puts "=== 诊断：单独配置 PL 并等待 ==="
puts "1. rst -system"
catch {rst -system -stop}
after 1500

puts "2. ps7_init + ps7_post_config"
catch {source "$REPO/sw/ws/7020_plat/hw/sdt/ps7_init.tcl"}
catch {ps7_init}
catch {ps7_post_config}
after 500

puts "3. fpga -f $BITFILE"
sel_pl
if {[catch {fpga -f $BITFILE} e]} { puts "fpga FAILED: $e" } else { puts "fpga OK" }

puts "4. 等待配置完成（轮询 PCFG_DONE，最多 10 秒）"
sel_a9
set done 0
for {set i 0} {$i < 20} {incr i} {
    after 500
    if {[catch {set d [rd32 0xF8007014]} e]} { puts "  读 DEVCFG 失败: $e"; break }
    set done [expr {($d >> 1) & 1}]
    puts [format "  t=%0.1fs DEVCFG_STATUS=0x%08X PCFG_DONE=%d PCFG_INIT=%d" [expr {$i*0.5}] $d $done [expr {$d & 1}]]
    if {$done} break
}
if {$done} { puts ">>> PL 配置成功（PCFG_DONE=1）" } else { puts ">>> PL 配置未完成（PCFG_DONE 仍为 0）" }
exit
