# -*- coding: utf-8 -*-
"""board_main.py —— 板上（Linux / PYNQ）唯一入口：把三块拼成一台能跑的车

为什么需要这个文件
==================
car/run.py 的 live 模式有两个"在板子上不成立"的假设：

  1. Vision JSON 从 **stdin** 进（`--vision-stdin`）。搬到板上没人打字了；
  2. 控制 TCP 是**郑 → 徐**的单向发，Status 靠徐回（这条没问题，但 run.py
     没有断线重连，徐那边重启一次就永远停车）。

board_main.py 把这两个洞补上，其余一律复用郑的代码（零改动）：

    雷达串口 ──┐
               ├─► LiveRadarProvider ─┐
    欧阳 TCP ──┴─► VisionTcpServer ───┴─► VisionProvider ─┐
                                                          ├─► DecisionMachine
    (5000)                                                │      (fusion/machine.py)
                                                          ▼
                                                      10Hz 决策 ──► TcpClient ──► 徐 (5001)
                                                          │
                                                          └──► MonitorBroadcaster ──► PC 云图 (5002)

网络拓扑（三台机器一个交换机，或板子开热点）
--------------------------------------------
    ┌────────────┐   Vision JSON/TCP 5000    ┌──────────────────────────┐
    │ 欧阳 PC     │ ───────────────────────► │  板子 (PYNQ)              │
    │ YOLO        │                           │  board_main.py            │
    └────────────┘                           │   · 雷达 /dev/ttyUSB0     │
                                              │   · 决策 10Hz             │
    ┌────────────┐   Control JSON/TCP 5001   │                          │
    │ 徐 电机板   │ ◄───────────────────────► │                          │
    │            │   Status JSON (回显 seq)   │                          │
    └────────────┘                           └───────────┬──────────────┘
                                                          │ Monitor JSON/TCP 5002
    ┌────────────┐                                        │  (只读，不参与决策)
    │ 郑 笔记本    │ ◄──────────────────────────────────────┘
    │ radar_map_view.py --tcp <板子IP>:5002                │
    └────────────┘

端口约定（⚠️ 需与徐/欧阳确认后冻结）
------------------------------------
    5000  视觉进（板上监听，欧阳连）      —— 欧阳侧只需要 socket.sendall(json+"\\n")
    5001  控制出（板上主动连徐，徐监听）  —— 徐侧 listen + 回 Status
    5002  监控出（板上监听，郑笔记本连）  —— 只读广播，车停了也不影响决策

用法
----
    # 板上正常跑（雷达 /dev/ttyUSB0，徐在 192.168.2.50）
    python3 board_main.py --motor 192.168.2.50:5001

    # 郑在笔记本上先单独验证（不接板子、不接雷达）
    python board_main.py --radar fake --motor 127.0.0.1:5001 --secs 15

    # 只看视觉链路通不通（雷达关掉 → 决策恒 R5 停，属预期）
    python board_main.py --radar off --secs 8

    # 自检（不需要任何硬件/端口）
    python board_main.py --selftest

⚠️ Python 3.6 兼容：PYNQ 2.7 底子是 Ubuntu 18.04 + Python 3.6.9，
   本文件不用 dataclass / 海象运算符 / 3.7+ 语法。
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAR = os.path.join(os.path.dirname(_HERE), "car")
for _p in (_HERE, _CAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import protocol                                    # noqa: E402  car/protocol.py
from fusion.machine import DecisionMachine         # noqa: E402
from radar import d6_protocol                      # noqa: E402
from comm.tcp_client import TcpClient              # noqa: E402
from vision.vision_provider import VisionProvider  # noqa: E402
from vision_server import VisionTcpServer          # noqa: E402

CYCLE_MS = 100          # 决策循环 10Hz，与 run.py 一致
DEFAULT_RADAR_PORT = "/dev/ttyUSB0" if os.name == "posix" else d6_protocol.PORT


def now_ms():
    return int(time.time() * 1000)


# ================================================================ 雷达
class BoardRadarProvider(object):
    """对 LiveRadarProvider 的一层薄包装：

    · 多一个 scan_snapshot()，让监控广播能拿到原始点算 12 扇区
      （只读快照，用父类自己的锁，不碰郑的 radar_provider.py）
    · dtr/rts 拉低失败不致命（某些 USB-串口驱动在 Linux 上会抛 IOError）
    """

    def __init__(self, port, baud, verbose=True):
        from radar.radar_provider import LiveRadarProvider
        self._inner = LiveRadarProvider(port, baud)
        if verbose:
            print("[radar] 串口 %s @ %d 已打开" % (port, baud))

    def poll(self):
        return self._inner.poll()

    def scan_snapshot(self):
        try:
            with self._inner._lock:                 # noqa: SLF001 只读快照
                return list(self._inner._scan)
        except Exception:
            return []

    def close(self):
        self._inner.close()


class FakeRadar(object):
    """不接雷达时的假数据源：固定距离，用来验证决策/链路而不依赖硬件。"""

    def __init__(self, front=1.50, left=1.00, right=1.00):
        self.front, self.left, self.right = front, left, right
        self._pts = self._synth()

    def _synth(self):
        """合成一圈点：让 12 扇区图和真雷达长得像（0° 前方、90° 左侧、270° 右侧）。"""
        pts = []
        if self.front is not None:
            for a in range(350, 360):
                pts.append((float(a), int(self.front * 1000), 80, False))
            for a in range(0, 11):
                pts.append((float(a), int(self.front * 1000), 80, False))
        if self.left is not None:
            for a in range(40, 81, 2):
                pts.append((float(a), int(self.left * 1000), 80, False))
        if self.right is not None:
            for a in range(280, 321, 2):
                pts.append((float(a), int(self.right * 1000), 80, False))
        return pts

    def poll(self):
        return protocol.make_radar_json(self.front, self.left, self.right,
                                        valid=True, t=now_ms())

    def scan_snapshot(self):
        return self._pts

    def close(self):
        pass


class OffRadar(object):
    """雷达关闭：poll() 恒 None → 决策侧判超时 → R5 停车（这是正确行为）。"""

    def poll(self):
        return None

    def scan_snapshot(self):
        return []

    def close(self):
        pass


def _sector12(buf):
    """12 个 30° 扇区的稳健最小距离（米）。s_k = [k*30-15, k*30+15)，s0 跨 0°。

    与 PC 端 radar_map_view.py 的 _sector12 同一套公式，口径必须一致：
    front = min(s11, s0, s1) 正好等于车上 SECTOR_FRONT=(345,15)。
    注意 _robust_min 收 mm、返回 m。
    """
    out = []
    for k in range(12):
        c = k * 30.0
        lo = (c - 15.0) % 360.0
        hi = (c + 15.0) % 360.0
        vals = []
        for p in buf:
            d = p[1]
            if d is None:
                continue
            a = p[0] % 360.0
            inside = (lo <= a < hi) if lo < hi else (a >= lo or a < hi)
            if inside:
                vals.append(d)
        out.append([d6_protocol._robust_min(vals) if vals else None, len(vals)])
    return out


# ================================================================ 控制链路
class MotorLink(object):
    """TcpClient 的断线重连外壳。

    为什么必须重连：TcpClient 一旦 link_lost 就 return 掉接收线程，永不恢复。
    联调时徐那边按一次 Ctrl-C 重启，郑这边就永远 STOP 且不报错 —— 最难查的
    "看着在跑其实早死了"。这里每 1s 重试一次，重连成功打一行日志。
    """

    def __init__(self, spec, timeout_ms=500, verbose=True):
        host, _, port = spec.rpartition(":")
        if not host:
            host, port = spec, "5001"
        self.host, self.port = host, int(port)
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self.cli = None
        self.connected_at = 0
        self._next_try = 0
        self.reconnects = 0
        self.ever_connected = False

    def _drop(self):
        if self.cli:
            try:
                self.cli.close()
            except Exception:
                pass
        self.cli = None

    def ensure(self):
        """返回 True = 当前有可用链路。断线时最多每 1s 尝试一次。"""
        if self.cli is not None and not self.cli.link_lost:
            return True
        t = now_ms()
        if t < self._next_try:
            return False
        if self.cli is not None:                    # 活着但被判定丢链 → 主动重建
            self._drop()
            self.reconnects += 1
        try:
            cli = TcpClient(self.host, self.port, self.timeout_ms)
            cli.connect()
        except Exception as e:
            if self.ever_connected or self.verbose:
                print("[motor] 连不上 %s:%d（%s），1s 后重试"
                      % (self.host, self.port, e))
            self._next_try = t + 1000
            return False
        self.cli = cli
        self.connected_at = t
        self.ever_connected = True
        if self.verbose:
            extra = "（第 %d 次重连）" % self.reconnects if self.reconnects else ""
            print("[motor] 已连上 %s:%d%s" % (self.host, self.port, extra))
        return True

    def fault(self, now=None):
        """True = 该停车。三种情况都算故障：
        ① 压根连不上（板子/网线/徐没起服务）
        ② 连上了但从没回过 Status 且已超时
        ③ 回过 Status 但已经超时
        """
        if self.cli is None:
            return True
        now = now_ms() if now is None else now
        if self.cli.link_lost:
            return True
        if not self.cli.last_status_t:
            return (now - self.connected_at) > self.timeout_ms
        return self.cli.check_watchdog(now)

    def send(self, ctrl):
        if self.cli is None:
            return False
        return self.cli.send_control(ctrl)

    def status(self):
        return self.cli.last_status if self.cli else None

    def close(self):
        self._drop()


# ================================================================ 监控广播
class MonitorBroadcaster(object):
    """只读 TCP 广播：郑的笔记本连上来看实时点云/扇区/决策。

    设计取舍：**只发不收**、**发不出去就丢**。
    监控端是"看"的，绝不能因为它卡住而拖慢 10Hz 决策循环 —— 所以 publish()
    是非阻塞 send（socket 设成非阻塞，塞不进去直接丢弃该客户端）。
    """

    def __init__(self, port, host="0.0.0.0", verbose=True):
        self.port, self.host, self.verbose = port, host, verbose
        self._clients = []
        self._lock = threading.Lock()
        self._srv = None
        self._running = False
        self.sent = 0

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(4)
        self._srv.settimeout(0.5)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        if self.verbose:
            print("[monitor] 监听 %s:%d（PC 端 radar_map_view.py --tcp 连这里）"
                  % (self.host, self.port))
        return self

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setblocking(False)
            with self._lock:
                self._clients.append((conn, addr))
            if self.verbose:
                print("[monitor] 客户端接入 %s:%d" % (addr[0], addr[1]))

    def publish(self, obj):
        if not self._running:
            return
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        dead = []
        with self._lock:
            for conn, addr in self._clients:
                try:
                    conn.sendall(line)
                except (BlockingIOError, InterruptedError):
                    pass                            # 对端读得慢 → 本帧丢弃
                except Exception:
                    dead.append((conn, addr))
            for conn, addr in dead:
                try:
                    conn.close()
                except Exception:
                    pass
                self._clients.remove((conn, addr))
                if self.verbose:
                    print("[monitor] 客户端断开 %s:%d" % (addr[0], addr[1]))
        if self._clients:
            self.sent += 1

    def stop(self):
        self._running = False
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        with self._lock:
            for conn, _addr in self._clients:
                try:
                    conn.close()
                except Exception:
                    pass
            self._clients = []


# ================================================================ 日志
def open_log(path=None):
    d = os.path.join(_HERE, "logs")
    os.makedirs(d, exist_ok=True)
    if not path:
        path = os.path.join(d, time.strftime("board_%Y%m%d_%H%M%S.jsonl"))
    return open(path, "a", encoding="utf-8")


def _load_contract():
    """加载 integration/contract.py（三人接口契约，唯一真源）。

    ⚠️ 刻意**不在 joint/ 下留副本** —— steering→差速 的映射只能有一处定义，
    留两份必然漂移，到时候查不出是哪个版本算错了。
    板上就把这个文件拷到 integration/contract.py（只拷这一个，别拷整个 integration）。
    """
    import importlib.util
    cands = [os.path.join(os.path.dirname(_HERE), "integration", "contract.py"),
             os.path.join(_HERE, "contract.py")]
    for p in cands:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("contract", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _with_diff(ctrl, contract):
    """给 Control 补上差速字段（pwm_l/pwm_r/in1..in4）。contract 为 None 时原样返回。"""
    if contract is None:
        return ctrl
    try:
        d = contract.control_to_diff(ctrl)
        for k in ("pwm_l", "pwm_r", "in1", "in2", "in3", "in4"):
            ctrl[k] = d[k]
    except Exception as e:
        print("[WARN] 差速映射失败：%s" % e)
    return ctrl


def _list_serial_hint():
    """串口打不开时，把「系统里到底有哪些口」直接打出来 —— 省掉用户自己查的一步。"""
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception:
        ports = []
    if ports:
        print("       当前系统里的串口：")
        for p in ports:
            print("         %-16s %s" % (p.device, p.description))
        print("       用 --radar-port 指定其中一个")
    else:
        print("       系统里一个串口都没有 → 雷达没插好 / USB 线是充电线 / 驱动没装")
    if os.name == "posix":
        print("       板子上若已有 /dev/ttyUSB0 但报权限错："
              "sudo usermod -aG dialout $USER 然后重新登录")
        print("       设备名不确定：ls -l /dev/ttyUSB* /dev/ttyACM*")


# ================================================================ 主循环
def run(args):
    # ---- 雷达 ----
    radar_p = None
    if args.radar == "live":
        try:
            radar_p = BoardRadarProvider(args.radar_port, args.radar_baud,
                                         verbose=not args.quiet)
        except Exception as e:
            print("[FAIL] 雷达串口 %s 打不开：%s" % (args.radar_port, e))
            _list_serial_hint()
            return 2
    elif args.radar == "fake":
        radar_p = FakeRadar(args.fake_front, args.fake_left, args.fake_right)
        print("[radar] 假数据 front=%.2f left=%.2f right=%.2f"
              % (args.fake_front, args.fake_left, args.fake_right))
    else:
        radar_p = OffRadar()
        print("[radar] 已关闭（决策会因雷达超时走 R5 停车，这是预期行为）")

    # ---- 视觉 ----
    drops = []

    def on_drop(why):
        drops.append(why)
        if not args.quiet:
            print("[vision] 丢弃一帧：%s" % why)

    vision_p = VisionProvider(on_drop=on_drop)
    vs = None
    if args.vision_port > 0:
        try:
            vs = VisionTcpServer(args.vision_port, on_line=vision_p.push,
                                 verbose=not args.quiet).start()
        except OSError as e:
            print("[FAIL] 视觉端口 %d 绑不上：%s" % (args.vision_port, e))
            print("       是不是已经有一个 board_main.py 在跑？")
            return 2
    else:
        print("[vision] 未开监听（--vision-port 0），视觉侧将恒为无数据")

    # ---- 控制链路 ----
    link = MotorLink(args.motor, args.watchdog_ms,
                     verbose=not args.quiet) if args.motor else None
    if link:
        link.ensure()                    # 首次尝试；连不上也不退出，靠重连+watchdog
    else:
        print("[motor] 未指定 --motor，控制只写日志不外发")

    # ---- 监控广播 ----
    mon = None
    if args.monitor_port > 0:
        try:
            mon = MonitorBroadcaster(args.monitor_port,
                                     verbose=not args.quiet).start()
        except OSError as e:
            print("[WARN] 监控端口 %d 绑不上：%s（继续跑，不影响决策）"
                  % (args.monitor_port, e))
            mon = None

    # ---- 决策 ----
    machine = DecisionMachine()
    contract = None
    if args.diff:
        contract = _load_contract()
        if contract is None:
            print("[WARN] 找不到 integration/contract.py → 只发 action/steering，"
                  "不发差速 pwm（徐要自己映射）")
        else:
            print("[motor] 差速映射已启用（contract v%s）：Control 里会带 "
                  "pwm_l/pwm_r/in1..in4" % getattr(contract, "CONTRACT_VERSION", "?"))
    log = open_log(args.log)
    if args.log:
        print("[log] %s" % os.path.abspath(args.log))

    t0 = now_ms()
    seq = 0
    last_rule = None
    last_print = 0
    print("[board] 决策循环 %dHz 启动，Ctrl-C 退出" % (1000 // CYCLE_MS))

    try:
        while True:
            if args.secs and (now_ms() - t0) / 1000.0 >= args.secs:
                print("[board] 到 --secs %.1fs，正常退出" % args.secs)
                break

            now = now_ms()
            if link:
                link.ensure()                       # 断线重连（内部限流 1Hz）
            radar = radar_p.poll()
            vision = vision_p.poll()
            fault = link.fault(now) if link else False
            ctrl, rule, why = machine.step(radar, vision, fault, now)

            # 把 steering 翻成差速 PWM 一起发出去：映射规则只在郑这边定义一次，
            # 徐拿到 pwm_l/pwm_r 直接写寄存器，不用再理解"90 是中位"。
            _with_diff(ctrl, contract)

            if link:
                link.send(ctrl)

            seq += 1
            rec = {"t": now, "seq": seq, "rule": rule, "why": why,
                   "control": ctrl, "fault": bool(fault)}
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()

            if mon:
                snap = radar_p.scan_snapshot()
                mon.publish({
                    "version": 1, "t": now, "seq": seq,
                    "rule": rule, "why": why,
                    "action": ctrl["action"], "speed": ctrl["speed"],
                    "steering": ctrl["steering"],
                    "radar": radar,
                    "sectors": _sector12(snap),
                    "pts": _pts_compact(snap) if args.monitor_pts else None,
                    "vision": _vision_brief(vision),
                    "fault": bool(fault),
                    "status": link.status() if link else None,
                    "vision_age_ms": vs.age_ms() if vs else None,
                    "drops": len(drops),
                })

            if rule != last_rule or (now - last_print) > 1000:
                last_rule = rule
                last_print = now
                extra = ""
                if link and link.reconnects:
                    extra = "  [重连%d]" % link.reconnects
                print("  %-3s %-8s %s%s" % (rule, ctrl["action"], why, extra))

            time.sleep(CYCLE_MS / 1000.0)
    except KeyboardInterrupt:
        print("\n[board] Ctrl-C，正在退出…")
    finally:
        # 退出前必发一条 STOP：否则徐那边保持最后一个速度继续跑
        if link:
            try:
                link.send(_with_diff(protocol.make_control("STOP"), contract))
                time.sleep(0.05)
            except Exception:
                pass
            link.close()
        radar_p.close()
        if vs:
            vs.stop()
        if mon:
            mon.stop()
        log.close()
        print("[board] 已退出（共 %d 个决策周期）" % seq)
    return 0


def _pts_compact(buf):
    """点云压成 [[角度×10(int), 距离mm], ...]，给 PC 端画图用。

    为什么乘 10 存整数：JSON 里 "3590" 比 "359.0" 省 2 字节，400 点省 800 字节，
    10Hz 下约省 8KB/s；而且 PC 端直接 /10 还原，无浮点误差争议。
    只发有回波的点（None 的没有画图意义）。
    """
    out = []
    for p in buf:
        d = p[1]
        if d is not None:
            out.append([int(p[0] * 10.0 + 0.5) % 3600, d])
    return out


def _vision_brief(v):
    if not v:
        return None
    out = {"obstacle": v.get("obstacle")}
    n = v.get("nearest")
    if n:
        out.update({"class": n.get("class"), "cx": n.get("cx"),
                    "cy": n.get("cy"), "conf": n.get("confidence"),
                    "side": protocol.vision_side(v)})
    return out


# ================================================================ 自检
def _selftest():
    """不需要硬件：起一个假徐（回 Status）+ 一个假欧阳，跑 2 秒看规则是否合理。"""
    import socket as _s
    ok = True

    # --- ① 12 扇区公式 ---
    buf = []
    for a in range(350, 360):
        buf.append((float(a), 1500, 80, False))
    for a in range(0, 11):
        buf.append((float(a), 1500, 80, False))
    s = _sector12(buf)

    def _f(v):
        return "None" if v is None else "%.2f" % v

    print("[1] s0=%s s1=%s s11=%s" % (_f(s[0][0]), _f(s[1][0]), _f(s[11][0])))
    # 350°~10° 全落在 s0=[345,15) 里；s1=[15,45) 和 s11=[315,345) 应当没点
    if not (s[0][0] is not None and abs(s[0][0] - 1.5) < 0.01):
        print("    [FAIL] s0 应为 1.5m")
        ok = False
    if s[1][0] is not None or s[11][0] is not None:
        print("    [FAIL] s1/s11 不该有点")
        ok = False

    # --- ② MotorLink 断线 → fault ---
    ml = MotorLink("127.0.0.1:1", 300, verbose=False)
    ml.ensure()
    if not ml.fault():
        print("    [FAIL] 连不上时 fault 应为 True")
        ok = False
    else:
        print("[2] 连不上 → fault=True  OK")

    # --- ③ 假徐 + 假欧阳 全链路 2 秒 ---
    srv = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    srv.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 5101))
    srv.listen(1)
    srv.settimeout(3)
    got = []

    def fake_motor():
        try:
            c, _ = srv.accept()
        except Exception:
            return
        buf2 = b""
        c.settimeout(0.5)
        while True:
            try:
                d = c.recv(4096)
            except Exception:
                break
            if not d:
                break
            buf2 += d
            while b"\n" in buf2:
                line, buf2 = buf2.split(b"\n", 1)
                if not line.strip():
                    continue
                j = json.loads(line.decode())
                got.append(j["action"])
                try:
                    c.sendall((json.dumps({"version": 1, "seq": j["seq"],
                                           "timestamp": now_ms(), "ok": 1,
                                           "vbat": 12.0, "enc_l": 0,
                                           "enc_r": 0}) + "\n").encode())
                except Exception:
                    return
        c.close()

    threading.Thread(target=fake_motor, daemon=True).start()

    args = argparse.Namespace(
        radar="fake", radar_port=None, radar_baud=None, quiet=True,
        fake_front=0.40, fake_left=1.00, fake_right=1.00,
        vision_port=5100, motor="127.0.0.1:5101", monitor_port=0,
        monitor_pts=0, diff=1,
        watchdog_ms=500, log=os.path.join(_HERE, "logs", "_selftest.jsonl"),
        secs=2.0)
    t = threading.Thread(target=run, args=(args,), daemon=True)
    t.start()
    time.sleep(0.6)

    # 假欧阳：一直报"正前方有人"，与雷达危险一致 → 应触发 R1 STOP
    try:
        vc = _s.create_connection(("127.0.0.1", 5100), timeout=2)
        for _ in range(15):
            vc.sendall((json.dumps({"version": 1, "timestamp": now_ms(),
                                    "obstacle": True,
                                    "nearest": {"class": "person",
                                                "confidence": 0.9, "cx": 0.5,
                                                "cy": 0.6, "width": 0.2,
                                                "height": 0.6}}) + "\n").encode())
            time.sleep(0.1)
        vc.close()
    except Exception as e:
        print("    [FAIL] 假欧阳连不上：%s" % e)
        ok = False

    t.join(timeout=6)
    try:
        srv.close()
    except Exception:
        pass

    print("[3] 徐收到 %d 条控制，动作序列=%s" % (len(got), got[:4]))
    if len(got) < 5:
        print("    [FAIL] 控制链路没通")
        ok = False
    elif "STOP" not in got:
        print("    [FAIL] 前向 0.40m + 视觉有人 应触发 STOP（R1）")
        ok = False
    else:
        print("    STOP 已出现  OK")

    print("[PASS] board_main 全链路自检通过" if ok else "[FAIL] 见上")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="板上入口：雷达 + 视觉TCP + 决策 + 控制TCP + 监控广播")
    ap.add_argument("--radar", choices=["live", "fake", "off"], default="live",
                    help="live=真串口 fake=假数据 off=关闭")
    ap.add_argument("--radar-port", default=DEFAULT_RADAR_PORT,
                    help="板上通常是 /dev/ttyUSB0，Windows 上是 COM5")
    ap.add_argument("--radar-baud", type=int, default=d6_protocol.BAUD)
    ap.add_argument("--fake-front", type=float, default=1.50)
    ap.add_argument("--fake-left", type=float, default=1.00)
    ap.add_argument("--fake-right", type=float, default=1.00)
    ap.add_argument("--vision-port", type=int, default=5000,
                    help="0=不开监听")
    ap.add_argument("--motor", default=None,
                    help="徐的 HOST:PORT，如 192.168.2.50:5001；不填则只写日志")
    ap.add_argument("--watchdog-ms", type=int, default=500)
    ap.add_argument("--diff", type=int, default=1, choices=[0, 1],
                    help="Control 里附带差速 pwm_l/pwm_r（用 integration/contract.py），"
                         "0=只发 action/steering")
    ap.add_argument("--monitor-port", type=int, default=5002,
                    help="给 PC 端云图的只读广播端口，0=关闭")
    ap.add_argument("--monitor-pts", type=int, default=1, choices=[0, 1],
                    help="监控里是否带原始点云（约 +70KB/s），0=只发扇区")
    ap.add_argument("--log", default=None)
    ap.add_argument("--secs", type=float, default=0, help="N 秒后自动退出（0=一直跑）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    # 输出重定向到文件时 stdout 是块缓冲，被 kill 会丢日志 → 强制行缓冲
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:            # Python 3.6 没有 reconfigure
        pass

    if a.selftest:
        return _selftest()
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
