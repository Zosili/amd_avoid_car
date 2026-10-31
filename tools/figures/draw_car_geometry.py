# -*- coding: utf-8 -*-
"""draw_car_geometry.py —— 按车体尺寸算雷达遮挡区，并画安装示意图

为什么需要它
------------
2D 雷达装在小车上，车体自己会挡住一部分扫描空间。挡多少取决于
「雷达装在哪个位置」。这个脚本按车体长宽和雷达安装偏置，算出：

  1. 车体在雷达视野里占的角度范围（= 必须屏蔽的遮挡扇区）
  2. 前方还剩多少可用角度
  3. 车体外接圆半径（= 任何方向的安全硬下限）
  4. 推荐的三扇区边界（覆盖全部可用角度，不留盲区）
  5. 推荐的三个阈值

用法
----
    python draw_car_geometry.py                       # 默认 35x18，车头前伸 3cm
    python draw_car_geometry.py --len 35 --wid 18 --fwd 3
    python draw_car_geometry.py --len 35 --wid 18 --fwd 3 --out my.png
"""
import argparse
import math
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Rectangle, Circle

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_FRONT, C_LEFT, C_RIGHT = "#3b7dd8", "#2e9e5b", "#e0a020"
C_BLOCK, C_BAD = "#b0b0b0", "#d64545"


def analyze(car_len_cm, car_wid_cm, fwd_cm):
    """以雷达为原点、车头朝 +Y、顺时针为正。返回几何分析结果（单位：米/度）。"""
    L, W, F = car_len_cm / 100.0, car_wid_cm / 100.0, fwd_cm / 100.0
    hw = W / 2.0
    # 车体矩形（雷达在原点）：Y 从 -(L-F) 到 +F，X 从 -hw 到 +hw
    y_front, y_rear = F, -(L - F)
    corners = [(hw, y_front), (-hw, y_front), (hw, y_rear), (-hw, y_rear)]
    # 车体各角相对雷达的极坐标
    polar = [(math.hypot(x, y), math.degrees(math.atan2(x, y)) % 360) for x, y in corners]
    # 车头两角的张角 = 前方可用视野的边界
    front_left_ang = math.degrees(math.atan2(hw, F)) % 360        # 左侧车头角
    front_right_ang = 360 - front_left_ang                        # 右侧车头角
    # 车尾两角（y_rear 是负数，直接用，别再取反）
    rear_left_ang = math.degrees(math.atan2(hw, y_rear)) % 360
    # 转向扫掠半径：车绕自身几何中心旋转时，最远角到中心的距离
    # ⚠️ 不是「车体最远角到雷达的距离」——那是 33cm，会把阈值算得过分保守
    r_circum = math.hypot(L / 2.0, W / 2.0)
    y_center = (y_front + y_rear) / 2.0
    return {
        "L": L, "W": W, "F": F, "hw": hw, "y_front": y_front, "y_rear": y_rear,
        "y_center": y_center,
        "corners": corners, "polar": polar,
        "open_deg": front_left_ang,          # 前方可用 ±open_deg
        "block_from": front_left_ang,        # 左侧遮挡起点
        "block_to": rear_left_ang,           # 左侧遮挡终点
        "r_circum": r_circum,
    }


def recommend(g, v_max=0.5, decel=2.0):
    """按几何推阈值。v_max m/s，decel m/s²。"""
    brake = (v_max ** 2) / (2 * decel)        # 刹车距离
    danger = g["r_circum"] + brake + 0.05      # 外接圆 + 刹车 + 5cm 余量
    warn = danger + v_max * 1.0                # 约 1 秒反应距离
    passable = g["r_circum"] + 0.05            # 侧向：刚过外接圆 + 5cm
    return {"danger": round(danger, 2), "warn": round(warn, 2),
            "passable": round(passable, 2), "brake": round(brake, 3)}


def sectors(g):
    """把 -open~+open 均分成 前/左/右 三个扇区，无盲区。"""
    o = g["open_deg"]
    return {"front": (-o / 3, o / 3),
            "left": (o / 3, o),
            "right": (-o, -o / 3)}


