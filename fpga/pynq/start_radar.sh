#!/bin/bash
# 雷达桥开机自启：加载 overlay + 持续读雷达写 GPIO
# 由 systemd radar.service 以 root 运行

export XILINX_XRT=/usr
cd /home/xilinx/radar

# 等 PYNQ 环境就绪（刚上电 /dev/fpga0 可能还没就绪）
for i in $(seq 1 30); do
    if [ -e /sys/class/fpga_manager/fpga0/firmware ]; then
        break
    fi
    sleep 1
done

# 2026-11-03 晚回退：--corridor 70 --flip-lr 1 会让正装的雷达左右反掉（实测一个劲右转），
# 退回 8:03 那版的 --corridor 60、不做左右互换。
exec /usr/local/share/pynq-venv/bin/python3 -u ps_radar_fusion.py motor.bit --corridor 60 --flip-lr 0 >> /home/xilinx/radar/radar_run.log 2>&1
