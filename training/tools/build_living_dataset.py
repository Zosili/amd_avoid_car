# -*- coding: utf-8 -*-
"""
================================================================
 合并「低视角非活体」+「活体」→ 一份 YOLO 数据集
================================================================

干的活：
    18cls（8884/2220，全非活体，贴地视角，类别 id 0..17）
        +
    person（COCO 格式的人体检测集，类别统一映射成 18）
        ↓
    datasets/living/
        images/{train,val}/*.jpg      ← 硬链接，不吃额外磁盘
        labels/{train,val}/*.txt
        data.yaml

关键设计：类别 id 完全不动
    tools/living_semantics.py 里 CLASS_NAMES 前 18 个与 18cls 顺序一致，
    person 追加成 18。所以 18cls 的标签文件可以**原样硬链接**，
    一个字节都不用重写 —— 这是当初把 person 放在末尾的唯一原因。

用法（在 D:\\FPGA\\ultralytics 下）：
    python tools\\build_living_dataset.py
    python tools\\build_living_dataset.py --person D:\\FPGA\\datasets_raw\\person_coco
    python tools\\build_living_dataset.py --no-person      # 只搬 18cls，先验证链路

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from living_semantics import CLASS_NAMES, NC  # noqa: E402

SRC_18CLS = PROJECT / "datasets" / "18cls"
DEFAULT_PERSON = Path(r"D:\FPGA\datasets_raw\person_coco")
OUT = PROJECT / "datasets" / "living"

PERSON_CLS_ID = CLASS_NAMES.index("person")   # = 18


# ---------------------------------------------------------------- 工具
def link_or_copy(src: Path, dst: Path) -> str:
    """优先硬链接（同盘不占空间），不行再复制。"""
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def ensure_dirs() -> None:
    for split in ("train", "val"):
        (OUT / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUT / "labels" / split).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 排除名单
def load_exclude(paths: list[str] | None) -> set[str]:
    """读取要排除的图片 stem 列表。

    为什么要这个：18cls 名义上是"纯非活体"数据集，但实测 val 2220 张里
    就有 267 张（12%）图里有真人却没 person 标签。这些图进训练集就是
    假负样本——模型每看一次就被惩罚"你说这里有人，标签说没有"，最后
    学到的是"人不算目标"，实车走到人跟前也不停。

    支持两种输入：
      · scan_person.py 输出的 JSON（取 key 的文件名）
      · 纯文本，每行一个文件名或 stem（# 开头是注释）
    """
    out: set[str] = set()
    for p in paths or []:
        f = Path(p)
        if not f.exists():
            print(f"  [!] 排除名单 {f} 不存在，忽略")
            continue
        if f.suffix.lower() == ".json":
            import json as _json
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:                      # noqa: BLE001
                print(f"  [!] 解析 {f} 失败：{exc}")
                continue
            for k in data:
                out.add(Path(k).stem)
        else:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                out.add(Path(line).stem)
        print(f"      排除名单 {f.name}: {len(out)} 条（累计）")
    return out


# ---------------------------------------------------------------- ① 搬 18cls
def import_18cls(exclude: set[str] | None = None,
                 person_labels: dict[str, list] | None = None) -> dict:
    """18cls 的类别序与 CLASS_NAMES 前 18 项一致，标签原样搬。

    exclude 里的图片整张跳过 —— 见 load_exclude() 的说明。

    person_labels: {图片 stem: [[cx,cy,w,h], ...]}（归一化）
        有值的图**不能硬链接标签**：必须复制原始家具标签再追写 person 行，
        否则 append 会把 datasets/18cls 的原始标注一起改掉（硬链接同 inode）。
    """
    exclude = exclude or set()
    person_labels = person_labels or {}
    stat = {"link": 0, "copy": 0, "img": 0, "lab": 0, "empty_label": 0,
            "excluded": 0, "person_img": 0, "person_box": 0, "relabel": 0}
    if not SRC_18CLS.is_dir():
        print(f"  [!] 找不到 {SRC_18CLS}，跳过")
        return stat

    for split in ("train", "val"):
        img_dir = SRC_18CLS / "images" / split
        lab_dir = SRC_18CLS / "labels" / split
        if not img_dir.is_dir():
            continue
        for img in sorted(img_dir.glob("*.jpg")):
            if img.stem in exclude:
                stat["excluded"] += 1
                continue
            how = link_or_copy(img, OUT / "images" / split / img.name)
            stat[how] += 1
            stat["img"] += 1

            lab = lab_dir / f"{img.stem}.txt"
            pl = person_labels.get(img.stem)
            dst_lab = OUT / "labels" / split / f"{img.stem}.txt"

            if pl:
                # 家具标签 + person 追加，写成新文件（绝不能硬链接后 append）
                base = lab.read_text(encoding="utf-8").rstrip() if lab.exists() else ""
                add = "\n".join(
                    f"{PERSON_CLS_ID} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                    for cx, cy, w, h in pl
                )
                dst_lab.write_text((base + "\n" + add).strip() + "\n", encoding="utf-8")
                stat["relabel"] += 1
                stat["person_img"] += 1
                stat["person_box"] += len(pl)
                stat["lab"] += 1
            elif lab.exists():
                link_or_copy(lab, dst_lab)
                stat["lab"] += 1
            else:
                # YOLO 允许空标签文件（= 全图无目标，纯背景负样本）
                dst_lab.write_text("")
                stat["empty_label"] += 1
    return stat


# ---------------------------------------------------------------- ② COCO → YOLO
def find_coco_root(root: Path) -> tuple[Path, Path, Path]:
    """在 person 目录里定位 (train_json, val_json, images_dir)。

    不同下载来源目录结构不一样，这里做几种常见布局的探测，
    避免每次下完数据都要手改脚本。
    """
    cands = [root, root / "train", root / "coco", root / "person_coco"]
    for base in cands:
        if not base.is_dir():
            continue
        jsons = {p.stem.lower(): p for p in base.glob("*.json")}
        if "train" not in jsons:
            continue
        img_dirs = [base / "images", base / "train", base / "JPEGImages"]
        img_dir = next((d for d in img_dirs if d.is_dir()), None)
        if img_dir is None:
            continue
        val = jsons.get("val") or jsons.get("validation")
        return jsons["train"], val, img_dir
    raise FileNotFoundError(
        f"在 {root} 里没找到 train.json + images/ 的组合。"
        f" 现有内容：{[p.name for p in root.iterdir()] if root.is_dir() else '(不存在)'}"
    )


def import_person_coco(root: Path) -> dict:
    """把 COCO 格式的 person 集转成 YOLO，类别统一写 PERSON_CLS_ID。"""
    stat = {"img": 0, "box": 0, "skip_nonperson": 0, "noimg": 0}
    train_j, val_j, img_dir = find_coco_root(root)
    print(f"  标注: train={train_j.name}", end="")
    print(f"  val={val_j.name}" if val_j else "  val=(无，将从 train 切)")
    print(f"  图片: {img_dir}")

    # images/ 下面可能还有子目录，建一张 文件名->真实路径 的索引
    index: dict[str, Path] = {}
    for p in img_dir.rglob("*.jpg"):
        index[p.name] = p
    for p in img_dir.rglob("*.png"):
        index[p.name] = p

    jobs: list[tuple[Path, str]] = []      # (json 文件, 输出 split)
    jobs.append((train_j, "train"))
    if val_j:
        jobs.append((val_j, "val"))

    for jf, split in jobs:
        data = json.loads(jf.read_text(encoding="utf-8"))
        cats = {c["id"]: c["name"].lower() for c in data.get("categories", [])}
        imgs = {im["id"]: im for im in data.get("images", [])}
        anns: dict[int, list] = {}
        for a in data.get("annotations", []):
            # 只要人；非 person 的框直接丢（本数据集理论上已过滤，防御性处理）
            if cats.get(a.get("category_id"), "") not in ("person", "people", "human"):
                stat["skip_nonperson"] += 1
                continue
            if a.get("iscrowd", 0):        # crowd 区域是"一群人的区域"，不是单人框
                continue
            anns.setdefault(a["image_id"], []).append(a)

        for img_id, alist in anns.items():
            meta = imgs.get(img_id)
            if not meta:
                continue
            fn = Path(meta["file_name"]).name
            src = index.get(fn)
            if src is None:
                stat["noimg"] += 1
                continue

            W, H = meta["width"], meta["height"]
            lines = []
            for a in alist:
                x, y, w, h = a["bbox"]
                if w <= 1 or h <= 1:
                    continue
                cx = (x + w / 2) / W
                cy = (y + h / 2) / H
                nw = w / W
                nh = h / H
                # 夹到 [0,1]，COCO 偶有越界框
                cx = min(max(cx, 0.0), 1.0)
                cy = min(max(cy, 0.0), 1.0)
                nw = min(nw, 1.0)
                nh = min(nh, 1.0)
                lines.append(f"{PERSON_CLS_ID} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            if not lines:
                continue

            # 加 p_ 前缀，避免和 18cls 的 000001.jpg 撞名
            stem = f"p_{split}_{img_id:012d}"
            link_or_copy(src, OUT / "images" / split / f"{stem}.jpg")
            (OUT / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n")
            stat["img"] += 1
            stat["box"] += len(lines)

    return stat


# ---------------------------------------------------------------- ②b YOLO → YOLO
def parse_map(spec: str) -> dict[int, int]:
    """解析 --map "0:person,15:cat,16:dog,14:bird" -> {源类id: 目标类id}

    为什么要显式映射而不是猜：
        每个外部数据集的类别编号都不一样（COCO 是 80 类，CrowdHuman 是 3 类，
        Roboflow 导出的又各不相同）。写死"person=0"这种假设，
        换一个来源就会把猫当成人。映射表必须由调用者明确给出。
    """
    out: dict[int, int] = {}
    if not spec:
        return out
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"--map 格式错了：{item!r}，应该是 源id:类名")
        a, b = item.split(":", 1)
        name = b.strip()
        if name not in CLASS_NAMES:
            raise ValueError(
                f"--map 里的类名 {name!r} 不在类别表里。可选：{CLASS_NAMES}")
        out[int(a)] = CLASS_NAMES.index(name)
    return out


def import_person_yolo(root: Path, src_cls: int = 0,
                       cls_map: dict[int, int] | None = None,
                       prefix: str = "ms",
                       val_ratio: float = 0.0) -> dict:
    """从**已经是 YOLO 格式**的数据集里并入 living。

    三种模式，按优先级：
        cls_map 非空 : 按显式映射搬指定的几类（推荐，最不容易出错）
        src_cls >= 0 : 只抽这一类，重映射成 person
        src_cls <  0 : 保留所有类、类别 id 原样不动
                       （用于 snap_frames.py 采的自建数据，本来就按 19/22 类标注）
    """
    stat = {"img": 0, "box": 0, "skip_img": 0, "dropped": 0}
    keep_all = (src_cls < 0) and not cls_map
    img_dir = None
    lab_dir = None
    for cand in (root / "images" / "train2017", root / "images" / "train",
                 root / "images", root):
        if cand.is_dir() and any(cand.glob("*.jpg")):
            img_dir = cand
            break
    if img_dir is None:
        raise FileNotFoundError(f"{root} 里找不到 images/*.jpg")

    for cand in (root / "labels" / "train2017", root / "labels" / "train",
                 root / "labels", img_dir.parent.parent / "labels" / img_dir.name):
        if cand.is_dir():
            lab_dir = cand
            break
    if lab_dir is None:
        raise FileNotFoundError(f"{root} 里找不到 labels/*.txt")

    for img in sorted(img_dir.glob("*.jpg")):
        lab = lab_dir / f"{img.stem}.txt"
        if not lab.exists():
            stat["skip_img"] += 1
            continue
        lines = []
        for line in lab.read_text().splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            cid = int(float(p[0]))
            if cls_map:
                if cid not in cls_map:
                    stat["dropped"] += 1
                    continue
                lines.append(f"{cls_map[cid]} " + " ".join(p[1:5]))
            elif keep_all:
                if not (0 <= cid < NC):
                    stat["dropped"] += 1
                    continue
                lines.append(f"{cid} " + " ".join(p[1:5]))
            else:
                if cid != src_cls:
                    stat["dropped"] += 1
                    continue
                lines.append(f"{PERSON_CLS_ID} " + " ".join(p[1:5]))
        if not lines and not keep_all:
            stat["skip_img"] += 1
            continue
        stem = f"{prefix}_{img.stem}"
        # val_ratio > 0 时按序号取模切分，能均匀分散到各个源视频，
        # 不会出现"整段某条视频都进了 val"这种偏斜
        split = "train"
        if val_ratio > 0:
            step = max(2, int(round(1.0 / val_ratio)))
            if stat["img"] % step == 0:
                split = "val"
        link_or_copy(img, OUT / "images" / split / f"{stem}.jpg")
        (OUT / "labels" / split / f"{stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""))
        stat["img"] += 1
        stat["box"] += len(lines)
        stat.setdefault("val_img", 0)
        if split == "val":
            stat["val_img"] += 1
    return stat


# ---------------------------------------------------------------- ③ data.yaml
def write_yaml() -> Path:
    p = OUT / "data.yaml"
    lines = [
        "# 由 tools/build_living_dataset.py 自动生成，不要手改类别顺序",
        "# 类别顺序的唯一事实源：tools/living_semantics.py",
        f"path: {OUT.as_posix()}",
        "train: images/train",
        "val: images/val",
        f"nc: {NC}",
        "names:",
    ]
    lines += [f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 统计
def count_split(split: str) -> tuple[int, dict[int, int]]:
    img_n = len(list((OUT / "images" / split).glob("*.jpg")))
    per_cls: dict[int, int] = {}
    for lab in (OUT / "labels" / split).glob("*.txt"):
        for line in lab.read_text().splitlines():
            if not line.strip():
                continue
            cid = int(line.split()[0])
            per_cls[cid] = per_cls.get(cid, 0) + 1
    return img_n, per_cls


# ---------------------------------------------------------------- main
def main() -> int:
    global OUT

    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=str(DEFAULT_PERSON),
                    help="COCO 格式 person 数据集根目录（含 train.json + images/）")
    ap.add_argument("--person-yolo", default=None,
                    help="已经是 YOLO txt 格式的 person 数据集根目录（如 coco128）")
    ap.add_argument("--person-cls", type=int, default=0,
                    help="--person-yolo 时，源数据集里 person 的类 id，默认 0")
    ap.add_argument("--map", default=None,
                    help='多类映射，如 "0:person,15:cat,16:dog,14:bird"'
                         "（coco128 里就是这几个 id）。给了它就不要再用 --person-cls")
    ap.add_argument("--no-person", action="store_true",
                    help="只搬 18cls，不并 person（用于先验证链路）")
    ap.add_argument("--phone", action="append", default=None,
                    help="自采数据目录（import_phone / snap_frames 的产物），"
                         "类 id 原样保留。可重复传入多个")
    ap.add_argument("--phone-val-ratio", type=float, default=0.2,
                    help="自采数据切多少比例进 val（默认 0.2）。"
                         "必须 >0，否则 val 里没有活体，person 的 mAP 无从评估")
    ap.add_argument("--clean", action="store_true",
                    help="重建前清空输出目录")
    ap.add_argument("--exclude", action="append", default=None,
                    help="排除名单（scan_person.py 的 JSON 或纯文本名单），"
                         "命中的 18cls 图片整张不搬。可重复传入")
    ap.add_argument("--person-labels", default=None,
                    help="tools/person_relabel.py apply 产出的 person_labels.json，"
                         "给 18cls 原图补 person 框（家具框保留）")
    ap.add_argument("--out", default=str(OUT),
                    help="输出目录（默认 datasets/living）。做 A/B 对照实验时"
                         "用不同目录，避免互相覆盖")
    args = ap.parse_args()

    OUT = Path(args.out)
    print("=" * 62)
    print(f"构建 {OUT}")
    print("=" * 62)
    print(f"  18cls  : {SRC_18CLS}")
    print(f"  person : {'(跳过)' if args.no_person else args.person}")
    print(f"  输出   : {OUT}")
    print()

    if args.clean and OUT.exists():
        print("  [clean] 清空输出目录 ...")
        shutil.rmtree(OUT, ignore_errors=True)

    ensure_dirs()

    print("[1/3] 搬运 18cls（类别 id 不变，标签原样硬链接）")
    exclude = load_exclude(args.exclude)

    # 补标：json 的 key 可能是完整相对路径，这里统一取 stem
    plabels: dict[str, list] = {}
    if args.person_labels:
        pj = Path(args.person_labels)
        if pj.exists():
            raw = json.loads(pj.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if v:
                    plabels[Path(k).stem] = v
            print(f"      补标文件 {pj.name}: {len(plabels)} 张图带 person 框")
        else:
            print(f"      [!] {pj} 不存在，忽略补标")

    s1 = import_18cls(exclude, plabels)
    print(f"      图片 {s1['img']} 张（硬链接 {s1['link']} / 复制 {s1['copy']}），"
          f"标签 {s1['lab']} 个，空标签 {s1['empty_label']} 个")
    if s1["relabel"]:
        print(f"      已补 person：{s1['person_img']} 张 / {s1['person_box']} 框"
              f"（这些图的家具标签是复制后追写，原 18cls 未改动）")
    if s1["excluded"]:
        print(f"      已排除 {s1['excluded']} 张（名单里含活体但没标注的图）")

    if not args.no_person:
        print("[2/3] 转换 person（类别统一为 "
              f"{PERSON_CLS_ID}:{CLASS_NAMES[PERSON_CLS_ID]}）")
        if args.person_yolo:
            root = Path(args.person_yolo)
            cmap = parse_map(args.map) if args.map else None
            if cmap:
                pretty = ", ".join(f"{k}->{CLASS_NAMES[v]}" for k, v in sorted(cmap.items()))
                print(f"      来源: YOLO 格式 {root}")
                print(f"      映射: {pretty}")
            else:
                print(f"      来源: YOLO 格式 {root}（源 person 类 id = {args.person_cls}）")
            if not root.is_dir():
                print(f"      [!] {root} 不存在，跳过")
            else:
                try:
                    s2 = import_person_yolo(root, args.person_cls, cmap)
                    print(f"      图片 {s2['img']} 张，保留框 {s2['box']} 个"
                          f"，丢弃无关框 {s2['dropped']} 个"
                          f"，跳过无目标图 {s2['skip_img']} 张")
                except (FileNotFoundError, ValueError) as exc:
                    print(f"      [!] {exc}")
        else:
            root = Path(args.person)
            if not root.is_dir():
                print(f"      [!] {root} 不存在，跳过 person 部分。")
                print("          可用 --person-yolo <YOLO格式目录>，或 --no-person 只建非活体版")
            else:
                try:
                    s2 = import_person_coco(root)
                    print(f"      图片 {s2['img']} 张，person 框 {s2['box']} 个"
                          f"，跳过非 person {s2['skip_nonperson']} 个，缺图 {s2['noimg']} 个")
                except FileNotFoundError as exc:
                    print(f"      [!] {exc}")
    else:
        print("[2/3] 跳过 person")

    # 自采数据（手机视频 / 摄像头），类 id 本来就是本工程的 22 类体系，
    # 因此原样保留 —— 不能用 --map 重映射，那会把自标注的语义全部搞乱。
    if args.phone:
        print("[2b/3] 并入自采数据（类 id 原样保留，空标签留作负样本）")
        for d in args.phone:
            root = Path(d)
            if not root.is_dir():
                print(f"      [!] {root} 不存在，跳过")
                continue
            try:
                s3 = import_person_yolo(root, src_cls=-1, prefix="ph",
                                        val_ratio=args.phone_val_ratio)
                print(f"      {root.name}: 图片 {s3['img']} 张"
                      f"（其中 {s3.get('val_img', 0)} 张进 val），"
                      f"框 {s3['box']} 个，丢弃越界类 {s3['dropped']} 个，"
                      f"缺标签 {s3['skip_img']} 张")
            except (FileNotFoundError, ValueError) as exc:
                print(f"      [!] {exc}")

    print("[3/3] 写 data.yaml")
    yml = write_yaml()
    print(f"      {yml}")

    print()
    print("=" * 62)
    print("类别分布（框数）")
    print("=" * 62)
    total: dict[int, int] = {}
    for split in ("train", "val"):
        n, per = count_split(split)
        print(f"  {split}: {n} 张图")
        for k, v in per.items():
            total[k] = total.get(k, 0) + v
    print()
    print(f"  {'id':>3}  {'name':<12s} {'框数':>7s}  占比")
    tot_box = sum(total.values()) or 1
    for cid in range(NC):
        v = total.get(cid, 0)
        flag = "   <-- 缺样本!" if v == 0 else ""
        print(f"  {cid:3d}  {CLASS_NAMES[cid]:<12s} {v:7d}  {v / tot_box * 100:5.2f}%{flag}")

    print()
    print(f"总计 {tot_box} 个框。data.yaml 就绪，可以训练：")
    print(f'  python train_yolo.py            # 改 train_yolo.py 顶部 DATASET = "living"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
