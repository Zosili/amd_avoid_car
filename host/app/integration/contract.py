# -*- coding: utf-8 -*-
"""
contract.py — 三人接口契约（唯一权威定义）
==========================================
这个文件是欧阳 / 徐 / 郑 三人代码之间的"接缝"。改这里必须三方一起改，
并且重跑 `python run_integration_demo.py` 全部场景。

【为什么需要这个文件】
三人的代码原本各说各话：
  · 郑  : 输出 control{action, speed, steering} —— 这是**舵机（阿克曼）**语义
  · 徐  : 输入 pwm_l / pwm_r + in1..in4      —— 这是**差速**语义
  · 欧阳: 输出 class_id + score              —— 这是**分类**语义
  · 郑收: 需要 {class, confidence, cx, cy, width, height} —— **检测框**语义

三处语义都不一致。本文件负责把这三处翻译统一。

--------------------------------------------------------------------
一、郑 → 徐：control → 差速 PWM
--------------------------------------------------------------------
郑的 steering 定义（car/protocol.py）：
    steer_left=135  steer_straight=90  steer_right=45
  → 90 是中位，>90 左转，<90 右转

差速车物理事实：左转 = 右轮快、左轮慢（或反转）

映射（TURN_GAIN=1.0 时）：
    k = (steering - 90) / 45.0        # k>0 左转，k<0 右转，范围约 -1~+1
    左转(k>=0): pwm_r = base,  pwm_l = base * (1 - TURN_GAIN*k)
    右转(k<0) : pwm_l = base,  pwm_r = base * (1 + TURN_GAIN*k)
  → 保证外侧轮不超速，内侧轮最多停转（k=±1 时内侧=0）

    k=0     : pwm_l = pwm_r = base        直行
    k=+1    : pwm_l = 0,      pwm_r = base  绕左轮原地转（左转极限）
    k=-1    : pwm_l = base,   pwm_r = 0    绕右轮原地转（右转极限）

方向引脚（in1..in4）按 TB6612/L298N 通用约定：
    pwm > 0 → in1=1,in2=0（正转）；pwm < 0 → in1=0,in2=1（反转）；pwm=0 → in1=in2=0（惰行）
⚠️ 徐的实际接线定义必须核对，若反了只改 DIR_LOGIC 一处。

--------------------------------------------------------------------
二、欧阳 → 郑：检测结果 → Vision JSON
--------------------------------------------------------------------
郑的 schema（car/protocol.py::parse_vision 强制校验）：
    {"version":1, "timestamp":int_ms, "obstacle":bool,
     "nearest":{class:str, confidence:0~1, cx:0~1, cy:0~1, width:0~1, height:0~1}}
  → cx/cy/w/h **必须是归一化到 0~1 的比例**，不是像素！

"nearest" 选取规则：取 cy 最大者（画面里最靠下 = 离车最近）。
  理由：地面视角下越近的物体在画面中越靠下，这比"置信度最高"更符合避障直觉。

--------------------------------------------------------------------
三、版本
--------------------------------------------------------------------
改任何一条规则，把 CONTRACT_VERSION +1，并在三方群里说一声。
"""

CONTRACT_VERSION = 2

# ============ 视觉频率契约（实测踩过的坑，别忽略） ============
#
# 郑的 DecisionMachine 里 vision_timeout_ms=300：视觉超过 300ms 没更新就判失效 → R5 强制停车。
# 而欧阳现在的推理循环是 `now - last_infer >= 0.5`，即 **2Hz**。
#
# 实测后果（integration 里跑过）：2Hz 发送 + 300ms 超时 → **70% 的决策周期在停车**，
# 车会一顿一顿地走，看起来像"程序抽风"，其实是频率没对齐。
#
# 约束公式：vision_timeout_ms >= 2 * (1000 / 发送频率Hz)
#   （留两个周期的余量，容忍单帧丢失）
#
#   发送频率    最小 timeout
#   2 Hz  →  1000 ms
#   5 Hz  →   400 ms
#  10 Hz  →   200 ms   ← 郑现在的 300ms 对应要求 ≥ 6.7Hz
#
# 两条出路，二选一：
#   A. 欧阳把推理提到 ≥ 7Hz（推荐。RyzenAI NPU 跑 yolo11n 到 20~30FPS 没问题，
#      瓶颈在 UDP 收图 + RGB565 解码，可以优化）
#   B. 郑把 CFG["vision_timeout_ms"] 调到 1000（简单，但视觉真挂了要 1 秒才发现）
#
# 中间路线（不推荐）：PC 端 2Hz 推理但 10Hz 重复发送旧结果 —— 时间戳会骗人，
# 人走了车还在躲，属于"看起来能用"的陷阱。

VISION_HZ_MIN = 7.0                    # PC 侧发送频率下限
VISION_TIMEOUT_MS_MIN = int(2000 / VISION_HZ_MIN)   # 对应的最小超时 = 285ms


# ============ 郑 → 徐：差速映射 ============

PWM_MAX = 1000          # 徐的 PWM 满量程（⚠️ 待徐确认，若他内部用 0~100 就改这里）
TURN_GAIN = 0.6         # 转向强度。1.0=极限时内侧轮停转(原地急转，实车会甩尾)；
                        # 0.6=内侧轮降到 40%（平缓右偏）。⚠️ 必须实车标定后定死
