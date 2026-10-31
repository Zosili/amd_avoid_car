# -*- coding: utf-8 -*-
"""
OV5640 → FPGA → UDP → RyzenAI NPU 推理 → 回发 FPGA（完整链路）
==============================================================
流程：
  1. 发启动命令 "1" 给 FPGA
  2. 收 UDP 1234 图像帧，解析 RGB565（小端序）
  3. 预处理成模型输入（缩放/归一化/NCHW）
  4. RyzenAI NPU 推理（VitisAIExecutionProvider）
  5. 结果回发 FPGA（UDP 到 192.168.1.10:1234）
  6. OpenCV 窗口叠加显示推理结果

退出：按 q 或 Esc
"""

import socket
import threading
import time
import numpy as np
import cv2

# 复用 NPU 封装
from npu_infer import NpuInfer

# ===== FPGA 链路参数（与工程一致）=====
BOARD_IP   = "192.168.1.10"
PORT       = 1234
WIDTH      = 640
HEIGHT     = 480
FRAME_HEAD = b"\xf0\x5a\xa5\x0f"
FRAME_FULL = 4 + 4 + WIDTH * HEIGHT * 2          # 614408

# ===== 模型参数（改成你自己的模型）=====
MODEL_PATH = r"C:\Program Files\RyzenAI\1.6.0\quicktest\test_model.onnx"
INPUT_H    = 32        # 模型输入高
INPUT_W    = 32        # 模型输入宽
# 分类标签（按你的模型改）
CLASS_NAMES = [f"class{i}" for i in range(10)]

# ===== 回发 FPGA 的格式：前4字节帧头 + 结果 =====
RESULT_HEAD = b"\xa5\x0f\xf0\x5a"    # 自定义结果帧头（和FPGA端约定即可）

# ---------- UDP 收图（双线程，抗丢包）----------
buf = bytearray()
buf_lock = threading.Lock()
latest_img = None      # 最新解码好的 BGR 图
frame_cnt = 0
running = True

def receiver(sock):
    global running
    while running:
        try:
            data, _ = sock.recvfrom(65536)
        except (socket.timeout, OSError):
            continue
        with buf_lock:
            buf.extend(data)

def rgb565_to_bgr(data, w, h):
    arr = np.frombuffer(data, dtype=np.uint16).reshape(h, w)
    arr = ((arr & 0xFF) << 8) | (arr >> 8)        # 小端序交换
    r = ((arr >> 11) & 0x1F) * 255 // 31
    g = ((arr >> 5)  & 0x3F) * 255 // 63
    b = (arr         & 0x1F) * 255 // 31
    return cv2.merge([b.astype(np.uint8), g.astype(np.uint8), r.astype(np.uint8)])

def extract_latest():
    """从缓冲取最新一帧 BGR 图"""
    global latest_img, frame_cnt, buf
    while True:
        with buf_lock:
            if len(buf) > 16 * 1024 * 1024:
                del buf[:-FRAME_FULL]
            idx = buf.find(FRAME_HEAD)
            if idx < 0:
                if len(buf) > 4:
                    del buf[:-4]
                return
            if len(buf) - idx < FRAME_FULL:
                if idx > 0:
                    del buf[:idx]
                return
            frame_bytes = bytes(buf[idx + 8: idx + 8 + WIDTH * HEIGHT * 2])
            del buf[:idx + FRAME_FULL]
        latest_img = rgb565_to_bgr(frame_bytes, WIDTH, HEIGHT)
        frame_cnt += 1

# ---------- 预处理：BGR(640x480) → 模型输入(1,3,H,W) float32 ----------
def preprocess(bgr, in_h, in_w):
    img = cv2.resize(bgr, (in_w, in_h))            # 缩放
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)     # BGR→RGB
    img = img.astype(np.float32) / 255.0           # 归一化到 [0,1]
    # （若模型需要 ImageNet 均值/方差，在这里做）
    img = np.transpose(img, (2, 0, 1))             # HWC→CHW
    return np.expand_dims(img, axis=0)             # 加 batch 维 → (1,3,H,W)

# ---------- 结果回发 FPGA ----------
def send_result(sock, class_id, score):
    # 简单格式：4字节帧头 + 1字节类别 + 4字节float分数
    payload = RESULT_HEAD + bytes([class_id & 0xFF]) + np.float32(score).tobytes()
    sock.sendto(payload, (BOARD_IP, PORT))

# ===== 主程序 =====
def main():
    global running
    # 1) 加载 NPU 模型
    npu = NpuInfer(MODEL_PATH)

    # 2) UDP socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(("0.0.0.0", PORT))
    s.settimeout(0.5)
    s.sendto(b"1", (BOARD_IP, PORT))              # 启动 FPGA 传图
    print(f"[主] 已发启动命令到 {BOARD_IP}:{PORT}")

    threading.Thread(target=receiver, args=(s,), daemon=True).start()
    time.sleep(1.0)

    last_infer = 0.0
    last_class = 0
    last_score = 0.0

    try:
        while True:
            extract_latest()
            if latest_img is None:
                cv2.waitKey(10)
                continue

            img = latest_img.copy()

            # 3) 每隔 ~0.5 秒推理一次（不必每帧都推，降负载）
            now = time.time()
            if now - last_infer >= 0.5:
                x = preprocess(img, INPUT_H, INPUT_W)
                out = npu.run({npu.input_names[0]: x})[0]
                last_class = int(np.argmax(out[0]))
                last_score = float(np.max(out[0]))
                last_infer = now
                send_result(s, last_class, last_score)   # 回发 FPGA
                print(f"[推理] 类别={last_class} 置信度={last_score:.3f}")

            # 4) 叠加显示
            label = f"{CLASS_NAMES[last_class]} {last_score:.2f}"
            cv2.putText(img, label, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            cv2.imshow("RyzenAI NPU", img)

            k = cv2.waitKey(30) & 0xFF
            if k == ord('q') or k == 27:
                break
    finally:
        running = False
        cv2.destroyAllWindows()
        print(f"[退出] 共处理 {frame_cnt} 帧")

if __name__ == "__main__":
    main()
