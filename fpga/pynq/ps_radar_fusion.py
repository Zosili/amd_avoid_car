# -*- coding: utf-8 -*-
"""
ps_radar_fusion.py —— PYNQ 侧：读 AXI UART Lite 雷达 -> 算前方最近障碍 -> 写 AXI GPIO

配合 motor.bit（含 axi_uartlite_0 @0x42C00000 + axi_gpio_0 @0x41200000）使用。

数据流（最终上车形态，电机归 PL 决策）：
    COIN-D6 --杜邦线--> 板子 J4(U9/U8) --AXI UART Lite--> 本脚本
        -> 解析 D6 帧 -> 前方 ±60° 最近障碍距离 + 左右方向
        -> 写 axi_gpio_0： [15:0]=距离mm  [23:16]=心跳  [25:24]=转向(0/1左转/2右转)
        -> PL motor_decision 第二路读它，和 UDP 视觉距离融合（取 min + 转向绕行）

用法（PYNQ jupyter / SSH）：
    python3 ps_radar_fusion.py motor.bit
    python3 ps_radar_fusion.py motor.bit --corridor 60 --avoid 0.50 --dry
    # 雷达倒置安装时必须带 --flip-lr 1（默认是 0＝正装）；正装就别带

依赖：
    - 队友的 uartlite.py（同目录，AXI UART Lite 的 pyserial 兼容驱动）
    - D6 协议解析（本文件内嵌，与 d6_protocol.py 对齐，零第三方依赖）
"""

import argparse
import os
import struct
import time
import numpy as np

# PYNQ 2.7 的 XRT 后端需要这个环境变量才能加载 libxrt_core.so。
# 本镜像 XRT 是 Debian 布局（库在 /usr/lib），所以指向 /usr。
os.environ.setdefault("XILINX_XRT", "/usr")

# ---------------- D6 协议常量（与 d6_protocol.py 对齐）----------------
PH = 0x55AA                 # 包头，参与校验
STEP_DEG = 0.5
ZERO_OFFSET_DEG = 0.0
MIN_RANGE = 80          # mm
MAX_RANGE = 60000       # mm
DROP_WEAK_ECHO = True


def _angle(raw):
    return (raw >> 1) / 64.0


def _checksum(ct, n, body):
    cal = PH ^ (ct + n * 256)
    cal ^= int.from_bytes(body[0:2], "little")
    cal ^= int.from_bytes(body[2:4], "little")
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k:9 + 3 * k]
        cal ^= s_l ^ (s_2 + s_h * 256)
    return cal


_rx_buf = bytearray()   # 接收缓冲区：批量读入，从里面切帧


def read_batch(mm):
    """高效读一批字节：读 STAT，FIFO 满则批量读 16 字节，否则读 1 字节。

    关键：AXI UART Lite 的 RX FIFO 只有 16 字节，雷达 230400 波特率数据很快。
    逐字节读（每字节查 STAT + 读 FIFO）吞吐只有 ~13K 字节/秒，跟不上 23K 数据率，
    FIFO 持续溢出丢字节。这里用 STAT 的 RX_FULL 位：FIFO 满(16字节)才批量读 16 字节，
    否则只读 1 字节，既不读空 FIFO（读空会出问题），又最大化吞吐。
    """
    out = bytearray()
    stat = mm.read(0x08)
    if stat & 0x02:            # RX_FULL：FIFO 满 16 字节
        for _ in range(16):
            out.append(mm.read(0x00) & 0xFF)
    elif stat & 0x01:          # RX_VALID：有 1~15 字节
        out.append(mm.read(0x00) & 0xFF)
    return bytes(out)


def read_frame(mm):
    """从缓冲区解析一帧 -> (ct, n, body) 或 None。"""
    global _rx_buf
    chunk = read_batch(mm)
    if chunk:
        _rx_buf += chunk
    while True:
        idx = _rx_buf.find(b"\xaa\x55")
        if idx < 0:
            if len(_rx_buf) > 1:
                del _rx_buf[:-1]       # 只留末尾 1 字节（可能是 0xAA 的一半）
            return None
        if idx > 0:
            del _rx_buf[:idx]          # 丢弃帧头前的垃圾字节
        if len(_rx_buf) < 4:
            return None                # 帧头后还没有 ct/n
        ct = _rx_buf[2]
        n = _rx_buf[3]
        if n == 0 or n > 60 or ((ct & 0x01) == 1 and n != 1):
            del _rx_buf[:2]            # 无效帧头，跳过 2 字节继续找
            continue
        frame_len = 4 + 6 + n * 3      # AA 55 CT N + body(6 + n*3)
        if len(_rx_buf) < frame_len:
            return None                # 帧还不完整，等下一批数据
        body = bytes(_rx_buf[4:frame_len])
        del _rx_buf[:frame_len]
        return ct, n, body


def parse_frame(ct, n, body):
    fsa = int.from_bytes(body[0:2], "little")
    lsa = int.from_bytes(body[2:4], "little")
    cs = int.from_bytes(body[4:6], "little")
    if not (fsa & 1) or not (lsa & 1):
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
        dist = s_h * 64 + (s_2 >> 2)
        weak = bool(s_l & 0x01)
        ang = (a0 + k * step + ZERO_OFFSET_DEG) % 360.0
        ok = MIN_RANGE <= dist <= MAX_RANGE
        if ok and weak and DROP_WEAK_ECHO:
            ok = False
        pts.append((ang, dist if ok else None))
    return pts


