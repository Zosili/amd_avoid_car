# -*- coding: utf-8 -*-
"""模型筛人：从一堆抽帧里挑出"含 person"的帧，写到一个干净目录。

判据（A+ 折中）：
  - conf>=0.25 的 person 框存在 → 保留；
  - 若某帧没有 person，但 conf 落在 [0.15,0.25) 且框较大 → 也保留（远景弱检出）。
  - 其余判为空帧，不写。

同时把保留帧复制到 out/images，标签写空 .txt（待人工标注）。
"""
import argparse
import glob
import os
import shutil
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODEL = r"D:\FPGA\ultralytics\runs\livingB_yolo11n\weights\best.onnx"
PERSON = 18


def person_dets(sess, im, ct=0.15):
    h0, w0 = im.shape[:2]
    r = min(640 / w0, 640 / h0)
    nw, nh = int(w0 * r), int(h0 * r)
    cv = np.full((640, 640, 3), 114, np.uint8)
    dx, dy = (640 - nw) // 2, (640 - nh) // 2
    cv[dy:dy + nh, dx:dx + nw] = cv2.resize(im, (nw, nh))
    x = cv[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
    o = sess.run(None, {"images": x})[0][0]
    sc = o[4:, :]
    cls = sc.argmax(0)
    conf = sc.max(0)
    k = np.where((cls == PERSON) & (conf > ct))[0]
    if len(k) == 0:
        return []
    cx, cy, bw, bh = o[0, k], o[1, k], o[2, k], o[3, k]
    box = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], 1)
    idx = [int(i) for i in np.array(
        cv2.dnn.NMSBoxes(box.tolist(), conf[k].tolist(), ct, 0.45)).flatten()]
    return [(float(conf[k[i]]), float(bh[i] / r), float(bw[i] / r)) for i in idx]


def keep_frame(dets):
    if not dets:
        return False
    best_c, best_h, best_w = max(dets, key=lambda z: z[0])
    if best_c >= 0.25:
        return True
    # 弱检出：conf 0.15~0.25 且框高>=180px（中近景人才会出现，远景误检小）
    if 0.15 <= best_c < 0.25 and best_h >= 180:
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="待筛图片目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", type=float, default=0.15, help="检测下限")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    fs = sorted(glob.glob(str(Path(args.src) / "images" / "*.jpg")) +
                glob.glob(str(Path(args.src) / "*.jpg")))
    kept = 0
    total = 0
    for f in fs:
        im = cv2.imread(f)
        if im is None:
            continue
        total += 1
        dets = person_dets(sess, im, args.conf)
        if keep_frame(dets):
            name = Path(f).stem
            shutil.copy(f, out / "images" / f"{name}.jpg")
            (out / "labels" / f"{name}.txt").write_text("", encoding="utf-8")
            kept += 1
    print(f"筛 {total} 张 -> 保留 {kept} 张 ({100 * kept / max(1, total):.1f}%) -> {out}")


if __name__ == "__main__":
    main()
