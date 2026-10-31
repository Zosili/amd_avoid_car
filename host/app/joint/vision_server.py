# -*- coding: utf-8 -*-
"""vision_server.py —— 视觉输入的 TCP 接收端（上板联调缺的那一块）

为什么需要它
------------
car/run.py 的 live 模式目前从 **stdin** 收 Vision JSON（`--vision-stdin`）。
一旦决策程序搬到板上，没人往 stdin 打字了，欧阳的视觉必须**走网络**进来。
这个类把「网络上的 Vision JSON 行」变成 `VisionProvider.push(line)` 的调用，
和原来的 stdin 路径等价，下游一行不用改。

协议（与欧阳约定，冻结）
------------------------
    传输：TCP，**每行一个 JSON**（\n 分隔，UTF-8）
    默认端口：5000（板子上监听 0.0.0.0，PC 侧连 板子IP:5000）
    内容：{"version":1,"timestamp":<ms int>,"obstacle":<bool>,
           "nearest":{"class":str,"confidence":0~1,"cx":0~1,"cy":0~1,
                      "width":0~1,"height":0~1}}
    注意：郑这边 **不信任发送方 timestamp**（VisionProvider.push 会用本机到达时间
    重新打戳），所以两边时钟不同步也不会误判过期 —— 但发送方仍要填，便于测延迟。

用法（作为模块）
----------------
    from vision_server import VisionTcpServer
    vs = VisionTcpServer(5000, on_line=lambda raw: vision_p.push(raw))
    vs.start()          # 后台线程
    ...
    vs.stop()

用法（自检）
------------
    python vision_server.py --selftest
"""
import argparse
import socket
import sys
import threading
import time


class VisionTcpServer:
    """监听 TCP，按行切分，把每行原文交给 on_line（bytes）。断线自动等待重连。"""

    def __init__(self, port, on_line, host="0.0.0.0", verbose=True):
        self.port = port
        self.host = host
        self.on_line = on_line
        self.verbose = verbose
        self.frames = 0
        self.bad_lines = 0
        self.clients = 0
        self.last_t = 0
        self._srv = None
        self._running = False
        self._threads = []

    # ---------------- 生命周期 ----------------
    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(4)
        self._srv.settimeout(0.5)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)
        if self.verbose:
            print(f"[vision] 监听 {self.host}:{self.port}（等欧阳连入）")
        return self

    def stop(self):
        self._running = False
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        if self.verbose:
            print(f"[vision] 关闭（收到 {self.frames} 帧，坏行 {self.bad_lines}）")

    def age_ms(self):
        return 0 if not self.last_t else int(time.time() * 1000) - self.last_t

    # ---------------- 内部 ----------------
    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.clients += 1
            if self.verbose:
                print(f"[vision] 客户端接入 {addr[0]}:{addr[1]}")
            t = threading.Thread(target=self._conn_loop, args=(conn, addr),
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def _conn_loop(self, conn, addr):
        buf = b""
        conn.settimeout(0.5)
        try:
            while self._running:
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
                    self.frames += 1
                    self.last_t = int(time.time() * 1000)
                    try:
                        self.on_line(line)
                    except Exception as e:            # 绝不让视觉端把主循环搞崩
                        self.bad_lines += 1
                        if self.verbose:
                            print(f"[vision] 回调异常: {e}")
        except Exception as e:
            if self.verbose:
                print(f"[vision] 连接异常 {addr}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            if self.verbose:
                print(f"[vision] 客户端断开 {addr[0]}")


# ================================================================ 自检
def _selftest():
    import json
    got = []

    def on_line(raw):
        d = json.loads(raw.decode("utf-8"))
        got.append(d)

    vs = VisionTcpServer(5099, on_line).start()
    time.sleep(0.3)
    c = socket.create_connection(("127.0.0.1", 5099), timeout=2)
    for i in range(3):
        c.sendall((json.dumps({"version": 1, "timestamp": int(time.time() * 1000),
                               "obstacle": False}) + "\n").encode())
    time.sleep(0.4)
    c.close()
    time.sleep(0.2)
    vs.stop()
    print(f"[selftest] 收到 {len(got)} 帧，服务端计数 {vs.frames}")
    ok = len(got) == 3 and vs.frames == 3
    print("[PASS] vision_server 正常" if ok else "[FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest())
    print("这是模块，不单独运行。用 --selftest 自检，或在 board_main.py 里 import。")
