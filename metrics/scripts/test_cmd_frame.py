# -*- coding: utf-8 -*-
"""
距离帧链路测试（不跑摄像头、不跑 YOLO）
==========================================
直接按档位往板子发 4 字节距离帧，验证 PL 侧 motor_decision.v 的
收帧 -> 阈值决策 -> 电机/蜂鸣器 整条链路。

帧格式（与 motor_decision.v 对齐）：
    0xAA 0x55 + dist_mm(u16 大端)，0xFFFF = 无人/测不出

四档预期动作（对应 RTL 的 ST_CLEAR / ST_WARN / ST_STOP / ST_SAFE）：
    CLEAR  5.0m  (>3m)      -> 70% 前进，蜂鸣器静音
    WARN   2.0m  (1~3m)     -> 70% 前进，蜂鸣器间歇响 120ms/1s
    STOP   0.5m  (<=1m)     -> 占空比清零（停），蜂鸣器连续响
    SAFE   停发 >400ms      -> 停车 + 静音（看门狗）

注意：CLEAR / WARN 两档车是真的往前走的，**先把轮子架空**。
脚本最后不发停车帧，而是直接停发，让 PL 落到 SAFE（停车且不响），
所以跑完板子会安静下来，不需要再手动静音。

用法：
    python test_cmd_frame.py                 # 完整一轮（5s 倒计时后开始）
    python test_cmd_frame.py --hold 3        # 每档保持 3 秒
    python test_cmd_frame.py --ip 192.168.1.10 --port 5000
    python test_cmd_frame.py --now           # 跳过倒计时
    python test_cmd_frame.py --skip 3        # 跳过 STOP 档（不想听连续响时）
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

BOARD_IP = "192.168.1.10"
BOARD_PORT = 5000
SEND_HZ = 20.0


def frame(dist_mm: int) -> bytes:
    mm = max(0, min(0xFFFF, int(dist_mm)))
    return b"\xAA\x55" + mm.to_bytes(2, "big")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=BOARD_IP)
    ap.add_argument("--port", type=int, default=BOARD_PORT)
    ap.add_argument("--hold", type=float, default=4.0, help="每档保持秒数")
    ap.add_argument("--now", action="store_true", help="跳过起跑倒计时")
    ap.add_argument("--skip", type=int, default=0,
                    help="跳过前 N 档（1=只测 WARN/STOP/SAFE）")
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (a.ip, a.port)
    period = 1.0 / SEND_HZ

    steps = [
        (5000, "CLEAR  5.0m  >3m    期望: 70% 前进, 蜂鸣器静音"),
        (2000, "WARN   2.0m  1~3m   期望: 70% 前进, 蜂鸣器 120ms/1s"),
        (500,  "STOP   0.5m  <=1m   期望: 停车, 蜂鸣器连续响"),
    ]
    if a.skip:
        steps = steps[a.skip:]

    def pump(dist_mm: int, secs: float, label: str) -> None:
        print(f"[{label}]")
        print(f"  发 {dist_mm}mm，持续 {secs:.1f}s", flush=True)
        t_end = time.monotonic() + secs
        n = 0
        while time.monotonic() < t_end:
            sock.sendto(frame(dist_mm), addr)
            n += 1
            time.sleep(period)
        print(f"  已发 {n} 帧")

    print("=" * 52)
    print(f"  目标 {a.ip}:{a.port}   每档 {a.hold:.1f}s")
    print("  CLEAR/WARN 档电机会转 -> 请确认轮子已架空")
    print("=" * 52)

    if not a.now:
        for i in range(5, 0, -1):
            print(f"  {i}... (Ctrl+C 取消)", end="\r", flush=True)
            time.sleep(1.0)
        print(" " * 30, end="\r")

    try:
        for mm, label in steps:
            pump(mm, a.hold, label)

        # 停发：靠 400ms 看门狗落到 SAFE（停车 + 静音）
        print("[SAFE] 停发，等看门狗 0.8s   期望: 停车, 静音", flush=True)
        time.sleep(0.8)
        print("  看门狗应已触发，板子现在应为停车且不响")
    except KeyboardInterrupt:
        print("\n中断。停止发帧，板子将自动进入 SAFE（停车+静音）")
        time.sleep(0.8)
        sys.exit(130)

    print("完成。四档跑完，板子保持 SAFE 静音。")


if __name__ == "__main__":
    main()
