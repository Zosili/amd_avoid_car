# -*- coding: utf-8 -*-
"""radar_map_view.py —— 简易云图小程序：看「车的数据里，地图长什么样」

它是什么
--------
一个实时窗口：左边是**累积点云图**（把最近几秒/全部点画在雷达坐标系里，
车体轮廓、遮挡区、决策扇区、阈值圈都叠上去），右边是决策链真正吃的
3 个数字和当前动作。数据路径和车上**完全同源**：
    d6_protocol.read_frame / parse_frame_ex  →  scan_stats  →  decide
你在桌面上看到的，就是将来车会看到的。

它不是 SLAM
-----------
没有里程计、没有定位，只是把点按「雷达坐标系」累积。车不动时，
墙就是墙的样子；车一动，墙会拖影 —— 这个拖影恰恰就是"为什么需要
SLAM"的直观演示。静止看场景，这个工具就是你要的"地图"。

用法
----
⚠️ 必须用**带 tkinter 的 Python** 运行，否则 matplotlib 退回 Agg、窗口不显示
   （envs/default 的 py3.13 没有 tkinter；用 envs/gui312/Scripts/python.exe）

    python radar_map_view.py                     # 实时读雷达 COM5
    python radar_map_view.py --port COM6
    python radar_map_view.py --log               # 同时把 12 扇区数据记 CSV
    python radar_map_view.py --demo              # 不接雷达，合成场景演示
    python radar_map_view.py --selftest          # 无窗口自检，出 PNG+几何报告
    python radar_map_view.py --mount center      # 看"装车体中部"是什么效果

快捷键（窗口内）
    a  切换 累积模式(像地图) / 拖尾模式(最近3秒)
    z  切换视野半径 0.8 / 1.5 / 3 / 6 m（看近处细节用 0.8）
    c  清空累积
    s  截图保存 PNG
    q  退出

12 扇区研究模式（2026-10-25 加）
    右栏极坐标图显示 12 个 30° 扇区的稳健最小距离（s0=345~15° 跨 0°，
    与车上 SECTOR_FRONT 同口径，k=2 稳健、复用车上的 _robust_min）。
    这是研究工具：先在电脑上看清每个方向，上车实测遮挡后再决定删哪些。
    车上的 3 扇区决策链一行没动；--log 可把 12 扇区时序记进 CSV 离线分析。

车体形状：超椭圆 |x/b|^n + |y/a|^n = 1（n=2 纯椭圆，n 越大越方），
雷达可装在前极点外 --fwd 厘米处，或 --mount center 装在几何中心。
遮挡区用数值光线投射算（对任意 n 都成立），并在图上画成红色扇区；
若当前 SECTOR_* 与遮挡区重叠会**直接报警**（装车死锁检查）。
"""
import argparse
import atexit
import csv
import json
import math
import os
import socket
import sys
import time
import threading
import collections

import numpy as np
import matplotlib
import matplotlib.animation            # ⚠️ 不会被隐式导入，交互模式必需
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Polygon

CAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "car")
if not os.path.isdir(CAR):                 # 万一被单独拷走，退回同仓相对位置
    CAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "car")
if CAR not in sys.path:
    sys.path.insert(0, CAR)

import protocol                      # noqa: E402
from radar import d6_protocol        # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_OK, C_WARN, C_BAD, C_GREY = "#2e9e5b", "#e0a020", "#d64545", "#9aa0a6"
TRAIL_SECS = 3.0          # 拖尾模式保留秒数
MAP_MAX_PTS = 60000       # 累积模式最大点数


# ================================================================ 车体几何
class CarShape:
    """超椭圆车体 + 雷达安装位置 + 数值光线投射算遮挡。"""

    def __init__(self, car_len=0.35, car_wid=0.18, n_exp=2.2, mount="front", fwd=0.03):
        self.a = car_len / 2.0          # 半长（纵轴 y）
        self.b = car_wid / 2.0          # 半宽（横轴 x）
        self.n = n_exp
        self.mount = mount
        if mount == "center":
            self.radar = (0.0, 0.0)
        else:
            self.radar = (0.0, self.a + fwd)

    def inside(self, x, y):
        return (abs(x) / self.b) ** self.n + (abs(y) / self.a) ** self.n <= 1.0

    def outline(self, npts=240):
        """超椭圆轮廓点（车体坐标系，原点=几何中心）。"""
        t = np.linspace(0, 2 * np.pi, npts)
        ct, st = np.cos(t), np.sin(t)
        x = self.b * np.sign(ct) * np.abs(ct) ** (2.0 / self.n)
        y = self.a * np.sign(st) * np.abs(st) ** (2.0 / self.n)
        return x, y

    def hit_dist(self, ang_deg, t_max=1.2, step=0.004):
        """从雷达沿 ang_deg 打一条线，返回打到车体的距离(米)；没打中返回 None。"""
        s, c = math.sin(math.radians(ang_deg)), math.cos(math.radians(ang_deg))
        x0, y0 = self.radar
        t = 0.006                      # 起步 6mm，避免贴着轮廓的边界问题
        while t <= t_max:
            if self.inside(x0 + s * t, y0 + c * t):
                return t
            t += step
        return None

    def occlusion_mask(self, step_deg=0.5):
        """0~360 每 step_deg 一格：True=该方向被车体挡住。"""
        angs = np.arange(0.0, 360.0, step_deg)
        mask = np.array([self.hit_dist(a) is not None for a in angs])
        return angs, mask

    def clear_fov(self, step_deg=0.5):
        """前方连续净空角（度）：从 0° 向两侧扩展到第一个被挡的角度。"""
        angs, mask = self.occlusion_mask(step_deg)
        half = int(round(180.0 / step_deg))
        idx0 = 0
        i = 0
        while i < half and not mask[(idx0 + i) % len(mask)]:
            i += 1
        j = 0
        while j < half and not mask[(idx0 - j) % len(mask)]:
            j += 1
        clear_half_deg = min(i, j) * step_deg
        return 2.0 * clear_half_deg, angs, mask

    def sweep_radius(self):
        """原地旋转扫掠半径（绕几何中心，含外伸的雷达）。"""
        if self.mount == "center":
            return self.a
        return self.a + (self.radar[1] - self.a)

    def overlap_with_sectors(self, step_deg=0.5):
        """检查 d6_protocol.SECTOR_* 与遮挡区的重叠角宽度（度）。"""
        angs, mask = self.occlusion_mask(step_deg)
        out = {}
        for name, sec in (("FRONT", d6_protocol.SECTOR_FRONT),
                          ("LEFT", d6_protocol.SECTOR_LEFT),
                          ("RIGHT", d6_protocol.SECTOR_RIGHT)):
            lo, hi = sec
            degs = np.arange(lo, hi + 1e-9, step_deg) if lo <= hi else \
                np.concatenate([np.arange(lo, 360, step_deg), np.arange(0, hi + 1e-9, step_deg)])
            idx = ((degs / step_deg).round().astype(int)) % len(mask)
            out[name] = float(mask[idx].sum() * step_deg)
        return out


