# -*- coding: utf-8 -*-
"""
run_integration_demo.py — 三方代码联调（纯软件，不需要任何硬件）
================================================================
把三个人的代码在 PC 上串成一条完整链路：

    欧阳的 YOLO 输出（像素框）
        ↓ contract.detections_to_vision()
    郑的 Vision JSON（归一化）
        ↓ + 雷达 JSON
    郑的 DecisionMachine.step()
        ↓ control{action,speed,steering}
    contract.control_to_diff()
        ↓
    徐的电机指令{pwm_l,pwm_r,in1..in4}

**这个脚本的唯一目的：在没有板子、没有雷达、没有摄像头的情况下，
把三方接口的全部接缝暴露出来。** 任何一处语义不一致，这里立刻看得见。

跑法：
    python run_integration_demo.py            # 全部场景
    python run_integration_demo.py -v         # 带完整 JSON 打印
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

import protocol                                    # noqa: E402
from fusion.machine import DecisionMachine         # noqa: E402
from contract import control_to_diff, detections_to_vision, CONTRACT_VERSION  # noqa: E402

W, H = 640, 480

# 每个场景：(说明, 雷达三距离(m), YOLO框(像素), 类别id, 置信度, 视觉时间偏移ms, fault)
#   雷达 None = 该方向无回波
#   视觉偏移 <0 表示"过期帧"（模拟视觉链路卡住）
SCENARIOS = [
    ("1 畅通直行", "前方无回波，视觉干净",
     dict(front=None, left=2.00, right=2.00),
     [], [], [], 0, False),

    ("2 前方0.4m有人", "视觉+雷达双确认 → 应 R1 停",
     dict(front=0.40, left=1.80, right=2.10),
     [[280, 180, 400, 460]], [0], [0.88], 0, False),

    ("3 左侧有人雷达无回波", "视觉单源 → 应 R2 向右避让",
     dict(front=None, left=2.00, right=2.00),
     [[60, 200, 190, 450]], [0], [0.81], 0, False),

    ("4 前方0.8m有物视觉未见", "雷达单源 → 应 R3 减速确认",
     dict(front=0.80, left=1.50, right=1.50),
     [], [], [], 0, False),

    ("5 前方0.5m有人但左侧堵死", "视觉居中 + 雷达危险 → R1 停（不转向）",
     dict(front=0.45, left=0.30, right=1.90),
     [[260, 190, 390, 470]], [0], [0.92], 0, False),

    ("6 视觉链路卡死400ms", "视觉过期 → 应 R5 无条件停",
     dict(front=None, left=2.00, right=2.00),
     [[280, 180, 400, 460]], [0], [0.88], -400, False),

    ("7 雷达前向1.0m警告区", "在 warn/danger 之间 → R4 找空档转向",
     dict(front=1.00, left=1.60, right=1.60),
     [], [], [], 0, False),
]


def run(verbose=False):
    print(f"三方联调 demo  |  contract v{CONTRACT_VERSION}")
    print("=" * 74)
    print("每场景连续跑 5 帧（500ms @10Hz）—— R2/R3/侧向都有连续确认，单帧看不出真实行为\n")

    for name, desc, rd, boxes, cls, scores, vis_off, fault in SCENARIOS:
        machine = DecisionMachine()          # 每个场景独立实例（清空确认计数）
        now0 = protocol.now_ms()
        last = None

        # 连续 5 帧，模拟真实 10Hz 决策循环
        for k in range(5):
            now = now0 + k * 100

            radar = protocol.make_radar_json(rd["front"], rd["left"], rd["right"], t=now)

            if boxes:
                vision = detections_to_vision(boxes, cls, scores, W, H,
                                              class_names=["person", "car", "chair"],
                                              timestamp=now + vis_off)
            else:
                vision = {"version": 1, "timestamp": now + vis_off, "obstacle": False}

            # 郑的校验器把门（模拟 vision_provider.push 的过滤）
            checked = protocol.parse_vision(vision)
            if checked is None:
                print(f"\n【{name}】{desc}")
                print("  ⚠ Vision JSON 未通过郑的 schema 校验 —— 欧阳的字段有问题")
                last = None
                break
            # ⚠ parse_vision 只返回协议字段，t 是 provider 收到时刻打的内部时间戳
            #   （见 car/vision/vision_provider.py::push 里 v["t"] = now_ms()）
            checked["t"] = now + vis_off

            ctrl, rule, why = machine.step(radar, checked, fault=fault, now=now)
            last = (radar, checked, ctrl, rule, why)

        if last is None:
            continue
        radar, checked, ctrl, rule, why = last
        motor = control_to_diff(ctrl)

        print(f"\n【{name}】{desc}")
        f = lambda v: "无回波" if v is None else f"{v:.2f}m"    # noqa: E731
        print(f"  [雷达] 前={f(rd['front'])}  左={f(rd['left'])}  右={f(rd['right'])}")
        if boxes:
            n = checked.get("nearest", {})
            print(f"  [视觉] {n.get('class')} conf={n.get('confidence'):.2f} "
                  f"cx={n.get('cx'):.2f} cy={n.get('cy'):.2f} "
                  f"w={n.get('width'):.2f} h={n.get('height'):.2f} "
                  f"→ {protocol.vision_side(checked)}")
        else:
            print("  [视觉] 无目标")
        print(f"  [决策] {rule:3s} {ctrl['action']:8s} speed={ctrl['speed']:3d} "
              f"steer={ctrl['steering']:3d}   {why}")
        print(f"  [电机] pwm_l={motor['pwm_l']:5d}  pwm_r={motor['pwm_r']:5d}   "
              f"in=({motor['in1']}{motor['in2']}|{motor['in3']}{motor['in4']})   {motor['why']}")
        if verbose:
            print("  [原始] control=" + json.dumps(ctrl, ensure_ascii=False))

    print("\n" + "=" * 74)
    print("全部场景跑完。任何一处 [决策] 或 [电机] 不符合预期，就是接缝问题。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    run(ap.parse_args().verbose)
