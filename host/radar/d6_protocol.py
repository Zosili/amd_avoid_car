# -*- coding: utf-8 -*-
"""
radar/d6_protocol.py — 国科光芯 COIN-D6 串口帧解析（协议来源三源交叉验证，实测定案）

来源：
  1)《COIN-D6激光雷达数据格式标准说明V1.0》官方 PDF（位打包公式/角度C校验位）
  2) 官方 STM32 例程 STM32F103RCT6_COIN-D6_Demo_V1.3.0/Driver/ax_laser.c
  3) 官方 ROS2 SDK cspc_lidar_sdk_ros2_D4_20250731/sdk/lidar_data_processing.cpp
实测抓包 1110 帧验证：官方 CS 校验公式命中 1109/1110（唯一失败帧为 UART 误码，
    恰说明该校验有效）；C 校验位 1111/1111 帧成立。

帧 85B = AA 55 | M&T(1B) | LSN(1B) | FSA(2B) | LSA(2B) | CS(2B) | LSN×(Si_L, Si_2nd, Si_H)
  M&T  bit0=T 包类型(0=点云包,1=起始包LSN=1)；bit7:1=扫描频率
  LSN  采样点数（点云包 25，起始包 1）
  FSA/LSA 角度 raw 小端 u16，低 bit C 是固定为 1 的校验位；Angle=(raw>>1)/64 度
  Si 位打包：距离 dist_mm = Si_H*64 + (Si_2nd>>2)          （14bit）
             强度 it      = (Si_2nd&0x03)*64 + (Si_L>>2)  （8bit）
             Si_L bit0 = 远距/弱回波标志（实测该位=1的点距离中位 9.7m、强度中位
             91，而正常点 0.28m/220；距离值仍有效但低置信，取扇区最小值时天然不敏感）
  CS 双字节异或（小端存）：
     0x55AA ^ (M&T + LSN*256) ^ FSA_raw ^ LSA_raw
       ^ XOR_k( Si_L ^ (Si_2nd + Si_H*256) )
坐标：左手法则、原点=旋转中心、角度顺时针增大；**0° = 外壳上"单个安装耳"的方向**
（官方《COIN-D6 单线激光雷达规格书 A1》第 7 页「图 3 扫描数据坐标系定义」：
零度角方向箭头从旋转中心指向那个单独的安装耳）。⚠️ **不是接线束方向** ——
4pin 连接器在外壳侧面，与 0° 大致垂直。装车偏角用 ZERO_OFFSET_DEG 补。
波特率 230400 8N1；上电即吐流；官方亦可 AA 55 F0 0F 启动 / AA 55 F5 0A 停止。
"""
try:
    import serial                    # 只有真正开串口时才需要 pyserial
except ImportError:                  # 离线解析 / demo 出图不装 pyserial 也能用
    serial = None

PORT = "COM5"
BAUD = 230400
NPTS = 25
STEP_DEG = 0.9            # 360/400，D6 实测 400 点/圈
MIN_RANGE = 60            # mm，官方 FAQ#16：<6cm 一律返回 0，无有效回波
MAX_RANGE = 12000         # mm，官方量程 12m
ZERO_OFFSET_DEG = 0.0     # 装车标定后修改

# 启停指令（官方两套都收：PDF 一套 F0 0F/F5 0A；FAQ 另一套 FE 01/FD 02；
# 实测本固件上电即吐流，但 FAQ#9/#27 称部分固件需先发指令——启动兜底用）
START_CMD_1 = bytes.fromhex("AA55F00F")
START_CMD_2 = bytes.fromhex("AA55FE01")

# 扇区定义（度，顺时针，0°=接线束方向；装上后按 ZERO_OFFSET 平移）
SECTOR_FRONT = (345.0, 15.0)   # 跨 0°
SECTOR_LEFT = (30.0, 90.0)
SECTOR_RIGHT = (270.0, 330.0)

PH = 0x55AA               # 包头，参与校验

