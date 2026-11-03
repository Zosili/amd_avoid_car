# -*- coding: utf-8 -*-
"""
============================================================
 训练结果评估 / 两模型对比
============================================================

为什么不能直接比 results.csv
----------------------------------------------------------------
两次训练各自在**自己的** val 上算指标：
    A 组（living ，无补标）  val = 2098 张，person 21 框
    B 组（livingB，有补标）  val = 2098 + 141 张，person 180 框
分母都不一样，日志里的 mAP 不可比。正确做法是把两个权重放到**同一个
val 集**上重跑一遍，本脚本就干这件事。

用法
----------------------------------------------------------------
    # 单独评估一个权重
    python tools/eval_report.py --weights runs/living_yolo11n/weights/best.pt \
        --data datasets/livingB/data.yaml --out runs/eval_A.json

    # 两个 json 对比（并按类打印差异）
    python tools/eval_report.py --compare runs/eval_A.json runs/eval_B.json

作者：欧阳博怀    日期：2026-10-29
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import CLASS_NAMES  # noqa: E402


def run_eval(args) -> int:
    os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ.pop("PYTHONPATH", None)
    from ultralytics import YOLO

    w = Path(args.weights)
    if not w.exists():
        print(f"[!] 权重不存在: {w}")
        return 1
    print(f"权重: {w}")
    print(f"数据: {args.data}  split={args.split}")
    model = YOLO(str(w))
    m = model.val(data=args.data, split=args.split, imgsz=args.imgsz,
                  batch=args.batch, device=args.device, plots=False,
                  verbose=False, workers=0)

    box = m.box
    idx = list(box.ap_class_index)
    names = m.names
    per = []
    for k, cid in enumerate(idx):
        per.append({
            "id": int(cid),
            "name": names.get(int(cid), CLASS_NAMES[int(cid)] if int(cid) < len(CLASS_NAMES) else str(cid)),
            "P": float(box.p[k]),
            "R": float(box.r[k]),
            "mAP50": float(box.ap50[k]),
            "mAP50_95": float(box.ap[k]),
        })
    out = {
        "weights": str(w),
        "data": str(args.data),
        "split": args.split,
        "overall": {
            "P": float(box.mp), "R": float(box.mr),
            "mAP50": float(box.map50), "mAP50_95": float(box.map),
        },
        "per_class": per,
    }
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=== 总体 ===")
    print("  P %.4f  R %.4f  mAP50 %.4f  mAP50-95 %.4f" % (
        out["overall"]["P"], out["overall"]["R"],
        out["overall"]["mAP50"], out["overall"]["mAP50_95"]))
    print()
    print("=== 按类（mAP50 降序）===")
    print("  %-12s %8s %8s %8s %10s" % ("class", "P", "R", "mAP50", "mAP50-95"))
    for r in sorted(per, key=lambda z: -z["mAP50"]):
        mark = "  <<< 活体" if r["name"] == "person" else ""
        print("  %-12s %8.3f %8.3f %8.3f %10.3f%s" % (
            r["name"], r["P"], r["R"], r["mAP50"], r["mAP50_95"], mark))
    print(f"\n-> {dst}")
    return 0


def compare(a_path: str, b_path: str) -> int:
    A = json.loads(Path(a_path).read_text(encoding="utf-8"))
    B = json.loads(Path(b_path).read_text(encoding="utf-8"))
    print(f"A = {A['weights']}  (data={Path(A['data']).parent.name})")
    print(f"B = {B['weights']}  (data={Path(B['data']).parent.name})")
    assert A["data"] == B["data"], "两次评估必须用同一个 data.yaml 才有可比性"

    oa, ob = A["overall"], B["overall"]
    print()
    print("=== 总体（同一 val 集）===")
    print("  %-10s %8s %8s %8s" % ("metric", "A", "B", "Δ(B-A)"))
    for k in ("P", "R", "mAP50", "mAP50_95"):
        print("  %-10s %8.4f %8.4f %+8.4f" % (k, oa[k], ob[k], ob[k] - oa[k]))

    ma = {r["name"]: r for r in A["per_class"]}
    mb = {r["name"]: r for r in B["per_class"]}
    common = [n for n in ma if n in mb]
    print()
    print("=== 按类 mAP50 ===")
    print("  %-12s %8s %8s %9s" % ("class", "A", "B", "Δ"))
    for n in sorted(common, key=lambda z: -mb[z]["mAP50"]):
        d = mb[n]["mAP50"] - ma[n]["mAP50"]
        mark = "  <<<" if n == "person" else ("  **" if abs(d) > 0.05 else "")
        print("  %-12s %8.3f %8.3f %+9.3f%s" % (n, ma[n]["mAP50"], mb[n]["mAP50"], d, mark))
    only_b = [n for n in mb if n not in ma]
    if only_b:
        print("  只在 B 里出现的类:", only_b)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights")
    ap.add_argument("--data", default=str(PROJECT / "datasets" / "livingB" / "data.yaml"))
    ap.add_argument("--split", default="val")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=str(PROJECT / "runs" / "eval.json"))
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)
    if not args.weights:
        ap.error("--weights 必填（或用 --compare A B）")
    return run_eval(args)


if __name__ == "__main__":
    sys.exit(main())
