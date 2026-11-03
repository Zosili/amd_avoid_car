# -*- coding: utf-8 -*-
"""RGB565 解码 + cam_data[5] 数据线缺陷补偿（PC 端共用）

背景（2026-10-31 定位）：
    抓包逐 bit 统计发现，16 位像素数据里 **bit5 与 bit13 均为 100% 为 1**。
    这两个位正好是同一条 8 位数据线（D5）在两个字节时隙上的落点：
        bit13 -> 高字节 bit5 -> RGB565 的 R[2]
        bit5  -> 低字节 bit5 -> RGB565 的 G[0]
    而 10-12 摄像头正常时期的抓包，bit5 是 ~50% 的随机值。
    约束文件里 cam_data[5] -> W13 各版本一致，RTL 里 cam_data 纯直通
    （img_data_pkt: wr_fifo_data <= img_data_d0），故排除软件侧。

    结论：FPGA 引脚 W13 与 OV5640 模块 D5 之间现在恒为高电平
    （断线 / 接触不良 / 短到 3V3）。R 的 bit2 被强行置 1，使 R 平均偏高
    约 4/31 ≈ 12.9%，画面整体偏红；而顶灯过曝区所有通道饱和成 255，
    看起来仍是纯白，容易误判成"只有暗部偏红"。

    修好排线后调用 set_mode("off") 或加 --color-fix off 即可恢复原始通路。

【2026-10-31 14:20 更新：问题已消失，默认改为 off】
    板子重新接好后实测：bit5/bit13 回到 45~51% 随机值，R/G = +1.5%，
    画面颜色完全正常（墙面纯白、肤色正常）。判定为 D5 之前的接触不良，
    重新插接后恢复。XDC 里给 cam_data[5] 留了 PULLTYPE PULLUP 作保险：
    万一再接触不良，该位会稳定恒 1（而不是随机漂移），此时把 MODE 切回
    "d5" 即可临时救画面。

补偿做法（治标，信息已丢，无法完全还原）：
    R: 清掉恒为 1 的 bit2 -> 3x3 中值抹掉量化台阶 -> 查表 +2 补平均偏移
       并要求 31->31（否则纯白会发青）
    G: bit0 恒 1 影响仅 ~1.6%，直接清零
"""
import os

import numpy as np
import cv2

# 也可用环境变量 CART_COLOR_FIX=off 全局关掉（view.py 这类没有命令行参数的工具靠它）
MODE = os.environ.get("CART_COLOR_FIX", "off")     # "off" | "d5"

# R5 修表：清 bit2 后 +2 补平均偏移，再拉伸保证 31 仍是 31
_LUT_R5 = np.array([min(31, int(round((v + 2) * 31.0 / 29.0)))
                    for v in range(32)], dtype=np.uint8)


def set_mode(mode):
    global MODE
    if mode not in ("d5", "off"):
        raise ValueError(f"colorfix mode 只支持 d5/off，收到 {mode!r}")
    MODE = mode


def decode(data, w, h, mode=None):
    """UDP 负载(RGB565 小端) -> BGR uint8 图像"""
    a = np.frombuffer(data, dtype=np.uint16).reshape(h, w)
    a = ((a & 0xFF) << 8) | (a >> 8)          # 小端字节序交换（实测必需）
    r5 = (a >> 11) & 0x1F
    g6 = (a >> 5) & 0x3F
    b5 = a & 0x1F
    if (mode or MODE) == "d5":
        t = (r5 & 0x1B).astype(np.uint8) << 3   # 清 bit2（0b11011）
        t = cv2.medianBlur(t, 3) >> 3           # 抹掉 4 级量化台阶
        r5 = _LUT_R5[t].astype(np.uint16)
        g6 = g6 & 0x3E                          # G 的 bit0 同样恒 1
    r = (r5 << 3) | (r5 >> 2)
    g = (g6 << 2) | (g6 >> 4)
    b = (b5 << 3) | (b5 >> 2)
    return cv2.merge([b.astype(np.uint8), g.astype(np.uint8), r.astype(np.uint8)])
