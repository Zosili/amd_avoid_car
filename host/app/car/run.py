# -*- coding: utf-8 -*-
"""run.py — 决策主循环（郑侧入口）

sim : python run.py --source sim --scenario front_close_radar_only
live: python run.py --source live [--vision-stdin] [--pynq HOST:PORT]
每周期写 logs/run_*.jsonl（含 rule 与 why，答辩复盘用）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import now_ms                       # noqa: E402
from fusion.machine import DecisionMachine        # noqa: E402
from radar.radar_provider import LiveRadarProvider  # noqa: E402
from comm.tcp_client import TcpClient              # noqa: E402
from sim.scenarios import SCENARIOS                # noqa: E402

CYCLE_MS = 100          # 决策循环 10Hz


def open_log():
    os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
    name = time.strftime("run_%Y%m%d_%H%M%S.jsonl")
    return open(os.path.join(os.path.dirname(__file__), "logs", name), "a", encoding="utf-8")


def run_sim(scenario_name):
    frames = SCENARIOS[scenario_name]
    machine = DecisionMachine()
    log = open_log()
    t0 = now_ms()
    i, radar, vision, fault = 0, None, None, False
    print(f"[sim] scenario={scenario_name} frames={len(frames)}")
    while i < len(frames):
        now = now_ms()
        rel = now - t0
        while i < len(frames) and frames[i][0] <= rel:
            ft, rj, vj, ff = frames[i]
            if rj is not None:
                radar = dict(rj, t=t0 + ft)
            if vj is not None:
                v = json.loads(json.dumps(vj))          # 深拷贝
                v["t"] = t0 + ft
                vision = v
            fault = ff
            i += 1
        ctrl, rule, why = machine.step(radar, vision, fault, now)
        log.write(json.dumps({"t": rel, "rule": rule, "why": why, "control": ctrl},
                             ensure_ascii=False) + "\n")
        log.flush()
        print(f"  t={rel:5d}ms  {rule:3s}  {ctrl['action']:8s}  {why}")
        time.sleep((CYCLE_MS - (now_ms() - now)) / 1000.0)
    log.close()


def run_live(args):
    machine = DecisionMachine()
    log = open_log()
    radar_p = LiveRadarProvider()
    from vision.vision_provider import VisionProvider
    vision_p = VisionProvider(on_drop=lambda why: print("vision dropped:", why))
    tcp = None
    if args.pynq:
        host, _, port = args.pynq.partition(":")
        tcp = TcpClient(host, int(port))
        tcp.connect()
        print(f"[live] PYNQ -> {host}:{port}")
    print("[live] radar on COM5; Vision JSON 每行一个从 stdin 送入")
    try:
        while True:
            now = now_ms()
            radar = radar_p.poll()
            if args.vision_stdin:
                line = sys.stdin.readline()
                if line:
                    vision_p.push(line)
            vision = vision_p.poll()
            fault = tcp.check_watchdog() if tcp else False
            ctrl, rule, why = machine.step(radar, vision, fault, now)
            if tcp:
                tcp.send_control(ctrl)
            log.write(json.dumps({"t": now, "rule": rule, "why": why, "control": ctrl},
                                 ensure_ascii=False) + "\n")
            log.flush()
            print(f"  {rule:3s}  {ctrl['action']:8s}  {why}")
            time.sleep(CYCLE_MS / 1000.0)
    finally:
        radar_p.close()
        if tcp:
            tcp.close()
        log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["sim", "live"], default="sim")
    ap.add_argument("--scenario", default="clear_road", choices=sorted(SCENARIOS))
    ap.add_argument("--pynq", default=None, help="HOST:PORT")
    ap.add_argument("--vision-stdin", action="store_true")
    args = ap.parse_args()
    if args.source == "sim":
        run_sim(args.scenario)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
