# -*- coding: utf-8 -*-
"""
vision_receiver.py — 板子 PS 侧的视觉结果接收器（郑用）
=======================================================
跑在 PYNQ 的 Linux 上（也可以在 PC 上测试）。

作用：监听 UDP，收 PC 发来的 Vision JSON，喂给郑的 VisionProvider。

【为什么走 PS 网口，而不是让 PL 转发】
板子是正点原子领航者 ZYNQ V2，有**两个千兆网口**：
  · PL 网口（靠近摄像头那个）：欧阳的 UDP 发图专用，Verilog 里写死 IP 192.168.1.10
  · PS 网口：Linux 的 eth0，PYNQ 正常上网口
两者独立。所以 PC 双发：
  · 图像 → PL 网口（192.168.1.10:1234）
  · 结果 → PS 网口（192.168.1.20:5000）← 本文件监听这个
**这样一行业务 Verilog 都不用改。**

⚠️ 如果板子实际只有一个网口，本方案不成立，退回"PL 转发 → AXI → PS"（见 README 断点④方案B）。

跑法：
    python3 vision_receiver.py --port 5000 --verbose
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

import protocol                                       # noqa: E402
from contract import CONTRACT_VERSION, VISION_HZ_MIN  # noqa: E402


class VisionReceiver:
    """UDP 收 Vision JSON → 校验 → 交给 VisionProvider

    与 car/vision/vision_provider.py 的分工：
      · 本类只管"收包 + 解 JSON + 统计"
      · schema 校验和 t 时间戳由 VisionProvider.push() 负责（单一真源，不重复实现）
    """

    def __init__(self, port=5000, provider=None, on_drop=None, verbose=False):
        self.port = port
        self.provider = provider
        self.on_drop = on_drop
        self.verbose = verbose
        self.sock = None
        self._stop = threading.Event()
        self._th = None
        self._recv_times = []
        self._t_start = time.time()
        self.stats = {"pkts": 0, "bad_json": 0, "rejected": 0, "last_t": 0, "rate_hz": 0.0}

    def _tick_rate(self):
        """2 秒滑动窗口算发送频率 —— 低于契约值车会一顿一顿地停"""
        now = time.time()
        self._recv_times = [t for t in self._recv_times if now - t <= 2.0]
        self.stats["rate_hz"] = len(self._recv_times) / 2.0

    def rate_settled(self):
        """启动 3 秒后频率才有意义（窗口填满），之前别报警"""
        return time.time() - self._t_start > 3.0

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.settimeout(0.2)
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            self.stats["pkts"] += 1
            self._recv_times.append(time.time())
            self._tick_rate()
            try:
                raw = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                self.stats["bad_json"] += 1
                self._drop("json parse failed")
                continue

            if self.provider is not None:
                v = self.provider.push(raw)
                if v is None:
                    self.stats["rejected"] += 1
                else:
                    self.stats["last_t"] = v.get("t", 0)
                    if self.verbose:
                        n = v.get("nearest")
                        extra = (f"{n['class']} {n['confidence']:.2f} "
                                 f"cx={n['cx']:.2f} cy={n['cy']:.2f}") if n else "无障碍"
                        print(f"  [{addr[0]}] {extra}")

    def _drop(self, why):
        if self.verbose:
            print(f"  [!] {why}")
        if self.on_drop:
            self.on_drop(why)

    def stop(self):
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# ============ 独立运行：只收不决策，用来验证 PC→PS 链路 ============

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    from vision.vision_provider import VisionProvider
    vp = VisionProvider(on_drop=lambda w: print(f"  [drop] {w}"))
    rx = VisionReceiver(a.port, vp, verbose=True).start()

    print(f"vision_receiver 已启动  contract v{CONTRACT_VERSION}")
    print(f"监听 UDP 0.0.0.0:{a.port}，等 PC 的 YOLO 结果...")
    print(f"⚠ 视觉频率必须 ≥ {VISION_HZ_MIN:.0f}Hz（郑的 vision_timeout_ms="
          f"{protocol.CFG['vision_timeout_ms']}ms），低了车会一顿一顿停\n")
    t0 = time.time()
    warned = False
    try:
        while True:
            time.sleep(2.0)
            v = vp.poll()
            s = rx.stats
            age = "从未收到" if not s["last_t"] else f"{protocol.now_ms() - s['last_t']}ms 前"
            n = (f"{v['nearest']['class']}" if v and v.get("nearest") else "无障碍") if v else "无有效帧"
            print(f"[{time.time()-t0:5.0f}s] 收包{s['pkts']:5d} "
                  f"{s['rate_hz']:4.1f}Hz 坏JSON{s['bad_json']:3d} 拒收{s['rejected']:3d} "
                  f"| 最新: {n} ({age})", flush=True)
            if s["pkts"] > 3 and s["rate_hz"] < VISION_HZ_MIN and not warned and rx.rate_settled():
                pct = max(0.0, 100 * (1 - s["rate_hz"] * protocol.CFG["vision_timeout_ms"] / 1000))
                print(f"  ⚠ 视觉频率 {s['rate_hz']:.1f}Hz < {VISION_HZ_MIN:.0f}Hz："
                      f"决策至少 {pct:.0f}% 的时间会被判 R5 停车"
                      f"（实测 2Hz 时是 70%，见 contract.py 频率契约）", flush=True)
                warned = True
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()
        print("\n退出")


if __name__ == "__main__":
    main()
