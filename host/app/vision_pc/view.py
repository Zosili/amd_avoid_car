# -*- coding: utf-8 -*-
"""
OV5640 实时图像显示 v3（最终版）
================================
帧格式（实测抓包确认）：
  每帧 = 4字节帧头 f0 5a a5 0f + 4字节宽高(大端 0x0280 0x01E0) + 614400字节像素
  像素 = RGB565，每像素2字节，【小端序】（低字节在前，需交换）
帧总长 = 614408 字节

退出：按 q 或 Esc
"""

import socket
import threading
import time
import numpy as np
import cv2

BOARD_IP   = "192.168.1.10"
PORT       = 1234
WIDTH      = 640
HEIGHT     = 480
FRAME_HEAD = b"\xf0\x5a\xa5\x0f"
FRAME_FULL = 4 + 4 + WIDTH * HEIGHT * 2      # 614408

buf = bytearray()
buf_lock = threading.Lock()
latest_frame = None
frame_cnt = 0
drop_cnt = 0
running = True

def receiver():
    global running
    while running:
        try:
            data, _ = s.recvfrom(65536)
        except (socket.timeout, OSError):
            continue
        with buf_lock:
            buf.extend(data)

def rgb565_to_bgr(data, w, h):
    arr = np.frombuffer(data, dtype=np.uint16).reshape(h, w)
    arr = ((arr & 0xFF) << 8) | (arr >> 8)        # 小端字节序交换（实测必需）
    r = ((arr >> 11) & 0x1F) * 255 // 31
    g = ((arr >> 5)  & 0x3F) * 255 // 63
    b = (arr         & 0x1F) * 255 // 31
    return cv2.merge([b.astype(np.uint8), g.astype(np.uint8), r.astype(np.uint8)])

def extract_frames():
    global latest_frame, frame_cnt, drop_cnt
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
            frame_bytes = bytes(buf[idx + 8: idx + 8 + WIDTH * HEIGHT * 2])  # 跳过帧头+宽高
            del buf[:idx + FRAME_FULL]
        latest_frame = rgb565_to_bgr(frame_bytes, WIDTH, HEIGHT)
        frame_cnt += 1

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
s.bind(("0.0.0.0", PORT))
s.settimeout(0.5)

s.sendto(b"1", (BOARD_IP, PORT))
print(f"[启动] 已发启动命令到 {BOARD_IP}:{PORT}，v3 双线程收图中... 按 q 退出")

threading.Thread(target=receiver, daemon=True).start()
time.sleep(1.0)   # 先攒1秒数据

try:
    while True:
        extract_frames()
        if latest_frame is not None:
            img = latest_frame.copy()
            cv2.putText(img, f"frame {frame_cnt} resync {drop_cnt}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.imshow("OV5640 UDP v3", img)
        k = cv2.waitKey(30) & 0xFF
        if k == ord('q') or k == 27:
            break
finally:
    running = False
    cv2.destroyAllWindows()
    print(f"\n[退出] 共显示 {frame_cnt} 帧，重同步 {drop_cnt} 次")