# ---- 点云级抗噪（2026-10-24 新增）----
# 1) 弱回波标志：Si_L 的 bit0。实测该位=1 的点距离中位 9.7m、强度中位 91，
#    而正常点 0.28m/220 —— 属于低置信回波。丢不丢见下。
DROP_WEAK_ECHO = True     # True=丢弃弱回波点（保守）；False=保留（调试/标定时用）
# 2) 扇区稳健统计：min() 对单点噪声极敏感（一圈 25 点里任意一个错点就能把
#    3m 拉到 0.2m）。改成取第 k 小。k=1 退化为原来的 min()。
SECTOR_ROBUST_K = 2
#    但扇区有效点太少时没法判断谁是真障碍，退回取最小。
SECTOR_ROBUST_MIN_PTS = 4
# 3) 扇区统计的重算频率（Hz）。雷达帧率 163Hz，但决策只有 10Hz ——
#    每帧都重算三扇区最小值，实测 94% 是重复劳动（一圈 400 点只变 10.2 次/秒）。
#    30Hz 相当于每次决策前有 3 次刷新，够用；设 0 表示每帧都算（回到旧行为）。
RECOMPUTE_HZ = 30.0


def _angle(raw):
    """角度 raw（小端 u16，低 bit 为 C 校验位）→ 度（官方公式 (FSA>>1)/64）"""
    return (raw >> 1) / 64.0


def checksum(ct, n, body):
    """官方 SDK 双字节异或校验值（实测 1109/1110 命中）"""
    cal = PH ^ (ct + n * 256)
    cal ^= int.from_bytes(body[0:2], "little")    # FSA raw，含 C 位
    cal ^= int.from_bytes(body[2:4], "little")    # LSA raw，含 C 位
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k: 9 + 3 * k]
        cal ^= s_l ^ (s_2 + s_h * 256)
    return cal


def read_frame(ser):
    """同步 AA 55 读一帧 → (ct, n, body) 或 None。body = M&T/LSN 之后的 6+n*3 字节。"""
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
        if len(rest) < 2:
            continue
        ct, n = rest[0], rest[1]
        if n == 0 or n > 60:
            continue
        if (ct & 0x01) == 1 and n != 1:
            continue                        # 起始包 LSN 必须为 1
        body = ser.read(6 + n * 3)
        if len(body) < 6 + n * 3:
            return None
        return ct, n, body


def parse_frame_ex(ct, n, body):
    """同 parse_frame，但每点多返回一个弱回波标志 → [(角度°, 距离mm|None, 强度, 弱回波bool)]
    校验失败 None。校验：C 位必须为 1 + 官方 CS 双字节异或（双保险，实测 0 误帧）"""
    fsa = int.from_bytes(body[0:2], "little")
    lsa = int.from_bytes(body[2:4], "little")
    cs = int.from_bytes(body[4:6], "little")
    if not (fsa & 1) or not (lsa & 1):      # C 校验位固定为 1
        return None
    if checksum(ct, n, body) != cs:
        return None
    a0, a1 = _angle(fsa), _angle(lsa)
    if n > 1:
        span = a1 - a0
        if span < 0 and a0 > 270.0 and a1 < 90.0:
            span += 360.0                   # 跨 0° 换行
        nominal = STEP_DEG * (n - 1)
        if not (0.5 * nominal <= span <= 1.5 * nominal):
            span = nominal                  # 异常帧退回标称步进
        step = span / (n - 1)
    else:
        step = 0.0
    pts = []
    for k in range(n):
        s_l, s_2, s_h = body[6 + 3 * k: 9 + 3 * k]
        dist = s_h * 64 + (s_2 >> 2)                    # 14bit，mm
        it = (s_2 & 0x03) * 64 + (s_l >> 2)             # 8bit
        weak = bool(s_l & 0x01)                         # bit0 = 远距/弱回波标志
        ang = (a0 + k * step + ZERO_OFFSET_DEG) % 360.0
        ok = MIN_RANGE <= dist <= MAX_RANGE              # 0=无回波/超量程 → None
        if ok and weak and DROP_WEAK_ECHO:
            ok = False                                   # 低置信回波按无回波处理
        pts.append((ang, dist if ok else None, it, weak))
    return pts


