# -*- coding: utf-8 -*-
"""
================================================================
 MotionGate —— 用运动信息辅助判断「这个框里到底是不是活物」
================================================================

为什么需要它：
    YOLO 是静态单帧检测器。它分不清「真人」和「墙上海报里的人」，
    也分不清「趴着不动的猫」和「一坨睡衣」。这些都靠外观是同一个东西。
    运动信息是唯一能把它们分开的第二路信号。

核心难点（以及为什么不能用最直觉的办法）：
    车在动，所以「画面里的像素在变」根本说明不了物体在动。
    静止的桌子在画面里也会大幅位移。

本模块的两个思路，分别对付两种场景：

  ① flow 模式 —— 车在动时用
     「跟位移无关，只跟形变有关」。
     框内取角点算稀疏光流，然后看这些位移向量的**方向一致性**：

        静止刚体  : 相机平移让框内所有点朝同一方向移 → 单位向量几乎重合
                     |mean(u_hat)| ≈ 1  → dispersion ≈ 0
        走动的腿  : 大腿往前、小腿往后、身体上下起伏 → 方向发散
                     |mean(u_hat)| 小    → dispersion 大

     这个量对相机运动是**免疫**的：相机怎么动，刚体的方向都一致。
     不需要知道车的速度，不需要深度，不需要标定。

  ② static 模式 —— 停车后那 0.3 秒用
     车一停，相机静止，「车在动」这个干扰项直接消失。
     此时最原始的帧间差分就是最可靠的判据。
     这是本模块推荐默认开启的模式，专门用来挡误报
     （海报、电视画面、被风掀动的窗帘）。

设计原则：
    * 任何一路失效都返回 None，让上层继续用纯检测结果，绝不因为运动判定
      算不出来就停车或不停车。
    * 不引入任何额外依赖，只用 cv2 + numpy。

用法：
    mg = MotionGate(mode="flow")
    for frame in frames:
        mg.push(frame)                      # 每帧喂一次（灰度内部自己转）
        scores = mg.score(boxes_xyxy)        # 与 boxes 等长的 list，元素 0..1 或 None
        # 0 = 完全刚性（不像活物），1 = 强烈非刚性（像活物）

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import numpy as np
import cv2


class MotionGate:
    """框级运动强度估计。

    mode:
        "off"    : 全返回 None（纯检测，最快）
        "flow"   : 光流方向一致性（车动时可用）
        "static" : 停车后帧间差分（最稳，需配合 set_stopped）
    """

    def __init__(
        self,
        mode: str = "flow",
        max_corners: int = 240,
        min_pts: int = 6,
        min_flow_px: float = 0.6,
        diff_thresh: float = 8.0,
        settle_frames: int = 3,
    ):
        self.mode = mode
        self.max_corners = max_corners
        self.min_pts = min_pts
        self.min_flow_px = min_flow_px      # 位移小于这个像素数的点当噪声丢掉
        self.diff_thresh = diff_thresh      # static 模式：灰度差大于它算"在动"
        self.settle_frames = settle_frames  # static 模式：车停后等几帧再取基准（防车架余震）

        self.prev_gray: np.ndarray | None = None
        self.prev_pts: np.ndarray | None = None

        # static 模式的状态
        self.stopped = False
        self._stop_counter = 0
        self._anchor: np.ndarray | None = None

        # 诊断计数（调试时看为什么返回 None）
        self.stat = {"calls": 0, "pts": 0, "nan": 0, "no_prev": 0}

    # ------------------------------------------------------------ 每帧调用
    def push(self, frame_bgr: np.ndarray) -> None:
        """喂入新的一帧。必须先 push 再 score，或者在同一帧上先 score 后 push
        （此时 score 用的是上一次 push 的帧作为参考）。"""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        self.prev_gray = gray

        if self.mode == "flow":
            self.prev_pts = cv2.goodFeaturesToTrack(
                gray,
                maxCorners=self.max_corners,
                qualityLevel=0.01,
                minDistance=8,
                blockSize=7,
            )

        if self.mode == "static":
            if self.stopped:
                self._stop_counter += 1
                # 车刚停下会有一段余震，等 settle_frames 帧后再把当前帧当基准
                if self._stop_counter == self.settle_frames:
                    self._anchor = gray.copy()

    def set_stopped(self, stopped: bool) -> None:
        """告诉模块「车现在是停着的」。static 模式靠它决定要不要建基准帧。"""
        if stopped and not self.stopped:
            self._stop_counter = 0
            self._anchor = None
        elif not stopped:
            self._stop_counter = 0
            self._anchor = None
        self.stopped = stopped

    # ------------------------------------------------------------ 打分
    def score(self, boxes_xyxy, frame_bgr: np.ndarray | None = None):
        """对每个框返回 0..1 的运动强度，或 None（这一路给不出结论）。

        boxes_xyxy: [(x1,y1,x2,y2), ...] 像素坐标
        frame_bgr : 当前帧。若不传就用 prev_gray（仅 static 模式可用）
        """
        self.stat["calls"] += 1
        if self.mode == "off" or len(boxes_xyxy) == 0:
            return [None] * len(boxes_xyxy)

        if self.mode == "flow":
            return self._score_flow(boxes_xyxy, frame_bgr)
        if self.mode == "static":
            return self._score_static(boxes_xyxy, frame_bgr)

        return [None] * len(boxes_xyxy)

    # ------------------------------------------------------------ ① 光流
    def _score_flow(self, boxes, frame_bgr):
        out = [None] * len(boxes)
        if self.prev_gray is None or self.prev_pts is None or len(self.prev_pts) < self.min_pts:
            self.stat["no_prev"] += 1
            return out
        if frame_bgr is None:
            self.stat["no_prev"] += 1
            return out

        cur = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(
            self.prev_gray, cur, self.prev_pts, None,
            winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
        )
        if nxt is None or st is None:
            self.stat["nan"] += 1
            return out

        ok = st.reshape(-1) == 1
        p0 = self.prev_pts.reshape(-1, 2)[ok]
        p1 = nxt.reshape(-1, 2)[ok]
        if len(p0) < self.min_pts:
            return out

        d = p1 - p0
        mag = np.linalg.norm(d, axis=1)
        # 位移太小的点：方向基本是噪声，必须丢掉，否则会把静止物体的
        # dispersion 算成很大（一堆抖动的小向量方向随机）
        good = mag >= self.min_flow_px
        if good.sum() < self.min_pts:
            return out
        p0, d, mag = p0[good], d[good], mag[good]
        self.stat["pts"] += int(len(p0))

        u = d / mag[:, None]     # 单位方向向量

        for i, (x1, y1, x2, y2) in enumerate(boxes):
            m = ((p0[:, 0] >= x1) & (p0[:, 0] <= x2) &
                 (p0[:, 1] >= y1) & (p0[:, 1] <= y2))
            n = int(m.sum())
            if n < self.min_pts:
                continue
            # 圆统计里的「平均合向量长度」：1 = 方向完全一致，0 = 完全发散
            r = float(np.linalg.norm(u[m].mean(axis=0)))
            r = min(max(r, 0.0), 1.0)
            out[i] = 1.0 - r

        return out

    # ------------------------------------------------------------ ② 停车差分
    def _score_static(self, boxes, frame_bgr):
        out = [None] * len(boxes)
        if frame_bgr is None or self._anchor is None:
            return out
        cur = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr

        diff = cv2.absdiff(cur, self._anchor)
        # 轻度模糊，压掉 RGB565 量化噪声和传感器噪声
        diff = cv2.GaussianBlur(diff, (5, 5), 0)
        H, W = diff.shape[:2]

        for i, (x1, y1, x2, y2) in enumerate(boxes):
            xa, ya = max(0, int(x1)), max(0, int(y1))
            xb, yb = min(W, int(x2)), min(H, int(y2))
            if xb - xa < 8 or yb - ya < 8:
                continue
            roi = diff[ya:yb, xa:xb]
            # 用「变化像素占比」而不是均值：均值会被大面积静止泡淡，
            # 而活体往往只有一小块区域（甩尾、抬手）在动。
            frac = float((roi > self.diff_thresh).mean())
            out[i] = min(frac * 4.0, 1.0)     # 25% 的像素在变就算满强度
        return out


# ================================================================
# 自测：用合成序列验证「刚体平移 vs 非刚性形变」能不能分开
# ================================================================
def _selftest() -> int:
    """构造两段合成视频：
         A) 整块图案平移 —— 模拟"车动，物体静止"
         B) 图案内部按不同方向撕开 —— 模拟"活体形变"
       期望 A 的 dispersion 接近 0，B 明显更大。
    """
    print("=" * 62)
    print("MotionGate 自测")
    print("=" * 62)

    W, H = 320, 240
    rng = np.random.default_rng(0)

    # 造一张有丰富角点的纹理图（棋盘 + 随机斑点）
    tex = np.zeros((H, W), np.uint8)
    for y in range(0, H, 16):
        for x in range(0, W, 16):
            if ((x // 16) + (y // 16)) % 2 == 0:
                tex[y:y + 16, x:x + 16] = 200
    spots = rng.integers([0, 0], [H - 4, W - 4], size=(60, 2))
    for y, x in spots:
        tex[y:y + 4, x:x + 4] = 255

    box = (60, 60, 240, 200)

    def run(case: str):
        mg = MotionGate(mode="flow", min_flow_px=0.4)
        prev = tex.copy()
        mg.push(prev)
        if case == "rigid":
            # 整图右移 4 像素 —— 纯相机运动
            cur = np.roll(tex, 4, axis=1)
        else:
            # 上半部分右移 4、下半部分左移 4 —— 非刚性撕开
            cur = tex.copy()
            h2 = H // 2
            cur[:h2] = np.roll(tex[:h2], 5, axis=1)
            cur[h2:] = np.roll(tex[h2:], -5, axis=1)
        s = mg.score([box], cv2.cvtColor(cur, cv2.COLOR_GRAY2BGR))
        return s[0]

    a = run("rigid")
    b = run("deform")

    print(f"  刚体整体平移 (车动/物静) : dispersion = {a}")
    print(f"  内部反向撕开 (活体形变)  : dispersion = {b}")

    ok = (a is not None and b is not None and a < 0.35 and b > a + 0.1)
    print()
    print(f"  [{'通过' if ok else '失败'}] "
          f"区分度 {'够' if ok else '不足'}（期望 rigid<0.35 且 deform 明显更大）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
