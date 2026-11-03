#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""radar_probe.py —— 测距传感器链路探针（两种取数后端）

判定四件事：帧率够不够、坏帧多不多、有效点率高不高、帧长对不对。
默认按 COIN-D6（dToF 激光雷达，AA 55 帧头，85 字节帧）解析；
换其它传感器只改 parse_frame()。

两个后端（判据完全相同，只是"字节从哪来"不同）：
  --port COM5           PC 侧，pyserial 读 CH340/USB 转接板
  --mmio 0x42C00000     板载 AXI UART Lite，PYNQ 直接 MMIO 读（无需 /dev/ttyUL0）

为什么要第二个后端：PYNQ 默认**不加载** uartlite 内核驱动，板子上根本没有
/dev/ttyUL0，pyserial 那条路走不通。此时用 --mmio 直接读寄存器，
判据一个不少（帧率/坏帧/有效点率/帧长）。

用法：
    python radar_probe.py --port COM5 --baud 230400 --secs 10
    python radar_probe.py --mmio 0x42C00000 --secs 12 --nominal-fps 170

退出码：全部判据通过 0，否则 1。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

HEAD = b"\xAA\x55"
STEP_DEG = 0.5
NOMINAL_FRAME = 85          # COIN-D6 标称帧长（含帧头）
MIN_RANGE, MAX_RANGE = 80, 60000     # mm
DROP_WEAK = True


# ---------------------------------------------------------------- 协议
def checksum(ct: int, n: int, body: bytes) -> int:
    cal = 0x55AA ^ (ct + n * 256)
    cal ^= int.from_bytes(body[0:2], "little")
    cal ^= int.from_bytes(body[2:4], "little")
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k: 9 + 3 * k]
        cal ^= s_l ^ (s_2 + s_h * 256)
    return cal


def parse_frame(ct: int, n: int, body: bytes):
    """返回 [(dist_mm | None), ...]；校验失败返回 None。换协议只改这里。"""
    if not (body[0] & 1) or not (body[2] & 1):
        return None
    cs = int.from_bytes(body[4:6], "little")
    if checksum(ct, n, body) != cs:
        return None
    pts = []
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k: 9 + 3 * k]
        dist = s_h * 64 + (s_2 >> 2)
        weak = bool(s_l & 0x01)
        ok = MIN_RANGE <= dist <= MAX_RANGE
        if ok and weak and DROP_WEAK:
            ok = False
        pts.append(dist if ok else None)
    return pts


def split_frames(buf: bytearray):
    """从缓冲区切出完整帧，产出 (frame_len, ct, n, body)。"""
    out = []
    while True:
        idx = buf.find(HEAD)
        if idx < 0:
            if len(buf) > 1:
                del buf[:-1]
            break
        if idx > 0:
            del buf[:idx]
        if len(buf) < 4:
            break
        ct, n = buf[2], buf[3]
        if n == 0 or n > 60 or ((ct & 1) == 1 and n != 1):
            del buf[:2]
            continue
        flen = 4 + 6 + n * 3
        if len(buf) < flen:
            break
        body = bytes(buf[4:flen])
        del buf[:flen]
        out.append((flen, ct, n, body))
    return out


