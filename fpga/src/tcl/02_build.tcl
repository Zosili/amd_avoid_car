# =====================================================================
#  02_build.tcl —— 加入顶层 top.v，综合 + 实现 + 出 bitstream
# =====================================================================
set root [file normalize [file dirname [file dirname [info script]]]]

# ---------------------------------------------------------------------
# [环境依赖] vivado.bat 退出码补丁
# ---------------------------------------------------------------------
# 本机 Vivado 2025.2 每次退出都返回 0xC0000409（STATUS_STACK_BUFFER_OVERRUN，
# PowerShell 显示 -1073740791，Git Bash 显示 127），即使日志 0 error、dcp 已生成。
# Vivado 自己的 ISEWrap.js 判定 "ISEExitCode != 0" 就写 .vivado.error.rst，
# 于是每个 OOC IP run 都被标成失败，launch_runs 直接中断（synth_1 根本没启动）。
#
# 注意：往 PATH 里塞 shim 是无效的！rundef.js 第 12 行会无条件把
#   D:/2025.2/Vitis/bin;...;D:/2025.2/Vivado/bin;
# 重新拼到子进程 PATH 最前面，任何外部注入都会被它盖住。
# 唯一有效的办法是直接改启动器最后一行，见 tools\patch_vivado_bat.ps1。
# 别写死盘符：优先用 Vivado 自己导出的 XILINX_VIVADO，
# 退而求其次用当前可执行文件所在目录。
if {[info exists ::env(XILINX_VIVADO)]} {
    set vivado_bat [file join $::env(XILINX_VIVADO) bin vivado.bat]
} else {
    set vivado_bat [file join [file dirname [info nameofexecutable]] vivado.bat]
}
if {![file exists $vivado_bat]} {
    puts "== [warn] 找不到 $vivado_bat，跳过退出码补丁检查"
} else {
    set fh [open $vivado_bat r]
    set _vb [read $fh]
    close $fh
    if {![string match "*patched-2026-10-24*" $_vb]} {
        puts "!!! vivado.bat 未打退出码补丁，OOC run 会被误判失败。"
        puts "!!! 先执行: powershell -ExecutionPolicy Bypass -File $root/tools/patch_vivado_bat.ps1"
        exit 1
    }
    puts "== vivado.bat 退出码补丁: 已就位 ($vivado_bat)"
}

# 退出码被归一化后，真实错误只能靠日志判定，这里做统一扫描
# 白名单：顶层 synth_1 在 `source top.tcl` 之前一定会打印一条
#   ERROR: [Common 17-1257] Failed to create directory 'C'.
# 这是本机某个 Windows 盘符路径被拆坏产生的，综合照常进行且产物完整，
# 5 个 OOC IP run 都没有这条。不能让它把成功的构建判成失败。
proc scan_log {dir tag} {
    set log [file join $dir runme.log]
    if {![file exists $log]} {
        puts "   [format %-16s $tag] 缺少 runme.log"
        return 1
    }
    set fh [open $log r]
    set data [read $fh]
    close $fh

    set benign 0
    set lines [split $data "\n"]
    set real 0
    foreach ln $lines {
        if {[string match "ERROR:*" $ln]} {
            if {[string match "*17-1257*" $ln]} {
                incr benign
            } else {
                incr real
                if {$real <= 3} { puts "      > $ln" }
            }
        }
    }
    if {$benign} {
        puts "   [format %-16s $tag] 忽略 $benign 条已知无害 ERROR（17-1257 目录 'C'）"
    }
    if {$real} {
        puts "   [format %-16s $tag] !!! 日志含 $real 条真实 ERROR"
    } else {
        puts "   [format %-16s $tag] 日志 0 条真实 ERROR（OK）"
    }
    return $real
}

open_project $root/prj/7020.xpr
puts "== 打开工程: [current_project]"

# 顶层加入（01 建 BD 时 top.v 还不存在）
if {![llength [get_files -quiet $root/rtl/top.v]]} {
    add_files -norecurse -fileset sources_1 $root/rtl/top.v
}
set_property top top [current_fileset]
update_compile_order -fileset sources_1
puts "== 顶层已设为: [get_property top [current_fileset]]"

