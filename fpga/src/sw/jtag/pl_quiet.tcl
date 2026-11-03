# ============================================================
#  pl_quiet.tcl -- 紧急静音：清掉 PL 配置
#    PL 一旦被清，beep/电机输出引脚不再驱动（高阻），
#    蜂鸣器停止、电机停转。PS 也会一并复位。
#    用途：调试时不想听蜂鸣器一直叫，重新 bringup 即可恢复。
# ============================================================
source [file join [file dirname [info script]] common.tcl]

connect
if {[catch {sel_apu} e]} { puts "  select APU FAILED: $e" }
if {[catch {rst -system -stop} e]} {
    puts "  rst -system FAILED: $e"
    catch {rst -dap}
    after 500
    catch {rst -system -stop}
}
puts "PL cleared -> beep/motor pins released"
exit
