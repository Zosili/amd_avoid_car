# -*- coding: utf-8 -*-
"""离线冒烟测试：真图 -> ONNX -> person 框 -> 单目估距 -> 状态"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import cv2
import onnxruntime as ort
import detect_fusion_live as D
from fusion_core import DecisionConfig, FusionEngine

DS = Path(r"D:\FPGA\ultralytics\datasets\livingB")
sess = ort.InferenceSession(D.MODEL, providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0]

cfg = DecisionConfig(cam_w=640, cam_h_px=480, hfov_deg=60.0,
                     cam_height_m=float(sys.argv[1]) if len(sys.argv) > 1 else 0.20)
eng = FusionEngine(cfg)

# 找 val 里有 person 标签的图
picks = []
for lp in (DS / "labels/val").glob("*.txt"):
    if any(l.split() and int(l.split()[0]) == 18
           for l in lp.read_text(encoding="utf-8").splitlines()):
        picks.append(lp.stem)
picks = sorted(picks)[:10]
print(f"抽 {len(picks)} 张含 person 的 val 图\n")

hit = 0
for i, stem in enumerate(picks):
    p = DS / "images/val" / f"{stem}.jpg"
    img = cv2.imread(str(p))
    if img is None:
        print(f"  {stem}: 读不到"); continue
    lb, r, pad = D.letterbox(img, 640, 32)
    x = np.ascontiguousarray(
        lb[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)[None] / 255.0)
    out = sess.run(None, {inp.name: x})[0]
    det = D.postprocess(out, r, pad, 0.35, 0.45, *img.shape[:2])
    pp = [d for d in det if d["cls"] == 18]
    res = eng.update(None, pp, None)
    hit += 1 if pp else 0
    d = f"{res.dist:.2f}m" if res.dist is not None else "  -- "
    src = res.readings[0].source if res.readings else "-"
    extra = ""
    if pp:
        y2 = pp[0]["bbox"][3]
        extra = f" bbox底边y={y2:.0f}"
    print(f"  {stem[:18]:<18} person {len(pp)} 个  距离 {d} [{src}]  "
          f"状态 {res.state.value:<5}{extra}")

print(f"\n检出 {hit}/{len(picks)} 张")
