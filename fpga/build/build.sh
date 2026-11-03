#!/usr/bin/env bash
# =====================================================================
#  Rebuild the Vivado project + bitstream from source.
#
#  Requirements:
#    Vivado 2025.2 on PATH, or set VIVADO to the vivado executable.
#  Output:
#    fpga/src/prj/7020.runs/impl_1/top.bit
#  Then copy it over fpga/build/top.bit and fpga/pynq/motor.bit.
# =====================================================================
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../src/tcl"
VIVADO="${VIVADO:-vivado}"

echo "[1/2] create project ..."
"$VIVADO" -mode batch -source "$SRC/01_project.tcl"

echo "[2/2] synth + impl + bitstream ..."
"$VIVADO" -mode batch -source "$SRC/02_build.tcl"

echo
echo "[OK] bitstream: fpga/src/prj/7020.runs/impl_1/top.bit"
echo "     copy it to fpga/build/top.bit and fpga/pynq/motor.bit"
