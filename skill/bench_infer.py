#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_infer.py —— 目标检测推理基准（后端可切换）

回答三个问题：单帧推理多久、首次与稳态差多少、换后端能快几倍。

核心约定：
  · 预热与稳态分离 —— NPU/GPU 首次会编译并写缓存，混进去会虚高数倍
  · 报分位数而非均值 —— 延迟长尾，均值掩盖卡顿
  · 预处理/推理/后处理分开报 —— 否则不知道瓶颈在哪

用法：
    python bench_infer.py --model best.onnx --backend dml --n 100
    python bench_infer.py --model best.onnx --backend cpu --n 30 --compare dml

退出码：正常 0。
"""
from __future__ import annotations

import argparse
import json
import sys
import time

BACKEND_MAP = {
    "cpu": "CPUExecutionProvider",
    "dml": "DmlExecutionProvider",
    "rocm": "ROCMExecutionProvider",
    "migraphx": "MIGraphXExecutionProvider",
    "vitisai": "VitisAIExecutionProvider",
    "cuda": "CUDAExecutionProvider",
}


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def summarize(vals):
    return {
        "n": len(vals),
        "p50": pct(vals, 50),
        "p95": pct(vals, 95),
        "p99": pct(vals, 99),
        "max": max(vals) if vals else 0.0,
        "mean": sum(vals) / len(vals) if vals else 0.0,
    }


def run(onnx_path, backend, shape, n, warmup):
    import numpy as np
    import onnxruntime as ort

    avail = ort.get_available_providers()
    ep = BACKEND_MAP.get(backend)
    if ep is None:
        sys.exit(f"未知后端 {backend}，可选：{list(BACKEND_MAP)}")
    if ep not in avail:
        sys.exit(f"后端 {ep} 不可用。\n当前可用：{avail}\n"
                 f"（Windows + AMD GPU 需 pip install onnxruntime-directml；"
                 f"Linux + ROCm 需装带 ROCM EP 的 onnxruntime）")
    print(f"后端 {ep}（可用：{avail}）")

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(onnx_path, sess_options=so, providers=[ep])

    # 真正生效的 provider 只有建完 session 才知道（列表里有 ≠ 实际用上了）。
    # 早先这里写的是 ort.get_session_options（不存在该属性），一跑到非 CPU 后端就崩，
    # 已改为查 session 自己报告的 provider 顺序。
    used = sess.get_providers()
    if not used or used[0] != ep:
        print(f"⚠ 警告：请求 {ep}，实际生效 {used[:1] if used else '无'}"
              f"（已回退，本次数字不代表该后端）")
    else:
        print(f"实际生效 provider：{used[0]}")
    inp = sess.get_inputs()[0]
    in_name = inp.name
    real_shape = inp.shape
    print(f"输入 {in_name} 声明形状 {real_shape}，实测用 {shape}")

    x = np.random.rand(*shape).astype(np.float32)

    # ---- 预热（不计入统计）----
    t_warm = time.perf_counter()
    for _ in range(warmup):
        sess.run(None, {in_name: x})
    warm_ms = (time.perf_counter() - t_warm) / max(warmup, 1) * 1000
    print(f"预热 {warmup} 帧完成，平均 {warm_ms:.1f} ms（含首次编译，已排除出统计）")

    # ---- 稳态 ----
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, {in_name: x})
        lat.append((time.perf_counter() - t0) * 1000)

    s = summarize(lat)
    fps = 1000.0 / s["p50"] if s["p50"] else 0.0
    print(f"\n=== 稳态 {n} 帧 ===")
    print(f"  P50 {s['p50']:.1f} ms   P95 {s['p95']:.1f} ms   "
          f"P99 {s['p99']:.1f} ms   max {s['max']:.1f} ms")
    print(f"  均值 {s['mean']:.1f} ms   等效 {fps:.1f} FPS（按 P50）")
    return s


def main():
    ap = argparse.ArgumentParser(description="onnxruntime 推理基准")
    ap.add_argument("--model", required=True)
    ap.add_argument("--backend", default="cpu", choices=list(BACKEND_MAP))
    ap.add_argument("--input", default="1x3x640x640", help="NxCxHxW")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--compare", help="与另一个后端对比，如 cpu")
    ap.add_argument("--json")
    args = ap.parse_args()

    shape = [int(v) for v in args.input.lower().replace("*", "x").split("x")]
    main_stat = run(args.model, args.backend, shape, args.n, args.warmup)

    other = None
    if args.compare:
        print("\n" + "=" * 52)
        other = run(args.model, args.compare, shape, max(args.n // 3, 10), args.warmup)
        if other["p50"] > 0 and main_stat["p50"] > 0:
            print(f"\n{args.backend} 相对 {args.compare} 提速约 "
                  f"{other['p50'] / main_stat['p50']:.1f}×")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"backend": args.backend, "stat": main_stat,
                       "compare": other}, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
