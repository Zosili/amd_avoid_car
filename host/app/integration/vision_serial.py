# -*- coding: utf-8 -*-
"""
vision_serial.py — 方案①：视觉结果走**一根串口线**（不用第二个网口）
=====================================================================
同一个文件两边都用：
  · PC 侧（欧阳）：SerialVisionSender.send(boxes, classes, scores)
  · 板子 PS 侧（郑）：SerialVisionReceiver → VisionProvider

【为什么有这个东西】
标准方案是双网口（图像走 PL 口、结果走 PS 口）。如果只想用一个网口，
就把结果从"网线"改成"串口线"：
    PL 口    → 图像（欧阳的代码，不动）
    USB-TTL  → 视觉结果（本文件）
零 Verilog 改动。

【带宽够不够】
一包 ~200 字节，10Hz = 2 KB/s。
  115200 波特 = 11.5 KB/s  → 5 倍余量
  460800 波特 = 46   KB/s  → 23 倍余量（默认，给 objects 数组留空间）
延迟：200 字节 @460800 = 4.3ms，相对 YOLO 本身的 100ms 可忽略。

【接线】⚠️ 不能一根 USB 线直连！PC 和板子的 USB 口都是 Host，Host 对 Host 不通。
必须两个 USB-TTL 模块背靠背：

    PC ─USB线─> [USB-TTL A] ──杜邦线 TX↔RX 交叉、GND↔GND──> [USB-TTL B] ─USB线─> 板子 USB 口

  · VCC 两边都不接（各自从自己的 USB 取电，接了一起烧）
  · 板子侧出现 /dev/ttyUSB1，PC 侧出现 COM7（设备管理器里看）
  · ⚠️ 别用板载 console 那个串口（UART0），那是登录用的，会串数据
  · 更省模块的替代：只用一个 USB-TTL 接 PS 的 UART1（MIO 排针），
    但要在 Block Design 里使能 UART1（徐的活）

【协议】
一行一个 JSON，\\n 结尾。没有校验和 —— JSON 结构本身就是校验，
截断/乱码的行 json.loads 必然失败，直接丢弃，下一行自动重新同步。

跑法：
  # PC 侧（假推理，验证链路）
  python vision_serial.py --mode send --port COM7 --hz 10

  # 板子 PS 侧
  python3 vision_serial.py --mode recv --port /dev/ttyUSB1 --baud 460800

  # 没有硬件也能测协议
  python vision_serial.py --mode selftest
"""
import argparse
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

from contract import detections_to_vision, CONTRACT_VERSION, VISION_HZ_MIN  # noqa: E402

DEFAULT_BAUD = 460800
MAX_LINE = 8192          # 单行上限，超了就当垃圾清掉
MAX_BACKLOG = 2048       # 积压超过这么多字节就只保留最新一帧（防"旧数据装新鲜"）


# ============ 发送端（PC / 欧阳） ============

