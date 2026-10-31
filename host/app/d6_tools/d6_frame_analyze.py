# -*- coding: utf-8 -*-
"""D6 抓包深度分析：确定帧结构（AA 55 头长、点格式、角度位置）"""
import collections

import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(_HERE_D6, "d6_capture.bin")
buf = open(CAP, "rb").read()
print(f"总长 {len(buf)} 字节")

# 1. 找所有 AA 55 位置与间距
pos = [i for i in range(len(buf) - 1) if buf[i] == 0xAA and buf[i + 1] == 0x55]
gaps = collections.Counter(b - a for a, b in zip(pos, pos[1:]))
print(f"AA 55 出现 {len(pos)} 次")
print(f"间距分布 top8: {gaps.most_common(8)}")

# 2. 取最常见间距的完整帧，统计各字节位的稳定性
main_gap = gaps.most_common(1)[0][0]
frames = [buf[p:p + main_gap] for p in pos[:-1]
          if (lambda nxt: nxt - p == main_gap)(pos[pos.index(p) + 1] if p in pos else -1)] if False else None
frames = []
for a, b in zip(pos, pos[1:]):
    if b - a == main_gap:
        frames.append(buf[a:b])
print(f"\n标准帧数: {len(frames)}, 帧长 {main_gap}")

print("\n前 2 帧完整 hexdump:")
for f in frames[:2]:
    print(" ", f.hex(" "))

# 3. 逐字节稳定性分析（找常量位 vs 数据位）
if frames:
    print("\n逐字节统计（位置: 出现最多的值 -> 种类数）:")
    for i in range(main_gap):
        col = collections.Counter(f[i] for f in frames)
        v, c = col.most_common(1)[0]
        print(f"  [{i:3d}] 0x{v:02X} (x{c}/{len(frames)})  唯一值数={len(col)}", end="")
        print("   <-- 常量" if len(col) == 1 else "")

# 4. 假设: 头 11 字节 (AA 55 00 19 + 7B) + 25 点 × 3B；测两种点格式
print("\n===== 点格式测试（假设头=11B，点=3B）=====")
H = 11
for mode in ("dist|conf", "conf|dist"):
    ok = tot = 0
    confs = []
    for f in frames:
        for k in range(H, len(f) - 2, 3):
            a, b, c = f[k], f[k + 1], f[k + 2]
            d = a | (b << 8) if mode == "dist|conf" else (b | (c << 8))
            cf = c if mode == "dist|conf" else a
            tot += 1
            if 50 <= d <= 12000:
                ok += 1
                confs.append(cf)
    cm = collections.Counter(confs).most_common(5) if confs else []
    print(f"  {mode}: 距离合理率 {ok}/{tot} = {ok/tot:.1%}  置信度众数 {cm}")

# 5. 头部 7B 中找角度：测各种缩放，看帧间是否递增 ~22.5°/帧(16帧/圈)
print("\n===== 头部 7B 角度假设测试 =====")
for off in range(4, 11):
    vals = []
    for f in frames[:16]:
        if off + 2 <= main_gap:
            vals.append(f[off] | (f[off + 1] << 8))
    if len(vals) >= 4:
        diffs = [(vals[i + 1] - vals[i]) % 65536 for i in range(len(vals) - 1)]
        d360 = [(d * 360 / 65536) for d in diffs]
        print(f"  offset {off}: 前8帧值 {vals[:8]} -> 相邻差(360°口径) {[f'{x:.1f}' for x in d360[:7]]}")
