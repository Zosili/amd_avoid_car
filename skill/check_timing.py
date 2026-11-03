#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_timing.py —— 把 Vivado 报告变成判据（只用标准库）

读 timing_summary_routed.rpt 与 utilization_placed.rpt，输出：
WNS / TNS / 违例端点、WHS、LUT/FF/BRAM/DSP/IOB 占用，以及一行 PASS/FAIL。

为什么要这个：人眼扫报告容易只看“All constraints met”那一行，
不看具体 WNS 剩多少、违例端点数是否为 0。串进构建脚本后，报告不合格直接拦下。

用法：
    python check_timing.py --reports ./fpga/report
    python check_timing.py --timing t.rpt --util u.rpt --min-wns 0.5

退出码：PASS 0，FAIL 1，报告缺失 2。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

UTIL_KEYS = [
    ("Slice LUTs", "LUT"),
    ("LUT as Logic", "LUT(logic)"),
    ("Slice Registers", "FF"),
    ("Register as Flip Flop", "FF(ff)"),
    ("Block RAM Tile", "BRAM"),
    ("DSPs", "DSP"),
    ("Bonded IOB", "IOB"),
    ("BUFGCTRL", "BUFG"),
]


def find_report(dirpath, *names):
    for n in names:
        p = os.path.join(dirpath, n)
        if os.path.exists(p):
            return p
    return None


def parse_timing(path):
    """从 timing summary 里抓 WNS/TNS/违例端点那一行。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    out = {"wns": None, "tns": None, "fail_ep": None,
           "whs": None, "ths": None, "fail_hold": None,
           "met": "All user specified timing constraints are met" in text}

    # 汇总表首行：数字序列 WNS TNS fail_ep total_ep WHS THS ...
    m = re.search(
        r"^\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)\s+"
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)",
        text, re.M)
    if m:
        g = m.groups()
        out.update(wns=float(g[0]), tns=float(g[1]), fail_ep=int(g[2]),
                   whs=float(g[4]), ths=float(g[5]), fail_hold=int(g[6]))
    return out


def parse_util(path):
    """取每个资源项在报告里出现的所有行，保留 total 最大的那一条
    （同一项在 summary 与详表里各出现一次，summary 那行 total 常为 0）。"""
    best = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            for key, label in UTIL_KEYS:
                if not re.match(r"^\|\s*" + re.escape(key) + r"\s*\|", line):
                    continue
                cells = [c.strip() for c in line.split("|")]
                nums = [c for c in cells if re.fullmatch(r"[\d.]+", c)]
                if len(nums) < 5:
                    continue
                try:
                    used, total, pctv = float(nums[0]), float(nums[3]), float(nums[-1])
                except ValueError:
                    continue
                if total <= 0:
                    continue
                if label not in best or total > best[label][1]:
                    best[label] = (used, total, pctv)
    return best


def main():
    ap = argparse.ArgumentParser(description="Vivado 报告判据化")
    ap.add_argument("--reports", help="报告目录（含 timing_summary_routed.rpt 与 utilization_placed.rpt）")
    ap.add_argument("--timing")
    ap.add_argument("--util")
    ap.add_argument("--min-wns", type=float, default=0.0,
                    help="要求的最小 WNS（ns），默认 0，即不违例即可")
    ap.add_argument("--max-lut", type=float, default=100.0, help="LUT 占用上限 %%")
    ap.add_argument("--json")
    args = ap.parse_args()

    tp, up = args.timing, args.util
    if args.reports:
        tp = tp or find_report(args.reports, "timing_summary_routed.rpt",
                               "top_timing_summary_routed.rpt")
        up = up or find_report(args.reports, "utilization_placed.rpt",
                               "top_utilization_placed.rpt")
    if not tp or not os.path.exists(tp):
        print("✗ 缺 timing_summary_routed.rpt（用 --timing 指定）", file=sys.stderr)
        return 2
    if not up or not os.path.exists(up):
        print("✗ 缺 utilization_placed.rpt（用 --util 指定）", file=sys.stderr)
        return 2

    t = parse_timing(tp)
    if t["wns"] is None:
        print("✗ timing 报告里找不到 WNS 汇总行 —— 可能实现未完成，报告是空的",
              file=sys.stderr)
        return 2
    u = parse_util(up)

    print("=== 时序 ===")
    print(f"  WNS {t['wns']:+.3f} ns   TNS {t['tns']:.3f} ns   违例端点 {t['fail_ep']}")
    print(f"  WHS {t['whs']:+.3f} ns   THS {t['ths']:.3f} ns   保持违例 {t['fail_hold']}")
    print(f"  约束状态：{'全部满足' if t['met'] else '未全部满足'}")

    print("\n=== 资源 ===")
    order = ["LUT", "FF", "BRAM", "DSP", "IOB", "BUFG"]
    for k in order:
        if k in u:
            used, total, pctv = u[k]
            print(f"  {k:5s} {used:>8g} / {total:<8g}  {pctv:5.2f}%")

    fails = []
    if t["wns"] < args.min_wns:
        fails.append(f"WNS {t['wns']:+.3f} < 要求 {args.min_wns:+.3f} ns")
    if t["fail_ep"] > 0:
        fails.append(f"建立时间违例端点 {t['fail_ep']} 个")
    if t["fail_hold"] > 0:
        fails.append(f"保持时间违例端点 {t['fail_hold']} 个")
    if not t["met"]:
        fails.append("Vivado 报约束未全部满足")
    if "LUT" in u and u["LUT"][2] > args.max_lut:
        fails.append(f"LUT 占用 {u['LUT'][2]:.2f}% > {args.max_lut:.0f}%")

    print("\n=== 结论 ===")
    if fails:
        for f in fails:
            print("  ✗ " + f)
        print("  FAIL")
    else:
        print(f"  ✓ 时序收敛，WNS 余量 {t['wns']:+.3f} ns；资源在限制内")
        print("  PASS")

    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"timing": t,
                       "util": {k: {"used": v[0], "total": v[1], "pct": v[2]}
                                for k, v in u.items()},
                       "fails": fails}, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")

    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