class SerialVisionSender:
    """接口和 vision_sender.VisionSender 完全一致，切换只改类名。"""

    def __init__(self, port="COM7", baud=DEFAULT_BAUD, conf_th=0.25,
                 class_names=None, verbose=False, ser=None):
        if ser is None:
            import serial
            ser = serial.Serial(port, baud, bytesize=8, parity="N",
                                stopbits=1, timeout=0.05, write_timeout=0.2)
        self.ser = ser
        self.conf_th = conf_th
        self.class_names = class_names
        self.verbose = verbose
        self.sent = 0
        self.failed = 0
        self.last_obstacle = None

    def send(self, boxes, classes, scores, img_w=640, img_h=480):
        """boxes 必须是**原图像素坐标**（letterbox 还原之后的）。"""
        v = detections_to_vision(boxes, classes, scores, img_w, img_h,
                                 class_names=self.class_names, conf_th=self.conf_th)
        # separators 去掉空格：一包能小 10~15%
        line = json.dumps(v, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self.ser.write(line.encode("utf-8"))
            self.sent += 1
        except Exception as e:
            self.failed += 1
            if self.verbose:
                print(f"[!] 串口写失败: {e}")
            return None

        if self.verbose:
            n = v.get("nearest")
            extra = (f"{n['class']} {n['confidence']:.2f} cx={n['cx']:.2f}"
                     if n else "无障碍")
            print(f"[发#{self.sent}] {extra}  {len(line)}B")
        self.last_obstacle = v.get("obstacle")
        return v

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ============ 接收端（板子 PS / 郑） ============

class SerialVisionReceiver:
    """串口收 Vision JSON → 校验 → 交给 VisionProvider

    与 vision_receiver.VisionReceiver（UDP 版）接口一致：
        rx = SerialVisionReceiver("/dev/ttyUSB1", vp).start()
        rx.poll() / rx.stats / rx.stop()
    """

    def __init__(self, port="/dev/ttyUSB1", baud=DEFAULT_BAUD, provider=None,
                 verbose=False, on_drop=None, ser=None):
        if ser is None:
            import serial
            ser = serial.Serial(port, baud, bytesize=8, parity="N",
                                stopbits=1, timeout=0.05)
        self.ser = ser
        self.provider = provider
        self.verbose = verbose
        self.on_drop = on_drop
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._th = None
        self._recv_times = []
        self._t_start = time.time()
        self.stats = {"lines": 0, "bad_json": 0, "rejected": 0,
                      "stale": 0, "overflow": 0, "last_t": 0, "rate_hz": 0.0}

    # ---- 解析核心：单独抽出来，方便无硬件测试 ----

    def feed(self, data):
        """喂入原始字节，返回这次解析出多少条完整行。线程里和测试里都走这里。"""
        with self._lock:
            self._buf += data
            return self._drain()

    def _drain(self):
        n = 0
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            line = bytes(self._buf[:i])
            del self._buf[:i + 1]
            self._handle_line(line)
            n += 1
        if len(self._buf) > MAX_LINE:
            self._buf.clear()
            self.stats["overflow"] += 1
            self._drop(f"单行超过 {MAX_LINE} 字节，清空重同步")
        return n

    def _handle_line(self, line):
        line = line.strip()
        if not line:
            return
        self.stats["lines"] += 1
        self._recv_times.append(time.time())
        self._tick_rate()
        try:
            raw = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            self.stats["bad_json"] += 1
            self._drop("json parse failed")
            return
        if self.provider is None:
            return
        v = self.provider.push(raw)
        if v is None:
            self.stats["rejected"] += 1
        else:
            self.stats["last_t"] = v.get("t", 0)
            if self.verbose:
                n = v.get("nearest")
                extra = (f"{n['class']} {n['confidence']:.2f} "
                         f"cx={n['cx']:.2f} cy={n['cy']:.2f}") if n else "无障碍"
                print(f"  {extra}")

    def _tick_rate(self):
        now = time.time()
        self._recv_times = [t for t in self._recv_times if now - t <= 2.0]
        self.stats["rate_hz"] = len(self._recv_times) / 2.0

    def rate_settled(self):
        return time.time() - self._t_start > 3.0

    def _drop(self, why):
        if self.verbose:
            print(f"  [!] {why}")
        if self.on_drop:
            self.on_drop(why)

    # ---- 线程 ----

    def start(self):
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            try:
                waiting = getattr(self.ser, "in_waiting", 0)
                # ⚠️ 积压保护：板子忙的时候串口会堆数据，堆久了再一口气解析，
                #    每帧的 t 都会打成"现在"，旧数据装成新鲜的 → 车对着空气躲。
                #    所以积压过多时只保留最后一整行，前面的丢掉。
                if waiting > MAX_BACKLOG:
                    chunk = self.ser.read(waiting)
                    cut = chunk.rfind(b"\n")
                    if cut >= 0:
                        self.stats["stale"] += 1
                        chunk = chunk[cut + 1:]
                    self.feed(chunk)
                    continue
                chunk = self.ser.read(waiting if waiting else 1)
            except Exception:
                return
            if chunk:
                self.feed(chunk)

    def stop(self):
        self._stop.set()
        try:
            self.ser.close()
        except Exception:
            pass


# ============ 假串口：没硬件也能验证协议 ============

class FakeSerial:
    """内存串口，把 write 的字节直接给 read。用来跑 --mode selftest。"""

    def __init__(self):
        self.buf = bytearray()
        self.timeout = 0.05
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.buf)

    def write(self, data):
        self.buf += data
        return len(data)

    def read(self, n=1):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def close(self):
        self.closed = True


# ============ 命令行 ============

def _fake_boxes(i, hz):
    """模拟一个从右往左走过来的人"""
    t = i / hz
    cx_px = 420 - 60 * min(t, 3)
    cy_px = 300 + 30 * min(t, 3)
    w_px = 60 + 25 * min(t, 3)
    h_px = 140 + 60 * min(t, 3)
    box = [cx_px - w_px / 2, cy_px - h_px / 2, cx_px + w_px / 2, cy_px + h_px / 2]
    return [box], [0], [0.85]


