# -*- coding: utf-8 -*-
"""
comm/tcp_client.py — 发 Control JSON 给 PYNQ 的 TCP 客户端 + watchdog
- 独立线程收 Status JSON；主循环只管发
- watchdog：超过 timeout_ms 没收到任何 Status → link_lost=True，主循环据此走 R5 停车
- seq 单调递增（0~65535 循环），与徐约好回显同一 seq
"""
import json
import socket
import threading
import time


class TcpClient:
    def __init__(self, host, port, timeout_ms=500, on_status=None, on_link_lost=None):
        self.host, self.port = host, port
        self.timeout_ms = timeout_ms
        self.on_status = on_status
        self.on_link_lost = on_link_lost
        self.sock = None
        self.link_lost = False
        self.last_status_t = 0
        self.last_status = None
        self._rx = threading.Thread(target=self._rx_loop, daemon=True)
        self._running = False

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
        self.sock.settimeout(0.1)
        self._running = True
        self._rx.start()
        return True

    def _rx_loop(self):
        buf = b""
        while self._running:
            try:
                data = self.sock.recv(1024)
                if not data:
                    raise ConnectionError("peer closed")
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        st = json.loads(line.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    self.last_status = st
                    self.last_status_t = int(time.time() * 1000)
                    self.link_lost = False
                    if self.on_status:
                        self.on_status(st)
            except socket.timeout:
                pass
            except Exception:
                self.link_lost = True
                if self.on_link_lost:
                    self.on_link_lost()
                return

    def send_control(self, ctrl):
        if self.sock is None:
            return False
        try:
            self.sock.sendall((json.dumps(ctrl, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except Exception:
            self.link_lost = True
            return False

    def check_watchdog(self, now_ms=None):
        """true = 需要停车（超时未收到 Status）"""
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if self._running and self.link_lost:
            return True
        if self._running and self.last_status_t and now - self.last_status_t > self.timeout_ms:
            return True
        return False

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except Exception:
            pass
