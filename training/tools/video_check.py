# -*- coding: utf-8 -*-
"""
============================================================
 用真实手机视频对比两个模型的识别效果
============================================================

检查的不是"mAP 多少"，而是三件在车上真正决定行为的事：
    ① 站在 3~4m 外的人，能不能在**撞上之前**发现（远景召回）
    ② 只露出腿/拖鞋的人，会不会被漏掉（近景召回）
    ③ 画面里的非人东西（挂衣、行李箱、窗帘）会不会被误叫人（误报）

做法：从每条视频里按固定间隔抽帧，两个模型分别推理，然后
    - 统计每帧检出/未检出、置信度、框高（框高 ≈ 距离的倒数）
    - 把"两模型结论不一致"和"都检出"的帧拼成三联图（原图 | A | B）

注意：这些视频抽出的帧有一部分进过训练集，所以绝对数字偏乐观。
      真正的泛化能力要看同一视频里**没进训练集**的帧，脚本默认
      用 offset 采样（间隔取半）来尽量避免和训练帧重合，但同一条
      视频相邻秒的画面本身就很像，只能算"半独立"。

用法
----------------------------------------------------------------
    python tools/video_check.py --a runs/living_yolo11n/weights/best.pt \
        --b runs/livingB_yolo11n/weights/best.pt \
        --videos "C:/Users/shang/Downloads/VID_20261029_073610_DOLBY.mp4" \
        --out runs/video_check
    # --videos 可以给多个文件或一个目录

作者：欧阳博怀    日期：2026-10-29
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent

# 19 类里 person 排在最后（0..17 是家具/门，见 tools/living_semantics.py），
# 写成 0 会去筛 trashcan，永远检不到人。
PERSON_CLS = 18


def grab_frames(video: Path, interval: float, size=(640, 480)) -> list[tuple[float, np.ndarray]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"  [!] 打不开 {video.name}")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = n / fps if fps else 0
    step = max(1, int(round(fps * interval)))
    out = []
    t = 0.0
    # 从半个间隔开始，避开 import_phone 按 0 秒起点抽的那些帧
    idx = step // 2
    while idx < n:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = cap.read()
        if not ok or fr is None:
            break
        h, w = fr.shape[:2]
        # 与训练一致：中心裁 4:3 再缩到 640x480
        if w / h > 4 / 3 + 0.01:
            nw = int(h * 4 / 3)
            x0 = (w - nw) // 2
            fr = fr[:, x0:x0 + nw]
        fr = cv2.resize(fr, size, interpolation=cv2.INTER_AREA)
        out.append((idx / fps, fr))
        idx += step
    cap.release()
    print(f"  {video.name}: {dur:.1f}s, 抽 {len(out)} 帧 (每 {interval}s)")
    return out


def run_model(weights: Path, frames: list[np.ndarray], conf: float, device: str):
    from ultralytics import YOLO
    model = YOLO(str(weights))
    res = []
    # 必须手动分块：predict(source=list) 会把整份帧一次性读进内存再 stack，
    # 几百帧 640x480 就吃掉几个 G。
    CHUNK = 16
    for i in range(0, len(frames), CHUNK):
        for r in model.predict(source=frames[i:i + CHUNK], stream=True, imgsz=640,
                               conf=conf, iou=0.6, classes=[PERSON_CLS], max_det=20,
                               verbose=False, device=device, batch=8):
            b = []
            if r.boxes is not None and len(r.boxes):
                xywhn = r.boxes.xywhn.cpu().numpy()
                cf = r.boxes.conf.cpu().numpy()
                for (cx, cy, w, h), c in zip(xywhn, cf):
                    b.append({"cx": float(cx), "cy": float(cy), "w": float(w),
                              "h": float(h), "conf": float(c)})
            res.append(b)
    return res


def draw(img: np.ndarray, boxes: list, tag: str, color=(0, 255, 0)) -> np.ndarray:
    out = img.copy()
    h, w = out.shape[:2]
    for b in boxes:
        x1 = int((b["cx"] - b["w"] / 2) * w)
        y1 = int((b["cy"] - b["h"] / 2) * h)
        x2 = int((b["cx"] + b["w"] / 2) * w)
        y2 = int((b["cy"] + b["h"] / 2) * h)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        cv2.putText(out, "%.2f" % b["conf"], (x1, max(y1 - 4, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    cv2.putText(out, tag, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="模型 A 权重")
    ap.add_argument("--b", required=True, help="模型 B 权重")
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=str(PROJECT / "runs" / "video_check"))
    ap.add_argument("--max-pages", type=int, default=8, help="每条视频最多出几页三联图")
    args = ap.parse_args()

    os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ.pop("PYTHONPATH", None)

    files: list[Path] = []
    for v in args.videos:
        p = Path(v)
        if p.is_dir():
            files += sorted(list(p.glob("*.mp4")) + list(p.glob("*.MP4")))
        else:
            files.append(p)
    files = [f for f in files if f.exists()]
    if not files:
        print("没有可用的视频")
        return 1

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    summary = []

    for video in files:
        print(f"\n=== {video.name} ===")
        frames = grab_frames(video, args.interval)
        if not frames:
            continue
        imgs = [f[1] for f in frames]
        print("  A 推理 ...")
        ra = run_model(Path(args.a), imgs, args.conf, args.device)
        print("  B 推理 ...")
        rb = run_model(Path(args.b), imgs, args.conf, args.device)

        stat = {
            "video": video.name, "frames": len(frames),
            "a_hit": sum(1 for x in ra if x), "b_hit": sum(1 for x in rb if x),
            "a_conf": round(float(np.mean([max(b["conf"] for b in x) for x in ra if x])), 3) if any(ra) else 0.0,
            "b_conf": round(float(np.mean([max(b["conf"] for b in x) for x in rb if x])), 3) if any(rb) else 0.0,
            "a_hmax": round(float(np.max([max(b["h"] for b in x) for x in ra if x])), 3) if any(ra) else 0.0,
            "b_hmax": round(float(np.max([max(b["h"] for b in x) for x in rb if x])), 3) if any(rb) else 0.0,
            "a_boxs": int(sum(len(x) for x in ra)),
            "b_boxs": int(sum(len(x) for x in rb)),
            "disagree": int(sum(1 for x, y in zip(ra, rb) if bool(x) != bool(y))),
        }
        print(f"  帧 {stat['frames']} | A 检出 {stat['a_hit']} 帧/{stat['a_boxs']} 框 | "
              f"B 检出 {stat['b_hit']} 帧/{stat['b_boxs']} 框 | 不一致 {stat['disagree']} 帧")
        summary.append(stat)

        # 三联图：优先"不一致"和"都检出"的帧
        order = sorted(range(len(frames)),
                       key=lambda i: (bool(ra[i]) == bool(rb[i]),
                                      -max([b["conf"] for b in ra[i]] or [0])))
        pick = [i for i in order if ra[i] or rb[i]][: args.max_pages * 6]
        for pg in range(0, len(pick), 6):
            tiles = []
            for i in pick[pg:pg + 6]:
                t, img = frames[i]
                a = draw(img, ra[i], "A %.2fs n=%d" % (t, len(ra[i])))
                b = draw(img, rb[i], "B %.2fs n=%d" % (t, len(rb[i])), (0, 165, 255))
                tiles.append(np.hstack([a, b]))
            while len(tiles) < 6:
                tiles.append(np.zeros((480, 1280, 3), np.uint8))
            grid = np.vstack(tiles[:3])
            grid2 = np.vstack(tiles[3:6])
            page = pg // 6 + 1
            cv2.imwrite(str(outdir / f"{video.stem[:22]}_p{page}.jpg"), np.vstack([grid, grid2]))

    (outdir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== 汇总 ===")
    tot_a = sum(s["a_hit"] for s in summary)
    tot_b = sum(s["b_hit"] for s in summary)
    tot_f = sum(s["frames"] for s in summary)
    print(f"  共 {tot_f} 帧：A 检出 {tot_a} ({tot_a / tot_f * 100:.1f}%)，"
          f"B 检出 {tot_b} ({tot_b / tot_f * 100:.1f}%)")
    print(f"  -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
