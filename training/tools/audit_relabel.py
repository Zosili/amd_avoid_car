# -*- coding: utf-8 -*-
"""全量目视复核：把 18cls 补标产生的 person 框**全部**渲染出来（不抽样）。

与 audit_labels.py 的区别：
  * 只取 18cls 来源的框（排除 coco128 的 ms_* 与手机自采 ph_*），避免把第三方
    标注噪声算进本轮成果；
  * 不抽样，默认全量；
  * 按文件名排序渲染 —— 同一场景的连续帧排在一起，便于用"前后帧一致性"判断
    静止误检（墙上照片、电视里的人、挂着的衣服）。

    python tools/audit_relabel.py --split train --cols 6 --rows 5 --out runs/audit_full
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"
PERSON_ID = 18
TILE = 320


def draw_full(img, box, label):
    """整图缩略 + 红框 + 顶部标签条。保留上下文，才能分辨"墙上照片"和"真人"。"""
    h, w = img.shape[:2]
    cx, cy, bw, bh = box
    x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
    x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
    s = TILE / max(h, w)
    small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    cv2.rectangle(small, (int(x1 * s), int(y1 * s)), (int(x2 * s), int(y2 * s)), (0, 0, 255), 2)
    canvas = np.full((TILE + 18, TILE, 3), 32, np.uint8)
    oy = (TILE - sh) // 2
    ox = (TILE - sw) // 2
    canvas[18 + oy:18 + oy + sh, ox:ox + sw] = small
    cv2.putText(canvas, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--out", default=str(PROJECT / "runs/audit_full"))
    ap.add_argument("--start", type=int, default=0, help="从第几个框开始（断点续看）")
    ap.add_argument("--limit", type=int, default=0, help="最多渲染多少个框，0=全部")
    ap.add_argument("--crop", action="store_true", help="改为只渲染框附近的裁剪图")
    args = ap.parse_args()

    src18 = {p.stem for p in (PROJECT / "datasets/18cls/labels/train").glob("*.txt")}
    src18 |= {p.stem for p in (PROJECT / "datasets/18cls/labels/val").glob("*.txt")}

    boxes = []
    for lp in sorted((DS / "labels" / args.split).glob("*.txt")):
        if lp.stem not in src18:
            continue
        for i, line in enumerate(lp.read_text(encoding="utf-8").splitlines()):
            v = line.split()
            if v and int(v[0]) == PERSON_ID:
                boxes.append((lp.stem, i, [float(x) for x in v[1:5]]))
    boxes.sort(key=lambda t: (t[0], t[1]))
    print(f"{args.split}: 18cls 补标 person 框 {len(boxes)} 个")
    if args.limit:
        boxes = boxes[args.start:args.start + args.limit]

    per = args.cols * args.rows
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    index = {}
    n_page = 0
    for page in range(0, len(boxes), per):
        tiles = []
        for k, (stem, i, box) in enumerate(boxes[page:page + per]):
            sid = f"Q{page + k}"
            index[sid] = [stem, i, box]
            img = cv2.imread(str(DS / "images" / args.split / f"{stem}.jpg"))
            if img is None:
                tiles.append(np.full((TILE + 18, TILE, 3), 32, np.uint8))
                continue
            a = box[2] * box[3]
            tiles.append(draw_full(img, box, f"{sid} {stem[:12]} a{a:.3f}"))
        while len(tiles) < per:
            tiles.append(np.full((TILE + 18, TILE, 3), 32, np.uint8))
        rows = [np.hstack(tiles[r * args.cols:(r + 1) * args.cols]) for r in range(args.rows)]
        n_page += 1
        cv2.imwrite(str(outdir / f"{args.split}_{n_page:02d}.jpg"), np.vstack(rows))
    (outdir / f"{args.split}_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"生成 {n_page} 页 -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
