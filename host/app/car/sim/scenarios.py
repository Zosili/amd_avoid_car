# -*- coding: utf-8 -*-
"""
sim/scenarios.py — 场景脚本（tests 与 run.py --source sim 共用）
每个场景 = [(t_ms, radar_json|None, vision_dict|None, fault), ...]，100ms 一帧
radar/vision 的 None 表示该帧无新数据（触发各自的过期/超时判定）
"""
from protocol import make_radar_json


def V(obstacle=False, cx=0.5, cls="person", conf=0.9, t=0):
    d = {"version": 1, "timestamp": t, "obstacle": obstacle}
    if obstacle:
        d["nearest"] = {"class": cls, "confidence": conf,
                        "cx": cx, "cy": 0.5, "width": 0.2, "height": 0.4}
    return d


def R(front=1.5, left=1.2, right=1.2, t=0):
    return make_radar_json(front, left, right, t=t)


def S(*frames):
    """帧 -> 场景（帧可省略 t，默认 100ms 步进）"""
    out = []
    t = 0
    for f in frames:
        out.append((f.get("t", t), f.get("radar"), f.get("vision"), f.get("fault", False)))
        t += 100
    return out


SCENARIOS = {
    # 1. 空旷直行
    "clear_road": S(*[{"radar": R(), "vision": V()} for _ in range(20)]),

    # 2. 正前方 0.4m 出现障碍 → 雷达单源：第1帧减速，第3帧停
    "front_close_radar_only": S(*[{"radar": R(front=0.4), "vision": V()} for _ in range(10)]),

    # 3. 雷达单次尖脉冲（1帧 front=0.4 后恢复正常）→ 不应 STOP
    "radar_single_spike": S({"radar": R(front=0.4), "vision": V()},
                            *[{"radar": R(), "vision": V()} for _ in range(9)]),

    # 4. 视觉看到左侧障碍 + 雷达前向无回波 → 应右转（R2）
    "vision_left_radar_none": S(*[{"radar": R(front=None), "vision": V(obstacle=True, cx=0.2)} for _ in range(10)]),

    # 5. 视觉正中有障碍 + 雷达无前向回波 → 停（R2）
    "vision_center_radar_none": S(*[{"radar": R(front=None), "vision": V(obstacle=True, cx=0.5)} for _ in range(10)]),

    # 6. 视觉右侧障碍 + 雷达前向 0.3m 危险 → 停（R1，最保守）
    "vision_right_radar_danger": S(*[{"radar": R(front=0.3), "vision": V(obstacle=True, cx=0.8)} for _ in range(10)]),

    # 7. 雷达第 500ms 起停止更新 → 200ms 超时后停（R5）
    "radar_timeout": S({"radar": R(), "vision": V()},
                       {"radar": R(t=400), "vision": V()},
                       *[{"radar": None, "vision": V()} for _ in range(15)]),

    # 8. 视觉第 500ms 起过期 → 300ms 后停（R5）
    "vision_stale": S({"radar": R(), "vision": V()},
                      {"radar": R(), "vision": V(t=400)},
                      *[{"radar": R(), "vision": None} for _ in range(15)]),

    # 9. fault 帧 → 立即停（R5）
    "fault": S({"radar": R(), "vision": V()},
               {"radar": R(), "vision": V(), "fault": True},
               *[{"radar": R(), "vision": V()} for _ in range(10)]),

    # 10. 左侧空间小右侧大 + 视觉建议右转 → 右转（R4 综合）
    "turn_right_space": S(*[{"radar": R(left=0.4, right=1.5), "vision": V(obstacle=True, cx=0.3)} for _ in range(10)]),

    # 11. 两侧都不可通行 → 停（R4）
    "no_passable_side": S(*[{"radar": R(left=0.3, right=0.3), "vision": V(obstacle=True, cx=0.3)} for _ in range(10)]),

    # 12. 远距警示：front=0.9（<warn）→ 综合转向而非停
    "warn_far_turn": S(*[{"radar": R(front=0.9, left=1.5, right=0.4), "vision": V()} for _ in range(10)]),
}
