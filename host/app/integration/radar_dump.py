# -*- coding: utf-8 -*-
"""
radar_dump.py — 雷达角度-距离实测（郑用；PC 和板子都能跑）
=========================================================
读 N 秒，按角度分桶输出距离，同时给出"是否正常"的量化判定。

用法：
    python radar_dump.py                          # 自动找串口，5° 一档
    python radar_dump.py --port COM5 --secs 6
    python radar_dump.py --step 10                # 10° 一档，表更短
    python radar_dump.py --raw                    # 输出最后一圈的原始 400 点
    python radar_dump.py --keep-weak              # 不丢弱回波（看原始数据）
    python radar_dump.py --csv angles.csv         # 存 CSV，方便画图

为什么按角度分桶而不是直接打印原始点：
    雷达每圈 400 点、0.9° 步进，原始点直接打是 400 行且角度乱序（帧内顺序是
    连续的，但帧与帧之间跨圈）。分桶后每个桶里有几十到几百个样本，取中位数
    才能把"这一片区域大概多远"说清楚。
"""
import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

RADAR_BAUD = 230400


def find_port():
    try:
        from serial.tools import list_ports
        cand = [p.device for p in list_ports.comports()
                if "ttyUSB" in p.device or p.device.upper().startswith("COM")]
        return cand[0] if cand else None
    except Exception:
        return None


def read_scan(port, secs, keep_weak, verbose=False):
    """读 secs 秒 → (buckets, raw_last_scan, stats)

    buckets[d] = [(dist_mm, intensity), ...]  按 1° 分桶，累积所有圈
    raw_last_scan = 最后攒满的那一圈（400 点，按角度排序）
    """
    import serial
    import radar.d6_protocol as d6

    if keep_weak:
        d6.DROP_WEAK_ECHO = False          # 运行时改模块常量，本进程内生效

    ser = serial.Serial(port, RADAR_BAUD, timeout=0.3)
    try:
        ser.dtr = False                    # 拉高会触发雷达复位
        ser.rts = False
    except Exception:
        pass
    try:
        ser.write(d6.START_CMD_1)
        ser.flush()
    except Exception:
        pass

    buckets = [[] for _ in range(360)]
    n_ok = n_bad = n_pts = n_none = n_weak = 0
    none_ints = []                         # 无回波点的强度 —— 用来识别"近距离遮挡"
    deg_hit = bytearray(360)
    scan = []                              # 滚动一圈
    last_full = None
    hz_t0 = time.time()

    while time.time() - hz_t0 < secs:
        r = d6.read_frame(ser)
        if r is None:
            continue
        ct, n, body = r
        pts = d6.parse_frame_ex(ct, n, body)
        if pts is None:
            n_bad += 1
            continue
        n_ok += 1
        for ang, dist, it, weak in pts:
            n_pts += 1
            deg_hit[int(ang) % 360] = 1
            if weak:
                n_weak += 1
            if dist is None:
                n_none += 1
                none_ints.append(it)
                continue
            buckets[int(ang) % 360].append((dist, it))
        scan.extend(pts)
        if len(scan) >= 400:
            last_full = sorted(scan[-400:], key=lambda p: p[0])
            scan = scan[-400:]
    dt = time.time() - hz_t0
    ser.close()

    stats = {"secs": dt, "frames_ok": n_ok, "frames_bad": n_bad, "pts": n_pts,
             "none": n_none, "weak": n_weak, "cover": sum(deg_hit),
             "hz": n_ok / dt if dt > 0 else 0.0,
             "none_int_mean": (sum(none_ints) / len(none_ints)) if none_ints else 0.0}
    return buckets, last_full, stats


def aggregate(buckets, step):
    """1° 桶 → step 度一档：[(start, end, 距离m|None, 点数, 平均强度)]"""
    out = []
    for start in range(0, 360, step):
        vals, ints = [], []
        for d in range(start, min(start + step, 360)):
            for dist, it in buckets[d]:
                vals.append(dist)
                ints.append(it)
        if vals:
            out.append((start, min(start + step - 1, 359),
                        statistics.median(vals) / 1000.0, len(vals),
                        sum(ints) / len(ints)))
        else:
            out.append((start, min(start + step - 1, 359), None, 0, 0.0))
    return out


