# 一键重建视觉链路 Vivado 工程并出 bit + 报告
#
# 用法（在仓库根目录）：
#   vivado -mode batch -source fpga/build/build_project.tcl -tclargs build_out
#
# 说明：
#   · 只用 fpga/src/vision 下的 RTL + 约束 + IP 配置(.xci) 重建，不依赖原工程中间产物
#   · 器件固定 xc7z020clg400-2（正点原子领航者 V2）
#   · 首次在新机器跑通后，把 Vivado 版本与日志摘要写回 fpga/build/README.md
#   · ⚠️ 本脚本尚未做完整回归（见 README「状态」一节）

set out_dir "build_out"
if {[llength $argv] > 0} { set out_dir [lindex $argv 0] }

set script_dir [file dirname [file normalize [info script]]]
set repo_root  [file normalize [file join $script_dir ".." ".."]]
set vis        [file join $repo_root "fpga" "src" "vision"]
set rpt_dir    [file join $out_dir "reports"]

puts "== repo_root : $repo_root"
puts "== vision src: $vis"
puts "== output    : $out_dir"

file mkdir $out_dir
file mkdir $rpt_dir

# ---------------------------------------------------------------- 工程
create_project -force ov5640_udp_pc [file join $out_dir "ov5640_udp_pc"] -part xc7z020clg400-2
set_property target_language Verilog [current_project]

# ---------------------------------------------------------------- 源文件
set rtl_files [concat \
    [glob -nocomplain [file join $vis "rtl" "*.v"]] \
    [glob -nocomplain [file join $vis "rtl" "*" "*.v"]] ]
if {[llength $rtl_files] == 0} {
    error "没找到 RTL：$vis/rtl/*.v"
}
add_files -norecurse $rtl_files
puts "== added [llength $rtl_files] RTL files"

set ip_files [glob -nocomplain [file join $vis "ip" "*.xci"]]
if {[llength $ip_files] > 0} {
    add_files -norecurse $ip_files
    puts "== added [llength $ip_files] IP files"
}

add_files -fileset constrs_1 -norecurse [file join $vis "constrs" "ov5640_udp_pc.xdc"]

# IP 输出产物（新版本 Vivado 一般自动生成；这里显式保证）
if {[llength $ip_files] > 0} {
    catch { generate_target all [get_files -quiet *.xci] }
}
update_compile_order -fileset sources_1

# ---------------------------------------------------------------- 顶层
set_property top ov5640_udp_pc [get_filesets sources_1]
set_property top_file [file join $vis "rtl" "ov5640_udp_pc.v"] [get_filesets sources_1]

# ---------------------------------------------------------------- 综合 / 实现
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    error "综合失败，看 $out_dir/ov5640_udp_pc/ov5640_udp_pc.runs/synth_1/runme.log"
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    error "实现失败，看 $out_dir/ov5640_udp_pc/ov5640_udp_pc.runs/impl_1/runme.log"
}

# ---------------------------------------------------------------- 报告（与 fpga/report 同名，便于对比）
open_run impl_1
report_utilization      -file [file join $rpt_dir "utilization_placed.rpt"]
report_timing_summary   -file [file join $rpt_dir "timing_summary_routed.rpt"]
report_drc              -file [file join $rpt_dir "drc_routed.rpt"]
report_clock_utilization -file [file join $rpt_dir "clock_utilization_routed.rpt"]
report_power            -file [file join $rpt_dir "power_routed.rpt"]

set bit [glob -nocomplain [file join $out_dir "ov5640_udp_pc" "ov5640_udp_pc.runs" "impl_1" "*.bit"]]
puts "== bitstream : $bit"
puts "== reports   : $rpt_dir"
puts "== 完成。请把 reports/*.rpt 覆盖到 fpga/report/ 并 commit。"
