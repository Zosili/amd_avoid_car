# -*- coding: utf-8 -*-
"""
DML 数值正确性核对：同一个 ONNX，CPU(fp32 参考) vs DML(fp32) vs DML(fp16)

比的是原始输出张量，并进一步解码成框做 IoU 比对——只看速度不看正确性是耍流氓。
"""

from __future__ import annotations

import sys

import numpy as np
import onnxruntime as ort

MODEL = r"D:\FPGA\ultralytics\export\yolo11n_18cls_480.onnx"
IMGSZ = 480
CONF = 0.25


def make_session(providers, opts=None):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    return ort.InferenceSession(MODEL, so, providers=providers, provider_options=opts)


def decode(t: np.ndarray, conf=CONF):
    """(1, 4+nc, N) -> (boxes_xyxy, scores, cls)，单标签取最大类"""
    p = t[0].T
    xywh, cls_scores = p[:, :4], p[:, 4:]
    conf_s, cls = cls_scores.max(1), cls_scores.argmax(1)
    m = conf_s > conf
    xywh, conf_s, cls = xywh[m], conf_s[m], cls[m]
    b = np.empty_like(xywh)
    b[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    b[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    b[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    b[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    return b, conf_s, cls


def iou(a, b):
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    u = aa + bb - inter
    return inter / u if u > 0 else 0.0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    rng = np.random.default_rng(0)
    x = rng.random((1, 3, IMGSZ, IMGSZ), dtype=np.float32)

    s_cpu = make_session(["CPUExecutionProvider"])
    y_cpu = s_cpu.run(None, {s_cpu.get_inputs()[0].name: x})[0]

    s_d = make_session(["DmlExecutionProvider"])
    y_df = s_d.run(None, {s_d.get_inputs()[0].name: x})[0]

    s_h = make_session(["DmlExecutionProvider"], [{"enable_fp16": "1"}])
    y_dh = s_h.run(None, {s_h.get_inputs()[0].name: x})[0]

    print("=" * 66, flush=True)
    print("DML 数值正确性核对（随机输入，比较原始输出张量）", flush=True)
    print("=" * 66, flush=True)
    for name, y in (("DML fp32", y_df), ("DML fp16", y_dh)):
        d = np.abs(y - y_cpu)
        rel = d.max() / (np.abs(y_cpu).max() + 1e-9)
        print(f"  {name:<10s} 与 CPU 参考相比: max绝对差 {d.max():8.4f}  "
              f"相对 {rel * 100:6.3f}%  平均 {d.mean():.5f}", flush=True)

    print(flush=True)
    bc, sc, cc = decode(y_cpu)
    print(f"  CPU     解码出 {len(bc)} 个候选框", flush=True)
    for name, y in (("DML fp32", y_df), ("DML fp16", y_dh)):
        bd, sd, cd = decode(y)
        if len(bd) != len(bc):
            print(f"  {name}: 候选框数不同 {len(bd)} vs {len(bc)}", flush=True)
            continue
        used, ious, dcls = set(), [], 0
        for i in range(len(bc)):
            best_j, best = -1, -1.0
            for j in range(len(bd)):
                if j in used:
                    continue
                v = iou(bc[i], bd[j])
                if v > best:
                    best, best_j = v, j
            used.add(best_j)
            ious.append(best)
            dcls += int(cc[i] != cd[best_j])
        print(f"  {name}: 候选框 {len(bd)} 个，最少 IoU {min(ious):.5f}，"
              f"类别不一致 {dcls} 个", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
