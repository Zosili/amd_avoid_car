# -*- coding: utf-8 -*-
"""
NPU 收益实测 —— 回答"到底值不值得上 NPU"

只统计节点数会骗人：YOLOv8n 的 8.7 GFLOPs 几乎全在 Conv 里，
Sigmoid/Mul/Concat 数量多但计算量极小。所以这里做三件事：

  1) 按算子类型拆开，看哪些 op 上了 NPU（不是只看总数）
  2) 测 VitisAI EP（NPU+CPU 混合）的推理延迟
  3) 测纯 CPU EP 的推理延迟，做同条件对比

用法：
    python bench_npu.py <model.onnx> [cache_key]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import onnx

os.environ.setdefault("RYZEN_AI_INSTALLATION_PATH", r"C:\Program Files\RyzenAI\1.6.0")

RYZEN_AI_DIR = Path(os.environ["RYZEN_AI_INSTALLATION_PATH"])
VOE_DIR = RYZEN_AI_DIR / "voe-4.0-win_amd64"
XCLBIN = VOE_DIR / "xclbins" / "phoenix" / "4x4.xclbin"
CACHE_DIR = Path(__file__).resolve().parent.parent / "export" / "vaip_cache"


def bench(sess, feed, n: int = 10, warm: int = 3):
    for _ in range(warm):
        sess.run(None, feed)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, feed)
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return ts[len(ts) // 2], ts[0], ts[-1], n + warm


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    cache_key = sys.argv[2] if len(sys.argv) > 2 else path.stem

    import onnxruntime as ort

    # ---- 算子类型表（供把 profile 里的名字映射回 op_type）----
    m = onnx.load(str(path))
    name2op = {}
    for n in m.graph.node:
        if n.name:
            name2op[n.name] = n.op_type

    so = ort.SessionOptions()
    so.enable_profiling = True
    so.log_severity_level = 3

    print("=" * 66)
    print(f"模型: {path.name}")
    print("=" * 66)

    # ---- 1) VitisAI EP ----
    po = [{
        "cache_dir": str(CACHE_DIR),
        "cache_key": cache_key,
        "target": "X1",
        "xclbin": str(XCLBIN),
    }]
    print("[1] 建立 VitisAI session ...")
    s_npu = ort.InferenceSession(str(path), so, providers=["VitisAIExecutionProvider"],
                                 provider_options=po)
    inp = s_npu.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    feed = {inp.name: np.random.rand(*shape).astype(np.float32)}

    med_npu, lo_npu, hi_npu, n_runs = bench(s_npu, feed)

    prof = s_npu.end_profiling()
    events = json.loads(Path(prof).read_text(encoding="utf-8"))
    # 每个 op 分 NPU/CPU 统计：次数 和 耗时(us)。dur 单位是微秒。
    per_op = defaultdict(lambda: {"npu": [0, 0], "cpu": [0, 0]})  # [次数, 总耗时us]
    for e in events:
        if e.get("cat") != "Node":
            continue
        a = e.get("args", {})
        prov = a.get("provider", "?")
        # args.op_name 里存的就是算子类型（如 QuantizeLinear），不是节点名
        op = a.get("op_name") or e.get("name", "?")
        tag = "npu" if "VitisAI" in prov else "cpu"
        per_op[op][tag][0] += 1
        per_op[op][tag][1] += int(e.get("dur", 0))
    try:
        os.remove(prof)
    except OSError:
        pass

    print()
    print(f"  按算子类型拆分（{n_runs} 次推理的累计值，已折算到单次）:")
    print(f"  {'算子':<20s} {'次数':>5s} {'NPU':>5s} {'CPU':>5s}  "
          f"{'NPU耗时us':>10s} {'CPU耗时us':>10s}  上NPU")
    print(f"  {'-' * 20} {'-' * 5} {'-' * 5} {'-' * 5}  "
          f"{'-' * 10} {'-' * 10}  {'-' * 6}")
    tot_npu = tot_all = 0
    tot_t_npu = tot_t_cpu = 0
    for op, d in sorted(per_op.items(), key=lambda kv: -(kv[1]["cpu"][1] + kv[1]["npu"][1])):
        n_npu = d["npu"][0] // n_runs
        n_cpu = d["cpu"][0] // n_runs
        t_npu = d["npu"][1] / n_runs
        t_cpu = d["cpu"][1] / n_runs
        n_all = n_npu + n_cpu
        if n_all == 0:
            continue
        tot_npu += n_npu
        tot_all += n_all
        tot_t_npu += t_npu
        tot_t_cpu += t_cpu
        pct = n_npu / n_all * 100 if n_all else 0
        print(f"  {op:<20s} {n_all:5d} {n_npu:5d} {n_cpu:5d}  "
              f"{t_npu:10.0f} {t_cpu:10.0f}  {pct:5.1f}%")
    print(f"  {'合计':<20s} {tot_all:5d} {tot_npu:5d} {tot_all - tot_npu:5d}  "
          f"{tot_t_npu:10.0f} {tot_t_cpu:10.0f}  "
          f"{tot_npu / tot_all * 100 if tot_all else 0:5.1f}%")

    # ---- 2) 纯 CPU EP ----
    print()
    print("[2] 纯 CPU EP 基准 ...")
    s_cpu = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    med_cpu, lo_cpu, hi_cpu, _ = bench(s_cpu, feed)

    print()
    print("=" * 66)
    print("延迟对比（单帧，640x640）")
    print("=" * 66)
    print(f"  VitisAI EP (NPU+CPU 混合) : 中位 {med_npu:7.2f} ms   [{lo_npu:.2f} ~ {hi_npu:.2f}]")
    print(f"  纯 CPU EP                 : 中位 {med_cpu:7.2f} ms   [{lo_cpu:.2f} ~ {hi_cpu:.2f}]")
    if med_cpu > 0:
        sp = med_cpu / med_npu
        print(f"  加速比                    : {sp:.2f}x")
        print()
        if sp < 1.05:
            print("  [结论] 上 NPU 没有收益（甚至更慢）。数据搬运和回退开销吃掉了全部加速。")
        elif sp < 1.5:
            print("  [结论] 有收益但很有限，混合执行的开销明显。")
        else:
            print("  [结论] 收益明确，NPU 值得用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
