# -*- coding: utf-8 -*-
"""
国科光芯 D6 最终解析器（2026-10-20 逆向验证通过）
帧格式（85 字节，25 点/帧，400 点/圈，0.9°/点，10Hz，230400 8N1）：
  AA 55 | 00 | 19(点数) | 起始角 u16LE | 结束角 u16LE | 未知 u16 | 25 × (置信度 u8 + 距离 u16LE mm)
  角度 = raw / 128 度，回绕常数 46080；结束角 = 起始角 + 24×0.9°；点间步进 0.9°
  置信度实测 ~170-255 较好；距离 0 或超量程 = 无效
输出：Radar JSON {"valid","front_distance","left_distance","right_distance"}（米，无效为 null）
"""
import serial, json, time, math, sys

PORT = "COM5"
BAUD = 230400
NPTS = 25
STEP = 0.9            # 度/点
WRAP = 46080          # 360° * 128
DEG = lambda raw: (raw % WRAP) / 128.0
MAX_RANGE = 12000     # mm

def read_frame(ser):
    """同步到 AA 55 00，读一帧。返回 (点数, body) 或 None"""
    while True:
        b = ser.read(1)
        if not b:
            return None
        if b[0] != 0xAA:
            continue
        b2 = ser.read(1)
        if not b2 or b2[0] != 0x55:
            continue
        rest = ser.read(2)
        if len(rest) < 2 or rest[0] != 0x00:
            continue
        n = rest[1]
        if n == 0 or n > 60:
            continue
        body = ser.read(6 + n * 3)
        if len(body) < 6 + n * 3:
            return None
        return n, body

def parse_frame(n, body):
    """返回 [(角度°, 距离mm, 置信度)], 或 None 表示校验失败"""
    a0 = int.from_bytes(body[0:2], "little")
    a1 = int.from_bytes(body[2:4], "little")
    # 校验 1：结束角 ≈ 起始角 + (n-1)*0.9°
    expect = (a0 + int(round((n - 1) * STEP * 128))) % WRAP
    if abs(((a1 - expect + WRAP // 2) % WRAP) - WRAP // 2) > 3 * 128:  # 容差 3°
        return None
    pts = []
    for k in range(n):
        conf = body[6 + 3 * k]
        d = int.from_bytes(body[7 + 3 * k:9 + 3 * k], "little")
        ang = (DEG(a0) + k * STEP) % 360.0
        ok = 50 <= d <= MAX_RANGE and conf > 0
        pts.append((ang, d if ok else None, conf))
    return pts

def sector_min(scan, lo, hi):
    """[lo,hi) 角度区间内最小有效距离(m)，无则 None。角度按数学方向 0-360"""
    vals = [d for a, d, c in scan if d is not None and lo <= (a % 360) < hi]
    return round(min(vals) / 1000.0, 3) if vals else None

def radar_json(scan):
    # 前=345°~15°(±15°窗口)，左=30~90，右=270~330（方向基准待实车标定）
    return {
        "valid": scan is not None and len(scan) > 0,
        "front_distance": sector_min(scan, 345, 360) or sector_min(scan, 0, 15),
        "left_distance": sector_min(scan, 30, 90),
        "right_distance": sector_min(scan, 270, 330),
    }

def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    ser = serial.Serial(PORT, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.2)
    print(f"已打开 {PORT}@{BAUD}，采集 {dur}s ...\n")
    t0 = time.time()
    scan = []           # 当前圈累积
    scans_done = 0
    bad = 0
    last_ang = None
    confs = []
    sample_printed = False
    while time.time() - t0 < dur:
        fr = read_frame(ser)
        if fr is None:
            continue
        pts = parse_frame(*fr)
        if pts is None:
            bad += 1
            continue
        a0 = pts[0][0]
        if last_ang is not None and a0 < last_ang - 180:  # 角度回卷 = 新一圈
            scans_done += 1
            if scans_done == 2 and not sample_printed:    # 第 2 圈做示例输出
                j = radar_json(scan)
                print("一圈完成，Radar JSON 示例:")
                print(json.dumps(j, ensure_ascii=False, indent=2))
                n_valid = sum(1 for _, d, _ in scan if d is not None)
                print(f"\n本圈 {len(scan)} 点, 有效 {n_valid} 点, 平均置信度 "
                      f"{sum(c for _, _, c in scan)/len(scan):.0f}")
                # 分扇区摘要（每 30°）
                print("\n扇区最小距离(m) [方向基准待实车标定]:")
                for lo in range(0, 360, 30):
                    print(f"  {lo:3d}°~{lo+30:3d}°: {sector_min(scan, lo, lo+30)}")
                sample_printed = True
            scan = []
        scan.extend(pts)
        last_ang = a0
        confs.extend(c for _, _, c in pts if c)
    ser.close()
    total = scans_done * 400
    print(f"\n===== 统计 =====")
    print(f"完整圈数: {scans_done} (≈{scans_done/dur:.1f} Hz), 校验失败帧: {bad}")
    print(f"平均置信度: {sum(confs)/len(confs):.0f}" if confs else "无数据")

if __name__ == "__main__":
    main()
