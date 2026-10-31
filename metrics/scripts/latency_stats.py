#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""latency_stats.py — 从决策日志算控制周期统计量（3.2.4.4「实时控制」指标口径）

输入：metrics/logs/*.jsonl，每行一条决策记录
    {"t": <unix_ms>, "seq": 1, "rule": "R1", "why": "...",
     "control": {"action": "STOP", ...}}
输出：每份日志一行汇总（Markdown 表格）+ 可选 CSV

口径（写进 metrics/README.md，别改口径不改文件）：
  · 控制周期 = 相邻两条决策记录的时间差 Δt（毫秒），目标周期由 --target-period-ms 给出
  · P50/P95/P99/最大 均为 Δt 的分位数（线性插值，numpy 默认口径）
  · 截止期未命中 = Δt > 目标周期 × (1 + --miss-tolerance)，默认容忍 10%
  · 丢帧窗口     = Δt > 目标周期 × --gap-factor（默认 5 倍），视为调度停摆而非抖动
  · 只统计同一份日志内部，不跨文件拼时间轴

⚠️ 这是**板上调度/控制周期**的证据，不是端到端响应延迟：
   端到端延迟要从「传感器产生数据」到「执行器物理响应」，需要逻辑分析仪或
   板级打点（见 metrics/evidence/README.md）。两者不要混用。

用法：
    python metrics/scripts/latency_stats.py
    python metrics/scripts/latency_stats.py --logs metrics/logs --csv metrics/decision_period.csv
"""
import argparse
import glob
import json
import os
import sys

TARGET_MS = 100.0        # 决策循环 10Hz（board_main.py::DECISION_HZ）


def pct(sorted_vals, q):
    """线性插值分位数（与 numpy.percentile 默认口径一致）"""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def load(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    return recs


def analyze(path, target_ms, miss_tol, gap_factor):
    recs = load(path)
    ts = [r["t"] for r in recs if isinstance(r.get("t"), (int, float))]
    ts.sort()
    deltas = [b - a for a, b in zip(ts, ts[1:])]
    deltas_s = sorted(deltas)

    out = {
        "file": os.path.basename(path),
        "records": len(recs),
        "span_s": (ts[-1] - ts[0]) / 1000.0 if len(ts) > 1 else 0.0,
        "n_intervals": len(deltas),
    }

    miss_limit = target_ms * (1.0 + miss_tol)
    gaps = [d for d in deltas if d > target_ms * gap_factor]
    misses = [d for d in deltas if d > miss_limit]

    out.update({
        "p50": pct(deltas_s, 0.50),
        "p95": pct(deltas_s, 0.95),
        "p99": pct(deltas_s, 0.99),
        "max": deltas_s[-1] if deltas_s else float("nan"),
        "mean": (sum(deltas) / len(deltas)) if deltas else float("nan"),
        "miss": len(misses),
        "miss_rate": (100.0 * len(misses) / len(deltas)) if deltas else float("nan"),
        "gaps": len(gaps),
    })

    # 动作分布 / 规则分布（判"成功率"时要用的分子分母）
    act = {}
    rule = {}
    for r in recs:
        c = r.get("control") or {}
        a = c.get("action", "?")
        act[a] = act.get(a, 0) + 1
        k = r.get("rule", "?")
        rule[k] = rule.get(k, 0) + 1
    out["actions"] = act
    out["rules"] = rule
    return out


def fmt(v, nd=1):
    if v != v:      # nan
        return "-"
    return ("%." + str(nd) + "f") % v


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="决策周期统计（3.2.4.4 实时控制口径）")
    ap.add_argument("--logs", default=os.path.join(here, "..", "logs"))
    ap.add_argument("--target-period-ms", type=float, default=TARGET_MS)
    ap.add_argument("--miss-tolerance", type=float, default=0.10,
                    help="超过目标周期多少比例算未命中，默认 0.10")
    ap.add_argument("--gap-factor", type=float, default=5.0,
                    help="多少倍目标周期算调度停摆窗口，默认 5.0")
    ap.add_argument("--csv", default=None, help="把汇总另存为 CSV")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.logs, "*.jsonl")))
    if not files:
        print("没找到 %s/*.jsonl" % args.logs)
        return 2

    rows = [analyze(p, args.target_period_ms, args.miss_tolerance, args.gap_factor)
            for p in files]

    head = ("| 日志 | 记录数 | 时长(s) | 周期P50 | 周期P95 | 周期P99 | 周期max "
            "| 均值 | 未命中 | 未命中率 | 停摆窗口 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    print("目标周期 %.0f ms，未命中阈值 %.0f ms，停摆阈值 %.0f ms\n"
          % (args.target_period_ms,
             args.target_period_ms * (1 + args.miss_tolerance),
             args.target_period_ms * args.gap_factor))
    print(head)
    print(sep)
    for r in rows:
        print("| %s | %d | %s | %s | %s | %s | %s | %s | %d | %s%% | %d |" % (
            r["file"], r["records"], fmt(r["span_s"], 2),
            fmt(r["p50"]), fmt(r["p95"]), fmt(r["p99"]), fmt(r["max"]),
            fmt(r["mean"]), r["miss"], fmt(r["miss_rate"], 2), r["gaps"]))

    print("\n动作分布 / 触发规则（判闭环成功率时的分子分母）：\n")
    for r in rows:
        acts = " ".join("%s=%d" % (k, v) for k, v in sorted(r["actions"].items()))
        rules = " ".join("%s=%d" % (k, v) for k, v in sorted(r["rules"].items()))
        print("- **%s**  动作: %s | 规则: %s" % (r["file"], acts or "-", rules or "-"))

    if args.csv:
        cols = ["file", "records", "span_s", "p50", "p95", "p99", "max", "mean",
                "miss", "miss_rate", "gaps"]
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(cols) + "\n")
            for r in rows:
                f.write(",".join(str(round(r[c], 4) if isinstance(r[c], float) else r[c])
                                 for c in cols) + "\n")
        print("\nCSV 已写：%s" % args.csv)

    print("\n⚠️ 本表只覆盖「决策循环周期」，端到端响应延迟与任务闭环成功率需另测，"
          "见 metrics/README.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
