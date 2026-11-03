# -*- coding: utf-8 -*-
"""长视频找人片段：顺序解码，抽样跑检测，输出"有人"的时间区间。

坑：这类 HDR/长视频 CAP_PROP_POS_FRAMES seek 不准，必须顺序 read()。
"""
import sys, json
import cv2
import numpy as np
import onnxruntime as ort

MODEL = r"D:\FPGA\ultralytics\runs\livingB_yolo11n\weights\best.onnx"
PERSON = 18


def infer(sess, im, ct=0.25, it=0.45):
    h0, w0 = im.shape[:2]
    r = min(640 / w0, 640 / h0)
    nw, nh = int(w0 * r), int(h0 * r)
    cv = np.full((640, 640, 3), 114, np.uint8)
    dx, dy = (640 - nw) // 2, (640 - nh) // 2
    cv[dy:dy + nh, dx:dx + nw] = cv2.resize(im, (nw, nh))
    x = cv[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
    o = sess.run(None, {"images": x})[0][0]
    sc = o[4:, :]
    cls = sc.argmax(0)
    conf = sc.max(0)
    k = np.where(conf > ct)[0]
    if len(k) == 0:
        return []
    cx, cy, bw, bh = o[0, k], o[1, k], o[2, k], o[3, k]
    box = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], 1)
    idx = [int(i) for i in np.array(
        cv2.dnn.NMSBoxes(box.tolist(), conf[k].tolist(), ct, it)).flatten()]
    out = []
    for i in idx:
        b = [float(v) for v in box[i]]
        out.append(dict(c=int(cls[k[i]]), conf=float(conf[k[i]]),
                        x1=round((b[0] - dx) / r, 1), y1=round((b[1] - dy) / r, 1),
                        w=round(b[2] / r, 1), h=round(b[3] / r, 1)))
    return out


def main():
    src = sys.argv[1]
    out_json = sys.argv[2]
    every = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps * every)))
    i = 0
    rec = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            d = infer(sess, fr)
            per = [x for x in d if x["c"] == PERSON]
            per.sort(key=lambda z: -z["conf"])
            rec.append(dict(t=round(i / fps, 2),
                            n=len(per),
                            conf=round(per[0]["conf"], 3) if per else 0.0,
                            h=round(per[0]["h"]) if per else 0,
                            nall=len(d)))
        i += 1
    json.dump(rec, open(out_json, "w"), ensure_ascii=False)
    print("frames", i, "sampled", len(rec), "->", out_json)


if __name__ == "__main__":
    main()
