# -*- coding: utf-8 -*-
"""_selftest_net.py —— 无窗口验证 radar_map_view 的 net 数据源

为什么单独写：真跑窗口会弹 matplotlib 窗口占用桌面，测试没必要。
这里只验数据通路：连板上监控端口 → 解码 pts → 12 扇区/规则/动作都到位。

用法（需先起 board_main + mock_motor）：
    set MPLBACKEND=Agg
    python _selftest_net.py 127.0.0.1:5002
"""
import os
import sys
import time

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from radar_map_view import RadarSource, MapView   # noqa: E402


def main(addr="127.0.0.1:5002"):
    src = RadarSource("net", addr)
    print(f"[net] 正在连 {addr} …")
    t0 = time.time()
    while time.time() - t0 < 8:
        # 至少 8 帧、且已跨过 1s 让 hz 算出来
        if src.frames_ok >= 8 and src.hz > 0:
            break
        time.sleep(0.2)
    ok = True
    if src.remote is None:
        print("[FAIL] 8s 内没收到板上监控帧")
        return 1
    m = src.remote
    print(f"[1] 收到 {src.frames_ok} 帧，缓冲 {len(src.buf)} 点，{src.hz:.0f} 帧/s")
    need = ("sectors", "rule", "action", "radar", "pts", "vision")
    miss = [k for k in need if k not in m]
    if miss:
        print(f"    [FAIL] 缺字段 {miss}")
        ok = False
    s12 = m.get("sectors") or []
    if len(s12) != 12:
        print(f"    [FAIL] sectors 应有 12 个，实际 {len(s12)}")
        ok = False
    else:
        got = sum(1 for v in s12 if v[0] is not None)
        print(f"[2] 12 扇区里有数据 {got}/12 个；rule={m['rule']} action={m['action']}")
        print("    " + "  ".join(
            f"s{k}={'--' if v[0] is None else '%.2f' % v[0]}" for k, v in enumerate(s12)))
    # 用收到的点重算 12 扇区，应与板上的一致（同一套公式的交叉验证）
    s12_local = MapView._sector12(src.buf)
    diff = 0
    for a, b in zip(s12, s12_local):
        if (a[0] is None) != (b[0] is None):
            diff += 1
        elif a[0] is not None and abs(a[0] - b[0]) > 0.02:
            diff += 1
    print(f"[3] 本地重算 vs 板上：{12 - diff}/12 一致（差异 {diff}）")
    if diff > 3:
        print("    [FAIL] 差异过大，pts 解码口径可能不一致")
        ok = False
    v = m.get("vision") or {}
    print(f"[4] 视觉={v.get('side') if v.get('obstacle') else '空'} "
          f"链路={'断' if m.get('fault') else '通'} "
          f"status={'有' if m.get('status') else '无'}")
    src.alive = False
    print("[PASS] net 数据源正常" if ok else "[FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:5002"))
