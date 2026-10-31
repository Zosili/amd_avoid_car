# -*- coding: utf-8 -*-
"""
radar/radar_provider.py — 雷达数据双轨提供者
live：后台线程读串口，维护最新一圈 → Radar JSON
sim ：按脚本回放，接口与 live 完全一致 → 下游零改动
"""
import threading
import time

import protocol
from radar import d6_protocol


class LiveRadarProvider:
    """实时雷达。loop 里 poll() 拿最新扇区 JSON；线程只负责读码攒圈。"""

    def __init__(self, port=d6_protocol.PORT, baud=d6_protocol.BAUD):
        import serial
        self._ser = serial.Serial(port, baud, bytesize=8, parity="N",
                                  stopbits=1, timeout=0.2)
        # dtr/rts 显式拉低：官方手册提示 DTR 拉高会触发雷达硬件复位
        self._ser.dtr = False
        self._ser.rts = False
        self._scan = []
        self._last = None
        self._last_stats_t = 0.0
        # 扇区统计的重算间隔（秒）。0 = 每帧都算（旧行为，CPU 高 5 倍）
        hz = getattr(d6_protocol, "RECOMPUTE_HZ", 0) or 0
        self._stats_dt = (1.0 / hz) if hz > 0 else 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        # 启动兜底：FAQ#9/#27 称部分固件上电不转、需先发启动指令。
        # 本机固件上电即吐流；若 2s 无帧则依次发两套官方启动指令。
        sent_at = [None]      # 第 1 套已发时间 / 第 2 套已发时间
        while not self._stop.is_set():
            fr = d6_protocol.read_frame(self._ser)
            if fr is None:
                now = time.monotonic()
                if sent_at[0] is None:
                    sent_at[0] = now
                elif now - sent_at[0] > 2.0 and len(sent_at) == 1:
                    try:
                        self._ser.write(d6_protocol.START_CMD_1)
                        self._ser.flush()
                    except Exception:
                        pass
                    sent_at.append(now)
                elif len(sent_at) == 2 and now - sent_at[1] > 2.0:
                    try:
                        self._ser.write(d6_protocol.START_CMD_2)
                        self._ser.flush()
                    except Exception:
                        pass
                    sent_at.append(now)      # 只发一轮，之后纯等待
                continue
            pts = d6_protocol.parse_frame_ex(*fr)
            if pts is None:
                continue
            now = time.monotonic()
            with self._lock:
                self._scan.extend(pts)
                if len(self._scan) >= 400:
                    self._scan = self._scan[-400:]
                # 扇区统计按 RECOMPUTE_HZ 限流：雷达 163 帧/秒，但决策只有 10Hz，
                # 每帧都重算实测 94% 是重复劳动。点数照常攒，只是不每次重算。
                if self._stats_dt <= 0 or now - self._last_stats_t >= self._stats_dt:
                    self._last = d6_protocol.scan_to_radar_json(
                        self._scan, t=protocol.now_ms())
                    self._last_stats_t = now

    def poll(self):
        with self._lock:
            return dict(self._last) if self._last else None

    def close(self):
        self._stop.set()
        try:
            self._ser.close()
        except Exception:
            pass


class ReplayRadarProvider:
    """按场景脚本回放。script: [(相对t_ms, radar_json或None), ...]，None=该帧无数据。"""

    def __init__(self, script, t0=None):
        self.script = sorted(script, key=lambda x: x[0])
        self.t0 = t0 if t0 is not None else protocol.now_ms()
        self.i = 0
        self._cur = None

    def poll(self, now=None):
        now = protocol.now_ms() if now is None else now
        rel = now - self.t0
        while self.i < len(self.script) and self.script[self.i][0] <= rel:
            self._cur = self.script[self.i][1]
            self.i += 1
        return dict(self._cur) if self._cur else None