class RevAccum:
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
            if cur is None or dist < cur:
                self.acc[k] = dist
        return out

    def flush(self):
        ranges = [None] * self.nbins
        for k, d in self.acc.items():
            ranges[k] = d
        self.acc = {}
        return ranges


def front_obstacle(ranges, corridor_deg):
    """前方 ±corridor 内最近障碍 (距离mm, 角度)。无则 (None, None)。"""
    nbins = len(ranges)
    half = int(round(corridor_deg / 0.5))
    best_d, best_a = None, None
    for k in range(-half, half + 1):
        idx = k % nbins
        d = ranges[idx]
        if d is not None and (best_d is None or d < best_d):
            best_d = d
            best_a = idx * 0.5
    return best_d, best_a


def main():
    ap = argparse.ArgumentParser(description="PYNQ 侧雷达桥：AXI UART Lite -> AXI GPIO")
    ap.add_argument("bit", help="overlay 位流，如 motor.bit")
    ap.add_argument("--corridor", type=float, default=35.0, help="前方走廊半宽（度）")
    ap.add_argument("--avoid", type=float, default=0.50, help="避障触发距离（米，上限）")
    ap.add_argument("--avoid-min", type=float, default=0.15,
                    help="避障最小有效距离（米，下限）。雷达装车底朝下时地面回波"
                         "恒为离地高度(十几厘米)，低于该值一律忽略，避免误转向")
    ap.add_argument("--flip-lr", type=int, default=0, choices=[0, 1],
                    help="雷达倒置安装后左右互换：1=互换(倒置装)，0=不换(正装)。"
                         "★ 默认 0：雷达正装时若误开 1，转向会整体左右反掉"
                         "（实测表现：该左转却一个劲右转）。只有真把雷达翻过来装才设 1")
    ap.add_argument("--hz", type=float, default=10.0, help="写 GPIO 频率")
    ap.add_argument("--dry", action="store_true", help="只打印，不写 GPIO")
    args = ap.parse_args()

    # 延迟导入 pynq，避免无 pynq 环境时 import 就崩
    from pynq import Overlay, MMIO

    print("[1] 加载 overlay:", args.bit)
    ol = Overlay(args.bit)
    print("    IP:", list(ol.ip_dict.keys()))

    if "axi_uartlite_0" not in ol.ip_dict:
        raise RuntimeError("overlay 没有 axi_uartlite_0，检查 .hwh 是否同名、是否含该 IP")
    if "axi_gpio_0" not in ol.ip_dict:
        raise RuntimeError("overlay 没有 axi_gpio_0")

    # 直接 MMIO 读 AXI UART Lite（@0x42C00000），不用 uartlite.py 的封装
    # （它的逐字节 read 吞吐不够，会导致 FIFO 溢出丢字节）
    mm = MMIO(ol.ip_dict["axi_uartlite_0"]["phys_addr"], 0x10000)
    gpio = ol.axi_gpio_0

    acc = RevAccum()
    seq = 0
    f_ok = f_bad = 0
    last_d = None
    last_a = None
    t0 = time.time()
    last_stat = 0.0
    period = 1.0 / max(args.hz, 0.1)
    last_send = 0.0

    avoid_mm = int(args.avoid * 1000)
    avoid_min_mm = int(args.avoid_min * 1000)

    print("[2] 开始读雷达 ...")
    try:
        while True:
            now = time.time()
            fr = read_frame(mm)
            if fr is None:
                continue          # 帧还没凑齐，等下一批数据
            ct, n, body = fr
            pts = parse_frame(ct, n, body)
            if pts is None:
                f_bad += 1        # 校验失败 = 真坏帧
                continue
            f_ok += 1
            ranges = acc.feed(pts)
            if ranges is not None:
                last_d, last_a = front_obstacle(ranges, args.corridor)

            if last_d is not None and now - last_send >= period:
                seq = (seq + 1) & 0xFF
                # 转向方向：正装时"障碍在左半(0~180) -> 右转(2)，右半 -> 左转(1)"。
                # 必须落在 [avoid_min_mm, avoid_mm) 才算障碍：
                # 下限滤掉车底雷达打到地面的固定回波，否则会永远触发转向。
                # ★ 倒置安装（2026-11-03 晚）：雷达翻过来后扫描镜像，物理左半会
                #   报到 >=180 度，所以用 --flip-lr 1 把左右判定取反（等价 steer 1/2 互换）。
                steer = 0
                if avoid_min_mm <= last_d < avoid_mm:
                    in_left = (last_a % 360.0) < 180.0
                    if args.flip_lr:
                        in_left = not in_left
                    steer = 2 if in_left else 1
                word = ((steer & 0x3) << 24) | (seq << 16) | (last_d & 0xFFFF)
                if not args.dry:
                    gpio.write(0x0, word)
                last_send = now

            if now - last_stat >= 3.0:
                last_stat = now
                d_txt = "%.2fm" % (last_d / 1000.0) if last_d else "--"
                in_range = (last_d is not None and avoid_min_mm <= last_d < avoid_mm)
                in_left = (last_a % 360.0) < 180.0 if last_a is not None else False
                if args.flip_lr:
                    in_left = not in_left
                steer_txt = "右转" if (in_range and in_left) else \
                            "左转" if in_range else "直行"
                print("  帧OK %d 坏 %d  前方最近 %s @%.0f°  %s" %
                      (f_ok, f_bad, d_txt, last_a if last_a else 0.0, steer_txt))
    except KeyboardInterrupt:
        print("\n退出。清空 GPIO -> PL 看门狗判超时 SAFE")
        if not args.dry:
            gpio.write(0x0, 0xFFFF0000)


if __name__ == "__main__":
    main()
