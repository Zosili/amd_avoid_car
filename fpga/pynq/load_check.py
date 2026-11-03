#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/xilinx/radar")
from pynq import Overlay

print("[1] 加载 overlay: motor.bit ...")
ol = Overlay("/home/xilinx/radar/motor.bit")
print("    IP:", list(ol.ip_dict.keys()))
for k, v in ol.ip_dict.items():
    if "axi" in k.lower() or "uart" in k.lower() or "gpio" in k.lower():
        print("    %s -> %s" % (k, v))
