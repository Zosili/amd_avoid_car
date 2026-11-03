# -*- coding: utf-8 -*-
"""
部署后端实测 —— 按赛题 3.2.4.3 的口径出数

赛题要求（原文）：
  "推理延迟原则上按单样本（batch=1）测量，分别列出预处理、模型推理、
   后处理和总感知延迟"
  "报告 P50、P95、P99 和最大值，不能只给平均值"

所以这里阶段拆分 + 分位数全给。

用法（必须用 yolo-rocm 环境）：
    直接运行即可，默认测 CPU 和 ROCm(780M) 两个后端。

注意：这是推理基准，不训练、不改权重。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ.pop("PYTHONPATH", None)

MODEL = PROJECT / "runs" / "18cls_yolo11n" / "weights" / "best.pt"
IMG_DIR = PROJECT / "datasets" / "18cls" / "images" / "val"
# 可选：命令行传 imgsz，例如 `bench_deploy.py 480` 测 480 输入
IMGSZ = int(sys.argv[1]) if len(sys.argv) > 1 else 640
N = 40          # 测量次数
WARM = 5        # 预热次数
SRC_W, SRC_H = 640, 480   # 模拟 OV5640 输出
MAX_DET = 100   # NMS 最多保留框数。不设上限时噪声图会产生上千框，把 NMS 时间打爆


def load_images(n: int = 8) -> list[np.ndarray]:
    """读真实验证集图片并统一缩放到 OV5640 的分辨率（真实输入才可靠）"""
    if not IMG_DIR.exists():
        print(f"[!] 找不到验证集图片目录 {IMG_DIR}，退回随机噪声（NMS 耗时会偏大）")
        return [np.random.randint(0, 255, (SRC_H, SRC_W, 3), dtype=np.uint8)]
    files = sorted(IMG_DIR.glob("*.jpg"))[:n]
    imgs = []
    for f in files:
        im = cv2.imread(str(f))
        if im is None:
            continue
        imgs.append(cv2.resize(im, (SRC_W, SRC_H), interpolation=cv2.INTER_LINEAR))
    print(f"  真实图片 : {len(imgs)} 张（{IMG_DIR.name}/）缩放至 {SRC_W}x{SRC_H}")
    return imgs or [np.random.randint(0, 255, (SRC_H, SRC_W, 3), dtype=np.uint8)]


def letterbox(im: np.ndarray, size: int):
    """等比缩放 + 右下补灰边，返回 (nchw float32 array, 原始尺寸)"""
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    x = canvas[:, :, ::-1].transpose(2, 0, 1)          # BGR->RGB, HWC->CHW
    x = np.ascontiguousarray(x, dtype=np.float32) / 255.0
    return x[None], (h, w)


def pct(sorted_ts: list[float], p: float) -> float:
    if not sorted_ts:
        return float("nan")
    k = min(len(sorted_ts) - 1, int(round((len(sorted_ts) - 1) * p)))
    return sorted_ts[k]


def bench(tag: str, dev: str, half: bool, imgs: list[np.ndarray]) -> None:
    import torch
    from ultralytics import YOLO
    # 8.4.x 起 non_max_suppression 从 utils.ops 移到了 utils.nms
    try:
        from ultralytics.utils.nms import non_max_suppression
    except ImportError:
        from ultralytics.utils.ops import non_max_suppression

    m = YOLO(str(MODEL))
    net = m.model.to(dev).eval()
    if half:
        net = net.half()
    dtype = torch.float16 if half else torch.float32

    img = imgs[0]

    def once(im: np.ndarray):
        t0 = time.perf_counter()
        x, _ = letterbox(im, IMGSZ)
        xt = torch.from_numpy(x).to(dev)
        if half:
            xt = xt.half()
        t1 = time.perf_counter()
        with torch.no_grad():
            raw = net(xt)
        t2 = time.perf_counter()
        pred = raw[0] if isinstance(raw, (list, tuple)) else raw
        _ = non_max_suppression(pred.float(), conf_thres=0.25, iou_thres=0.45, max_det=MAX_DET)
        t3 = time.perf_counter()
        return (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000

    for i in range(WARM):
        once(imgs[i % len(imgs)])

    pre, inf, post, tot = [], [], [], []
    for i in range(N):
        a, b, c = once(imgs[i % len(imgs)])
        pre.append(a)
        inf.append(b)
        post.append(c)
        tot.append(a + b + c)

    def line(name: str, arr: list[float]) -> str:
        s = sorted(arr)
        return (f"  {name:<10s} P50 {pct(s, .50):7.2f}  P95 {pct(s, .95):7.2f}  "
                f"P99 {pct(s, .99):7.2f}  max {s[-1]:7.2f} ms")

    fps = 1000.0 / (sum(tot) / len(tot))
    print(f"[{tag}]  device={dev}  precision={'FP16' if half else 'FP32'}  n={N}")
    print(line("预处理", pre))
    print(line("模型推理", inf))
    print(line("后处理", post))
    print(line("总感知", tot))
    print(f"  持续吞吐   {fps:6.2f} FPS")
    print()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import torch

    print("=" * 66)
    print("部署后端实测")
    print("=" * 66)
    print(f"  权重   : {MODEL}")
    print(f"  输入   : {SRC_W}x{SRC_H} -> letterbox {IMGSZ}")
    print(f"  torch  : {torch.__version__}")
    print(f"  ROCm?  : cuda_available={torch.cuda.is_available()}"
          + (f"  设备={torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else ""))
    imgs = load_images()
    print("  （CPU 组用的是 ROCm 版 torch 的 CPU 路径，绝对值偏保守，"
          "跨后端比较请看 GPU 侧）")
    print()

    bench("CPU", "cpu", False, imgs)
    if torch.cuda.is_available():
        bench("780M ROCm", "cuda:0", False, imgs)
        bench("780M ROCm", "cuda:0", True, imgs)
    else:
        print("[!] torch 看不到 ROCm 设备，跳过核显测试")
    return 0


if __name__ == "__main__":
    sys.exit(main())
