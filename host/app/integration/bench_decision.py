# -*- coding: utf-8 -*-
"""
bench_decision.py — 量一下郑的决策链路到底吃多少 CPU（回答"要不要改 C"）

用法：
    # PC 上
    python bench_decision.py
    # 板子 PS 上（这个数才是真正要看的）
    python3 bench_decision.py

它按真实负载跑：雷达 163 帧/秒（400 点/圈 ÷ 25 点/帧 × 10.2Hz）、
决策 10Hz（CYCLE_MS=100ms）、视觉 7Hz。最后算出"一个决策周期里 CPU 忙了多久"。

判定标准：
    占用 < 5%   → 完全不用管，改 C 没有意义
    5% ~ 20%   → 还有余量，优先优化算法而不是语言
    > 20%      → 才值得考虑把热函数拆成 C
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "car"))

import protocol                      # noqa: E402
from radar import d6_protocol        # noqa: E402
from fusion.machine import DecisionMachine   # noqa: E402

# ---- 真实负载参数 ----
RADAR_FPS = 163          # 400 点/圈 / 25 点/帧 * 10.2 Hz
CYCLE_HZ = 10            # CFG 里 CYCLE_MS = 100
VISION_HZ = 7            # 契约要求的最低频率
BUDGET_MS = 1000.0 / CYCLE_HZ      # 一个决策周期的预算


def build_frame(n=25, dist_mm=1500, start_deg=0.0):
    """造一帧合法的 85 字节雷达包（AA 55 | M&T | LSN | FSA | LSA | CS | n×3）"""
    fsa = int(round(start_deg * 64)) << 1 | 1
    lsa = int(round((start_deg + 0.9 * (n - 1)) * 64)) << 1 | 1
    body = bytearray()
    body += fsa.to_bytes(2, "little")
    body += lsa.to_bytes(2, "little")
    body += b"\x00\x00"                     # CS 占位
    for k in range(n):
        d = dist_mm + k                      # 每点略微不同，避免被常量折叠
        it = 200 + (k & 0x0F)                # 强度 8bit
        s_h = (d >> 6) & 0xFF                            # dist 高 8 位
        s_2 = ((d & 0x3F) << 2) | ((it >> 6) & 0x03)     # dist 低 6 位 + it 高 2 位
        s_l = ((it & 0x3F) << 2) | 0x00                  # it 低 6 位 + 弱回波标志
        body += bytes((s_l, s_2, s_h))
    ct, nn = 0x00, n
    cs = d6_protocol.checksum(ct, nn, bytes(body))
    body[4:6] = cs.to_bytes(2, "little")
    return ct, nn, bytes(body)


def bench(fn, n_iter, warmup=200):
    for _ in range(warmup):
        fn()
    t0 = time.perf_counter()
    for _ in range(n_iter):
        fn()
    dt = time.perf_counter() - t0
    return dt / n_iter * 1e6            # µs/次


def main():
    print("=" * 66)
    print("郑的决策链路性能基线")
    print("=" * 66)

    # ---------- 1. 雷达帧解析（最热的函数） ----------
    ct, n, body = build_frame()
    pts = d6_protocol.parse_frame(ct, n, body)
    assert pts is not None, "造的测试帧没通过校验，bench 无效"

    t_cs = bench(lambda: d6_protocol.checksum(ct, n, body), 20000)
    t_pf = bench(lambda: d6_protocol.parse_frame(ct, n, body), 20000)

    # 一整圈 16 帧拼成 scan（400 点），测扇区统计
    scan = []
    for i in range(16):
        _, _, b = build_frame(start_deg=i * 22.5)
        scan.extend(d6_protocol.parse_frame(0x00, 25, b))

    def _sectors():
        return d6_protocol.scan_to_radar_json(scan)

    t_sec = bench(_sectors, 5000)

    # ---------- 2. 视觉 JSON 解析 ----------
    vis_obj = {
        "version": 1, "timestamp": int(time.time() * 1000),
        "obstacle": True,
        "nearest": {"class": "person", "confidence": 0.81,
                    "cx": 0.31, "cy": 0.62, "width": 0.12, "height": 0.35},
    }
    vis_raw = json.dumps(vis_obj).encode("utf-8")

    def _parse_vis():
        return protocol.parse_vision(json.loads(vis_raw))

    assert _parse_vis() is not None, "视觉样例没过校验，bench 无效"
    t_vis = bench(_parse_vis, 20000)

    # ---------- 3. 决策 ----------
    m = DecisionMachine()
    rj = d6_protocol.scan_to_radar_json(scan)
    vj = _parse_vis()
    # 时间戳固定住：否则跑几千次后 t 过期，step() 会一直走 R5 早退路径，量不到真实决策
    T_NOW = 1_000_000.0
    rj["t"] = T_NOW - 50
    vj["t"] = T_NOW - 10

    t_step = bench(lambda: m.step(rj, vj, False, T_NOW), 20000)

    # ---------- 4. 下行序列化 ----------
    ctrl, _, _ = m.step(rj, vj, False, T_NOW)
    t_dump = bench(lambda: protocol.dumps(ctrl), 20000)

    # ---------- 汇总（按线程分开算，因为雷达是后台线程） ----------
    # 雷达线程：每帧 = read_frame(阻塞等串口) + parse_frame_ex；
    #           扇区统计按 RECOMPUTE_HZ 限流，不是每帧都算
    stats_hz = getattr(d6_protocol, "RECOMPUTE_HZ", 0) or RADAR_FPS
    radar_thread_ms = (t_pf * RADAR_FPS + t_sec * stats_hz) / 1000.0

    # 主线程：每周期 = poll() + parse_vision + step + dumps
    main_cycle_ms = (t_vis + t_step + t_dump) / 1000.0
    main_thread_pct = main_cycle_ms / BUDGET_MS * 100

    rows = [
        ("checksum()（单帧，25 点）", t_cs, "µs/帧", RADAR_FPS, t_cs * RADAR_FPS / 1000),
        ("parse_frame_ex()（单帧，25 点）", t_pf, "µs/帧", RADAR_FPS, t_pf * RADAR_FPS / 1000),
        ("scan_to_radar_json（400 点 3 扇区）", t_sec, "µs/次", stats_hz, t_sec * stats_hz / 1000),
        ("parse_vision()（JSON + 校验）", t_vis, "µs/包", VISION_HZ, t_vis * VISION_HZ / 1000),
        ("machine.step()（五规则）", t_step, "µs/次", CYCLE_HZ, t_step * CYCLE_HZ / 1000),
        ("dumps(control)（下行序列化）", t_dump, "µs/次", CYCLE_HZ, t_dump * CYCLE_HZ / 1000),
    ]
    print(f"\n{'函数':<36}{'单次':>9} {'单位':<7}{'频率':>8}{'每秒占用':>11}")
    print("-" * 72)
    for name, us, unit, hz, ms_per_s in rows:
        print(f"{name:<36}{us:>9.2f} {unit:<7}{hz:>7.0f}/s{ms_per_s:>10.2f}ms")

    print("\n" + "=" * 72)
    print("两条线程各自的负载")
    print("=" * 72)
    print(f"  雷达线程  {radar_thread_ms:>7.2f} ms/s  "
          f"= 单核 {radar_thread_ms/10:>5.2f}%"
          f"   ← 解码 {RADAR_FPS:.0f} 帧/秒 + 扇区统计 {stats_hz:.0f} 次/秒")
    print(f"  主线程    {main_cycle_ms:>7.3f} ms/周期  "
          f"= 单核 {main_thread_pct:>5.2f}%   ← 每 100ms 才动一次")
    print(f"  合计      {(radar_thread_ms/10 + main_thread_pct):>7.2f} % 单核")

    main_thread_ms_per_s = main_cycle_ms * CYCLE_HZ
    waste_ms = t_sec * (RADAR_FPS - stats_hz) / 1000.0
    print("\n  雷达线程每秒 %.1fms，主线程每秒只有 %.2fms —— 相差 %.0f 倍。"
          % (radar_thread_ms, main_thread_ms_per_s, radar_thread_ms / main_thread_ms_per_s))
    print("  扇区统计已按 RECOMPUTE_HZ=%.0f 限流（旧代码是每帧都算）。" % stats_hz)
    print("  若改回每帧都算，雷达线程会从 %.1f 涨到 %.1f ms/s（多烧 %.1f ms/s）。"
          % (radar_thread_ms, radar_thread_ms + waste_ms, waste_ms))

    print("\n" + "=" * 72)
    print("改成 C 能省多少（假设 30 倍提速，乐观估计）")
    print("=" * 72)
    print(f"  雷达线程  {radar_thread_ms:>7.2f} → {radar_thread_ms/30:>5.2f} ms/s")
    print(f"  主线程    {main_cycle_ms:>7.3f} → {main_cycle_ms/30:>5.4f} ms/周期")
    print(f"  节省      {radar_thread_ms - radar_thread_ms/30:>7.2f} ms/s")
    print("\n  但这两个数都不在关键路径上：主线程本来就在 time.sleep() 等 100ms，")
    print("  雷达线程的产出一圈才更新一次（98ms），省下的 CPU 没人用。")

    print("\n" + "=" * 72)
    print("真正的延迟在哪（这才是要优化的地方）")
    print("=" * 72)
    print("  雷达串口   230400 波特 → 一帧 85B 要 3.7ms 传完；一圈 16 帧 = 59ms 串口占用")
    print("             （一圈总共 98ms，串口占空比 60%——这已经是硬上限了）")
    print("  视觉推理   YOLO 在 PC 上跑，30~140ms 才是整条链路最大的延迟")
    print("  决策周期   time.sleep() 主动等满 100ms（CYCLE_MS）")
    print("  电机响应   20kHz PWM 周期 0.05ms，但机械惯性是几十毫秒级")
    print("\n  结论：把 Python 改成 C，车的反应速度一秒都不会变快。")
    print("        要快就动这三样：① 视觉提频到 10Hz ② CYCLE_MS 降到 50 ③ 舵机/电机响应")


if __name__ == "__main__":
    main()
