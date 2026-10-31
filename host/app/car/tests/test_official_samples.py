# -*- coding: utf-8 -*-
"""
tests/test_official_samples.py - 官方数据回归测试（协议定案证据链，自动生成）

数据来源（全部官方一手材料，非我们抓包）：
  1. COIN-D6数据.txt：官方完整串口会话日志（含 AA 55 F0 0F 启动 + ACK），前两帧
  2. calibration.py：官方校验脚本自带的两个样本包
期望值 = 官方三源（PDF 位打包公式 / STM32 例程 / ROS2 SDK + calibration.py 校验算法）
交叉验证后的解析器产出。改解析公式必须让本文件继续全绿。
"""
from radar import d6_protocol as P

FRAMES = [
    "aa550019a70def194579d09a1b94c61a74061ad4e618c46218f8221a248f1b346b1b9c861ba4a519cca51900fa193c661a80e61aa0561bc4de1bdc6a1cf8021d08931d0c2b1e20d31e247f1f283720ac282cbc502d",
    "aa5500195170177b7a415c92053c9a0538b20528e6051802060c2606f03906e05106cc8506c0bd06b4d506a01d07a44507a49107ace507ac1d089849087c710890c108b02909c8ea0918270a183f0a205f0a187f0a",
    "aa5500197d003b0b1f458887027c8b02849302849702809702749702709f026c9f0268a3026cb3026cbb0264bf0264cb026cd30258d7024cdf024ce70240f3023cff02480f033413033023032c2f03243b03004703",
    "aa550019ad0b8716f178e85203e46a03c47a03a48e0374b2038cad03b80000ec0000a09c1c5cf41f94142044651c58e21d18ef1d381b1e74e61c146a17948216a88a15b086149066136c7612d00e1414b713d89e12",
]  # [0,1]=官方会话日志前两帧, [2,3]=官方校准脚本样本包

# (点数, 首点(角,距,强), 末点(角,距,强), 无效点数)
EXPECT = [
    (25, (27.296875, 1766, 180), (51.859375, 2900, 47), 0),
    (25, (224.625000, 356, 151), (246.171875, 671, 198), 0),
    (25, (0.968750, 161, 226), (22.453125, 209, 192), 0),
    (25, (23.343750, 212, 186), (45.046875, 1191, 182), 2),
]


def _parse(hexstr):
    f = bytes.fromhex(hexstr)
    assert f[0] == 0xAA and f[1] == 0x55
    return P.parse_frame(f[2], f[3], f[4:])


def test_official_samples_parse():
    for i, hx in enumerate(FRAMES):
        pts = _parse(hx)
        assert pts is not None, "官方帧%d 官方CS校验未通过" % i
        n, p0, p1, ninv = EXPECT[i]
        assert len(pts) == n
        assert abs(pts[0][0] - p0[0]) < 1e-6 and pts[0][1] == p0[1] and pts[0][2] == p0[2]
        assert abs(pts[-1][0] - p1[0]) < 1e-6 and pts[-1][1] == p1[1] and pts[-1][2] == p1[2]
        assert sum(1 for p in pts if p[1] is None) == ninv


def test_official_checksum_matches_script_formula():
    """与官方 calibration.py 公式双实现一致，且与帧内 CS 相等"""
    def cs_script(f):
        c = ((f[1] << 8) | f[0]) ^ ((f[3] << 8) | f[2]) ^ ((f[5] << 8) | f[4]) ^ ((f[7] << 8) | f[6])
        for k in range(f[3]):
            sl, s2, sh = f[10 + 3 * k], f[11 + 3 * k], f[12 + 3 * k]
            c ^= (sh << 8) | s2
            c ^= sl
        return c
    for hx in FRAMES:
        f = bytes.fromhex(hx)
        assert cs_script(f) == P.checksum(f[2], f[3], f[4:])
        assert cs_script(f) == (f[8] | f[9] << 8)


def test_angles_monotonic_step_plausible():
    """角度必须单调递增且步进在 D6 物理范围内（0.5~1.1°）。
    官方日志实测每点中位 0.8916°（≈400点/圈）；单帧跨度受丢点/量化影响
    可有 4~25° 波动（官方日志 min 4.28° max 24.56°），故不定死 0.9。"""
    for i in range(len(FRAMES)):
        pts = _parse(FRAMES[i])
        steps = [pts[j + 1][0] - pts[j][0] for j in range(len(pts) - 1)]
        assert all(s > 0 for s in steps), ("第%d帧角度非单调" % i, steps)
        assert all(0.5 <= s <= 1.1 for s in steps), ("第%d帧步进越界" % i, steps)
