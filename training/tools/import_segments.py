# -*- coding: utf-8 -*-
"""固定机位长视频 → 有人片段抽帧 + 4:3 裁切 + 线缆补掉 → 导入暂存。

流程：
  1. 读背景差/检测扫描出的"有人"时间区间（用 0.5s 的采样判定，往前 1.0s、
     往后 1.0s 留裕量）。
  2. 顺序解码，只在这些区间内按 interval 抽帧（顺序解码，不 seek）。
  3. 中心裁 4:3（1280x720 → 960x720）。
  4. 左下角线缆整块用邻近纹理 inpaint 补掉（不是裁掉，几何不变）。
  5. 缩放到 1280x960，按 prefix 写 jpg；同名 .txt 占位（空标签）。

产物：<out>/images/*.jpg + <out>/labels/*.txt + meta.json
线缆区域：本段视频固定机位，线缆外接框 [0,180,596,663]（在 1280x720 下）。
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cable_bbox(w=1280, h=720):
    """线缆整块（含线圈 + 左边缘延伸 + 左上翘起）在 720p 原图上的外接框。"""
    # 实测外接框，四周各外扩一点保证盖全
    return (0, 170, 620, h - 40)   # x0,y0,x1,y1


def inpaint_cable(bgr, bbox):
    """用邻近纹理把线缆块补掉（inpaint），不改画面几何。"""
    x0, y0, x1, y1 = bbox
    mask = np.zeros(bgr.shape[:2], np.uint8)
    mask[y0:y1, x0:x1] = 255
    # 先轻微膨胀，确保边缘黑像素被一起盖掉
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=1)
    return cv2.inpaint(bgr, mask, 7, cv2.INPAINT_TELEA)


def to_crop43(bgr):
    h, w = bgr.shape[:2]
    want_w = h * 4 // 3
    if w > want_w:
        x0 = (w - want_w) // 2
        return bgr[:, x0:x0 + want_w]
    return bgr


def load_runs(runs_path):
    if not runs_path or not Path(runs_path).exists():
        return None
    return json.load(open(runs_path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--segments", required=True,
                    help="有人时间区间 json（[[start,end],...] 秒）")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--margin", type=float, default=1.0,
                    help="区间前后各留多少秒裕量")
    ap.add_argument("--prefix", default="v2")
    ap.add_argument("--size", default="1280x960")
    ap.add_argument("--no-clean-cable", action="store_true")
    ap.add_argument("--min-gap", type=float, default=2.0,
                    help="相邻区间间距小于此值则合并")
    args = ap.parse_args()

    ow, oh = map(int, args.size.split("x"))
    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    segs = load_runs(args.segments)
    if segs is None:
        print("无法读取区间文件", args.segments)
        return 1

    # 合并相邻 + 加裕量
    segs = sorted(segs)
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] <= args.min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    span = []
    for a, b in merged:
        span.append([max(0.0, a - args.margin), b + args.margin])

    cap = cv2.VideoCapture(args.src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps * args.interval)))

    def in_span(t):
        return any(a <= t <= b for a, b in span)

    n_out = 0
    i = 0
    saved_at = set()
    meta = {"src": args.src, "fps": fps, "interval": args.interval,
            "segments": span, "prefix": args.prefix, "size": args.size,
            "frames": []}
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = i / fps
        if in_span(t) and i % step == 0:
            # 4:3 裁切
            fr = to_crop43(fr)
            if not args.no_clean_cable:
                bbox = cable_bbox(fr.shape[1], fr.shape[0])
                fr = inpaint_cable(fr, bbox)
            # 统一到输出尺寸
            if (fr.shape[1], fr.shape[0]) != (ow, oh):
                fr = cv2.resize(fr, (ow, oh), interpolation=cv2.INTER_AREA)
            name = f"{args.prefix}_{i:06d}"
            cv2.imwrite(str(out / "images" / f"{name}.jpg"), fr)
            (out / "labels" / f"{name}.txt").write_text("", encoding="utf-8")
            meta["frames"].append({"name": name, "t": round(t, 2)})
            n_out += 1
        i += 1

    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"导出 {n_out} 张 -> {out}  区间 {len(span)} 段 共 "
          f"{sum(b - a for a, b in span):.1f}s")


if __name__ == "__main__":
    main()