# ---------------------------------------------------------------- 取数后端
class SerialReader:
    """PC 侧：pyserial 一次性读一大块。"""

    def __init__(self, port, baud):
        try:
            import serial
        except ImportError:
            sys.exit("缺 pyserial：pip install pyserial（或改用 --mmio）")
        try:
            self.ser = serial.Serial(port, baud, timeout=0.05)
        except Exception as e:
            sys.exit(f"打不开串口 {port}@{baud}：{e}\n"
                     f"（端口被占用、驱动没装、或线上没有设备）")
        self.desc = f"{port} @ {baud}"

    def read(self, want=4096):
        return self.ser.read(want)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class MMIOReader:
    """板载 AXI UART Lite：直接读寄存器，不需要 /dev/ttyUL0。

    寄存器布局：0x00 RX FIFO / 0x04 TX FIFO / 0x08 STAT。
    STAT 位：0x01 RX_VALID（有 1~15 字节）、0x02 RX_FULL（满 16 字节）。
    FIFO 只有 16 字节，230400 波特率下逐字节读会被追平溢出，
    所以"满就批量读 16、不满读 1"，与 ps_radar_fusion.py 的做法一致。
    """

    def __init__(self, addr, overlay=None):
        try:
            from pynq import MMIO, Overlay
        except ImportError:
            sys.exit("缺 pynq：--mmio 只能在板子的 PYNQ 环境里用")
        if overlay:
            Overlay(overlay)
        self.mm = MMIO(int(addr, 0), 0x10000)
        self.desc = f"AXI UART Lite @ {addr}"

    def read(self, want=4096):
        out = bytearray()
        while len(out) < want:
            stat = self.mm.read(0x08)
            if stat & 0x02:                    # RX_FULL = 16 字节
                for _ in range(16):
                    out.append(self.mm.read(0x00) & 0xFF)
            elif stat & 0x01:                  # RX_VALID = 1~15 字节
                out.append(self.mm.read(0x00) & 0xFF)
            else:
                break                          # 空了就返回，别读空 FIFO
        return bytes(out)

    def close(self):
        pass


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="测距传感器链路探针（串口 / MMIO 双后端）")
    ap.add_argument("--port", help="串口，如 COM5 或 /dev/ttyUSB0（与 --mmio 二选一）")
    ap.add_argument("--mmio", help="AXI UART Lite 基址，如 0x42C00000（PYNQ 板载）")
    ap.add_argument("--overlay", help="--mmio 时可选：先加载该 .bit")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("--min-fps-ratio", type=float, default=0.90,
                    help="合格帧率 = 标称 × 该比例；标称由 --nominal-fps 给")
    ap.add_argument("--nominal-fps", type=float, default=0.0,
                    help="标称帧率，0 表示只报实测不做判定")
    ap.add_argument("--max-bad-ratio", type=float, default=0.01)
    ap.add_argument("--min-valid-ratio", type=float, default=0.80)
    ap.add_argument("--json")
    args = ap.parse_args()

    if bool(args.port) == bool(args.mmio):
        sys.exit("--port 与 --mmio 必须且只能给一个")
    rd = (SerialReader(args.port, args.baud) if args.port
          else MMIOReader(args.mmio, args.overlay))

    buf = bytearray()
    n_frame = n_bad = 0
    n_points = n_valid = 0
    lens = {}
    t0 = time.time()
    print(f"读 {rd.desc}，持续 {args.secs}s …")

    try:
        while time.time() - t0 < args.secs:
            chunk = rd.read(4096)
            if chunk:
                buf += chunk
            elif args.mmio:
                time.sleep(0.0002)      # MMIO 空读要稍微让一下，否则空转烧 CPU
            for flen, ct, n, body in split_frames(buf):
                n_frame += 1
                lens[flen] = lens.get(flen, 0) + 1
                pts = parse_frame(ct, n, body)
                if pts is None:
                    n_bad += 1
                    continue
                n_points += len(pts)
                n_valid += sum(1 for p in pts if p is not None)
    except KeyboardInterrupt:
        pass
    finally:
        rd.close()

    el = max(time.time() - t0, 1e-6)
    fps = n_frame / el
    bad_ratio = n_bad / n_frame if n_frame else 1.0
    valid_ratio = n_valid / n_points if n_points else 0.0
    main_len = max(lens, key=lens.get) if lens else 0

    print("\n=== 实测 ===")
    print(f"  帧数 {n_frame}   帧率 {fps:.1f} 帧/s   坏帧 {n_bad}（{bad_ratio*100:.2f}%）")
    print(f"  有效点率 {valid_ratio*100:.1f}%   主导帧长 {main_len} B")
    if len(lens) > 1:
        print(f"  帧长分布 {dict(sorted(lens.items()))}（应只有一种）")

    fails = []
    infos = []
    if args.nominal_fps > 0:
        need = args.nominal_fps * args.min_fps_ratio
        if fps < need:
            fails.append(f"帧率 {fps:.1f} < 合格线 {need:.1f}（标称 {args.nominal_fps} 的 "
                         f"{args.min_fps_ratio:.0%}）→ 吞吐不够或解析卡住")
    if bad_ratio > args.max_bad_ratio:
        fails.append(f"坏帧率 {bad_ratio*100:.2f}% > {args.max_bad_ratio*100:.1f}% "
                     f"→ 串口丢字节，检查波特率与读取方式")
    if valid_ratio < args.min_valid_ratio:
        fails.append(f"有效点率 {valid_ratio*100:.1f}% < {args.min_valid_ratio*100:.0f}% "
                     f"→ 回波弱：镜头脏 / 被测面吸收 / 超量程 / 对着空气")
    # 帧长：判"主导长度是否等于标称"，不要求全表唯一。
    # COIN-D6 本来就会发多种包（n=1/23/24 等，长度 13/79/82），校验和都合法；
    # 早先这里判"len(lens) > 1 即失败"，在真机上必然误报，已改。
    if main_len and NOMINAL_FRAME and main_len != NOMINAL_FRAME:
        fails.append(f"主导帧长 {main_len} B ≠ 标称 {NOMINAL_FRAME} B → 协议版本不符")
    if len(lens) > 1:
        others = {k: v for k, v in sorted(lens.items()) if k != main_len}
        infos.append(f"另有 {len(others)} 种包长（{others}），"
                     f"占 {sum(others.values())/max(n_frame,1):.1%}——多包型传感器属正常")

    print("\n=== 判据 ===")
    for i in infos:
        print("  · " + i)
    if fails:
        for f in fails:
            print("  ✗ " + f)
    else:
        print("  ✓ 全部通过")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump({"fps": fps, "frames": n_frame, "bad": n_bad,
                       "bad_ratio": bad_ratio, "valid_ratio": valid_ratio,
                       "frame_len": main_len, "fails": fails},
                      fp, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")

    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
