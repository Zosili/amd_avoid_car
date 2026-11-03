# insert_ila.tcl
# Runs at STEPS.OPT_DESIGN.TCL.PRE on impl_1 (post-synthesis netlist).
# Clock net: the RX clock net is named u_decision/eth_txc in the netlist
# (verified: get_clocks -of it returns eth_rxc, 125MHz).
#
# probe0, 26 bits, MSB -> LSB:
#   [25]     udp_rec_pkt_done
#   [24]     udp_rec_en
#   [23:16]  udp_rec_data[7:0]
#   [15:0]   u_decision/dist_mm_reg_n_0_[15:0]
#
# trigger for capture: (1 << 24) -> first byte of any UDP payload

create_debug_core u_ila_0 ila

set_property C_DATA_DEPTH        4096   [get_debug_cores u_ila_0]
set_property C_TRIGIN_EN         false  [get_debug_cores u_ila_0]
set_property C_TRIGOUT_EN        false  [get_debug_cores u_ila_0]
set_property C_ADV_TRIGGER       false  [get_debug_cores u_ila_0]
set_property C_INPUT_PIPE_STAGES 0      [get_debug_cores u_ila_0]
set_property C_EN_STRG_QUAL      false  [get_debug_cores u_ila_0]
set_property ALL_PROBE_SAME_MU   true   [get_debug_cores u_ila_0]

connect_debug_port u_ila_0/clk [get_nets u_decision/eth_txc]

set plist {}
lappend plist [get_nets udp_rec_pkt_done]
lappend plist [get_nets udp_rec_en]
foreach i {7 6 5 4 3 2 1 0} {
    lappend plist [get_nets "udp_rec_data\[$i\]"]
}
foreach i {15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0} {
    lappend plist [get_nets "u_decision/dist_mm_reg_n_0_\[$i\]"]
}

set_property port_width 26 [get_debug_ports u_ila_0/probe0]
connect_debug_port u_ila_0/probe0 $plist
