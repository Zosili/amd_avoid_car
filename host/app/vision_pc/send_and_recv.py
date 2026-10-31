import socket, time

# 一体化测试：先向板子发启动命令"1"，再监听收图
BOARD_IP = "192.168.1.10"
PORT = 1234

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", PORT))
s.settimeout(3)

# 1) 发启动命令（1字节 '1' = 0x31）
s.sendto(b"1", (BOARD_IP, PORT))
print(f"已向 {BOARD_IP}:{PORT} 发送启动命令 0x31，开始监听15秒...")

# 2) 收图
total = 0; pkts = 0; head = 0; start_t = time.time(); first = None
while time.time() - start_t < 15:
    try:
        data, addr = s.recvfrom(4096)
    except socket.timeout:
        continue
    if first is None:
        first = addr
        print(f"首包来源 {addr[0]}:{addr[1]}，长度 {len(data)}")
    total += len(data); pkts += 1
    head += data.count(b"\xf0\x5a\xa5\x0f")

print("=" * 40)
if pkts == 0:
    print("结果：15秒收到 0 包 —— 板子没有发图")
else:
    print(f"结果：收到 {pkts} 包 / {total} 字节，帧头出现 {head} 次")
    print("结论：链路全通，板子在发图！")
