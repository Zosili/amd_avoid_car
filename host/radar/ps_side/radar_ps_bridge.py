#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_ps_bridge.py —— 板子 PS(Linux) 侧雷达桥（零第三方依赖）

用途（最终上车形态）：
    COIN-D6 ──PS USB 串口(/dev/ttyUSBx, 230400 8N1)──> 本脚本
        └─ 解析 D6 帧 → 按 0.5° 分箱 → 算前方走廊最近障碍
        └─ 写入 AXI GPIO 0x41200000 给 PL（[15:0]=距离mm, [23:16]=心跳）

设计约束（写死不能改）：
    1. 板子 Linux 没有网络、大概率没有 pip/pyserial。所以本脚本只用
       Python 标准库：直接 open('/dev/ttyUSBx') 逐字节读，不 import serial。
    2. 写寄存器走 /dev/mem + mmap（不依赖 devmem 这个外部二进制；mmap 失败
       才回退到 subprocess 调 devmem）。
    3. 语义与 PC 侧"距离帧"一致：**最近障碍**（不是最近的人）。
       融合规则在 PL 里做：两路都有效取 min，只有一路有效用那一路，都超时 SAFE。

位域约定（与 docs/interface.md §6 一致）：
    axi_gpio_0 @ 0x41200000
    [15:0]  最近障碍距离 mm，0xFFFF = 无回波
    [23:16] 心跳计数，每次刷新 +1（PL 用它判断数据新鲜度，防 PS 挂了还当新鲜）

用法：
    python3 radar_ps_bridge.py                      # 自动找 /dev/ttyUSB*
    python3 radar_ps_bridge.py --port /dev/ttyUSB0  # 手动指定串口
    python3 radar_ps_bridge.py --corridor 35        # 前方走廊半宽（度）
    python3 radar_ps_bridge.py --dry                # 只解析打印，不写寄存器（调试）
    python3 radar_ps_bridge.py --hz 10              # 写寄存器频率