def analytic_ellipse_occlusion_halfwidth(f):
    """n=2 纯椭圆、雷达在前极点外 f 米：解析切线半宽（弧度），用于校验数值法。
    切点 y0=a^2/(a+f)，x0=b*sqrt(1-(y0/a)^2)。"""
    a, b = 0.175, 0.09
    y0 = a * a / (a + f)
    x0 = b * math.sqrt(max(0.0, 1.0 - (y0 / a) ** 2))
    dx, dy = x0, y0 - (a + f)          # 指向切点，dy<0
    return math.atan2(dx, -dy)         # 与正后方轴的夹角


# ================================================================ 决策（与 radar_decision_view.decide 一致）
def decide(front, left, right, cfg):
    danger, warn, passable = cfg["danger_m"], cfg["warn_m"], cfg["passable_m"]
    if front is None:
        return "STOP", "前扇区无回波", C_BAD
    if front < danger:
        return "STOP", f"前方 {front:.2f}m < 危险 {danger:.2f}m", C_BAD
    l_ok = left is not None and left > passable
    r_ok = right is not None and right > passable
    if front < warn:
        if not l_ok and not r_ok:
            return "STOP", f"左右都不足 {passable:.2f}m", C_BAD
        return "SLOW", f"前方 {front:.2f}m < 警惕 {warn:.2f}m", C_WARN
    return "FORWARD", f"前方 {front:.2f}m 通畅", C_OK


# ================================================================ 合成场景（demo / selftest）
def build_scene():
    """线段列表 [(x1,y1,x2,y2)] + 圆列表 [(cx,cy,r)]。雷达在原点，y 向前。"""
    segs = [
        (-1.2, -1.0, 1.2, -1.0),   # 身后墙
        (-1.2, 4.0, 1.2, 4.0),     # 前墙
        (-1.2, -1.0, -1.2, 4.0),   # 左墙
        (1.2, -1.0, 1.2, 4.0),     # 右墙
        # 纸箱 A（左前 0.35 方块）
        (-0.62, 0.75, -0.27, 0.75), (-0.62, 1.10, -0.27, 1.10),
        (-0.62, 0.75, -0.62, 1.10), (-0.27, 0.75, -0.27, 1.10),
        # 纸箱 B（右前长条）
        (0.38, 1.7, 0.85, 1.7), (0.38, 2.2, 0.85, 2.2),
        (0.38, 1.7, 0.38, 2.2), (0.85, 1.7, 0.85, 2.2),
        # 斜挡板（考验"墙有一角对着雷达"）
        (-1.2, 2.6, -0.3, 3.3),
    ]
    circles = [(0.15, 0.5, 0.12)]   # 近处圆柱（腿/桶）
    return segs, circles


def ray_dist(ang_deg, segs, circles, t_max=6.0):
    s, c = math.sin(math.radians(ang_deg)), math.cos(math.radians(ang_deg))
    best = t_max
    for (x1, y1, x2, y2) in segs:
        ex, ey = x2 - x1, y2 - y1
        den = s * ey - c * ex
        if abs(den) < 1e-12:
            continue
        mx, my = x1, y1
        t = (mx * ey - my * ex) / den
        u = (mx * c - my * s) / den
        if t > 1e-6 and -1e-9 <= u <= 1 + 1e-9 and t < best:
            best = t
    for (cx, cy, r) in circles:
        fx, fy = cx, cy
        tq = fx * s + fy * c
        d2 = fx * fx + fy * fy - tq * tq
        if d2 > r * r:
            continue
        t = tq - math.sqrt(max(0.0, r * r - d2))
        if 1e-6 < t < best:
            best = t
    return best


