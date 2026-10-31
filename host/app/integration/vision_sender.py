# -*- coding: utf-8 -*-
"""
vision_sender.py — PC 侧视觉结果发送器（欧阳用）
================================================
跑在 PC 上，把 YOLO 的检测结果转成郑要的 Vision JSON，UDP 发给**板子的 PS 网口**。

【与现有代码的关系】
欧阳现在的 `ryzenai/main_ryzenai.py` 已经能把结果回发 FPGA，但发的是
`a5 0f f0 5a + class_id + score`（分类，没有框），而且发给了 PL 网口。
本文件替代那一段，改动只有三行：

    # 原来
    send_result(s, last_class, last_score)
    # 改成
    from vision_sender import VisionSender
    sender = VisionSender("192.168.1.20", 5000)     # 板子 PS 的 IP
    ...
    sender.send(boxes, classes, scores, img_w, img_h)

【为什么发给 PS 而不是 PL】
板子两个网口：
  · PL 网口 192.168.1.10：只用来传图（Verilog 写死，别碰）
  · PS 网口 192.168.1.20：Linux，跑郑的决策 Python ← 结果发这里
零 Verilog 改动。

【网络要求】
PC 和板子两个网口要在同一网段。最省事：一个千兆交换机，PC / PL口 / PS口 都插上去。
    PC   192.168.1.102
    PL   192.168.1.10   （Verilog 里写死，不能改）
    PS   192.168.1.20   （在 PYNQ 的 Linux 里配静态 IP）
"""
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from contract import detections_to_vision, CONTRACT_VERSION   # noqa: E402

DEFAULT_PS_IP = "192.168.1.20"
DEFAULT_PS_PORT = 5000


class VisionSender:
    def __init__(self, target_ip=DEFAULT_PS_IP, port=DEFAULT_PS_PORT,
                 conf_th=0.25, class_names=None, verbose=False):
        self.target = (target_ip, port)
        self.conf_th = conf_th
        self.class_names = class_names
        self.verbose = verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent = 0
        self.last_obstacle = None

    def send(self, boxes, classes, scores, img_w=640, img_h=480):
        """boxes 必须是**原图像素坐标**（letterbox 还原之后的）。
        yolo_test.py::postprocess 返回的就是原图坐标，直接传进来即可。
        """
        v = detections_to_vision(boxes, classes, scores, img_w, img_h,
                                 class_names=self.class_names, conf_th=self.conf_th)
        try:
            self.sock.sendto(json.dumps(v, ensure_ascii=False).encode("utf-8"), self.target)
            self.sent += 1
        except Exception as e:
            if self.verbose:
                print(f"[!] 发送失败: {e}")
            return None

        if self.verbose:
            n = v.get("nearest")
            if n:
                print(f"[发#{self.sent}] {n['class']} {n['confidence']:.2f} "
                      f"cx={n['cx']:.2f} cy={n['cy']:.2f} → {self.target[0]}:{self.target[1]}")
            else:
                print(f"[发#{self.sent}] 无障碍 → {self.target[0]}:{self.target[1]}")
        self.last_obstacle = v.get("obstacle")
        return v

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ============ 独立运行：假推理，验证 PC→PS 链路 ============

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=DEFAULT_PS_IP, help="板子 PS 网口 IP")
    ap.add_argument("--port", type=int, default=DEFAULT_PS_PORT)
    ap.add_argument("--hz", type=float, default=2.0, help="发送频率")
    a = ap.parse_args()

    s = VisionSender(a.ip, a.port, conf_th=0.25,
                     class_names=["person", "car", "chair"], verbose=True)
    print(f"vision_sender  contract v{CONTRACT_VERSION}  目标 {a.ip}:{a.port}  {a.hz}Hz")
    print("发的是**假检测结果**（人在画面中间偏右），只用来验证链路。Ctrl-C 退出。\n")

    # 模拟一个从远到近走过来的人
    i = 0
    try:
        while True:
            t = i / a.hz
            # 人从画面右侧走进来，越走越大、越走越靠下
            cx_px = 420 - 60 * min(t, 3)
            cy_px = 300 + 30 * min(t, 3)
            w_px = 60 + 25 * min(t, 3)
            h_px = 140 + 60 * min(t, 3)
            box = [cx_px - w_px / 2, cy_px - h_px / 2, cx_px + w_px / 2, cy_px + h_px / 2]
            s.send([box], [0], [0.85], 640, 480)
            i += 1
            time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        print(f"\n共发 {s.sent} 包")
    finally:
        s.close()


if __name__ == "__main__":
    main()
