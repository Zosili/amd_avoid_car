# -*- coding: utf-8 -*-
"""
================================================================
 低视角合成增强 —— 把「全身 person」改造成「只有下半身」
================================================================

⚠ 实测结论（2026-10-28，重要，别跳过）：
    在 coco128 上实测：61 张含 person 的图里，只有 6 张能通过几何过滤，
    而这 6 张里只有 1 张（1/61）真的生成出"看腿"的效果，其余变成了
    头发特写、手的特写、猫头特写。

    根本原因不是脚本 bug，是数据的性质：COCO 里大量 person 是近景半身
    （坐着、蹲着、被桌子挡住），框本身就只覆盖上半身。它们的几何特征
    （瘦高、够大、底边靠下）和"站立全身"完全一样，几何上无法区分。

    → 所以不要指望用这个脚本把公开数据集转换成低视角数据。产能太低。

    真正有效的做法是「直接使用蹲/坐姿态的样本」：低视角下摄像头离地
    20cm，人一蹲反而更容易拍到全身。这些样本 COCO 里现成就有，
    不需要合成 —— 见下面「为什么必须做这件事」一节的修正说明。

    这个脚本现在的定位：只对**你自己拍的**站立全身照有用（给少量实拍
    数据扩增变体）。对混合姿态的数据不要开。

为什么要做这件事（原始动机，仍然成立）：
    公开数据集里的人很多是行人视角的全身照（相机高 1.5m 左右）。
    你的 OV5640 装在车上，离地十几二十厘米，人站着走过来时画面里
    只有两条腿和裤脚。模型学到的"头+肩+躯干轮廓"在低视角下不存在，
    检测置信度会掉到 0.1 以下。

    但这个问题有一半解法更省事：**不要把"站立全身"当唯一正样本**。
    蹲着的人、坐在地上的人、趴着的猫，在低视角下反而更完整地出现在
    画面里。把活体定义成「任何姿态的人 + 动物」，直接喂混合姿态的
    样本，比强行合成"腿"样本有效得多。

标签怎么处理：
    裁切后，框被部分裁掉的按几何收缩；完全跑到画面外的直接丢弃。
    透视变换后的框用四个角点变换再取外接矩形 —— 会有轻微虚胖，
    但在这种"只要别漏人"的场景里，宁大勿小是对的。

用法（在 D:\\FPGA\\ultralytics 下）：
    # 给 living 数据集里所有 person 样本各生成 2 个低视角变体
    python tools\\lowangle_aug.py

    # 只看看会做什么，不落盘
    python tools\\lowangle_aug.py --dry-run

    # 调整力度
    python tools\\lowangle_aug.py --copies 3 --keep 0.40,0.65 --persp 0.03

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import CLASS_NAMES  # noqa: E402

PERSON_ID = CLASS_NAMES.index("person")
DEFAULT_SRC = PROJECT / "datasets" / "living"


# ---------------------------------------------------------------- 几何
def clip_box(b, w, h):
    """把框裁到画面内，完全在外则返回 None。"""
    x1, y1, x2, y2 = b
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(w), x2), min(float(h), y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return [x1, y1, x2, y2]


def transform_boxes(boxes, M, out_w, out_h):
    """用 3x3 变换矩阵把像素框搬到新图，返回 (新框列表, 保留索引)。"""
    out, keep = [], []
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        pts = np.array([[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]], np.float32)
        q = cv2.perspectiveTransform(pts, M).reshape(-1, 2)
        nb = [q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()]
        nb = clip_box(nb, out_w, out_h)
        if nb is None:
            continue
        # 变换后面积萎缩超过 85% 的说明基本跑出画面了，丢掉
        a0 = (x2 - x1) * (y2 - y1)
        a1 = (nb[2] - nb[0]) * (nb[3] - nb[1])
        if a0 > 1 and a1 / a0 < 0.15:
            continue
        out.append(nb)
        keep.append(i)
    return out, keep


# ---------------------------------------------------------------- 单张增强
def low_angle(im, boxes, cls, keep_ratio, expand, persp, rng):
    """对一张图做一次低视角改造。返回 (新图, 新框, 新类) 或 None。"""
    h, w = im.shape[:2]
    person_idx = [i for i, c in enumerate(cls) if c == PERSON_ID]
    if not person_idx:
        return None

    # 以最大的 person 框为主体 —— 它最可能是"走近的那个人"
    areas = [(boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]) for i in person_idx]
    main = person_idx[int(np.argmax(areas))]
    x1, y1, x2, y2 = boxes[main]
    bw, bh = x2 - x1, y2 - y1
    if bw < 12 or bh < 24:
        return None

    # ============================================================
    # 只对「像站立全身照」的框做增强。这一条是踩过坑之后加的：
    #
    # COCO 对遮挡目标只标可见部分。一个人坐在桌边、下半身被桌子挡住时，
    # 框本身就只覆盖上半身 —— 图里根本没有腿，这时候取"框的下部"
    # 只能取到脸和头发，生成出来的是一堆"头发特写"，纯噪声。
    #
    # 三个条件一起用，宁可漏掉也不要造垃圾：
    #   ① 瘦高：站立的人 bw/bh 通常 < 0.6（坐下、横躺会明显更方）
    #   ② 够大：框高占画面 30% 以上，排除远景小人（那种裁出来全是糊的）
    #   ③ 底边落在画面中下部：说明脚（或至少膝盖）在画面里
    # ============================================================
    ar = bw / bh
    if ar > 0.62:
        return None
    if bh < 0.30 * h:
        return None
    if not (0.55 * h <= y2 <= 0.995 * h):
        return None
    # 左右贴边说明人只进来半个，裁出来框会缺一块
    if x1 < 2 or x2 > w - 2:
        return None

    # ① 取框底下 keep_ratio 那一段，左右各扩 expand 倍
    top = y2 - bh * keep_ratio
    cx = (x1 + x2) / 2
    half = bw * expand / 2
    sx1, sx2 = cx - half, cx + half
    sy1, sy2 = top, y2

    # 注意：这里**不做**宽高比修正。
    # 早先版本为了凑 640:480 会把窗口向上扩，结果当 person 框接近方形
    # （坐着的人、或标注偏大）时一路扩到头顶，生成出一堆"头发特写"。
    # 低视角下相机贴地，竖直方向本来就被透视压缩，直接非等比 resize
    # 反而更接近真实观感。窗口只由「框的下部」决定，永远不会跑到头上去。
    cw, ch = sx2 - sx1, sy2 - sy1
    if cw < 8 or ch < 8:
        return None
    M = np.array([[1, 0, -sx1], [0, 1, -sy1], [0, 0, 1]], np.float64)
    new, kept = transform_boxes(boxes, M, cw, ch)
    if not new:
        return None

    src = cv2.warpAffine(im, M[:2], (int(round(cw)), int(round(ch))),
                         flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    new_cls = [cls[i] for i in kept]
    new_h, new_w = src.shape[:2]

    # ② 仰视透视：底边保持宽，顶边收窄 —— 贴地仰拍时远处的东西会收缩
    if persp > 0:
        k = rng.uniform(0.4, 1.0) * persp          # 实际收缩比例
        d = new_w * k / 2
        src_pts = np.float32([[0, 0], [new_w, 0], [new_w, new_h], [0, new_h]])
        dst_pts = np.float32([[d, 0], [new_w - d, 0], [new_w, new_h], [0, new_h]])
        P = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped = cv2.warpPerspective(src, P, (new_w, new_h),
                                     flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        nb, kept2 = transform_boxes(new, P, new_w, new_h)
        if nb:
            new, new_cls, src = nb, [new_cls[i] for i in kept2], warped

    # 统一回原始尺寸（模型的输入尺寸没变，这一步不能省）
    fx, fy = w / src.shape[1], h / src.shape[0]
    src = cv2.resize(src, (w, h), interpolation=cv2.INTER_LINEAR)
    new = [[b[0] * fx, b[1] * fy, b[2] * fx, b[3] * fy] for b in new]
    return src, new, new_cls


# ---------------------------------------------------------------- 文件 IO
def read_labels(p: Path):
    boxes, cls = [], []
    if not p.exists():
        return boxes, cls
    for line in p.read_text().splitlines():
        q = line.split()
        if len(q) < 5:
            continue
        cls.append(int(float(q[0])))
        boxes.append([float(v) for v in q[1:5]])     # 归一化
    return boxes, cls


def write_labels(p: Path, boxes, cls, w, h):
    lines = []
    for b, c in zip(boxes, cls):
        cx = (b[0] + b[2]) / 2 / w
        cy = (b[1] + b[3]) / 2 / h
        bw = (b[2] - b[0]) / w
        bh = (b[3] - b[1]) / h
        cx, cy = min(max(cx, 0), 1), min(max(cy, 0), 1)
        bw, bh = min(bw, 1), min(bh, 1)
        if bw <= 0.002 or bh <= 0.002:
            continue
        lines.append(f"{c} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    p.write_text("\n".join(lines) + ("\n" if lines else ""))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="数据集根目录")
    ap.add_argument("--splits", default="train", help="要处理的 split，逗号分隔")
    ap.add_argument("--copies", type=int, default=2, help="每张 person 图生成几个变体")
    ap.add_argument("--keep", default="0.45,0.70",
                    help="保留框下部的高度比例范围，默认 0.45~0.70")
    ap.add_argument("--expand", type=float, default=1.6,
                    help="左右扩展倍数，越大画面越偏「贴脸看腿」")
    ap.add_argument("--persp", type=float, default=0.02,
                    help="仰视透视强度，0=关闭，0.02 温和，0.05 很夸张")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    args = ap.parse_args()

    root = Path(args.src)
    k_lo, k_hi = [float(v) for v in args.keep.split(",")]
    rng = random.Random(args.seed)

    print("=" * 62)
    print("低视角合成增强")
    print("=" * 62)
    print(f"  数据集 : {root}")
    print(f"  参数   : copies={args.copies} keep={k_lo}~{k_hi} "
          f"expand={args.expand} persp={args.persp}")
    print(f"  模式   : {'dry-run（不落盘）' if args.dry_run else '实际生成'}")
    print()

    grand = 0
    for split in [s.strip() for s in args.splits.split(",") if s.strip()]:
        img_dir = root / "images" / split
        lab_dir = root / "labels" / split
        if not img_dir.is_dir():
            print(f"  [!] {img_dir} 不存在，跳过")
            continue

        n_src = n_ok = n_new = n_box = 0
        for img_path in sorted(img_dir.glob("*.jpg")):
            # 已经是增强产物的不再二次增强，否则会越叠越糊
            if "_la" in img_path.stem:
                continue
            lab = lab_dir / f"{img_path.stem}.txt"
            nrm_b, cls = read_labels(lab)
            if PERSON_ID not in cls:
                continue
            n_src += 1

            im = cv2.imread(str(img_path))
            if im is None:
                continue
            h, w = im.shape[:2]
            boxes = [[b[0] * w, b[1] * h, b[2] * w, b[3] * h] for b in nrm_b]

            for k in range(args.copies):
                res = low_angle(im, boxes, cls, rng.uniform(k_lo, k_hi),
                                args.expand, args.persp, rng)
                if res is None:
                    continue
                new_im, new_boxes, new_cls = res
                n_ok += 1
                n_box += len(new_boxes)
                if args.dry_run:
                    continue
                stem = f"{img_path.stem}_la{k}"
                cv2.imwrite(str(img_dir / f"{stem}.jpg"), new_im,
                            [cv2.IMWRITE_JPEG_QUALITY, 92])
                write_labels(lab_dir / f"{stem}.txt", new_boxes, new_cls, w, h)
                n_new += 1

        grand += n_new
        print(f"  {split}: person 图 {n_src} 张 -> 生成 {n_ok} 个变体"
              f"（写盘 {n_new}），涉及 {n_box} 个框")

    if args.dry_run:
        print()
        print("  dry-run 结束，未改动任何文件。去掉 --dry-run 才会真正生成。")
    else:
        print()
        print(f"  共写入 {grand} 张增强图。重跑 build_living_dataset.py 会覆盖掉它们，")
        print("  所以增强要在合并数据集**之后**做。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