def synth_scan(rng, step_deg=0.9):
    segs, circles = build_scene()
    pts = []
    ang = 0.0
    while ang < 360.0 - 1e-9:
        d = ray_dist(ang, segs, circles)
        d += rng.normal(0, 0.008)
        if rng.random() < 0.04:
            d = None                      # 丢点
        elif rng.random() < 0.02:
            d += rng.uniform(0.1, 0.3)    # 离群噪点
        pts.append((ang, None if d is None else d * 1000.0, 200, False))
        ang += step_deg
    return pts


# ================================================================ 数据源
class RadarSource:
    """统一接口：real=串口线程，synth=合成线程，net=连板上广播。
    产出 (pts, stat) 到共享缓冲；net 模式另外把板上决策结果存在 self.remote。"""

    def __init__(self, mode, port=None):
        self.mode = mode
        self.port = port
        self.buf = collections.deque(maxlen=420)   # 最近一圈点 (ang, dist_mm|None, it, weak)
        self.lock = threading.Lock()
        self.frames_ok = 0
        self.frames_bad = 0
        self.hz = 0.0
        self.alive = True
        self.remote = None          # net 模式：板上最近一帧监控 JSON
        self.remote_t = 0.0         # 本机收到它的时刻（time.time()）
        self.connected = None       # net 模式：None=正在连 True/False=结果
        self.rng = np.random.default_rng(7)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        if self.mode == "real":
            self._run_real()
        elif self.mode == "net":
            self._run_net()
        else:
            self._run_synth()

    def _run_net(self):
        """连板子的监控端口（board_main.py --monitor-port），只读不改。

        断线自动重连（板上程序重启很常见），连不上不退出、每 2s 重试，
        这样 PC 端窗口可以一直开着，板子什么时候起来什么时候就有数据。
        """
        host, _, port = self.port.rpartition(":")
        if not host:
            host, port = self.port, "5002"
        port = int(port)
        while self.alive:
            try:
                sock = socket.create_connection((host, port), timeout=2.0)
            except OSError as e:
                if self.connected is not False:
                    print(f"[FAIL] 连不上 {host}:{port} —— {e}")
                    print("       板上 board_main.py 起来了吗？IP 对吗？"
                          "（2s 后重试）")
                    self.connected = False
                time.sleep(2.0)
                continue
            self.connected = True
            print(f"[net] 已连板上 {host}:{port}")
            sock.settimeout(1.0)
            buf = b""
            t0, n0 = time.time(), 0
            try:
                while self.alive:
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            msg = json.loads(line.decode("utf-8", "replace"))
                        except Exception:
                            self.frames_bad += 1
                            continue
                        pts = msg.get("pts")
                        with self.lock:
                            if pts:
                                # [角度×10, 距离mm] → (角度, 距离mm, 强度, 弱回波)
                                self.buf.extend(
                                    [(a / 10.0, d, 0, False) for a, d in pts])
                                self.frames_ok += 1
                                n0 += 1
                            self.remote = msg
                            self.remote_t = time.time()
                            if time.time() - t0 >= 1.0:
                                self.hz = n0 / (time.time() - t0)
                                n0, t0 = 0, time.time()
            except Exception as e:
                print(f"[net] 连接异常：{e}")
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            if self.alive:
                print("[net] 与板子断开，2s 后重连")
                time.sleep(2.0)

    def _run_real(self):
        import serial
        try:
            ser = serial.Serial(self.port, d6_protocol.BAUD, bytesize=8,
                                parity="N", stopbits=1, timeout=0.2)
            ser.dtr = False            # 官方手册：DTR 拉高会复位雷达
            ser.rts = False
        except Exception as e:
            print(f"[FAIL] 打不开 {self.port}: {e}")
            self.alive = False
            return
        t0 = time.time()
        n0 = 0
        while self.alive:
            fr = d6_protocol.read_frame(ser)
            if fr is None:
                continue
            pts = d6_protocol.parse_frame_ex(*fr)
            with self.lock:
                if pts is None:
                    self.frames_bad += 1
                else:
                    self.frames_ok += 1
                    self.buf.extend(pts)
                    n0 += 1
                    if time.time() - t0 >= 1.0:
                        self.hz = n0 / (time.time() - t0)
                        n0, t0 = 0, time.time()
        ser.close()

    def _run_synth(self):
        t0 = time.time()
        n0 = 0
        while self.alive:
            pts = synth_scan(self.rng)
            with self.lock:
                self.buf.extend(pts)
                self.frames_ok += 1
                n0 += 1
                if time.time() - t0 >= 1.0:
                    self.hz = n0 / (time.time() - t0)
                    n0, t0 = 0, time.time()
            time.sleep(0.1)            # 模拟 10Hz

    def snapshot(self):
        with self.lock:
            return list(self.buf), self.frames_ok, self.frames_bad, self.hz


# ================================================================ 绘图
def runs_of(mask, step_deg):
    """布尔圈数组 → 连续 True 区间列表 [(lo,hi)]（处理跨 0）。"""
    n = len(mask)
    if not mask.any():
        return []
    trues = [i for i, v in enumerate(mask) if v]
    runs, start, prev = [], trues[0], trues[0]
    for i in trues[1:]:
        if i == prev + 1:
            prev = i
        else:
            runs.append((start, prev))
            start = prev = i
    runs.append((start, prev))
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == n - 1:
        lo0, _ = runs.pop(0)
        hi1, _ = runs.pop()
        runs.append((hi1 * step_deg, lo0 * step_deg + 360.0))
    return [(lo * step_deg, hi * step_deg) for lo, hi in runs]


