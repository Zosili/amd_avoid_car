# ============================================================
#  common.tcl  --  xsdb 公共辅助过程
# ============================================================

# 连上后先跑一次：先把原始 JTAG 链枚举出来，否则 targets 可能返回空
proc jtag_probe {} {
    catch {jtag targets}
    catch {targets}
    return 1
}

proc jtag_chain_ok {} {
    if {[catch {set l [jtag targets]} e]} { return 0 }
    return [expr {[llength $l] > 0}]
}

# 读 32 位寄存器（自动解析 "ADDR: VALUE" 输出，带 catch）
proc rd32 {addr} {
    if {[catch {set r [lindex [mrd -force $addr] end]} e]} {
        error "mrd $addr failed: $e"
    }
    return [expr "0x$r"]
}

# 写 32 位寄存器
proc wr32 {addr val} {
    if {[catch {mwr -force $addr $val} e]} {
        error "mwr $addr failed: $e"
    }
}

proc sel_a9 {} { targets -set -filter {name =~ "*MPCore #0*"} }
proc sel_pl {} { targets -set -filter {name =~ "*xc7z020*"} }
proc sel_apu {} { targets -set -filter {name =~ "APU"} }

# 返回 A9 是否可用：OK / DAP_ERR / SEL_FAIL
proc a9_state {} {
    if {[catch {sel_a9} e]} { return "SEL_FAIL" }
    set t [string trim [target]]
    if {[string match "*APB AP transaction*" $t]} { return "DAP_ERR" }
    if {[string match "*Running*"         $t]} { return "Running" }
    if {[string match "*Suspended*"       $t]} { return "Suspended" }
    if {[string match "*Halted*"          $t]} { return "Halted" }
    if {[string match "*Reset*"           $t]} { return "Reset" }
    return "Other"
}

# 拼电机控制字：  duty_l/duty_r 0~100，dir 0=正转 1=反转
proc motor_word {duty_l duty_r {dir_l 0} {dir_r 0}} {
    set w [expr {($duty_l & 0x7F) | (($duty_r & 0x7F) << 8) \
                 | (($dir_l & 1) << 16) | (($dir_r & 1) << 17)}]
    return $w
}

# ------------------------------------------------------------
#  PL 已配置的标记文件
#
#  为什么不用 DEVCFG_STATUS.PCFG_DONE 判断？实测证明它不可靠：
#    PL 未配置 -> 0x40000A30
#    PL 已配置 -> 0x40000F30
#    两者的 bit0/bit1 都是 0，所以 PCFG_DONE 根本不在这两位上。
#  而"读 AXI 会不会超时"这个实测结论是确定的，所以用实测来落标记：
#    bringup.tcl  fpga -f 成功后落标记
#    monitor.tcl  成功读到 GPIO 后落标记（自证）
#    rst -system  之前要先清除标记
# ------------------------------------------------------------
proc pl_marker {} {
    return [file join [file dirname [info script]] ".pl_configured"]
}
proc pl_mark_ok {} {
    if {[catch {
        set f [open [pl_marker] w]
        puts $f "PL configured at [clock format [clock seconds]]"
        close $f
    } e]} { return 0 }
    return 1
}
proc pl_mark_clear {} {
    catch {file delete [pl_marker]}
    return 1
}
proc pl_is_ok {} {
    return [file exists [pl_marker]]
}

# ------------------------------------------------------------
#  PL 是否真的配置好了
#
#  读 DEVCFG_STATUS(0xF8007014) —— 这是 PS 侧寄存器，任何状态下读都安全。
#  实测两态的差异：
#      未配置 0x40000A30   (bit8=0 bit10=0)
#      已配置 0x40000F30   (bit8=1 bit10=1)
#  注意 PCFG_DONE 不在 bit0/bit1（这两态都是 0），别用那个判断。
# ------------------------------------------------------------
proc pl_configured_p {} {
    if {[catch {set d [rd32 0xF8007014]} e]} { return -1 }
    return [expr {($d & 0x500) == 0x500}]
}

# ------------------------------------------------------------
#  确保 PL 已配置。未配置（或标记失效）就自动重下 bit 流。
#
#  为什么必须先确保：PL 未配置时对 AXI 地址做 mrd/mwr 会总线超时，
#  并把 A9 调试口锁死在 DAP 粘滞错误 0xF0000021，只能物理断电恢复。
#  而 fpga -f 走独立的 JTAG 配置口，不经 APB AP，任何状态下都安全。
# ------------------------------------------------------------
proc pl_ensure {{bitfile ""}} {
    if {$bitfile eq ""} {
        # 从脚本位置反推仓库根，别写死盘符
        set repo [file normalize [file join [file dirname [info script]] .. ..]]
        set bitfile "$repo/prj/7020.runs/impl_1/top.bit"
        if {![file exists $bitfile] && [file exists "$repo/artifacts/top.bit"]} {
            set bitfile "$repo/artifacts/top.bit"
        }
    }

    # --- 前置：确认 ps7_init 跑过。POR 之后 FCLK_CLK0 是停的，
    #     此时就算 PL 配好，AXI 也不会响应，一写就锁死 DAP。
    #     FPGA0_CLK_CTRL 的期望值来自 ps7_init.tcl 里的 mask_write。
    if {[catch {set ck [rd32 0xF8000170]} e]} {
        error "读 FPGA0_CLK_CTRL 失败，A9 可能已锁死（需断电重上电）: $e"
    }
    if {($ck & 0x03F03F30) != 0x00300600} {
        error [format "PS 时钟未初始化 (FPGA0_CLK_CTRL=0x%08X) -> 请先跑 bringup.tcl" $ck]
    }

    set c [pl_configured_p]
    if {$c == -1} {
        error "读 DEVCFG_STATUS 失败，A9 可能已锁死（需断电重上电）"
    }
    if {$c == 1 && [pl_is_ok]} { return 1 }      ;# 两者都说是 -> 直接过

    puts "  [pl] DEVCFG 判定未配置($c) 或 标记缺失 -> 自动重下 bit 流"
    sel_pl
    if {[catch {fpga -f $bitfile} e]} { error "fpga -f 失败: $e" }
    after 200
    sel_a9
    set c2 [pl_configured_p]
    puts "  [pl] 重下后 DEVCFG 判定 = $c2"
    pl_mark_ok
    return 1
}


