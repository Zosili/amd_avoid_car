# -*- coding: utf-8 -*-
"""五条融合规则 + 协议校验的单元测试"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import parse_vision, make_radar_json, radar_is_fresh  # noqa: E402
from fusion.machine import DecisionMachine                          # noqa: E402
from sim.scenarios import SCENARIOS, S, R, V                        # noqa: E402
from tests.drive import drive                                          # noqa: E402


def last(scenario):
    return drive(DecisionMachine(), SCENARIOS[scenario])[-1]


def test_clear_road_forward():
    t, rule, action, why = last("clear_road")
    assert rule == "OK" and action == "FORWARD"


def test_r1_vision_plus_radar_danger_stop():
    t, rule, action, why = last("vision_right_radar_danger")
    assert rule == "R1" and action == "STOP"


def test_r2_vision_obstacle_radar_none_turns_away():
    t, rule, action, why = last("vision_left_radar_none")
    assert rule == "R2" and action == "RIGHT"


def test_r2_center_obstacle_stop():
    t, rule, action, why = last("vision_center_radar_none")
    assert rule == "R2" and action == "STOP"


def test_r3_single_spike_no_stop():
    seq = drive(DecisionMachine(), SCENARIOS["radar_single_spike"])
    assert not any(a == "STOP" for _, _, a, _ in seq)
    assert seq[0][1] == "R3" and seq[0][2] == "SLOW"      # 单帧只减速确认


def test_r3_consecutive_frames_stop():
    seq = drive(DecisionMachine(), SCENARIOS["front_close_radar_only"])
    assert seq[0][2] == "SLOW"
    assert seq[2][1] == "R3" and seq[2][2] == "STOP"     # 第3帧停


def test_r5_radar_timeout_stop():
    seq = drive(DecisionMachine(), SCENARIOS["radar_timeout"])
    stops = [x for x in seq if x[1] == "R5" and x[2] == "STOP"]
    assert stops, seq


def test_r5_vision_stale_stop():
    seq = drive(DecisionMachine(), SCENARIOS["vision_stale"])
    stops = [x for x in seq if x[1] == "R5" and x[2] == "STOP"]
    assert stops, seq


def test_r5_fault_stop():
    seq = drive(DecisionMachine(), SCENARIOS["fault"])
    assert seq[1][1] == "R5" and seq[1][2] == "STOP"


def test_r4_turn_right_space():
    t, rule, action, why = last("turn_right_space")
    assert rule == "R4" and action == "RIGHT"


def test_r4_no_passable_side_stop():
    t, rule, action, why = last("no_passable_side")
    assert rule == "R4" and action == "STOP"


def test_r4_warn_far_turn_not_stop():
    t, rule, action, why = last("warn_far_turn")
    assert rule == "R4" and action == "LEFT"


# ---------- 抗误检机制（2026-10-24 新增）----------

def test_r2_single_frame_noise_not_turn():
    """视觉单帧误检：只减速确认，绝不转向（转向是危险动作）"""
    seq = drive(DecisionMachine(), S(
        {"radar": R(front=None), "vision": V(obstacle=True, cx=0.2)},
        *[{"radar": R(front=None), "vision": V()} for _ in range(5)]))
    assert seq[0][1] == "R2" and seq[0][2] == "SLOW", seq[0]
    assert not any(a == "RIGHT" for _, _, a, _ in seq)


def test_r2_low_confidence_never_turns():
    """置信度低于 vision_single_conf：连续 10 帧也只减速，不转向"""
    seq = drive(DecisionMachine(), S(
        *[{"radar": R(front=None), "vision": V(obstacle=True, cx=0.2, conf=0.4)}
          for _ in range(10)]))
    assert all(r == "R2" for _, r, _, _ in seq)
    assert all(a == "SLOW" for _, _, a, _ in seq)


def test_side_single_frame_noise_not_blocked():
    """侧向单帧低读数：不判堵死，先观察（否则一次噪声就误停）"""
    frames = [{"radar": R(front=1.0, left=0.3, right=0.3), "vision": V()}]
    frames += [{"radar": R(front=1.0, left=1.5, right=1.5), "vision": V()}
               for _ in range(5)]
    seq = drive(DecisionMachine(), S(*frames))
    assert seq[0][2] != "STOP", f"单帧噪声导致误停: {seq[0]}"


def test_side_persistent_blocked_stops():
    """侧向持续堵死（连续 >= side_confirm_frames 帧）：才判两侧不通"""
    seq = drive(DecisionMachine(), S(
        *[{"radar": R(front=1.0, left=0.3, right=0.3), "vision": V()}
          for _ in range(6)]))
    assert seq[-1][1] == "R4" and seq[-1][2] == "STOP", seq[-1]


# ---------- 协议校验 ----------

def test_parse_vision_rejects_garbage():
    assert parse_vision({"version": 2}) is None
    assert parse_vision({"version": 1, "obstacle": "yes", "timestamp": 1}) is None
    assert parse_vision({"version": 1, "timestamp": 1, "obstacle": True}) is None
    bad = {"version": 1, "timestamp": 1, "obstacle": True,
           "nearest": {"class": "p", "confidence": 0.9, "cx": 1.5, "cy": 0, "width": 0, "height": 0}}
    assert parse_vision(bad) is None          # cx 越界
    ok = {"version": 1, "timestamp": 1, "obstacle": False}
    assert parse_vision(ok) == {"version": 1, "timestamp": 1, "obstacle": False}


def test_radar_is_fresh_boundary():
    rj = make_radar_json(1.0, 1.0, 1.0, t=1000)
    assert radar_is_fresh(rj, 1200) is True
    assert radar_is_fresh(rj, 1201) is False
