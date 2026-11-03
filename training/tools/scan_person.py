# -*- coding: utf-8 -*-
"""
============================================================
  扫描数据集里"有活体但没标注"的图 —— 找出被当成负样本的人
============================================================

为什么需要这个脚本：
    18cls 数据集（8884 train + 2220 val，扫地机器人低视角）是纯非活体
    数据集，它的标注里没有 person。但实际上**里面本来就有人**——实测
    val 集 2220 张里就有 263 张被 COCO 权重检出人体（11.8%）。

    这些图如果原样进训练，就是"图里明明站着人，标签却说他不在"。
    模型每看一次就被惩罚一次，最后学到的是"人这种东西不算目标"。
    表现是：person 召回上不去，而且越训越差；val 上 18 类指标看着
    还行（因为人本来就少），实车却是走到人跟前也不停。

    所以要先找出这批图，再决定是补标还是剔除。

怎么用：

    # 扫描 + 生成预览（只看前 24 张有检出的）
    python tools\\scan_person.py --src datasets\\18cls\\images\\val --out val_scan.json

    # 多个目录一次扫（结果分开记）
    python tools\\scan_person.py --src datasets\\18cls\\images\\train --out train_scan.json

    # 只看 JSON 里命中多少、尺度分布
    python tools\\scan_person.py --report val_scan.json

产物：
    JSON: {图片路径: [{cls, conf, cx, cy, w, h}, ...]}，只记有检出的图
    预览: <out>_preview.jpg

关于阈值：
    conf 默认 0.15（和标注时一致，宁可多召回让后面人工删）。
    如果只是想知道"大概有多少张有人"，可以用 0.35 看高置信那批。
============================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ.pop("PYTHONPATH", None)

import cv2                                    # noqa: E402
import numpy as np                            # noqa: E402

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from living_semantics import CLASS_NAMES       # noqa: E402

# COCO 80 类 id -> 本工程 id
COCO_MAP = {0: 18, 14: 21, 15: 19, 16: 20}
LIVE_IDS = set(COCO_MAP.values())

DEFAULT_MODEL = PROJECT / "weights" / "yolo11n.pt"


def report(path: Path) -> int:
    """只看已有的扫描结果，不再跑推理。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    n_img = len(data)
    per_cls: dict[int, int] = {}
    sizes = []
    confs = []
    for boxes in data.values():
        for b in boxes:
            per_cls[b["cls"]] = per_cls.get(b["cls"], 0) + 1
            sizes.append(b["w"] * b["h"])
            confs.append(b["conf"])
    print(f"文件 {path}")
    print(f"  含活体的图 {n_img} 张")
    for c in sorted(per_cls):
        print(f"    {CLASS_NAMES[c]:<8} id={c:<3} {per_cls[c]:5d} 框")
    if sizes:
        s = np.array(sizes)
        cf = np.array(confs)
        print(f"  尺度(面积占比) p10={np.percentile(s, 10):.3f} "
              f"p50={np.percentile(s, 50):.3f} p90={np.percentile(s, 90):.3f}")
        print(f"  置信度 p10={np.percentile(cf, 10):.2f} "
              f"p50={np.percentile(cf, 50):.2f} p90={np.percentile(cf, 90):.2f}")
        for lo, hi, name in [(0.35, 1.01, "conf>=0.35 高置信"),
                             (0.25, 0.35, "0.25~0.35"),
                             (0.15, 0.25, "0.15~0.25 低置信")]:
            m = (cf >= lo) & (cf < hi)
            print(f"    {name:<18} {int(m.sum()):5d} 框 / "
                  f"{len({k for k, v in data.items() if any(lo <= b['conf'] < hi for b in v)})} 张图")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描数据集里未标注的活体")
    ap.add_argument("--src", default=None, help="图片目录")
    ap.add_argument("--out", default=None, help="输出 JSON 路径")
    ap.add_argument("--report", default=None, help="只打印已有 JSON 的统计")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--preview", type=int, default=24, help="预览图放几张，0=不出")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张（调试用）")
    args = ap.parse_args()

    if args.report:
        return report(Path(args.report))
    if not args.src or not args.out:
        ap.error("要么给 --report，要么同时给 --src 和 --out")

    src = Path(args.src)
    files = sorted(p for p in src.rglob("*")
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"})
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"[!] {src} 下没有图片")
        return 1

    from ultralytics import YOLO
    model = YOLO(str(args.model))
    names = model.names
    print("=" * 62)
    print(f"  扫描 {src}")
    print(f"  {len(files)} 张  conf={args.conf}  imgsz={args.imgsz}  "
          f"batch={args.batch}")
    print("=" * 62)

    hits: dict[str, list[dict]] = {}
    preview_imgs: list[tuple[Path, list[dict]]] = []
    t0 = time.time()
    done = 0
    for i in range(0, len(files), args.batch):
        chunk = [str(p) for p in files[i:i + args.batch]]
        res = model.predict(chunk, conf=args.conf, imgsz=args.imgsz,
                            verbose=False, stream=False)
        for p, r in zip(files[i:i + args.batch], res):
            boxes = []
            if r.boxes is not None and len(r.boxes):
                xywhn = r.boxes.xywhn.cpu().numpy()
                clses = r.boxes.cls.cpu().numpy().astype(int)
                confs = r.boxes.conf.cpu().numpy()
                for (cx, cy, bw, bh), c, cf in zip(xywhn, clses, confs):
                    if int(c) not in COCO_MAP:
                        continue
                    boxes.append({"cls": COCO_MAP[int(c)], "conf": round(float(cf), 4),
                                  "cx": float(cx), "cy": float(cy),
                                  "w": float(bw), "h": float(bh),
                                  "src_name": names[int(c)]})
            if boxes:
                hits[str(p)] = boxes
                if args.preview and len(preview_imgs) < args.preview:
                    preview_imgs.append((p, boxes))
            done += 1
        if (i // args.batch) % 20 == 0:
            print(f"  {done}/{len(files)}  {time.time() - t0:.0f}s  "
                  f"命中 {len(hits)} 张", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(f"  扫完 {len(files)} 张，用时 {time.time() - t0:.0f}s")
    print(f"  含活体 {len(hits)} 张（{len(hits) / len(files) * 100:.1f}%），"
          f"共 {sum(len(v) for v in hits.values())} 框")
    print(f"  -> {out}")

    if preview_imgs:
        tiles = []
        for p, boxes in preview_imgs:
            im = cv2.imread(str(p))
            if im is None:
                continue
            h, w = im.shape[:2]
            for b in boxes:
                x1 = int((b["cx"] - b["w"] / 2) * w)
                y1 = int((b["cy"] - b["h"] / 2) * h)
                x2 = int((b["cx"] + b["w"] / 2) * w)
                y2 = int((b["cy"] + b["h"] / 2) * h)
                cv2.rectangle(im, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(im, "%s %.2f" % (CLASS_NAMES[b["cls"]], b["conf"]),
                            (x1, max(y1 - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, (0, 0, 255), 1)
            t = cv2.resize(im, (320, 240))
            cv2.putText(t, p.name[:18], (4, 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 255), 1)
            tiles.append(t)
        while len(tiles) % 4:
            tiles.append(np.zeros((240, 320, 3), np.uint8))
        rows = [np.hstack(tiles[j:j + 4]) for j in range(0, len(tiles), 4)]
        pv = out.with_name(out.stem + "_preview.jpg")
        cv2.imwrite(str(pv), np.vstack(rows))
        print(f"  预览 {pv}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