def no_echo_ranges(rows):
    """连续的无回波段，例如 [(75, 115), (300, 320)]"""
    segs, cur = [], None
    for s, e, d, n, _i in rows:
        if d is None:
            cur = (s, e) if cur is None else (cur[0], e)
        elif cur is not None:
            segs.append(cur)
            cur = None
    if cur is not None:
        segs.append(cur)
    return segs


def segments(buckets, tol=0.30, min_len=2):
    """把整圈切成"距离相近的连续角度段" —— 每段大致对应一个物体/平面。

    标定 0° 时用这个：在某个方位摆个盒子，看它落在哪个角度段，就知道角度怎么对应。
    tol: 相邻整度距离差小于这个值(米)就认为是同一个物体
    min_len: 少于这么多个整度的段丢掉（多半是噪声或掠射角）
    """
    med = {}
    for d in range(360):
        if buckets[d]:
            med[d] = statistics.median(x[0] for x in buckets[d]) / 1000.0

    segs, cur = [], None
    for d in range(360):
        if d not in med:
            if cur:
                segs.append(cur)
                cur = None
            continue
        v = med[d]
        if cur and abs(v - cur["last"]) < tol and d == cur["end"] + 1:
            cur["end"] = d
            cur["last"] = v
            cur["vals"].append(v)
        else:
            if cur:
                segs.append(cur)
            cur = {"start": d, "end": d, "last": v, "vals": [v]}
    if cur:
        segs.append(cur)
    return [s for s in segs if len(s["vals"]) >= min_len]


def verdict(stats, rows):
    """量化判定：返回 [(级别, 说明)]"""
    v = []
    hz, cover = stats["hz"], stats["cover"]
    tot = stats["frames_ok"] + stats["frames_bad"]
    cr = 100.0 * stats["frames_ok"] / tot if tot else 0.0
    valid = 100.0 * (stats["pts"] - stats["none"]) / stats["pts"] if stats["pts"] else 0.0
    weak = 100.0 * stats["weak"] / stats["pts"] if stats["pts"] else 0.0

    v.append(("OK" if hz >= 120 else ("WARN" if hz >= 50 else "FAIL"),
              f"帧率 {hz:.1f} Hz（期望 ≈163）"))
    v.append(("OK" if cover >= 350 else ("WARN" if cover >= 180 else "FAIL"),
              f"角度覆盖 {cover}°/360°"))
    v.append(("OK" if cr >= 95 else ("WARN" if cr >= 80 else "FAIL"),
              f"校验通过 {cr:.1f}%（{stats['frames_ok']} 好 / {stats['frames_bad']} 坏）"))
    v.append(("OK" if valid >= 30 else ("WARN" if valid >= 10 else "FAIL"),
              f"有效回波 {valid:.1f}%（{stats['pts']} 点）"))
    v.append(("OK" if weak < 50 else "WARN",
              f"弱回波标志 {weak:.1f}%"))

    # "无回波"有两种完全不同的原因，靠无回波点的强度区分：
    #   强度高（>150）→ 近距离有强反射物，但距离 < MIN_RANGE(60mm) 被滤掉 = 镜片被遮住
    #   强度低        → 真的没有反射物（>12m 空旷）
    nim = stats.get("none_int_mean", 0.0)
    if valid < 10:
        if nim > 150:
            v.append(("WARN", f"无回波点强度均值 {nim:.0f}（很高）→ "
                              f"近距离有强反射物，但距离 <60mm 被量程下限滤掉了。"
                              f"雷达镜片很可能被遮住 / 扣在桌上 / 贴着墙"))
        else:
            v.append(("INFO", f"无回波点强度均值 {nim:.0f}（很低）→ "
                              f"典型空旷场景：前方没有 12m 内的反射物"))

    got = [r for r in rows if r[2] is not None]
    if not got:
        v.append(("FAIL", "整圈没有任何有效回波"))
        return v

    near = min(got, key=lambda r: r[2])
    far = max(got, key=lambda r: r[2])
    v.append(("INFO", f"最近 {near[2]:.2f}m @ {near[0]}°~{near[1]}°；"
                      f"最远 {far[2]:.2f}m @ {far[0]}°~{far[1]}°"))

    segs = no_echo_ranges(rows)
    if segs:
        txt = "，".join(f"{s}°~{e}°" for s, e in segs[:6])
        more = f" 等 {len(segs)} 段" if len(segs) > 6 else ""
        v.append(("INFO", f"无回波区间：{txt}{more}"))

    # 三面都在 1m 内 → 大概率被包着
    if far[2] < 1.0:
        v.append(("WARN", "整圈最远不足 1m —— 雷达可能被外壳/桌面/线束挡住，"
                          "测不出真实性能"))
    return v


