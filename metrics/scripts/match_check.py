# -*- coding: utf-8 -*-
"""
相机 - 雷达 角度映射自检（离线，不需要硬件）
=============================================
核心只有一条公式（见 fusion_core 顶部注释）：

    theta = theta0 + sign * alpha        alpha = atan((u - cx) / fx)

本脚本用**合成的人框 + 合成的雷达扫描**把这条链路逐项验一遍，
不依赖摄像头、雷达、板子。跑通说明"角度对得上"这件事的**代码是自洽的**；
剩下要现场做的只有一件事——把真实的 theta0 / sign 量出来填进去。

用法：
    python match_check.py
    python match_check.py --theta0 30 --sign -1     # 用你标定出的值再验一遍
"""

from __future__ import annotations

import argparse
import math

import numpy as np

from fusion_core import (
    DecisionConfig, FusionEngine, Scan, bbox_bearing_window, cam_to_lidar,
)


def make_scan(theta_deg: float, dist_m: float, width_deg: float = 2.0) -> Scan:
    """整圈 1° 分辨率，只在 theta 附近 width 度内填距离，其余 NaN。"""
    angles = np.arange(0.0, 360.0, 1.0)
    ranges = np.full(angles.shape, np.nan, dtype=np.float32)
    d = np.abs(((angles - theta_deg + 180.0) % 360.0) - 180.0)
    ranges[d <= width_deg] = dist_m
    return Scan(angles, ranges)


def box_at_u(u: float, cfg: DecisionConfig, w_px: float = 90.0,
             img_w: int = 640, img_h: int = 480):
    """造一个"站在图上 u 位置"的人框：宽 w_px，底边在画面下方 1/4 处。"""
    return (u - w_px / 2, img_h * 0.30, u + w_px / 2, img_h * 0.75)


def one_case(cfg: DecisionConfig, u: float, theta_target: float,
             radar_m: float, label: str):
    """把人框放在 u，雷达在 theta_target 放一个目标，看能不能对上。"""
    eng = FusionEngine(cfg)
    box = box_at_u(u, cfg)
    a_lo, a_w = bbox_bearing_window(box[0], box[2], eng.fx, eng.cx, cfg.pad_deg)
    a_c = a_lo + a_w / 2.0
    th = cam_to_lidar(a_c, cfg.theta0, cfg.ang_sign)
    rd = eng._read_person(box, 0.9, make_scan(theta_target, radar_m))
    hit = rd.source == "lidar" and rd.lidar_dist is not None
    ok = "对上了" if hit else "没对上"
    print(f"  {label:<28} u={u:5.0f}  alpha={a_c:+6.1f}°  "
          f"theta={th:6.1f}°  目标在{theta_target:6.1f}°  "
          f"-> {rd.source:<5} {('%5.2fm' % rd.lidar_dist) if hit else '     '}  {ok}")
    return hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theta0", type=float, default=0.0)
    ap.add_argument("--sign", type=int, default=-1, choices=[1, -1])
    a = ap.parse_args()

    cfg = DecisionConfig(theta0=a.theta0, ang_sign=a.sign)
    eng = FusionEngine(cfg)
    print(f"配置: 640x480  hfov={cfg.hfov_deg}°  ->  fx={eng.fx:.1f}px  cx={eng.cx:.0f}px")
    print(f"      theta0={cfg.theta0:+.1f}°  sign={cfg.ang_sign:+d}  pad={cfg.pad_deg}°\n")

    print("=== 用例 ===")
    cases = []
    # 1) 人站画面正中，雷达目标就在 theta0
    cases.append(one_case(cfg, 320, a.theta0, 2.00, "正中 + 目标在 theta0"))
    # 2) 人站正中，雷达目标偏 20° -> 不该对上
    cases.append(not one_case(cfg, 320, a.theta0 + 20, 2.00, "正中 + 目标偏 20°"))
    # 3) 人偏右，雷达目标按公式摆到对应角度 -> 该对上
    a_lo, a_w = bbox_bearing_window(box_at_u(430, cfg)[0],
                                    box_at_u(430, cfg)[2], eng.fx, eng.cx, cfg.pad_deg)
    th430 = cam_to_lidar(a_lo + a_w / 2, cfg.theta0, cfg.ang_sign)
    cases.append(one_case(cfg, 430, th430, 1.30, "偏右 + 目标按公式摆放"))
    # 4) 人偏左，同理
    a_lo, a_w = bbox_bearing_window(box_at_u(210, cfg)[0],
                                    box_at_u(210, cfg)[2], eng.fx, eng.cx, cfg.pad_deg)
    th210 = cam_to_lidar(a_lo + a_w / 2, cfg.theta0, cfg.ang_sign)
    cases.append(one_case(cfg, 210, th210, 0.80, "偏左 + 目标按公式摆放"))
    # 5) 人偏右但雷达目标摆到镜像位置（典型 sign 填反）-> 不该对上
    b430 = box_at_u(430, cfg)
    a_lo5, a_w5 = bbox_bearing_window(b430[0], b430[2], eng.fx, eng.cx, cfg.pad_deg)
    th_mirror = cam_to_lidar(a_lo5 + a_w5 / 2, cfg.theta0, -cfg.ang_sign)
    cases.append(not one_case(cfg, 430, th_mirror, 1.30, "偏右 + 目标镜像摆放(sign反)"))

    print("\n=== 结论 ===")
    if all(cases):
        print("  角度映射自洽：正例命中、反例不命中。")
        print("  现场只剩一步——把真实 theta0 / sign 量出来（见 fusion/calib_yaw.py）。")
    else:
        print("  有用例不符合预期，先别接硬件，先查这里的公式/参数。")


if __name__ == "__main__":
    main()
