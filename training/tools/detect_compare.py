# -*- coding: utf-8 -*-
"""同一批 val 图上并排对比 A/B 两个模型的 person 检出。

为什么需要它
----------------------------------------------------------------
用手机视频做的检查几乎分不出 A/B —— 那些画面进过训练集，两边都认得。
真正能分开两者的是 val 集里由 18cls 补标出来的 person 框：低视角、
只露腿、遮挡严重，A 组训练时**整张剔除了这些图**，完全没见过。

输出三列：原图+真值(GT) | 模型A | 模型B
命中判据：与任一 GT 框 IoU >= 0.25 即算命中（低视角小目标框得很松，
          用 0.5 会误判成漏检）。

    python tools/detect_compare.py --n 24 --seed 5
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"
PERSON_ID = 18
TILE = 380


def iou(a, b) -> float:
    """a/b 为归一化 xywh。"""
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def load_person_imgs(split="val"):
    out = []
    for lp in sorted((DS / "labels" / split).glob("*.txt")):
        gts = []
        for line in lp.read_text(encoding="utf-8").splitlines():
            v = line.split()
            if v and int(v[0]) == PERSON_ID:
                gts.append([float(x) for x in v[1:5]])
        if gts:
            img = DS / "images" / split / f"{lp.stem}.jpg"
            if img.exists():
                out.append((lp.stem, img, gts))
    return out


def infer(model, img_path: Path, conf: float, device: str):
    r = model.predict(source=str(img_path), imgsz=640, conf=conf, iou=0.6,
                      classes=[PERSON_ID], max_det=20, verbose=False, device=device)[0]
    boxes = []
    if r.boxes is not None and len(r.boxes):
        for (cx, cy, w, h), c in zip(r.boxes.xywhn.cpu().numpy(),
                                     r.boxes.conf.cpu().numpy()):
            boxes.append(([float(cx), float(cy), float(w), float(h)], float(c)))
    return boxes


def paint(img, gts, preds, tag, hit_ok=None):
    h, w = img.shape[:2]
    s = TILE / max(h, w)
    canvas = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ch, cw = canvas.shape[:2]
    pad = np.full((TILE + 26, TILE, 3), 28, np.uint8)
    oy, ox = (TILE - ch) // 2, (TILE - cw) // 2
    pad[26 + oy:26 + oy + ch, ox:ox + cw] = canvas

    def rect(b, color, th=2):
        x1 = int((b[0] - b[2] / 2) * w * s) + ox
        y1 = int((b[1] - b[3] / 2) * h * s) + 26 + oy
        x2 = int((b[0] + b[2] / 2) * w * s) + ox
        y2 = int((b[1] + b[3] / 2) * h * s) + 26 + oy
        cv2.rectangle(pad, (x1, y1), (x2, y2), color, th)

    for g in gts:
        rect(g, (0, 255, 255), 1)      # 真值：黄细框
    for b, c in preds:
        rect(b, (0, 255, 0) if hit_ok else (0, 0, 255), 2)
    cv2.putText(pad, tag, (3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA)
    return pad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(PROJECT / "runs/living_yolo11n/weights/best.pt"))
    ap.add_argument("--b", default=str(PROJECT / "runs/livingB_yolo11n/weights/best.pt"))
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou-hit", type=float, default=0.25)
    ap.add_argument("--device", default="0")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--out", default=str(PROJECT / "runs/detect_compare"))
    args = ap.parse_args()

    from ultralytics import YOLO
    ma, mb = YOLO(args.a), YOLO(args.b)

    pool = load_person_imgs(args.split)
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    picked = pool[:args.n]
    print(f"{args.split} 含 person 的图 {len(pool)} 张，抽 {len(picked)} 张")

    stat = {"A": [0, 0], "B": [0, 0]}   # [命中图数, 总数]
    rows = []
    detail = []
    for stem, img_path, gts in picked:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        pa = infer(ma, img_path, args.conf, args.device)
        pb = infer(mb, img_path, args.conf, args.device)

        def hit(preds):
            return any(iou(b, g) >= args.iou_hit for b, _ in preds for g in gts)

        ha, hb = hit(pa), hit(pb)
        stat["A"][0] += int(ha); stat["A"][1] += 1
        stat["B"][0] += int(hb); stat["B"][1] += 1
        detail.append({"stem": stem, "gt": len(gts), "A_hit": ha, "A_n": len(pa),
                       "B_hit": hb, "B_n": len(pb)})
        rows.append((stem, gts, pa, ha, pb, hb))

    # 把"只有一边检出"的排前面，一眼看到差异
    rows.sort(key=lambda r: (r[3] == r[5], r[3]))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("*.jpg"):
        f.unlink()
    per = args.cols
    n_page = 0
    for page in range(0, len(rows), per):
        tiles = []
        for stem, gts, pa, ha, pb, hb in rows[page:page + per]:
            img = cv2.imread(str(DS / "images" / args.split / f"{stem}.jpg"))
            if img is None:
                continue
            tiles.append(np.hstack([
                paint(img, gts, [], f"{stem[:12]} GT{len(gts)}", None),
                paint(img, gts, pa, f"A {'HIT' if ha else 'MISS'} n{len(pa)}", ha),
                paint(img, gts, pb, f"B {'HIT' if hb else 'MISS'} n{len(pb)}", hb),
            ]))
        while len(tiles) % per:
            tiles.append(np.full((TILE + 26, TILE * 3, 3), 28, np.uint8))
        n_page += 1
        cv2.imwrite(str(outdir / f"cmp_{n_page:02d}.jpg"), np.vstack(tiles))

    print()
    for k in ("A", "B"):
        h, t = stat[k]
        print(f"  模型{k}: 命中 {h}/{t} = {h / t * 100:.1f}%")
    onlyA = sum(1 for d in detail if d["A_hit"] and not d["B_hit"])
    onlyB = sum(1 for d in detail if d["B_hit"] and not d["A_hit"])
    both = sum(1 for d in detail if d["A_hit"] and d["B_hit"])
    none = sum(1 for d in detail if not d["A_hit"] and not d["B_hit"])
    print(f"  两边都检出 {both} | 只有 A {onlyA} | 只有 B {onlyB} | 都漏 {none}")
    (outdir / "detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {outdir}  ({n_page} 页)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
