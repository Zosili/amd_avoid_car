# -*- coding: utf-8 -*-
"""u16 字段 = f(角度)? 确定性测试"""
import collections, statistics

import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(_HERE_D6, "d6_capture.bin")
buf = open(CAP, "rb").read()
allpos = [i for i in range(len(buf) - 1) if buf[i] == 0xAA and buf[i + 1] == 0x55]
starts = [a for a, b in zip(allpos, allpos[1:]) if b - a == 85]
frames = [buf[p:p + 85] for p in starts]

WRAP = 46080
a = [int.from_bytes(f[4:6], "little") % WRAP for f in frames]
x = [int.from_bytes(f[8:10], "little") for f in frames]

# 1. 同角度 -> x3 是否确定
key2x = collections.defaultdict(list)
for ai, xi in zip(a, x):
    key2x[ai].append(xi)
multi = {k: v for k, v in key2x.items() if len(v) >= 5}
print(f"出现>=5次的相同角度值: {len(multi)} 个")
if multi:
    within = statistics.mean(statistics.pstdev(v) if len(v) > 1 else 0 for v in multi.values())
    overall = statistics.pstdev(x)
    print(f"同角度内 x3 标准差均值 = {within:.0f}, 总体标准差 = {overall:.0f}")
    print(f"总体变异系数口径: 组内/总体 = {within/overall:.2f}  (≈0 → 完全由角度决定; ≈1 → 与角度无关)")
    ex = sorted(multi.items())[:3]
    for k, v in ex:
        print(f"  角度raw={k} ({k/128:.1f}°): x3样本 {v[:6]}")

# 2. 线性拟合 x3 = m*角度 + c
n = len(a)
ma, mx = statistics.mean(a), statistics.mean(x)
cov = sum((ai - ma) * (xi - mx) for ai, xi in zip(a, x)) / n
va_ = statistics.pvariance(a)
m = cov / va_
c = mx - m * ma
r = cov / (statistics.pstdev(a) * statistics.pstdev(x))
print(f"\n线性拟合 x3 = {m:.3f} * 角raw + {c:.0f},  相关系数 r = {r:+.3f}")

# 3. 按 16 帧/圈 分组看: 每圈同一位置的 x3 是否相近（角度周期性）
per = collections.defaultdict(list)
for i, xi in enumerate(x):
    per[i % 16].append(xi)
print("\n按帧序 mod 16 分组的 x3 均值（若角度决定，应各不同且稳定）:")
grp_means = [statistics.mean(v) for _, v in sorted(per.items())]
grp_stds  = [statistics.pstdev(v) for _, v in sorted(per.items())]
print("  均值:", [f"{m0:.0f}" for m0 in grp_means])
print("  组内std:", [f"{s0:.0f}" for s0 in grp_stds])
print(f"  组间均值std={statistics.pstdev(grp_means):.0f} vs 组内std均值={statistics.mean(grp_stds):.0f}")

# 4. byte[9] 低熵 -> 是否等于 a_start 高字节的某种映射?
b9 = [f[9] for f in frames]
ahi = [ai >> 8 for ai in a]
cnt = 0
mapping = collections.defaultdict(collections.Counter)
for hi, lo9 in zip(ahi, b9):
    mapping[hi][lo9] += 1
top_hits = sum(c.most_common(1)[0][1] for c in mapping.values())
print(f"\nbyte9 众数命中(按 a_start 高字节分组): {top_hits}/{len(frames)}")
