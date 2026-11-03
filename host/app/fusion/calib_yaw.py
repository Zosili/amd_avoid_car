# -*- coding: utf-8 -*-
"""
相机 - 雷达 安装角标定
=======================
需要标两个量：
    theta0 : 雷达自身 0 度指向，在图像里对应哪个方位
    sign   : 图像往右，雷达角度是变大(+1)还是变小(-1)

--------------------------------------------------------------------------
最快的手工办法（不用跑任何代码，两分钟）
--------------------------------------------------------------------------
前提：车不动，前方只放一个人（或一把椅子），2m 左右。

第 1 步 标 theta0：
    把人挪到**画面正中央**（u ≈ 320）。此时相机方位角 alpha = 0，
    由 theta = theta0 + sign*alpha 得 theta = theta0 —— 也就是说
    **此刻雷达报出来的人的角度，就是 theta0**。
    （看雷达的极坐标图，读出那个孤立的簇在哪一度。）

第 2 步 标 sign：
    把人挪到画面**左三分之一**（u ≈ 210）。此时 alpha < 0。
      * 雷达角度变小了 -> sign = +1
      * 雷达角度变大了 -> sign = -1
    再挪到右三分之一复核一次，两次结论必须一致。

第 3 步 验证：人沿直线走近，看估距是否跟着线性变小、且与卷尺读数一致。

--------------------------------------------------------------------------
自动化办法（本脚本）
--------------------------------------------------------------------------
录若干帧 (bbox + 雷达扫描)，网格搜索 (theta0, sign)，使"预测角度处确有
一个簇、且簇中心就在预测角度上"的残差最小。不需要卷尺，也不需要知道距离。

采集格式（JSON 列表）：
    [{"bbox": [x1,y1,x2,y2],
      "angles": [...], "ranges": [...],     # 单位：度 / 米
      "truth_m": 2.00                        # 可选，有的话会额外报误差
     }, ...]

用法：
    python fusion/calib_yaw.py --demo            # 内置仿真，验证求解器
    python fusion/calib_yaw.py --capture cap.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fusion_core import (DecisionConfig, Scan, bbox_bearing_window,
                         cam_to_lidar, cam_to_lidar, fx_from_hfov, wrap180)


def best_cluster(scan: Scan, center_deg: float, tol_deg: float = 12.0):
    """在 center_deg 附近 tol 度内找最显著的簇，返回 (簇中心角, 距离, 点数)。"""
    m = np.abs(np.vectorize(wrap180)(scan.angles - center_deg)) <= tol_deg
    if m.sum() < 2:
        return None
    a, r = scan.angles[m], scan.ranges[m]
    v = (r > 0.08) & (r < 6.0)
    a, r = a[v], r[v]
    if r.size < 2:
        return None
    order = np.argsort(r)
    r, a = r[order], a[order]
    cuts = np.where(np.diff(r) > 0.6)[0] + 1
    groups = np.split(np.arange(r.size), cuts)
    groups = [g for g in groups if g.size >= 2] or [np.arange(r.size)]
    g = max(groups, key=lambda x: x.size)
    rad = np.radians(a[g])
    cen = math.degrees(math.atan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360.0
    return cen, float(np.median(r[g])), int(g.size)


def score(cap, theta0: float, sign: int, fx: float, cx: float):
    """返回 (角度残差中位数, 距离相对离散度)。越小越好。"""
    res, dists = [], []
    for rec in cap:
        x1, y1, x2, y2 = rec["bbox"]
        a_lo, a_w = bbox_bearing_window(x1, x2, fx, cx, 0.0)
        a_c = a_lo + a_w / 2.0
        pred = cam_to_lidar(a_c, theta0, sign)
        scan = Scan(rec["angles"], rec["ranges"])
        bc = best_cluster(scan, pred, tol_deg=max(6.0, a_w))
        if bc is None:
            res.append(20.0)   # 预测位置没有任何东西 -> 罚分
            continue
        cen, d, n = bc
        res.append(abs(wrap180(cen - pred)))
        dists.append(d)
    if not res:
        return 1e9, 1e9
    spread = float(np.std(dists) / max(1e-6, np.mean(dists))) if dists else 1e9
    return float(np.median(res)), spread


def solve(cap, cfg: DecisionConfig, step: float = 0.5):
    fx = fx_from_hfov(cfg.cam_w, cfg.hfov_deg)
    cx = cfg.cam_w / 2.0 if cfg.cx is None else cfg.cx
    best = None
    for sign in (+1, -1):
        for t in np.arange(0.0, 360.0, step):
            ang, sp = score(cap, float(t), sign, fx, cx)
            s = ang + 25.0 * sp            # 角度残差为主，距离稳定性为辅
            if best is None or s < best[0]:
                best = (s, float(t), sign, ang, sp)
    return best


def make_demo_capture(theta0_true=27.0, sign_true=-1, n=25, seed=3):
    """造一组仿真采集：人站在正前方 2.0m，横向随机偏移。"""
    rng = np.random.default_rng(seed)
    fx = fx_from_hfov(640, 60.0)
    cap = []
    for _ in range(n):
        py = rng.uniform(-0.45, 0.45)     # 横向偏移（左为正）
        px = 2.0
        a_lo_u = 320 - fx * (py + 0.22) / px
        a_hi_u = 320 - fx * (py - 0.22) / px
        v_top, v_bot = 240 - fx * 1.5 / px, 240 + fx * 0.2 / px
        # 世界角 -> 雷达报告角
        beta = math.atan2(py, px)
        th = (theta0_true + sign_true * math.degrees(-beta)) % 360.0
        angs = np.arange(0, 360, 1.0)
        ranges = np.full(angs.shape, 8.0) + rng.normal(0, 0.01, angs.shape)
        # 人在 th 附近 ±5 度
        for i, a in enumerate(angs):
            if abs(wrap180(a - th)) <= 5.0:
                ranges[i] = 2.0 + rng.normal(0, 0.01)
        cap.append({"bbox": [min(a_lo_u, a_hi_u), v_top, max(a_lo_u, a_hi_u), v_bot],
                    "angles": angs.tolist(), "ranges": ranges.tolist(),
                    "truth_m": 2.0})
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", help="JSON 采集文件")
    ap.add_argument("--demo", action="store_true", help="用内置仿真验证求解器")
    ap.add_argument("--hfov", type=float, default=60.0)
    ap.add_argument("--truth", type=float, default=None, help="已知真实距离（米）")
    a = ap.parse_args()

    cfg = DecisionConfig(hfov_deg=a.hfov)
    if a.demo:
        true_t, true_s = 27.0, -1
        cap = make_demo_capture(true_t, true_s)
        print(f"[demo] 真实参数 theta0={true_t}° sign={true_s}，"
              f"人站在 2.00m，横向随机偏移 {len(cap)} 帧")
        print("[demo] 求解器不知道上面两个值，只看到 bbox 和扫描\n")
    else:
        if not a.capture:
            ap.error("需要 --capture 或 --demo")
        cap = json.loads(Path(a.capture).read_text(encoding="utf-8"))

    s, t0, sign, ang, sp = solve(cap, cfg)
    print(f"最优解： theta0 = {t0:.1f}°   sign = {sign:+d}")
    print(f"        角度残差中位 {ang:.2f}°   距离相对离散度 {sp*100:.1f}%")

    # 给出两个 sign 各自的成绩，差得越多越可信
    print("\n两个旋转方向各自的成绩（差得越大越可信）：")
    fx = fx_from_hfov(cfg.cam_w, cfg.hfov_deg)
    cx = cfg.cam_w / 2.0 if cfg.cx is None else cfg.cx
    for sg in (+1, -1):
        best_sg = min((score(cap, float(t), sg, fx, cx) for t in np.arange(0, 360, 0.5)),
                      key=lambda v: v[0] + 25 * v[1])
        tag = "  <-- 选它" if sg == sign else ""
        print(f"  sign={sg:+d}  最佳 theta0 使残差 {best_sg[0]:.2f}°{tag}")

    if ang > 3.0:
        print(f"\n[警告] 残差 {ang:.1f}° 偏大。常见原因：")
        print("  - 人没被雷达打全（只扫到一条腿），换宽一点的遮挡物重录")
        print("  - hfov 猜错了，用 --hfov 改（OV5640 常见 60/70/90）")
        print("  - 相机有桶形畸变，靠画面边缘误差大 -> 只用画面中间 1/3 的帧")
    if a.truth or any("truth_m" in r for r in cap):
        cfg.theta0, cfg.ang_sign = t0, sign
        from fusion_core import window_range
        errs = []
        for r in cap:
            x1, y1, x2, y2 = r["bbox"]
            a_lo, a_w = bbox_bearing_window(x1, x2, fx, cx, 1.5)
            st = cam_to_lidar(a_lo, t0, sign) if sign > 0 else cam_to_lidar(a_lo + a_w, t0, sign)
            hit = window_range(Scan(r["angles"], r["ranges"]), st, a_w,
                               a_lo + a_w / 2, t0, sign)
            truth = a.truth or r.get("truth_m")
            if hit.ok and truth:
                errs.append(hit.dist - truth)
        if errs:
            print(f"\n标定后测距误差：中位 {np.median(errs)*100:+.1f}cm，"
                  f"极差 {np.ptp(errs)*100:.1f}cm")

    print("\n把这两行填进 DecisionConfig：")
    print(f"    theta0={t0:.1f}, ang_sign={sign:+d},")


if __name__ == "__main__":
    main()
