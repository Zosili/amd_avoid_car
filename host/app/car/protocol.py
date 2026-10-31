# -*- coding: utf-8 -*-
"""
protocol.py — 全队统一的 JSON 消息协议（郑侧实现）
口径：
  · 距离单位：米（m）。无有效回波 = null，绝不用 0 代替
  · 时间戳：Unix 毫秒 int(time.time()*1000)
  · 动作集合：FORWARD / SLOW / LEFT / RIGHT / STOP（⏳ SLOW 去留待队内拍板）
  · speed 0~100；steering 0~180 舵机角度，90 = 中位（⏳ 左右映射待与徐对齐）
"""
import json
import time

ACTIONS = ("FORWARD", "SLOW", "LEFT", "RIGHT", "STOP")

# 阈值初值（⏳ 待队内拍板后写入 protocol.md）
CFG = {
    "danger_m": 0.50,        # 前向危险：小于它必须停/减速确认
    "warn_m": 1.20,          # 前向警惕：小于它降速
    "passable_m": 0.60,      # 侧向可通行阈值
    "radar_timeout_ms": 200,  # 雷达多久无新帧判失效
    "vision_timeout_ms": 300, # Vision JSON 多久过期
    "r3_confirm_frames": 3,   # 雷达单源报警需连续确认帧数
    # ---- 抗误检确认（2026-10-24 新增，解决"单帧噪声导致误停/乱转"）----
    "side_confirm_frames": 2,  # 侧向"堵死"需连续帧数；只闪一帧不算堵死
    "r2_confirm_frames": 2,    # R2 视觉单源转向需连续帧数（停车不等确认）
    "vision_single_conf": 0.60,  # 视觉单源允许转向的最低置信度，低于此只减速
    "speed_forward": 60,
    "speed_slow": 25,
    "speed_turn": 35,
    "steer_left": 135,
    "steer_right": 45,
    "steer_straight": 90,
}


def now_ms():
    return int(time.time() * 1000)


# ---------- Radar JSON（郑内部） ----------

def make_radar_json(front, left, right, valid=True, t=None):
    """front/left/right: 米(float) 或 None"""
    return {"valid": bool(valid), "front_distance": front,
            "left_distance": left, "right_distance": right,
            "t": t if t is not None else now_ms()}


def radar_is_fresh(rj, now, timeout_ms=None):
    timeout_ms = timeout_ms if timeout_ms is not None else CFG["radar_timeout_ms"]
    if not rj or not rj.get("valid"):
        return False
    return now - rj.get("t", 0) <= timeout_ms


# ---------- Vision JSON（欧阳 → 郑） ----------

def parse_vision(d):
    """校验并归一化 Vision JSON。不合格返回 None（调用方按"本帧无视觉"处理）。"""
    try:
        if not isinstance(d, dict):
            return None
        if d.get("version") != 1:
            return None
        if not isinstance(d.get("timestamp"), int):
            return None
        if not isinstance(d.get("obstacle"), bool):
            return None
        out = {"version": 1, "timestamp": d["timestamp"], "obstacle": d["obstacle"]}
        if d["obstacle"]:
            n = d.get("nearest")
            if not isinstance(n, dict):
                return None
            for k in ("cx", "cy", "width", "height"):
                v = n.get(k)
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
                    return None
            c = n.get("confidence")
            if not isinstance(c, (int, float)) or isinstance(c, bool) or not (0.0 <= c <= 1.0):
                return None
            if not isinstance(n.get("class"), str):
                return None
            out["nearest"] = {"class": n["class"], "confidence": float(c),
                              "cx": float(n["cx"]), "cy": float(n["cy"]),
                              "width": float(n["width"]), "height": float(n["height"])}
        return out
    except Exception:
        return None


def vision_side(v):
    """视觉目标在画面哪一侧：left / center / right（cx=画面水平坐标 0~1）"""
    n = v.get("nearest")
    if not n:
        return "center"
    cx = n["cx"]
    return "left" if cx < 0.45 else ("right" if cx > 0.55 else "center")


# ---------- Control JSON（郑 → 徐） ----------

_seq = 0

def next_seq():
    global _seq
    _seq = (_seq + 1) % 65536
    return _seq


def make_control(action, speed=None, steering=None, seq=None, t=None):
    if action not in ACTIONS:
        raise ValueError(f"bad action {action!r}")
    if speed is None:
        speed = {"FORWARD": CFG["speed_forward"], "SLOW": CFG["speed_slow"],
                 "LEFT": CFG["speed_turn"], "RIGHT": CFG["speed_turn"], "STOP": 0}[action]
    if steering is None:
        steering = {"FORWARD": CFG["steer_straight"], "SLOW": CFG["steer_straight"],
                    "LEFT": CFG["steer_left"], "RIGHT": CFG["steer_right"],
                    "STOP": CFG["steer_straight"]}[action]
    return {"version": 1, "seq": next_seq() if seq is None else seq,
            "timestamp": now_ms() if t is None else t,
            "action": action, "speed": int(speed), "steering": int(steering)}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
