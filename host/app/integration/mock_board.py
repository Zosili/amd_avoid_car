# -*- coding: utf-8 -*-
"""
mock_board.py — 假板子（TCP 服务端），在没有 PYNQ 的情况下验证下行链路
=====================================================================
作用：郑的 `run.py --pynq 127.0.0.1:8000` 连上来后，本脚本假装自己是 PYNQ：
  1. 收 Control JSON（每行一条）
  2. 用 contract.control_to_diff 转成差速 PWM
  3. 打印出来（真车上这一步是写 AXI 寄存器）
  4. 每 100ms 回一条 Status JSON —— 不回郑的 watchdog 会判掉线、走 R5 停车

这一层验证的是：**郑的 control → 徐的电机指令** 这段接缝。
真板子上，把 `_apply()` 里的打印换成 mmio.write 就是最终实现。

跑法：
    终端A:  python mock_board.py --port 8000
    终端B:  cd ../car && python run.py --source sim --pynq 127.0.0.1:8000
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
from contract import control_to_diff, CONTRACT_VERSION   # noqa: E402

_lock = threading.Lock()
_state = {"seq": 0, "pwm_l": 0, "pwm_r": 0, "count": 0, "last_ctrl": None}


def _apply(ctrl):
    """真板子上这里换成：
        mmio.write(0x04, pwm_l & 0xFFFF)
        mmio.write(0x08, pwm_r & 0xFFFF)
    """
    m = control_to_diff(ctrl)
    with _lock:
        _state.update(seq=ctrl.get("seq", 0), pwm_l=m["pwm_l"], pwm_r=m["pwm_r"],
                      count=_state["count"] + 1, last_ctrl=ctrl)
    print(f"[{_state['count']:4d}] seq={ctrl.get('seq', 0):5d} "
          f"{ctrl.get('action', '?'):8s} speed={ctrl.get('speed', 0):3d} "
          f"steer={ctrl.get('steering', 90):3d} → "
          f"pwm_l={m['pwm_l']:5d} pwm_r={m['pwm_r']:5d} "
          f"in=({m['in1']}{m['in2']}|{m['in3']}{m['in4']})", flush=True)
    return m


def _status_json():
    with _lock:
        return json.dumps({"version": 1, "seq": _state["seq"],
                           "pwm_l": _state["pwm_l"], "pwm_r": _state["pwm_r"],
                           "t": int(time.time() * 1000)}, ensure_ascii=False) + "\n"


def _handle(conn, addr, status_hz):
    print(f"[+] 客户端接入 {addr[0]}:{addr[1]}", flush=True)
    buf = b""
    stop = threading.Event()

    def _heartbeat():
        period = 1.0 / status_hz
        while not stop.is_set():
            try:
                conn.sendall(_status_json().encode("utf-8"))
            except Exception:
                stop.set()
                return
            time.sleep(period)

    threading.Thread(target=_heartbeat, daemon=True).start()

    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    ctrl = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    print(f"[!] 非法 JSON: {line[:80]!r}", flush=True)
                    continue
                _apply(ctrl)
    except Exception as e:
        print(f"[!] 连接异常: {e}", flush=True)
    finally:
        stop.set()
        conn.close()
        print(f"[-] 断开 {addr[0]}:{addr[1]}，共收 {_state['count']} 条指令", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--status-hz", type=float, default=10.0, help="回 Status 的频率")
    a = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.host, a.port))
    srv.listen(1)
    print(f"mock_board 已启动  contract v{CONTRACT_VERSION}  "
          f"监听 {a.host}:{a.port}  Status {a.status_hz}Hz", flush=True)
    print("等郑的 run.py --pynq 连上来...\n", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            _handle(conn, addr, a.status_hz)
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
