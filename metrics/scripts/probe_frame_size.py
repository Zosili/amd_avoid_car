# -*- coding: utf-8 -*-
"""
帧长探针：判定 OV5640 当前输出模式
====================================
一帧的总字节数直接反映像素格式（640x480 下）：
    614400 -> RGB565，每像素 2 字节，正确
    307200 -> 每像素 1 字节（YUV422 / RAW），说明 I2C 配置没写全，解码必然花屏

切帧方法：同一帧的 UDP 包是背靠背的（间隔几十微秒），
帧与帧之间有一个帧间隔（30fps ≈ 33ms）。所以间隔 > 5ms 就算新的一帧。

用法：
    python probe_frame_size.py
    python probe_frame_size.py --frames 5
"""

from __future__ import annotations

import argparse
import socket
import time

BOARD_IP = "192.168.1.10"
PORT = 1234
GAP_S = 0.005          # 帧间隔阈值
RGB565 = 640 * 480 * 2  # 614400
MONO8 = 640 * 480       # 307200


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=BOARD_IP)
    ap.add_argument("--frames", type=int, default=5)
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
    s.bind(("0.0.0.0", PORT))
    s.settimeout(1.0)
    s.sendto(b"1", (a.ip, PORT))
    print(f"已发启动命令 -> {a.ip}:{PORT}")

    frames: list[int] = []
    cur = 0
    last = None
    idle_from = time.monotonic()

    while len(frames) < a.frames:
        try:
            d, _ = s.recvfrom(70000)
        except socket.timeout:
            if time.monotonic() - idle_from > 3.0:
                print("3 秒无新包，停止")
                break
            continue

        now = time.monotonic()
        if last is not None and (now - last) > GAP_S and cur > 0:
            frames.append(cur)
            cur = 0
            if len(frames) >= a.frames:
                break
        cur += len(d)
        last = now
        idle_from = now

    s.sendto(b"0", (a.ip, PORT))
    s.close()

    print(f"\n收到 {len(frames)} 帧完整边界：")
    for i, n in enumerate(frames, 1):
        tag = ""
        if n == RGB565:
            tag = "  <- RGB565，正确"
        elif n == MONO8:
            tag = "  <- 每像素 1 字节，I2C 配置不对（YUV/RAW），会花屏"
        print(f"  第{i}帧 {n} 字节{tag}")

    if frames:
        avg = sum(frames) / len(frames)
        print(f"\n平均 {avg:.0f} 字节/帧")
        if avg == RGB565:
            print("结论：格式正确，花屏不是格式问题")
        elif avg == MONO8:
            print("结论：传感器在 8 位模式，需要重新给 OV5640 上电/重配")
        else:
            print("结论：帧不完整（可能丢包或拼帧错位）")


if __name__ == "__main__":
    main()
