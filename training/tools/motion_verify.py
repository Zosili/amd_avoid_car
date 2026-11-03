# -*- coding: utf-8 -*-
"""
============================================================
  用运动信息校验标注 —— 专治"把挂着的衣服标成人"
============================================================

为什么需要这个脚本：
    COCO 权重（yolo11n.pt）在宿舍/办公室这类场景里会稳定犯一个错：
    把挂在柜门上、椅背上的**黑色长外套**判成 person。实测一条 25 秒
    的固定机位视频，69 个自动框里有 31 个是这么来的，而且位置几乎
    完全重合（cx=0.76 cy=0.28 w=0.11 h=0.56，重复 21 次）。

    这种误检比漏检更毒：漏检只是少学，误检是教模型"黑挂衣=活体"，
    实车看到窗帘、外套、拖把就会停车鸣叫。而且它在 val 集里同样会
    出现，指标看着还不差。

    好消息是这类视频有个天然判据——**机位是固定的**。挂着的衣服在
    整段视频里一动不动，而人只要走过就一定会动。所以：

        取整段视频的时间中值图像当"背景"
        ↓
        对每张图算 |帧 - 背景|，只看标注框内的平均差异
        ↓
        真人框：差异很大（实测 49~69）
        静止物框：差异接近 0（实测 4~13）
        ↓
        中间是空的，不存在需要拍脑袋的灰区

    阈值默认 30，正好落在那个断层里。实测 69 个框：>30 的 38 个全是
    真人，<30 的 31 个全是挂衣，零误判。

局限（必须知道）：
    ① **只适用于固定机位**。手持走动拍的视频不能用——相机自己在动，
       背景本身就是漂移的，差分全是噪声。脚本会先算"帧-背景"的全局
       均值，超过 MAX_DRIFT 就拒绝执行并提示。
    ② 一个人如果**站着不动超过半段视频**，会被中值背景吸收掉，然后
       被判成静止物。这条视频里人一直在走所以没事；要覆盖"静态的人"
       场景，得另拍有停留的视频，别在这里强求。
    ③ 光照明暗变化（自动曝光）会让全局差分抬高。所以算框内差异之前
       先把整幅图的亮度平移到背景均值，抵消曝光漂移。

怎么用：

    # 只看报告，不动文件
    python tools\\motion_verify.py --video D:\\拍摄\\VID.mp4 --labels datasets\\custom_raw\\labels

    # 删掉静止误检框（先加 --dry 看一眼要删什么）
    python tools\\motion_verify.py --video D:\\拍摄\\VID.mp4 --labels datasets\\custom_raw\\labels --drop

    # 顺便检查漏检（有运动区域没被任何框盖住）
    python tools\\motion_verify.py --video D:\\拍摄\\VID.mp4 --labels datasets\\custom_raw\\labels --check-miss

产物：
    被删的框会追加到 labels/_motion_dropped.txt，可以随时翻回去复核。
============================================================
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from import_phone import to_crop43          # noqa: E402  保证裁法和导入时一致

# 背景图统一在这个分辨率上算，1080p 全分辨率做中值太吃内存且没必要
WORK_SIZE = (640, 480)

# 框内平均差分超过这个值就认为是"动的活物"。实测真人 49~69，静止物 4~13
DROP_THR = 30.0

# 整段视频的"帧-背景"平均差上限，超过说明机位在动，本方法失效
MAX_DRIFT = 40.0


def build_background(video: Path, step: int = 10) -> tuple[np.ndarray, float]:
    """逐帧采样取时间中值，返回 (背景灰度图, 全局漂移均值)。"""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"打不开 {video}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in range(0, max(n, 1), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok or fr is None:
            continue
        g = cv2.cvtColor(to_crop43(fr), cv2.COLOR_BGR2GRAY)
        frames.append(cv2.resize(g, WORK_SIZE, interpolation=cv2.INTER_AREA))
    cap.release()
    if len(frames) < 10:
        raise RuntimeError(f"只采到 {len(frames)} 帧，视频太短或解码失败")
    arr = np.stack(frames).astype(np.float32)
    med = np.median(arr, axis=0)
    drift = float(np.mean(np.abs(arr - med)))
    return med, drift


def load_gray_aligned(path: Path, bg: np.ndarray) -> np.ndarray:
    """读图 -> 缩到工作分辨率 -> 整体亮度对齐到背景均值。

    对齐这一步是必须的：手机自动曝光会让整段视频慢慢变亮/变暗，
    不做对齐的话背景里所有静止物体都会带上 10~20 的假差分，
    和真人的 49 就拉不开距离了。
    """
    im = cv2.imread(str(path))
    if im is None:
        raise RuntimeError(f"读不了 {path}")
    g = cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY),
                   WORK_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    return g - g.mean() + bg.mean()


def read_boxes(txt: Path) -> list[list[float]]:
    if not txt.exists():
        return []
    out = []
    for line in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        v = line.split()
        if len(v) >= 5:
            out.append([float(x) for x in v[:5]])
    return out


def box_energy(diff: np.ndarray, box: list[float]) -> float:
    h, w = diff.shape
    _, cx, cy, bw, bh = box
    x1 = max(0, int((cx - bw / 2) * w))
    x2 = min(w, int((cx + bw / 2) * w))
    y1 = max(0, int((cy - bh / 2) * h))
    y2 = min(h, int((cy + bh / 2) * h))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return float(diff[y1:y2, x1:x2].mean())


def find_uncovered(diff: np.ndarray, boxes: list[list[float]],
                   thr: float = 35.0, min_area: float = 0.01) -> list[tuple]:
    """找有运动、却没被任何框覆盖的区域 —— 即疑似漏检。

    min_area 用"占全图比例"，0.01 相当于 640x480 上的 3072 像素，
    比这小的多半是噪声或被背景吸收的碎影。
    """
    h, w = diff.shape
    mask = (diff > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    cover = np.zeros_like(mask)
    for box in boxes:
        _, cx, cy, bw, bh = box
        x1 = max(0, int((cx - bw / 2) * w))
        x2 = min(w, int((cx + bw / 2) * w))
        y1 = max(0, int((cy - bh / 2) * h))
        y2 = min(h, int((cy + bh / 2) * h))
        cover[y1:y2, x1:x2] = 1

    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area / (w * h) < min_area:
            continue
        comp = (lab[y:y + bh, x:x + bw] == i)
        cov = cover[y:y + bh, x:x + bw] > 0
        ratio = float(comp.sum()) / max(cov.sum(), 1) if cov.sum() else 99.0
        if ratio > 3.0:                    # 盖住的只有运动区域的 1/3 以下
            out.append((x, y, bw, bh, area / (w * h), ratio))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="用固定机位的时间中值背景，校验标注框是不是真的在动")
    ap.add_argument("--video", required=True, help="该批图片对应的源视频")
    ap.add_argument("--labels", required=True, help="标签目录")
    ap.add_argument("--images", default=None,
                    help="图片目录，默认取 --labels 同级的 images")
    ap.add_argument("--prefix", default="", help="只处理该前缀，如 ph_")
    ap.add_argument("--drop", action="store_true", help="真的删掉静止误检框")
    ap.add_argument("--dry", action="store_true", help="配合 --drop 只打印不写文件")
    ap.add_argument("--thr", type=float, default=DROP_THR, help="删除阈值，默认 30")
    ap.add_argument("--check-miss", action="store_true", help="顺便找疑似漏检")
    ap.add_argument("--bg-cache", default=str(TOOLS / "_bg_med.npy"),
                    help="背景缓存路径，避免重复算")
    ap.add_argument("--rebuild-bg", action="store_true", help="强制重算背景")
    args = ap.parse_args()

    lab_dir = Path(args.labels)
    img_dir = Path(args.images) if args.images else lab_dir.parent / "images"

    cache = Path(args.bg_cache)
    if cache.exists() and not args.rebuild_bg:
        bg = np.load(cache)
        drift = 0.0
        print(f"[i] 复用背景缓存 {cache}")
    else:
        print(f"[i] 构建时间中值背景：{Path(args.video).name}")
        bg, drift = build_background(Path(args.video))
        np.save(cache, bg)
        cv2.imwrite(str(cache.with_suffix(".jpg")), bg.astype(np.uint8))
        print(f"    采样漂移均值 {drift:.1f}（>{MAX_DRIFT} 说明机位在动）")

    if drift > MAX_DRIFT:
        print(f"[!] 全局漂移 {drift:.1f} 超过 {MAX_DRIFT}，机位可能在动，"
              f"本方法不可靠，已中止")
        return 2

    pat = f"{args.prefix}*.txt" if args.prefix else "*.txt"
    txts = sorted(lab_dir.glob(pat))
    if not txts:
        print(f"[!] {lab_dir} 下没有匹配 {pat} 的标签")
        return 1

    dropped_log = lab_dir / "_motion_dropped.txt"
    rows, n_keep, n_drop, n_files_changed = [], 0, 0, 0
    for txt in txts:
        stem = txt.stem
        img = None
        for ext in (".jpg", ".png", ".jpeg"):
            cand = img_dir / (stem + ext)
            if cand.exists():
                img = cand
                break
        if img is None:
            print(f"  [!] {stem} 找不到对应图片，跳过")
            continue
        g = load_gray_aligned(img, bg)
        diff = np.abs(g - bg)
        boxes = read_boxes(txt)

        keep, drop = [], []
        for b in boxes:
            e = box_energy(diff, b)
            (keep if e >= args.thr else drop).append((b, e))
            rows.append((stem, b[3] * b[4], e))

        if args.check_miss:
            total = [b for b, _ in keep] + [b for b, _ in drop]
            for x, y, bw, bh, ratio, cov in find_uncovered(diff, total):
                print(f"  [漏检?] {stem}  运动区域 x={x} y={y} {bw}x{bh} "
                      f"占 {ratio * 100:.1f}%  框覆盖仅 1/{cov:.1f}")

        if drop and args.drop:
            n_files_changed += 1
            if not args.dry:
                lines = []
                for b, _ in keep:
                    lines.append("%d %.6f %.6f %.6f %.6f" % tuple(b))
                txt.write_text("\n".join(lines) + ("\n" if lines else ""),
                               encoding="utf-8")
                with dropped_log.open("a", encoding="utf-8") as f:
                    for b, e in drop:
                        f.write("%s %.1f %d %.6f %.6f %.6f %.6f\n"
                                % (stem, e, int(b[0]), b[1], b[2], b[3], b[4]))
        n_keep += len(keep)
        n_drop += len(drop)

    print()
    print("=" * 62)
    print(f"  扫描 {len(txts)} 个标签，共 {len(rows)} 个框")
    if rows:
        e = np.array([r[2] for r in rows])
        print(f"  差分能量 p10={np.percentile(e, 10):.1f} "
              f"p50={np.percentile(e, 50):.1f} p90={np.percentile(e, 90):.1f}")
    print(f"  保留 {n_keep}  删除 {n_drop}"
          f"（阈值 {args.thr}，涉及 {n_files_changed} 个文件）")

    if args.drop and n_drop and not args.dry:
        print(f"  删除记录 -> {dropped_log}")
    elif n_drop and not args.drop:
        print("  未写入。要执行删除加 --drop（建议先加 --dry 看清单）")
        low = sorted([r for r in rows if r[2] < args.thr], key=lambda z: z[2])
        for stem, a, e in low[:15]:
            print(f"    {stem:<12} a={a:.3f} 差分={e:.1f}")
        if len(low) > 15:
            print(f"    ... 共 {len(low)} 个")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
