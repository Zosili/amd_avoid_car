# -*- coding: utf-8 -*-
"""radar_decision_view.py —— 把原始点云翻译成「决策视图」

为什么要这个工具
----------------
上位机的极坐标云图画的是 400 个原始点/圈，而决策链只吃 3 个数字：
前扇区、左扇区、右扇区的「稳健最小距离」。云图看起来乱是正常的
（噪声、地面反射、物体边缘多路径、自身支架都会画成点），
但云图里根本看不到决策信息。这个工具把两者并排画出来。

复用 car/ 下真实代码：
  protocol.CFG            阈值（danger_m / warn_m / passable_m）
  d6_protocol.SECTOR_*    扇区定义
  d6_protocol._robust_min 稳健最小（第 k 小，默认 k=2，去掉最噪的一个）

用法
----
    python radar_decision_view.py --port COM5 --secs 5
    python radar_decision_view.py --port COM5 --secs 5 --out my.png

输出
----
一张 4 面板 PNG：原始点云 / 扇区化 / 盲区示意 / 决策结论
"""
import argparse
import os
import sys
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

CAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "car")
if CAR not in sys.path:
    sys.path.insert(0, CAR)

import protocol                      # noqa: E402
from radar import d6_protocol        # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_OK, C_WARN, C_BAD, C_GREY = "#2e9e5b", "#e0a020", "#d64545", "#9aa0a6"


# ---------------------------------------------------------------- 采集
def collect(port, secs, snap_dt=0.15):
    """读雷达 secs 秒，返回 (全部点, 快照列表)。点 = (angle_deg, dist_m)。"""
    import serial
    ser = serial.Serial(port, d6_protocol.BAUD, bytesize=8, parity="N",
                        stopbits=1, timeout=0.2)
    ser.dtr = False          # 官方手册：DTR 拉高会触发雷达硬件复位
    ser.rts = False
    scan, snaps = [], []
    t0 = time.time()
    last_snap = 0.0
    try:
        while time.time() - t0 < secs:
            fr = d6_protocol.read_frame(ser)
            if fr is None:
                continue
            pts = d6_protocol.parse_frame_ex(*fr)
            if not pts:
                continue
            scan.extend(pts)
            if len(scan) > 400:
                scan = scan[-400:]
            now = time.time()
            if now - last_snap >= snap_dt and len(scan) >= 300:
                last_snap = now
                snaps.append(list(scan))
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return scan, snaps


def split_points(scan):
    """(angle, dist_m, intensity) 三数组，丢掉无回波点。"""
    a, d, i = [], [], []
    for p in scan:
        if p[1] is None:
            continue
        a.append(float(p[0]))
        d.append(p[1] / 1000.0)
        i.append(float(p[2]) if len(p) > 2 and p[2] is not None else 0.0)
    return np.array(a), np.array(d), np.array(i)


def sector_series(snaps):
    """对每个快照算三扇区稳健最小 → dict[name] = np.array(米)"""
    out = {"front": [], "left": [], "right": []}
    for s in snaps:
        st = d6_protocol.scan_stats(s)
        for k in out:
            out[k].append(st[k][0])
    return {k: np.array([np.nan if v is None else v for v in v]) for k, v in out.items()}


def decide(front, left, right, cfg):
    """按 protocol.CFG 复现 machine.py 的纯雷达判定（不含视觉）。
    返回 (动作, 原因, 颜色)"""
    danger, warn, passable = cfg["danger_m"], cfg["warn_m"], cfg["passable_m"]
    if front is None:
        return "STOP", "前扇区无回波（可能全被吸收/超量程）", C_BAD
    if front < danger:
        return "STOP", f"前方 {front:.2f}m < 危险阈值 {danger:.2f}m", C_BAD
    l_ok = left is not None and left > passable
    r_ok = right is not None and right > passable
    if front < warn:
        if not l_ok and not r_ok:
            return "STOP", (f"前方 {front:.2f}m 需减速，但左右都不足 {passable:.2f}m"
                            f"（左 {left:.2f}m / 右 {right:.2f}m）"), C_BAD
        return "SLOW", f"前方 {front:.2f}m < 警惕阈值 {warn:.2f}m，减速观察", C_WARN
    return "FORWARD", f"前方 {front:.2f}m 通畅（左 {left:.2f}m / 右 {right:.2f}m）", C_OK


