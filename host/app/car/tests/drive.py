# -*- coding: utf-8 -*-
"""测试驱动：把场景脚本逐帧喂给状态机（虚拟时钟）"""


def drive(machine, frames, t0=1_000_000):
    """返回 [(t, rule, action, why)]，最后一帧即最终决策"""
    radar, vision, fault = None, None, False
    out = []
    for ft, rj, vj, ff in frames:
        now = t0 + ft
        if rj is not None:
            radar = dict(rj, t=now)
        if vj is not None:
            import copy
            from protocol import parse_vision
            v = parse_vision(copy.deepcopy(vj))
            if v is None:
                raise AssertionError(f"scenario vision invalid at t={ft}")
            v["t"] = now
            vision = v
        fault = ff
        ctrl, rule, why = machine.step(radar, vision, fault, now)
        out.append((ft, rule, ctrl["action"], why))
        if ff:
            fault = False
    return out
