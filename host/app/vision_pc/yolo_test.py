# -*- coding: utf-8 -*-
"""
YOLO11n 检测测试脚本
====================
用 onnxruntime 跑 yolo11n.onnx，对一张图做目标检测，画框保存结果。
当前先用 CPU 跑（NPU 需先量化，见 npu_infer 说明）。

COCO 80 类标签。
"""

import os
import numpy as np
import cv2
import onnxruntime as ort

MODEL = r"D:\FPGA\42_ov5640_udp_pc\yolo11n.onnx"
IMAGE = r"D:\FPGA\42_ov5640_udp_pc\test_frame_swap.png"

# COCO 80 类
COCO_NAMES = [
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
    "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
    "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
    "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
    "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
    "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
    "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
    "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
    "remote","keyboard","cell phone","microwave","oven","toaster","sink","refrigerator",
    "book","clock","vase","scissors","teddy bear","hair drier","toothbrush",
]

CONF_TH = 0.25     # 置信度阈值
NMS_TH  = 0.45     # NMS 阈值

def letterbox(img, size=640, stride=32):
    """等比缩放 + 灰边填充到 size×size，返回图、缩放比、padding"""
    h0, w0 = img.shape[:2]
    r = min(size / h0, size / w0)
    nh, nw = int(round(h0 * r)), int(round(w0 * r))
    # 确保是 stride 整数倍
    nh = max(stride, int(round(nh / stride) * stride))
    nw = max(stride, int(round(nw / stride) * stride))
    dh = size - nh
    dw = size - nw
    top, bottom = dh // 2, dh - dh // 2
    left, right = dw // 2, dw - dw // 2
    img = cv2.resize(img, (nw, nh))
    img = cv2.copyMakeBorder(img, top, bottom, left, right,
                             cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return img, r, (left, top)

def postprocess(pred, r, pad, conf_th, nms_th, img_h, img_w):
    """pred: [1,84,8400]，解码成 boxes+labels+score"""
    pred = pred[0].T          # [8400, 84]
    boxes = pred[:, :4]
    scores = pred[:, 4:]
    cls = scores.argmax(1)
    score = scores.max(1)

    keep = score > conf_th
    boxes, cls, score = boxes[keep], cls[keep], score[keep]
    if len(boxes) == 0:
        return [], [], []

    # 解码 cxcywh -> xyxy
    cx, cy, w, h = boxes[:,0], boxes[:,1], boxes[:,2], boxes[:,3]
    x1 = cx - w/2; y1 = cy - h/2; x2 = cx + w/2; y2 = cy + h/2

    # 去掉 letterbox 缩放和 padding
    scale = r
    x1 = (x1 - pad[0]) / scale
    y1 = (y1 - pad[1]) / scale
    x2 = (x2 - pad[0]) / scale
    y2 = (y2 - pad[1]) / scale
    x1 = np.clip(x1, 0, img_w); x2 = np.clip(x2, 0, img_w)
    y1 = np.clip(y1, 0, img_h); y2 = np.clip(y2, 0, img_h)

    xyxy = np.stack([x1, y1, x2, y2], 1)
    keep = cv2.dnn.NMSBoxes(xyxy.tolist(), score.tolist(), conf_th, nms_th)
    if len(keep) == 0:
        return [], [], []
    keep = np.array(keep).flatten()
    return xyxy[keep], cls[keep], score[keep]


def main():
    os.environ["RYZEN_AI_INSTALLATION_PATH"] = r"C:\Program Files\RyzenAI\1.6.0"

    print("[1/3] 加载模型...")
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])

    print("[2/3] 读图 + 预处理...")
    img0 = cv2.imread(IMAGE)
    if img0 is None:
        print("读图失败:", IMAGE); return
    h0, w0 = img0.shape[:2]
    img, r, pad = letterbox(img0, 640, 32)
    x = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    x = x[None]

    print("[3/3] 推理...")
    import time
    t0 = time.time()
    out = sess.run(None, {"images": x})[0]
    dt = time.time() - t0
    print(f"推理耗时 {dt*1000:.1f}ms  (CPU)")

    boxes, cls, score = postprocess(out, r, pad, CONF_TH, NMS_TH, h0, w0)

    print(f"\n检测到 {len(boxes)} 个目标:")
    for b, c, s in zip(boxes, cls, score):
        name = COCO_NAMES[int(c)]
        print(f"  {name:15s}  conf={s:.2f}  box=({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f})")
        cv2.rectangle(img0, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0,255,0), 2)
        cv2.putText(img0, f"{name} {s:.2f}", (int(b[0]), int(b[1])-5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    out_path = r"D:\FPGA\42_ov5640_udp_pc\yolo_detect_result.png"
    cv2.imwrite(out_path, img0)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