class MapView:
    def __init__(self, args, interactive=True):
        self.args = args
        self.car = CarShape(car_len=args.car_len / 100.0, car_wid=args.car_wid / 100.0,
                            n_exp=args.shape_n, mount=args.mount, fwd=args.fwd / 100.0)
        self.accumulate = False
        self.map_pts = []              # [(x,y)] 累积
        self.trail = collections.deque(maxlen=20000)
        self.save_path = None
        self._t0 = time.time()         # 供 --auto-close 计时
        self.view_r = 1.5              # 左图视野半径(m)，按 z 切换
        self._view_steps = [0.8, 1.5, 3.0, 6.0]
        self._last_log = 0.0
        self._csv = None
        self._csv_f = None
        if getattr(args, "log", False):
            self._open_log()
        self._build_fig()
        self.src = None
        if interactive:
            if getattr(args, "tcp", None):
                self.src = RadarSource("net", args.tcp)
            else:
                self.src = RadarSource("synth" if args.demo else "real",
                                       None if args.demo else args.port)
            self.anim = matplotlib.animation.FuncAnimation(
                self.fig, self._update, interval=120, cache_frame_data=False)
            self.fig.canvas.mpl_connect("key_press_event", self._on_key)
        atexit.register(self._close_log)

    # ---------- 12 扇区（研究模式，口径与车上一致） ----------
    @staticmethod
    def _sector12(buf):
        """12 个 30° 扇区的稳健最小：s_k = [k*30-15, k*30+15)，s0 跨 0°。
        复用 d6_protocol._robust_min（k=2，与车上 scan_stats 同一套）。"""
        out = []
        for k in range(12):
            lo = (k * 30.0 - 15.0) % 360.0
            hi = (k * 30.0 + 15.0) % 360.0
            vals = []
            for p in buf:
                if p[1] is None:
                    continue
                a = p[0] % 360.0
                if (lo <= a < hi) if lo < hi else (a >= lo or a < hi):
                    vals.append(p[1])
            out.append((d6_protocol._robust_min(vals) if vals else None, len(vals)))
        return out

    # ---------- CSV 日志 ----------
    def _open_log(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"云图12扇区_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        self._csv_f = open(path, "w", newline="", encoding="utf-8-sig")
        self._csv = csv.writer(self._csv_f)
        head = ["t_ms"]
        for k in range(12):
            head += [f"s{k:02d}_min", f"s{k:02d}_n"]
        head += ["front", "left", "right", "action"]
        self._csv.writerow(head)
        self._log_path = path
        print(f"[log] 12扇区数据 → {path}")

    def _log_row(self, s12, st, act):
        if self._csv is None:
            return
        now = time.time()
        if now - self._last_log < 0.2:          # 5Hz 足够研究用
            return
        self._last_log = now
        row = [int(now * 1000)]
        for m, n in s12:
            row += ["" if m is None else f"{m:.3f}", n]
        if st:
            row += [("" if st[k][0] is None else f"{st[k][0]:.3f}")
                    for k in ("front", "left", "right")] + [act]
        else:
            row += ["", "", "", ""]
        self._csv.writerow(row)
        if int(now * 10) % 20 == 0:             # 每 2s 落一次盘
            self._csv_f.flush()

    def _close_log(self):
        if self._csv_f is not None:
            try:
                self._csv_f.close()
            except Exception:
                pass
            self._csv_f = None

    # ---------- 静态元素 ----------
    def _build_fig(self):
        self.fig = plt.figure(figsize=(14.2, 7.6))
        gs = self.fig.add_gridspec(1, 2, width_ratios=[1.62, 1.0], wspace=0.12)
        self.ax = self.fig.add_subplot(gs[0, 0])
        self.ax.set_aspect("equal")
        self.ax.grid(alpha=0.22, lw=0.6)
        self.ax.set_xlabel("x 左右 (m)", fontsize=9)
        self.ax.set_ylabel("y 前后 (m)", fontsize=9)
        self.ax.tick_params(labelsize=8)

        car = self.car
        clear_fov, _angs, mask = car.clear_fov()
        self.clear_fov = clear_fov
        self.occ_runs = runs_of(mask, 0.5)

        # 背景扇形一律画到很大，缩放时自动铺满
        r_bg = 12.0
        for lo, hi in self.occ_runs:
            self.ax.add_patch(Wedge((0, 0), r_bg, 90 - hi, 90 - lo,
                                    facecolor=C_BAD, alpha=0.13,
                                    edgecolor="none", zorder=1))
        self._sec_lbls = []
        for sec, col, lb in ((d6_protocol.SECTOR_FRONT, "#3b7dd8", "前 F"),
                             (d6_protocol.SECTOR_LEFT, "#2e9e5b", "左 L"),
                             (d6_protocol.SECTOR_RIGHT, "#e0a020", "右 R")):
            lo, hi = sec
            self.ax.add_patch(Wedge((0, 0), r_bg, 90 - hi, 90 - lo,
                                    facecolor=col, alpha=0.09, edgecolor=col,
                                    lw=1.0, ls=":", zorder=2))
            mid = ((lo + (hi + 360 if hi < lo else hi)) / 2) % 360
            self._sec_lbls.append((self.ax.text(0, 0, lb, ha="center", va="center",
                                                fontsize=10.5, color=col,
                                                fontweight="bold", zorder=7), mid))

        # 阈值圈（不写字，避免原点附近文字打架，改用图例）
        cfg = protocol.CFG
        th = np.radians(np.arange(0, 361, 1))
        for r, c, ls in ((cfg["danger_m"], C_BAD, "--"),
                         (cfg["passable_m"], C_WARN, ":")):
            self.ax.plot(np.sin(th) * r, np.cos(th) * r, ls=ls, lw=1.2,
                         color=c, alpha=0.9, zorder=3)

        # 车体轮廓 + 雷达 + 车头方向
        ox, oy = car.outline()
        ox, oy = ox - car.radar[0], oy - car.radar[1]
        self.ax.add_patch(Polygon(np.c_[ox, oy], closed=True, facecolor="#e9e9e9",
                                  edgecolor="#2b2b2b", lw=2.0, zorder=5))
        self.ax.plot([0], [0], marker="*", ms=15, color="#111",
                     markeredgecolor="w", markeredgewidth=0.8, zorder=6)
        self.ax.annotate("", xy=(0, 0.16), xytext=(0, 0.035),
                         arrowprops=dict(arrowstyle="-|>", color="#555", lw=1.4),
                         zorder=6)
        self.ax.text(0.02, 0.17, "车头", fontsize=8.5, color="#555")

        # 点层
        self.sc_old = self.ax.scatter([], [], s=6, c="#8a949e", alpha=0.38,
                                      linewidths=0, zorder=4)
        self.sc_new = self.ax.scatter([], [], s=11, c="#1f4fd8", alpha=0.95,
                                      linewidths=0, zorder=6)

        # 图例（把所有颜色含义集中在这里，图上不再散落文字）
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        self.ax.legend(
            handles=[
                Patch(facecolor=C_BAD, alpha=0.20, label="车体遮挡"),
                Patch(facecolor="#3b7dd8", alpha=0.22, label="前扇区 F"),
                Patch(facecolor="#2e9e5b", alpha=0.22, label="左扇区 L"),
                Patch(facecolor="#e0a020", alpha=0.22, label="右扇区 R"),
                Line2D([], [], color=C_BAD, ls="--", lw=1.2,
                       label=f"危险 {cfg['danger_m']}m"),
                Line2D([], [], color=C_WARN, ls=":", lw=1.2,
                       label=f"可通行 {cfg['passable_m']}m"),
                Line2D([], [], marker="o", ls="", color="#1f4fd8", ms=5,
                       label="点云"),
            ],
            loc="lower left", fontsize=8, framealpha=0.92, borderpad=0.6,
            labelspacing=0.45, handlelength=1.6)
        self.txt_view = self.ax.text(0.985, 0.985, "", ha="right", va="top",
                                     fontsize=8.5, color="#666",
                                     transform=self.ax.transAxes)

        # ---------- 右栏：状态 / 12扇区极坐标 / 动作 ----------
        gs_r = gs[0, 1].subgridspec(3, 1, height_ratios=[0.60, 1.70, 0.62],
                                    hspace=0.46)
        self.axr = self.fig.add_subplot(gs_r[0])
        self.axr.axis("off")
        self.txt_info = self.axr.text(0.0, 1.0, "", va="top", ha="left",
                                      fontsize=10, linespacing=1.6, clip_on=True)

        # 12 扇区极坐标（0°=车头朝上，顺时针，与 D6 同向）
        self.axp = self.fig.add_subplot(gs_r[1], projection="polar")
        self.axp.set_theta_zero_location("N")
        self.axp.set_theta_direction(-1)
        self.axp.set_ylim(0, 3.0)
        self.bars12 = self.axp.bar([math.radians(k * 30.0) for k in range(12)],
                                   [0.1] * 12, width=math.radians(25), bottom=0.0,
                                   color=C_GREY, alpha=0.92, edgecolor="white",
                                   linewidth=0.6, zorder=3)
        thc = np.linspace(0, 2 * np.pi, 181)
        for r, c, ls in ((cfg["danger_m"], C_BAD, "--"),
                         (cfg["passable_m"], C_WARN, ":")):
            self.axp.plot(thc, [r] * len(thc), color=c, lw=1.0, ls=ls,
                          alpha=0.85, zorder=2)
        self.axp.set_thetagrids(np.arange(0, 360, 30),
                                [f"s{k}" for k in range(12)], fontsize=7,
                                color="#8a8a8a")
        self.axp.set_rgrids([0.5, 1.0, 1.5, 2.0, 2.5], fontsize=6.5,
                            color="#c0c0c0", angle=15)
        self.axp.set_title("12 扇区最小距离（30°/个）", fontsize=10, pad=22)
        # 每个扇区中心角处的数值（水平排布，落在环内，不和标签打架）
        self.lbl_val12 = [
            self.axp.text(math.radians(k * 30.0), 2.62, "--", ha="center",
                          va="center", fontsize=7.5, color="#333",
                          fontweight="bold", zorder=5)
            for k in range(12)]

        # 动作卡片
        axa = self.fig.add_subplot(gs_r[2])
        axa.axis("off")
        axa.set_xlim(0, 1)
        axa.set_ylim(0, 1)
        self.act_box = plt.Rectangle((0.03, 0.42), 0.94, 0.54,
                                     transform=axa.transAxes, facecolor="#eeeeee",
                                     edgecolor="none", zorder=1)
        axa.add_patch(self.act_box)
        self.txt_act = axa.text(0.5, 0.69, "--", ha="center", va="center",
                                fontsize=26, fontweight="bold", zorder=3)
        self.txt_why = axa.text(0.5, 0.14, "", ha="center", va="center",
                                fontsize=9.5, color="#444", zorder=3)

        mount_desc = ("几何中心" if car.mount == "center"
                      else f"前极点外 {self.args.fwd:g}cm")
        self.fig.suptitle(
            f"雷达迷你云图 · 车体 {self.args.car_len:g}×{self.args.car_wid:g}cm "
            f"超椭圆 n={self.args.shape_n:g} · 雷达装在{mount_desc} · "
            f"前方净空 {self.clear_fov:.0f}°",
            fontsize=13, fontweight="bold", y=0.985)
        self.fig.text(0.5, 0.012,
                      "a 累积/拖尾 · c 清空 · z 缩放视野 · s 截图 · q 退出　　"
                      "红色扇形=车体遮挡（这些方向的回波是车自己，必须屏蔽）",
                      ha="center", fontsize=9.5, color="#555")
        self._apply_view()

    def _apply_view(self):
        """按当前视野半径重设坐标范围，并把扇区标签放到视野内侧。"""
        v = self.view_r
        self.ax.set_xlim(-v, v)
        self.ax.set_ylim(-v, v * 1.06)
        for obj, mid in self._sec_lbls:
            rm = math.radians(mid)
            obj.set_position((v * 0.86 * math.sin(rm), v * 0.86 * math.cos(rm)))
        self.txt_view.set_text(f"视野 ±{v:g} m（z 切换）")

    # ---------- 每帧 ----------
    def _update(self, _frame=None):
        if self.src is None:
            return
        buf, fok, fbad, hz = self.src.snapshot()
        now = time.time()
        new_xy, old_xy = [], []
        for p in buf:
            if p[1] is None:
                continue
            d = p[1] / 1000.0
            x, y = d * math.sin(math.radians(p[0])), d * math.cos(math.radians(p[0]))
            if self.accumulate:
                self.map_pts.append((x, y))
                if len(self.map_pts) > MAP_MAX_PTS:
                    self.map_pts = self.map_pts[::2]
            else:
                self.trail.append((x, y, now))
        if self.accumulate:
            pts = self.map_pts
            old_xy = pts
        else:
            cut = now - TRAIL_SECS
            old_xy = [(x, y) for x, y, t in self.trail if t > cut]

        # 最近 0.6s 的新点（拖尾模式下从 trail 里挑，累积模式从 map 最后一圈挑）
        if self.accumulate:
            new_xy = old_xy[-400:]
        else:
            cut2 = now - 0.6
            new_xy = [(x, y) for x, y, t in self.trail if t > cut2]

        self.sc_old.set_offsets(np.array(old_xy).reshape(-1, 2) if old_xy
                                else np.empty((0, 2)))
        self.sc_new.set_offsets(np.array(new_xy).reshape(-1, 2) if new_xy
                                else np.empty((0, 2)))

        # 决策：走真实 scan_stats（一圈缓冲）—— 车上当前输入
        cfg = protocol.CFG
        st = d6_protocol.scan_stats(list(buf)) if len(buf) >= 100 else None
        # 研究模式：12×30° 扇区（口径与车上一致的稳健最小）
        s12 = self._sector12(buf) if len(buf) >= 100 else None
        # net 模式：板上自己算好的 12 扇区优先（PC 只是显示器，不重算一遍）
        remote, r_age = None, None
        if self.src is not None and getattr(self.src, "mode", "") == "net":
            r_age = time.time() - self.src.remote_t if self.src.remote_t else None
            if self.src.remote and r_age is not None and r_age < 1.0:
                remote = self.src.remote
                rs = remote.get("sectors")
                if rs and len(rs) == 12:
                    s12 = [(v[0], v[1]) for v in rs]
        if s12 is not None:
            bands = ((cfg["danger_m"], C_BAD), (cfg["passable_m"], C_WARN),
                     (2.0, "#3b7dd8"))
            for k, (rect, (m, _n)) in enumerate(zip(self.bars12, s12)):
                if m is None:
                    rect.set_height(0.06)
                    rect.set_facecolor("#c9c9c9")
                    self.lbl_val12[k].set_text("--")
                    self.lbl_val12[k].set_color("#999")
                    continue
                rect.set_height(min(m, 2.45))
                for lim, col in bands:
                    if m < lim:
                        rect.set_facecolor(col)
                        break
                else:
                    rect.set_facecolor("#9aa0a6")
                self.lbl_val12[k].set_text(f"{m:.2f}")
                self.lbl_val12[k].set_color(col if m < 2.0 else "#666")
        if st:
            f, l, r = st["front"][0], st["left"][0], st["right"][0]
            act, why, col = decide(f, l, r, cfg)
            self.txt_act.set_text(act)
            self.txt_act.set_color(col)
            self.txt_why.set_text(why)
            self.act_box.set_facecolor(col)
            self.act_box.set_alpha(0.12)
            fmt = lambda v: "无回波" if v is None else f"{v:.2f}m"
            n_f, n_l, n_r = st["front"][1], st["left"][1], st["right"][1]
        else:
            f = l = r = None
            n_f = n_l = n_r = 0
            fmt = lambda v: "--"
            self.txt_act.set_text("…")
            self.txt_act.set_color("#999")
            self.act_box.set_facecolor("#eeeeee")
            self.act_box.set_alpha(1.0)
            self.txt_why.set_text("等待数据")

        # net 模式：板上真实决策覆盖本地估算 —— 屏幕上必须是"车实际会怎么做"
        if remote is not None:
            rd = remote.get("radar") or {}
            if rd.get("front_distance") is not None or rd.get("valid"):
                f = rd.get("front_distance")
                l = rd.get("left_distance")
                r = rd.get("right_distance")
            act = remote.get("action") or "STOP"
            col = {"STOP": C_BAD, "SLOW": C_WARN, "LEFT": C_OK,
                   "RIGHT": C_OK, "FORWARD": C_OK}.get(act, C_GREY)
            self.txt_act.set_text(act)
            self.txt_act.set_color(col)
            self.act_box.set_facecolor(col)
            self.act_box.set_alpha(0.12)
            self.txt_why.set_text(f"{remote.get('rule', '')}  {remote.get('why', '')}")

        mode = "累积" if self.accumulate else "拖尾3s"
        src_desc = ("DEMO" if self.args.demo
                    else (getattr(self.args, "tcp", None) or self.args.port))
        # 只留 3 行：数据源/健康度 · 三扇区 · 几何信息
        if remote is not None:
            v = remote.get("vision") or {}
            stt = remote.get("status") or {}
            v_txt = (f"视觉有人·{v.get('side', '?')}"
                     f"({v.get('class', '?')} {v.get('conf', 0):.2f})"
                     if v.get("obstacle") else "视觉空")
            self.txt_info.set_text(
                f"车上 {src_desc} · {hz:.0f} 帧/s · 延迟 {(r_age or 0)*1000:.0f}ms"
                f" · {mode}\n"
                f"前F {fmt(f)}({n_f})   左L {fmt(l)}({n_l})   右R {fmt(r)}({n_r})\n"
                f"{v_txt} · 链路 {'断' if remote.get('fault') else '通'}"
                f" · 电池 {stt.get('vbat', '--')}V · {remote.get('rule', '')}")
        else:
            self.txt_info.set_text(
                f"{src_desc} · {hz:.0f} 帧/s · 坏帧 {fbad}/{fok + fbad} · {mode}\n"
                f"前F {fmt(f)}({n_f})   左L {fmt(l)}({n_l})   右R {fmt(r)}({n_r})\n"
                f"净空 {self.clear_fov:.0f}° · 扫掠 {self.car.sweep_radius()*100:.0f}cm"
                f" · 缓冲 {len(buf)}点")
        if s12 is not None:
            self._log_row(s12, st, act if remote is not None else (act if st else ""))
        self.fig.canvas.draw_idle()

        # 自检用：--auto-close N 秒后自己关窗（证明窗口链路真的跑起来了）
        ac = getattr(self.args, "auto_close", 0) or 0
        if ac and time.time() - self._t0 > ac:
            print(f"[auto-close] 已运行 {ac:g}s，关闭窗口")
            plt.close(self.fig)

    def _on_key(self, ev):
        if ev.key == "a":
            self.accumulate = not self.accumulate
            print(f"[mode] {'累积' if self.accumulate else '拖尾'}")
        elif ev.key == "z":
            i = self._view_steps.index(self.view_r) if self.view_r in self._view_steps else 1
            self.view_r = self._view_steps[(i + 1) % len(self._view_steps)]
            self._apply_view()
            print(f"[view] 视野 ±{self.view_r:g} m")
        elif ev.key == "c":
            self.map_pts.clear()
            print("[mode] 累积已清空")
        elif ev.key == "s":
            import datetime
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             f"云图截图_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
            self.fig.savefig(p, dpi=140, facecolor="white")
            print("[OK] 已保存", p)
        elif ev.key == "q":
            plt.close(self.fig)


# ================================================================ 自检
def selftest(args):
    """无窗口：合成 4 秒数据 → 渲染 → 存 PNG → 打印几何报告 + 像素体检。"""
    matplotlib.use("Agg")
    car = CarShape(car_len=args.car_len / 100.0, car_wid=args.car_wid / 100.0,
                   n_exp=args.shape_n, mount=args.mount, fwd=args.fwd / 100.0)
    clear_fov, angs, mask = car.clear_fov()
    occ_runs = runs_of(mask, 0.5)

    print("=" * 64)
    print(f"车体 {args.car_len:g}×{args.car_wid:g}cm 超椭圆 n={args.shape_n:g}，"
          f"雷达 mount={args.mount} fwd={args.fwd:g}cm")
    print(f"  遮挡区间 : {['%.1f°~%.1f°' % r for r in occ_runs]}")
    print(f"  前方净空 : {clear_fov:.1f}°   扫掠半径: {car.sweep_radius()*100:.1f}cm")
    ov = car.overlap_with_sectors()
    for k, v in ov.items():
        flag = "  ← 🔴 与遮挡重叠，装车会死锁" if v > 1.0 else ""
        print(f"  扇区 {k:5s} 落在遮挡里的角度: {v:.1f}°{flag}")
    if args.shape_n == 2.0 and car.mount == "front":
        ana = math.degrees(analytic_ellipse_occlusion_halfwidth(args.fwd / 100.0))
        print(f"  [校验] n=2 解析切线半宽 = {ana:.2f}° "
              f"(数值法 {180 - clear_fov / 2:.2f}°，应一致)")
    print("=" * 64)

    view = MapView(args, interactive=False)
    src = RadarSource("synth")
    view.src = src
    time.sleep(4.5)                    # 攒 ~45 圈
    view.accumulate = True             # 自检出"地图"样子
    view._update()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "云图selftest.png")
    view.fig.savefig(out, dpi=130, facecolor="white")

    # ---- 12 扇区验证（合成场景几何已知）----
    buf, _fok, _fbad, _hz = src.snapshot()
    s12 = MapView._sector12(buf)
    print("12 扇区稳健最小（合成场景）:")
    for k, (m, n) in enumerate(s12):
        lo, hi = k * 30 - 15, k * 30 + 15
        rng = "345~15" if k == 0 else f"{lo}~{hi}"
        print(f"  s{k:>2} [{rng:>7}°] {n:>3}点  "
              f"{'--' if m is None else f'{m:.2f} m'}")
    # 合成场景已知几何：圆柱(0.15,0.5,r0.12)张角3.4°~30°跨s0/s1 → 两扇区≈0.40；
    # 纸箱A最近角(−0.27,0.75)→s11≈0.80；身后墙y=−1→s6≈1.00；左右墙→s3≈1.20
    m0, m1, m11, m6 = s12[0][0], s12[1][0], s12[11][0], s12[6][0]
    ok12 = (all(n > 0 for _m, n in s12)
            and m0 is not None and m0 < 0.6          # 圆柱跨进 s0
            and m1 is not None and m1 < 0.6          # 圆柱主体在 s1
            and m11 is not None and m11 < 1.5        # 纸箱A
            and m6 is not None and 0.8 < m6 < 1.2)   # 身后墙
    print("[PASS 12扇区]" if ok12 else "[FAIL 12扇区]",
          f"s0={m0}, s1={m1}, s11={m11}, s6={m6}")
    src.alive = False

    from PIL import Image
    im = Image.open(out).convert("L")
    arr = np.asarray(im)
    nonwhite = float((arr < 245).mean())
    print(f"[OK] 自检图: {out}  {im.size[0]}x{im.size[1]}px  非白像素 {nonwhite*100:.1f}%")
    ok = nonwhite > 0.02 and src.frames_ok > 30
    print("[PASS]" if ok else "[FAIL]", f"frames_ok={src.frames_ok}")
    return 0 if (ok and ok12) else 1


