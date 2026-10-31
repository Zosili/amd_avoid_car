# -*- coding: utf-8 -*-
"""用官方公式重解 d6_capture.bin：
样本: dist = S2*64 + (S1>>2) mm; inten = (S1&0x03)*64 + (S0>>2); exp_m = S0&1
校验: CS = 0x55AA ^ (CT|LSN<<8) ^ FSA_raw ^ LSA_raw ^ XOR(所有样本原始字节)
角度: (FSA>>1)/64 度, 回绕 46080; 段内 i 点角度插值
"""
import collections

import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(_HERE_D6, "d6_capture.bin")
buf = open(CAP, "rb").read()

pos = [i for i in range(len(buf) - 1) if buf[i] == 0xAA and buf[i + 1] == 0x55]
starts = [a for a, b in zip(pos, pos[1:]) if b - a == 85]
frames = [buf[p:p + 85] for p in starts]
print(f"帧数 {len(frames)}")

PH = 0x55AA
ok = bad = 0
dists, intens = [], []
sector = collections.defaultdict(list)
for f in frames:
    if f[2] != 0x00 or f[3] != 0x19:
        bad += 1
        continue
    fsa = int.from_bytes(f[4:6], "little")
    lsa = int.from_bytes(f[6:8], "little")
    cs = int.from_bytes(f[8:10], "little")
    chk = PH ^ int.from_bytes(f[2:4], "little") ^ fsa ^ lsa
    x = 0
    for k in range(25):
        s0, s1, s2 = f[10 + 3 * k], f[11 + 3 * k], f[12 + 3 * k]
        x ^= s0 ^ s1 ^ s2
    chk ^= x
    if chk == cs:
        ok += 1
    else:
        bad += 1
        continue
    a0 = (fsa >> 1) / 64.0
    for k in range(25):
        s0, s1, s2 = f[10 + 3 * k], f[11 + 3 * k], f[12 + 3 * k]
        d = s2 * 64 + (s1 >> 2)
        it = (s1 & 0x03) * 64 + (s0 >> 2)
        ang = (a0 + k * 0.9) % 360.0
        dists.append((ang, d))
        intens.append(it)

print(f"CS 校验: 通过 {ok} / 失败 {bad}")

valid = [(a, d) for a, d in dists if 50 <= d <= 12000]
print(f"有效点 {len(valid)}/{len(dists)} ({len(valid)/len(dists):.0%})")
print(f"有效距离范围: {min(d for _, d in valid)*1:.0f}~{max(d for _, d in valid)*1:.0f} mm")
print(f"强度范围: {min(intens)}~{max(intens)}")

def smin(lo, hi):
    v = [d for a, d in valid if lo <= a < hi]
    return min(v) / 1000 if v else None

print("\n30° 扇区最小距离(米)（正确解码后）:")
for lo in range(0, 360, 30):
    print(f"  {lo:3d}°~{lo+30:3d}°: {smin(lo, lo+30)}")
f_ = smin(345, 360) or smin(0, 15)
print(f"\nRadar JSON: front={f_} left={smin(30,90)} right={smin(270,330)}")
