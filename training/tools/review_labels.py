# -*- coding: utf-8 -*-
"""
================================================================
 标注审核 —— 按键过一遍预标注结果
================================================================

为什么需要它：
    tools/autolabel.py 的实测精度是「精确 76%」—— 意思是自动标出来的
    框里约 1/4 是误检。直接拿去训练会把模型带歪，必须人工过一遍。
    但用 labelImg 逐框检查太慢：43 个框要看 43 次。

    这个工具把粒度从"框"提到"图"：多数图瞄一眼按 y 就过，
    只有个别误检按数字键删掉。实测比 labelImg 快 3~5 倍。

用法（在 D:\\FPGA\\ultralytics 下）：
    python tools\\review_labels.py --src datasets\\custom_raw\\images

键位：
    y / 空格 / Enter   整张接受，下一张      ← 绝大多数情况按这个
    1..9               删掉第 N 个框，原地重绘
    n                  清空这张的所有框（整张都是误检）
    a                  接受，并且之后不再询问这张（等同于 y）
    s                  跳过，不做任何修改
    d                  切换"只看图"（把框藏起来，看清有没有漏检）
    ←/→                上一张 / 下一张
    q / Esc            退出

    ⚠ 这个工具**不会**帮你补漏检。漏掉的人得自己加 ——
      用 labelImg 打开同一个目录补，标注格式是通的。

进度会记在 labels/.reviewed.txt 里，重开时自动跳过已审核的图。

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import CLASS_NAMES, KIND_COLOR_BGR, kind_of  # noqa: E402


def read_txt(p: Path):
    boxes = []
    if p.exists():
        for line in p.read_text().splitlines():
            q = line.split()
            if len(q) >= 5:
                boxes.append(q[:5])
    return boxes


def write_txt(p: Path, boxes) -> None:
    p.write_text("\n".join(" ".join(b) for b in boxes) + ("\n" if boxes else ""))


def render(im, boxes, show: bool, idx_hi: int = -1):
    h, w = im.shape[:2]
    vis = im.copy()
    if not show:
        return vis
    for i, b in enumerate(boxes):
        cid = int(float(b[0]))
        cx, cy, bw, bh = [float(v) for v in b[1:5]]
        x1 = int((cx - bw / 2) * w); y1 = int((cy - bh / 2) * h)
        x2 = int((cx + bw / 2) * w); y2 = int((cy + bh / 2) * h)
        name = CLASS_NAMES[cid] if 0 <= cid < len(CLASS_NAMES) else f"#{cid}"
        col = KIND_COLOR_BGR.get(kind_of(name), (200, 200, 200))
        thick = 3 if i == idx_hi else 2
        cv2.rectangle(vis, (x1, y1), (x2, y2), col, thick)
        tag = f"{i+1}:{name}"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(vis, (x1, max(0, y1 - th - 5)), (x1 + tw + 4, y1), col, -1)
        cv2.putText(vis, tag, (x1 + 2, max(10, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return vis


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="图片目录（labels 在同级）")
    ap.add_argument("--redo", action="store_true", help="重新审核已标记过的图")
    ap.add_argument("--max-width", type=int, default=1100,
                    help="窗口最大宽度，大图会等比缩小显示")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print(f"[!] {src} 不存在")
        return 2
    lab_dir = src.parent / "labels"
    lab_dir.mkdir(parents=True, exist_ok=True)
    done_file = lab_dir / ".reviewed.txt"

    done: set[str] = set()
    if done_file.exists() and not args.redo:
        done = {ln.strip() for ln in done_file.read_text().splitlines() if ln.strip()}

    files = sorted(list(src.glob("*.jpg")) + list(src.glob("*.png")))
    todo = [p for p in files if p.stem not in done]

    print("=" * 62)
    print("标注审核")
    print("=" * 62)
    print(f"  目录   : {src}")
    print(f"  共 {len(files)} 张，未审核 {len(todo)} 张"
          f"{'（已审核的会跳过）' if done and not args.redo else ''}")
    print()
    print("  y/空格 = 接受下一张    1..9 = 删第N个框    n = 清空")
    print("  s = 跳过    d = 隐藏框    ←/→ = 前后      q = 退出")
    print()

    if not todo:
        print("  没有待审核的图。要重审加 --redo")
        return 0

    i = 0
    show = True
    changed = 0
    while 0 <= i < len(todo):
        p = todo[i]
        im = cv2.imread(str(p))
        if im is None:
            i += 1
            continue
        lab = lab_dir / f"{p.stem}.txt"
        boxes = read_txt(lab)

        while True:
            vis = render(im, boxes, show)
            head = f"[{i+1}/{len(todo)}] {p.name}   boxes={len(boxes)}"
            cv2.rectangle(vis, (0, 0), (vis.shape[1], 30), (30, 30, 30), -1)
            cv2.putText(vis, head, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (255, 255, 255), 1, cv2.LINE_AA)
            if vis.shape[1] > args.max_width:
                sc = args.max_width / vis.shape[1]
                vis = cv2.resize(vis, (args.max_width, int(vis.shape[0] * sc)))
            cv2.imshow("review", vis)
            k = cv2.waitKey(0) & 0xFF

            if k in (ord("y"), 32, 13, ord("a")):
                write_txt(lab, boxes)
                with done_file.open("a", encoding="utf-8") as f:
                    f.write(p.stem + "\n")
                i += 1
                changed += 1
                break
            if k == ord("n"):
                boxes = []
                vis2 = render(im, boxes, show)
                cv2.imshow("review", vis2)
                cv2.waitKey(1)
                # 不立刻跳走，让用户看到"框没了"再决定 y 还是 s
                continue
            if k == ord("s"):
                i += 1
                break
            if k == ord("d"):
                show = not show
                continue
            if k in (81, ord(",")):          # ←
                i = max(0, i - 1)
                break
            if k in (83, ord(".")):          # →
                i += 1
                break
            if k in (27, ord("q")):
                cv2.destroyAllWindows()
                print(f"\n退出。本次处理 {changed} 张。")
                return 0
            if ord("1") <= k <= ord("9"):
                n = k - ord("1")
                if 0 <= n < len(boxes):
                    boxes.pop(n)
                continue

    cv2.destroyAllWindows()
    print(f"\n完成。本次处理 {changed} 张，剩余 {len(todo) - i} 张未审。")
    print(f"已审核记录：{done_file}")
    print()
    print("  漏检的目标用 labelImg 补（格式相通）:")
    print(f"    labelImg {src} {src.parent / 'classes.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