DEADBAND_PWM = 20       # 小于它的值当 0（避免电机嗡嗡响不转）


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def control_to_diff(ctrl, pwm_max=None, turn_gain=None):
    """郑的 control dict → 徐的电机指令

    返回 dict:
        {"pwm_l": int(-pwm_max~+pwm_max), "pwm_r": int,
         "in1": 0/1, "in2": 0/1, "in3": 0/1, "in4": 0/1,
         "action": 原始动作, "why": 人读说明}
    带符号 pwm 表示方向；in1..in4 是同一信息给硬件用的电平形式。
    """
    pwm_max = PWM_MAX if pwm_max is None else pwm_max
    turn_gain = TURN_GAIN if turn_gain is None else turn_gain

    action = ctrl.get("action", "STOP")
    speed = float(ctrl.get("speed", 0))
    steering = float(ctrl.get("steering", 90))

    # STOP 直接双零，不走差速公式（否则 steering=90 会算出 base=0，结果一样但意图更明确）
    if action == "STOP" or speed <= 0:
        return {"pwm_l": 0, "pwm_r": 0, "in1": 0, "in2": 0, "in3": 0, "in4": 0,
                "action": action, "why": "STOP: 双轮断电惰行"}

    base = clamp(speed / 100.0 * pwm_max, 0, pwm_max)
    k = clamp((steering - 90.0) / 45.0, -1.0, 1.0)

    if k >= 0:                      # 左转：左轮减速
        pwm_r = base
        pwm_l = base * (1.0 - turn_gain * k)
    else:                           # 右转：右轮减速
        pwm_l = base
        pwm_r = base * (1.0 + turn_gain * k)

    pwm_l = clamp(int(round(pwm_l)), -pwm_max, pwm_max)
    pwm_r = clamp(int(round(pwm_r)), -pwm_max, pwm_max)
    if abs(pwm_l) < DEADBAND_PWM:
        pwm_l = 0
    if abs(pwm_r) < DEADBAND_PWM:
        pwm_r = 0

    def _dirs(p):
        if p > 0:
            return 1, 0
        if p < 0:
            return 0, 1
        return 0, 0

    in1, in2 = _dirs(pwm_l)
    in3, in4 = _dirs(pwm_r)

    if k > 0.05:
        why = f"左转 k={k:+.2f}: 左轮{pwm_l} 右轮{pwm_r}"
    elif k < -0.05:
        why = f"右转 k={k:+.2f}: 左轮{pwm_l} 右轮{pwm_r}"
    else:
        why = f"直行: 双轮{pwm_l}/{pwm_r}"

    return {"pwm_l": pwm_l, "pwm_r": pwm_r, "in1": in1, "in2": in2,
            "in3": in3, "in4": in4, "action": action, "why": why}


# ============ 欧阳 → 郑：检测结果归一化 ============

def detections_to_vision(boxes, classes, scores, img_w=640, img_h=480,
                         class_names=None, conf_th=0.25, timestamp=None):
    """欧阳的 YOLO 输出 → 郑的 Vision JSON

    boxes      : [[x1,y1,x2,y2], ...]  **像素坐标**（YOLO 后处理出来的原始框）
    classes    : [int, ...]            类别 id
    scores     : [float, ...]          置信度 0~1
    class_names: {id: "person"} 或 list；None 时用 "cls{id}"
    conf_th    : 低于它直接丢

    返回 dict（永远合法，obstacle=False 时无 nearest 键）
    """
    import time
    ts = int(time.time() * 1000) if timestamp is None else int(timestamp)

    cand = []
    for b, c, s in zip(boxes, classes, scores):
        s = float(s)
        if s < conf_th:
            continue
        x1, y1, x2, y2 = [float(v) for v in b]
        # 裁剪到画面内
        x1 = clamp(x1, 0.0, img_w)
        x2 = clamp(x2, 0.0, img_w)
        y1 = clamp(y1, 0.0, img_h)
        y2 = clamp(y2, 0.0, img_h)
        if x2 <= x1 or y2 <= y1:
            continue
        cand.append({
            "class": (class_names[c] if class_names and c < len(class_names)
                      else f"cls{int(c)}"),
            "confidence": clamp(s, 0.0, 1.0),
            "cx": clamp((x1 + x2) / 2.0 / img_w, 0.0, 1.0),
            "cy": clamp((y1 + y2) / 2.0 / img_h, 0.0, 1.0),
            "width": clamp((x2 - x1) / img_w, 0.0, 1.0),
            "height": clamp((y2 - y1) / img_h, 0.0, 1.0),
        })

    if not cand:
        return {"version": 1, "timestamp": ts, "obstacle": False}

    # 取画面最靠下的（离车最近）
    nearest = max(cand, key=lambda d: d["cy"])
    return {"version": 1, "timestamp": ts, "obstacle": True, "nearest": nearest}


def vision_is_valid(v):
    """直接复用郑的校验器，避免两份 schema 漂移"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "car"))
    import protocol
    return protocol.parse_vision(v) is not None


# ============ 自检 ============
if __name__ == "__main__":
    print(f"contract v{CONTRACT_VERSION}")
    print("\n--- 差速映射（base=600）---")
    for act, st in [("FORWARD", 90), ("LEFT", 135), ("RIGHT", 45),
                    ("LEFT", 110), ("RIGHT", 70), ("STOP", 90)]:
        c = {"action": act, "speed": 60 if act != "STOP" else 0, "steering": st}
        r = control_to_diff(c)
        print(f"  {act:8s} steer={st:3d} → pwm_l={r['pwm_l']:5d} pwm_r={r['pwm_r']:5d}  {r['why']}")

    print("\n--- 检测归一化（640x480，人在画面右下）---")
    v = detections_to_vision([[300, 200, 420, 440]], [0], [0.88],
                             class_names=["person"])
    print(f"  {v}")
    print(f"  郑的校验器: {'通过' if vision_is_valid(v) else '拒绝'}")
