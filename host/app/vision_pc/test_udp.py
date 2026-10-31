import socket, sys, time

# 抓 UDP 1234 端口，判断板子有没有发图
UDP_IP = "0.0.0.0"
UDP_PORT = 1234

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((UDP_IP, UDP_PORT))
s.settimeout(3)

print(f"监听 UDP {UDP_PORT}，等待板子数据（最多15秒）...")
print("提示：如果板子在发图，几秒内就会有数据进来。\n")

total = 0
pkts = 0
first_addr = None
frame_head_count = 0
start = time.time()

try:
    while time.time() - start < 15:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        if first_addr is None:
            first_addr = addr
            print(f"✅ 收到首包！来源 {addr[0]}:{addr[1]}，长度 {len(data)} 字节")
            print(f"   首包前16字节(hex): {data[:16].hex(' ')}")
        total += len(data)
        pkts += 1
        # 统计帧头 f0 5a a5 0f 出现次数
        frame_head_count += data.count(b"\xf0\x5a\xa5\x0f")
except KeyboardInterrupt:
    pass

print("\n================= 结果 =================")
if pkts == 0:
    print("❌ 15秒内 0 字节，板子没发任何数据")
    print("   可能原因：摄像头初始化失败 / 未收到启动命令 / bit没跑起来")
else:
    print(f"✅ 收到 {pkts} 个包，共 {total} 字节")
    print(f"   来源: {first_addr[0]}:{first_addr[1]}")
    print(f"   帧头 f05aa50f 出现 {frame_head_count} 次")
    print("   结论：链路全通，板子在正常发图 → 上位机看图即可")
