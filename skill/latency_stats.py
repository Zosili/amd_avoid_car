#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""latency_stats.py —— 控制环延迟统计（只用标准库）

从 JSONL 日志统计周期分位数、抖动、截止期未命中率。

关键约定（这两条是本脚本存在的原因）：
  1. 按文件独立统计，绝不跨文件拼时间轴 —— 不同 run 的 t 来自不同会话甚至不同机器时钟，
     拼起来会算出几百万毫秒的“间隔”
  2. 报未命中率，不只报均值 —— 均值 100 ms 但每 10 帧卡一次 500 ms 的环，均值看不出来

日志契约：每行一个 JSON，至少含 {"t": <unix 毫秒>}。
用法：
    python latency_stats.py logs/*.jsonl --period 100
    python latency_stats.py logs/ --period 100 --json out.json

退出码：有任何文件的未命中率超阈值则 1，否则 0。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def load_ts(path):
    ts = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  ! {os.path.basename(path)}:{ln} JSON 解析失败：{e}")
                continue
            if "t" not in obj:
                sys.exit(f"  {os.path.basename(path)}:{ln} 缺 t 字段（契约：t = unix 毫秒）")
            ts.append(float(obj["t"]))
    return ts


def analyze(path, period_ms, miss_factor):
    ts = load_ts(path)
    name = os.path.basename(path)
    if len(ts) < 3:
        print(f"  {name:40s} 记录太少（{len(ts)}），跳过")
        return None

    delta = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
    delta = [d for d in delta if d > 0]
    if not delta:
        print(f"  {name:40s} 时间戳无递增，跳过（可能是同一毫秒内的批记录）")
        return None

    miss_th = period_ms * miss_factor
    miss = sum(1 for d in delta if d > miss_th)
    jitter = [abs(d - period_ms) for d in delta]

    p50 = pct(delta, 50)
    res = {
        "file": name, "n": len(ts), "cycles": len(delta),
        "p50": p50, "p95": pct(delta, 95), "p99": pct(delta, 99),
        "max": max(delta), "min": min(delta),
        "jitter_p99": pct(jitter, 99),
        "miss": miss, "miss_ratio": miss / len(delta) * 100.0,
        "miss_threshold": miss_th,
    }
    flag = ""
    if period_ms > 0 and abs(p50 - period_ms) > period_ms * 0.25:
        flag = f"  ← P50 {p50:.1f} 偏离设定 {period_ms:.0f}，环可能没按预期跑"
    print(f"  {name:40s} n={res['n']:4d}  P50 {p50:6.1f}  P95 {res['p95']:6.1f}  "
          f"P99 {res['p99']:6.1f}  max {res['max']:7.1f}  "
          f"抖动P99 {res['jitter_p99']:5.1f}  未命中 {res['miss_ratio']:5.2f}%{flag}")
    return res


def main():
    ap = argparse.ArgumentParser(description="控制环延迟统计")
    ap.add_argument("paths", nargs="+", help="jsonl 文件或目录")
    ap.add_argument("--period", type=float, default=0.0, help="标称周期 ms，0=不判定")
    ap.add_argument("--miss-factor", type=float, default=1.10,
                    help="超过 周期×该系数 记未命中")
    ap.add_argument("--max-miss", type=float, default=1.0,
                    help="未命中率超过该百分比则退出码 1")
    ap.add_argument("--json")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            files.extend(sorted(glob.glob(os.path.join(p, "*.jsonl"))))
        else:
            files.extend(sorted(glob.glob(p)))
    if not files:
        sys.exit("没找到 jsonl 文件")

    print(f"标称周期 {args.period or '未设'} ms，未命中阈值 "
          f"{args.period * args.miss_factor if args.period else '—'} ms\n")
    results = []
    for f in files:
        r = analyze(f, args.period, args.miss_factor)
        if r:
            results.append(r)

    if not results:
        sys.exit("没有可统计的文件")

    all_miss = [r["miss_ratio"] for r in results]
    bad = [r for r in results if r["miss_ratio"] > args.max_miss]
    print(f"\n合计 {len(results)} 份  未命中率最高 {max(all_miss):.2f}%  "
          f"平均 {sum(all_miss)/len(all_miss):.2f}%")
    if bad:
        print(f"✗ {len(bad)} 份超过 {args.max_miss}% 阈值：")
        for r in bad:
            print(f"    {r['file']}  {r['miss_ratio']:.2f}%")
    else:
        print(f"✓ 全部在 {args.max_miss}% 阈值内")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")

    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
