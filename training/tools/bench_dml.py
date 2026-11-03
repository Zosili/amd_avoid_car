# -*- coding: utf-8 -*-
"""
DirectML 推理实测 —— 用于对比 torch+ROCm 路径

只测 session.run（模型推理），不含前后处理，便于和 torch 的"模型推理"耗时对比。

用法（必须用 ryzen-ai-1.6.0 环境，DML 只在那儿）：
    python bench_dml.py <model.onnx> [--imgsz 480] [--n 50]
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import onnxruntime as ort


def bench(sess, feed, n: int = 50, warm: int = 10):
    for _ in range(warm):
        sess.run(None, feed)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, feed)
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return ts


def pct(s, p):
    k = min(len(s) - 1, int(round((len(s) - 1) * p)))
    return s[k]


def run(tag: str, path: str, providers, opts, shape):
    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(path, so, providers=providers,
                                    provider_options=opts)
    except Exception as e:
        print(f"[{tag}] 建 session 失败: {type(e).__name__}: {e}", flush=True)
        return None
    inp = sess.get_inputs()[0]
    feed = {inp.name: np.random.rand(*shape).astype(np.float32)}
    try:
        out = sess.run(None, feed)
    except Exception as e:
        print(f"[{tag}] 推理失败: {type(e).__name__}: {e}", flush=True)
        return None
    s = bench(sess, feed)
    print(f"[{tag}]  P50 {pct(s, .50):7.2f}  P95 {pct(s, .95):7.2f}  "
          f"P99 {pct(s, .99):7.2f}  max {s[-1]:7.2f} ms   输出 {out[0].shape}",
          flush=True)
    return pct(s, .50)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--imgsz", type=int, default=480)
    ap.add_argument("--n", type=int, default=50)
    a = ap.parse_args()

    shape = (1, 3, a.imgsz, a.imgsz)
    print("=" * 66, flush=True)
    print(f"DirectML 实测  model={a.model}  input={shape}", flush=True)
    print(f"onnxruntime {ort.__version__}", flush=True)
    print("=" * 66, flush=True)

    run("CPU ", a.model, ["CPUExecutionProvider"], None, shape)
    run("DML ", a.model, ["DmlExecutionProvider"], None, shape)
    # DML 的 fp16 开关：内部用 fp16 计算，模型输入仍是 fp32
    run("DML-fp16", a.model, ["DmlExecutionProvider"], [{"enable_fp16": "1"}], shape)
    return 0


if __name__ == "__main__":
    sys.exit(main())
