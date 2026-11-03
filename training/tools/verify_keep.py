# -*- coding: utf-8 -*-
"""确认页：只渲染"人工保留"的框，紧贴裁剪，用于第二次核对（查漏掉的误检）。

    python tools/verify_keep.py --cols 6 --rows 5 --out runs/audit_keep
"""

import argparse
import json
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
    ap.add_argument("--keep", default=str(PROJECT / "runs/person_final/manual_keep.json"))
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--out", default=str(PROJECT / "runs/audit_keep"))
    args = ap.parse_args()

    keep = json.loads(Path(args.keep).read_text(encoding="utf-8"))
    items = []
    for stem, boxes in keep.items():
        for b in boxes:
            items.append((stem, b))
    items.sort(key=lambda t: t[0])
    print(f"待确认 {len(items)} 个框")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    per = args.cols * args.rows
    index, n_page = {}, 0
    for page in range(0, len(items), per):
        tiles = []
        for k, (stem, box) in enumerate(items[page:page + per]):
            sid = f"K{page + k}"
            index[sid] = [stem, box]
            img = None
            for sp in ("train", "val"):
                p = DS / "images" / sp / f"{stem}.jpg"
                if p.exists():
                    img = cv2.imread(str(p))
                    break
            if img is None:
                tiles.append(np.zeros((260, 200, 3), np.uint8))
                continue
            tiles.append(_render_crop(img, box, sid))
        while len(tiles) < per:
            tiles.append(np.zeros((260, 200, 3), np.uint8))
        hh = max(t.shape[0] for t in tiles)
        ww = max(t.shape[1] for t in tiles)
        tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, ww - t.shape[1],
                                    cv2.BORDER_CONSTANT, value=(32, 32, 32)) for t in tiles]
        rows = [np.hstack(tiles[r * args.cols:(r + 1) * args.cols]) for r in range(args.rows)]
        n_page += 1
        cv2.imwrite(str(outdir / f"keep_{n_page:02d}.jpg"), np.vstack(rows))
    (outdir / "keep_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"生成 {n_page} 页 -> {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
