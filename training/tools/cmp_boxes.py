# -*- coding: utf-8 -*-
"""
跨后端检测结果比对 —— 只依赖 numpy，两个环境都能跑。

用法：
    python detect_realtime.py --dump 20 --dump-out export/boxes_rocm.npz        # yolo-rocm
    python detect_realtime.py --backend dml --dump 20 --dump-out export/boxes_dml.npz
    python cmp_boxes.py export/boxes_rocm.npz export/boxes_dml.npz

判定口径：
    逐图按 IoU 贪心匹配（不能按置信度排序逐位比，置信度接近时顺序会翻转），
    要求框数完全相同、每个框都配上同类且 IoU >= 0.99。
"""

from __future__ import annotations

import sys

import numpy as np


def iou(a, b) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    u = aa + bb - inter
    return inter / u if u > 0 else 0.0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pa, pb = sys.argv[1], sys.argv[2]
    A, B = np.load(pa), np.load(pb)
    files = [str(s) for s in A["files"]]

    print("=" * 70)
    print(f"后端 A: {pa}")
    print(f"后端 B: {pb}")
    print("=" * 70)

    bad = 0
    tot_boxes = 0
    all_min_iou = []
    max_score_diff = 0.0

    for i, name in enumerate(files):
        ba, sa, ca = A[f"b{i}"], A[f"s{i}"], A[f"c{i}"]
        bb, sb, cb = B[f"b{i}"], B[f"s{i}"], B[f"c{i}"]
        tot_boxes += len(ba)

        if len(ba) != len(bb):
            print(f"  [差异] {name}  框数 A={len(ba)} B={len(bb)}")
            bad += 1
            continue
        if len(ba) == 0:
            print(f"  [OK  ] {name}  两边都是 0 框")
            continue

        used, ious, sdiff = set(), [], 0.0
        ok = True
        for k in range(len(ba)):
            best_j, best = -1, 0.0
            for j in range(len(bb)):
                if j in used:
                    continue
                v = iou(ba[k], bb[j])
                if v > best:
                    best, best_j = v, j
            if best_j < 0 or ca[k] != cb[best_j]:
                ok = False
                break
            used.add(best_j)
            ious.append(best)
            sdiff = max(sdiff, abs(float(sa[k]) - float(sb[best_j])))

        if not ok:
            print(f"  [差异] {name}  存在未匹配的框 / 类别不一致")
            bad += 1
            continue
        min_iou = min(ious)
        all_min_iou.append(min_iou)
        max_score_diff = max(max_score_diff, sdiff)
        flag = "OK  " if min_iou >= 0.99 else "退化"
        if min_iou < 0.99:
            bad += 1
        print(f"  [{flag}] {name}  {len(ba)} 框  minIoU={min_iou:.5f}  "
              f"最大置信度差={sdiff:.5f}")

    print()
    print(f"  共 {len(files)} 张，{tot_boxes} 个框")
    if all_min_iou:
        print(f"  全局最低 IoU {min(all_min_iou):.5f}   最大置信度差 {max_score_diff:.6f}")
    if bad == 0:
        print("  [OK] 两个后端逐框一致")
        return 0
    print(f"  [X] {bad} 张不一致")
    return 1


if __name__ == "__main__":
    sys.exit(main())
