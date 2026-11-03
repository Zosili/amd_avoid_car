# ============================================================
#  status.tcl  --  只读体检：不掉程序、不写寄存器
#
#  !! 重要防呆 !!
#  PL 未配置时读 AXI 地址(0x40000000-0x7FFFFFFF)会总线超时，
#  并给 A9 调试口留下 DAP 粘滞错误 0xF0000021，之后所有 A9 访问
#  全部失效，只能物理断电才能恢复。
#  => 因此本脚本一律先查 DEVCFG_STATUS.PCFG_DONE，为 0 就跳过 AXI。
# ============================================================

source [file join [file dirname [info script]] common.tcl]

connect

puts "======== 1. JTAG target ========"
targets

puts ""
puts "======== 2. PS (A9) 可访问性 ========"
set st [a9_state]
puts "  A9 state: $st"

if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts ""
    puts "  !!! A9 调试口不可用（DAP 粘滞错误 / APB 无响应）"
    puts "  !!! 软件复位清不掉，需要【物理断电重上电】"
    puts "  !!! 常见诱因：对着未配置的 PL 地址做了 mrd"
    exit 0
}

# ---------- PL 是否配置（必须最先查） ----------
set pcfg_done 0
if {[catch {
    set d [rd32 0xF8007014]
    set pcfg_done [expr {($d >> 1) & 1}]
    puts ""
    puts "======== 3. PL 配置状态 ========"
    puts [format "  DEVCFG_STATUS = 0x%08X" $d]
    puts [format "    bit0(PCFG_INIT) = %d" [expr {($d >> 0) & 1}]]
    puts [format "    bit1(PCFG_DONE) = %d   <== 1 才代表 PL 已配置" $pcfg_done]
} e]} {
    puts "  读 DEVCFG 失败: $e"
}

# ---------- 启动模式 ----------
puts ""
puts "======== 4. 启动模式 (SLCR) ========"
if {[catch {
    set w [rd32 0xF8000024]
    puts [format "  BOOT_MODE_REG = 0x%08X   (低4位: 0=JTAG 1=QSPI 2=NOR 3=NAND 4=SD)" $w]
} e]} { puts "  读取失败: $e" }

# ---------- 电机 GPIO（仅当 PL 已配置） ----------
puts ""
puts "======== 5. 电机 AXI GPIO (0x41200000) ========"
if {!$pcfg_done} {
    puts "  跳过：PL 未配置，读 AXI 会超时并破坏 DAP 状态"
} elseif {[catch {
    set g  [rd32 0x41200000]
    set tr [rd32 0x41200004]
    puts [format "  GPIO_DATA = 0x%08X" $g]
    puts [format "  GPIO_TRI  = 0x%08X" $tr]
    puts [format "    duty_l = %d %%   duty_r = %d %%" [expr {$g & 0x7F}] [expr {($g >> 8) & 0x7F}]]
    puts [format "    dir_l  = %d      dir_r  = %d"   [expr {($g >> 16) & 1}] [expr {($g >> 17) & 1}]]
} e]} {
    puts "  AXI 访问失败：$e"
}

# ---------- 温度 ----------
puts ""
puts "======== 6. 片内温度 (XADC 0xF8007100) ========"
if {[catch {
    set raw [expr {[rd32 0xF8007100] & 0xFFFF}]
    set t   [expr {$raw * 503.975 / 65536.0 - 273.15}]
    puts [format "  raw=0x%04X   T=%.2f C" $raw $t]
    if {$t > 85.0} {
        puts "  !!! 超过 85 C，确实可能过热降频"
    } elseif {$t > 70.0} {
        puts "  ! 偏热但未到降频点"
    } else {
        puts "  温度正常，过热不成立"
    }
} e]} {
    puts "  读取失败：$e"
}

exit
