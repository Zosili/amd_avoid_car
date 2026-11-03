# -*- coding: utf-8 -*-
"""
radar/radar_view.py —— COIN-D6 云图（车体坐标系 / 与 car7020 同一套角度约定）

和 `d6_stream.py` 共用同一份 `d6_config.json`，所以**画出来的方向、掩膜、姿态
与发给队友融合的数据完全一致**（0° = 车头，逆时针为正）。

用法
----
    # 实时窗口（需要图形后端：gui312 环境）
    python radar_view.py

    # 无窗口出一张图（给报告/给没装 GUI 的环境）
    python radar_view.py --save cloud.png --secs 4

    # 没有雷达时看界面长什么样
    python radar_view.py --demo

    # 指定串口/姿态（覆盖 d6_config.json）
    python radar_view.py --port COM5 --mount inverted --mask 137:223

快捷键（窗口里按）
------------------
    q 退出   c 清空累积   a 累积/单圈切换   z 视野 0.8/1.5/3.0/6.0 m   s 存图

画面上都有什么
--------------
    彩色点云        本圈扫描（亮度=距离）
    灰色扇环        12 个 30° 扇区（s0 = 345°~15°），斜纹=被 --mask 屏蔽
    两条虚线圈      1 m / 3 m —— 队友 PL 的 STOP / WARN 阈值
    白框            车体轮廓（35 × 18 cm 超椭圆，车头朝上）
    红十字          前方 ±35° 走廊内的最近点（就是发给 PL 的那个距离）
    右栏            帧率、圈速、有效点、12 扇区数值、距离帧、对应 PL 状态
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import matplotlib
if "--save" in sys.argv:            # 出图模式：不要求图形后端（服务器/无桌面也能跑）
    matplotlib.use("Agg")
import matplotlib.animation as animation                      # 显式导入（踩过的坑）
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Wedge

# 中文字形：默认字体没有 CJK，不设会满屏 "Glyph missing"（坑位 §9.2）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import d6_protocol as d6
import d6_stream as ds

CAR_LEN, CAR_WID, CAR_N = 0.35, 0.18, 2.2      # 车体 35×18 cm，超椭圆 n=2.2
SECTOR_DEG = 30.0
SECTOR_ZERO = 345.0                            # s0 = 345°~15°，与 d6_protocol 一致


# ------------------------------------------------------------------ 数据源
class LivePoller:
    """后台读串口 → 每圈一份 (angles, ranges)。掉线自动重连。"""

    def __init__(self, port, baud, rb):
        import threading
        self.port, self.baud, self.rb = port, baud, rb
        self.lock = threading.Lock()
        self.latest = None
        self.frames = 0
        self.bad = 0
        self.revs = 0
        self.hz = 0.0
        self._stop = threading.Event()
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        ser = None
        while not self._stop.is_set():
            if ser is None:
                try:
                    ser = ds.open_serial(self.port, self.baud)
                    print("[雷达] %s 已连接" % self.port)
                except Exception as e:
                    print("[雷达] %s 打不开：%s" % (self.port, str(e)[:60]))
                    time.sleep(1.0)
                    continue
            try:
                fr = d6.read_frame(ser)
            except Exception as e:
                print("[雷达] 掉线：%s" % str(e)[:60])
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                self.latest = None
                time.sleep(0.5)
                continue
            if fr is None:
                self.bad += 1
                continue
            pts = d6.parse_frame_ex(*fr)
            if pts is None:
                self.bad += 1
                continue
            self.frames += 1
            with self.lock:
                rev = self.rb.feed(pts)
                if rev is not None:
                    self.latest = rev
                    self.revs = self.rb.revs
                    self.hz = self.rb.hz
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            return self.latest, self.frames, self.bad, self.revs, self.hz

    def stop(self):
        self._stop.set()


class DemoPoller:
    """没有雷达时的合成数据：正前方 2.4 m 一个人，左前 1.1 m 一堵墙。"""

    def __init__(self, rb, hz=10.0):
        self.rb, self.hz = rb, hz
        self.latest = self._make()
        self.frames = 0
        self.bad = 0
        self.revs = 0

    def _make(self):
        ang = np.arange(0, 360, 0.5)
        rng = np.full(ang.shape, np.nan)
        rng[np.abs(((ang - 0.0 + 180) % 360) - 180) <= 8] = 2.40       # 正前方的人
        rng[(ang > 40) & (ang < 70)] = 1.10                            # 左前方墙
        rng[(ang > 100) & (ang < 250)] = 0.30                          # 侧后方车体
        return ang, rng

    def snapshot(self):
        self.revs += 1
        return self.latest, self.frames, self.bad, self.revs, self.hz

    def stop(self):
        pass


# ------------------------------------------------------------------ 画面
def car_outline(a_len=CAR_LEN, a_wid=CAR_WID, n=CAR_N, pts=120):
    """超椭圆车体轮廓（以雷达为原点，车头朝 +y）。"""
    t = np.linspace(0, 2 * np.pi, pts)
    ct, st = np.cos(t), np.sin(t)
    x = (abs(ct) ** (2.0 / n)) * np.sign(ct) * (a_wid / 2.0)
    y = (abs(st) ** (2.0 / n)) * np.sign(st) * (a_len / 2.0)
    return np.column_stack([x, y])


def polar_to_xy(ang_deg, r):
    a = np.deg2rad(ang_deg)
    return r * np.sin(a), r * np.cos(a)


class CloudView:
    def __init__(self, args, cfg):
        self.args = args
        self.view_r = 1.5
        self.view_steps = [0.8, 1.5, 3.0, 6.0]
        self.accum = False
        self.map_pts = []
        self.fig = plt.figure(figsize=(11.6, 6.4))
        self.ax = self.fig.add_axes([0.035, 0.06, 0.56, 0.88])
        self.panel = self.fig.add_axes([0.62, 0.06, 0.36, 0.88])
        self.panel.axis("off")
        self._setup_ax()
        self.fig.canvas.mpl_connect("key_press_event", self._on_key)

    def _setup_ax(self):
        ax = self.ax
        ax.set_aspect("equal")
        ax.set_xlabel("x / m（右为正）")
        ax.set_ylabel("y / m（车头朝上）")
        ax.grid(True, ls=":", lw=0.6, alpha=0.6)
        # 扇区（斜纹=被 mask）
        masked = self.args.mask or []
        for k in range(12):
            lo = (SECTOR_ZERO + k * SECTOR_DEG) % 360.0
            if lo > 270:
                th1, th2 = 90 - (lo - 270), 90 - (lo - 270) + SECTOR_DEG
            else:
                th1, th2 = 90 - lo - SECTOR_DEG, 90 - lo
            is_masked = any(
                (mlo <= lo < mhi) if mlo <= mhi else (lo >= mlo or lo < mhi)
                for mlo, mhi in masked)
            w = Wedge((0, 0), 6.0, th1, th2, width=6.0,
                      facecolor=(0.55, 0.55, 0.55, 0.16) if is_masked else (0.2, 0.5, 0.9, 0.05),
                      edgecolor="none", hatch="///" if is_masked else None)
            ax.add_patch(w)
        # 阈值圈：队友 PL 的 1 m / 3 m
        for r, c in ((1.0, "#c0392b"), (3.0, "#d68910"), (0.35, "#7f8c8d")):
            ax.add_patch(Circle((0, 0), r, fill=False, ls="--", lw=1.0, ec=c, alpha=0.85))
        # 车体
        ax.add_patch(Polygon(car_outline(), closed=True, fill=False, ec="white",
                             lw=1.6, zorder=5))
        # 动态元素
        self.sc = ax.scatter([], [], s=5, c=[], cmap="viridis", vmin=0, vmax=3.0, zorder=4)
        self.near_pt, = ax.plot([], [], "x", color="#e74c3c", ms=12, mew=2, zorder=6)
        self.sector_txt = []
        for k in range(12):
            mid = (SECTOR_ZERO + k * SECTOR_DEG + SECTOR_DEG / 2) % 360.0
            x, y = polar_to_xy(mid, 3.05 if k % 2 == 0 else 2.88)
            self.sector_txt.append(
                ax.text(x, y, "s%d" % k, fontsize=7.5, color="#34495e", ha="center", va="center"))
        self.head, = ax.plot([], [], color="#2c3e50", lw=1.2)
        ax.set_xlim(-3.35, 3.35)
        ax.set_ylim(-3.45, 3.55)

    def _on_key(self, ev):
        if ev.key == "q":
            plt.close(self.fig)
        elif ev.key == "c":
            self.map_pts.clear()
        elif ev.key == "a":
            self.accum = not self.accum
            if not self.accum:
                self.map_pts.clear()
        elif ev.key == "z":
            i = self.view_steps.index(self.view_r) if self.view_r in self.view_steps else 1
            self.view_r = self.view_steps[(i + 1) % len(self.view_steps)]
            self.ax.set_xlim(-self.view_r, self.view_r)
            self.ax.set_ylim(-self.view_r * 0.75, self.view_r * 1.12)
        elif ev.key == "s":
            fn = "radar_cloud_%s.png" % time.strftime("%Y%m%d_%H%M%S")
            self.fig.savefig(fn, dpi=130)
            print("已存", fn)

    def draw(self, scan, stats):
        ang, rng = scan
        ok = np.isfinite(rng)
        a = ang[ok]
        r = rng[ok]
        x, y = polar_to_xy(a, r)
        keep = (np.abs(x) < 9) & (np.abs(y) < 9)
        if self.accum:
            self.map_pts.extend(zip(x[keep], y[keep]))
            self.map_pts = self.map_pts[-8000:]
            if self.map_pts:
                mx, my = zip(*self.map_pts)
                self.sc.set_offsets(np.column_stack([mx, my]))
                self.sc.set_array(np.hypot(mx, my))
        else:
            self.sc.set_offsets(np.column_stack([x[keep], y[keep]]) if keep.any() else np.empty((0, 2)))
            self.sc.set_array(r[keep])

        # 前方走廊最近点（发给 PL 的那个）
        d_mm = ds.front_nearest(ang, rng, self.args.dist_corridor)
        if d_mm is not None:
            d = d_mm / 1000.0
            corr = np.abs(((a - 0.0 + 180) % 360) - 180) <= self.args.dist_corridor
            if corr.any():
                i = np.argmin(np.where(corr, r, np.inf))
                nx, ny = polar_to_xy(a[i], r[i])
                self.near_pt.set_data([nx], [ny])
            self.head.set_data(polar_to_xy([0, 0], [0, d]))
        else:
            self.near_pt.set_data([], [])
            self.head.set_data([], [])

        # 12 扇区数值
        cells = []
        for k in range(12):
            lo = (SECTOR_ZERO + k * SECTOR_DEG) % 360.0
            m = ((ang - lo) % 360.0) < SECTOR_DEG
            v = rng[m & np.isfinite(rng)]
            cells.append(None if v.size == 0 else float(np.median(v)))
            self.sector_txt[k].set_text(
                "s%d\n%s" % (k, "--" if cells[-1] is None else "%.2f" % cells[-1]))
            self.sector_txt[k].set_color("#bdc3c7" if cells[-1] is None else "#2c3e50")

        frames, bad, revs, hz = stats
        st = ds.pl_state_from_dist(d_mm).split("(")[0]
        frame = " ".join("%02X" % b for b in ds.pack_dist_frame(d_mm))
        self.panel.clear()
        self.panel.axis("off")
        lines = [
            ("COIN-D6 云图（车体坐标系）", 13, "bold", "#111"),
            ("0° = 车头  逆时针为正  与 car7020 融合同一套约定", 9, "normal", "#555"),
            ("", 6, "normal", "#555"),
            ("姿态  %s   sign=%+d   theta0=%.1f°" % (
                "倒装" if self.args.mount == "inverted" else "正装",
                self.args.sign, self.args.theta0), 10, "normal", "#111"),
            ("掩膜  %s" % (self.args.mask or "无"), 10, "normal", "#111"),
            ("", 6, "normal", "#555"),
            ("帧 %d  坏 %d   圈 %d   圈速 %.1f Hz" % (frames, bad, revs, hz), 10, "normal", "#111"),
            ("本圈有效点 %d / %d" % (int(ok.sum()), len(rng)), 10, "normal", "#111"),
            ("前方 ±%.0f° 最近  %s" % (
                self.args.dist_corridor, "--" if d_mm is None else "%.2f m" % (d_mm / 1000.0)),
             11, "bold", "#c0392b"),
            ("距离帧  %s   →  %s" % (frame, st), 10, "normal", "#111"),
            ("", 6, "normal", "#555"),
            ("12 扇区（中位距离 / m）", 10, "bold", "#111"),
        ]
        for i in range(0, 12, 4):
            lines.append(("   ".join("s%-2d %5s" % (j, "--" if cells[j] is None else "%.2f" % cells[j])
                                     for j in range(i, i + 4)), 10, "normal", "#111"))
        lines += [
            ("", 6, "normal", "#555"),
            ("虚线：1 m STOP / 3 m WARN（队友 PL 阈值）", 8.5, "normal", "#666"),
            ("斜纹扇区 = 车体遮挡，已屏蔽", 8.5, "normal", "#666"),
            ("按键 q退出  c清空  a累积  z缩放  s存图", 8.5, "normal", "#666"),
        ]
        y = 0.98
        for txt, size, weight, color in lines:
            if txt:
                self.panel.text(0.0, y, txt, fontsize=size, fontweight=weight,
                                color=color, family="Microsoft YaHei",
                                va="top", ha="left", transform=self.panel.transAxes)
            y -= size * 0.0038 + (0.004 if txt else 0.002)


# ------------------------------------------------------------------ 入口
def main():
    cfg = ds.load_config()
    ap = argparse.ArgumentParser(description="COIN-D6 云图（车体坐标系）")
    ap.add_argument("--port", default="auto")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--sign", type=int, default=-1, choices=[1, -1])
    ap.add_argument("--theta0", type=float, default=0.0)
    ap.add_argument("--mount", default="normal", choices=["normal", "inverted"])
    ap.add_argument("--mask", default="")
    ap.add_argument("--bin-deg", type=float, default=0.5)
    ap.add_argument("--dist-corridor", type=float, default=35.0)
    ap.add_argument("--demo", action="store_true", help="用合成数据看界面")
    ap.add_argument("--save", default=None, help="不开窗口，直接存一张 PNG")
    ap.add_argument("--secs", type=float, default=0, help="配合 --save：先采 N 秒")
    if cfg:
        allowed = {"port", "baud", "sign", "theta0", "mount", "mask", "bin_deg", "dist_corridor"}
        ap.set_defaults(**{k: v for k, v in cfg.items() if k in allowed})
    a = ap.parse_args()

    explicit_sign = any(s == "--sign" or s.startswith("--sign=") for s in sys.argv[1:])
    if (a.mount == "inverted") and not explicit_sign:
        a.sign = -a.sign
    a.mask = ds.parse_mask(a.mask)

    rb = ds.RevBuilder(a.bin_deg, a.theta0, a.sign, a.mask)
    view = CloudView(a, cfg)

    if a.save:
        collector = DemoPoller(rb) if a.demo else LivePoller(a.port, a.baud, rb)
        secs = a.secs or 4.0
        t0 = time.time()
        while time.time() - t0 < secs:
            scan = collector.snapshot()[0]
            if scan is not None:
                last = scan
            time.sleep(0.1)
        collector.stop()
        scan = collector.snapshot()[0] or getattr(locals().get("last", None), "__class__", None) and last
        if scan is None:
            print("!! 没采到完整一圈，无法存图")
            return 1
        view.draw(scan, collector.snapshot()[1:])
        view.fig.savefig(a.save, dpi=130, bbox_inches="tight")
        print("已存 %s" % a.save)
        return 0

    collector = DemoPoller(rb) if a.demo else LivePoller(a.port, a.baud, rb)

    def _tick(_i):
        scan = collector.snapshot()[0]
        if scan is not None:
            view.draw(scan, collector.snapshot()[1:])
        return []

    anim = animation.FuncAnimation(view.fig, _tick, interval=120, cache_frame_data=False)
    view.fig.suptitle("COIN-D6 云图  %s" % ("合成数据" if a.demo else a.port), fontsize=9, y=0.995)
    try:
        plt.show()
    finally:
        collector.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
