# -*- coding: utf-8 -*-
"""mock_motor.py —— 扮演徐的电机侧（TCP 服务端），用于郑这边单独联调

它做什么
--------
1. 监听 TCP（默认 5001），接受郑的决策程序连入；
2. 逐行读 Control JSON，打印 action/speed/steering/seq；
3. **回一条 Status JSON，seq 原样回显** —— 郑的 watchdog 靠这个判断链路活着；
4. `--drop-after N` 之后不再回 Status（模拟板子/网线断），用来验证 R5 停车。

与徐约定的两个 JSON（冻结，别各自改）
-------------------------------------
Control（郑 → 徐）：
    {"version":1,"seq":0~65535,"timestamp":ms,"action":"FORWARD|SLOW|LEFT|RIGHT|STOP",
     "speed":0~100,"steering":0~180}       # steering 90 = 直行
Status（徐 → 郑，本项目最小集）：
    {"version":1,"seq":<回显>,"timestamp":ms,"ok":1,"vbat":12.1,
     "enc_l":<int>,"enc_r":<int>}
    # 郑只用两件事：有没有收到（watchdog）、seq 对不对（丢包/错序排查）

用法
----
    python mock_motor.py                       # 监听 0.0.0.0:5001
    python mock_motor.py --drop-after 30       # 第 30 帧后不再回 Status（测 R5）
    python mock_motor.py --log motor.csv       # 同时把收到的控制存 CSV
"""
import argparse
import csv
import json
import os
import socket
import sys
import threading
import time


def now_ms():
    return int(time.time() * 1000)


def load_contract():
    """integration/contract.py —— steering→差速 的唯一真源。找不到就降级。"""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "contract.py"),
              os.path.join(os.path.dirname(here), "integration", "contract.py")):
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("contract", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


class MotorStub:
    def __init__(self, port=5001, host="0.0.0.0", drop_after=0, quiet=False,
                 log_path=None, every=1, no_diff=False):
        self.port, self.host = port, host
        self.drop_after = drop_after
        self.quiet = quiet
        self.every = max(0, every)
        self.n = 0
        self.conn = None
        self.running = False
        self._csv = None
        self._csv_fh = None
        self.contract = None if no_diff else load_contract()
        if log_path:
            self._csv_fh = open(log_path, "w", newline="", encoding="utf-8-sig")
            self._csv = csv.writer(self._csv_fh)
            self._csv.writerow(["t_ms", "seq", "action", "speed", "steering"])
            self._csv_fh.flush()

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(2)
        srv.settimeout(0.5)
        self.running = True
        print(f"[motor] 监听 {self.host}:{self.port}，等决策程序连入…")
        try:
            while self.running:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                print(f"[motor] 接入 {addr[0]}:{addr[1]}")
                self.conn = conn
                threading.Thread(target=self._serve, args=(conn,),
                                 daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            try:
                srv.close()
            except Exception:
                pass
            print(f"[motor] 退出，共收到 {self.n} 条控制")

    def _serve(self, conn):
        buf = b""
        conn.settimeout(0.5)
        try:
            while self.running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    self._on_ctrl(conn, line)
        except Exception as e:
            print(f"[motor] 连接异常: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            print("[motor] 连接关闭")

    def _on_ctrl(self, conn, line):
        try:
            ctrl = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            print(f"[motor] 畸形控制帧: {line[:60]!r}")
            return
        self.n += 1
        seq = ctrl.get("seq")
        act = ctrl.get("action")
        spd = ctrl.get("speed")
        st = ctrl.get("steering")
        # --every N：每 N 条打一行，免得 10Hz 把决策日志刷没（0 = 不打）
        show = (not self.quiet) and self.every > 0 and (
            self.every == 1 or self.n % self.every == 0)
        if show:
            extra = ""
            if "pwm_l" in ctrl:
                # 郑已经把差速算好了 → 直接用；顺便验一下和本地映射是否一致
                extra = "  L=%-5s R=%-5s" % (ctrl.get("pwm_l"), ctrl.get("pwm_r"))
            elif self.contract is not None:
                d = self.contract.control_to_diff(ctrl)
                extra = "  L=%-5d R=%-5d" % (d["pwm_l"], d["pwm_r"])
            print(f"[motor] #{self.n:<4} seq={seq:<6} {str(act):8s} "
                  f"speed={spd:<4} steer={st:<4}{extra}", flush=True)
        if self._csv:
            self._csv.writerow([now_ms(), seq, act, spd, st])
            self._csv_fh.flush()          # 被 Ctrl-C / kill 时不丢最后几行

        # 故障注入：到点后不再回 Status → 郑的 watchdog 应当判链路丢失并停车
        if self.drop_after and self.n >= self.drop_after:
            if self.n == self.drop_after:
                print(f"[motor] ⚠ 已到 {self.drop_after} 帧，"
                      f"开始不回 Status（模拟链路断）")
            return
        st_msg = {"version": 1, "seq": seq, "timestamp": now_ms(), "ok": 1,
                  "vbat": 12.1, "enc_l": self.n * 7, "enc_r": self.n * 7}
        try:
            conn.sendall((json.dumps(st_msg) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"[motor] 回 Status 失败: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--drop-after", type=int, default=0,
                    help="收到 N 条控制后停止回 Status（测 watchdog）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--every", type=int, default=1,
                    help="每 N 条控制打一行（10=不刷屏，0=静默）")
    ap.add_argument("--log", default=None)
    ap.add_argument("--no-diff", action="store_true",
                    help="不加载 contract.py（只看 action/steering）")
    a = ap.parse_args()
    # 输出重定向到文件时 stdout 是块缓冲，被 kill 会丢日志 → 强制行缓冲
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:            # Python 3.6 没有 reconfigure
        pass
    try:
        MotorStub(a.port, a.host, a.drop_after, a.quiet, a.log, a.every,
                  a.no_diff).start()
    except OSError as e:
        print(f"[FAIL] 端口 {a.port} 绑不上：{e}（是不是已经有实例在跑？）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
