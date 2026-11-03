# -*- coding: utf-8 -*-
"""
================================================================
 自动预标注 —— 把「画框」变成「删框」
================================================================

用法（在 D:\\FPGA\\ultralytics 下）：

    # 标 datasets/custom_raw/images，输出同名 .txt
    python tools\\autolabel.py --src datasets\\custom_raw\\images

    # 用大模型标得更准（慢 5 倍，但标注只做一次，值得）
    python tools\\autolabel.py --src datasets\\custom_raw\\images --model yolo11l.pt

    # 先只看质量，不写盘
    python tools\\autolabel.py --src datasets\\custom_raw\\images --dry-run

标完用 tools\\review_labels.py 逐个过一遍（按键审核，比 labelImg 快很多）。

----------------------------------------------------------------
 为什么用 COCO 预训练权重，而不是你自己训的 best.pt
----------------------------------------------------------------
    你自己的模型（18cls_yolo11n/best.pt）只认识 18 类家具，
    **根本不认识 person/cat/dog/bird** —— 用它预标注活体会得到一片空白。
    weights/yolo11n.pt 是 COCO 80 类权重，原生就认识这 4 类活体。

----------------------------------------------------------------
 实测精度（coco128 的 62 张含 person 图，conf=0.15, imgsz=640）
----------------------------------------------------------------
    TP=219  FP=69  FN=64
    召回 0.774    精确 0.760    平均 IoU 0.840

    意味着：自动标出来的框里约 3/4 是对的且位置够准（IoU>0.8 一般不用
    手动微调），另外约 1/4 的目标会漏掉要人补。所以定位是
    「省掉大部分画框动作」，不是全自动。

----------------------------------------------------------------
 类别映射
----------------------------------------------------------------
    COCO 80 类  ->  本工程 19 类
         0 person  ->  18

    cat/dog/bird 已从类别表删除（样本量 4/9/16 个框，训不出东西，
    具体原因见 living_semantics.py 里的注释），所以 COCO 的
    14/15/16 也一并丢弃。

    其余 76 类（car/chair/bottle...）直接丢弃 —— 它们的语义和你的
    家具类（stool/table/cabinet）不是一回事，混进来会污染标注。

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import CLASS_NAMES  # noqa: E402

# COCO 80 类的 id -> 本工程 19 类的 id
#
# 只映射 person。cat/dog/bird 已从类别表里删掉（原因见 living_semantics.py）：
# 原来留着 14/15/16 -> 19/20/21，但本工程只有 19 类，写出来的 19~21 就是
# 越界 id，build 时会被当成"丢弃越界类"，白标一场。
COCO_TO_OURS: dict[int, int] = {
    0:  CLASS_NAMES.index("person"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="图片目录")
    ap.add_argument("--model", default=str(PROJECT / "weights" / "yolo11n.pt"),
                    help="COCO 预训练权重。n 快，l/x 准，标注建议用大的")
    ap.add_argument("--conf", type=float, default=0.15,
                    help="置信度阈值。标注要偏低多召回，默认 0.15")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--augment", action="store_true",
                    help="TTA 增强推理，召回略高但慢 3 倍")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    ap.add_argument("--preview", default=None,
                    help="生成一张预览拼图，路径如 D:\\tmp\\preview.jpg")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print(f"[!] {src} 不存在")
        return 2
    files = sorted(list(src.glob("*.jpg")) + list(src.glob("*.png")))
    if not files:
        print(f"[!] {src} 里没有 jpg/png")
        return 2

    import cv2
    import numpy as np
    from ultralytics import YOLO

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = PROJECT / model_path
    if not model_path.exists():
        print(f"[!] 权重不存在：{model_path}")
        print("    可用 weights/yolo11n.pt，或先下载：")
        print("    curl -L -o weights/yolo11x.pt "
              "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11x.pt")
        return 2

    print("=" * 62)
    print("自动预标注（COCO 权重）")
    print("=" * 62)
    print(f"  图片   : {src}  共 {len(files)} 张")
    print(f"  模型   : {model_path.name}")
    print(f"  参数   : conf={args.conf} imgsz={args.imgsz} "
          f"{'TTA' if args.augment else ''}")
    print(f"  类别   : person/bird/cat/dog -> "
          f"{sorted(COCO_TO_OURS.values())} "
          f"({', '.join(CLASS_NAMES[i] for i in sorted(COCO_TO_OURS.values()))})")
    print(f"  模式   : {'dry-run' if args.dry_run else '写盘'}")
    print()

    model = YOLO(str(model_path))
    t0 = time.time()
    n_box = 0
    n_img = 0
    n_empty = 0
    per_cls: dict[int, int] = {}
    preview_tiles: list = []

    for k, p in enumerate(files, 1):
        im = cv2.imread(str(p))
        if im is None:
            continue
        r = model.predict(im, conf=args.conf, iou=args.iou, imgsz=args.imgsz,
                          augment=args.augment, verbose=False)[0]
        h, w = im.shape[:2]
        lines = []
        for b, c in zip(r.boxes.xyxy.cpu().numpy(),
                        r.boxes.cls.cpu().numpy().astype(int)):
            c = int(c)
            if c not in COCO_TO_OURS:
                continue
            ours = COCO_TO_OURS[c]
            x1, y1, x2, y2 = [float(v) for v in b]
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(w), x2), min(float(h), y2)
            if x2 - x1 < 3 or y2 - y1 < 3:
                continue
            lines.append(f"{ours} {((x1+x2)/2)/w:.6f} {((y1+y2)/2)/h:.6f} "
                         f"{(x2-x1)/w:.6f} {(y2-y1)/h:.6f}")
            per_cls[ours] = per_cls.get(ours, 0) + 1

        if lines:
            n_img += 1
            n_box += len(lines)
            if not args.dry_run:
                lab = src.parent / "labels" / f"{p.stem}.txt"
                lab.parent.mkdir(parents=True, exist_ok=True)
                lab.write_text("\n".join(lines) + "\n")
            if args.preview and len(preview_tiles) < 6:
                vis = im.copy()
                for ln in lines:
                    q = ln.split()
                    cx, cy, bw, bh = [float(v) for v in q[1:5]]
                    a1 = int((cx-bw/2)*w); b1 = int((cy-bh/2)*h)
                    a2 = int((cx+bw/2)*w); b2 = int((cy+bh/2)*h)
                    cv2.rectangle(vis, (a1, b1), (a2, b2), (0, 0, 255), 2)
                    cv2.putText(vis, CLASS_NAMES[int(q[0])], (a1, max(12, b1-4)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                preview_tiles.append(cv2.resize(vis, (400, 300)))
        else:
            n_empty += 1

        if k % 50 == 0:
            print(f"  {k}/{len(files)}  {time.time()-t0:.0f}s  已标 {n_box} 框",
                  flush=True)

    print()
    print("=" * 62)
    print(f"  处理 {len(files)} 张，用时 {time.time()-t0:.0f}s")
    print(f"  有目标 {n_img} 张 / 无目标 {n_empty} 张，共 {n_box} 个框")
    for cid in sorted(per_cls):
        print(f"    {cid:2d} {CLASS_NAMES[cid]:<8s} {per_cls[cid]:5d}")
    if args.dry_run:
        print("  dry-run，未写任何文件")
    else:
        print(f"  标签写到 {src.parent / 'labels'}")
        print(f"  下一步：python tools\\review_labels.py --src {src}")
    print("=" * 62)

    if args.preview and preview_tiles:
        rows = []
        for i in range(0, len(preview_tiles), 2):
            pair = preview_tiles[i:i+2]
            if len(pair) == 1:
                pair.append(np.full_like(pair[0], 40))
            rows.append(np.hstack(pair))
        out = np.vstack(rows)
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(args.preview, out)
        print(f"  预览图已存 {args.preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