def draw(g, rec, sec, car_len_cm, car_wid_cm, fwd_cm, out):
    fig = plt.figure(figsize=(14, 8.2), dpi=110)
    ax = fig.add_subplot(1, 2, 1)
    ax.set_aspect("equal")

    rmax = max(1.0, g["y_rear"] * -1 * 1.15, rec["warn"] * 1.15)
    ax.set_xlim(-rmax, rmax)
    ax.set_ylim(-rmax * 1.05, rmax * 1.05)

    def wedge(lo, hi, r, color, alpha, label=None, ls=None):
        # 极坐标角度（0=上，顺时针）→ matplotlib theta1/theta2（0=右，逆时针）
        t1 = 90 - hi
        t2 = 90 - lo
        ax.add_patch(Wedge((0, 0), r, t1, t2, width=None,
                           facecolor=color, alpha=alpha, edgecolor=color,
                           lw=1.0 if ls else 0, ls=ls or "-"))
        if label:
            mid = math.radians((lo + hi) / 2)
            ax.text(r * 0.72 * math.sin(mid), r * 0.72 * math.cos(mid), label,
                    ha="center", va="center", fontsize=11, fontweight="bold",
                    color=color)

    # 车体遮挡扇区（灰）
    wedge(g["block_from"], g["block_to"], rmax, C_BLOCK, 0.28, "车体遮挡\n（必须屏蔽）")
    wedge(-g["block_to"], -g["block_from"], rmax, C_BLOCK, 0.28)

    # 三个决策扇区
    for k, col, lb in (("front", C_FRONT, "前"), ("left", C_LEFT, "左斜"), ("right", C_RIGHT, "右斜")):
        lo, hi = sec[k]
        wedge(lo, hi, rec["warn"], col, 0.16, lb)

    # 距离圈
    for r, c, txt in ((rec["danger"], C_BAD, f"危险 {rec['danger']}m"),
                      (rec["passable"], "#e0a020", f"侧向可通行 {rec['passable']}m"),
                      (rec["warn"], "#888888", f"警惕 {rec['warn']}m")):
        th = np.linspace(0, 2 * np.pi, 400)
        ax.plot(r * np.sin(th), r * np.cos(th), ls="--", lw=1.0, color=c, alpha=0.85)
        ax.text(r * math.sin(math.radians(-118)), r * math.cos(math.radians(-118)),
                txt, fontsize=8.5, color=c, ha="center")

    # 转向扫掠圆（以车体几何中心为圆心，不是雷达）
    ax.add_patch(Circle((0, g["y_center"]), g["r_circum"], fill=False,
                        ls=":", lw=1.2, color="#555555"))
    ax.text(g["r_circum"] * math.sin(math.radians(150)),
            g["y_center"] + g["r_circum"] * math.cos(math.radians(150)),
            f"转向扫掠 {g['r_circum']*100:.1f}cm", fontsize=8.5, color="#555555",
            ha="center")

    # 车体
    ax.add_patch(Rectangle((-g["hw"], g["y_rear"]), g["W"], g["y_front"] - g["y_rear"],
                           facecolor="#d9d9d9", edgecolor="#333333", lw=1.8, zorder=5))
    ax.text(0, (g["y_front"] + g["y_rear"]) / 2, f"车体\n{car_len_cm}×{car_wid_cm}cm",
            ha="center", va="center", fontsize=11, fontweight="bold", zorder=6)

    # 雷达
    ax.plot(0, 0, marker="*", ms=20, color="#d64545", zorder=8)
    ax.text(0.04, 0.04, "雷达", fontsize=10, color="#d64545", fontweight="bold", zorder=8)

    # 车头方向箭头
    ax.annotate("", xy=(0, g["y_front"] + 0.18), xytext=(0, g["y_front"] + 0.02),
                arrowprops=dict(arrowstyle="-|>", color="#333333", lw=2))
    ax.text(0, g["y_front"] + 0.21, "车头 0°", ha="center", fontsize=10, fontweight="bold")

    # 车头角标线
    for sgn, ang in ((1, g["open_deg"]), (-1, -g["open_deg"])):
        ax.plot([0, sgn * g["hw"]], [0, g["y_front"]], color="#666666", ls="-.", lw=1.1)
        ax.text(sgn * (g["hw"] + 0.05), g["y_front"] + 0.04,
                f"{abs(ang):.0f}°", fontsize=9, color="#666666", ha="center")

    ax.set_title(f"雷达安装几何 · 车 {car_len_cm}×{car_wid_cm}cm · 车头前伸 {fwd_cm}cm",
                 fontsize=13, fontweight="bold")
    ax.grid(alpha=0.2)
    ax.set_xlabel("左右 (m)")
    ax.set_ylabel("前后 (m)")

    # ---- 右图：数据表 ----
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.axis("off")
    lines = [
        ("【车体在雷达视野里的遮挡】", "", "#000000"),
        ("车头两角张角", f"±{g['open_deg']:.1f}°", "#d64545"),
        ("→ 前方可用视野", f"-{g['open_deg']:.0f}° ~ +{g['open_deg']:.0f}°"
                          f"（共 {2*g['open_deg']:.0f}°）", C_FRONT),
        ("左侧遮挡", f"{g['block_from']:.0f}° ~ {g['block_to']:.0f}°", C_BLOCK),
        ("右侧遮挡", f"-{g['block_to']:.0f}° ~ -{g['block_from']:.0f}°", C_BLOCK),
        ("", "", "#000000"),
        ("【推荐扇区（覆盖全部可用角度，无盲区）】", "", "#000000"),
        ("前", f"{sec['front'][0]:+.0f}° ~ {sec['front'][1]:+.0f}°", C_FRONT),
        ("左斜", f"{sec['left'][0]:+.0f}° ~ {sec['left'][1]:+.0f}°", C_LEFT),
        ("右斜", f"{sec['right'][0]:+.0f}° ~ {sec['right'][1]:+.0f}°", C_RIGHT),
        ("", "", "#000000"),
        ("【推荐阈值（按几何推，v_max=0.5m/s, a=2m/s²）】", "", "#000000"),
        ("车体外接圆半径", f"{g['r_circum']*100:.1f} cm（任何方向硬下限）", "#555555"),
        ("刹车距离", f"{rec['brake']*100:.1f} cm", "#555555"),
        ("danger_m 前向危险", f"{rec['danger']:.2f} m", C_BAD),
        ("warn_m   前向警惕", f"{rec['warn']:.2f} m", "#e0a020"),
        ("passable_m 侧向可通行", f"{rec['passable']:.2f} m", C_LEFT),
    ]
    y = 0.96
    for a, b, c in lines:
        if not a and not b:
            y -= 0.022
            continue
        if b == "":
            ax2.text(0.02, y, a, fontsize=11.5, fontweight="bold", color=c,
                     transform=ax2.transAxes)
        else:
            ax2.text(0.04, y, a, fontsize=10.5, color="#222222", transform=ax2.transAxes)
            ax2.text(0.52, y, b, fontsize=10.5, fontweight="bold", color=c,
                     transform=ax2.transAxes)
        y -= 0.052

    ax2.text(0.02, 0.03,
             "！遮挡扇区必须在软件里屏蔽，否则车体自己会被当成障碍\n"
             "！遮挡区回波距离若突然变短 = 车体碰到东西，可当碰撞检测用",
             fontsize=9, color="#d64545", transform=ax2.transAxes, va="bottom")

    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--len", type=float, default=35.0, help="车长 cm")
    ap.add_argument("--wid", type=float, default=18.0, help="车宽 cm")
    ap.add_argument("--fwd", type=float, default=3.0, help="雷达相对车头前伸 cm")
    ap.add_argument("--vmax", type=float, default=0.5, help="最大速度 m/s")
    ap.add_argument("--decel", type=float, default=2.0, help="制动减速度 m/s²")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    g = analyze(args.len, args.wid, args.fwd)
    rec = recommend(g, args.vmax, args.decel)
    sec = sectors(g)

    if args.out is None:
        args.out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "车体几何与扇区_%gx%g.png" % (args.len, args.wid))

    print(f"车体 {args.len}x{args.wid}cm，雷达前伸 {args.fwd}cm")
    print(f"  车体外接圆半径 : {g['r_circum']*100:.2f} cm")
    print(f"  车头角张角     : ±{g['open_deg']:.2f}°  → 前方可用 {2*g['open_deg']:.0f}°")
    print(f"  左侧遮挡       : {g['block_from']:.1f}° ~ {g['block_to']:.1f}°")
    print(f"  推荐扇区       : 前 {sec['front'][0]:+.0f}~{sec['front'][1]:+.0f} / "
          f"左 {sec['left'][0]:+.0f}~{sec['left'][1]:+.0f} / "
          f"右 {sec['right'][0]:+.0f}~{sec['right'][1]:+.0f}")
    print(f"  推荐阈值       : danger {rec['danger']}m / warn {rec['warn']}m / passable {rec['passable']}m")
    print(f"  图已保存       : {args.out}")
    draw(g, rec, sec, args.len, args.wid, args.fwd, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
