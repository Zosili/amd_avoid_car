# -*- coding: utf-8 -*-
"""抓3帧原始数据存盘，用于离线分析正确解析方式"""
import socket, time

PORT = 1234
BOARD_IP = "192.168.1.10"
FRAME_HEAD = b"\xf0\x5a\xa5\x0f"
FRAME_SIZE = 640 * 480 * 2

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
s.bind(("0.0.0.0", PORT))
s.settimeout(0.5)
s.sendto(b"1", (BOARD_IP, PORT))
print("已发启动命令，抓数据...")

buf = b""
target = FRAME_SIZE + 200  # 抓够2帧多
deadline = time.time() + 10
while len(buf) < target and time.time() < deadline:
    try:
        data, _ = s.recvfrom(65536)
        buf += data
    except socket.timeout:
        pass

with open(r"D:\FPGA\42_ov5640_udp_pc\raw_capture.bin", "wb") as f:
    f.write(buf[:target])
print(f"已保存 {min(len(buf), target)} 字节到 raw_capture.bin")
print(f"总收到 {len(buf)} 字节，帧头出现 {buf.count(FRAME_HEAD)} 次")
print("前64字节:", buf[:64].hex(" "))
