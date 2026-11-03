# -*- coding: utf-8 -*-
"""
融合链路端到端仿真
==================
雷达还没到货，先用几何仿真把整条链路验证一遍：
    场景(走廊 + 一个人)  ->  光线投射出雷达扫描
                         ->  透视投影出 bbox
                         ->  FusionEngine 估距 + 出状态
                         ->  和真值对比

能验证到的东西：
  * 安装偏航角 theta0 不标定会错成什么样（这是最容易出事的一环）
  * 雷达只扫到腿、甚至从两腿之间漏过去时的退化表现
  * 单目兜底能不能补上雷达的空窗
  * 1m/3m 阈值 + 迟滞 + 确认帧的实际时序对不对

用法：
  python fusion/sim_check.py                 # 标准场景（人正面走近）
  python fusion/sim_check.py --legs          # 细腿场景（模拟雷达漏检）
  python fusion/sim_check.py --theta0-bad    # 故意不标定，看错成什么样
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fusion_core import (DecisionConfig, FusionEngine, Scan, State,
                         fx_from_hfov, ground_distance)

# ------------------------- 场景参数（世界系：x 前，y 左，z 上） -------------------------
CORRIDOR_HALF_W = 1.2   # 走廊半宽
FAR_WALL_X = 9.0        # 尽头墙
CAM_H = 0.20            # 相机/雷达离地高度（小车底盘）
THETA0_TRUE = 27.0      # 雷达自身 0 度的真实指向（故意不是 0）
LIDAR_RES = 1.0         # 角度分辨率（度），约等于 RPLIDAR A1
BODY_W = 0.22           # 人体半宽
BODY_H = 1.70           # 身高
LEG_R = 0.05            # 单腿半径
LEG_SEP = 0.10          # 腿中心到身体中线距离


def ray_range(beta_deg: float, px: float, py: float, legs: bool) -> float:
    """沿世界方向 beta 打一条射线，返回最近障碍距离。beta 是 CW 为正的屏幕角？不，是标准 CCW。"""
    b = math.radians(beta_deg)
    dx, dy = math.cos(b), math.sin(b)
    best = 1e9

    # 远端墙 x = FAR_WALL_X
    if dx > 1e-6:
        best = min(best, FAR_WALL_X / dx)
    # 两侧墙 y = ±CORRIDOR_HALF_W
    if abs(dy) > 1e-6:
        best = min(best, CORRIDOR_HALF_W / abs(dy))

    # 人：一个圆柱（或两条腿）
    if legs:
        cyls = [(px, py - LEG_SEP, LEG_R), (px, py + LEG_SEP, LEG_R)]
    else:
        cyls = [(px, py, BODY_W)]
    for cx, cy, r in cyls:
        # 解 |t*d - c|^2 = r^2
        proj = dx * cx + dy * cy
        per2 = (cx * cx + cy * cy) - proj * proj
        if per2 <= r * r:
            t = proj - math.sqrt(max(0.0, r * r - per2))
            if t > 0.05:
                best = min(best, t)
    return best


def make_scan(px: float, py: float, legs: bool, theta0: float,
              res: float = LIDAR_RES, noise: float = 0.01,
              rng: np.random.Generator | None = None,
              occlusion: float = 0.0) -> Scan:
    """生成一帧雷达扫描。reported_angle = theta0 + world_beta。

    occlusion: 落在人身上的回波被丢弃的比例。真实雷达打深色裤子、裤腿偏细、
    或者波束正好从两腿之间穿过时就是这个效果，用来验证单目兜底。
    """
    angs = np.arange(0.0, 360.0, res)
    ranges = np.full(angs.shape, np.inf)
    for i, th in enumerate(angs):
        beta = th - theta0
        r = ray_range(beta, px, py, legs)
        if occlusion > 0.0 and rng is not None and r < FAR_WALL_X - 0.5:
            # 打在人身上（明显比墙近）的回波有概率丢失 -> 变成无效点
            if rng.random() < occlusion:
                r = np.inf
        ranges[i] = r
    ranges = ranges + (rng.normal(0, noise, ranges.shape) if rng else 0.0)
    return Scan(angs, ranges)


def project_bbox(px: float, py: float, w: int = 640, h: int = 480,
                 hfov: float = 60.0):
    """把 3D 人投影成像素 bbox。返回 (x1,y1,x2,y2, 是否出画)。"""
    fx = fx_from_hfov(w, hfov)
    cx, cy = w / 2.0, h / 2.0
    if px <= 0.05:
        return None
    u1 = cx - fx * (py + BODY_W) / px
    u2 = cx - fx * (py - BODY_W) / px
    v_top = cy - fx * (BODY_H - CAM_H) / px
    v_bot = cy + fx * CAM_H / px
    clipped = (u1 < 0) or (u2 > w) or (v_bot > h)
    return (min(u1, u2), max(v_top, 0.0), max(u1, u2), min(v_bot, h - 1), clipped)


def run(legs: bool, bad_calib: bool, fps: float = 25.0, occlusion: float = 0.0):
    hfov = 60.0
    cfg = DecisionConfig(
        cam_w=640, cam_h_px=480, hfov_deg=hfov,
        cam_height_m=CAM_H,
        theta0=0.0 if bad_calib else THETA0_TRUE,
        ang_sign=-1,
        stop_m=1.0, warn_m=3.0, hyst_m=0.15,
        confirm_frames=5, release_s=1.0, corridor_deg=35.0,
    )
    eng = FusionEngine(cfg)
    rng = np.random.default_rng(7)

    # 场景：远处站出来 -> 走到 0.7m -> 转身走开
    traj = []
    for x in np.arange(5.0, 0.7, -0.05):
        traj.append((x, 0.0))
    for x in np.arange(0.7, 5.0, 0.05):
        traj.append((x, 0.0))

    rows, prev_state = [], None
    t = 0.0
    for i, (px, py) in enumerate(traj):
        t = i / fps
        scan = make_scan(px, py, legs, THETA0_TRUE, rng=rng, occlusion=occlusion)
        bb = project_bbox(px, py, 640, 480, hfov)
        persons = [] if bb is None else [{"bbox": bb[:4], "conf": 0.9}]
        res = eng.update(t, persons, scan)
        truth = math.hypot(px, py) - (LEG_R if legs else BODY_W)
        src = res.readings[0].source if res.readings else "-"
        lidar_d = res.readings[0].lidar_dist if res.readings else None
        mono_d = res.readings[0].mono_dist if res.readings else None
        rows.append((t, truth, lidar_d, mono_d, res.dist, src, res.state, res.buzzer))
        if res.state is not prev_state:
            print(f"  t={t:6.2f}s  真值 {truth:5.2f}m  ->  {res.state.value:<5}"
                  f" (估距 {res.dist if res.dist is not None else float('nan'):5.2f}m, 来源 {src})")
            prev_state = res.state
    return rows


def summarize(rows, title):
    print("=" * 78)
    print(title)
    print("=" * 78)

    # 只统计"走近"这一段（距离单调变小的前半程）。后半程人往后退时，
    # 状态机是刻意**保持 STOP** 的（不跟着人往前挪），不参与分档正确率。
    split = int(np.argmin([r[1] for r in rows]))
    appr, after = rows[:split + 1], rows[split:]

    print(f"{'距离段':<12}{'帧数':>6}{'雷达可用':>10}{'单目可用':>10}"
          f"{'估距中位误差':>14}{'状态正确率':>12}")
    for name, lo, hi in (("0.5-1.0m", 0.5, 1.0), ("1.0-3.0m", 1.0, 3.0),
                         ("3.0-5.0m", 3.0, 5.0)):
        b = [r for r in appr if lo <= r[1] <= hi]
        if not b:
            continue
        nl = sum(1 for r in b if r[2] is not None)
        nm = sum(1 for r in b if r[3] is not None)
        errs = [abs(r[4] - r[1]) for r in b if r[4] is not None]
        med = float(np.median(errs)) if errs else float("nan")
        good = sum(1 for r in b if
                   (r[6] is State.STOP and r[1] <= 1.0) or
                   (r[6] is State.WARN and 1.0 < r[1] <= 3.0) or
                   (r[6] is State.CLEAR and r[1] > 3.0))
        print(f"{name:<12}{len(b):>6}{nl:>10}{nm:>10}{med:>14.3f}{good/len(b):>11.0%}")

    err = [abs(r[4] - r[1]) for r in rows if r[4] is not None]
    print(f"\n全时程估距误差：中位 {np.median(err):.3f}m，"
          f"90 分位 {np.percentile(err, 90):.3f}m")

    # 停车保持：人后退期间是否一直停着
    n_stop = sum(1 for r in after if r[6] is State.STOP)
    d_release = next((r[1] for r in after if r[6] is not State.STOP), None)
    print(f"停车保持：人后退阶段 {len(after)} 帧里 {n_stop} 帧保持 STOP"
          f"（{n_stop/len(after):.0%}）"
          + (f"，退到 {d_release:.2f}m 才恢复" if d_release else "，全程未恢复"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legs", action="store_true", help="用两条细腿代替躯干圆柱")
    ap.add_argument("--theta0-bad", action="store_true", help="故意不标定 theta0")
    ap.add_argument("--occlusion", type=float, default=0.0,
                    help="打在人身上的回波丢失比例，0~1，模拟深色裤子/波束穿腿缝")
    a = ap.parse_args()

    title = "标准场景（躯干圆柱 22cm）"
    if a.legs:
        title = "细腿场景（单腿半径 5cm，模拟真实雷达漏检）"
    if a.occlusion > 0:
        title += f"  + {a.occlusion:.0%} 回波丢失"
    if a.theta0_bad:
        title += "  + theta0 未标定"
    print("\n--- 状态切换时序 ---")
    rows = run(a.legs, a.theta0_bad, occlusion=a.occlusion)
    print()
    summarize(rows, title)


if __name__ == "__main__":
    main()