def _selftest():
    print("=" * 60)
    print("selftest：内存串口跑通 发→收 全链路")
    print("=" * 60)
    from vision.vision_provider import VisionProvider

    fake = FakeSerial()
    vp = VisionProvider()
    rx = SerialVisionReceiver(provider=vp, ser=fake)
    tx = SerialVisionSender(ser=fake, class_names=["person"],
                            conf_th=0.25, verbose=False)

    ok = 0
    for i in range(20):
        boxes, classes, scores = _fake_boxes(i, 10)
        tx.send(boxes, classes, scores, 640, 480)
        ok += rx.feed(bytes(fake.buf))
        fake.buf.clear()

    s = rx.stats
    print(f"  发出 {tx.sent} 条 / 解析 {s['lines']} 行 / 坏JSON {s['bad_json']} / 拒收 {s['rejected']}")
    v = vp.poll()
    print(f"  最终视觉状态: obstacle={v.get('obstacle')} "
          f"nearest={v.get('nearest', {}).get('class')} "
          f"conf={v.get('nearest', {}).get('confidence')}")

    # 先断言干净链路（要放在断包测试之前，否则计数已经被污染）
    assert tx.sent == 20 and s["lines"] == 20, "20 条应全部解析"
    assert s["bad_json"] == 0, "正常包不该有坏 JSON"
    assert s["rejected"] == 0, "正常包不该被拒收"
    assert v.get("obstacle") is True and v["nearest"]["class"] == "person"

    # 断包测试：串口丢字节时只坏一帧，下一帧必须能恢复
    fake.buf.clear()
    tx.send(*_fake_boxes(0, 10), 640, 480)
    raw = bytes(fake.buf)
    fake.buf.clear()

    n0, bad0, rej0 = rx.stats["lines"], rx.stats["bad_json"], rx.stats["rejected"]
    rx.feed(raw[:10])                       # 只来半截，没有换行
    assert rx.stats["lines"] == n0, "没有换行的半截不该算完整行"
    rx.feed(b"GARBAGE\n")                   # 半截 + 垃圾拼成一行坏 JSON
    assert rx.stats["bad_json"] == bad0 + 1, "坏行没被识别"
    rx.feed(raw)                            # 再来一整条，必须恢复正常
    recovered = (rx.stats["lines"] == n0 + 2
                 and rx.stats["bad_json"] == bad0 + 1
                 and rx.stats["rejected"] == rej0)
    print(f"  断包恢复: {'OK' if recovered else '失败'}"
          f"（行数 {n0}→{rx.stats['lines']}，坏JSON +{rx.stats['bad_json'] - bad0}，"
          f"误拒收 +{rx.stats['rejected'] - rej0}）")
    assert recovered, "断包之后没能重新同步"
    print("\n全部通过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["send", "recv", "selftest"], default="selftest")
    ap.add_argument("--port", default=None, help="send: COM7 / recv: /dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--hz", type=float, default=10.0, help="send 模式的发送频率")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    print(f"vision_serial  contract v{CONTRACT_VERSION}")

    if a.mode == "selftest":
        _selftest()
        return

    if a.mode == "send":
        port = a.port or "COM7"
        s = SerialVisionSender(port, a.baud, class_names=["person", "car", "chair"],
                               verbose=True)
        print(f"  发送端  {port} @ {a.baud}  {a.hz}Hz（假推理，只验链路）\n")
        i = 0
        try:
            while True:
                boxes, classes, scores = _fake_boxes(i, a.hz)
                s.send(boxes, classes, scores, 640, 480)
                i += 1
                time.sleep(1.0 / a.hz)
        except KeyboardInterrupt:
            print(f"\n共发 {s.sent} 条，失败 {s.failed}")
        finally:
            s.close()
        return

    # recv
    port = a.port or "/dev/ttyUSB1"
    from vision.vision_provider import VisionProvider
    import protocol
    vp = VisionProvider(on_drop=lambda w: print(f"  [drop] {w}"))
    rx = SerialVisionReceiver(port, a.baud, vp, verbose=a.verbose).start()
    print(f"  接收端  {port} @ {a.baud}")
    print(f"⚠ 视觉频率必须 ≥ {VISION_HZ_MIN:.0f}Hz"
          f"（vision_timeout_ms={protocol.CFG['vision_timeout_ms']}ms）\n")
    t0 = time.time()
    warned = False
    try:
        while True:
            time.sleep(2.0)
            v = vp.poll()
            s = rx.stats
            age = "从未收到" if not s["last_t"] else f"{protocol.now_ms() - s['last_t']}ms 前"
            n = (v["nearest"]["class"] if v and v.get("nearest") else "无障碍") \
                if v else "无有效帧"
            print(f"[{time.time()-t0:5.0f}s] 行{s['lines']:5d} {s['rate_hz']:4.1f}Hz "
                  f"坏JSON{s['bad_json']:3d} 拒收{s['rejected']:3d} 积压丢{s['stale']:3d} "
                  f"| 最新: {n} ({age})", flush=True)
            if s["lines"] > 3 and s["rate_hz"] < VISION_HZ_MIN and not warned and rx.rate_settled():
                print(f"  ⚠ 频率 {s['rate_hz']:.1f}Hz < {VISION_HZ_MIN:.0f}Hz，"
                      f"决策会频繁判 R5 停车", flush=True)
                warned = True
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()
        print("\n退出")


if __name__ == "__main__":
    main()
