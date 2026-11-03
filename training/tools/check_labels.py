# -*- coding: utf-8 -*-
"""抽查：把带 person 标签的整图画上框，检查框是否贴合、有没有漏标的人。

用法：
    python tools/check_labels.py --n 24 --seed 1 --out runs/chk_labels_1.jpg
"""

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--split", default="val")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--out", default=str(PROJECT / "runs/chk_labels.jpg"))
    args = ap.parse_args()

    imgs = sorted((DS / "images" / args.split).glob("*.jpg"))
    pos = []
    for p in imgs:
        lp = DS / "labels" / args.split / f"{p.stem}.txt"
        if not lp.exists():
            continue
        has = any(l.split() and int(l.split()[0]) == 18
                  for l in lp.read_text(encoding="utf-8").splitlines())
        if has:
            pos.append(p)
    random.seed(args.seed)
    sel = random.sample(pos, min(args.n, len(pos)))
    print(f"{args.split}: 有 person 的图 {len(pos)} 张，抽 {len(sel)}")

    tiles = []
    for p in sel:
        im = cv2.imread(str(p))
        h, w = im.shape[:2]
        lp = DS / "labels" / args.split / f"{p.stem}.txt"
        for l in lp.read_text(encoding="utf-8").splitlines():
            v = l.split()
            if not v or int(v[0]) != 18:
                continue
            cx, cy, bw, bh = (float(x) for x in v[1:5])
            x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
            x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
            cv2.rectangle(im, (x1, y1), (x2, y2), (0, 0, 255), 3)
        t = cv2.resize(im, (400, 300))
        cv2.putText(t, p.stem, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        tiles.append(t)

    while len(tiles) % args.cols:
        tiles.append(np.zeros((300, 400, 3), np.uint8))
    rows = [np.hstack(tiles[i:i + args.cols]) for i in range(0, len(tiles), args.cols)]
    cv2.imwrite(args.out, np.vstack(rows))
    print("->", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
