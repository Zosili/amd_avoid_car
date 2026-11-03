# -*- coding: utf-8 -*-
"""
链路探针：证明 PC -> PL 的 UDP 确实到了板子
================================================
原理：PL 里有个图传开关（start_transfer_ctrl），收到 **1 字节** 的 b"1"
就置 transfer_flag=1，开始把摄像头图像 UDP 推回 PC 的 1234 端口。
这是板子上唯一能"回话"的动作 —— 收到回包就说明：
  ARP 通 + IP 对 + UDP 收包通路通 + PL 里跑的是这套工程

用法：
    python probe_link.py            # 只探链路
    python probe_link.py --stop     # 发 b"0" 关掉图传
"""

from __future__ import annotations

import argparse
import socket
import time

BOARD_IP = "192.168.1.10"
PC_PORT = 1234


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=BOARD_IP)
    ap.add_argument("--port", type=int, default=PC_PORT, help="板子回传的目标端口")
    ap.add_argument("--rx-port", type=int, default=5000, help="附带再试一个接收口")
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--stop", action="store_true", help="发 b'0' 停止图传")
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    s.bind(("0.0.0.0", a.port))
    s.settimeout(0.5)

    if a.stop:
        s.sendto(b"0", (a.ip, a.port))
        print("已发 b'0' 停止图传")
        return

    # 两个端口各发一次启动命令，排除端口过滤的可能
    for p in {a.port, a.rx_port}:
        s.sendto(b"1", (a.ip, p))
        print(f"发 b'1' -> {a.ip}:{p}")

    print(f"监听 0.0.0.0:{a.port}  {a.secs:.0f}s ...")
    t_end = time.monotonic() + a.secs
    n = 0
    total = 0
    sizes = []
    while time.monotonic() < t_end:
        try:
            data, src = s.recvfrom(70000)
            n += 1
            total += len(data)
            if len(sizes) < 3:
                sizes.append(len(data))
        except socket.timeout:
            continue

    if n == 0:
        print("收到 0 个包 -> PC 到板子的 UDP 没打通，或板子里跑的不是这套工程")
        print("  （发 b'0' 收尾）")
        s.sendto(b"0", (a.ip, a.port))
        raise SystemExit(2)

    print(f"收到 {n} 个包，共 {total/1e6:.2f} MB，前几个包长 {sizes}")
    print("=> 链路通：ARP/IP/UDP 收包都正常")
    print("发 b'0' 停止图传")
    for _ in range(3):
        s.sendto(b"0", (a.ip, a.port))
        time.sleep(0.05)


if __name__ == "__main__":
    main()
