# -*- coding: utf-8 -*-
"""
OV5640 -> YOLO(person) -> 雷达融合 -> 停车/鸣叫指令
===================================================
在 gpu_detect_live.py 的基础上加两件事：
  1. 用 19 类模型（person = 18），只关心人
  2. 人框 + 雷达距离 交给 fusion_core 决策，输出 GPIO 指令

运行环境：任意装了 onnxruntime + opencv 的 Python 3.9+；
         用 DirectML 时 onnxruntime 会挂 DmlExecutionProvider（AMD/Intel 核显/独显）
模型：默认用 --model / CART_MODEL / 同目录 models/best.onnx（19 类，person=18）
      当前版本 = livingB 40ep v2，all mAP50 0.882 / person mAP50 0.857

--------------------------------------------------------------------------
雷达接法（现在还没到货，三种模式都能跑）
--------------------------------------------------------------------------
--lidar none     不用雷达，只用单目地面估距。**现在就能跑**，验证人和状态机
--lidar udp      收 UDP 上的扫描：默认 127.0.0.1:15001（5001 被 NI 服务占用）
                 报文 JSON: {"angles":[度...], "ranges":[米...]}
                 任何语言的雷达驱动 20 行就能发出来，不绑定型号
--lidar file     replay 一份录好的扫描（jsonl），做离线复现

--------------------------------------------------------------------------
指令下发
--------------------------------------------------------------------------
--cmd-udp 192.168.1.10:5000
    每帧发 5 字节: 0xAA 0x55 + 距离(u16 大端, mm) + 转向(1 字节)
    距离：0xFFFF=无人，0xFFFE=有人但测不出距离（PL 按 WARN 鸣叫、不停车），其余=mm
    决策在 PL 端做（motor_decision.v）：它自己按 1m/3m 阈值判断
    该走/该鸣/该停，PC 只负责"最近的人有多远"。
    退出时直接停发（不发 0mm），PL 400ms 看门狗落到 SAFE 停车静音。
板端看门狗：超过 400ms 没收到新帧就 duty=0（已含在 motor_decision.v）

退出：q / Esc；Ctrl+C。退出前会自动发一条"停车+响"的指令。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

# 本机用户级 site-packages 里装了 onnxruntime；换机器可能没有，存在才加。
# 写死绝对路径会让这个脚本不可移植（换用户名就 ImportError）。
_SP = os.path.expanduser(r"~\AppData\Roaming\Python\Python312\site-packages")
if os.path.isdir(_SP):
    sys.path.insert(0, _SP)
sys.path.insert(0, str(Path(__file__).resolve().parent / "fusion"))

import numpy as np
import cv2
import onnxruntime as ort

from fusion_core import DecisionConfig, FusionEngine, Scan, State, cam_to_lidar

os.environ.setdefault("RYZEN_AI_INSTALLATION_PATH", r"C:\Program Files\RyzenAI\1.6.0")

BOARD_IP, PORT = "192.168.1.10", 1234
WIDTH, HEIGHT = 640, 480
FRAME_HEAD = b"\xf0\x5a\xa5\x0f"
FRAME_FULL = 4 + 4 + WIDTH * HEIGHT * 2

_HERE = Path(__file__).resolve().parent
_MDL_PKG = _HERE / "models" / "best.onnx"          # 便携包里模型放这
# 优先级：--model 参数 > 环境变量 CART_MODEL > 当前目录 models/best.onnx
# 不写死任何绝对路径：换机器/换盘符只需设 CART_MODEL 或把模型放到 models/ 下
MODEL = os.environ.get("CART_MODEL") or str(_MDL_PKG)
NAMES = ["trashcan", "slippers", "wire", "socks", "carpet", "book", "feces",
         "curtain", "stool", "bed", "sofa", "closestool", "table", "cabinet",
         "ajardoor", "opendoor", "closedoor", "stairway", "person"]
PERSON = 18

STATE_COLOR = {State.CLEAR: (80, 220, 80), State.WARN: (0, 200, 255),
               State.STOP: (40, 40, 255)}

# ---------------------------------------------------------------- 雷达源
class LidarSource:
    def read(self) -> Scan | None:
        return None


class UdpLidar(LidarSource):
    """收 JSON 扫描。驱动侧只需 sendto(JSON, ('127.0.0.1', 15001))。

    默认端口用 15001 而不是教科书里的 5001：这台机器上 5001 被 NI 的
    nidmsrv 服务占着（实测 WinError 10048），绑不上的话会走下面的友好报错。
    """
    def __init__(self, host="0.0.0.0", port=15001):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        try:
            self.s.bind((host, port))
        except OSError as e:
            self.s.close()
            raise RuntimeError(
                f"UDP {port} 绑定失败：{e}。该端口可能被别的程序占用"
                f"（本机 5001 就被 NI 的 nidmsrv 占了），换一个：--lidar-port 15001"
            ) from None
        self.s.settimeout(0.0)
        self.last = None
        self.hz = 0.0
        self._t = []

    def read(self):
        got = False
        while True:                      # 把积压的包全吃掉，只留最新一帧
            try:
                data, _ = self.s.recvfrom(65535)
            except (BlockingIOError, socket.timeout, OSError):
                break
            try:
                d = json.loads(data)
                self.last = Scan(d["angles"], d["ranges"], time.monotonic())
                got = True
            except Exception:
                continue
        if got:
            now = time.monotonic()
            self._t = [t for t in self._t if now - t < 2.0] + [now]
            self.hz = len(self._t) / 2.0
        return self.last


class SerialLidarSource(LidarSource):
    """直连 COIN-D6 / LD06 串口（USB 转 TTL 那种）。

    和 UdpLidar 的区别：UdpLidar 等着别人把扫描算好塞进来；这个自己读串口、
    自己解帧、自己凑整圈。现场调试时用这个最省事，插上就能看。
    """

    def __init__(self, port: str, baud: int = 230400, angle_sign: int = -1):
        from lidar_ld06 import SerialLidar
        self.l = SerialLidar(port, baud, angle_sign)
        self.hz = 0.0
        self.port = port

    def read(self):
        rev = self.l.poll()
        if rev is None:
            return None
        angles, ranges = rev
        self.hz = self.l.hz
        return Scan(angles, ranges, time.monotonic())


class FakeLidar(LidarSource):
    """假雷达：还没有雷达硬件时，用来验证"相机角度 -> 雷达角度"这条映射。

    整圈 360°、1° 一格，只在一个窄扇区里填距离（其余 NaN），
    模拟"前方站着一个人"。配合 --fake-sweep 让它左右摆动，
    就能肉眼确认画面上那条品红窗口是不是一直套在人身上。
    """

    def __init__(self, theta=0.0, dist=2.0, sweep=0.0, width=2.0):
        self.theta = theta
        self.dist = dist
        self.sweep = sweep
        self.width = width
        self.t0 = time.monotonic()
        self.hz = 10.0

    def read(self):
        t = time.monotonic() - self.t0
        th = self.theta + (self.sweep * np.sin(2 * np.pi * 0.15 * t)
                           if self.sweep else 0.0)
        angles = np.arange(0.0, 360.0, 1.0)
        ranges = np.full(angles.shape, np.nan, dtype=np.float32)
        d = np.abs(((angles - th + 180.0) % 360.0) - 180.0)
        ranges[d <= self.width] = self.dist
        return Scan(angles, ranges, time.monotonic())


class FileLidar(LidarSource):
    def __init__(self, path):
        self.rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
        self.i = 0

    def read(self):
        if not self.rows:
            return None
        r = self.rows[self.i % len(self.rows)]
        self.i += 1
        return Scan(r["angles"], r["ranges"])


# ---------------------------------------------------------------- 指令下发
class CmdSender:
    """5 字节指令帧： 0xAA 0x55 + 距离(u16 大端, mm) + 转向(1 字节)

    距离约定：
        0xFFFF = 无人
        0xFFFE = 有人但测不出距离（PL 端按 WARN 处理：鸣叫、不停车）
        其余   = 距离 mm
    转向字节：0x00=直行，0x01=左转(障碍在右)，0x02=右转(障碍在左)。
    为什么只发"最近人的距离 + 转向方向"而不发 GPIO 控制字：
    底盘策略（巡航速度、停/鸣阈值、看门狗）归 PL 的 motor_decision.v，
    PC 只当感知前端。以后雷达接 PS 做融合，也只需改"距离从哪来"，协议不动。
    """
    def __init__(self, target: str | None, dry: bool = True):
        self.dry = dry or not target
        self.seq = 0
        if not self.dry:
            host, port = target.rsplit(":", 1)
            self.addr = (host, int(port))
            self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def frame_mm(self, mm, steer: int = 0) -> bytes:
        mm = max(0, min(0xFFFF, int(mm)))
        return b"\xAA\x55" + mm.to_bytes(2, "big") + bytes([steer & 0xFF])

    def send_mm(self, mm, steer: int = 0):
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        if self.dry:
            return self.frame_mm(mm, steer)
        try:
            self.s.sendto(self.frame_mm(mm, steer), self.addr)
        except OSError:
            pass
        return None


# ---------------------------------------------------------------- 图像
buf = bytearray()
buf_lock = threading.Lock()
latest_frame = None
frame_cnt = 0
running = True


# ---------------------------------------------------------------- 颜色
# RGB565 解码 + cam_data[5] 恒高导致的偏红补偿，实现与原理见 colorfix.py
import colorfix

COLOR_FIX = os.environ.get("CART_COLOR_FIX", "off")   # "off" | "d5"（D5 线接触不良才用 d5）


def rgb565_to_bgr(data, w, h):
    return colorfix.decode(data, w, h)


def receiver(sock):
    while running:
        try:
            data, _ = sock.recvfrom(65536)
        except (socket.timeout, OSError):
            continue
        with buf_lock:
            buf.extend(data)


def keepalive(sock):
    """板子刚上电时 overlay 还没加载完，图传启动命令"1"会丢。每隔 2 秒重发一次，
    直到收到第一帧（frame_cnt>0）为止——这样用户上电后直接双击 bat 也能自动等到
    板子就绪出图，不用手动掐 60 秒。收到图后停止重发。"""
    while running and frame_cnt == 0:
        time.sleep(2.0)
        if frame_cnt == 0:
            try:
                sock.sendto(b"1", (BOARD_IP, PORT))
            except OSError:
                pass


def extract_frames():
    global latest_frame, frame_cnt
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
        fb = bytes(buf[idx + 8: idx + 8 + WIDTH * HEIGHT * 2])
        del buf[:idx + FRAME_FULL]
    latest_frame = rgb565_to_bgr(fb, WIDTH, HEIGHT)
    frame_cnt += 1


def letterbox(img, size=640, stride=32):
    h0, w0 = img.shape[:2]
    r = min(size / h0, size / w0)
    nh, nw = int(round(h0 * r)), int(round(w0 * r))
    nh = max(stride, int(round(nh / stride) * stride))
    nw = max(stride, int(round(nw / stride) * stride))
    dh, dw = size - nh, size - nw
    top, bottom = dh // 2, dh - dh // 2
    left, right = dw // 2, dw - dw // 2
    img = cv2.resize(img, (nw, nh))
    img = cv2.copyMakeBorder(img, top, bottom, left, right,
                             cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return img, r, (left, top)


def postprocess(pred, r, pad, conf_th, nms_th, img_h, img_w):
    pred = pred[0].T
    boxes, scores = pred[:, :4], pred[:, 4:]
    cls = scores.argmax(1)
    score = scores.max(1)
    keep = score > conf_th
    boxes, cls, score = boxes[keep], cls[keep], score[keep]
    if len(boxes) == 0:
        return []
    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = np.clip((cx - w / 2 - pad[0]) / r, 0, img_w)
    y1 = np.clip((cy - h / 2 - pad[1]) / r, 0, img_h)
    x2 = np.clip((cx + w / 2 - pad[0]) / r, 0, img_w)
    y2 = np.clip((cy + h / 2 - pad[1]) / r, 0, img_h)
    xyxy = np.stack([x1, y1, x2, y2], 1)
    k = cv2.dnn.NMSBoxes(xyxy.tolist(), score.tolist(), conf_th, nms_th)
    if len(k) == 0:
        return []
    k = np.array(k).flatten()
    return [{"bbox": tuple(xyxy[i]), "cls": int(cls[i]), "conf": float(score[i])} for i in k]


def draw(img, res, persons, engine, fps, lidar_hz):
    h, w = img.shape[:2]
    col = STATE_COLOR[res.state]

    for p in persons:
        x1, y1, x2, y2 = [int(v) for v in p["bbox"]]
        is_p = p["cls"] == PERSON
        c = col if is_p else (150, 150, 150)
        thick = 3 if is_p else 1
        cv2.rectangle(img, (x1, y1), (x2, y2), c, thick)
        cv2.putText(img, f"{NAMES[p['cls']]} {p['conf']:.2f}", (x1, max(y1 - 5, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1 if not is_p else 2)

    # 人的方位角窗口（画在画面顶部），标定时能直接看出对不对
    for rd in res.readings:
        a_lo, a_w = rd.window
        fx = engine.fx
        cx = engine.cx
        for a in (rd.alpha_center - a_w / 2, rd.alpha_center + a_w / 2):
            u = int(cx + fx * np.tan(np.radians(a)))
            if 0 <= u < w:
                cv2.line(img, (u, 0), (u, h), (200, 80, 200), 1)

    # 角度对齐 HUD：相机方位角 -> 映射到的雷达角 -> 雷达距离 / 单目距离 / 最终来源
    # 联调雷达时只看这几行：假目标（或真雷达那个簇）落在窗口内时 source 会变 lidar
    for i, rd in enumerate(res.readings[:2]):
        th = cam_to_lidar(rd.alpha_center, engine.cfg.theta0, engine.cfg.ang_sign)
        r_txt = f"{rd.lidar_dist:5.2f}m" if rd.lidar_dist else "   -- "
        m_txt = f"{rd.mono_dist:5.2f}m" if rd.mono_dist else "   -- "
        cv2.putText(img,
                    f"p{i} alpha={rd.alpha_center:+6.1f} -> theta={th:6.1f}  "
                    f"radar={r_txt} mono={m_txt} [{rd.source}]",
                    (10, 72 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255), 1, cv2.LINE_AA)

    # 大号状态条
    txt = res.state.value
    d = f"{res.dist:.2f}m" if res.dist is not None else "--"
    src = res.readings[0].source if res.readings else "-"
    cv2.rectangle(img, (0, 0), (w, 46), (30, 30, 30), -1)
    cv2.putText(img, f"{txt}  {d}  [{src}]", (10, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 1.05, col, 3)
    lidar_txt = "onboard" if lidar_hz <= 0 else f"{lidar_hz:.0f}Hz"
    cv2.putText(img, f"buzzer={'ON ' if res.buzzer else 'off'}  "
                     f"{fps:.0f}fps lidar={lidar_txt}",
                (w - 300, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
    if res.state is State.STOP:
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), col, 8)
    return img


def main():
    global running, COLOR_FIX
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--lidar", default="none",
                    choices=["none", "udp", "serial", "file", "fake"])
    ap.add_argument("--fake-theta", type=float, default=0.0,
                    help="假雷达目标角度（度，雷达坐标系），标定 theta0 时用")
    ap.add_argument("--fake-range", type=float, default=2.0,
                    help="假雷达目标距离（米）")
    ap.add_argument("--fake-sweep", type=float, default=0.0,
                    help="假雷达左右摆动幅度（度），0=不动；用来看窗口跟不跟得住人")
    ap.add_argument("--lidar-port", type=int, default=15001)
    ap.add_argument("--lidar-serial", default="COM5", help="例 COM5")
    ap.add_argument("--lidar-baud", type=int, default=230400)
    ap.add_argument("--lidar-sign", type=int, default=-1, choices=[1, -1],
                    help="雷达自身角度方向；顺时针雷达填 -1")
    ap.add_argument("--lidar-file")
    ap.add_argument("--cmd-udp", default=None, help="例 192.168.1.10:5000")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--send", action="store_true", help="真的发指令（否则只打印）")
    # 标定参数
    ap.add_argument("--theta0", type=float, default=0.0)
    ap.add_argument("--sign", type=int, default=-1, choices=[1, -1])
    ap.add_argument("--hfov", type=float, default=60.0)
    ap.add_argument("--cam-height", type=float, default=0.20)
    ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--stop-m", type=float, default=1.0)
    ap.add_argument("--warn-m", type=float, default=3.0)
    ap.add_argument("--record", default=None, help="把 bbox+扫描 录成 json 供标定")
    ap.add_argument("--log", default=None, help="把状态行同时写入文件（供外部监控）")
    ap.add_argument("--jsonl", default=None,
                    help="每处理一帧追加一行 JSON（t/帧号/帧率/推理耗时/状态），"
                         "可直接喂给 skill/latency_stats.py --period 100")
    ap.add_argument("--color-fix", default=COLOR_FIX, choices=["d5", "off"],
                    help="off=原始解码（默认，D5 已正常）；d5=补偿 cam_data[5] 恒高导致的偏红")
    ap.add_argument("--no-show", action="store_true")
    a = ap.parse_args()

    COLOR_FIX = a.color_fix
    colorfix.set_mode(COLOR_FIX)
    if COLOR_FIX == "d5":
        print("[颜色] 已启用 D5 偏红补偿（仅在画面明显偏红时才需要）；"
              "D5 正常时应加 --color-fix off")
    else:
        print("[颜色] 使用原始 RGB565 解码（D5 正常）")

    print("[1/4] 载入模型 ...")
    sess = ort.InferenceSession(a.model,
                                providers=[("DmlExecutionProvider", {"device_id": 0}),
                                           "CPUExecutionProvider"])
    print("      ", sess.get_providers()[0], "-> Radeon 780M (device_id=0)")
    inp = sess.get_inputs()[0]

    cfg = DecisionConfig(cam_w=WIDTH, cam_h_px=HEIGHT, hfov_deg=a.hfov,
                         cam_height_m=a.cam_height, cam_pitch_deg=a.pitch,
                         theta0=a.theta0, ang_sign=a.sign,
                         stop_m=a.stop_m, warn_m=a.warn_m)
    eng = FusionEngine(cfg)
    print(f"      几何: fx={eng.fx:.1f}px  相机高 {a.cam_height}m  俯角 {a.pitch}°")
    print(f"      融合: theta0={a.theta0}°  sign={a.sign:+d}  "
          f"阈值 停<{a.stop_m}m 鸣<{a.warn_m}m")

    lidar = LidarSource()
    if a.lidar == "udp":
        lidar = UdpLidar(port=a.lidar_port)
        print(f"[2/4] 雷达: UDP :{a.lidar_port} 收 JSON 扫描")
    elif a.lidar == "serial":
        try:
            lidar = SerialLidarSource(a.lidar_serial, a.lidar_baud, a.lidar_sign)
            print(f"[2/4] 雷达: 串口 {a.lidar_serial} @{a.lidar_baud} "
                  f"(COIN-D6/LD06 协议, sign={a.lidar_sign:+d})")
        except Exception as e:
            print(f"[2/4] 雷达: 打开 {a.lidar_serial} 失败 -> {e}")
            print("      退回单目地面估距（相机高必须准）")
            lidar = LidarSource()
    elif a.lidar == "file":
        lidar = FileLidar(a.lidar_file)
        print(f"[2/4] 雷达: replay {a.lidar_file}")
    elif a.lidar == "fake":
        lidar = FakeLidar(a.fake_theta, a.fake_range, a.fake_sweep)
        print(f"[2/4] 雷达: **假数据** theta={a.fake_theta:+.1f}° "
              f"r={a.fake_range:.2f}m 摆动=±{a.fake_sweep:.0f}°")
        print("          用途：验证 theta0/sign 这条映射。画面上的品红竖线是"
              "该人框对应的雷达窗口，假目标落在窗口内时距离来源会变成 lidar")
    else:
        print("[2/4] 雷达: 板载 GPIO 通路（PS 读串口 -> 写 AXI GPIO -> PL 融合），"
              "本进程只发视觉距离帧")

    sender = CmdSender(a.cmd_udp, dry=not a.send)
    print(f"[3/4] 指令: {'只打印（--send 才会真发）' if sender.dry else a.cmd_udp}")

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(("0.0.0.0", PORT))
    s.settimeout(0.5)
    s.sendto(b"1", (BOARD_IP, PORT))
    threading.Thread(target=receiver, args=(s,), daemon=True).start()
    threading.Thread(target=keepalive, args=(s,), daemon=True).start()
    time.sleep(1.0)
    print("[4/4] 开始，按 q 退出")

    rec = []
    t_last = time.time()
    t_stat = time.time()
    t_prev = time.time()
    fps = 0.0
    n_person = 0
    last_cmd = None
    last_processed = -1
    last_shown = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    t_inf = 0.0
    # 逐帧 JSONL：一行一帧，字段契约见 skill/latency_stats.py（必须有 t=unix 毫秒）
    jf = open(a.jsonl, "w", encoding="utf-8", buffering=1) if a.jsonl else None  # 行缓冲：被杀也不丢
    if jf:
        print(f"[jsonl] 逐帧记录 -> {a.jsonl}")

    try:
        while running:
            extract_frames()
            # 帧门控：只有真的收到新帧才推理。否则 60fps 的循环在反复算同一张图，
            # fps 是假的，n_person 也会虚高（实测 1086 次"看到人"只对应 274 帧）。
            if latest_frame is None or frame_cnt == last_processed:
                if not a.no_show and latest_frame is not None:
                    cv2.imshow("fusion", last_shown)
                    if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                        break
                elif not a.no_show:
                    cv2.imshow("fusion", np.zeros((HEIGHT, WIDTH, 3), np.uint8))
                    cv2.waitKey(5)
                time.sleep(0.002)
                continue
            last_processed = frame_cnt
            img = latest_frame.copy()
            if os.environ.get("CART_DUMP") and frame_cnt % 20 == 0:
                cv2.imwrite(f"_dump_{frame_cnt:04d}.png", img)
            h0, w0 = img.shape[:2]
            lb, r, pad = letterbox(img, 640, 32)
            x = np.ascontiguousarray(
                lb[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)[None] / 255.0)

            t0 = time.time()
            out = sess.run(None, {inp.name: x})[0]
            t_inf = (time.time() - t0) * 1000.0          # 本帧推理耗时 ms
            persons = postprocess(out, r, pad, a.conf, 0.45, h0, w0)
            scan = lidar.read()
            res = eng.update(None, [p for p in persons if p["cls"] == PERSON], scan)
            now = time.time()
            fps = 0.9 * fps + 0.1 / max(1e-3, now - t_prev)   # 真实帧率（帧间隔）
            t_prev = now

            # 指令帧下发：PL 端 motor_decision.v 按距离决策 + 转向字节缓转绕障
            # steer: 0=直行 1=左转(障碍在右) 2=右转(障碍在左)；无人时无障碍，恒 0
            steer = 0
            if res.obstacle_dist is not None:
                steer = 1 if res.obstacle_side > 0 else 2   # 障碍在右->左转，在左->右转
            # 距离编码：有距离发距离；有人但测不出发 0xFFFE（PL 按 WARN 鸣叫不停车）；无人发 0xFFFF。
            # 这里绝不能把"有人测不出距离"发成 0xFFFF（=无人），否则 PL 会放行、
            # 出现"PC 显示 STOP 轮子还在转"。
            if res.dist is not None:
                mm = int(round(res.dist * 1000.0))
                mm = max(0, min(0xFFFD, mm))
            elif res.readings:
                mm = 0xFFFE
            else:
                mm = 0xFFFF
            sender.send_mm(mm, steer)
            key = (res.state, res.buzzer, steer)
            if key != last_cmd:
                print(f"  状态 {res.state.value:<5} "
                      f"距离 {('%.2f' % res.dist) if res.dist else '--':>5}m  "
                      f"蜂鸣 {res.buzzer}"
                      + (f"  避障转{'左' if steer == 1 else '右' if steer == 2 else ''}" if steer else ""))
                last_cmd = key

            # 每 2 秒一行状态，长时间调参时看数值变化（不刷屏）
            if time.time() - t_stat >= 2.0:
                t_stat = time.time()
                d_txt = f"{res.dist:.2f}" if res.dist else "--"
                radar_txt = ("板载" if a.lidar == "none"
                             else f"{getattr(lidar, 'hz', 0.0):.0f}Hz")
                line = (f"  [{time.strftime('%H:%M:%S')}] {fps:5.1f}fps(真) "
                        f"推理{t_inf:4.1f}ms  帧{frame_cnt:<5d}人{n_person:<4d}  "
                        f"距离{d_txt:>5}m  状态 {res.state.value:<5} 蜂鸣 {int(res.buzzer)}  "
                        f"雷达{radar_txt}")
                print(line)
                if a.log:
                    try:
                        with open(a.log, "a", encoding="utf-8") as _lf:
                            _lf.write(line + "\n")
                    except OSError:
                        pass

            if a.record and res.readings:
                rec.append({"bbox": list(res.readings[0].bbox),
                            "angles": scan.angles.tolist() if scan else [],
                            "ranges": scan.ranges.tolist() if scan else []})
            if res.readings:
                n_person += 1

            # 逐帧落盘：t 用真机毫秒时间戳，latency_stats 靠相邻两行的差值算周期
            if jf:
                jf.write(json.dumps({"t": int(time.time() * 1000),
                                     "frame": frame_cnt,
                                     "fps": round(fps, 2),
                                     "infer_ms": round(t_inf, 2),
                                     "state": res.state.value,
                                     "steer": steer,
                                     "person": n_person},
                                    ensure_ascii=False) + "\n")

            if not a.no_show:
                last_shown = draw(img, res, persons, eng, fps,
                                  getattr(lidar, "hz", 0.0))
                cv2.imshow("fusion", last_shown)
                k = cv2.waitKey(1) & 0xFF
                if k == ord('q') or k == 27:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        # 不主动发 0mm：那会让 PL 进 STOP（停车 + 连续响，很吵）。
        # 直接停发，PL 的 400ms 看门狗会落到 SAFE（停车 + 静音），
        # 效果一样是"车停住"，但不会吵。
        print("[退出] 停止发帧 -> PL 看门狗 400ms 后进入 SAFE（停车静音）")
        cv2.destroyAllWindows()
        print(f"\n[退出] {frame_cnt} 帧，其中 {n_person} 帧看到人")
        if jf:
            jf.close()
            print(f"[jsonl] 已写入 {a.jsonl}")
        if a.record and rec:
            Path(a.record).write_text(json.dumps(rec, ensure_ascii=False),
                                      encoding="utf-8")
            print(f"采集已存 {a.record}（{len(rec)} 条，可喂给 calib_yaw.py）")


if __name__ == "__main__":
    main()