def angle_medians(buckets):
    """每度中位距离（米）。无回波的角度不出现在字典里。"""
    out = {}
    for d in range(360):
        if buckets[d]:
            out[d] = statistics.median(x[0] for x in buckets[d]) / 1000.0
    return out


def show_diff(buckets, base, tol=0.25, min_span=5):
    """与基线对比，找出"变近"的角度段 —— 差分法标定 0° 的核心。

    为什么需要差分：实际房间里到处都有东西（墙、家具），直接看"最近的目标"
    分不清哪个是你摆的。先测一次空场景存基线，再在车头方向放个纸板测一次，
    差出来的那一段就是你放的目标 —— 它的角度就是车头方向在雷达坐标系里的位置。

    min_span：跨度小于这个度数的变化段直接忽略。同一环境自比对实测会产生
    1~3° 的假阳性（有人走过、雷达轻微抖动、掠射角回波时有时无），
    真正的平板目标至少 10° 以上。
    """
    med = angle_medians(buckets)
    hits = []
    for d in range(360):
        now = med.get(d)
        if now is None:
            continue
        was = base.get(str(d))
        if was is None:
            hits.append((d, None, now))
        elif now < was - tol:
            hits.append((d, was, now))

    if not hits:
        print(f"\n【与基线对比】没有角度变近超过 {tol:.2f}m —— "
              f"是不是目标没放上 / 放的位置不在雷达视野里？")
        return

    segs = []
    for d, was, now in hits:
        if segs and d == segs[-1]["end"] + 1:
            segs[-1]["end"] = d
            segs[-1]["pts"].append((was, now))
        else:
            segs.append({"start": d, "end": d, "pts": [(was, now)]})

    keep = [s for s in segs if s["end"] - s["start"] + 1 >= min_span]
    dropped = len(segs) - len(keep)

    if not keep:
        print(f"\n【与基线对比】没有跨 {min_span}° 以上的角度段变近")
        if dropped:
            print(f"  （另有 {dropped} 段零星变化，跨度都 <{min_span}°，属环境抖动/噪声，已忽略）")
        print("  目标是不是没放上 / 不在雷达视野里 / 比基线还远？")
        return

    print(f"\n【与基线对比】{len(keep)} 段变近（阈值 {tol:.2f}m，最小跨度 {min_span}°）")
    print("  角度范围        基线      现在     变化")
    print("  " + "-" * 46)
    for s in keep:
        now_m = statistics.median([p[1] for p in s["pts"]])
        wass = [p[0] for p in s["pts"] if p[0] is not None]
        was_s = f"{statistics.median(wass):.2f}m" if wass else "无回波"
        delta = f"{now_m - statistics.median(wass):+.2f}m" if wass else "新出现"
        print(f"  {s['start']:3d}°~{s['end']:3d}°{was_s:>10}{now_m:>9.2f}m{delta:>9}")
    if dropped:
        print(f"  （另有 {dropped} 段零星变化已忽略，跨度 <{min_span}°）")

    big = max(keep, key=lambda s: s["end"] - s["start"] + 1)
    center = (big["start"] + big["end"]) / 2.0
    span = big["end"] - big["start"] + 1
    print(f"\n  最宽的一段：{big['start']}°~{big['end']}°（跨 {span}°，中心 {center:.0f}°）")
    off = (360.0 - center) % 360.0
    print(f"  如果这就是车头正前方，则 ZERO_OFFSET_DEG ≈ {off:.1f}")
    print(f"  （改 car/radar/d6_protocol.py 顶部；改完重启进程，查表在 import 时构建）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--secs", type=float, default=6.0)
    ap.add_argument("--step", type=int, default=5, help="角度分辨率（度）")
    ap.add_argument("--raw", action="store_true", help="输出最后一圈原始点")
    ap.add_argument("--keep-weak", action="store_true", help="不丢弱回波点")
    ap.add_argument("--segments", action="store_true",
                    help="输出距离分段（每段对应一个物体，标定 0° 时用）")
    ap.add_argument("--save-base", default=None,
                    help="把本次每度中位距离存成基线（清空场景后跑一次）")
    ap.add_argument("--diff", default=None,
                    help="与基线文件对比，找出变近的角度段（标定 0° 用）")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()

    port = a.port or find_port()
    if not port:
        print("找不到串口。先确认雷达插上了，或用 --port 指定。")
        return 1

    print("=" * 62)
    print(f"  雷达角度-距离实测   {port} @ {RADAR_BAUD}   采样 {a.secs:.0f}s"
          + ("   [保留弱回波]" if a.keep_weak else ""))
    print("=" * 62)
    try:
        buckets, last, stats = read_scan(port, a.secs, a.keep_weak)
    except Exception as e:
        print(f"打不开或读不了串口：{type(e).__name__}: {e}")
        print("  ① 雷达插了吗  ② 被别的程序占用了吗  ③ 换根 USB 线")
        return 1

    if stats["frames_ok"] == 0:
        print("0 个合法帧。查：线序（雷达 TXD→板子 RXD 要交叉）、供电 5V、波特率 230400")
        return 1

    rows = aggregate(buckets, a.step)

    # ---- 判定摘要 ----
    print("\n【判定】")
    for lv, txt in verdict(stats, rows):
        print(f"  [{lv:4s}] {txt}")

    # ---- 角度表 ----
    print(f"\n【角度-距离表】{a.step}° 一档，距离取中位数")
    print(f"  {'角度':<12}{'距离':>10}{'点数':>8}{'强度':>8}   条")
    print("  " + "-" * 56)
    dmax = max((r[2] for r in rows if r[2] is not None), default=1.0)
    for s, e, d, n, it in rows:
        if d is None:
            print(f"  {s:3d}°-{e:3d}°{'无回波':>10}{n:>8}{'—':>8}")
        else:
            bar = "█" * max(1, int(round(d / max(dmax, 0.1) * 18)))
            print(f"  {s:3d}°-{e:3d}°{d:>9.2f}m{n:>8}{it:>8.0f}   {bar}")

    # ---- 距离分段 ----
    if a.segments:
        segs = segments(buckets)
        print(f"\n【距离分段】{len(segs)} 段（相邻距离差 <0.3m 归为同一物体）")
        print("  角度范围        中位距离   极差    跨度")
        print("  " + "-" * 46)
        for s in segs:
            span = s["end"] - s["start"] + 1
            print(f"  {s['start']:3d}°~{s['end']:3d}°"
                  f"{statistics.median(s['vals']):10.2f}m"
                  f"{max(s['vals']) - min(s['vals']):7.2f}m{span:6d}°")
        if segs:
            print(f"\n  距离突变处（物体边界）：", end="")
            bounds = [f"{s['start']}°" for s in segs[1:]]
            print("，".join(bounds) if bounds else "无")

    # ---- 基线保存 / 差分对比 ----
    if a.save_base:
        base = {str(d): v for d, v in angle_medians(buckets).items()}
        with open(a.save_base, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=0)
        print(f"\n基线已存 {a.save_base}（{len(base)}/360 个角度有回波）")
        print("  下一步：把纸板放到你认为的车头方向，再跑一次 --diff " + a.save_base)

    if a.diff:
        try:
            with open(a.diff, encoding="utf-8") as f:
                base = json.load(f)
        except Exception as e:
            print(f"\n读不了基线文件 {a.diff}: {e}")
        else:
            show_diff(buckets, base)

    # ---- 原始点 ----
    if a.raw and last:
        print(f"\n【最后一圈原始点】{len(last)} 个（按角度排序）")
        print(f"  {'角度':>7}{'距离':>10}{'强度':>7}{'弱回波':>8}")
        for ang, dist, it, weak in last:
            ds = "无回波" if dist is None else f"{dist/1000.0:.2f}m"
            print(f"  {ang:6.1f}°{ds:>10}{it:>7}{'是' if weak else '':>8}")

    # ---- CSV ----
    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig") as f:
            f.write("angle_start,angle_end,distance_m,points,intensity\n")
            for s, e, d, n, it in rows:
                f.write(f"{s},{e},{'' if d is None else f'{d:.3f}'},{n},{it:.1f}\n")
        print(f"\n已存 {a.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
