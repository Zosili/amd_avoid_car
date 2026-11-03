"""
PASCAL VOC  ->  YOLO 格式转换器

适用：D:\\FPGA\\18类物体数据集（Annotations/*.xml + JPEGImages/*.jpg，无 train/val 划分）
输出：D:\\FPGA\\ultralytics\\datasets\\18cls\\
        images/{train,val}/*.jpg
        labels/{train,val}/*.txt      # cls cx cy w h（归一化）
        data.yaml

用法：
  C:\\Users\\shang\\.conda\\envs\\yolo-rocm\\python.exe voc2yolo.py

特性：
  * 按 XML 文件名同名匹配图片（该数据集 XML 内部的 <filename> 字段被截断，不可信）
  * 自动修正拼写变体脏标签，丢弃"两个类名粘一起"和无意义的误标
  * 用真实图片尺寸归一化（已全量校验 11104 张与 XML 一致）
  * 图片用硬链接，不额外占用磁盘；跨盘时自动回退为复制
  * 固定随机种子，划分可复现
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from PIL import Image

# ---------------------------------------------------------------- 配置

SRC = r"D:\FPGA\18类物体数据集"
OUT = r"D:\FPGA\ultralytics\datasets\18cls"
VAL_RATIO = 0.2
SEED = 0

# 18 个标准类，顺序来自 rob标签.txt
CLASSES = [
    "trashcan", "slippers", "wire", "socks", "carpet", "book", "feces",
    "curtain", "stool", "bed", "sofa", "closestool", "table", "cabinet",
    "ajardoor", "opendoor", "closedoor", "stairway",
]
CLS2ID = {n: i for i, n in enumerate(CLASSES)}

# 拼写变体 -> 标准名
ALIAS = {
    "trash can": "trashcan",
    "closeddoor": "closedoor",
    "closetool": "closestool",
    "curtainw": "curtain",
    "stoolz": "stool",
}

# 明确丢弃的标签（两个类名粘连 / 无意义误标）
DROP = {
    "carpetcabinet", "sofafeces", "wirecabinet", "cabinettrashcan",
    "sockscabinet", "carpetsofa", "fecesstool",
    "DT", "DX", "sf", "发展", "本地", "分", "出来",
}


def link_or_copy(src: str, dst: str) -> str:
    """优先硬链接，失败则复制。返回 'link' 或 'copy'。"""
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main() -> int:
    ann_dir = os.path.join(SRC, "Annotations")
    img_dir = os.path.join(SRC, "JPEGImages")

    if not os.path.isdir(ann_dir) or not os.path.isdir(img_dir):
        print(f"[错误] 源目录结构不对：{SRC}")
        return 1

    stems = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(ann_dir)
        if f.lower().endswith(".xml")
    )
    print(f"发现 {len(stems)} 个 XML")

    # 建输出目录
    for split in ("train", "val"):
        os.makedirs(os.path.join(OUT, "images", split), exist_ok=True)
        os.makedirs(os.path.join(OUT, "labels", split), exist_ok=True)

    # 划分
    rng = random.Random(SEED)
    shuffled = stems[:]
    rng.shuffle(shuffled)
    n_val = int(len(shuffled) * VAL_RATIO)
    val_set = set(shuffled[:n_val])
    print(f"划分：train {len(shuffled) - n_val} / val {n_val}")

    stats_cls = Counter()          # 每类保留的框数
    stats_drop = Counter()         # 每类被丢弃的框数
    alias_used = Counter()         # 别名修正次数
    skipped_no_img: list[str] = []
    n_img_link = n_img_copy = 0
    n_empty_label = 0
    n_degenerate = 0
    split_cls = defaultdict(Counter)

    for idx, stem in enumerate(stems, 1):
        src_img = os.path.join(img_dir, stem + ".jpg")
        if not os.path.exists(src_img):
            skipped_no_img.append(stem)
            continue

        split = "val" if stem in val_set else "train"
        with Image.open(src_img) as im:
            W, H = im.size

        lines: list[str] = []
        root = ET.parse(os.path.join(ann_dir, stem + ".xml")).getroot()

        for obj in root.findall("object"):
            raw = (obj.findtext("name") or "").strip()
            if not raw or raw in DROP:
                stats_drop[raw] += 1
                continue

            name = ALIAS.get(raw, raw)
            if name != raw:
                alias_used[f"{raw} -> {name}"] += 1
            if name not in CLS2ID:
                stats_drop[raw] += 1
                continue

            bb = obj.find("bndbox")
            if bb is None:
                stats_drop[raw] += 1
                continue
            try:
                x1, y1, x2, y2 = (
                    float(bb.findtext(k)) for k in ("xmin", "ymin", "xmax", "ymax")
                )
            except (TypeError, ValueError):
                stats_drop[raw] += 1
                continue

            # 裁剪到图像范围
            x1 = max(0.0, min(x1, W))
            x2 = max(0.0, min(x2, W))
            y1 = max(0.0, min(y1, H))
            y2 = max(0.0, min(y2, H))

            w, h = x2 - x1, y2 - y1
            if w <= 1 or h <= 1:      # 退化框
                n_degenerate += 1
                continue

            cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
            lines.append(
                f"{CLS2ID[name]} {cx:.6f} {cy:.6f} {w / W:.6f} {h / H:.6f}"
            )
            stats_cls[name] += 1
            split_cls[split][name] += 1

        # 写图片
        dst_img = os.path.join(OUT, "images", split, stem + ".jpg")
        if link_or_copy(src_img, dst_img) == "link":
            n_img_link += 1
        else:
            n_img_copy += 1

        # 写 label（空列表也要建文件，表示该图为负样本）
        if not lines:
            n_empty_label += 1
        with open(
            os.path.join(OUT, "labels", split, stem + ".txt"), "w", encoding="utf-8"
        ) as f:
            f.write("\n".join(lines))

        if idx % 2000 == 0:
            print(f"  ... {idx}/{len(stems)}")

    # data.yaml
    yaml_path = os.path.join(OUT, "data.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write("# 由 voc2yolo.py 自动生成\n")
        f.write(f"path: {OUT.replace(os.sep, '/')}\n")
        f.write("train: images/train\n")
        f.write("val: images/val\n")
        f.write(f"nc: {len(CLASSES)}\n")
        f.write("names:\n")
        for i, n in enumerate(CLASSES):
            f.write(f"  {i}: {n}\n")

    # ------------------------------------------------------------ 报告
    print()
    print("=" * 58)
    print("转换完成")
    print("=" * 58)
    print(f"图片：硬链接 {n_img_link}，复制 {n_img_copy}")
    print(f"无标注/全丢弃的图片：{n_empty_label}")
    print(f"退化框剔除：{n_degenerate}")
    if skipped_no_img:
        print(f"找不到图片而跳过：{len(skipped_no_img)} -> {skipped_no_img[:5]}")
    print()
    print("别名修正：")
    for k, v in alias_used.most_common():
        print(f"  {k:<28} {v}")
    print()
    print("丢弃的脏标签：")
    for k, v in stats_drop.most_common():
        print(f"  {str(k):<20} {v}")
    print()
    print(f"{'类别':<14}{'总数':>8}{'train':>8}{'val':>8}")
    print("-" * 40)
    for n in CLASSES:
        print(f"{n:<14}{stats_cls[n]:>8}{split_cls['train'][n]:>8}{split_cls['val'][n]:>8}")
    print("-" * 40)
    total = sum(stats_cls.values())
    print(f"{'合计':<14}{total:>8}")
    print()
    print(f"data.yaml -> {yaml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