def parse_frame(ct, n, body):
    """→ [(角度°, 距离mm 或 None, 强度)]；校验失败 None
    兼容旧接口（3 元组）。要看弱回波标志请用 parse_frame_ex()。"""
    pts = parse_frame_ex(ct, n, body)
    if pts is None:
        return None
    return [(a, d, i) for a, d, i, _w in pts]


def _in_sector(ang, sec):
    lo, hi = sec
    if lo <= hi:
        return lo <= ang < hi
    return ang >= lo or ang < hi          # 跨 0° 扇区


def _build_sector_lut():
    """整度 → 扇区号（0=front 1=left 2=right -1=不用）
    实测每点调 1~3 次 _in_sector 占扇区统计 70% 的时间；查表后快 3 倍。
    ⚠️ 改 SECTOR_* 常量后要重启进程（LUT 在 import 时构建）。"""
    lut = {}
    for deg in range(360):
        a = float(deg)
        if _in_sector(a, SECTOR_FRONT):
            lut[deg] = 0
        elif _in_sector(a, SECTOR_LEFT):
            lut[deg] = 1
        elif _in_sector(a, SECTOR_RIGHT):
            lut[deg] = 2
        else:
            lut[deg] = -1
    return lut


_SECTOR_LUT = _build_sector_lut()


def _robust_min(vals):
    """vals: 距离 mm 列表 → 稳健最小距离(米) 或 None
    点太少（< SECTOR_ROBUST_MIN_PTS）时退回真最小：样本不够时没法判断谁是噪点。
    ⚠️ 不能用 sorted(vals)[k-1]：排序是 O(n log n)，实测比原来的 min() 慢一倍。
       这里维护一个长度 k 的升序小表，O(n·k)，k 通常 =2。"""
    n = len(vals)
    if n == 0:
        return None
    k = SECTOR_ROBUST_K
    if k <= 1 or n < SECTOR_ROBUST_MIN_PTS:
        return min(vals) / 1000.0
    if k > n:
        k = n
    best = []                       # 升序，最多 k 个
    for v in vals:
        if len(best) < k:
            best.append(v)
            if len(best) == k:
                best.sort()
        elif v < best[-1]:
            best[-1] = v
            best.sort()
    return best[-1] / 1000.0


def sector_min(scan, sec):
    """扇区内稳健最小距离(米)；无则 None（单扇区版，保留兼容）"""
    return _robust_min([d for a, d, *_ in scan
                        if d is not None and _in_sector(a, sec)])


def scan_stats(scan):
    """单遍扫描三个扇区 → {"front": (距离m|None, 有效点数), "left": ..., "right": ...}

    比调三次 sector_min 快约 2.5 倍（400 点扫 1 遍 + 查表定扇区），
    落在关注角度之外的 210° 只查一次表就跳过 —— 这就是"用不到的角度"能省的部分。
    注意：串口字节和解码省不掉（雷达机械旋转，整圈都会发），省的是扇区统计。
    """
    buf_f, buf_l, buf_r = [], [], []
    get = _SECTOR_LUT.get
    # ⚠️ 用下标访问而不是 `for ang, d, *_`：`*_` 每轮都要建一个 list，
    #    400 点下实测比下标访问慢 20%。scan 可能是 3 元组或 4 元组。
    for p in scan:
        d = p[1]
        if d is None:
            continue
        sid = get(int(p[0]) % 360, -1)
        if sid < 0:
            continue                    # 用不到的角度，直接跳过
        if sid == 0:
            buf_f.append(d)
        elif sid == 1:
            buf_l.append(d)
        else:
            buf_r.append(d)
    return {"front": (_robust_min(buf_f), len(buf_f)),
            "left": (_robust_min(buf_l), len(buf_l)),
            "right": (_robust_min(buf_r), len(buf_r))}


def scan_to_radar_json(scan, t=None):
    from protocol import make_radar_json
    st = scan_stats(scan)
    return make_radar_json(st["front"][0], st["left"][0], st["right"][0], t=t)
