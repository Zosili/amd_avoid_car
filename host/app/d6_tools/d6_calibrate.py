# -*- coding: utf-8 -*-
"""D6 帧格式定标：角度字段标定 + 每圈点数自相关 + 校验字段猜测"""
import collections, math

import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(_HERE_D6, "d6_capture.bin")
buf = open(CAP, "rb").read()

pos = [i for i in range(len(buf) - 1) if buf[i] == 0xAA and buf[i + 1] == 0x55]
# 只保留间距 85 的帧序列（连续无丢帧的一段段）
frames = []
pairs = [(a, b) for a, b in zip(pos, pos[1:]) if b - a == 85]
# 重建帧内容：帧 i 从 a 开始 85 字节
starts = [a for a, b in pairs] + [pos[-1]]
for s in starts:
    frames.append(buf[s:s + 85])
frames = [f for f in frames if len(f) == 85]
print(f"帧数 {len(frames)}")

a_start = [f[4] | (f[5] << 8) for f in frames]
a_end   = [f[6] | (f[7] << 8) for f in frames]
x3      = [f[8] | (f[9] << 8) for f in frames]

def delta_hist(vals, name):
    d = collections.Counter((vals[i + 1] - vals[i]) % 65536 for i in range(len(vals) - 1))
    print(f"{name} 相邻帧差 top6: {d.most_common(6)}")

delta_hist(a_start, "头[4:6]")
delta_hist(a_end,   "头[6:8]")
delta_hist(x3,      "头[8:10]")

# 三角形自相关找每圈点数
dists = []
for f in frames:
    for k in range(10, 85, 3):
        dists.append(f[k + 1] | (f[k + 2] << 8))
print(f"\n点总数 {len(dists)}, 平均距离 {sum(dists)/len(dists):.0f}mm, 最小 {min(dists)}, 最大 {max(dists)}")

N = len(dists)
mean = sum(dists) / N
var = sum((d - mean) ** 2 for d in dists) / N
print("自相关峰（lag 350~650）:")
best = []
for lag in range(350, 651):
    s = sum((dists[i] - mean) * (dists[i + lag] - mean) for i in range(0, N - lag, 7))
    r = s / ((N - lag) / 7 * var)
    best.append((r, lag))
best.sort(reverse=True)
for r, lag in best[:5]:
    print(f"  lag={lag}  r={r:.3f}")

# 距离-角度连续性: 假设角度=raw*scale, 看哪种 scale 让 25 点/帧的角步进 = 360/pts_per_rev
print("\n头部三种 u16 的前 12 帧值:")
print("  [4:6] ", a_start[:12])
print("  [6:8] ", a_end[:12])
print("  [8:10]", x3[:12])

# 校验字段猜测：头[8:10] 与各部分求和/异或的相关性
for name, calc in [
    ("sum(头6B)", lambda f: sum(f[4:10])),
    ("sum(头4B)", lambda f: sum(f[2:10])),
    ("xor(头6B)", lambda f: __import__("functools").reduce(lambda a, b: a ^ b, f[4:10])),
]:
    match = sum(1 for f in frames if calc(f) == (f[8] | (f[9] << 8)))
    print(f"头[8:10] == {name}: {match}/{len(frames)}")
