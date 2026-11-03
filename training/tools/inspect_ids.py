# -*- coding: utf-8 -*-
"""疑点详查：给指定 S/J/K 编号渲染「整图 + 2 倍放大裁剪」对照条。

    python tools/inspect_ids.py --ids S26 S94 S57 S103 S108 --out runs/inspect
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS = PROJECT / "datasets/livingB"
H = 460          # 每格高度
PAIRS = []       # 无


def render(stem, box, sid):
    img = None
    src = None
    for cand in (DS / "images" / "train" / f"{stem}.jpg",
                 DS / "images" / "val" / f"{stem}.jpg"):
        if cand.exists():
            img = cv2.imread(str(cand))
            if img is not None:
                src = cand
                break
    if img is None:
        return np.full((H + 20, H * 2 + 10, 3), 32, np.uint8)
    h, w = img.shape[:2]

    # 左：整图
    s = H / max(h, w)
    full = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    cv2.rectangle(full, (int((box[0] - box[2] / 2) * w * s), int((box[1] - box[3] / 2) * h * s)),
                  (int((box[0] + box[2] / 2) * w * s), int((box[1] + box[3] / 2) * h * s)),
                  (0, 0, 255), 2)

    # 右：紧贴裁剪，放大到 H 高
    cx, cy, bw, bh = box
    pad = 0.35
    x1 = max(0, int((cx - bw / 2 - bw * pad) * w))
    x2 = min(w, int((cx + bw / 2 + bw * pad) * w))
    y1 = max(0, int((cy - bh / 2 - bh * pad) * h))
    y2 = min(h, int((cy + bh / 2 + bh * pad) * h))
    if x2 - x1 < 4 or y2 - y1 < 4:
        crop = np.zeros((H, H, 3), np.uint8)
        cs = 1.0
    else:
        crop = img[y1:y2, x1:x2]
        ch, cw = crop.shape[:2]
        cs = min(H / cw, H / ch)
        crop = cv2.resize(crop, (max(1, int(cw * cs)), max(1, int(ch * cs))), interpolation=cv2.INTER_CUBIC)
    ch, cw = crop.shape[:2]
    right = np.full((H, H, 3), 32, np.uint8)
    oy, ox = (H - ch) // 2, (H - cw) // 2
    right[oy:oy + ch, ox:ox + cw] = crop
    if cs > 0:
        cv2.rectangle(right,
                      (int(((cx - bw / 2) * w - x1) * cs) + ox, int(((cy - bh / 2) * h - y1) * cs) + oy),
                      (int(((cx + bw / 2) * w - x1) * cs) + ox, int(((cy + bh / 2) * h - y1) * cs) + oy),
                      (0, 255, 0), 2)

    row = np.full((H + 20, H * 2 + 10, 3), 32, np.uint8)
    row[20:20 + full.shape[0], 0:full.shape[1]] = full
    row[20:, H + 10:H + 10 + H] = right
    cv2.putText(row, f"{sid} a{box[2] * box[3]:.4f} {stem}", (3, 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--index", action="append", default=[],
                    help="索引 json，可多次给；默认按 pref 猜")
    ap.add_argument("--out", default=str(PROJECT / "runs/inspect"))
    args = ap.parse_args()

    idxs = {}
    for p in (PROJECT / "runs/audit_prec/index.json",
              PROJECT / "runs/audit_keep3/keep_index.json",
              PROJECT / "runs/audit_keep/keep_index.json"):
        if Path(p).exists():
            idxs.update(json.loads(Path(p).read_text(encoding="utf-8")))
    for p in args.index:
        idxs.update(json.loads(Path(p).read_text(encoding="utf-8")))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("*.jpg"):
        f.unlink()

    tiles = []
    keep_index = {}
    for sid in args.ids:
        if sid not in idxs:
            print("索引缺失", sid)
            continue
        stem, bi, box = idxs[sid]
        keep_index[sid] = idxs[sid]
        tiles.append(render(stem, box, sid))
    if tiles:
        cv2.imwrite(str(outdir / "inspect.jpg"), np.vstack(tiles))
        (outdir / "index.json").write_text(json.dumps(keep_index, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{len(tiles)} 个疑点 -> {outdir / 'inspect.jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