测试（无板子也能跑，用 PC 假雷达喂串口）见 ps_side/README.md。
"""

import argparse
import os
import struct
import sys
import time

# ---------------- D6 协议常量（与 radar_pkg/radar/d6_protocol.py 对齐，别改） ----------------
PH = 0xA5                      # 异或校验初值
STEP_DEG = 0.5                 # 相邻点标称角度步进
ZERO_OFFSET_DEG = 0.0          # 角度零位补偿（标定时可调）
MIN_RANGE = 80                 # mm，小于视为无回波
MAX_RANGE = 60000              # mm，超出量程视为无回波
DROP_WEAK_ECHO = True          # 弱回波（bit0=1）按无回波处理

AXI_GPIO_ADDR = 0x41200000     # axi_gpio_0 数据寄存器（PL 侧已引出 gpio_out）
PAGE_SIZE = 0x1000


def _angle(raw):
    """角度 raw（小端 u16，低 bit 为 C 校验位）→ 度（官方公式 (FSA>>1)/64）"""
    return (raw >> 1) / 64.0


def _checksum(ct, n, body):
    """官方 SDK 双字节异或校验值"""
    cal = PH ^ (ct + n * 256)
    cal ^= int.from_bytes(body[0:2], "little")
    cal ^= int.from_bytes(body[2:4], "little")
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k:9 + 3 * k]
        cal ^= s_l ^ (s_2 + s_h * 256)
    return cal


def read_frame(fd):
    """同步 AA 55 读一帧 → (ct, n, body) 或 None。逐字节读，无 pyserial。"""
    # 找 AA
    while True:
        b = os.read(fd, 1)
        if not b:
            return None
        if b[0] == 0xAA:
            break
    # 找 55
    while True:
        b = os.read(fd, 1)
        if not b:
            return None
        if b[0] == 0x55:
            break
    rest = os.read(fd, 2)
    if len(rest) < 2:
        return None
    ct, n = rest[0], rest[1]
    if n == 0 or n > 60:
        return None
    if (ct & 0x01) == 1 and n != 1:
        return None
    body = os.read(fd, 6 + n * 3)
    if len(body) < 6 + n * 3:
        return None
    return ct, n, body


def parse_frame(ct, n, body):
    """→ [(角度°, 距离mm 或 None)]；校验失败 None。"""
    fsa = int.from_bytes(body[0:2], "little")
    lsa = int.from_bytes(body[2:4], "little")
    cs = int.from_bytes(body[4:6], "little")
    if not (fsa & 1) or not (lsa & 1):      # C 校验位固定为 1
        return None
    if _checksum(ct, n, body) != cs:
        return None
    a0, a1 = _angle(fsa), _angle(lsa)
    if n > 1:
        span = a1 - a0
        if span < 0 and a0 > 270.0 and a1 < 90.0:
            span += 360.0
        nominal = STEP_DEG * (n - 1)
        if not (0.5 * nominal <= span <= 1.5 * nominal):
            span = nominal
        step = span / (n - 1)
    else:
        step = 0.0
    pts = []
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k:9 + 3 * k]
        dist = s_h * 64 + (s_2 >> 2)         # 14bit，mm
        weak = bool(s_l & 0x01)
        ang = (a0 + k * step + ZERO_OFFSET_DEG) % 360.0
        ok = MIN_RANGE <= dist <= MAX_RANGE
        if ok and weak and DROP_WEAK_ECHO:
            ok = False
        pts.append((ang, dist if ok else None))
    return pts


class GpioMailbox:
    """通过 /dev/mem + mmap 写 AXI GPIO 数据寄存器（含 devmem 回退）。"""

    def __init__(self, addr=AXI_GPIO_ADDR, dry=False):
        self.addr = addr
        self.dry = dry
        self._mm = None
        self._devmem = False
        if dry:
            print("[邮箱] DRY 模式：只打印，不写寄存器")
            return
        try:
            base = addr & ~(PAGE_SIZE - 1)
            off = addr - base
            self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
            import mmap
            self._mm = mmap.mmap(self._fd, PAGE_SIZE,
                                 mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                                 offset=base)
            self._off = off
            print("[邮箱] /dev/mem + mmap 就绪 @0x%08X" % addr)
        except Exception as e:
            print("[邮箱] mmap 失败(%s)，回退到 devmem" % str(e)[:60])
            self._devmem = True

    def write(self, val):
        if self.dry:
            return
        if self._devmem:
            import subprocess
            subprocess.run(["devmem", "0x%08X" % self.addr, "32",
                            "%d" % val], check=False)
            return
        self._mm[self._off:self._off + 4] = struct.pack("<I", val & 0xFFFFFFFF)


class RevAccum:
    """按角度回绕切圈，0.5° 分箱，每箱取最小（前方避障要保守，取最近）。"""

    def __init__(self, bin_deg=0.5):
        self.bin_deg = float(bin_deg)
        self.nbins = int(round(360.0 / self.bin_deg))
        self.acc = {}
        self.last_raw = None

    def feed(self, pts):
        out = None
        for ang, dist in pts:
            if self.last_raw is not None and ang < self.last_raw - 180.0:
                out = self.flush()
            self.last_raw = ang
            if dist is None:
                continue
            k = int(ang / self.bin_deg) % self.nbins
            cur = self.acc.get(k)
            if cur is None or dist < cur:      # 取最近，保守
                self.acc[k] = dist
        return out

    def flush(self):
        ranges = [None] * self.nbins
        for k, d in self.acc.items():
            ranges[k] = d
        self.acc = {}
        return ranges


def front_nearest(ranges, corridor_deg):
    """前方走廊（车头左右 ±corridor_deg）内取最近距离 mm；无回波返回 None。"""
    nbins = len(ranges)
    half = int(round(corridor_deg / 0.5))
    best = None
    for k in range(-half, half + 1):
        d = ranges[k % nbins]
        if d is not None and (best is None or d < best):
            best = d
    return best


def find_port(preferred=None):
    if preferred:
        return preferred
    for i in range(4):
        p = "/dev/ttyUSB%d" % i
        if os.path.exists(p):
            return p
    for p in ("/dev/ttyS1", "/dev/ttyS0"):
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="PS 侧雷达桥：D6 → AXI GPIO 0x41200000")
    ap.add_argument("--port", default=None, help="串口，默认自动找 /dev/ttyUSB*")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--corridor", type=float, default=35.0, help="前方走廊半宽（度）")
    ap.add_argument("--hz", type=float, default=10.0, help="写寄存器频率")
    ap.add_argument("--dry", action="store_true", help="只解析打印，不写寄存器")
    ap.add_argument("--addr", default="0x41200000", help="AXI GPIO 地址")
    args = ap.parse_args()

    port = find_port(args.port)
    if not port:
        print("没找到雷达串口（/dev/ttyUSB*）。雷达插到 PS 的 USB 口了吗？")
        sys.exit(1)

    # 打开串口：termios 设 8N1，无 pyserial
    import termios
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    # 8N1，关硬件流控，关 modem 线
    attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                  | termios.ISTRIP | termios.INLCR | termios.IGNCR
                  | termios.ICRNL | termios.IXON)
    attrs[1] = 0
    attrs[2] &= ~termios.CSIZE
    attrs[2] |= termios.CS8
    attrs[2] &= ~(termios.PARENB | termios.PARODD)
    attrs[2] &= ~termios.CSTOPB
    attrs[3] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
    attrs[4] = termios.B230400 if args.baud == 230400 else termios.B115200
    attrs[5] = termios.B230400 if args.baud == 230400 else termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    # 清空收发缓冲
    termios.tcflush(fd, termios.TCIOFLUSH)
    print("[串口] %s @%d 8N1 已打开" % (port, args.baud))

    addr = int(args.addr, 16)
    mbox = GpioMailbox(addr, dry=args.dry)

    acc = RevAccum()
    seq = 0
    f_ok = f_bad = 0
    last_d = None
    t0 = time.time()
    last_stat = 0.0
    period = 1.0 / max(args.hz, 0.1)
    last_send = 0.0

    try:
        while True:
            fr = read_frame(fd)
            if fr is None:
                f_bad += 1
                continue
            ct, n, body = fr
            pts = parse_frame(ct, n, body)
            if pts is None:
                f_bad += 1
                continue
            f_ok += 1
            ranges = acc.feed(pts)
            if ranges is not None:
                last_d = front_nearest(ranges, args.corridor)

            now = time.time()
            if last_d is not None and now - last_send >= period:
                seq = (seq + 1) & 0xFF
                word = (seq << 16) | (last_d & 0xFFFF)
                mbox.write(word)
                last_send = now

            if now - last_stat >= 5.0:
                last_stat = now
                d_txt = "%.2f m" % (last_d / 1000.0) if last_d else "--"
                print("[%s] 帧OK %d 坏 %d  前方最近 %s  (seq=%d)"
                      % (time.strftime("%H:%M:%S"), f_ok, f_bad, d_txt, seq))

    except KeyboardInterrupt:
        print("\n退出。最后把心跳归零，PL 看门狗会判超时 → SAFE")
        # 清空 = 心跳停止 + 距离 0xFFFF，让 PL 看门狗接管
        mbox.write(0xFFFF0000)


if __name__ == "__main__":
    main()