# ================================================================ 入口
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=d6_protocol.PORT)
    ap.add_argument("--demo", action="store_true", help="合成场景，不接雷达")
    ap.add_argument("--tcp", default=None, metavar="HOST:PORT",
                    help="连板上 board_main.py 的监控端口（默认 5002），"
                         "实现「车上产数据、PC 显示」")
    ap.add_argument("--selftest", action="store_true", help="无窗口自检")
    ap.add_argument("--mount", choices=["front", "center"], default="front")
    ap.add_argument("--fwd", type=float, default=3.0, help="雷达在前极点外多少 cm")
    ap.add_argument("--car-len", type=float, default=35.0)
    ap.add_argument("--car-wid", type=float, default=18.0)
    ap.add_argument("--shape-n", type=float, default=2.2,
                    help="超椭圆指数：2=纯椭圆，越大越方")
    ap.add_argument("--log", action="store_true",
                    help="把 12 扇区+三扇区+动作 以 5Hz 记入 CSV")
    ap.add_argument("--auto-close", type=float, default=0.0,
                    help="自检用：N 秒后自动关窗")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest(args))

    # 交互模式先验后端：没有 GUI 后端时 plt.show() 会「静默什么都不显示」
    # （venv 里的 Python 3.13 就没 tkinter，matplotlib 会退回 Agg —— 2026-10-25 踩过）
    be = matplotlib.get_backend()
    if be.lower() in ("agg", "pdf", "svg", "ps", "cairo", "template"):
        print(f"[FAIL] 绘图后端是 {be}（无窗口后端），窗口无法显示。")
        print("       原因：当前 Python 没有 tkinter / Qt 等 GUI 工具包。")
        print(r"       解决：换用带 tkinter 的解释器，如 "
              r"envs\gui312\Scripts\python.exe")
        return 2
    print(f"绘图后端: {be}")

    view = MapView(args, interactive=True)
    if args.tcp:
        mode = f"板上监控 {args.tcp}"
    elif args.demo:
        mode = "DEMO 合成场景"
    else:
        mode = f"雷达 {args.port}"
    print(f"迷你云图启动 · {mode}")
    print(f"  前方净空 {view.clear_fov:.0f}°；快捷键 a 累积 / z 缩放 / c 清空 / s 截图 / q 退出")
    if args.tcp:
        print("  连不上：确认板子上 board_main.py 在跑、IP 对、没被防火墙挡")
    elif not args.demo:
        print("  若无数据：检查 COM 口、雷达上电、DTR 未拉高")
    plt.show()
    view.src.alive = False
    return 0


if __name__ == "__main__":
    sys.exit(main())
