# -*- coding: utf-8 -*-
"""
============================================================
  角落黑块清理 —— 贴边暗块 -> 用邻近地面纹理补掉
============================================================

为什么需要：
    手机放地上拍的时候，左下角容易被外物（包带、垫子卷边、支架腿）
    切掉一块。实测量化（1280x960，B 组 29 张）：

        黑块 x=0..205, y=766..960，占全图 1.0~1.7%
        形状 = 斜切四边形，顶点 (204,772)，斜边 -45° 落到 (0,877)

    它带来两个问题：
      ① 模型可能把这个"角落暗块"当成场景特征。真实车上没有这块
         黑，等于喂了一个永远不成立的捷径特征（shortcut）。
      ② 框底边 y 是单目测距的依据，这块黑贴在最下方地面上，
         紧挨着人脚，容易干扰"脚-地"的判断。

怎么补，为什么不是裁剪：
    裁掉底部 20% 地面会丢掉最近距离的视野——低视角小车的核心信息
    就在画面最下方那一条。裁左边同理。而且裁剪后归一化标签要重算。
    所以这里**保持几何不变**，只把黑块区域用右侧同一水平线上的地面
    纹理搬过来填平：同一 y 上的地面透视尺度一致，地毯又是各向同性
    的噪声纹理，贴过去肉眼看不出来。

============================================================
  重要：什么样的暗块**不能**补
============================================================
    踩过的坑，C 组三张图里的"左下角暗块"其实是：
        C_000001  人贴近镜头，大片黑衣占 2.9%
        C_000008  蹲在地上的人，黑裤子占 3.2%
    这些是训练样本本身，补掉等于把人抹掉。所以判据必须是四条全中，
    少一条都不动：

        a) 同时贴住 left 和 bottom 两条边（真角落，不是画面中间家具）
        b) 宽 <= 22% 图宽、高 <= 30% 图高（排除人的躯干/腿）
        c) 面积占全图 0.5% ~ 2.5%
        d) 和任何标注框的重叠 < 5%（有框就是目标，不碰）

    b) 是最关键的：B 组黑块 205x194（宽 16%、高 20%），
    C 组黑衣 448+x384（宽 >35%、高 40%），一比就分开了。

怎么用：
    # 先看报告（不动文件）
    python tools\\clean_corner.py --src datasets\\custom_raw\\images

    # 确认要改再写（建议先备份）
    python tools\\clean_corner.py --src datasets\\custom_raw\\images --write

注意：数据集里 custom_raw 的图和 living 的是**硬链接**（同一 inode）。
cv2.imwrite 是原地截断写，不会断开链接，所以训练集内容会同步更新，
不需要重建。但如果你先把文件删了再写，链接就断了，必须重跑
build_living_dataset.py。
============================================================
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

CORNERS = {
    # name: (x 窗口起止比例, y 窗口起止比例)
    "lb": ((0.0, 0.24), (0.60, 1.0)),   # 左下
    "rb": ((0.76, 1.0), (0.60, 1.0)),   # 右下
    "lt": ((0.0, 0.24), (0.0, 0.40)),   # 左上
    "rt": ((0.76, 1.0), (0.0, 0.40)),   # 右上
}

# ---- 判据门槛（解释见文件头）----
MAX_W_RATIO = 0.25
MAX_H_RATIO = 0.25          # 只占画面下部 1/4 的才可能是"贴边遮挡"
MIN_TOP_RATIO = 0.70        # bbox 顶边必须在 70% 图高以下
MIN_PCT, MAX_PCT = 0.5, 4.5
MAX_LABEL_OVERLAP = 0.05
DEFAULT_THR = 90            # 黑块表面有反光，45 只能抓到下半截，见文件头


# ---------------------------------------------------------------- 标签
def load_boxes(label_path: Path) -> list[tuple[float, float, float, float, int]]:
    """读 YOLO 归一化框 (cx, cy, w, h, cls)。文件不存在返回空表。"""
    boxes = []
    if not label_path.exists():
        return boxes
    for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        v = line.split()
        if len(v) >= 5:
            try:
                boxes.append((float(v[1]), float(v[2]), float(v[3]), float(v[4]), int(v[0])))
            except ValueError:
                continue
    return boxes


def overlap_with_boxes(mask: np.ndarray, boxes) -> float:
    """mask 像素落在任何标注框内的比例。>5% 就认为这是目标不是遮挡。"""
    if not boxes:
        return 0.0
    h, w = mask.shape
    total = float((mask > 0).sum())
    if total == 0:
        return 0.0
    inside = np.zeros((h, w), bool)
    for cx, cy, bw, bh, _ in boxes:
        x0 = int(max(0, (cx - bw / 2) * w))
        x1 = int(min(w, (cx + bw / 2) * w))
        y0 = int(max(0, (cy - bh / 2) * h))
        y1 = int(min(h, (cy + bh / 2) * h))
        if x1 > x0 and y1 > y0:
            inside[y0:y1, x0:x1] = True
    return float((mask.astype(bool) & inside).sum()) / total


# ---------------------------------------------------------------- 找暗块
def find_dark_blob(gray: np.ndarray, corner: str = "lb", thr: int = 45,
                   min_pct: float = MIN_PCT, max_pct: float = MAX_PCT,
                   boxes=None):
    """在角落窗口里找"贴两边 + 尺寸受限 + 面积合适 + 不和标注框重叠"的暗块。

    返回 (mask, info)。mask 是全图 uint8，1=要补的像素；找不到返回 (None, info)。
    """
    h, w = gray.shape
    (rx0, rx1), (ry0, ry1) = CORNERS[corner]
    x0, x1 = int(w * rx0), int(w * rx1)
    y0, y1 = int(h * ry0), int(h * ry1)
    # 底部/右侧各让 3px：实拍视频最底一行常有一条贯穿全宽的暗线
    # （编码或缩放的边界伪影），它会把左下角暗块和画面右边的暗区连成
    # 一个连通域，bbox 直接撑到 1280 宽，尺寸判据全部失效。
    roi = gray[y0:y1 - 3, x0:x1 - 3]
    rh, rw = roi.shape

    dark = (roi < thr).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                            np.ones((3, 3), np.uint8))   # 再断一次 1~2px 细桥
    if dark.sum() == 0:
        return None, {"reason": "窗口内没有暗像素"}

    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n <= 1:
        return None, {"reason": "没有连通暗块"}

    touches = {"lb": ("left", "bottom"), "rb": ("right", "bottom"),
               "lt": ("left", "top"), "rt": ("right", "top")}[corner]

    cands = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 200:
            continue
        # a) 必须同时贴住外侧两条边
        ok = True
        for t in touches:
            if t == "left" and x > 2:
                ok = False
            elif t == "right" and x + bw < rw - 3:
                ok = False
            elif t == "top" and y > 2:
                ok = False
            elif t == "bottom" and y + bh < rh - 3:
                ok = False
        if not ok:
            continue
        # b) 尺寸上限（排除人的躯干/腿）
        if bw > w * MAX_W_RATIO or bh > h * MAX_H_RATIO:
            continue
        # b2) 必须"从画面底部往上长"。人的黑衣/黑裤是从画面中部就开始的，
        #     这条比尺寸更可靠：C_000001 的黑衣顶边在 60% 图高处，直接出局。
        if (y0 + y) < h * MIN_TOP_RATIO:
            continue
        # c) 面积区间
        pct = area / (w * h) * 100
        if not (min_pct <= pct <= max_pct):
            continue
        cands.append((area, i, pct, (x + x0, y + y0, bw, bh)))   # 换成全图坐标

    if not cands:
        return None, {"reason": "有暗像素，但没一块同时满足 贴两边/尺寸/面积"}

    cands.sort(reverse=True)
    area, idx, pct, (bx, by, bbw, bbh) = cands[0]

    full = np.zeros((h, w), np.uint8)
    full[y0:y0 + rh, x0:x0 + rw] = (lab == idx).astype(np.uint8)

    # d) 和标注框重叠就放过（那是目标本身）
    ov = overlap_with_boxes(full, boxes)
    info = {"pct": pct, "area": int(area), "bbox": (bx, by, bbw, bbh),
            "label_overlap": ov}
    if ov > MAX_LABEL_OVERLAP:
        return None, {**info, "reason": f"与标注框重叠 {ov*100:.0f}%，是目标，跳过"}

    return full, info


# ---------------------------------------------------------------- 填洞
def _mask_from_blob(blob: np.ndarray, dilate: int = 7) -> np.ndarray:
    """把暗块轮廓补成实心凸包再膨胀。

    为什么用凸包：黑块的表面有反光，阈值只能抓到最黑的一圈边，
    内部是灰的，直接填会留下一堆洞。凸包一次把整块包住。
    """
    cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return blob
    hull = cv2.convexHull(np.vstack(cnts))
    solid = np.zeros_like(blob)
    cv2.fillPoly(solid, [hull], 1)
    if dilate > 0:
        solid = cv2.dilate(solid, np.ones((dilate, dilate), np.uint8))
    return solid


def _match_mean(patch: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """把 patch 的亮度平移到和参考像素一致。

    保留但**不在主流程里用**。试过用 mask 外扩一圈当参考，结果 mask 贴着
    左边和底边，外扩的一圈全落在黑块那条渐变暗边上，参考值只有 72
    （地毯实际约 120），补上去比周围暗一大块。同图同 y 的地面亮度本来
    就一致，直接贴才对。留着这个函数是为了以后换场景时还有得选。
    """
    p = patch.astype(np.float32)
    m_p = p.reshape(-1, 3).mean(axis=0)
    m_r = ring.reshape(-1, 3).mean(axis=0)
    return np.clip(p + (m_r - m_p), 0, 255).astype(np.uint8)


def _ring_of(bgr: np.ndarray, mask: np.ndarray, grow: int = 12) -> np.ndarray:
    """取 mask 外扩一圈、但不含 mask 本身的像素，作为亮度参考。"""
    k = np.ones((grow * 2 + 1, grow * 2 + 1), np.uint8)
    outer = cv2.dilate(mask, k)
    ring = (outer > 0) & (mask == 0)
    px = bgr[ring]
    if len(px) < 30:
        px = bgr[mask == 0]
    return px if len(px) else bgr.reshape(-1, 3)


def fill_by_clone(bgr: np.ndarray, mask: np.ndarray, boxes=None) -> tuple[np.ndarray, str]:
    """优先：把右侧同一水平线的地面搬过来（透视尺度一致，最自然）。

    不做亮度匹配。踩过的坑：一开始拿 mask 外扩一圈当参考来对齐亮度，
    但 mask 本身贴着左边和底边，外扩的一圈全落在黑块那条渐变暗边上，
    算出来的参考值只有 72（地毯实际约 120），结果补上去比周围暗一大块。
    同一张图同一 y 的地面亮度本来就一致，直接贴就对了。
    """
    h, w = mask.shape
    ys, xs = np.where(mask > 0)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0

    gap = max(30, bw // 3)
    src = None
    for sx0 in (x1 + gap, x0 - bw - gap, w - bw):
        sx1 = sx0 + bw
        if sx0 < 0 or sx1 > w:
            continue
        if mask[y0:y1, sx0:sx1].sum() > 0:
            continue
        patch = bgr[y0:y1, sx0:sx1]
        if patch.shape[:2] != (bh, bw):
            continue
        # 源块里如果有目标（人脚走过去），换一个位置，别把脚搬进来
        if boxes and overlap_with_boxes(mask[y0:y1, sx0:sx1], boxes) > 0.10:
            continue
        src = (patch, sx0)
        break

    if src is None:
        return _fill_by_flip(bgr, mask, y0, y1, x0, x1)

    patch, sx0 = src

    # 硬贴 + 1px 过渡。之前用 5x5 高斯羽化，三角边缘留了半透明黑边，这里收紧
    alpha = cv2.GaussianBlur(mask[y0:y1, x0:x1].astype(np.float32), (3, 3), 0)[..., None]
    dst = bgr[y0:y1, x0:x1].astype(np.float32)
    out = bgr.copy()
    out[y0:y1, x0:x1] = (patch.astype(np.float32) * alpha +
                         dst * (1 - alpha)).astype(np.uint8)
    return out, f"右侧平移 x={sx0}"


def _fill_by_flip(bgr, mask, y0, y1, x0, x1) -> tuple[np.ndarray, str]:
    """退路一：垂直翻转上方地面。"""
    bh = y1 - y0
    sy1 = max(bh, y0 - 10)
    sy0 = sy1 - bh
    if sy0 < 0:
        return _fill_by_inpaint(bgr, mask)
    patch = cv2.flip(bgr[sy0:sy1, x0:x1], 0)
    alpha = cv2.GaussianBlur(mask[y0:y1, x0:x1].astype(np.float32), (3, 3), 0)[..., None]
    dst = bgr[y0:y1, x0:x1].astype(np.float32)
    out = bgr.copy()
    out[y0:y1, x0:x1] = (patch.astype(np.float32) * alpha +
                         dst * (1 - alpha)).astype(np.uint8)
    return out, f"垂直翻转 y={sy0}..{sy1}"


def _fill_by_inpaint(bgr, mask) -> tuple[np.ndarray, str]:
    """退路二：inpaint。贴边时先补镜像边，否则边界留黑晕。"""
    pad = 16
    big = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_REFLECT_101)
    mbig = cv2.copyMakeBorder(mask, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    out = cv2.inpaint(big, mbig, 9, cv2.INPAINT_TELEA)
    return out[pad:pad + bgr.shape[0], pad:pad + bgr.shape[1]], "inpaint(TELEA)"


def clean(bgr: np.ndarray, corner: str = "lb", thr: int = 45,
          min_pct: float = MIN_PCT, max_pct: float = MAX_PCT,
          dilate: int = 7, boxes=None):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blob, info = find_dark_blob(gray, corner, thr, min_pct, max_pct, boxes)
    if blob is None:
        return bgr, None, info
    mask = _mask_from_blob(blob, dilate)
    out, how = fill_by_clone(bgr, mask, boxes)
    return out, how, info


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="清掉贴边角落的黑块（纹理克隆填充）")
    ap.add_argument("--src", required=True, help="图片目录或单张图")
    ap.add_argument("--labels", default="", help="标签目录，默认取 <src 同级>/labels")
    ap.add_argument("--corner", default="lb", choices=list(CORNERS),
                    help="要清理的角，默认 lb 左下")
    ap.add_argument("--thr", type=int, default=DEFAULT_THR,
                    help=f"灰度阈值，默认 {DEFAULT_THR}（低到 45 只能抓到黑块下半截）")
    ap.add_argument("--min-pct", type=float, default=MIN_PCT, help="面积下限 %，默认 0.5")
    ap.add_argument("--max-pct", type=float, default=MAX_PCT, help="面积上限 %，默认 2.5")
    ap.add_argument("--dilate", type=int, default=7, help="mask 膨胀核，默认 7")
    ap.add_argument("--write", action="store_true", help="真的写回文件")
    ap.add_argument("--backup", default="", help="写之前把原图复制到该目录")
    ap.add_argument("--preview", default="", help="对比图保存路径")
    ap.add_argument("--preview-n", type=int, default=4, help="对比图放几张，默认 4")
    args = ap.parse_args()

    src = Path(args.src)
    files = ([src] if src.is_file()
             else sorted(p for p in src.rglob("*") if p.suffix.lower() in IMAGE_EXT))
    if not files:
        print(f"[!] {src} 里没有图片")
        return 1

    lab_dir = Path(args.labels) if args.labels else src.parent / "labels"

    if args.backup and args.write:
        Path(args.backup).mkdir(parents=True, exist_ok=True)

    hit = 0
    skip_reason: dict[str, int] = {}
    total_pct = 0.0
    hows: dict[str, int] = {}
    pairs = []

    print("=" * 74)
    print(f" 角落黑块清理  corner={args.corner} thr={args.thr} "
          f"[{args.min_pct}%, {args.max_pct}%]  写入={'开' if args.write else '关(仅报告)'}")
    print("=" * 74)

    for p in files:
        bgr = cv2.imread(str(p))
        if bgr is None:
            print(f"  [!] 读不了 {p.name}")
            continue
        boxes = load_boxes(lab_dir / (p.stem + ".txt"))
        out, how, info = clean(bgr, args.corner, args.thr,
                              args.min_pct, args.max_pct, args.dilate, boxes)
        if how is None:
            r = info.get("reason", "?")
            key = r.split("，")[0].split("（")[0]
            skip_reason[key] = skip_reason.get(key, 0) + 1
            continue

        hit += 1
        total_pct += info["pct"]
        key = how.split(" ")[0]
        hows[key] = hows.get(key, 0) + 1
        x, y, bw, bh = info["bbox"]
        print(f"  {p.name:<18} {info['pct']:5.2f}%  "
              f"x{x}..{x+bw} y{y}..{y+bh}  {bw}x{bh}  <- {how}")

        if len(pairs) < args.preview_n:
            a = bgr.copy()
            cv2.rectangle(a, (x, y), (x + bw, y + bh), (0, 0, 255), 3)
            pairs.append((a, out))

        if args.write:
            if args.backup:
                shutil.copy2(p, Path(args.backup) / p.name)
            ok = cv2.imwrite(str(p), out,
                             [cv2.IMWRITE_JPEG_QUALITY, 95] if p.suffix.lower()
                             in {".jpg", ".jpeg"} else [])
            if not ok:
                print(f"  [!] 写失败 {p.name}")

    print("-" * 74)
    print(f"  命中 {hit} / {len(files)} 张"
          + (f"，平均占比 {total_pct/hit:.2f}%" if hit else ""))
    if hows:
        print(f"  填充方式 {hows}")
    if skip_reason:
        print("  跳过原因：")
        for k, v in sorted(skip_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {v:3d} 张  {k}")

    if args.preview and pairs:
        rows = []
        for a, b in pairs:
            sep = np.full((a.shape[0], 6, 3), 255, np.uint8)
            rows.append(np.hstack([a, sep, b]))
        img = np.vstack(rows)
        if img.shape[1] > 1700:                     # 压一下宽度方便看
            s = 1700 / img.shape[1]
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        Path(args.preview).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(args.preview, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"  对比图 {args.preview}（每行 左=原始红框 / 右=清理后）")

    if not args.write and hit:
        print("  加 --write 才会真改文件")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
