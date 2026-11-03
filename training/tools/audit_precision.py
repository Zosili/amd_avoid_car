# -*- coding: utf-8 -*-
"""量化精度审计：从最终标签里按图分层随机抽样，渲染**高分辨率裁剪**（外扩上下文）。

与 audit_relabel.py 的区别：
  * 这里抽的是 person_labels.json 的最终结果，也就是"要拿去训练的那份"；
  * tile 更大（400px）且裁剪外扩，避免缩略图里分不清抱枕/椅背/真人腿；
  * 抽样按**图**为单位，同一张图只贡献一个框，避免同场景连续帧重复计数，
    让统计量接近独立样本（否则一个 20 帧的房间会主导整个精度估计）。

    python tools/audit_precision.py --n 120 --seed 31
"""

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"
TILE = 400
PAD = 0.45          # 裁剪时四周外扩比例（相对框尺寸），保留上下文


def crop_tile(img, box, label, split):
    h, w = img.shape[:2]
    cx, cy, bw, bh = box
    x1 = max(0, int((cx - bw / 2 - bw * PAD) * w))
    x2 = min(w, int((cx + bw / 2 + bw * PAD) * w))
    y1 = max(0, int((cy - bh / 2 - bh * PAD) * h))
    y2 = min(h, int((cy + bh / 2 + bh * PAD) * h))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return np.full((TILE + 18, TILE, 3), 32, np.uint8)
    crop = img[y1:y2, x1:x2]
    ch, cw = crop.shape[:2]
    s = min(TILE / cw, TILE / ch)
    crop = cv2.resize(crop, (max(1, int(cw * s)), max(1, int(ch * s))), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((TILE + 18, TILE, 3), 32, np.uint8)
    ch, cw = crop.shape[:2]
    oy, ox = (TILE - ch) // 2, (TILE - cw) // 2
    canvas[18 + oy:18 + oy + ch, ox:ox + cw] = crop
    # 在裁剪坐标系里画框
    bx1 = int(((cx - bw / 2) * w - x1) * s) + ox
    by1 = int(((cy - bh / 2) * h - y1) * s) + 18 + oy
    bx2 = int(((cx + bw / 2) * w - x1) * s) + ox
    by2 = int(((cy + bh / 2) * h - y1) * s) + 18 + oy
    cv2.rectangle(canvas, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
    cv2.putText(canvas, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(PROJECT / "runs/person_final/person_labels.json"))
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=31)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--out", default=str(PROJECT / "runs/audit_prec"))
    ap.add_argument("--exclude-phone", action="store_true", default=True)
    ap.add_argument("--all", action="store_true", help="不抽样，每图每框全部渲染")
    args = ap.parse_args()

    data = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    # 每张图随机取一个框；key 是相对路径，stem 用于对齐 manual_keep
    rng = random.Random(args.seed)
    pool = []
    for key, boxes in data.items():
        stem = Path(key).stem
        if args.exclude_phone and stem.startswith("ph_"):
            continue
        if not boxes:
            continue
        if args.all:
            for bi, bx in enumerate(boxes):
                pool.append((stem, key, bi, bx))
        else:
            bi = rng.randrange(len(boxes))
            pool.append((stem, key, bi, boxes[bi]))
    rng.shuffle(pool)
    picked = pool if args.all else pool[:args.n]
    print(f"候选框 {len(pool)} 个，渲染 {len(picked)} 个")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("*.jpg"):
        f.unlink()
    per = args.cols * args.rows
    index, n_page = {}, 0
    for page in range(0, len(picked), per):
        tiles = []
        for k, (stem, key, bi, box) in enumerate(picked[page:page + per]):
            sid = f"S{page + k}"
            index[sid] = [stem, bi, box]
            img = None
            for cand in (PROJECT / key,
                         DS / "images" / "train" / f"{stem}.jpg",
                         DS / "images" / "val" / f"{stem}.jpg"):
                if cand.exists():
                    img = cv2.imread(str(cand))
                    if img is not None:
                        break
            if img is None:
                tiles.append(np.full((TILE + 18, TILE, 3), 32, np.uint8))
                continue
            tiles.append(crop_tile(img, box, f"{sid} {stem[:10]} a{box[2] * box[3]:.3f}", ""))
        while len(tiles) < per:
            tiles.append(np.full((TILE + 18, TILE, 3), 32, np.uint8))
        rows = [np.hstack(tiles[r * args.cols:(r + 1) * args.cols]) for r in range(args.rows)]
        n_page += 1
        cv2.imwrite(str(outdir / f"prec_{n_page:02d}.jpg"), np.vstack(rows))
    (outdir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"生成 {n_page} 页 -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
