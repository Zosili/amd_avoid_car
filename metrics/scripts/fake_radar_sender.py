# -*- coding: utf-8 -*-
"""
假雷达发送端（UDP）
====================
模拟"雷达驱动"往 `detect_fusion_live.py --lidar udp` 喂扫描，
报文格式和将来 Linux 那边要发的一模一样：

    {"angles": [0,1,2,...359], "ranges": [nan, nan, 2.31, ...]}

整圈 1° 一格，只在一个窄扇区里有距离（其余 NaN = 没测到）。

用法：
    python fake_radar_sender.py                       # 0°、2.0m、不摆
    python fake_radar_sender.py --theta 30 --range 1.2
    python fake_radar_sender.py --sweep 25            # ±25° 左右摆，看窗口跟不跟得住

然后另一个窗口跑：
    python detect_fusion_live.py --lidar udp --lidar-port 15001
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15001)
    ap.add_argument("--theta", type=float, default=0.0, help="目标角度（度）")
    ap.add_argument("--range", type=float, default=2.0, help="目标距离（米）")
    ap.add_argument("--sweep", type=float, default=0.0, help="左右摆幅（度）")
    ap.add_argument("--width", type=float, default=2.0, help="目标扇区宽度（度，半宽）")
    ap.add_argument("--hz", type=float, default=10.0)
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    angles = np.arange(0.0, 360.0, 1.0)
    period = 1.0 / a.hz
    t0 = time.monotonic()
    print(f"往 {a.host}:{a.port} 发假扫描 @{a.hz:.0f}Hz  "
          f"theta={a.theta:+.1f}° r={a.range:.2f}m 摆动=±{a.sweep:.0f}°   Ctrl+C 退出")

    try:
        while True:
            t = time.monotonic() - t0
            th = a.theta + (a.sweep * math.sin(2 * math.pi * 0.15 * t)
                            if a.sweep else 0.0)
            ranges = np.full(angles.shape, np.nan, dtype=np.float32)
            d = np.abs(((angles - th + 180.0) % 360.0) - 180.0)
            ranges[d <= a.width] = a.range
            msg = json.dumps({
                "angles": angles.tolist(),
                "ranges": [None if not np.isfinite(v) else round(float(v), 3)
                           for v in ranges],
            })
            s.sendto(msg.encode(), (a.host, a.port))
            time.sleep(period)
    except KeyboardInterrupt:
        print("\n停止")


if __name__ == "__main__":
    main()
