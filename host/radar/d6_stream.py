# -*- coding: utf-8 -*-
"""
radar/d6_stream.py —— 把 COIN-D6 接到 car7020 的融合管线（不改队友任何文件）

为什么要这个桥
--------------
队友 `car7020/host/app/fusion/lidar_ld06.py` 假设 COIN-D6 走 **LD06 协议**
（47 字节 `0x54 0x2C … CRC8`）。实测 D6 是 **85 字节 `AA 55` 帧 + 双字节异或校验**，
两者包头都不同，所以队友那条串口路径对 D6 永远锁不上包（`--lidar serial` 会一圈都凑不出来）。

本文件不改他们任何代码，只做三件事：

  1) 用我们已定案的 D6 解析器（`d6_protocol.py`）读串口
  2) 按 `fusion_core.Scan` 的契约产出 **每圈一份 (angles, ranges)**：
        - angles：0~360°，**逆时针为正**（队友 `DecisionConfig.ang_sign=-1` 的约定）
        - ranges：米，float；无回波用 None（队友 `Scan` 会转成 nan）
        - 分箱 0.5°（队友 `SerialLidar.bin_deg` 默认值），同箱取中位数
  3) 以 JSON 报文送到队友 `detect_fusion_live.py --lidar udp --lidar-port 15001`：
        {"angles":[度...], "ranges":[米 或 null ...]}

配合使用（三个窗口）
--------------------
    # 窗口1：本驱动
    python d6_stream.py --port COM5 --udp 127.0.0.1:15001
    # 窗口2：队友的融合主程序（他们原样跑，不用改）
    python detect_fusion_live.py --lidar udp --lidar-port 15001
    # 窗口3（可选）：需要时给板子直发 4 字节距离帧
    python d6_stream.py --port COM5 --udp 127.0.0.1:15001 --dist-udp 192.168.1.10:5000

角度约定
--------
    theta = (theta0 + sign * d6_angle) % 360          # 与队友 cam_to_lidar() 同式
    - D6 自身：顺时针增大、0° = 外壳"单个安装耳"
    - 队友要：逆时针为正、0° = 车头 → 默认 sign=-1
    - 装车后用 --theta0 把 0° 拧到车头正前方（标定见 docs/烧录…无关，见 README）

自检
----
    python d6_stream.py --selftest --secs 3
打印帧率/坏帧/点数/距离分布 + 12 扇区（他们坐标系），与 `radar_console` 的体检同口径。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import d6_protocol as d6                                    # noqa: E402

# ----------------------------------------------------------------- 一 圈 扫 描
class RevBuilder:
    """按角度回绕切圈；每箱取中位数（和队友 SerialLidar._flush 同策略）。

    mask: [(lo, hi), ...] 度区间，落在里面的箱一律置 None（无回波）。
          倒装在车底时，车体/轮子挡住的那几十度会持续返回几厘米，
          屏蔽掉可避免融合把它当障碍。
    """

    def __init__(self, bin_deg=0.5, theta0=0.0, sign=-1, mask=None):
        self.bin_deg = float(bin_deg)
        self.nbins = int(round(360.0 / self.bin_deg))
        self.theta0 = float(theta0)
        self.sign = int(sign)
        self.mask = list(mask or [])
        self.acc = {}
        self.last_raw = None
        self.revs = 0
        self._t_rev = time.monotonic()
        self.hz = 0.0

    def feed(self, pts):
        """喂一帧的点 [(angle_deg, dist_mm|None, inten, weak)]；凑满一圈返回 (angles, ranges)。

        ⚠ 圈的切分用 **D6 原生角**（顺时针单调增），不要用转换后的角：
          sign=-1 时转换后的角是单调**减**的，回绕方向相反，用同一判据会一圈都切不出来。
        """
        out = None
        for ang, dist_mm, _it, _wk in pts:
            if self.last_raw is not None and ang < self.last_raw - 180.0:
                out = self._flush()                     # 原生角回绕 → 一圈结束
            self.last_raw = ang
            if dist_mm is None:
                continue
            a = (self.theta0 + self.sign * ang) % 360.0
            k = int(a / self.bin_deg) % self.nbins
            self.acc.setdefault(k, []).append(dist_mm / 1000.0)
        return out

    def _flush(self):
        angles = np.arange(self.nbins) * self.bin_deg
        ranges = np.full(self.nbins, np.nan, dtype=np.float64)
        for k, ds in self.acc.items():
            ranges[k] = float(np.median(ds))
        self.acc = {}
        # 屏蔽遮挡扇区（车体/轮子/支架）
        for lo, hi in self.mask:
            if lo <= hi:
                m = (angles >= lo) & (angles < hi)
            else:                                   # 跨 0°
                m = (angles >= lo) | (angles < hi)
            ranges[m] = np.nan
        now = time.monotonic()
        dt = now - self._t_rev
        self._t_rev = now
        self.hz = 1.0 / dt if dt > 1e-6 else 0.0
        self.revs += 1
        return angles, ranges


def scan_to_json(angles, ranges):
    """队友 UdpLidar 的报文格式：None 表示无回波（他们 Scan 会转成 nan）。"""
    return json.dumps({
        "angles": [round(float(a), 3) for a in angles],
        "ranges": [None if not np.isfinite(r) else round(float(r), 4) for r in ranges],
    })


# ----------------------------------------------------------------- 距 离 帧
def pack_dist_frame(dist_mm):
    """队友 PL 的 4 字节距离帧：AA 55 | 距离(mm, 大端)。0xFFFF = 无目标。"""
    v = 0xFFFF if dist_mm is None else max(0, min(0xFFFE, int(dist_mm)))
    return bytes((0xAA, 0x55, (v >> 8) & 0xFF, v & 0xFF))


def front_nearest(angles, ranges, corridor_deg=35.0):
    """车头 ±corridor 内的最近有效距离（mm）；没有则 None。

    ⚠ 扇区宽度必须与队友 `DecisionConfig.corridor_deg`（默认 **35°**）一致：
      他们的融合只关心前方 ±35°，侧后方的人不该让车停。
      给 PL 直发距离时也照这个宽度截，语义才和他们原本的"最近的人"对齐。
    """
    if angles is None or len(angles) == 0:
        return None
    d = np.abs(((angles - 0.0 + 180.0) % 360.0) - 180.0)      # 到 0° 的角距
    m = (d <= corridor_deg) & np.isfinite(ranges)
    if not m.any():
        return None
    return int(round(float(np.min(ranges[m])) * 1000.0))


def pl_state_from_dist(dist_mm, stop_mm=1000, warn_mm=3000):
    """按队友 motor_decision.v 的阈值把距离翻译成 4 档状态（只用于自检显示）。"""
    if dist_mm is None or dist_mm == 0xFFFF:
        return "SAFE(无目标: 停车+静音)"
    if dist_mm > warn_mm:
        return "CLEAR(70%%前进, 静音)"
    if dist_mm <= stop_mm:
        return "STOP(停车 + 连续响)"
    return "WARN(70%%前进 + 间歇响)"


class DistSender(threading.Thread):
    """独立线程按固定频率发 4 字节距离帧。

    为什么单独开线程：解析循环的速度受串口帧率牵制（~170 帧/s，单帧 5.9ms），
    在主循环里按 now-last>=50ms 判发，实测只有 16 Hz 且抖动。
    队友 PL 的看门狗是 400ms，频率抖动会让 WARN/STOP 切换的时间戳不准；
    独立线程用"下一个截止时刻"推进，能做到稳定 20 Hz。
    """

    def __init__(self, sock, addr, hz, getter):
        threading.Thread.__init__(self, daemon=True)
        self.sock = sock
        self.addr = addr
        self.period = 1.0 / max(hz, 1.0)
        self.getter = getter
        self._stop = threading.Event()
        self.sent = 0

    def run(self):
        nxt = time.monotonic()
        while not self._stop.is_set():
            d = self.getter()
            try:
                self.sock.sendto(pack_dist_frame(d), self.addr)
                self.sent += 1
            except OSError:
                pass
            nxt += self.period
            dt = nxt - time.monotonic()
            if dt > 0:
                time.sleep(dt)
            else:
                nxt = time.monotonic()      # 落后了就别追，避免连发

    def stop(self):
        self._stop.set()


# ----------------------------------------------------------------- 主 循 环
def list_ports():
    """列出可用串口，标注哪个像 D6（CH340 / 1a86:7523）。"""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("没装 pyserial，无法列串口：pip install pyserial")
        return []
    out = []
    for p in lp.comports():
        vid = "%04X:%04X" % (p.vid or 0, p.pid or 0)
        like = ("1A86:7523" in vid) or ("CH340" in (p.description or "").upper()) \
            or ("CH34" in (p.description or "").upper())
        out.append((p.device, p.description or "", vid, like))
    return out


def resolve_port(port):
    """把配置里的端口名变成真端口；支持 "auto"（自动找 CH340，找不到就找第一个）。

    这样同一个压缩包换台电脑也能直接跑：不用手改 COM 号（Linux 上是 /dev/ttyUSB0）。
    """
    if port and str(port).lower() not in ("auto", ""):
        return port
    ports = list_ports()
    if not ports:
        if str(port).lower() == "auto":
            raise RuntimeError("没有找到任何串口 —— 雷达插好了吗？（先跑 --list-ports 看看）")
        return port or "COM5"
    for dev, desc, vid, like in ports:
        if like:
            print("[端口] 自动选中 %s（%s %s）" % (dev, desc, vid))
            return dev
    dev = ports[0][0]
    print("[端口] 没找到 CH340，退回第一个串口 %s（%s）" % (dev, ports[0][1]))
    return dev


def open_serial(port, baud):
    import serial
    port = resolve_port(port)
    ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.2)
    try:                                    # 手册：DTR 拉高会复位雷达
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    return ser


def run_selftest(port, baud, secs, sign, theta0, bin_deg, mask=None, corridor_deg=35.0):
    try:
        ser = open_serial(port, baud)
    except Exception as e:
        print("打不开串口 %s：%s" % (port, str(e)[:80]))
        print("排查：① 雷达插了吗（先跑 --list-ports）② 端口被别的程序占了吗"
              "（队友的上位机/串口助手）③ 换个 USB 口")
        return 2
    rb = RevBuilder(bin_deg, theta0, sign, mask)
    f_ok = f_bad = 0
    npts = nvalid = 0
    dists = []
    best = None
    t0 = time.time()
    print("自检 %s @%d，%.1fs …（角度按 sign=%+d theta0=%.1f 转换）"
          % (port, baud, secs, sign, theta0))
    while time.time() - t0 < secs:
        fr = d6.read_frame(ser)
        if fr is None:
            f_bad += 1
            continue
        pts = d6.parse_frame_ex(*fr)
        if pts is None:
            f_bad += 1
            continue
        f_ok += 1
        npts += len(pts)
        for a, d, _i, _w in pts:
            if d is not None:
                nvalid += 1
                dists.append(d / 1000.0)
        rev = rb.feed(pts)
        if rev is not None:
            best = rev
    ser.close()
    dt = time.time() - t0
    print("  帧 OK %d / 坏 %d   %.1f 帧/s" % (f_ok, f_bad, f_ok / dt))
    print("  点 %d，有效 %d（%.1f%%）" % (npts, nvalid, 100.0 * nvalid / max(1, npts)))
    if dists:
        a = np.array(dists)
        print("  距离 min %.2f / 中位 %.2f / p90 %.2f / max %.2f m"
              % (a.min(), np.median(a), np.percentile(a, 90), a.max()))
    print("  圈数 %d，实测 %.1f 圈/s" % (rb.revs, rb.revs / dt))
    if best is None:
        print("  ⚠ 一圈都没凑出来（数据太少或角度不连续）")
        return 1
    ang, rng = best
    # 12 扇区（他们坐标系；s0 = 345°~15°）
    print("  12 扇区（他们坐标系，0°=车头，逆时针为正）：")
    cells = []
    for k in range(12):
        lo = (345 + 30 * k) % 360
        m = ((ang - lo) % 360.0) < 30.0
        mm = rng[m & np.isfinite(rng)]
        cells.append("--" if mm.size == 0 else "%.2f" % float(np.median(mm)))
    for i in range(0, 12, 4):
        print("    " + "   ".join("s%-2d %5s" % (j, cells[j]) for j in range(i, i + 4)))
    # 给 PL 直发距离时的预览（4 字节帧 + 会落到哪一档）
    dmm = front_nearest(ang, rng, corridor_deg)
    frame = pack_dist_frame(dmm)
    print("  距离帧预览（前方 ±%.0f°）：%s  →  %s"
          % (corridor_deg, " ".join("%02X" % b for b in frame),
             pl_state_from_dist(dmm)))
    return 0


def load_config(path=None):
    """读 d6_config.json（默认与本文件同目录）→ dict。

    返回的 `mask` 已归一化成 `"137:223,300:330"` 字符串，方便两个入口（stream/view）共用。
    读不到就返回空 dict（用代码里的默认值）。
    """
    p = path or os.path.join(_HERE, "d6_config.json")
    cfg = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception as e:
            print("配置读取失败（用默认值）：%s" % e)
            return {}
    if isinstance(cfg.get("mask"), list):
        cfg["mask"] = ",".join("%g:%g" % (a, b) for a, b in cfg["mask"])
    cfg.pop("_notes", None)
    cfg.pop("config", None)
    return cfg


def parse_mask(s):
    """'137:223,300:330' → [(137.0, 223.0), (300.0, 330.0)]；跨 0° 的区间交给 RevBuilder 处理。"""
    out = []
    if not s:
        return out
    for part in str(s).replace("，", ",").replace(" ", "").split(","):
        if not part:
            continue
        lo_s, _, hi_s = part.partition(":")
        try:
            lo = float(lo_s)
            hi = float(hi_s) if hi_s else lo + 1.0
        except ValueError:
            raise ValueError("mask 格式应为 120:240 或 120:240,300:330")
        out.append((lo % 360.0, hi % 360.0))
    return out


def main():
    try:                                    # Windows 控制台默认 GBK，中文会乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--list-ports" in sys.argv:
        ps = list_ports()
        if not ps:
            print("没有可用串口（或没装 pyserial）")
        for dev, desc, vid, like in ps:
            print("%s %-12s %-42s %s" % ("*" if like else " ", dev, desc, vid))
        return 0

    # ---- 可选配置文件：与本文件同目录的 d6_config.json（CLI 覆盖它）----
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args()
    cfg_path = known.config or os.path.join(_HERE, "d6_config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        cfg = load_config(cfg_path)
        print("读取配置：%s" % cfg_path)

    ap = argparse.ArgumentParser(description="COIN-D6 → car7020 融合管线（不修改队友代码）")
    ap.add_argument("--port", default="auto",
                    help="串口号；写 auto 自动找 CH340（换电脑/换系统都不用改）")
    ap.add_argument("--list-ports", action="store_true", help="列出可用串口后退出")
    ap.add_argument("--baud", type=int, default=d6.BAUD)
    ap.add_argument("--udp", default="127.0.0.1:15001",
                    help="扫描 JSON 目标（队友 detect_fusion_live.py --lidar udp 的端口）")
    ap.add_argument("--no-udp", action="store_true", help="只解析不发送")
    ap.add_argument("--hz", type=float, default=10.0, help="扫描发送频率上限")
    ap.add_argument("--bin-deg", type=float, default=0.5, help="角度分箱粒度（队友默认 0.5）")
    ap.add_argument("--sign", type=int, default=-1, choices=[1, -1],
                    help="角度方向：-1 = D6 顺时针转成逆时针为正（队友默认）")
    ap.add_argument("--theta0", type=float, default=0.0, help="装车偏航补偿（度）")
    ap.add_argument("--mount", default="normal", choices=["normal", "inverted"],
                    help="安装姿态：inverted=倒装（平面内镜像，等价于 sign 取反）")
    ap.add_argument("--invert", action="store_true",
                    help="显式把 sign 取反（倒装时用，与 --mount inverted 等效）")
    ap.add_argument("--mask", default=None,
                    help="屏蔽被车体/轮子遮挡的扇区，单位度，如 120:240 或 120:240,300:330")
    ap.add_argument("--dist-udp", default=None,
                    help="可选：给板子发 4 字节距离帧，如 192.168.1.10:5000")
    ap.add_argument("--dist-hz", type=float, default=20.0)
    ap.add_argument("--dist-corridor", type=float, default=35.0,
                    help="--dist-udp 用的前方走廊半宽（度）；必须与队友 corridor_deg 一致，默认 35")

    ap.add_argument("--save", default=None, help="把最后一圈存成 json（回放用）")
    ap.add_argument("--secs", type=float, default=0, help="运行 N 秒后退出（0=一直跑）")
    ap.add_argument("--selftest", action="store_true", help="只做健康自检，不发送")
    # ⚠ set_defaults 必须在所有 add_argument 之后：argparse 里 add_argument(default=…)
    #   会覆盖先前 set_defaults 设的同名默认值（这是个很容易踩的顺序坑）。
    if cfg:
        allowed = {"port", "baud", "udp", "hz", "bin_deg", "sign", "theta0",
                   "mount", "mask", "dist_udp", "dist_hz", "dist_corridor"}
        ap.set_defaults(**{k: v for k, v in cfg.items() if k in allowed})

    a = ap.parse_args()

    # 安装姿态 → 角度符号；遮挡扇区 → (lo,hi) 列表
    # 注意：命令行显式给了 --sign 时以它为准（不再叠加倒装取反），避免"改两处"踩坑
    explicit_sign = any(s == "--sign" or s.startswith("--sign=") for s in sys.argv[1:])
    if (a.mount == "inverted" or a.invert) and not explicit_sign:
        a.sign = -a.sign
    try:
        a.mask = parse_mask(a.mask)
    except ValueError as e:
        ap.error(str(e))

    if a.selftest:
        return run_selftest(a.port, a.baud, a.secs or 3.0, a.sign, a.theta0,
                            a.bin_deg, a.mask, a.dist_corridor)

    ser = None                      # 由主循环负责首次打开/重连
    sock = None
    addr = None
    if not a.no_udp:
        host, _, port = a.udp.rpartition(":")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = (host or "127.0.0.1", int(port))
    dsock = None
    daddr = None
    if a.dist_udp:
        host, _, port = a.dist_udp.rpartition(":")
        dsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        daddr = (host, int(port))

    rb = RevBuilder(a.bin_deg, a.theta0, a.sign, a.mask)
    print("D6 %s @%d → UDP %s%s" % (a.port, a.baud,
                                   a.udp if sock else "(不发)",
                                   ("  + 距离帧 %s" % a.dist_udp) if dsock else ""))
    print("角度：sign=%+d theta0=%.1f  分箱 %.2f°  发送上限 %.0f Hz   Ctrl+C 退出"
          % (a.sign, a.theta0, a.bin_deg, a.hz))

    last_send = 0.0
    last_stat = 0.0
    f_ok = f_bad = 0
    last_scan = None
    t0 = time.time()
    period = 1.0 / max(a.hz, 0.1)
    last_d_mm = None
    reconnects = 0

    # 距离帧用独立线程按固定 20Hz 发（主循环受串口帧率牵制，做不到稳定 20Hz）
    dsender = None
    if dsock:
        dsender = DistSender(dsock, daddr, a.dist_hz, lambda: last_d_mm)
        dsender.start()

    try:
        while True:
            if a.secs and time.time() - t0 > a.secs:
                break
            # ---- 串口掉线自动重连（队友 PL 的看门狗是 400ms，断了就是 SAFE 停车）----
            if ser is None:
                try:
                    ser = open_serial(a.port, a.baud)
                    reconnects += 1
                    print("  [重连] 雷达已恢复（第 %d 次）" % reconnects)
                except Exception as e:
                    print("  [重连] %s 打不开：%s" % (a.port, str(e)[:60]))
                    time.sleep(1.0)
                    continue
            try:
                fr = d6.read_frame(ser)
            except Exception as e:                      # 拔线/掉线
                print("  [掉线] %s，稍后重连：%s" % (a.port, str(e)[:60]))
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                time.sleep(0.5)
                continue
            if fr is None:
                f_bad += 1
                continue
            pts = d6.parse_frame_ex(*fr)
            if pts is None:
                f_bad += 1
                continue
            f_ok += 1
            rev = rb.feed(pts)
            if rev is not None:
                last_scan = rev

            now = time.monotonic()
            ang = rng = None
            if last_scan is not None:
                ang, rng = last_scan
            # 扫描：按 hz 限流发最近一圈（队友 detect_fusion_live.py --lidar udp）
            if sock and ang is not None and now - last_send >= period:
                try:
                    sock.sendto(scan_to_json(ang, rng).encode("utf-8"), addr)
                except OSError:
                    pass
                last_send = now
            # 距离值：每出一圈就刷新一次（真正的发送由 DistSender 线程按 20Hz 做）
            if dsock and ang is not None:
                last_d_mm = front_nearest(ang, rng, a.dist_corridor)

            if now - last_stat >= 5.0:
                last_stat = now
                extra = ""
                if dsender:
                    extra = "  距离帧=%s（%s，已发 %d）" % (
                        "--" if last_d_mm is None else "%dmm" % last_d_mm,
                        pl_state_from_dist(last_d_mm).split("(")[0], dsender.sent)
                print("  运行 %.0fs  帧 OK %d/坏 %d  圈 %.1f Hz  重连 %d%s"
                      % (time.time() - t0, f_ok, f_bad, rb.hz, reconnects, extra))
    except KeyboardInterrupt:
        print("\n停止")
    finally:
        try:
            ser.close()
        except Exception:
            pass
        if sock:
            sock.close()
        if dsender:
            dsender.stop()
        if dsock:
            dsock.close()
        if a.save and last_scan is not None:
            ang, rng = last_scan
            with open(a.save, "w", encoding="utf-8") as f:
                f.write(scan_to_json(ang, rng))
            print("已存 %s" % a.save)
        print("合计：帧 OK %d / 坏 %d，圈 %d" % (f_ok, f_bad, rb.revs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
