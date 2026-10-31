# -*- coding: utf-8 -*-
"""
calibrate_zero.py — COIN-D6 装车 0° 基准标定工具（郑）

原理
----
硬件 0° = 雷达外壳上【单个安装耳】的方向。
    官方《COIN-D6 单线激光雷达规格书 A1》第 7 页「图 3 扫描数据坐标系定义」：
    俯视图里外壳顶部有一个单独的安装耳（另两个耳左右对称分布），
    "零度角方向"箭头从旋转中心垂直指向那个单耳；角度顺时针增大。
    ⚠️ **不是接线束方向** —— 4pin 连接器在外壳侧面，与 0° 大致垂直。
    （2026-10-24 核对官方 PDF 原图后修正，此前记录为"接线束方向"是错的。）

装车时单耳方向通常不朝车头，需量出偏角 θ，把 radar/d6_protocol.py 的
ZERO_OFFSET_DEG 改为 θ。

标定条件（否则结果无意义）
------------------------
把雷达摆在最终安装位（或至少朝向一致），在**车头正前方** 1~3m 处放一块
平板/墙/纸箱，**四周背景尽量比它远**。跑 --survey，脚本找最近物体的角度峰值。

⚠️ 如果四周都堆着东西（背景和目标的距离混在一起），本脚本的"取全局最近 bin"
判断会失效 —— 那时改用 integration/radar_dump.py 的差分法：
先 --save-base 存空场景，再放目标 --diff，差出来的那段才是目标。

用法
----
  python calibrate_zero.py                 # 实时模式：扇区读数 + 36×10° 直方图，Ctrl+C 停
  python calibrate_zero.py --survey 20     # 测 20 秒 → 输出建议 ZERO_OFFSET_DEG
  python calibrate_zero.py --port COM5 --baud 230400
"""
import argparse
import sys
import time

from radar import d6_protocol as P


def collect_bins(seconds=None, port=None, baud=None, live=False):
    """读串口攒 36 个 10° bin 的最近距离(米)。seconds=None → 一直跑到 Ctrl+C。
    返回 (bins[36], frames)。live=True 时打印实时直方图。"""
    import serial
    ser = serial.Serial(port or P.PORT, baud or P.BAUD, bytesize=8,
                        parity="N", stopbits=1, timeout=0.2)
    # dtr/rts 拉低：官方手册提示 DTR 拉高会触发雷达硬件复位
    ser.dtr = False
    ser.rts = False
    bins = [None] * 36
    frames = 0
    t0 = time.time()
    try:
        while seconds is None or time.time() - t0 < seconds:
            fr = P.read_frame(ser)
            if fr is None:
                continue
            pts = P.parse_frame(*fr)
            if pts is None:
                continue
            frames += 1
            for ang, d, it in pts:
                if d is None:
                    continue
                b = int(ang // 10) % 36
                m = d / 1000.0
                if bins[b] is None or m < bins[b]:
                    bins[b] = m
            if live:
                render(bins, frames)
                time.sleep(0.4)
    finally:
        ser.close()
    return bins, frames


def render(bins, frames):
    """36 bin 文本直方图（最近距离越短条越长）"""
    lines = ["\x1b[2J\x1b[H", "D6 0° 标定  |  帧数 %d  |  bin=10°，条形长度∝1/距离" % frames,
             "0° = 接线束出口方向（车头正前方目标应出现在 offset 后的 345~15°）"]
    for b, v in enumerate(bins):
        if v is None:
            lines.append("  %3d°  —          无回波" % (b * 10))
        else:
            n = max(1, int(30 / max(v, 0.1)))
            lines.append("  %3d°  %-30s %.2f m" % (b * 10, "#" * n, v))
    print("\n".join(lines), flush=True)


def survey(seconds, port, baud):
    bins, frames = collect_bins(seconds, port, baud, live=False)
    valid = [(i * 10, v) for i, v in enumerate(bins) if v is not None]
    if not valid:
        print("未收到任何有效点，检查接线/COM 口")
        sys.exit(1)
    print("采集 %d 帧，%d/36 个 bin 有回波" % (frames, len(valid)))
    render(bins, frames)
    # 全局最近 bin（车头正前方目标）
    peak_bin, peak_d = min(valid, key=lambda x: x[1])
    peak_ang = peak_bin + 5.0
    print("\n=== 标定结果 ===")
    print("最近目标出现在 bin %d°（中心 %.0f°），距离 %.2f m" % (peak_bin, peak_ang, peak_d))
    print("即：接线束出口方向 = 该目标方向，硬件 0° 实际指向 %.0f°（雷达坐标系）" % peak_ang)
    off = (360.0 - peak_ang) % 360.0
    print(">>> 建议 radar/d6_protocol.py: ZERO_OFFSET_DEG = %.1f" % off)
    print("（改完后 front 扇区(345~15°)即车头正前方；左右扇区随之对齐）")
    # 自检：最小 bin 两侧应平滑变远（说明近距目标唯一）
    nb = [(b, v) for b, v in valid if abs((b - peak_bin + 18) % 36 - 18) <= 3]
    if nb:
        far = [v for b, v in nb if b != peak_bin]
        if far and min(far) < peak_d * 1.15:
            print("⚠ 警告：峰值附近 %d 个邻 bin 距离相近(%.2fm)，目标可能不唯一/太平，"
                  "建议换更窄的正面目标重测" % (len(nb) - 1, min(far)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", type=float, default=0,
                    help="定点测量秒数，输出建议 ZERO_OFFSET_DEG")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=None)
    a = ap.parse_args()
    if a.survey > 0:
        survey(a.survey, a.port, a.baud)
    else:
        try:
            collect_bins(None, a.port, a.baud, live=True)
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
