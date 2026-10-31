# -*- coding: utf-8 -*-
"""mock_vision.py —— 扮演欧阳的视觉端（TCP 客户端），用于郑这边单独联调

它按 10Hz 往板子（或本机）推 Vision JSON，场景可切换，用来验证：
  · 视觉单源转向（R2）、视觉+雷达冲突（R3）、断流降级（R5）
  · 欧阳改代码后，先用这个对齐字段，再换真模型

Vision JSON（与欧阳约定，冻结）
------------------------------
    {"version":1,"timestamp":<ms>,"obstacle":<bool>,
     "nearest":{"class":"person","confidence":0.82,
                "cx":0.30,"cy":0.55,"width":0.18,"height":0.42}}
    cx/cy/width/height 都是**画面归一化 0~1**；cx<0.45 算左，>0.55 算右
    obstacle=False 时 nearest 可以不给

用法
----
    python mock_vision.py                              # 连 127.0.0.1:5000，空场景
    python mock_vision.py --scenario person_left       # 左边有人
    python mock_vision.py --scenario person_center --hz 10 --secs 20
    python mock_vision.py --scenario flicker           # 每 10 帧断 3 帧（测过期）
    python mock_vision.py --scenario bad               # 故意发畸形帧（测校验）
    python mock_vision.py --host 192.168.2.99          # 连板子
"""
import argparse
import json
import socket
import sys
import time

SCENES = {
    # name: (obstacle, cx, cy, w, h, conf)
    "clear":         (False, None, None, None, None, None),
    "person_center": (True, 0.50, 0.60, 0.20, 0.55, 0.86),
    "person_left":   (True, 0.28, 0.58, 0.18, 0.50, 0.78),
    "person_right":  (True, 0.74, 0.62, 0.22, 0.58, 0.81),
    "far_small":     (True, 0.51, 0.42, 0.05, 0.09, 0.63),
    "low_conf":      (True, 0.49, 0.55, 0.15, 0.30, 0.41),
}


def now_ms():
    return int(time.time() * 1000)


def frame(scene):
    if scene in ("flicker", "bad"):
        scene = "person_center"
    obstacle, cx, cy, w, h, conf = SCENES[scene]
    d = {"version": 1, "timestamp": now_ms(), "obstacle": obstacle}
    if obstacle:
        d["nearest"] = {"class": "person", "confidence": conf, "cx": cx,
                        "cy": cy, "width": w, "height": h}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--scenario", default="clear", choices=sorted(SCENES) +
                    ["flicker", "bad"])
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--secs", type=float, default=0, help="0=一直发")
    a = ap.parse_args()

    try:
        s = socket.create_connection((a.host, a.port), timeout=3)
    except OSError as e:
        print(f"[FAIL] 连不上 {a.host}:{a.port} —— {e}")
        print("       板子那边 board_main.py 起来了吗？IP/端口对吗？")
        return 1
    s.settimeout(1.0)
    print(f"[vision] 已连 {a.host}:{a.port}，场景={a.scenario} {a.hz:g}Hz")
    period = 1.0 / max(0.5, a.hz)
    t0 = time.time()
    n = 0
    try:
        while True:
            if a.secs and time.time() - t0 > a.secs:
                break
            if a.scenario == "flicker" and (n % 10) in (7, 8, 9):
                pass                      # 断 3 帧不发
            elif a.scenario == "bad" and n % 5 == 0:
                s.sendall(b'{"version":1,"obstacle":"yes"}\n')   # 畸形
            else:
                s.sendall((json.dumps(frame(a.scenario)) + "\n").encode())
            n += 1
            if n % 20 == 0:
                print(f"[vision] 已发 {n} 帧")
            time.sleep(period)
    except (BrokenPipeError, ConnectionResetError):
        print("[vision] 连接被对端关闭")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass
    print(f"[vision] 结束，共发 {n} 帧")
    return 0


if __name__ == "__main__":
    sys.exit(main())