# ---------------------------------------------------------------- 绘图
def draw(port, scan, snaps, out_path):
    cfg = protocol.CFG
    ang, dist, inten = split_points(scan)
    series = sector_series(snaps)

    # 用中位数代表「稳态」输入，避开单帧抖动
    med = {k: (np.nanmedian(v) if len(v) and not np.all(np.isnan(v)) else np.nan)
           for k, v in series.items()}
    act, why, acol = decide(med["front"], med["left"], med["right"], cfg)

    fig = plt.figure(figsize=(15.5, 10.5), dpi=110)
    gs = GridSpec(2, 3, figure=fig, width_ratios=[1.25, 1.0, 1.0],
                  height_ratios=[1.0, 0.75], hspace=0.28, wspace=0.26)

    # ---------- 面板1：原始点云（极坐标）----------
    ax1 = fig.add_subplot(gs[:, 0], projection="polar")
    ax1.set_theta_zero_location("N")
    ax1.set_theta_direction(-1)          # 顺时针增大，符合 D6 约定
    ax1.set_ylim(0, min(3.0, max(1.0, np.nanmax(dist) * 1.15)))
    ax1.set_title("① 原始点云（上位机云图看到的）\n"
                  f"{len(ang)} 个点 · 灰=全部原始点",
                  fontsize=12, pad=18)
    ax1.scatter(np.radians(ang), dist, s=6, c=C_GREY, alpha=0.45,
                label="原始点（含噪声/支架/地面）")

    # 三扇区扇形底色
    for sec, col, name in ((d6_protocol.SECTOR_FRONT, "#3b7dd8", "前"),
                           (d6_protocol.SECTOR_LEFT, "#2e9e5b", "左"),
                           (d6_protocol.SECTOR_RIGHT, "#e0a020", "右")):
        lo, hi = sec
        if lo <= hi:
            th = np.radians(np.arange(lo, hi + 0.5, 0.5))
        else:
            th = np.radians(np.concatenate([np.arange(lo, 360, 0.5),
                                            np.arange(0, hi + 0.5, 0.5)]))
        ax1.fill_between(th, 0, ax1.get_ylim()[1], color=col, alpha=0.10, zorder=0)

    # 盲区（前方半圆内没被任何扇区覆盖）
    for lo, hi in ((d6_protocol.SECTOR_FRONT[1], d6_protocol.SECTOR_LEFT[0]),
                   (d6_protocol.SECTOR_RIGHT[1], d6_protocol.SECTOR_FRONT[0])):
        th = np.radians(np.arange(lo, hi + 0.5, 0.5))
        ax1.fill_between(th, 0, ax1.get_ylim()[1], color=C_BAD, alpha=0.18, zorder=0)
        mid = np.radians((lo + hi) / 2)
        ax1.text(mid, ax1.get_ylim()[1] * 0.82, "盲区", ha="center", va="center",
                 fontsize=9, color=C_BAD, fontweight="bold")

    # 阈值圆
    th_all = np.radians(np.arange(0, 360, 1))
    for r, c, lb in ((cfg["danger_m"], C_BAD, f"危险 {cfg['danger_m']}m"),
                     (cfg["passable_m"], C_WARN, f"可通行 {cfg['passable_m']}m"),
                     (cfg["warn_m"], "#888888", f"警惕 {cfg['warn_m']}m")):
        ax1.plot(th_all, np.full_like(th_all, r), color=c, lw=1.0, ls="--", alpha=0.8)
    ax1.text(np.radians(200), cfg["danger_m"], f"危险 {cfg['danger_m']}m",
             fontsize=8, color=C_BAD)
    ax1.text(np.radians(200), cfg["passable_m"] + 0.06, f"可通行 {cfg['passable_m']}m",
             fontsize=8, color=C_WARN)
    ax1.text(np.radians(200), cfg["warn_m"] + 0.06, f"警惕 {cfg['warn_m']}m",
             fontsize=8, color="#666666")
    ax1.legend(loc="lower left", bbox_to_anchor=(-0.12, -0.06), fontsize=9, framealpha=0.9)

    # ---------- 面板2：三扇区时间序列 ----------
    ax2 = fig.add_subplot(gs[0, 1])
    tt = np.arange(len(next(iter(series.values())))) * 0.15
    for k, col, lb in (("front", "#3b7dd8", "前 345°~15°"),
                       ("left", "#2e9e5b", "左 30°~90°"),
                       ("right", "#e0a020", "右 270°~330°")):
        ax2.plot(tt, series[k], color=col, lw=1.6, label=lb, marker="o", ms=2.5)
    ax2.axhline(cfg["danger_m"], color=C_BAD, ls="--", lw=1, label=f"危险 {cfg['danger_m']}m")
    ax2.axhline(cfg["passable_m"], color=C_WARN, ls=":", lw=1, label=f"可通行 {cfg['passable_m']}m")
    ax2.axhline(cfg["warn_m"], color="#888888", ls="--", lw=1, label=f"警惕 {cfg['warn_m']}m")
    ax2.set_xlabel("时间 (s)", fontsize=10)
    ax2.set_ylabel("稳健最小距离 (m)", fontsize=10)
    ax2.set_title("② 三扇区稳健最小距离（决策真正吃的 3 个数）", fontsize=12)
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8.5, ncol=2, loc="best")

    # ---------- 面板3：三扇区当前值 ----------
    ax3 = fig.add_subplot(gs[0, 2])
    names = ["前", "左", "右"]
    vals = [med["front"], med["left"], med["right"]]
    cols = []
    for k, v in zip(("front", "left", "right"), vals):
        if np.isnan(v):
            cols.append(C_BAD)
        elif k == "front":
            cols.append(C_BAD if v < cfg["danger_m"] else
                        (C_WARN if v < cfg["warn_m"] else C_OK))
        else:
            cols.append(C_OK if v > cfg["passable_m"] else C_BAD)
    bars = ax3.bar(names, [0 if np.isnan(v) else v for v in vals],
                   color=cols, width=0.55, edgecolor="white", lw=1.5)
    for b, v in zip(bars, vals):
        ax3.text(b.get_x() + b.get_width() / 2,
                 0 if np.isnan(v) else v, "无回波" if np.isnan(v) else f"{v:.2f}m",
                 ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax3.axhline(cfg["danger_m"], color=C_BAD, ls="--", lw=1.2)
    ax3.axhline(cfg["passable_m"], color=C_WARN, ls=":", lw=1.2)
    ax3.set_ylim(0, max(1.6, np.nanmax([v for v in vals if not np.isnan(v)] or [1]) * 1.35))
    ax3.set_ylabel("距离 (m)", fontsize=10)
    ax3.set_title("③ 稳态输入（各扇区中位）", fontsize=12)
    ax3.grid(axis="y", alpha=0.3)

    # ---------- 面板4：决策结论 ----------
    ax4 = fig.add_subplot(gs[1, 1:])
    ax4.axis("off")
    ax4.add_patch(plt.Rectangle((0.02, 0.10), 0.96, 0.80, transform=ax4.transAxes,
                                facecolor=acol, alpha=0.10, edgecolor=acol, lw=2))
    ax4.text(0.5, 0.70, act, transform=ax4.transAxes, ha="center", va="center",
             fontsize=34, fontweight="bold", color=acol)
    ax4.text(0.5, 0.42, why, transform=ax4.transAxes, ha="center", va="center",
             fontsize=12.5, color="#333333", wrap=True)
    ax4.text(0.5, 0.18, "（仅雷达单源判定，未含视觉；实际 machine.py 还有 R1/R2/R3/R4 视觉融合分支）",
             transform=ax4.transAxes, ha="center", va="center", fontsize=9, color="#777777")
    ax4.set_title("④ 雷达单源会给出的动作", fontsize=12)

    fig.suptitle(f"雷达决策视图 · {port} · {len(snaps)} 个快照 / {len(ang)} 个原始点",
                 fontsize=14, fontweight="bold", y=0.975)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return med, act, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=d6_protocol.PORT)
    ap.add_argument("--secs", type=float, default=5.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.out is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"决策视图_{ts}.png")

    print(f"读雷达 {args.port} {args.secs}s ...")
    scan, snaps = collect(args.port, args.secs)
    if not scan:
        print("[FAIL] 没读到数据，检查串口/雷达")
        return 1
    print(f"  原始点 {len(scan)}  快照 {len(snaps)}")

    med, act, why = draw(args.port, scan, snaps, args.out)
    print()
    print("三扇区稳健最小（中位）:")
    for k, lb in (("front", "前 345~15"), ("left", "左 30~90"), ("right", "右 270~330")):
        v = med[k]
        print(f"  {lb:14} {'无回波' if np.isnan(v) else f'{v:.3f} m'}")
    print()
    print(f"雷达单源动作: {act}")
    print(f"原因: {why}")
    print()
    print(f"图已保存: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
