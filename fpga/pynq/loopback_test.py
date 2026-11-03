#!/usr/bin/env python3
"""AXI UART Lite 回环测试：板子 TX 发数据，RX 读回。
需要把 J4-9(TX/U9) 和 J4-10(RX/U8) 用一根杜邦线短接。
如果读回 == 发出的字节，说明 AXI UART Lite 本身工作正常。
"""
import sys, os, time
sys.path.insert(0, "/home/xilinx/radar")
os.environ.setdefault("XILINX_XRT", "/usr")
from pynq import Overlay, MMIO

ol = Overlay("/home/xilinx/radar/motor.bit")
mm = MMIO(0x42C00000, 0x10000)

# 复位 FIFO
mm.write(0x0C, 0x03)
mm.write(0x0C, 0x00)

test = b"Hello UART 12345"
print("发送:", test)

# 逐字节发到 TX FIFO (0x04)
for b in test:
    # 等 TX FIFO 不满 (STAT bit3 = TX_FULL)
    for _ in range(10000):
        stat = mm.read(0x08)
        if not (stat & 0x08):
            break
        time.sleep(0.0001)
    mm.write(0x04, b)

print("等待回环 ...")
time.sleep(1.0)

# 读 RX FIFO (0x00)
rx = bytearray()
for _ in range(len(test)):
    # 等 RX 有数据 (STAT bit0 = RX_VALID)
    got = False
    for _ in range(10000):
        stat = mm.read(0x08)
        if stat & 0x01:
            rx.append(mm.read(0x00) & 0xFF)
            got = True
            break
        time.sleep(0.0001)
    if not got:
        break

print("读回:", bytes(rx))
if bytes(rx) == test:
    print("✅ 回环成功！AXI UART Lite 工作正常")
else:
    print("❌ 回环失败：", "收到 " + str(len(rx)) + " 字节" if rx else "没收到任何字节")
    print("   可能原因：TX/RX 没短接，或 AXI UART Lite 配置/引脚有问题")
