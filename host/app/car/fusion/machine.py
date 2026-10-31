# -*- coding: utf-8 -*-
"""
fusion/machine.py — 融合决策状态机（郑的核心交付物）
五状态：FORWARD / SLOW / LEFT / RIGHT / STOP
五条规则（每一步返回触发的规则号，写进日志 why 字段）：
  R1 双确认加险：视觉有障碍 + 雷达前向危险 → STOP
  R2 视觉优先  ：视觉有障碍 + 雷达前向无回波 → 仍按视觉保守动作（不停在"雷达没数据就放行"）
  R3 雷达单源谨慎：视觉无障碍 + 雷达前向危险 → 减速确认，连续 N 帧才 STOP；绝不凭单次回波转向
  R4 转向决策  ：方向 = 视觉目标方位 + 左右可通行空间综合；两侧都不通 → STOP
  R5 失效即停  ：fault / 雷达超时 / 视觉过期 → 无条件 STOP
原则：雷达没检测到 ≠ 没有障碍；视觉为主，雷达为辅。

抗误检机制（2026-10-24 新增）：
  · 分扇区确认：front/left/right 各自独立计数，连续 N 帧才认定。
    解决"单帧噪声让车误停"和"不同方向随机噪声被当成同一个障碍"。
  · R2 单源转向需置信度 ≥ vision_single_conf 且连续 r2_confirm_frames 帧。
    停车是安全动作，不等确认立即执行；转向是危险动作，必须确认。
  · 侧向"堵死"同样要连续 side_confirm_frames 帧，只闪一帧按"观察中"处理。
"""
import protocol


class DecisionMachine:
    def __init__(self, cfg=None):
        self.cfg = dict(protocol.CFG, **(cfg or {}))
        self.state = "FORWARD"
        self.last_rule = None
        # 分扇区连续确认计数：每个方向独立，互不干扰
        self.confirm = {"front": 0, "left": 0, "right": 0}
        # R2 视觉单源转向的连续确认计数
        self.vision_confirm = 0

    # ---- 内部工具 ----
    def _turn(self, direction, rule, why):
        self.state = direction
        self.last_rule = rule
        return protocol.make_control(direction), rule, why

    def _stop(self, rule, why):
        self.state = "STOP"
        self.last_rule = rule
        return protocol.make_control("STOP"), rule, why

    def _slow(self, rule, why):
        self.state = "SLOW"
        self.last_rule = rule
        return protocol.make_control("SLOW"), rule, why

    # ---- 主入口：每次决策循环调一次 ----
    def step(self, radar, vision, fault=False, now=None):
        """
        radar : Radar JSON dict（带 t）或 None（从未收到/已判失效）
        vision: 合法 vision dict（带 t）或 None
        返回 (control_dict, rule_id, why)
        """
        now = protocol.now_ms() if now is None else now
        c = self.cfg

        # ===== R5 失效即停（最高优先级） =====
        if fault:
            return self._stop("R5", "status fault=true")
        if radar is None:
            return self._stop("R5", "no radar frame ever")
        if not protocol.radar_is_fresh(radar, now, c["radar_timeout_ms"]):
            return self._stop("R5", f"radar stale >{c['radar_timeout_ms']}ms")
        if vision is None:
            return self._stop("R5", "no vision frame ever")
        if now - vision.get("t", 0) > c["vision_timeout_ms"]:
            return self._stop("R5", f"vision stale >{c['vision_timeout_ms']}ms")

        front = radar.get("front_distance")
        left = radar.get("left_distance")
        right = radar.get("right_distance")

        # ===== 分扇区连续确认计数（每帧都更新，与后面走哪条规则无关）=====
        self.confirm["front"] = (self.confirm["front"] + 1
                                 if (front is not None and front < c["danger_m"]) else 0)
        self.confirm["left"] = (self.confirm["left"] + 1
                                if (left is not None and left <= c["passable_m"]) else 0)
        self.confirm["right"] = (self.confirm["right"] + 1
                                 if (right is not None and right <= c["passable_m"]) else 0)

        front_danger = front is not None and front < c["danger_m"]
        front_warn = front is not None and front < c["warn_m"]

        # 侧向可通行判定：距离够 → 通；距离不够但只闪了不足 N 帧 → 先按通处理（观察中）
        left_ok = (left is not None) and (left > c["passable_m"]
                                          or self.confirm["left"] < c["side_confirm_frames"])
        right_ok = (right is not None) and (right > c["passable_m"]
                                            or self.confirm["right"] < c["side_confirm_frames"])

        v_obs = bool(vision.get("obstacle"))
        v_side = protocol.vision_side(vision) if v_obs else None

        # ===== R1 视觉+雷达双确认 =====
        if v_obs and front_danger:
            self.vision_confirm = 0
            return self._stop("R1", f"vision obs({vision['nearest'].get('class')}) "
                                    f"+ radar front {front:.2f}m")

        # ===== R2 视觉有障碍但雷达前向无回波 → 按视觉保守动作 =====
        if v_obs and front is None:
            n = vision["nearest"]
            conf = float(n.get("confidence", 0.0))

            # 正中障碍：停车是安全动作，立即执行，不等确认
            if v_side == "center":
                self.vision_confirm = 0
                return self._stop("R2", f"vision center obs({n.get('class')}), radar front no echo")

            # 置信度太低：不敢只凭视觉转向，先减速
            if conf < c["vision_single_conf"]:
                self.vision_confirm = 0
                return self._slow("R2", f"vision {v_side} conf={conf:.2f}"
                                        f"<{c['vision_single_conf']} too weak to turn")

            # 转向是危险动作：连续确认 N 帧才执行
            self.vision_confirm += 1
            if self.vision_confirm < c["r2_confirm_frames"]:
                return self._slow("R2", f"vision {v_side} conf={conf:.2f} "
                                        f"confirm {self.vision_confirm}/{c['r2_confirm_frames']}")

            away = "RIGHT" if v_side == "left" else "LEFT"
            return self._turn(away, "R2", f"vision obs {v_side} conf={conf:.2f} "
                                          f"x{self.vision_confirm}, radar no echo, turn away")

        # 视觉没触发 R2 → 清空转向确认计数（障碍消失后再出现要重新确认）
        self.vision_confirm = 0

        # ===== R3 雷达单源：连续确认才升级 =====
        if not v_obs and front_danger:
            if self.confirm["front"] >= c["r3_confirm_frames"]:
                return self._stop("R3", f"radar front {front:.2f}m "
                                        f"x{self.confirm['front']} frames, no vision obs")
            return self._slow("R3", f"radar front {front:.2f}m "
                                    f"confirm {self.confirm['front']}/{c['r3_confirm_frames']}")

        # ===== R4 转向决策 =====
        if v_obs or front_warn:
            want = None
            if v_obs:
                want = "RIGHT" if v_side == "left" else ("LEFT" if v_side == "right" else None)
            # 视觉方向与侧向空间冲突 → 不信视觉的方向
            if want == "RIGHT" and not right_ok:
                want = None
            if want == "LEFT" and not left_ok:
                want = None
            if want is None:
                if left_ok and not right_ok:
                    want = "LEFT"
                elif right_ok and not left_ok:
                    want = "RIGHT"
                elif left_ok and right_ok:
                    want = "LEFT" if left >= right else "RIGHT"
                else:
                    return self._stop("R4", "no passable side")
            return self._turn(want, "R4",
                              f"vision={v_side or '-'} L={left} R={right} -> {want}")

        # ===== 全清 =====
        self.state = "FORWARD"
        self.last_rule = "OK"
        return protocol.make_control("FORWARD"), "OK", f"front={front} clear"
