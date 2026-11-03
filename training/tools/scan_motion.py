# -*- coding: utf-8 -*-
"""固定机位长视频：用"时间中值背景差"找出有活动目标的片段。

为什么不能只靠检测器：模型会漏远处小目标，而这些恰恰是"有人"的片段。
固定机位下背景差更可靠，而且地面那团静止线缆不会被算成活动目标。

用法：
    python tools/scan_motion.py <video> <out.json> [采样间隔秒]
"""
import sys
import json
import cv2
import numpy as np

SMALL = (320, 180)


def main():
    src, out = sys.argv[1], sys.argv[2]
    every = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps * every)))

    # ---- pass 1: 取缩略灰度帧，算时间中值背景
    frames, times = [], []
    i = 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            g = cv2.cvtColor(cv2.resize(fr, SMALL, interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY)
            frames.append(g)
            times.append(i / fps)
        i += 1
    print("pass1 frames:", len(frames))
    bg = np.median(np.stack(frames), axis=0).astype(np.uint8)

    # ---- pass 2: 背景差，统计活动像素
    rec = []
    for g, t in zip(frames, times):
        d = cv2.absdiff(g, bg)
        m = (d > 28).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        area = 0
        bb = None
        for k in range(1, n):
            a = int(stats[k, cv2.CC_STAT_AREA])
            if a < 40:            # 噪声
                continue
            area += a
            x, y, w, h = (int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP]),
                          int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT]))
            if bb is None:
                bb = [x, y, x + w, y + h]
            else:
                bb = [min(bb[0], x), min(bb[1], y), max(bb[2], x + w), max(bb[3], y + h)]
        rec.append(dict(t=round(t, 2), area=area, bb=bb))

    json.dump(rec, open(out, "w"), ensure_ascii=False)
    cv2.imwrite(out.replace(".json", "_bg.jpg"), bg)
    print("pass2 recs:", len(rec), "->", out)


if __name__ == "__main__":
    main()
