# -*- coding: utf-8 -*-
"""
lidar_ld06.py -- 国科光芯 COIN-D6（LD06/LD19 协议族）解析器

为什么按 LD06 协议写：
    COIN-D6 是 12m dToF、4000 点/秒、ROS2 兼容的入门雷达。同价位同定位的
    国产雷达（LD06/LD19/STL-19P 这一族）都用同一套 47 字节帧：
        0x54 | 0x2C | 转速(2B) | 起始角(2B) | 12×(距离2B+置信度1B) | 结束角(2B) | 时间戳(2B) | CRC8
    如果实测抓不到包，用本文件的 --dump 模式看原始字节，不用改别的代码。

两个必须现场确认的点（代码里都有对应参数）：
    1) 波特率：这一族基本都是 230400 8N1，少数固件是 115200
    2) 角度方向：COIN-D6 默认顺时针，换算成我们约定的"逆时针为正"要取反

用法（三种）：
    # 1) 看有没有数据、波形对不对
    python lidar_ld06.py --port COM5 --dump

    # 2) 存一份扫描给融合算法回放
    python lidar_ld06.py --port COM5 --save scan.json

    # 3) 作为库被 detect_fusion_live.py 调用（见文件末尾 SerialLidar）
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np

# ---- 协议常量 -------------------------------------------------------------
PKT_HEAD = 0x54
PKT_LEN = 47
PKT_VER_LEN = 0x2C          # 0x2C = 44，表示后面还有 44 字节
POINTS_PER_PKT = 12

# CRC8 表，多项式 0x4D（这一族通吃）
_CRC_TABLE = [
    0x00, 0x4D, 0x9A, 0xD7, 0x79, 0x34, 0xE3, 0xAE, 0xF2, 0xBF, 0x68, 0x25, 0x8B, 0xC6, 0x11, 0x5C,
    0xA9, 0xE4, 0x33, 0x7E, 0xD0, 0x9D, 0x4A, 0x07, 0x5B, 0x16, 0xC1, 0x8C, 0x22, 0x6F, 0xB8, 0xF5,
    0x1F, 0x52, 0x85, 0xC8, 0x66, 0x2B, 0xFC, 0xB1, 0xED, 0xA0, 0x77, 0x3A, 0x94, 0xD9, 0x0E, 0x43,
    0xB6, 0xFB, 0x2C, 0x61, 0xCF, 0x82, 0x55, 0x18, 0x44, 0x09, 0xDE, 0x93, 0x3D, 0x70, 0xA7, 0xEA,
    0x3E, 0x73, 0xA4, 0xE9, 0x47, 0x0A, 0xDD, 0x90, 0xCC, 0x81, 0x56, 0x1B, 0xB5, 0xF8, 0x2F, 0x62,
    0x97, 0xDA, 0x0D, 0x40, 0xEE, 0xA3, 0x74, 0x39, 0x65, 0x28, 0xFF, 0xB2, 0x1C, 0x51, 0x86, 0xCB,
    0x21, 0x6C, 0xBB, 0xF6, 0x58, 0x15, 0xC2, 0x8F, 0xD3, 0x9E, 0x49, 0x04, 0xAA, 0xE7, 0x30, 0x7D,
    0x88, 0xC5, 0x12, 0x5F, 0xF1, 0xBC, 0x6B, 0x26, 0x7A, 0x37, 0xE0, 0xAD, 0x03, 0x4E, 0x99, 0xD4,
    0x7C, 0x31, 0xE6, 0xAB, 0x05, 0x48, 0x9F, 0xD2, 0x8E, 0xC3, 0x14, 0x59, 0xF7, 0xBA, 0x6D, 0x20,
    0xD5, 0x98, 0x4F, 0x02, 0xAC, 0xE1, 0x36, 0x7B, 0x27, 0x6A, 0xBD, 0xF0, 0x5E, 0x13, 0xC4, 0x89,
    0x63, 0x2E, 0xF9, 0xB4, 0x1A, 0x57, 0x80, 0xCD, 0x91, 0xDC, 0x0B, 0x46, 0xE8, 0xA5, 0x72, 0x3F,
    0xCA, 0x87, 0x50, 0x1D, 0xB3, 0xFE, 0x29, 0x64, 0x38, 0x75, 0xA2, 0xEF, 0x41, 0x0C, 0xDB, 0x96,
    0x42, 0x0F, 0xD8, 0x95, 0x3B, 0x76, 0xA1, 0xEC, 0xB0, 0xFD, 0x2A, 0x67, 0xC9, 0x84, 0x53, 0x1E,
    0xEB, 0xA6, 0x71, 0x3C, 0x92, 0xDF, 0x08, 0x45, 0x19, 0x54, 0x83, 0xCE, 0x60, 0x2D, 0xFA, 0xB7,
    0x5D, 0x10, 0xC7, 0x8A, 0x24, 0x69, 0xBE, 0xF3, 0xAF, 0xE2, 0x35, 0x78, 0xD6, 0x9B, 0x4C, 0x01,
    0xF4, 0xB9, 0x6E, 0x23, 0x8D, 0xC0, 0x17, 0x5A, 0x06, 0x4B, 0x9C, 0xD1, 0x7F, 0x32, 0xE5, 0xA8,
]


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c = _CRC_TABLE[(c ^ b) & 0xFF]
    return c


def parse_packet(pkt: bytes, angle_sign: int = -1) -> list[tuple[float, float, int]] | None:
    """解析一帧 47 字节。返回 [(角度deg, 距离m, 置信度), ...]，CRC 错则返回 None。"""
    if len(pkt) != PKT_LEN or pkt[0] != PKT_HEAD or pkt[1] != PKT_VER_LEN:
        return None
    if crc8(pkt[:PKT_LEN - 1]) != pkt[PKT_LEN - 1]:
        return None

    speed = struct.unpack_from("<H", pkt, 2)[0]          # deg/s
    fsa = struct.unpack_from("<H", pkt, 4)[0] / 100.0    # 起始角
    lsa = struct.unpack_from("<H", pkt, 42)[0] / 100.0   # 结束角
    # ts  = struct.unpack_from("<H", pkt, 44)[0]         # 毫秒，这里不用

    # 跨 0 度时结束角要补 360，否则插值方向反了
    diff = (lsa - fsa) % 360.0
    span = diff if diff < 180.0 else diff - 360.0        # 允许小幅反转

    out = []
    for i in range(POINTS_PER_PKT):
        off = 6 + i * 3
        dist_mm = struct.unpack_from("<H", pkt, off)[0]
        conf = pkt[off + 2]
        if dist_mm == 0 or conf == 0:                    # 无效点（吸光/超量程）
            continue
        ang = fsa + span * i / (POINTS_PER_PKT - 1)
        ang = (ang * angle_sign) % 360.0                 # 方向适配
        out.append((ang, dist_mm / 1000.0, conf))
    return out


def parse_stream(buf: bytes, angle_sign: int = -1):
    """从字节流里尽力切包。返回 (点列表, 消耗掉的字节数)。"""
    pts = []
    i = 0
    n = len(buf)
    while i + PKT_LEN <= n:
        if buf[i] != PKT_HEAD or buf[i + 1] != PKT_VER_LEN:
            i += 1                                       # 逐字节找包头
            continue
        p = parse_packet(buf[i:i + PKT_LEN], angle_sign)
        if p is None:
            i += 1
            continue
        pts.extend(p)
        i += PKT_LEN
    return pts, i


# ---------------------------------------------------------------------------
class SerialLidar:
    """把 COIN-D6 包装成 FusionEngine 要的 Scan(angles, ranges)。

    FusionEngine 只吃 (angles_deg, ranges_m) 两个 ndarray，角度单位是度、
    0~360，ranges 用 inf 表示无回波。这里把一圈的点按 0.5° 分箱取中位数，
    避免同一个角度有多个点导致后面取距离时抖动。
    """

    def __init__(self, port: str, baud: int = 230400, angle_sign: int = -1,
                 bin_deg: float = 0.5, timeout: float = 0.01):
        import serial                                    # pyserial
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.buf = b""
        self.angle_sign = angle_sign
        self.bin_deg = bin_deg
        self.nbins = int(round(360.0 / bin_deg))
        self.hz = 0.0
        self._t0 = time.monotonic()
        self._last_ang = None
        self._acc: dict[int, list[float]] = {}
        self._rev_start = time.monotonic()

    def poll(self):
        """读一批字节，返回 (angles, ranges) 或 None（还没凑满一圈）。"""
        chunk = self.ser.read(max(1, self.ser.in_waiting))
        if not chunk:
            return None
        self.buf += chunk
        pts, consumed = parse_stream(self.buf, self.angle_sign)
        self.buf = self.buf[consumed:]
        if len(self.buf) > 4096:                          # 防止解不出包时无界增长
            self.buf = self.buf[-PKT_LEN * 2:]

        for ang, dist, _conf in pts:
            if self._last_ang is not None and ang < self._last_ang - 180.0:
                self._last_ang = ang
                return self._flush()                      # 转过 360 了，出一圈
            self._last_ang = ang
            k = int(ang / self.bin_deg) % self.nbins
            self._acc.setdefault(k, []).append(dist)
        return None

    def _flush(self):
        angles = np.arange(self.nbins) * self.bin_deg
        ranges = np.full(self.nbins, np.inf)
        for k, ds in self._acc.items():
            ranges[k] = float(np.median(ds))              # 中位数，抗单点飞点
        self._acc = {}
        now = time.monotonic()
        dt = now - self._rev_start
        self._rev_start = now
        self.hz = 1.0 / dt if dt > 1e-6 else 0.0
        return angles, ranges

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="COIN-D6 / LD06 协议解析与自测")
    ap.add_argument("--port", help="COM5 / /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--angle-sign", type=int, default=-1, choices=[1, -1],
                    help="雷达角度方向；顺时针雷达填 -1 转成逆时针为正")
    ap.add_argument("--dump", action="store_true", help="打印原始字节与解析结果")
    ap.add_argument("--save", help="存一份扫描 json 给融合回放")
    ap.add_argument("--secs", type=float, default=5.0)
    a = ap.parse_args()

    if not a.port:
        ap.error("--port 必填，例如 --port COM5")

    lidar = SerialLidar(a.port, a.baud, a.angle_sign)
    t_end = time.monotonic() + a.secs
    n_rev = 0
    saved = None
    print(f"打开 {a.port} @{a.baud}，采 {a.secs}s ...")
    while time.monotonic() < t_end:
        rev = lidar.poll()
        if rev is None:
            continue
        angles, ranges = rev
        n_rev += 1
        valid = np.isfinite(ranges)
        nv = int(valid.sum())
        if a.dump:
            print(f"  第{n_rev}圈 {lidar.hz:5.1f}Hz  有效点 {nv:3d}  "
                  f"最近 {ranges[valid].min() if nv else float('nan'):.2f}m")
        if nv:
            saved = (angles, ranges)

    if n_rev == 0:
        print("\n一圈都没凑出来。排查顺序：")
        print("  1) 波特率对不对（先试 115200）")
        print("  2) 雷达是不是要发启动指令（部分型号上电直接转，不发命令）")
        print("  3) 有没有转（dToF 要转到一定转速才出数据）")
        print("  4) --dump 看有没有 0x54 0x2C 开头的字节")
        sys.exit(1)

    print(f"\n共 {n_rev} 圈，平均 {n_rev / a.secs:.1f}Hz")
    if a.save and saved is not None:
        angles, ranges = saved
        Path(a.save).write_text(json.dumps({
            "angles": angles.tolist(),
            "ranges": [None if not np.isfinite(r) else round(float(r), 3)
                       for r in ranges],
            "note": f"COIN-D6 @{a.port} {a.baud} angle_sign={a.angle_sign}",
        }), encoding="utf-8")
        print(f"已存 {a.save}")
    lidar.close()


if __name__ == "__main__":
    main()
