# -*- coding: utf-8 -*-
"""
============================================================
 手机实拍导入 —— 视频/照片 -> 训练用 YOLO 数据集
============================================================

为什么需要这个脚本：
    手机拍的东西不能直接扔进数据集，有四处必须对齐，否则模型在车上
    会水土不服。这个脚本把四件事一次做完，不需要你记参数。

    ① 宽高比 4:3
       手机视频几乎都是 16:9，而 OV5640 是 640x480 的 4:3。
       直接把 16:9 缩成 640x480 会把画面横向压扁 25%，人看起来
       又瘦又高，等于教模型一个错误的世界。所以默认中心裁掉左右
       各 1/12，得到 4:3。（顺带把水平视场从 ~70° 收到 ~58°，
       反而更接近 OV5640 模组。）

    ② 色深 RGB565（默认关，实测收益很小）
       板子输出 RGB565（5/6/5 位），色阶只有 32/64/32 级。理论上
       训练在 24 位上、推理在 16 位上是个域差距，但实测发现：
         - 存 JPEG 会把量化彻底破坏（q=95 解码回来又是 256 级），
           只有存 PNG 才保得住，体积大 5 倍；
         - 训练时 YOLO 把 1280x960 缩到 640x480，双线性插值本身
           就把色带抹平了；
         - 训练还带 HSV 增强，色彩本来就被随机扰动。
       所以默认不做。要深究的话加 --rgb565，它会自动改成存 PNG。

    ③ 输出尺寸 1280x960
       不直接缩到 640x480，留 2 倍细节方便标注（小目标看得清）。
       YOLO 训练时按 imgsz 自动缩放，标签是归一化的，不受影响。

    ④ 角落黑块（默认开，见 clean_corner.py）
       手机放地上拍，左下角容易被包带、垫子卷边、支架腿切掉一块。
       实测量化：B 组 29 张全部命中，黑块 233x209、占全图 3.1%，
       贴在最下方地面上、紧挨人脚——它既是"真实车上不存在"的假特征，
       又会干扰靠框底边 y 做的单目测距。这里在导出后自动用邻近地面
       纹理补掉，几何不变、标签不用重算。
       补之前有四条必须全部满足的判据（贴两边/尺寸/只占下部/不与标注框
       重叠），C 组那种"人贴到镜头前"的黑衣不会被误伤。细节见
       tools/clean_corner.py 文件头。
       关掉：--no-clean-corner

怎么用：

    # 单段视频
    python tools\\import_phone.py --src D:\\拍摄\\VID_001.mp4

    # 整个目录（递归找 mp4/jpg/png）
    python tools\\import_phone.py --src D:\\拍摄 --interval 1.0

    # 带预标注（COCO 权重先标一遍，人工只删不改）
    python tools\\import_phone.py --src D:\\拍摄 --prelabel

    # 抽帧密一点（快速动作/人走过）
    python tools\\import_phone.py --src VID.mp4 --interval 0.5

产物在 datasets/custom_raw/{images,labels,classes.txt,meta.json}，
随后用 build_living_dataset.py --person-cls -1 并进主数据集。
============================================================
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
DEFAULT_OUT = PROJECT / "datasets" / "custom_raw"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from clean_corner import clean as clean_corner_bgr   # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

# 去重：缩到这么小比灰度差，小于阈值就当重复帧丢掉
DEDUP_SIZE = (64, 48)


# ---------------------------------------------------------------- 帧读取
def read_frame_at(cap: cv2.VideoCapture, idx: int, tries: int = 4):
    """seek 到指定帧读取，失败时往回退几帧重试。

    为什么需要重试：1080p HDR 视频在部分解码路径上 seek 到某个偏移
    会返回空帧（我实测踩到过），退 2 帧通常就好了。
    """
    j = idx
    for _ in range(tries):
        cap.set(cv2.CAP_PROP_POS_FRAMES, j)
        ok, fr = cap.read()
        if ok and fr is not None:
            return fr
        j = max(0, j - 2)
    return None


def fingerprint(bgr: np.ndarray) -> np.ndarray:
    small = cv2.resize(bgr, DEDUP_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


# ---------------------------------------------------------------- 后处理
def to_crop43(bgr: np.ndarray) -> np.ndarray:
    """中心裁成 4:3。宽了裁左右，高了裁上下。"""
    h, w = bgr.shape[:2]
    want_w = h * 4 // 3
    if w > want_w:
        x0 = (w - want_w) // 2
        return bgr[:, x0:x0 + want_w]
    want_h = w * 3 // 4
    if h > want_h:
        y0 = (h - want_h) // 2
        return bgr[y0:y0 + want_h, :]
    return bgr


def to_rgb565(bgr: np.ndarray) -> np.ndarray:
    """往返量化成 RGB565 再解回 8 位，模拟板上真实色深。

    位扩展用「高位复制」而不是简单左移：
        R5 -> R8 : (r << 3) | (r >> 2)
        G6 -> G8 : (g << 2) | (g >> 4)
    这是硬件和 OpenCV cvtColor 内部都采用的方式。只左移的话 255
    会变成 248，整幅图整体偏暗一档，白白引入一个假偏差。

    注意：不能用 cv2.COLOR_BGR5652BGR —— 那个函数的输入要求是
    3 通道 8 位，不是单通道 16 位，传 uint16 单通道会直接抛
    「Invalid number of channels」。detect_realtime.py 里原有的
    udp_to_bgr() 就踩了这个坑，本脚本不再复用它的实现。
    """
    b = bgr[:, :, 0] >> 3
    g = bgr[:, :, 1] >> 2
    r = bgr[:, :, 2] >> 3
    b = (b << 3) | (b >> 2)
    g = (g << 2) | (g >> 4)
    r = (r << 3) | (r >> 2)
    return np.dstack([b, g, r]).astype(np.uint8)


def postprocess(bgr: np.ndarray, size: tuple[int, int],
                crop43: bool, rgb565: bool) -> np.ndarray:
    if crop43:
        bgr = to_crop43(bgr)
    if (bgr.shape[1], bgr.shape[0]) != size:
        bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    if rgb565:
        bgr = to_rgb565(bgr)
    return bgr


# ---------------------------------------------------------------- 收集输入
def collect(src: Path) -> tuple[list[Path], list[Path]]:
    videos, images = [], []
    if src.is_file():
        (videos if src.suffix.lower() in VIDEO_EXT else images).append(src)
        return videos, images
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            videos.append(p)
        elif ext in IMAGE_EXT:
            images.append(p)
    return videos, images


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(
        description="把手机拍的视频/照片导成 YOLO 训练集（自动 4:3 + RGB565）")
    ap.add_argument("--src", required=True, help="视频文件 / 图片目录 / 混合目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出根目录")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="视频抽帧间隔（秒），默认 1.0。人走得快就 0.5")
    ap.add_argument("--size", default="1280x960", help="输出尺寸，默认 1280x960")
    ap.add_argument("--no-crop43", action="store_true", help="不做 4:3 裁剪")
    ap.add_argument("--rgb565", action="store_true",
                    help="模拟 RGB565 色深（自动改存 PNG，体积约 5 倍；默认关，见文件头）")
    ap.add_argument("--dedup", type=float, default=1.2,
                    help="去重阈值（灰度平均绝对差），越大越宽松；0 关闭")
    ap.add_argument("--max", type=int, default=0, help="最多导出多少张，0=不限")
    ap.add_argument("--prelabel", action="store_true",
                    help="用 COCO 权重预标注（conf 调低多召回，人工再删）")
    ap.add_argument("--conf", type=float, default=0.15, help="预标注置信度阈值")
    ap.add_argument("--prefix", default="ph", help="文件名前缀，默认 ph")
    ap.add_argument("--no-clean-corner", action="store_true",
                    help="不清左下角贴边黑块（默认清，见文件头 ④）")
    ap.add_argument("--corner", default="lb", choices=["lb", "rb", "lt", "rt"],
                    help="清理哪个角，默认 lb 左下")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"[!] 找不到 {src}")
        return 1

    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        print(f"[!] --size 格式应为 1280x960，收到 {args.size}")
        return 1

    videos, images = collect(src)
    if not videos and not images:
        print(f"[!] {src} 里没有可用的视频或图片")
        return 1

    out = Path(args.out)
    img_dir = out / "images"
    lab_dir = out / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lab_dir.mkdir(parents=True, exist_ok=True)

    # 已有文件时顺延编号，不覆盖之前导入的
    ext = ".png" if args.rgb565 else ".jpg"
    existing = sorted(p for p in img_dir.glob(f"{args.prefix}_*.*")
                      if p.suffix.lower() in IMAGE_EXT)
    seq = len(existing)
    if seq:
        print(f"[i] {img_dir} 已有 {seq} 张，本次从 {args.prefix}_{seq + 1:06d} 继续")

    print("=" * 62)
    print(" 手机实拍导入")
    print("=" * 62)
    print(f"  来源   : {src}")
    print(f"  发现   : {len(videos)} 个视频 / {len(images)} 张图")
    print(f"  抽帧   : 每 {args.interval}s 一帧，去重阈值 {args.dedup}")
    print(f"  处理   : 4:3={'关' if args.no_crop43 else '开'}  "
          f"RGB565={'开(存PNG)' if args.rgb565 else '关'}  -> {w}x{h} {ext}")

    kept = 0
    n_dup = 0
    n_fail = 0
    n_cleaned = 0
    items = []
    sources = []
    t0 = time.time()

    def emit(bgr, meta_extra: dict) -> bool:
        nonlocal seq, kept, n_cleaned
        if args.max and kept >= args.max:
            return False
        proc = postprocess(bgr, (w, h), not args.no_crop43, args.rgb565)
        how = None
        if not args.no_clean_corner:
            # 放在 resize 之后：尺寸固定了判据里的比例才稳定
            proc, how, _info = clean_corner_bgr(proc, args.corner)
            if how:
                n_cleaned += 1
        seq += 1
        name = f"{args.prefix}_{seq:06d}{ext}"
        if args.rgb565:
            # 无损。用 JPEG 会把 565 的量化彻底抹掉，等于白做
            cv2.imwrite(str(img_dir / name), proc)
        else:
            cv2.imwrite(str(img_dir / name), proc, [cv2.IMWRITE_JPEG_QUALITY, 95])
        items.append({"file": name, "corner_cleaned": bool(how), **meta_extra})
        kept += 1
        return True

    for v in videos:
        cap = cv2.VideoCapture(str(v))
        if not cap.isOpened():
            print(f"  [!] 打不开 {v.name}，跳过")
            n_fail += 1
            continue
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps * args.interval)))
        print(f"\n  -- {v.name}  {n_frames} 帧 @ {fps:.1f}fps  "
              f"({n_frames / fps:.0f}s)  每 {step} 帧取一张")
        last_fp = None
        took = 0
        idx = 0
        while idx < n_frames:
            if args.max and kept >= args.max:
                break
            fr = read_frame_at(cap, idx)
            if fr is None:
                idx += step
                continue
            fp = fingerprint(fr)
            if args.dedup > 0 and last_fp is not None:
                if float(np.mean(np.abs(fp - last_fp))) < args.dedup:
                    n_dup += 1
                    idx += step
                    continue
            last_fp = fp
            if not emit(fr, {"source": v.name, "frame": idx}):
                break
            took += 1
            print(f"     f{idx:<6d} -> {args.prefix}_{seq:06d}{ext}", flush=True)
            idx += step
        cap.release()
        sources.append({"path": str(v), "kind": "video",
                        "frames": n_frames, "fps": round(fps, 2), "took": took})

    for p in images:
        if args.max and kept >= args.max:
            break
        fr = cv2.imread(str(p))
        if fr is None:
            print(f"  [!] 读不了 {p.name}")
            n_fail += 1
            continue
        if not emit(fr, {"source": p.name, "frame": -1}):
            break
    if images:
        sources.append({"path": str(src), "kind": "images",
                        "count": len(images), "took": len(images)})

    dt = time.time() - t0
    print()
    print("=" * 62)
    print(f"  导出 {kept} 张，丢弃重复 {n_dup} 张，失败 {n_fail} 个，"
          f"用时 {dt:.1f}s")
    if not args.no_clean_corner:
        print(f"  清角   {n_cleaned} 张左下角黑块已补掉（{args.corner}）")
    print(f"  图片目录 {img_dir}")
    print(f"  尺寸 {w}x{h}   总计 {seq} 张")

    # classes.txt 给 labelImg / review_labels 用
    try:
        sys.path.insert(0, str(TOOLS))
        from living_semantics import CLASS_NAMES
        (out / "classes.txt").write_text(
            "\n".join(CLASS_NAMES) + "\n", encoding="utf-8")
        print(f"  类别表 {out / 'classes.txt'}（{len(CLASS_NAMES)} 类）")
    except Exception as exc:                      # noqa: BLE001
        print(f"  [!] 写 classes.txt 失败：{exc}")

    (out / "meta.json").write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": {"interval": args.interval, "size": f"{w}x{h}",
                   "crop43": not args.no_crop43,
                   "rgb565": args.rgb565,
                   "clean_corner": None if args.no_clean_corner else args.corner,
                   "dedup": args.dedup, "prefix": args.prefix},
        "sources": sources,
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 62)

    if args.prelabel and kept:
        print()
        print("  [预标注] 用 COCO 权重先标一遍（person/cat/dog/bird）")
        cmd = [sys.executable, str(TOOLS / "autolabel.py"),
               "--src", str(img_dir), "--conf", str(args.conf)]
        r = subprocess.run(cmd, cwd=str(PROJECT))
        if r.returncode != 0:
            print(f"  [!] 预标注退出码 {r.returncode}，标签可能不全")

    print()
    print("  下一步：")
    print(f"    1) 审核/删误检  python tools\\review_labels.py --src {img_dir}")
    print(f"    2) 补漏检        labelImg {img_dir} {out / 'classes.txt'}")
    print(f"    3) 并进主数据集  python tools\\build_living_dataset.py "
          f"--clean --person-yolo {out} --person-cls -1")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
