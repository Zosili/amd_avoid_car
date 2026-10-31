# -*- coding: utf-8 -*-
"""mount_compare_shape.py —— 安装位置对比（椭圆 vs 方形车体）修正版

背景：郑说明车体实际是"椭圆形的变体"而非方形。
此前 install_tradeoff.py 按**方形**算出"前伸越多前方视野越窄"——
这个结论对方形成立（车头两个直角把视野切掉），但对**椭圆恰好相反**
（表面光滑无棱角，雷达越往前，车体张角反而越小）。

本图用 radar_map_view.CarShape 的数值光线投射（任意超椭圆 n 都成立）重算：
  (a) 净空角 vs 前伸距离，三条曲线（纯椭圆 n=2 / 椭圆变体 n=2.2 / 近方形 n=8）
  (b) 椭圆 n=2.2 前极点外 3cm 俯视：净空绿 / 遮挡红
  (c) 近方形 n=8 前伸 3cm 俯视
  (d) 椭圆 + 雷达装几何中心（低装）：全遮挡 0° —— 直觉里"最完美"其实最差
注：n=8 仍有圆角；真直角矩形更糟（解析值：3cm→143°，10cm→84°）。
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Polygon
from matplotlib.gridspec import GridSpec

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radar_map_view import CarShape, runs_of   # noqa: E402  复用同一套几何代码

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_OK, C_BAD = "#2e9e5b", "#d64545"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "安装对比_椭圆vs方形.png")

SHAPES = [(2.0, "#1f77b4", "纯椭圆 n=2"),
          (2.2, "#2ca02c", "椭圆变体 n=2.2（你的车）"),
          (8.0, "#d62728", "近方形 n=8（旧假设，仍有圆角）")]


def clear_fov_of(n, fwd_cm, mount="front"):
    car = CarShape(n_exp=n, mount=mount, fwd=fwd_cm / 100.0)
    fov, _angs, _mask = car.clear_fov(0.5)
    return fov


fig = plt.figure(figsize=(13.2, 10.2))
gs = GridSpec(2, 3, figure=fig, height_ratios=[1.0, 1.05],
              hspace=0.30, wspace=0.10)

# ---------------------------------------------------------------- (a) 曲线
ax = fig.add_subplot(gs[0, :])
fs = [0, 1, 2, 3, 4, 5, 6, 8, 10]
for n, col, lb in SHAPES:
    vals = [clear_fov_of(n, f) for f in fs]
    ax.plot(fs, vals, "-o", ms=4.5, lw=2.2, color=col, label=lb)
ax.axhline(0, color="#888", lw=1.2, ls=":")
ax.text(9.9, 14, "低装在几何中心 = 0°（全部光线打壳）", ha="right",
        fontsize=9.5, color="#888")
ax.axvline(3.0, color="#2ca02c", lw=1.2, ls=":")
ax.set_xlabel("雷达在车头极点外伸出多少 (cm)", fontsize=11)
ax.set_ylabel("前方连续净空角 (°)", fontsize=11)
ax.set_title("(a) 前伸距离 vs 净空角 —— 方形与椭圆结论相反", fontsize=12.5,
             fontweight="bold")
ax.set_xlim(-0.3, 10.3)
ax.set_ylim(-18, 340)
ax.grid(alpha=0.3)
ax.legend(loc="lower left", fontsize=10, framealpha=0.95)
ax.annotate("近方形：同前伸下始终最差；\n真直角矩形更糟（3cm→143°，10cm→84°）",
            xy=(10, 289), xytext=(5.9, 130), fontsize=9.5, color="#d62728",
            arrowprops=dict(arrowstyle="->", color="#d62728"))
ax.annotate("椭圆：光滑表面是加分项\n3cm→275°，10cm→312°",
            xy=(10, 312), xytext=(5.6, 250), fontsize=9.5, color="#2ca02c",
            arrowprops=dict(arrowstyle="->", color="#2ca02c"))


# ---------------------------------------------------------------- 俯视三联
def top_view(axx, n, fwd_cm, mount, title, sub):
    car = CarShape(n_exp=n, mount=mount, fwd=fwd_cm / 100.0)
    fov, _angs, mask = car.clear_fov(0.5)
    R = 1.55
    for lo, hi in runs_of(mask, 0.5):
        axx.add_patch(Wedge((0, 0), R, 90 - hi, 90 - lo, facecolor=C_BAD,
                            alpha=0.20, edgecolor="none"))
    axx.add_patch(Wedge((0, 0), R, 90 - fov / 2, 90 + fov / 2,
                        facecolor=C_OK, alpha=0.14, edgecolor="none"))
    ox, oy = car.outline()
    axx.add_patch(Polygon(np.c_[ox - car.radar[0], oy - car.radar[1]],
                          closed=True, facecolor="#dcdcdc",
                          edgecolor="#333", lw=1.6, zorder=5))
    axx.plot([0], [0], marker="*", ms=15, color="#111",
             markeredgecolor="w", zorder=6)
    axx.text(0, -R * 0.80, title, ha="center", fontsize=11, fontweight="bold")
    axx.text(0, -R * 0.93, sub, ha="center", fontsize=10, color="#333")
    axx.set_xlim(-R, R)
    axx.set_ylim(-R, R)
    axx.set_aspect("equal")
    axx.set_xticks([])
    axx.set_yticks([])
    return fov


fov_b = top_view(fig.add_subplot(gs[1, 0]), 2.2, 3.0, "front",
                 "(b) 椭圆变体 · 前极点外3cm", "★当前位置，推荐")
fov_c = top_view(fig.add_subplot(gs[1, 1]), 8.0, 3.0, "front",
                 "(c) 近方形 n=8 · 前伸3cm", "旧假设，棱角吃掉视野")
fov_d = top_view(fig.add_subplot(gs[1, 2]), 2.2, 0.0, "center",
                 "(d) 装几何中心（低装）", "雷达在壳内 = 全遮挡")

fig.suptitle("雷达安装位置对比（椭圆车体修正版）· 35×18cm 超椭圆 · 数值光线投射",
             fontsize=14, fontweight="bold", y=0.985)
fig.text(0.5, 0.012,
         "结论：① 车体中部低装 = 全遮挡（雷达在壳内，任何方向的光都先打到车壳），绝对不行；"
         "② 椭圆车体下前伸反而扩大视野（与方形相反）；"
         "③ 唯一能同时拿到 360° 的办法是抬到车体上沿之上，此时中部安装才成立且最优雅。",
         ha="center", fontsize=9.5, color="#555")
fig.subplots_adjust(left=0.04, right=0.985, top=0.90, bottom=0.075)
fig.savefig(OUT, dpi=150, facecolor="white")
print("[OK]", OUT)

# 控制台数字表
print("\n净空角对比（度）")
print(f"{'前伸cm':>6} | {'纯椭圆 n=2':>10} | {'变体 n=2.2':>10} | {'近方形 n=8':>10}")
print("-" * 48)
for f in (0, 1, 3, 5, 8, 10):
    row = [clear_fov_of(n, f) for n, _c, _l in SHAPES]
    print(f"{f:>6.0f} | {row[0]:>10.0f} | {row[1]:>10.0f} | {row[2]:>10.0f}")
print(f"\n(b)={fov_b:.0f}°  (c)={fov_c:.0f}°  (d)={fov_d:.0f}°")
