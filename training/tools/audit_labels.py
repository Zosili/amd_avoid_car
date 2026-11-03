# -*- coding: utf-8 -*-
"""定量审计：随机抽 person 框渲染裁剪图，用于统计标注精确率。

    python tools/audit_labels.py --n 100 --seed 7 --split train --out runs/audit
"""

import argparse
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"
sys.path.insert(0, str(PROJECT / "tools"))
from person_relabel import _render_crop  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--split", default="train")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--out", default=str(PROJECT / "runs/audit"))
    ap.add_argument("--only18cls", action="store_true", default=True)
    args = ap.parse_args()

    boxes = []
    for lp in sorted((DS / "labels" / args.split).glob("*.txt")):
        # 只看来自 18cls 的图（补标部分才是本轮的成果），跳过手机自采 ph_*
        if args.only18cls and lp.stem.startswith("ph_"):
            continue
        for i, line in enumerate(lp.read_text(encoding="utf-8").splitlines()):
            v = line.split()
            if v and int(v[0]) == 18:
                cx, cy, w, h = (float(x) for x in v[1:5])
                boxes.append((lp.stem, i, [cx, cy, w, h]))
    print(f"{args.split}: person 框 {len(boxes)} 个（剔除 ph_ 自采）")
    random.seed(args.seed)
    sel = random.sample(boxes, min(args.n, len(boxes)))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    index = {}
    per = args.cols * args.rows
    n_page = 0
    for page in range(0, len(sel), per):
        tiles = []
        for k, (stem, i, box) in enumerate(sel[page:page + per]):
            sid = f"P{page + k}"
            index[sid] = [stem, i]
            img = cv2.imread(str(DS / "images" / args.split / f"{stem}.jpg"))
            if img is None:
                tiles.append(np.zeros((300, 300, 3), np.uint8))
                continue
            a = box[2] * box[3]
            tiles.append(_render_crop(img, box, f"{sid} {stem[:10]} a{a:.3f}"))
        while len(tiles) < per:
            tiles.append(np.zeros((300, 300, 3), np.uint8))
        rows = [np.hstack(tiles[r * args.cols:(r + 1) * args.cols]) for r in range(args.rows)]
        n_page += 1
        cv2.imwrite(str(outdir / f"audit_{n_page:02d}.jpg"), np.vstack(rows))
    (outdir / f"audit_{args.split}_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"生成 {n_page} 页 -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
