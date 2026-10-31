# -*- coding: utf-8 -*-
"""
tests/test_radar_filter.py — 点云级抗噪测试（2026-10-24）

覆盖三件事：
  1. 弱回波标志位（Si_L bit0）能被识别，且 DROP_WEAK_ECHO 生效
  2. 扇区稳健统计（取第 k 小）能吃掉单点噪声
  3. 单遍 scan_stats() 与三次 sector_min() 结果一致（改性能不改行为）
"""
import random

import pytest

from radar import d6_protocol as P


def _mk_frame(n=25, start_deg=0.0, dist_mm=1500, weak_idx=()):
    """造一帧合法的 85 字节雷达包（bit0 of Si_L = 弱回波标志）"""
    fsa = int(round(start_deg * 64)) << 1 | 1
    lsa = int(round((start_deg + 0.9 * (n - 1)) * 64)) << 1 | 1
    body = bytearray()
    body += fsa.to_bytes(2, "little")
    body += lsa.to_bytes(2, "little")
    body += b"\x00\x00"                       # CS 占位
    for k in range(n):
        d = dist_mm + k
        it = 200
        s_h = (d >> 6) & 0xFF
        s_2 = ((d & 0x3F) << 2) | ((it >> 6) & 0x03)
        s_l = ((it & 0x3F) << 2) | (1 if k in weak_idx else 0)
        body += bytes((s_l, s_2, s_h))
    cs = P.checksum(0x00, n, bytes(body))
    body[4:6] = cs.to_bytes(2, "little")
    return 0x00, n, bytes(body)


def _mk_scan(pairs):
    """[(角度, 距离mm), ...] → 4 元组 scan（与 radar_provider 里一致）"""
    return [(a, d, 200, False) for a, d in pairs]


# ---------- 1. 弱回波标志 ----------

def test_weak_echo_flag_detected():
    ct, n, body = _mk_frame(weak_idx={3, 7, 11})
    pts = P.parse_frame_ex(ct, n, body)
    assert pts is not None and len(pts) == 25
    assert [k for k, p in enumerate(pts) if p[3]] == [3, 7, 11]


def test_weak_echo_dropped_by_default():
    ct, n, body = _mk_frame(weak_idx={3, 7, 11})
    pts = P.parse_frame_ex(ct, n, body)
    assert pts[3][1] is None          # 弱回波 → 当作无回波
    assert pts[7][1] is None
    assert pts[11][1] is None
    assert pts[4][1] == 1500 + 4      # 正常点不受影响


def test_weak_echo_kept_when_switch_off(monkeypatch):
    monkeypatch.setattr(P, "DROP_WEAK_ECHO", False)
    ct, n, body = _mk_frame(weak_idx={3})
    pts = P.parse_frame_ex(ct, n, body)
    assert pts[3][1] == 1500 + 3      # 保留距离，仅标志位仍为 True
    assert pts[3][3] is True


def test_parse_frame_backward_compatible():
    """旧的 3 元组接口不能变（官方回归测试依赖它）"""
    ct, n, body = _mk_frame(weak_idx={5})
    pts = P.parse_frame(ct, n, body)
    assert pts is not None
    assert all(len(p) == 3 for p in pts)
    assert pts[5][1] is None          # 弱回波同样被丢
    assert pts[6][1] == 1500 + 6


# ---------- 2. 扇区稳健统计 ----------

def test_sector_robust_ignores_single_spike():
    """20 个 2.0m 的点 + 1 个 0.2m 的错点 → 前扇区仍应是 2.0m"""
    pairs = [(350.0 + i * 0.9, 2000) for i in range(20)]
    pairs.append((10.0, 200))                      # 单点噪声，落在前扇区
    st = P.scan_stats(_mk_scan(pairs))
    assert st["front"][1] == 21                    # 21 个点都在前扇区
    assert st["front"][0] == 2.0                   # 第 2 小 = 2000mm


def test_sector_sparse_falls_back_to_min():
    """只有 2 个点时无法判断谁是噪点 → 退回取最小（宁可保守）"""
    st = P.scan_stats(_mk_scan([(350.0, 2000), (5.0, 200)]))
    assert st["front"][1] == 2
    assert st["front"][0] == 0.2


def test_sector_k1_is_plain_min(monkeypatch):
    """k=1 时退化成原来的 min()，老行为可复现"""
    monkeypatch.setattr(P, "SECTOR_ROBUST_K", 1)
    pairs = [(350.0 + i * 0.9, 2000) for i in range(20)]
    pairs.append((10.0, 200))
    st = P.scan_stats(_mk_scan(pairs))
    assert st["front"][0] == 0.2


def test_real_obstacle_still_detected():
    """真障碍给出一片点（不是孤立单点）时必须照常检出"""
    pairs = [(350.0 + i * 0.9, 300) for i in range(15)]   # 15 个点都贴脸
    st = P.scan_stats(_mk_scan(pairs))
    assert st["front"][0] == 0.3


# ---------- 3. 单遍统计与旧实现一致 ----------

def test_scan_stats_matches_sector_min():
    rnd = random.Random(20261024)
    for _ in range(50):
        scan = [(rnd.uniform(0, 360), rnd.choice([None, rnd.randint(60, 12000)]), 200, False)
                for _ in range(400)]
        st = P.scan_stats(scan)
        for name, sec in (("front", P.SECTOR_FRONT),
                          ("left", P.SECTOR_LEFT),
                          ("right", P.SECTOR_RIGHT)):
            assert st[name][0] == P.sector_min(scan, sec), name


def test_unused_angles_ignored():
    """90~270 / 330~345 / 15~30 这些用不到的角度不参与任何扇区"""
    st = P.scan_stats(_mk_scan([(100.0, 300), (200.0, 300), (335.0, 300)]))
    assert st["front"][0] is None
    assert st["left"][0] is None
    assert st["right"][0] is None


def test_no_echo_gives_none():
    st = P.scan_stats(_mk_scan([(350.0, None), (0.0, None)]))
    assert st["front"] == (None, 0)
