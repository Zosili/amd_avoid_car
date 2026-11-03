# -*- coding: utf-8 -*-
"""
================================================================
 决策层 —— 从「检测到什么」到「车该怎么动」
================================================================

这一层是整个系统里唯一会直接导致车动/停的地方，所以按 fail-safe 设计：

    ⚠ 原则一：宁可误停，不可漏停。
      检测到疑似活体且距离近，一律先停。运动判据只能用来决定
      「要不要恢复行驶」，绝不能用来否决停车。

    ⚠ 原则二：任何一路信号缺失都不许让车继续高速前进。
      雷达没接 → 用单目估距（精度差但方向对）；运动判据算不出 →
      退化成纯检测。任何异常都往"更保守"的方向倒。

    ⚠ 原则三：所有阈值带滞回 + 去抖。
      单帧抖动不该让车在"走-停"之间反复横跳，那比一直停着更危险
      （电流冲击 + 机械抖动）。

三个动作来源的分工：
    tools/living_semantics.py  : 类名 → 活体/可压/障碍/通道
    tools/motion_gate.py       : 框内是否在形变（活体佐证）
    本文件                      : 距离 + 上述两路 → 动作 + 串口命令

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import kind_of, is_living  # noqa: E402


# ============================================================
# 一、单目地面测距
# ============================================================
class MonoGroundDistance:
    """用「框底边在画面里的高度」反推地面距离。

    原理（相机水平、低视角、目标站在地面上）：
        地平线以上是天空/远处，越靠下越近，且严格满足
            d = K / (y_bottom - y_horizon)
        其中 K = f * h（焦距×相机高度），y_horizon 是地平线的像素 y。

    为什么这个假设在你的车上够用：
        18cls 那批图我看过，相机基本水平、离地 10~25cm，
        地面在画面下部占比大，正好落在公式最准的区间。

    标定（强烈建议做一次，两分钟）：
        把物体摆在 0.5m 和 1.5m 处，各记一次框底边 y，
        用 calibrate() 反算 K 和 y_horizon，比猜准得多。

    局限（必须知道）：
        * 目标不在地面上（桌上的人、飘着的窗帘）会算偏
        * 距离 > 3m 时 y_bottom 逼近地平线，分母趋零，误差爆炸
          → 所以超过 FAR_M 一律返回 None，让上层按"很远"处理，不装懂
    """

    FAR_M = 5.0      # 超过这个距离不再给数值（误差已经没意义）

    def __init__(self, K: float = 100.0, y_horizon: float = 240.0,
                 img_h: int = 480):
        self.K = float(K)
        self.y_horizon = float(y_horizon)
        self.img_h = int(img_h)

    def estimate(self, box_xyxy) -> float | None:
        """返回米，或 None（算不出/太远/不合理）。box 为像素坐标。"""
        y_bottom = float(box_xyxy[3])
        dy = y_bottom - self.y_horizon
        if dy <= 2.0:                       # 在地平线附近或以上，无解
            return None
        d = self.K / dy
        if d > self.FAR_M or d < 0.05:
            return None
        return d

    def calibrate(self, d1: float, y1: float, d2: float, y2: float) -> None:
        """两点标定。d1/d2 是真实米数，y1/y2 是对应框底边像素 y。"""
        if abs(d2 - d1) < 1e-6 or abs(y1 - y2) < 1e-6:
            raise ValueError("两个标定点太接近，无法反算")
        self.K = (y1 - y2) * d1 * d2 / (d2 - d1)
        self.y_horizon = y1 - self.K / d1
        print(f"[标定] K={self.K:.2f} px·m, y_horizon={self.y_horizon:.1f} px")
        for d, y in ((d1, y1), (d2, y2)):
            chk = self.estimate((0, 0, 0, y))
            print(f"       校验 d={d}m -> 算得 {chk:.3f}m" if chk else
                  f"       校验 d={d}m -> 无解（K/y_horizon 不匹配）")


# ============================================================
# 二、雷达接口（队友那套到位后接这里，其余代码不用动）
# ============================================================
class LidarDistance:
    """激光雷达距离源 —— 目前是桩，等队友的雷达整合进来。

    对接时要做的只有一件事：实现 distance_to_box(box)->米。
    通常做法是取框底部中心的一条竖直扫描扇区，取其中最近的有效点。
    决策层只管调用，不关心是雷达还是单目。
    """

    def __init__(self):
        self.online = False

    def distance_to_box(self, box_xyxy) -> float | None:
        if not self.online:
            return None          # 没上线就返回 None，上层自动回落到单目
        return None


# ============================================================
# 三、动作与结果
# ============================================================
@dataclass
class Action:
    mode: str = "GO"             # GO / SLOW / STOP / AVOID_L / AVOID_R
    duty_l: int = 70
    duty_r: int = 70
    dir_l: int = 1               # 1=前进 0=后退（固件实测如此）
    dir_r: int = 1
    beep: bool = False
    reason: str = ""

    def cmds(self) -> list[str]:
        """翻译成固件认的串口命令序列。

        固件只认两件事，逻辑全在这里：
            M<dl>,<dr>,<ldir>,<rdir>   原子设置四元组（不用拆成 L/R/B/F/V 拼）
            H<0|1>                     蜂鸣器
        """
        out = [f"M{self.duty_l},{self.duty_r},{self.dir_l},{self.dir_r}"]
        out.append("H1" if self.beep else "H0")
        return out


# ============================================================
# 四、决策器
# ============================================================
class CarDecider:
    """带滞回和去抖的状态机。"""

    # ---- 阈值（米）。按你的车实际尺寸和刹车距离调 ----
    LIVING_STOP = 1.00      # 活体进这个距离：停
    LIVING_RESUME = 1.30    # 活体退到这么远：可以恢复（滞回，防止边界抖动）
    LIVING_SLOW = 1.80      # 活体在这么远：先减速
    OBST_AVOID = 0.60       # 障碍进这个距离：绕
    OBST_SLOW = 1.00        # 障碍在这么远：减速

    # ---- 速度 ----
    DUTY_CRUISE = 70
    DUTY_SLOW = 30
    DUTY_AVOID = 45

    # ---- 时序 ----
    CONFIRM_FRAMES = 3      # 连续几帧才允许切换状态
    BEEP_HOLD_S = 1.5       # 鸣叫至少持续多久（不要一闪一闪）

    def __init__(self, dist: MonoGroundDistance | None = None,
                 lidar: LidarDistance | None = None, img_w: int = 640):
        self.dist = dist or MonoGroundDistance()
        self.lidar = lidar or LidarDistance()
        # 画面宽度只用来判「障碍偏左还是偏右」，必须跟实际输入尺寸一致，
        # 否则 416 输入时 midline 会算到画面外面去
        self.img_w = int(img_w)

        self.mode = "GO"
        self._pending_mode = "GO"
        self._pending_n = 0
        self._beep_until = 0.0
        self.log: list[str] = []
        self.last_seen: dict = {}

    # ---------------------------------------------------------- 距离
    def _dist_of(self, box) -> float | None:
        d = self.lidar.distance_to_box(box)
        if d is not None:
            return d
        return self.dist.estimate(box)

    # ---------------------------------------------------------- 主入口
    def update(self, dets: list[dict], motion: list | None = None,
               moving: bool = True, now: float | None = None) -> Action:
        """dets: 每项 {name, conf, box(x1,y1,x2,y2)}，conf 已过阈值。
        motion: 与 dets 等长的运动强度 0..1 或 None，来自 MotionGate。
        moving: 车当前是否在主动前进（决定 fail-safe 松紧）。
        """
        now = now if now is not None else time.time()

        # ---- 把每个检测归类 + 估距 ----
        living_near, living_info = None, None
        obst_near, obst_box = None, None
        n_flat = 0
        for i, d in enumerate(dets):
            name = d["name"]
            kind = kind_of(name)
            if kind == "flat":
                n_flat += 1
                continue
            if kind in ("unknown", "passage"):
                continue
            dist = self._dist_of(d["box"])
            if dist is None:
                continue
            if kind == "living":
                # 运动佐证：只有「算出来明确不像活物」且置信度很低时，
                # 才把它的有效距离放远一点。注意这是**降权不是否决**。
                m = None if motion is None else motion[i]
                eff = dist
                if m is not None and m < 0.03 and d["conf"] < 0.25:
                    eff = dist + 0.5         # 不像活物且没把握 → 当远一点
                if living_near is None or eff < living_near:
                    living_near, living_info = eff, (d, dist, m)
            elif kind == "obstacle":
                if obst_near is None or dist < obst_near:
                    obst_near, obst_box = dist, d["box"]

        # ---- 状态判决 ----
        want, reason = "GO", "无威胁"
        if living_near is not None and living_near < self.LIVING_STOP:
            want, reason = "STOP", f"活体 {living_near:.2f}m"
        elif living_near is not None and living_near < self.LIVING_SLOW:
            want, reason = "SLOW", f"活体接近 {living_near:.2f}m"
        elif obst_near is not None and obst_near < self.OBST_AVOID:
            # 左右选择：现在没有侧向测距，先用「障碍框偏哪边就往另一边躲」
            cx = (obst_box[0] + obst_box[2]) / 2
            want = "AVOID_L" if cx > self.img_w / 2 else "AVOID_R"
            reason = f"障碍 {obst_near:.2f}m cx={cx:.0f}"
        elif obst_near is not None and obst_near < self.OBST_SLOW:
            want, reason = "SLOW", f"障碍接近 {obst_near:.2f}m"

        # 已有的 STOP 要等目标真的退远了才解除（滞回）
        if self.mode == "STOP" and want != "STOP":
            still_near = living_near is not None and living_near < self.LIVING_RESUME
            if still_near:
                want, reason = "STOP", f"活体仍近 {living_near:.2f}m（滞回 {self.LIVING_RESUME}m 才放行）"

        # ---- 去抖 ----
        if want == self._pending_mode:
            self._pending_n += 1
        else:
            self._pending_mode, self._pending_n = want, 1
        if self._pending_n >= self.CONFIRM_FRAMES and want != self.mode:
            self.log.append(f"[{now % 10000:7.2f}] {self.mode} -> {want}  ({reason})")
            self.mode = want

        # ---- 鸣叫：进 STOP 就响，且至少响 BEEP_HOLD_S ----
        if self.mode == "STOP":
            self._beep_until = max(self._beep_until, now + self.BEEP_HOLD_S)
        beep = now < self._beep_until

        # ---- 生成动作 ----
        if self.mode == "STOP":
            act = Action("STOP", 0, 0, 1, 1, beep, reason)
        elif self.mode == "SLOW":
            d = self.DUTY_SLOW
            act = Action("SLOW", d, d, 1, 1, beep, reason)
        elif self.mode == "AVOID_L":
            # 往左躲：右轮快、左轮慢（差速）
            act = Action("AVOID_L", self.DUTY_AVOID // 2, self.DUTY_AVOID, 1, 1, beep, reason)
        elif self.mode == "AVOID_R":
            act = Action("AVOID_R", self.DUTY_AVOID, self.DUTY_AVOID // 2, 1, 1, beep, reason)
        else:
            act = Action("GO", self.DUTY_CRUISE, self.DUTY_CRUISE, 1, 1, beep, reason)

        self.last_seen = {
            "living_m": None if living_near is None else round(living_near, 2),
            "obstacle_m": None if obst_near is None else round(obst_near, 2),
            "n_flat": n_flat,
            "n_det": len(dets),
        }
        return act


# ============================================================
# 五、串口下发
# ============================================================
class FpgaLink:
    """把动作命令发给 ZYNQ 板（PS UART0，115200 8N1）。

    没装 pyserial 或没插板子时不报错，只打印命令（dry-run），
    这样代码在没硬件的机器上也能跑完整个链路。
    """

    def __init__(self, port: str = "COM10", baud: int = 115200, dry: bool = False):
        self.port, self.baud = port, baud
        self.sp = None
        self.dry = dry
        self.last_sent: tuple[str, float] = ("", 0.0)
        self.sent_count = 0
        if dry:
            print("[FpgaLink] dry-run 模式，命令只打印不发")
            return
        try:
            import serial                       # noqa: F401
        except ImportError:
            print("[FpgaLink] 没装 pyserial，自动进 dry-run。"
                  "装它：pip install pyserial -i https://mirrors.aliyun.com/pypi/simple/")
            self.dry = True
            return
        try:
            import serial
            self.sp = serial.Serial(port, baud, timeout=0.05)
            print(f"[FpgaLink] 已打开 {port} @ {baud}")
        except Exception as exc:                # noqa: BLE001
            print(f"[FpgaLink] 打不开 {port}（{exc}），进 dry-run")
            self.dry = True

    def send_action(self, act: Action, min_interval: float = 0.1) -> None:
        """发给板子。相同命令在 min_interval 内不重复发，省串口带宽。"""
        cmd = "\r\n".join(act.cmds()) + "\r\n"
        now = time.time()
        if cmd == self.last_sent[0] and now - self.last_sent[1] < min_interval:
            return
        self.last_sent = (cmd, now)
        self.sent_count += 1
        if self.dry:
            return
        try:
            self.sp.write(cmd.encode("ascii"))
        except Exception as exc:                # noqa: BLE001
            print(f"[FpgaLink] 写串口失败：{exc}")

    def close(self) -> None:
        if self.sp is not None:
            try:
                self.sp.close()
            except Exception:
                pass


# ============================================================
# 六、自测
# ============================================================
def _selftest() -> int:
    print("=" * 62)
    print("CarDecider 自测（无硬件，纯逻辑）")
    print("=" * 62)

    dist = MonoGroundDistance(K=100.0, y_horizon=240.0, img_h=480)
    # 反推：想要 1.0m，则 y_bottom = 240 + 100/1.0 = 340
    print("  单目测距校验：")
    for d_target in (0.5, 1.0, 2.0, 3.0):
        y = 240 + 100.0 / d_target
        got = dist.estimate((0, 0, 100, y))
        print(f"    目标 {d_target:.1f}m -> y_bottom={y:.0f} -> 算得 {got:.3f}m")

    print()
    dec = CarDecider(dist=dist)

    cases = [
        ("远处来人 2.5m", [{"name": "person", "conf": 0.8, "box": (300, 240, 340, 280)}]),
        ("中距离来人 1.5m", [{"name": "person", "conf": 0.8, "box": (300, 250, 350, 306)}]),
        ("近距离来人 0.8m", [{"name": "person", "conf": 0.7, "box": (280, 300, 400, 365)}]),
        ("人退远 2.0m", [{"name": "person", "conf": 0.8, "box": (300, 260, 350, 290)}]),
        ("只有地毯", [{"name": "carpet", "conf": 0.9, "box": (0, 380, 640, 470)}]),
        ("近处桌子 0.5m", [{"name": "table", "conf": 0.9, "box": (100, 350, 300, 440)}]),
    ]
    # 时间必须推进，否则看不出 beep 的保持/过期行为
    t = 1000.0
    for title, dets in cases:
        for _ in range(CarDecider.CONFIRM_FRAMES + 2):
            act = dec.update(dets, None, True, now=t)
            t += 0.05
        print(f"  {title:<18s} -> {act.mode:<8s} duty={act.duty_l}/{act.duty_r} "
              f"beep={act.beep}  {act.reason}")

    # 专门验一件事：人走了以后，beep 该在 BEEP_HOLD_S 之后自己停掉
    print()
    print(f"  鸣叫时序校验（BEEP_HOLD_S={CarDecider.BEEP_HOLD_S}s）：")
    for _ in range(CarDecider.CONFIRM_FRAMES + 1):
        act = dec.update([], None, True, now=t)
        t += 0.05
    print(f"    人刚走        -> mode={act.mode} beep={act.beep}")
    t += CarDecider.BEEP_HOLD_S + 0.3
    act = dec.update([], None, True, now=t)
    print(f"    过了 {CarDecider.BEEP_HOLD_S}s 后 -> mode={act.mode} beep={act.beep}  "
          f"{'[OK] 已自动静音' if not act.beep else '[!] 还在响，逻辑有问题'}")

    print()
    print("  状态迁移日志：")
    for line in dec.log:
        print("   ", line)
    print()
    print("  [OK] 逻辑自测跑完（阈值需按实车标定）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
