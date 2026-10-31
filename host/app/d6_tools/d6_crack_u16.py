# -*- coding: utf-8 -*-
"""暴力破解 D6 帧头 offset[8:10] 的未知 u16 字段"""
import collections, functools

import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(_HERE_D6, "d6_capture.bin")
buf = open(CAP, "rb").read()

# 用间距法提取帧（d6_calibrate.py 已验证可行）
SYNC = bytes([0xAA, 0x55])
allpos = [i for i in range(len(buf) - 1) if buf[i] == 0xAA and buf[i + 1] == 0x55]
clean = [a for a, b in zip(allpos, allpos[1:]) if b - a == 85]
frames = [buf[p:p + 85] for p in clean]
print(f"同步帧数: {len(frames)}")

x = [f[8] | (f[9] << 8) for f in frames]

def crc16(data, poly, init, refin, refout, xorout):
    crc = init
    for b in data:
        if refin:
            b = int(f"{b:08b}"[::-1], 2)
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    if refout:
        crc = int(f"{crc:016b}"[::-1], 2)
    return crc ^ xorout

VARIANTS = {
    "CRC16/XMODEM":     (0x1021, 0x0000, False, False, 0x0000),
    "CRC16/CCITT-FALSE":(0x1021, 0xFFFF, False, False, 0x0000),
    "CRC16/KERMIT":     (0x1021, 0x0000, True, True, 0x0000),
    "CRC16/ARC":        (0x8005, 0x0000, True, True, 0x0000),
    "CRC16/MODBUS":     (0x8005, 0xFFFF, True, True, 0x0000),
    "CRC16/X25":        (0x1021, 0xFFFF, True, True, 0xFFFF),
    "CRC16/DNP":        (0x3D65, 0x0000, True, True, 0xFFFF),
}

regions = {
    "a_start(4:6)":   lambda f: f[4:6],
    "a_end(6:8)":     lambda f: f[6:8],
    "角(4:8)":        lambda f: f[4:8],
    "头[2:8]":        lambda f: f[2:8],
    "点区(10:85)":    lambda f: f[10:85],
    "整帧除u16":      lambda f: f[0:8] + f[10:85],
    "整帧后2B含同步": lambda f: f[2:8] + f[10:85],
}

print("\n===== 校验假设测试（匹配率）=====")
results = []
for rname, fn in regions.items():
    for vname, (poly, init, ri, ro, xo) in VARIANTS.items():
        m = sum(1 for f in frames if crc16(fn(f), poly, init, ri, ro, xo) == (f[8] | (f[9] << 8)))
        results.append((m, f"CRC {vname} over {rname}"))
    for sname, sf in [("sum", lambda d: sum(d) & 0xFFFF),
                      ("~sum", lambda d: (~sum(d)) & 0xFFFF),
                      ("xor16", lambda d: functools.reduce(lambda a, b: a ^ (b << 8 if i % 2 == 0 else b), [(d[i]) for i in range(len(d))] ) if False else None)]:
        pass
    m1 = sum(1 for f in frames if (sum(fn(f)) & 0xFFFF) == (f[8] | (f[9] << 8)))
    results.append((m1, f"sum16 over {rname}"))
    m2 = sum(1 for f in frames if ((~sum(fn(f))) & 0xFFFF) == (f[8] | (f[9] << 8)))
    results.append((m2, f"~sum16 over {rname}"))
    m3 = sum(1 for f in frames if (functools.reduce(lambda a, b: a ^ b, fn(f), 0) | (functools.reduce(lambda a, b: a ^ b, fn(f), 0) << 8) & 0xFFFF) == (f[8] | (f[9] << 8)))
    results.append((m3, f"xor8^2 over {rname}"))

results.sort(reverse=True)
for m, name in results[:10]:
    rate = m / len(frames)
    flag = "  <<<< 破解!" if rate > 0.95 else ""
    print(f"  {m}/{len(frames)} ({rate:.1%})  {name}{flag}")

# 前一帧关系测试
print("\n===== 跨帧关系 =====")
m = sum(1 for a, b in zip(frames, frames[1:]) if (sum(a[10:85]) & 0xFFFF) == (b[8] | (b[9] << 8)))
print(f"x3[i] == sum16(点区[i-1]): {m}/{len(frames)-1}")
m = sum(1 for a, b in zip(frames, frames[1:]) if (sum(a) & 0xFFFF) == (b[8] | (b[9] << 8)))
print(f"x3[i] == sum16(全帧[i-1]): {m}/{len(frames)-1}")

# 周期性: 自相关 lag 1..20
print("\n===== x3 周期性（自相关）=====")
import statistics
n = len(x)
mu = statistics.mean(x)
va = statistics.pvariance(x)
for lag in [1, 2, 4, 8, 16, 17, 32]:
    s = sum((x[i] - mu) * (x[i + lag] - mu) for i in range(0, n - lag, 5))
    r = s / ((n - lag) / 5 * va)
    print(f"  lag={lag:3d}: r={r:+.3f}")

# 两字节分开看
b8 = collections.Counter(f[8] for f in frames)
b9 = collections.Counter(f[9] for f in frames)
print(f"\nbyte[8]: 唯一值 {len(b8)}, 最高频 {b8.most_common(3)}")
print(f"byte[9]: 唯一值 {len(b9)}, 最高频 {b9.most_common(3)}")
# byte9 是低概率值域? 看分布分位
vals9 = sorted(f[9] for f in frames)
print(f"byte[9] 分位: p5={vals9[len(vals9)//20]} p50={vals9[len(vals9)//2]} p95={vals9[-len(vals9)//20]}")
vals8 = sorted(f[8] for f in frames)
print(f"byte[8] 分位: p5={vals8[len(vals8)//20]} p50={vals8[len(vals8)//2]} p95={vals8[-len(vals8)//20]}")
