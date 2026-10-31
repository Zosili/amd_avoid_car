# -*- coding: utf-8 -*-
"""radar_helper.py —— 给「雷达控制台」GUI 用的命令行小工具（由 venv python 运行）

GUI 用系统 Python（带 tkinter）跑，雷达相关计算全部委托到这里，
两边不混依赖。所有子命令只往 stdout 打**单行 JSON**，GUI 直接 json.loads。

    python radar_helper.py ports
    python radar_helper.py probe --port COM5 --secs 3
    python radar_helper.py geom  --len 35 --wid 18 --fwd 3 --n 2.2 --mount front
"""
import argparse
import json
import sys

import os

import matplotlib
matplotlib.use("Agg")            # 本脚本永不画窗口

INTEG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "integration")
if INTEG not in sys.path:
    sys.path.insert(0, INTEG)


def cmd_ports(_a):
    import serial.tools.list_ports as lp
    return {"ports": [{"device": p.device, "desc": p.description}
                      for p in lp.comports()]}


def cmd_probe(a):
    """读雷达 a.secs 秒 → 帧健康度 + 12 扇区 + 三扇区（车上口径）"""
    import time
    from radar_map_view import RadarSource, MapView
    from radar import d6_protocol as d6

    src = RadarSource("real", a.port)
    time.sleep(max(1.0, a.secs))
    buf, fok, fbad, hz = src.snapshot()
    src.alive = False
    if len(buf) < 100:
        return {"ok": False,
                "msg": f"{a.port} 读不到数据：雷达没上电 / 串口被占用 / 口选错了",
                "frames_ok": fok}
    valid = sorted(p[1] / 1000.0 for p in buf if p[1] is not None)
    s12 = MapView._sector12(buf)
    st = d6.scan_stats(buf)
    return {
        "ok": True, "frames_ok": fok, "frames_bad": fbad, "hz": round(hz, 1),
        "npts": len(buf), "valid": len(valid),
        "d_med": round(valid[len(valid) // 2], 2) if valid else None,
        "d_min": round(valid[0], 2) if valid else None,
        "d_max": round(valid[-1], 2) if valid else None,
        "s12": [[None if m is None else round(m, 2), n] for m, n in s12],
        "s3": {k: [None if st[k][0] is None else round(st[k][0], 2), st[k][1]]
               for k in st},
    }


def cmd_geom(a):
    """车体几何：净空角 / 遮挡区间 / 扫掠半径 / 与 3 扇区的重叠"""
    from radar_map_view import CarShape, runs_of
    car = CarShape(car_len=a.len / 100.0, car_wid=a.wid / 100.0,
                   n_exp=a.n, mount=a.mount, fwd=a.fwd / 100.0)
    fov, _angs, mask = car.clear_fov(0.5)
    occ = [[round(lo, 1), round(hi, 1)] for lo, hi in runs_of(mask, 0.5)]
    ov = car.overlap_with_sectors()
    warn = [f"扇区 {k} 有 {v:.1f}° 落在车体遮挡里 → 装车后该扇区读数会变成"
            f"车体距离，决策会死锁" for k, v in ov.items() if v > 1.0]
    return {"clear_fov": round(fov, 1), "occlusion": occ,
            "sweep_cm": round(car.sweep_radius() * 100, 1),
            "overlap": {k: round(v, 1) for k, v in ov.items()},
            "warn": warn}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports")

    p = sub.add_parser("probe")
    p.add_argument("--port", default="COM5")
    p.add_argument("--secs", type=float, default=3.0)

    g = sub.add_parser("geom")
    g.add_argument("--len", type=float, default=35.0)
    g.add_argument("--wid", type=float, default=18.0)
    g.add_argument("--fwd", type=float, default=3.0)
    g.add_argument("--n", type=float, default=2.2)
    g.add_argument("--mount", choices=["front", "center"], default="front")

    a = ap.parse_args()
    fn = {"ports": cmd_ports, "probe": cmd_probe, "geom": cmd_geom}[a.cmd]
    try:
        out = fn(a)
    except Exception as e:                      # GUI 需要拿到错误而不是崩
        out = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
