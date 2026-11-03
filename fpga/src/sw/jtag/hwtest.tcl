# ============================================================
#  hwtest.tcl  --  beeper + motor hardware check (bypass FW, direct AXI GPIO)
#
#  Control word bitfield (aligned with top.v / motor_axi_top.v):
#    [6:0]  duty_l   left duty 0~100 (physically drives RIGHT wheel)
#    [14:8] duty_r   right duty 0~100 (physically drives LEFT wheel)
#    [16]   dir_l    0=fwd 1=rev (measured: dir=1 is forward)
#    [17]   dir_r    0=fwd 1=rev
#    [18]   beep     active buzzer, 1=sound (M14)
#    [19]   stop_lock hard clamp, 1=force PWM 0 (must be 0 to spin)
#
#  Sequence:
#    0. ensure PL configured (pl_ensure, may re-download bit)
#    1. halt core (prevent FW from overwriting)
#    2. beeper: beep=1 for HOLDMS ms -> off
#    3. motor: 70% fwd for HOLDMS -> reverse for HOLDMS -> stop
#    4. readback GPIO each step
#
#  Env vars: HOLDMS (default 2000), SKIP_MOTOR (default 0)
# ============================================================

source [file join [file dirname [info script]] common.tcl]

set HOLDMS 2000
if {[info exists ::env(HOLDMS)]} { set HOLDMS $::env(HOLDMS) }
set SKIP_MOTOR 0
if {[info exists ::env(SKIP_MOTOR)]} { set SKIP_MOTOR $::env(SKIP_MOTOR) }

connect
jtag_probe

set st [a9_state]
if {$st eq "DAP_ERR" || $st eq "SEL_FAIL"} {
    puts "!!! A9 not accessible. Power-cycle then run bringup.tcl"
    exit 1
}
puts "A9 state = $st"

pl_ensure

if {[catch {stop} e]} { puts "stop failed: $e" } else { puts "A9 halted" }

wr32 0x41200000 0x00000000
after 100
puts [format "init GPIO = 0x%08X" [rd32 0x41200000]]

# ---------- 1. beeper ----------
puts ""
puts "==== BEEPER test (beep=bit18) ===="
set w_beep 0x00040000
wr32 0x41200000 $w_beep
set g [rd32 0x41200000]
puts [format "write beep=1 -> GPIO = 0x%08X  beep=%d  (should SOUND for %d sec)" $g [expr {($g>>18)&1}] [expr {$HOLDMS/1000}]]
after $HOLDMS
wr32 0x41200000 0x00000000
puts "beep OFF"

# ---------- 2. motor ----------
if {$SKIP_MOTOR} {
    puts "SKIP_MOTOR=1 skip motor test"
    puts "HW TEST DONE"
    exit
}

puts ""
puts "==== MOTOR test (70% each side) ===="
set w_motor [motor_word 70 70 1 1]
wr32 0x41200000 $w_motor
set g [rd32 0x41200000]
puts [format "write motor -> GPIO = 0x%08X  duty_l=%d dir_l=%d duty_r=%d dir_r=%d stop_lock=%d" \
      $g [expr {$g & 0x7F}] [expr {($g>>16)&1}] [expr {($g>>8)&0x7F}] [expr {($g>>17)&1}] [expr {($g>>19)&1}]]
puts [format "wheels should go FORWARD for %d sec" [expr {$HOLDMS/1000}]]
after $HOLDMS

set w_rev [motor_word 70 70 0 0]
wr32 0x41200000 $w_rev
set g [rd32 0x41200000]
puts [format "write reverse -> GPIO = 0x%08X  (dir=0, should REVERSE for %d sec)" $g [expr {$HOLDMS/1000}]]
after $HOLDMS

wr32 0x41200000 0x00000000
puts "motor STOP"

puts ""
puts "########## HW TEST DONE ##########"
exit