# 列出参与综合的源文件，便于核对
puts "===== 源文件 ====="
foreach f [get_files -of_objects [get_filesets sources_1]] {
    puts "   [file tail $f]"
}

# ---------------------------------------------------------------------
# 综合
# ---------------------------------------------------------------------
# 上一轮被误判为失败 / 被中途掐断的 run 会留下两种垃圾：
#   .vivado.begin.rst  —— 没有配对的 .vivado.end.rst，Vivado 会认为该 run
#                         "正在运行"，于是它一直排在队列里永远不启动
#                         （表现：runme.log 时间戳不更新、没有子进程、整个 wait 卡死）
#   .vivado.error.rst  —— 状态变成 synth_design ERROR，launch_runs 直接判失败
# 两个都必须在 launch_runs 之前清掉，否则 synth_1 连启动都不会发生。
foreach pat {.vivado.begin.rst .vivado.end.rst .vivado.error.rst} {
    foreach f [glob -nocomplain $root/prj/7020.runs/*/$pat] {
        file delete -force $f
    }
}
foreach f [glob -nocomplain $root/prj/7020.runs/*/.Vivado_*.queue.rst] {
    file delete -force $f
}
puts "== 已清理陈旧 run 标记"

# 判据用"有没有 __synthesis_is_complete__"而不是只看 STATUS：
# 被掐断的 run 状态可能是 Not started / Running，但产物是缺的。
set nreset 0
foreach r [get_runs -quiet] {
    set st  [get_property STATUS $r]
    set dir [get_property DIRECTORY $r]
    set done [file exists [file join $dir __synthesis_is_complete__]]
    if {!$done || [string match "*ERROR*" $st]} {
        puts "== 重置 run: [get_property NAME $r]  (status=$st, complete=$done)"
        if {[catch {reset_run [get_property NAME $r]} msg]} {
            puts "   重置失败: $msg"
        }
        incr nreset
    }
}
puts "== 共重置 $nreset 个 run"
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
set st [get_property STATUS [get_runs synth_1]]
puts "== synth_1 状态: $st"
if {[scan_log $root/prj/7020.runs/synth_1 "synth_1"] > 0} {
    puts "!!! 综合日志有 ERROR，见 $root/prj/7020.runs/synth_1/runme.log"
    close_project
    exit 1
}
if {![file exists $root/prj/7020.runs/synth_1/top.dcp]} {
    puts "!!! 综合未产出 top.dcp"
    close_project
    exit 1
}

open_run synth_1 -name synth_1
report_utilization -file $root/logs/util_synth.rpt
report_timing_summary -file $root/logs/timing_synth.rpt -delay_type max
puts "===== 综合后资源 ====="
puts [report_utilization -return_string -quiet]

# ---------------------------------------------------------------------
# 实现 + bitstream
# ---------------------------------------------------------------------
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
puts "== impl_1 状态: $st"
if {[scan_log $root/prj/7020.runs/impl_1 "impl_1"] > 0} {
    puts "!!! 实现日志有 ERROR，见 $root/prj/7020.runs/impl_1/runme.log"
    close_project
    exit 1
}
if {![llength [glob -nocomplain $root/prj/7020.runs/impl_1/*.bit]]} {
    puts "!!! 实现未产出 bitstream，见 $root/prj/7020.runs/impl_1/runme.log"
    close_project
    exit 1
}

open_run impl_1
report_utilization  -file $root/logs/util_impl.rpt
report_timing_summary -file $root/logs/timing_impl.rpt -delay_type max
report_drc          -file $root/logs/drc.rpt

puts "===== WNS / WHS ====="
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1 -nworst 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1 -nworst 1]]
puts "   WNS = $wns ns"
puts "   WHS = $whs ns"

puts "===== 实现后资源 ====="
puts [report_utilization -return_string -quiet]

set bit [glob -nocomplain $root/prj/7020.runs/impl_1/*.bit]
puts "== bitstream: $bit"

# 顺便导出 xsa，Vitis 建平台时直接要用
set xsa $root/prj/7020.xsa
write_hw_platform -fixed -include_bit -force $xsa
puts "== XSA: $xsa"

close_project
puts "== 02_build.tcl 完成"
