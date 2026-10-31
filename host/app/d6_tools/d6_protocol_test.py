# -*- coding: utf-8 -*-
"""
D6 协议自测脚本（2026-10-20）
目的：判定 D6 数据帧是否兼容 LD06（0x54 0x2C 开头 47 字节帧）
流程：
  1. 被动监听 1s（部分雷达上电自动吐数据）
  2. 依次尝试启动指令 AA 55 FE 01 / AA 55 F0 0F / AA 55 F5 0A
  3. 抓 3s 原始字节流存 d6_capture.bin
  4. 分析：找 54 2C 帧头 → 帧间距分布 → CRC-8(poly 0x07) 校验 → 解析首帧字段
"""
import serial, time, sys, collections

PORT = "COM5"
BAUD = 230400
import os
_HERE_D6 = os.path.dirname(os.path.abspath(__file__))
CAP_PATH = os.path.join(_HERE_D6, "d6_capture.bin")

def crc8_ld06(data: bytes) -> int:
    """LD06 系 CRC-8，多项式 x^8+x^2+x^1+1 (0x07)，初值 0x00"""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def read_for(ser, seconds):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < seconds:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.005)
    return bytes(buf)

def analyze(buf: bytes, label: str):
    print(f"\n===== {label} =====")
    print(f"总字节数: {len(buf)}")
    if not buf:
        print("(无数据)")
        return 0
    # 字节分布 top5（判断是否像文本还是二进制流）
    top = collections.Counter(buf).most_common(5)
    print("最高频字节:", " ".join(f"0x{b:02X}x{c}" for b, c in top))
    # 找 54 2C 帧头
    heads = [i for i in range(len(buf) - 1) if buf[i] == 0x54 and buf[i + 1] == 0x2C]
    print(f"54 2C 帧头出现次数: {len(heads)}")
    if not heads:
        print("前 64 字节 hexdump:", buf[:64].hex(" "))
        return 0
    # 帧间距分布
    gaps = collections.Counter(b - a for a, b in zip(heads, heads[1:]))
    print("帧间距分布(字节):", dict(gaps.most_common(6)))
    # 对最常见间距做 CRC 全帧校验
    main_gap = gaps.most_common(1)[0][0] if len(heads) > 1 else None
    ok = bad = 0
    frame_len = 46  # 1+1+1+2+36+2+2+1 = 46，先按 46 试；若间距稳定为其他值自动切换
    if main_gap and main_gap in (46, 47, 48):
        frame_len = main_gap
    for h in heads:
        f = buf[h:h + frame_len]
        if len(f) < frame_len:
            break
        if crc8_ld06(f[:-1]) == f[-1]:
            ok += 1
        else:
            bad += 1
    print(f"CRC-8(0x07) 校验（按帧长 {frame_len}）: 通过 {ok} / 失败 {bad}")
    # 解析第一个 CRC 通过的帧
    for h in heads:
        f = buf[h:h + frame_len]
        if len(f) < frame_len or crc8_ld06(f[:-1]) != f[-1]:
            continue
        speed = f[2]
        start_angle = int.from_bytes(f[3:5], "little") / 100.0
        end_angle = int.from_bytes(f[-4:-2], "little") / 100.0
        ts = int.from_bytes(f[-2 - 2 + 2:-2], "little") if False else int.from_bytes(f[frame_len - 4:frame_len - 2], "little")
        pts = []
        for k in range(12):
            d = int.from_bytes(f[5 + 3 * k:7 + 3 * k], "little")
            conf = f[7 + 3 * k]
            pts.append((d, conf))
        print(f"示例帧@{h}: 转速={speed} 起始角={start_angle}° 结束角={end_angle}° 时间戳={ts}")
        print("  12点 (距离mm,置信度):", pts)
        break
    return len(heads)

def main():
    ser = serial.Serial(PORT, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.1)
    print(f"已打开 {PORT} @ {BAUD}")
    # 1. 被动监听
    passive = read_for(ser, 1.0)
    n1 = analyze(passive, "阶段1: 被动监听 1s（未发指令）")
    if n1 > 5:
        print("\n>> 雷达上电自动吐数据，无需启动指令。")
        capture = passive
        cmd_used = "(自动)"
    else:
        # 2. 依次尝试启动指令
        capture = bytearray(passive)
        cmd_used = None
        for cmd, name in [(b"\xAA\x55\xFE\x01", "AA 55 FE 01"),
                          (b"\xAA\x55\xF0\x0F", "AA 55 F0 0F"),
                          (b"\xAA\x55\xF5\x0A", "AA 55 F5 0A")]:
            ser.write(cmd)
            seg = read_for(ser, 2.0)
            capture += seg
            print(f"\n>> 已发送启动指令 {name}")
            n = analyze(seg, f"阶段2: 发 {name} 后监听 2s")
            if n > 5:
                cmd_used = name
                break
        capture = bytes(capture)
    ser.close()
    with open(CAP_PATH, "wb") as fp:
        fp.write(capture)
    print(f"\n原始流已存: {CAP_PATH} ({len(capture)} 字节)")
    print(f"生效指令: {cmd_used}")
    # 汇总判定
    heads = sum(1 for i in range(len(capture) - 1) if capture[i] == 0x54 and capture[i + 1] == 0x2C)
    print("\n===== 判定 =====")
    if heads >= 20:
        print("检测到大量 54 2C 帧 → D6 数据帧兼容 LD06，可借用 LD06 生态（henjin0/LIDAR_LD06_python_loader）")
    elif heads > 0:
        print("有少量 54 2C，需人工确认是否真帧（看上面 CRC 通过率）")
    else:
        print("无 54 2C 帧 → 不兼容 LD06，需按官方 C++ 脚本自写解析")

if __name__ == "__main__":
    main()
