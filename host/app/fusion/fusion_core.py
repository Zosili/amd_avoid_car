# -*- coding: utf-8 -*-
"""
人物 x 2D 激光雷达 融合与决策核心
==================================
纯逻辑模块：不打开摄像头、不读串口、不写 GPIO。所有函数都能单独测试。

--------------------------------------------------------------------------
为什么要这么设计（先把最容易被问的两件事说清）
--------------------------------------------------------------------------
1) **雷达根本不知道哪个距离是"人"。**
   2D 雷达每转一圈输出 N 个 (角度 θ, 距离 r)，每个点只是"θ 这个方向上
   有个面，离我 r 米"。它不知道那是墙、是桌腿、还是人的小腿。
   "这是人"只能由相机给 —— 相机是唯一有语义的传感器。

   所以融合的本质是：**相机负责"框出人并给出方位角"，雷达负责"这个方位
   角上有多远"。** 雷达是被"选中方向"之后才提供距离的，不是自己找人。

2) **方向怎么对上。**
   相机像素列 u -> 相对光轴的方位角 alpha = atan((u - cx) / fx)
   雷达角度 theta 和 alpha 之间差一个安装偏航角 theta0，以及一个旋转方向
   sign（雷达正方向可能是逆时针也可能是顺时针）。
        theta = theta0 + sign * alpha
   theta0 和 sign 必须实测（fusion/calib_yaw.py），不能默认 0 —— 默认 0
   等于假设两个传感器装在同一个光轴上、且零度对齐，现实中不成立。

--------------------------------------------------------------------------
两路距离估计（互为备份）
--------------------------------------------------------------------------
A. 雷达路（准，但可能扫空）
   人的小腿只有 8~12cm 宽，2D 雷达 1° 分辨率在 3m 处点间距 5.2cm，
   一个人腿可能只落 1~2 个点；两腿分开时波束还可能从腿缝里穿过去。
   所以雷达"扫不到人"是常态而不是故障。

B. 地面单目路（不需要雷达）
   相机装在离地 h 米处，人脚落地点的像素行 v2 与距离 X 有确定关系：
        X = h * (cos p + k sin p) / (sin p - k cos p),  k = (cy - v2)/fy
   p 是相机俯角。这个方法对"只看到下半身"依然成立，因为只用 bbox 的
   **底边**（脚），不用框高 —— 这正是低视角小车最需要的。

   优先级：雷达有有效回波 -> 用雷达；否则用单目估距兜底。
   两路都有时把差值打进日志，用来发现标定错误。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence

import numpy as np

PERSON_CLS = 18  # living 数据集里 person 的类别 id（唯一事实源：tools/living_semantics.py）


# ===========================================================================
# 一、几何：像素 <-> 方位角 <-> 雷达角度
# ===========================================================================

def fx_from_hfov(width: int, hfov_deg: float) -> float:
    """由水平视场角反推焦距（像素）。OV5640 常见镜头约 60°，需实测确认。"""
    return (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def bearing(u: float, fx: float, cx: float) -> float:
    """像素列 u -> 相对光轴的方位角（度）。右侧为正、左侧为负。"""
    return math.degrees(math.atan2(u - cx, fx))


def wrap180(a: float) -> float:
    """归一化到 (-180, 180]。"""
    return (a + 180.0) % 360.0 - 180.0


def wrap360(a: float) -> float:
    """归一化到 [0, 360)。"""
    return a % 360.0


def cam_to_lidar(alpha_deg: float, theta0: float, sign: int) -> float:
    """相机方位角 -> 雷达角度（度，0~360）。sign 取 +1 或 -1。"""
    return wrap360(theta0 + sign * alpha_deg)


def in_window(theta_deg, start_deg: float, width_deg: float):
    """判断雷达角度是否落在 [start, start+width) 这个**可能跨 0 度**的窗口里。

    跨零是必须处理的：窗口在 355°~5° 时直接比较 theta>=355 or theta<=5，
    用 (theta - start) mod 360 <= width 就自然成立。
    """
    return ((np.asarray(theta_deg) - start_deg) % 360.0) <= width_deg


def bbox_bearing_window(x1: float, x2: float, fx: float, cx: float,
                        pad_deg: float = 1.5) -> tuple[float, float]:
    """bbox 左右边 -> 相机方位角窗口 (alpha_start, width)，宽度恒为正。

    为什么用 pad：bbox 会抖、人会比框宽一点、雷达角度是离散的、
    标定有残差。1.5° 在 3m 处约 8cm，够覆盖这些误差又不会宽到框进隔壁墙。
    """
    a1 = bearing(x1, fx, cx) - pad_deg
    a2 = bearing(x2, fx, cx) + pad_deg
    if a1 > a2:
        a1, a2 = a2, a1
    return a1, (a2 - a1)


# ===========================================================================
# 二、雷达扫描数据与"窗口内取距离"
# ===========================================================================

@dataclass
class Scan:
    """一帧 2D 雷达扫描。angles/ranges 等长，角度单位度。"""
    angles: np.ndarray
    ranges: np.ndarray
    stamp: float = 0.0

    def __post_init__(self):
        self.angles = np.asarray(self.angles, dtype=np.float64).ravel()
        self.ranges = np.asarray(self.ranges, dtype=np.float64).ravel()
        if self.angles.shape != self.ranges.shape:
            raise ValueError("angles 与 ranges 长度不一致")

    def __len__(self) -> int:
        return int(self.angles.size)


# 有效回波的门限：<8cm 是近场饱和/杂散，>6m 与停车决策无关
MIN_VALID_RANGE = 0.08
MAX_VALID_RANGE = 6.0


@dataclass
class WindowHit:
    """窗口取距离的完整中间结果，方便打日志复盘。"""
    ok: bool
    dist: Optional[float]          # 米
    n_points: int
    n_clusters: int
    spread: float                  # 选中簇的极差，判断"是一个面还是一堆散点"
    reason: str = ""


def window_range(scan: Scan, start_deg: float, width_deg: float,
                 center_alpha_deg: float, theta0: float, sign: int,
                 gap_m: float = 0.60, min_pts: int = 2) -> WindowHit:
    """在角度窗口内取"人"的距离。

    步骤：
      1. 角度窗口筛选（处理跨零）
      2. 距离合法性筛选
      3. 按距离间隔聚簇（同一个人是一条连续的弧，被墙/桌腿隔开时会断开）
      4. 选"角度中心最贴近 bbox 中心"的那个簇 —— 而不是简单地取最小值。
         取最小值会被窗口边缘的墙、门框、椅子背带走，直接导致误停车。
      5. 取该簇距离的 25 分位（偏向最近表面，因为我们要的是"离车的最近点"）
    """
    m = in_window(scan.angles, start_deg, width_deg)
    pts_a = scan.angles[m]
    pts_r = scan.ranges[m]
    valid = (pts_r > MIN_VALID_RANGE) & (pts_r < MAX_VALID_RANGE)
    pts_a, pts_r = pts_a[valid], pts_r[valid]
    if pts_r.size < min_pts:
        return WindowHit(False, None, int(pts_r.size), 0, 0.0, "no_return")

    # 按距离排序后，相邻间隔 > gap_m 就切簇
    order = np.argsort(pts_r)
    rs, as_ = pts_r[order], pts_a[order]
    cuts = np.where(np.diff(rs) > gap_m)[0] + 1
    groups = np.split(np.arange(rs.size), cuts)

    target = cam_to_lidar(center_alpha_deg, theta0, sign)
    best, best_score = None, 1e9
    for g in groups:
        if g.size < min_pts:
            continue
        # 簇的角度中心（取中心向量再求角度，避免 359/1 跨零平均出错）
        a = np.radians(as_[g])
        cen = math.degrees(math.atan2(np.sin(a).mean(), np.cos(a).mean())) % 360.0
        dev = abs(wrap180(cen - target))
        r_med = float(np.median(rs[g]))
        # 角度偏离是主判据，距离偏离只是微弱加权
        score = dev + 0.15 * abs(r_med - float(np.median(rs)))
        if score < best_score:
            best, best_score = g, score
    if best is None:  # 所有簇都太小，退化成全部点
        best = np.arange(rs.size)

    r_sel = rs[best]
    return WindowHit(True, float(np.percentile(r_sel, 25)), int(pts_r.size),
                     len(groups), float(np.ptp(r_sel)), "ok")


# ===========================================================================
# 三、单目地面估距（雷达扫不到时的兜底）
# ===========================================================================

def ground_distance(v_bottom: float, fy: float, cy: float,
                    cam_h: float, pitch_deg: float = 0.0) -> Optional[float]:
    """用 bbox 底边像素行估算"脚落地点"的径向距离（米）。

    推导（相机俯角 p，相机离地 h，脚在世界坐标 z=0）：
        k  = (cy - v_bottom) / fy
        X  = h * (cos p + k sin p) / (sin p - k cos p)
    p=0 时退化为大家熟悉的 X = h * fy / (v_bottom - cy)。

    局限（必须知道，否则会误用）：
      - 只在 bbox 底边真的落在脚上时成立。人被桌子挡住下缘、或脚出画面
        （v_bottom 贴到图像底边）时必须返回 None。
      - 距离越远越敏感：h=0.2m、fy=554 时，3m 处 1 像素误差约 8cm。
    """
    if v_bottom is None:
        return None
    k = (cy - v_bottom) / fy
    p = math.radians(pitch_deg)
    den = math.sin(p) - k * math.cos(p)
    if abs(den) < 1e-6:
        return None
    x = cam_h * (math.cos(p) + k * math.sin(p)) / den
    if x <= 0.05 or x > 30.0:
        return None
    return x


# ===========================================================================
# 四、决策状态机（带迟滞 + 确认帧 + 释放时间）
# ===========================================================================

class State(str, Enum):
    CLEAR = "CLEAR"   # 前方无人或 >3m：正常走，不响
    WARN = "WARN"     # 1~3m：蜂鸣器响，**不停车**
    STOP = "STOP"     # <1m：停车，蜂鸣器连续响


@dataclass
class DecisionConfig:
    # --- 几何标定 ---
    cam_w: int = 640
    cam_h_px: int = 480
    hfov_deg: float = 60.0        # OV5640 镜头水平视场，必须实测
    cx: Optional[float] = None    # None -> cam_w/2
    cy: Optional[float] = None
    cam_height_m: float = 0.20    # 相机镜头离地高度
    cam_pitch_deg: float = 0.0    # 俯角，向下为正
    mono_offset_m: float = 0.08   # 单目量的脚点 vs 雷达量的小腿前表面，差约小腿半径
    mono_max_m: float = 6.0       # 单目估距超过这个值就拒信（见下）
    # 为什么要有 mono_max_m：人坐在椅子上、被桌子挡住下缘、或者脚出画时，
    # bbox 底边并不在"脚落地点"上，地面公式会给出 10m 这种离谱值。
    # 宁可判成"看到人但测不出距离"（保守 -> WARN），也不能给出一个虚假的"很远"。

    theta0: float = 0.0           # 雷达角度 == 相机光轴的方向（标定得到）
    ang_sign: int = -1            # +1/-1，标定得到

    # --- 融合 ---
    pad_deg: float = 1.5
    corridor_deg: float = 35.0    # 只看前方 ±35°；侧后方的人不该让车停

    # --- 阈值（用户要求）---
    stop_m: float = 1.0           # < 1.0m 停车
    warn_m: float = 3.0           # 1~3m 鸣叫
    hyst_m: float = 0.15          # 迟滞带，防止在阈值上来回跳

    # --- 时序 ---
    confirm_frames: int = 5       # 解除/降级需连续确认帧数
    release_s: float = 1.0        # 人消失多久后回到 CLEAR
    stop_hold_s: float = 1.0      # 停车后最小保持时间：进入 STOP 后至少保持这么久才允许解除
    unknown_as: State = State.WARN  # 看到人但测不出距离时的保守处理

    # --- 距离平滑 ---
    # 人框在相邻帧会抖几像素，alpha 抖几度，雷达窗口随之在"人"和"旁边障碍"
    # 之间来回扫，距离在 1.5m/2.7m 这种两个值上跳变。用滑动中值压掉单帧跳变：
    # 只有当连续多数帧都同意"距离变了"时才真的变。窗口越大越稳，但响应越慢。
    dist_median_win: int = 7      # 滑动窗口（帧），取中值。奇数更稳。

    # --- 蜂鸣器 ---
    warn_continuous: bool = False  # True=1~3m 也连续响；False=越近越急的间歇响
    warn_beep_fast: float = 0.15  # 1m 时的鸣叫周期（秒）
    warn_beep_slow: float = 0.60  # 3m 时的鸣叫周期（秒）

    min_box_px: int = 12          # 太小的框（远处噪声）忽略

    # --- 非活体障碍避障 ---
    # 前方走廊里，比"最近的人"还近（或没有人时）的雷达回波 = 障碍。
    # 障碍 < avoid_m 时触发转向绕行（减速缓转，不停车不响）。
    avoid_m: float = 0.50         # 障碍小于该距离触发转向
    obstacle_corridor_deg: float = 35.0  # 障碍检测也用前方 ±35°（与 corridor 一致）


@dataclass
class PersonReading:
    """单个人的融合结果。"""
    bbox: tuple
    conf: float
    alpha_center: float           # 相机方位角（度）
    lidar_dist: Optional[float]
    mono_dist: Optional[float]
    dist: Optional[float]         # 最终采用的距离
    source: str                   # "lidar" / "mono" / "none"
    window: tuple                 # (start, width) 雷达窗口，调试用


@dataclass
class Result:
    state: State
    dist: Optional[float]
    readings: list = field(default_factory=list)
    buzzer: int = 0
    motors_allowed: bool = True
    note: str = ""
    # 非活体障碍（前方走廊内、比人更近的雷达回波）：
    #   obstacle_dist: 最近障碍距离（米）；None = 无障碍
    #   obstacle_side: -1 = 障碍在左半 -> 应向右缓转；+1 = 右半 -> 向左缓转；0 = 无/居中
    obstacle_dist: Optional[float] = None
    obstacle_side: int = 0


class FusionEngine:
    """输入"这一帧的人框 + 这一帧的雷达扫描"，输出"该不该停、该不该响"。"""

    def __init__(self, cfg: DecisionConfig):
        self.cfg = cfg
        self.fx = fx_from_hfov(cfg.cam_w, cfg.hfov_deg)
        self.cx = cfg.cam_w / 2.0 if cfg.cx is None else cfg.cx
        self.cy = cfg.cam_h_px / 2.0 if cfg.cy is None else cfg.cy

        self.state = State.CLEAR
        self.dist: Optional[float] = None
        self._last_seen = -1e9
        self._stop_at = -1e9       # 进入 STOP 的时刻（停车缓冲用）
        self._cand: Optional[State] = None
        self._cand_n = 0
        # 距离滑动中值滤波：压掉人框抖动导致的 1.5m/2.7m 跳变
        self._dist_hist: list = []

    # ---- 单个人 ----
    def _read_person(self, box: Sequence[float], conf: float,
                     scan: Optional[Scan]) -> PersonReading:
        x1, y1, x2, y2 = [float(v) for v in box]
        a_lo, a_w = bbox_bearing_window(x1, x2, self.fx, self.cx, self.cfg.pad_deg)
        a_c = a_lo + a_w / 2.0

        start = cam_to_lidar(a_lo, self.cfg.theta0, self.cfg.ang_sign)
        # 窗口宽度在两种旋转方向下相同（只是遍历方向相反），起点要重算
        if self.cfg.ang_sign < 0:
            start = cam_to_lidar(a_lo + a_w, self.cfg.theta0, self.cfg.ang_sign)

        lidar_d = None
        if scan is not None and len(scan):
            hit = window_range(scan, start, a_w, a_c, self.cfg.theta0,
                               self.cfg.ang_sign)
            if hit.ok:
                lidar_d = hit.dist

        mono_d = None
        if y2 < self.cfg.cam_h_px - 1:  # 底边贴图像下沿 -> 脚出画，单目不可用
            mono_d = ground_distance(y2, self.fx, self.cy, self.cfg.cam_height_m,
                                     self.cfg.cam_pitch_deg)
            if mono_d is not None:
                # 单目量的是"脚落地点"（人身体中线），雷达量的是最前面的表面，
                # 两者差约半个人身厚度。不减这个偏移，单目接管时车会多贴近 15cm 才停。
                mono_d = max(0.05, mono_d - self.cfg.mono_offset_m)
                if mono_d > self.cfg.mono_max_m:
                    mono_d = None

        if lidar_d is not None:
            d, src = lidar_d, "lidar"
        elif mono_d is not None:
            d, src = mono_d, "mono"
        else:
            d, src = None, "none"
        return PersonReading((x1, y1, x2, y2), float(conf), a_c,
                             lidar_d, mono_d, d, src, (start, a_w))

    # ---- 主入口 ----
    def update(self, now: Optional[float] = None, persons: Iterable[dict] = (),
               scan: Optional[Scan] = None) -> Result:
        cfg = self.cfg
        # 时间戳不给就用单调时钟。绝不能依赖调用方传对 —— 一旦时间不前进，
        # "人离开 N 秒后恢复行驶"这个条件永远不会满足，车会一直停着。
        if now is None:
            now = time.monotonic()
        readings: list[PersonReading] = []
        for p in persons:
            b = p.get("bbox") or (p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2"))
            if b[2] - b[0] < cfg.min_box_px or b[3] - b[1] < cfg.min_box_px:
                continue
            r = self._read_person(b, float(p.get("conf", 1.0)), scan)
            if abs(r.alpha_center) <= cfg.corridor_deg:  # 只关心正前方
                readings.append(r)

        # 最危险（最近）的那个人决定状态
        usable = [r for r in readings if r.dist is not None]
        if readings:
            self._last_seen = now
        if usable:
            nearest = min(usable, key=lambda r: r.dist)
            raw = nearest.dist
        elif readings:
            raw = None  # 看到人但测不出距离
        else:
            raw = None

        # 距离滑动中值：压掉人框抖动带来的单帧跳变（1.5m/2.7m 来回跳）。
        # 只在"有距离"和"无距离"之间保持一致语义：无距离时也记一个占位，
        # 否则人短暂测不出距离时历史会被清空、恢复时又抖。
        if self.cfg.dist_median_win > 1:
            self._dist_hist.append(raw if raw is not None else float("inf"))
            if len(self._dist_hist) > self.cfg.dist_median_win:
                self._dist_hist.pop(0)
            vals = [v for v in self._dist_hist if v != float("inf")]
            if vals and len(vals) >= (len(self._dist_hist) + 1) // 2:
                raw = float(np.median(vals))
            elif not vals:
                raw = None
            else:
                # 有效距离不足半数 -> 本帧视为测不出（避免单帧孤立值直接改状态）
                raw = None

        note = ""
        if raw is None:
            if readings:
                # 保守：看到了人却测不出距离，按 unknown_as 处理
                self.state = cfg.unknown_as if self.state is not State.STOP else State.STOP
                self.dist = None
                note = "person_no_distance"
            else:
                self.dist = None
                if (now - self._last_seen > cfg.release_s
                        and now - self._stop_at >= cfg.stop_hold_s
                        and self.state is not State.CLEAR):
                    self._propose(State.CLEAR, now)
                note = "no_person"
        else:
            self.dist = raw
            self._propose(self._target_state(raw, now), now)
            note = "ok"

        # ---- 非活体障碍检测（前方走廊内、比人更近的雷达回波）----
        obstacle_dist, obstacle_side = self._detect_obstacle(scan, raw)

        res = Result(self.state, self.dist, readings,
                     buzzer=self._buzzer_level(now),
                     motors_allowed=self.state is not State.STOP,
                     note=note,
                     obstacle_dist=obstacle_dist,
                     obstacle_side=obstacle_side)
        return res

    def _detect_obstacle(self, scan: Optional[Scan],
                         nearest_person: Optional[float]) -> tuple:
        """前方走廊内最近有效回波，若比最近的人更近则判为障碍。

        返回 (obstacle_dist_m, obstacle_side)。
        obstacle_side：-1=障碍在左半(应向右缓转)，+1=右半(向左缓转)，0=无/居中。
        语义：避障是"绕开挡在前面的非人物体"，不停车不响，减速缓转。
        """
        cfg = self.cfg
        if scan is None or not len(scan):
            return None, 0
        # 前方走廊内的有效回波
        front = abs(((scan.angles - 0.0 + 180.0) % 360.0) - 180.0) <= cfg.obstacle_corridor_deg
        valid = (scan.ranges > MIN_VALID_RANGE) & (scan.ranges < MAX_VALID_RANGE)
        m = front & valid
        if not m.any():
            return None, 0
        ang = scan.angles[m]
        rng = scan.ranges[m]
        # 最近的那个回波
        idx = int(np.argmin(rng))
        d = float(rng[idx])
        a = float(ang[idx])
        # 如果这个最近回波就是"最近的人"（或比人还远），不算障碍
        if nearest_person is not None and d >= nearest_person - 0.05:
            return None, 0
        # 只有足够近才触发避障
        if d >= cfg.avoid_m:
            return None, 0
        # 判断左右：a 在 0~180 是左半，180~360 是右半（0=正前方，逆时针为正）
        side = -1 if (a % 360.0) < 180.0 else +1
        return d, side

    def _target_state(self, d: float, now: float) -> State:
        c = self.cfg
        if self.state is State.STOP:
            # 关键：停车之后**不因为"人退到 1.2m"就恢复**。
            # 那样车会跟着人一路往前挪、走走停停 —— 人退一步它进两步。
            # 只有人退到鸣叫距离之外（> 3m，带迟滞）才允许恢复；人跑出画面
            # 或离开前方走廊则由 update() 里的"无人 release_s"路径解除。
            # 另加 stop_hold_s：停车后至少保持 1s，防止某帧忽然漏检导致车突然动。
            if d > c.warn_m + c.hyst_m and now - self._stop_at >= c.stop_hold_s:
                return State.CLEAR
            return State.STOP
        if self.state is State.WARN:
            if d <= c.stop_m:
                return State.STOP
            if d > c.warn_m + c.hyst_m:
                return State.CLEAR
            return State.WARN
        # CLEAR
        if d <= c.stop_m:
            return State.STOP
        if d <= c.warn_m:
            return State.WARN
        return State.CLEAR

    def _propose(self, nxt: State, now: float) -> None:
        """STOP 立即生效（危险不能等），其余转换要连续 confirm_frames 帧。"""
        if nxt is self.state:
            self._cand, self._cand_n = None, 0
            return
        if nxt is State.STOP:
            self.state, self._cand, self._cand_n = State.STOP, None, 0
            self._stop_at = now   # 记录停车时刻，供 stop_hold 缓冲
            return
        if nxt is self._cand:
            self._cand_n += 1
        else:
            self._cand, self._cand_n = nxt, 1
        if self._cand_n >= self.cfg.confirm_frames:
            self.state, self._cand, self._cand_n = nxt, None, 0

    def _buzzer_level(self, now: float) -> int:
        if self.state is State.CLEAR:
            return 0
        if self.state is State.STOP:
            return 1  # 停车时连续响
        if self.cfg.warn_continuous:
            return 1
        c = self.cfg
        d = self.dist if self.dist is not None else c.warn_m
        # 越近越急：1m -> warn_beep_fast，3m -> warn_beep_slow
        f = min(1.0, max(0.0, (d - c.stop_m) / max(1e-6, c.warn_m - c.stop_m)))
        period = c.warn_beep_fast + f * (c.warn_beep_slow - c.warn_beep_fast)
        return 1 if (now % period) < period * 0.5 else 0


# ===========================================================================
# 五、给 FPGA 的指令打包（与 motor_axi_top.v 的位域对齐）
# ===========================================================================

def pack_gpio(duty_l: int = 0, duty_r: int = 0, dir_l: int = 0, dir_r: int = 0,
              buzzer: int = 0, stop: int = 0, warn: int = 0) -> int:
    """bit[6:0] duty_l / [14:8] duty_r / [16] dir_l / [17] dir_r
       [18] buzzer / [19] stop_lock / [20] warn

    stop_lock 由 PL 侧做硬钳位：置 1 时无论 duty 写多少都不出 PWM。
    这样"PC 死机但最后一条指令是前进"这种危险情况，还有第二道保险。
    """
    return ((duty_l & 0x7F)
            | ((duty_r & 0x7F) << 8)
            | ((dir_l & 1) << 16)
            | ((dir_r & 1) << 17)
            | ((buzzer & 1) << 18)
            | ((stop & 1) << 19)
            | ((warn & 1) << 20))


# ===========================================================================
# 六、自检
# ===========================================================================

def _selftest() -> None:
    ok = 0
    # 1) 方位角：正中心 = 0，右侧为正
    fx = fx_from_hfov(640, 60.0)
    assert abs(bearing(320, fx, 320.0)) < 1e-9
    assert bearing(480, fx, 320.0) > 0 > bearing(160, fx, 320.0)
    ok += 1

    # 2) 跨零窗口
    assert bool(in_window(359.0, 355.0, 10.0))
    assert bool(in_window(2.0, 355.0, 10.0))
    assert not bool(in_window(20.0, 355.0, 10.0))
    ok += 1

    # 3) 窗口取距离：人 2.0m，旁边 0.9m 有堵墙 —— 不能被墙带走
    ang = np.arange(0, 360, 1.0)
    rng = np.full(ang.shape, 8.0)
    rng[100:120] = 2.0   # 人的弧段，20° 宽
    rng[96:100] = 0.9    # 紧邻的墙
    scan = Scan(ang, rng)
    hit = window_range(scan, 100.0, 20.0, 110.0, 0.0, -1)
    assert hit.ok and abs(hit.dist - 2.0) < 0.05, hit
    ok += 1

    # 4) 单目地面估距：h=0.2m、水平安装，2m 处脚应在 v = cy + fy*h/X
    v = 240 + fx * 0.2 / 2.0
    assert abs(ground_distance(v, fx, 240.0, 0.20) - 2.0) < 0.02
    ok += 1

    # 5) 状态机：1~3m 只响不停，<1m 才停
    cfg = DecisionConfig(confirm_frames=1, release_s=0.0)
    cfg.cam_height_m = 0.20
    eng = FusionEngine(cfg)
    def mk(d):
        # 用雷达直接给距离，绕开单目
        a = np.arange(0, 360, 1.0); r = np.full(a.shape, 8.0); r[358:360] = d
        r = np.concatenate([r, np.full(2, d)]); a = np.concatenate([a, np.array([0.0, 1.0])])
        return Scan(a, r)
    p = {"bbox": (300, 200, 340, 400), "conf": 0.9}
    eng.update(0.0, [p], mk(2.0)); assert eng.state is State.WARN, eng.state
    assert eng._buzzer_level(0.0) in (0, 1)
    eng.update(0.1, [p], mk(0.8)); assert eng.state is State.STOP
    assert eng.update(0.2, [p], mk(0.8)).motors_allowed is False
    ok += 1

    print(f"selftest: {ok}/5 passed")


if __name__ == "__main__":
    _selftest()
