# -*- coding: utf-8 -*-
"""
============================================================
 给 18cls 数据集补 person 标注（高精度流水线）
============================================================

为什么需要这个脚本
----------------------------------------------------------------
`datasets/18cls` 是被当作"纯非活体"数据集收集的，但实际有约 12% 的图里
有人（站立/坐着/门口/床上的老人），而标签里没有 person。这些图当负样本
训练，等于教模型"人 = 背景"，这是最致命的错误——比漏检严重得多。

第一版处理方式是把这些图整张剔除（`runs/exclude_18cls_living.txt`），
代价是丢了 731 个 person 框，而且这些恰好是扫地机器人低视角图，比
coco128 的 1.5m 站立视角更贴合车载场景。

本脚本改为**补标**：只给"确认是人"的位置加 person 框，原家具框一个不动。

精度怎么保证（三道关）
----------------------------------------------------------------
1) 三模型交叉验证
     yolo11l (25.4M) + yolo26n (2.6M) + yolo11n (2.6M)
   三个不同架构/规模的 COCO 权重各自独立推理，同一位置 IoU>=0.45 才算
   "共识"。挂衣、被子、塑料袋这类静态物，靠单个模型的置信度很难和真人
   区分，但三个模型同时把它当人的概率很低。

2) 时序静止检测
    18cls 的图大多是从连续视频抽帧（000001.jpg 递增、opendoor123.jpg）。
   真正的误检源是**挂在墙上不动的衣服**：相邻帧里框的位置几乎不变。
   反过来，机器人在走、人在动时框会移动。同位置在 >=2 个邻近帧反复出现
   且中心几乎不动的框，降级到人工复核（不直接删，因为站着不动的人也会这样）。

3) 与已有家具框冲突检测
   18cls 有 11104 个标签文件（0~17 类家具）。一个 person 框如果和已有的
   stool/table/bed 框重叠 >60%，而那个家具框本身是可信的，说明模型可能
   把家具当成了人 → 降级人工复核。

分层路由（宁可排除，绝不标错）
----------------------------------------------------------------
    AUTO   : 直接采纳（多模型共识 且 高置信）
    REVIEW : 人工看裁剪图逐个判
    NEGATIVE: 三个模型都认不出人（conf<0.10）→ 原样保留当负样本
    图像级：只要有一张图进了 REVIEW，整张图先挂起；人工判完才决定
            "加框"还是"整张排除"。绝不出现"有人但没标"就进训练集。

用法
----------------------------------------------------------------
    python tools/person_relabel.py scan          # 三模型推理，存 raw json（要 GPU，约 20 分钟）
    python tools/person_relabel.py tiers         # 聚类+打分层+出复核图
    python tools/person_relabel.py stats         # 看统计
    python tools/person_relabel.py apply --accept runs/person_accept.json --out datasets/livingB

作者：欧阳博怀    日期：2026-10-29
"""

from __future__ import annotations

import argparse
import json
import time
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
DS18 = PROJECT / "datasets" / "18cls"
SPLITS = ("train", "val")

# ---- 三模型 ----
MODELS = [
    ("yolo11l", PROJECT / "weights" / "yolo11l.pt"),
    ("yolo26n", PROJECT / "weights" / "yolo26n.pt"),
    ("yolo11n", PROJECT / "weights" / "yolo11n.pt"),
]
PERSON_ID = 0          # COCO 里 person 的类 id

CONF_LO = 0.08         # 扫描下限，低于此认为"该模型完全没看到人"
IOU_MERGE = 0.45       # 跨模型聚类阈值
MIN_PRESENT = 0.10     # 任一簇最高置信低于此 → 判定该图无人

# 分层阈值
AUTO_CONSENSUS_CONF = 0.60   # 两模型共识时的最低置信
AUTO_TRIPLE_CONF = 0.45      # 三模型全部同意的较低门槛
AUTO_SINGLE_CONF = 0.88      # 单模型独占时必须极高置信
SKIP_CONF = 0.30             # 单模型且低于此 → 丢弃该框（整图只剩它就排除整图）
SMALL_H = 0.045              # 框高占比低于此视为噪声（640x480 下约 21px）
FURNITURE_OVERLAP = 0.60     # 与已有家具框重叠超过此比例 → 复核


# ============================================================
# 基础工具
# ============================================================
def iou(a, b) -> float:
    """a/b = (cx, cy, w, h) 归一化。"""
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def overlap_ratio(person, other) -> float:
    """交叠面积 / person 框面积（看 person 是否被家具框"吃进去"）。"""
    px1, py1 = person[0] - person[2] / 2, person[1] - person[3] / 2
    px2, py2 = person[0] + person[2] / 2, person[1] + person[3] / 2
    ox1, oy1, ox2, oy2 = other[:4]
    iw = max(0.0, min(px2, ox2) - max(px1, ox1))
    ih = max(0.0, min(py2, oy2) - max(py1, oy1))
    inter = iw * ih
    area = (px2 - px1) * (py2 - py1)
    return inter / area if area > 0 else 0.0


def load_furniture(split: str, stem: str) -> list[tuple]:
    """读 18cls 原始家具标签，返回 [(cls, x1,y1,x2,y2), ...] 归一化。"""
    p = DS18 / "labels" / split / f"{stem}.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        v = line.split()
        if len(v) < 5:
            continue
        c = int(v[0])
        cx, cy, w, h = (float(x) for x in v[1:5])
        out.append((c, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    return out


def parse_index(stem: str) -> tuple[str, int] | None:
    """'000123' -> ('', 123)；'opendoor45' -> ('opendoor', 45)；其它 -> None。"""
    m = re.match(r"^(.*?)(\d+)$", stem)
    if not m:
        return None
    return m.group(1), int(m.group(2))


# ============================================================
# ① scan：三模型推理
# ============================================================
def cmd_scan(args) -> int:
    import os

    os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ.pop("PYTHONPATH", None)
    from ultralytics import YOLO

    if args.paths_from:
        sel = json.loads(Path(args.paths_from).read_text(encoding="utf-8"))
        files = [PROJECT / x for x in sel]
    else:
        files = []
        for s in SPLITS:
            files += sorted((DS18 / "images" / s).glob("*.jpg"))
    if args.limit:
        files = files[: args.limit]

    want = None
    if args.models:
        want = {x.strip() for x in args.models.split(",") if x.strip()}
    todo = [(n, p) for n, p in MODELS if (want is None or n in want) and p.exists()]
    print(f"待扫描 {len(files)} 张图，用 {[n for n, _ in todo]} 个模型", flush=True)

    if args.merge and Path(args.out).exists():
        raw: dict[str, dict] = json.loads(Path(args.out).read_text(encoding="utf-8"))
        print(f"  合并模式：已有 {len(raw)} 张图的记录", flush=True)
    else:
        raw = {}
    for f in files:
        raw.setdefault(f.relative_to(PROJECT).as_posix(), {})
    key_of = {str(f): f.relative_to(PROJECT).as_posix() for f in files}
    paths = [str(f) for f in files]

    CHUNK = args.chunk   # 一次送进 predict 的图数。
                    # 踩过的坑：把 11104 个路径一次性传给 predict(stream=True)，
                    # ultralytics 会把整个数据集预读成一个
                    # (11104, 640, 640, 3) 的 uint8 数组（12.7 GiB），直接 OOM。
                    # 自己分块调用，内存上限锁在 24 张（约 30 MB），稳定。

    for mname, mpath in todo:
        print(f"\n=== {mname} ({mpath.name}) ===", flush=True)
        model = YOLO(str(mpath))
        n_hit = 0
        t0 = time.time()
        done = 0
        for s in range(0, len(paths), CHUNK):
            batch = paths[s:s + CHUNK]
            results = model.predict(
                source=batch, stream=False, imgsz=args.imgsz, conf=CONF_LO,
                iou=0.65, classes=[PERSON_ID], max_det=30, verbose=False,
                batch=args.batch, device=args.device, half=args.half,
            )
            for r in results:
                p = key_of.get(str(r.path))
                if p is None:
                    try:
                        p = Path(r.path).resolve().relative_to(PROJECT).as_posix()
                    except ValueError:
                        continue
                boxes = []
                if r.boxes is not None and len(r.boxes):
                    xywhn = r.boxes.xywhn.cpu().numpy()
                    conf = r.boxes.conf.cpu().numpy()
                    for (cx, cy, w, h), c in zip(xywhn, conf):
                        boxes.append([float(c), float(cx), float(cy), float(w), float(h)])
                if boxes:
                    n_hit += 1
                raw[p][mname] = boxes
            done += len(batch)
            if done % 1200 < CHUNK or done >= len(paths):
                el = time.time() - t0
                print(f"   {done}/{len(paths)}  命中 {n_hit} 张  "
                      f"{done / el:.1f} img/s  剩余 {int((len(paths) - done) / max(done / el, 0.1) / 60)} 分钟",
                      flush=True)
        print(f"   完成，命中 {n_hit} 张，用时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)
        del model

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"\n原始结果 -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


# ============================================================
# ② tiers：聚类 + 分层
# ============================================================
def cluster_models(per_model: dict[str, list]) -> list[dict]:
    """同一张图里，跨模型把 IoU 高的框并成一簇。"""
    items = []
    for mname, boxes in per_model.items():
        for b in boxes:
            items.append((b[0], mname, b[1:]))       # (conf, model, (cx,cy,w,h))
    items.sort(key=lambda z: -z[0])

    clusters: list[dict] = []
    for conf, mname, box in items:
        best, best_iou = None, 0.0
        for c in clusters:
            v = iou(c["box"], box)
            if v > best_iou:
                best, best_iou = c, v
        if best is not None and best_iou >= IOU_MERGE:
            best["models"][mname] = max(best["models"].get(mname, 0.0), conf)
            if conf > best["maxconf"]:
                best["maxconf"], best["box"], best["src"] = conf, box, mname
        else:
            clusters.append({"box": box, "maxconf": conf, "src": mname,
                             "models": {mname: conf}})
    return clusters


def cmd_tiers(args) -> int:
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    print(f"读入 {len(raw)} 张图的原始检测")

    # --- 第一遍：聚类 + 几何特征 ---
    per_img_clusters: dict[str, list[dict]] = {}
    for path, per_model in raw.items():
        cs = cluster_models(per_model)
        stem = Path(path).stem
        split = Path(path).parent.name
        furn = load_furniture(split, stem)
        for c in cs:
            cx, cy, w, h = c["box"]
            c["w"], c["h"] = w, h
            c["aspect"] = (w * 640) / max(h * 480, 1e-6)
            c["area"] = w * h
            c["bottom"] = cy + h / 2
            c["top"] = cy - h / 2
            fmax, fcls = 0.0, -1
            for fc, *box in furn:
                ov = overlap_ratio(c["box"], box)
                if ov > fmax:
                    fmax, fcls = ov, fc
            c["furn_ov"], c["furn_cls"] = fmax, fcls
            c["static"] = False
            c["static_nb"] = 0
        per_img_clusters[path] = cs

    # --- 第二遍：时序静止检测 ---
    # 同前缀同序号邻近的图，若在几乎同一位置反复检出，说明是静止物体
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for path in raw:
        pi = parse_index(Path(path).stem)
        if pi:
            # 带上 split：train/val 都可能有 000020.jpg，是两个不同视频里的帧
            groups[f"{Path(path).parent.name}/{pi[0]}"].append((pi[1], path))

    n_static = 0
    for prefix, arr in groups.items():
        if len(arr) < 3:
            continue
        arr.sort()
        idx = {n: p for n, p in arr}
        for n, path in arr:
            for c in per_img_clusters[path]:
                cnt = 0
                for d in (-3, -2, -1, 1, 2, 3):
                    op = idx.get(n + d)
                    if not op:
                        continue
                    for oc in per_img_clusters[op]:
                        if iou(c["box"], oc["box"]) >= 0.62:
                            dx = abs(c["box"][0] - oc["box"][0])
                            dy = abs(c["box"][1] - oc["box"][1])
                            if dx < 0.035 and dy < 0.035 and oc["maxconf"] >= 0.30:
                                cnt += 1
                                break
                c["static_nb"] = cnt
                if cnt >= 2:
                    c["static"] = True
                    n_static += 1
    print(f"时序静止可疑簇 {n_static} 个")

    # --- 第三遍：分层 ---
    def decide(c) -> tuple[str, str]:
        """返回 (tier, reason)。

        tier:
            AUTO   多模型共识或极高置信，直接采纳
            REVIEW 人工看图判定
            SKIP   不可信但又不像是人的位置 → 丢弃此框；若整图只剩这类，
                   整张图排除（既不标错，也不当负样本）
            DROP   噪声，直接丢
        顺序很重要：先看共识，再看几何。反过来会让"站在柜子前的人"
        因为和柜子框重叠被打进复核（实测 529 个，绝大多数是真的人）。
        """
        n = len(c["models"])
        conf = c["maxconf"]
        if c["h"] < SMALL_H and conf < 0.55:
            return "DROP", "too_small"
        # 共识优先，不受家具重叠 / 静止影响（三模型都认，基本就是人）
        if n >= 3 and conf >= AUTO_TRIPLE_CONF:
            return "AUTO", "triple"
        if n >= 2 and conf >= AUTO_CONSENSUS_CONF:
            return "AUTO", "consensus"
        if n == 1 and conf >= AUTO_SINGLE_CONF and not c["static"]:
            return "AUTO", "highconf"
        # 以下都是"证据不足"的，先排除明显不可信的
        if n == 1 and conf < SKIP_CONF:
            return "SKIP", f"lowconf1_{conf:.2f}"
        if c["aspect"] > 2.6 and c["h"] < 0.45:
            return "REVIEW", "wide_flat"
        if c["static"]:
            return "REVIEW", f"static_x{c['static_nb']}"
        if c["furn_ov"] > FURNITURE_OVERLAP:
            return "REVIEW", f"overlap_furn{c['furn_cls']}"
        return "REVIEW", f"lowconf{n}"

    out: dict[str, dict] = {}
    stat = Counter()
    for path, cs in per_img_clusters.items():
        kept = []
        n_skip = 0
        for c in cs:
            tier, reason = decide(c)
            if tier == "DROP":
                stat["drop_box"] += 1
                continue
            if tier == "SKIP":
                stat["skip_box"] += 1
                n_skip += 1
                continue
            kept.append({
                "box": [round(v, 5) for v in c["box"]],
                "conf": round(c["maxconf"], 4),
                "models": {k: round(v, 3) for k, v in c["models"].items()},
                "tier": tier,
                "reason": reason,
                "static_nb": c["static_nb"],
                "furn_ov": round(c["furn_ov"], 3),
                "furn_cls": c["furn_cls"],
            })
            stat[f"box_{tier}"] += 1

        auto = [b for b in kept if b["tier"] == "AUTO"]
        rev = [b for b in kept if b["tier"] == "REVIEW"]
        present = any(b["conf"] >= MIN_PRESENT for b in kept)

        if auto and rev:
            status = "REVIEW"        # 已有可信框，但还有待判框 → 整图挂起
        elif auto:
            status = "POSITIVE"
        elif rev:
            status = "REVIEW"
        elif present:
            status = "REVIEW"
        elif n_skip:
            # 只有不可信的候选框 → 整图排除。不标（怕标错）也不当负样本（怕是漏检）
            status = "SKIP"
        else:
            status = "NEGATIVE"
        stat[f"img_{status}"] += 1
        out[path] = {"split": Path(path).parent.name, "status": status, "boxes": kept}

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")

    print()
    print("=== 图像级 ===")
    for k in ("img_POSITIVE", "img_REVIEW", "img_SKIP", "img_NEGATIVE"):
        print(f"  {k[4:]:<10} {stat[k]:5d}")
    print("=== 框级 ===")
    for k in ("box_AUTO", "box_REVIEW", "skip_box", "drop_box"):
        print(f"  {k[4:]:<10} {stat[k]:5d}")
    print(f"\n分层结果 -> {dst}")
    return 0


# ============================================================
# ③ review：生成复核用裁剪图
# ============================================================
def _render_crop(img: np.ndarray, box, label: str, size: int = 300) -> np.ndarray:
    h, w = img.shape[:2]
    cx, cy, bw, bh = box
    x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
    x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
    pad_x = max(30, int((x2 - x1) * 0.55))
    pad_y = max(30, int((y2 - y1) * 0.55))
    cx0, cy0 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    cx1, cy1 = min(w, x2 + pad_x), min(h, y2 + pad_y)
    crop = img[cy0:cy1, cx0:cx1].copy()
    if crop.size == 0:
        return np.zeros((size, size, 3), np.uint8)
    sc = min(size / crop.shape[1], (size - 26) / crop.shape[0])
    crop = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    cv2.rectangle(crop, (int((x1 - cx0) * sc), int((y1 - cy0) * sc)),
                  (int((x2 - cx0) * sc), int((y2 - cy0) * sc)), (0, 0, 255), 2)
    canvas = np.zeros((size, size, 3), np.uint8)
    canvas[26:26 + crop.shape[0], :crop.shape[1]] = crop[: size - 26]
    cv2.putText(canvas, label, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return canvas


def cluster_spatial(items: list[tuple]) -> list[dict]:
    """把同一视频(prefix)里位置几乎相同的框聚成一簇。

    为什么必须这么做：18cls 里最大的误检源是**静止的东西**——墙上的
    人像照片、电视里的人物、地上揉成团的衣服、挂着的裤子。它们在连续
    帧里反复被检出，位置几乎不变。逐个框看要筛 700 次，而且要保证前后
    判断一致；按簇看只需 100 多次，且同一处物体只做一次决定。

    簇内元素越多 → 越可能是静止物体（真人走动会分散成小簇/单点）。
    """
    groups: dict[tuple, list] = defaultdict(list)
    for path, i, b in items:
        pi = parse_index(Path(path).stem) or ("", 0)
        groups[(Path(path).parent.name, pi[0])].append((path, i, b))

    clusters: list[dict] = []
    for _key, arr in groups.items():
        arr.sort(key=lambda z: -z[2]["conf"])
        made: list[dict] = []
        for item in arr:
            bx = item[2]["box"]
            a = max(bx[2] * bx[3], 1e-9)
            hit = None
            for c in made:
                rb = c["rep"][2]["box"]
                if (abs(bx[0] - rb[0]) < 0.06 and abs(bx[1] - rb[1]) < 0.06
                        and 0.65 < a / max(rb[2] * rb[3], 1e-9) < 1.55):
                    hit = c
                    break
            if hit:
                hit["items"].append(item)
            else:
                made.append({"rep": item, "items": [item]})
        clusters += made
    clusters.sort(key=lambda c: -c["rep"][2]["conf"])
    return clusters


def cmd_review(args) -> int:
    tiers = json.loads(Path(args.tiers).read_text(encoding="utf-8"))
    want = args.tier.upper()
    items = []
    for path, d in tiers.items():
        for i, b in enumerate(d["boxes"]):
            if b["tier"] != want:
                continue
            if args.reason and not b["reason"].startswith(args.reason):
                continue
            items.append((path, i, b))

    clusters = cluster_spatial(items)
    print(f"{want} 层共 {len(items)} 个框 -> {len(clusters)} 个位置簇")
    if args.sample and args.sample < len(clusters):
        import random
        random.seed(args.seed)
        clusters = random.sample(clusters, args.sample)
        clusters.sort(key=lambda c: -c["rep"][2]["conf"])
        print(f"（抽样审计 {args.sample} 簇）")
    elif args.limit:
        clusters = clusters[: args.limit]

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    tag = "R" if want == "REVIEW" else ("A" if want == "AUTO" else want[0])
    suffix = f"_{args.reason}" if args.reason else ""
    stemname = f"{want}{suffix}"
    index: dict[str, dict] = {}
    for k, c in enumerate(clusters):
        sid = f"{tag}{k}"
        rep_path, rep_i, rep_b = c["rep"]
        index[sid] = {
            "keys": [f"{p}#{i}" for p, i, _ in c["items"]],
            "stems": sorted({Path(p).stem for p, _, _ in c["items"]}),
            "n": len(c["items"]),
            "conf": rep_b["conf"],
            "reason": rep_b["reason"],
            "box": rep_b["box"],
            "path": rep_path,
        }
    (outdir / f"{stemname}_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")

    COLS, ROWS = args.cols, args.rows
    per = COLS * ROWS
    n_page = 0
    for page in range(0, len(clusters), per):
        chunk = clusters[page:page + per]
        tiles = []
        for off, c in enumerate(chunk):
            sid = f"{tag}{page + off}"
            rep_path, rep_i, rep_b = c["rep"]
            img = cv2.imread(str(PROJECT / rep_path))
            if img is None:
                continue
            # 簇成员框全部画上，看这个"物体"在不同帧里是否稳定
            extra = [b["box"] for p, _, b in c["items"][1:4] if p == rep_path]
            lbl = "%s x%d c%.2f %s" % (sid, len(c["items"]), rep_b["conf"],
                                       rep_b["reason"][:9])
            tile = _render_crop(img, rep_b["box"], lbl)
            # 用细线把簇内其它成员框也标出来（同图才画）
            if extra:
                h, w = img.shape[:2]
                cx, cy, bw, bh = rep_b["box"]
                x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
                x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
                pad_x = max(30, int((x2 - x1) * 0.55))
                pad_y = max(30, int((y2 - y1) * 0.55))
                cx0, cy0 = max(0, x1 - pad_x), max(0, y1 - pad_y)
                sc = min(300 / max(1, min(w, x2 + pad_x) - cx0),
                         (300 - 26) / max(1, min(h, y2 + pad_y) - cy0))
                for eb in extra:
                    ex1 = int((eb[0] - eb[2] / 2) * w)
                    ey1 = int((eb[1] - eb[3] / 2) * h)
                    ex2 = int((eb[0] + eb[2] / 2) * w)
                    ey2 = int((eb[1] + eb[3] / 2) * h)
                    cv2.rectangle(tile, (int((ex1 - cx0) * sc), 26 + int((ey1 - cy0) * sc)),
                                  (int((ex2 - cx0) * sc), 26 + int((ey2 - cy0) * sc)),
                                  (0, 220, 220), 1)
            tiles.append(tile)
        while len(tiles) < per:
            tiles.append(np.zeros((300, 300, 3), np.uint8))
        rows = [np.hstack(tiles[r * COLS:(r + 1) * COLS]) for r in range(ROWS)]
        n_page += 1
        cv2.imwrite(str(outdir / f"{stemname.lower()}_{n_page:02d}.jpg"), np.vstack(rows))
    print(f"生成 {n_page} 页 -> {outdir}")
    print(f"短 id 映射 -> {outdir / (stemname + '_index.json')}（{len(index)} 簇）")
    return 0


# ============================================================
# ④ stats
# ============================================================
def cmd_stats(args) -> int:
    tiers = json.loads(Path(args.tiers).read_text(encoding="utf-8"))
    st = Counter()
    by_reason = {"AUTO": Counter(), "REVIEW": Counter()}
    for path, d in tiers.items():
        st[f"img_{d['status']}"] += 1
        for b in d["boxes"]:
            st[f"box_{b['tier']}"] += 1
            key = b["reason"].split("_")[0].rstrip("0123456789")
            by_reason.get(b["tier"], Counter())[key] += 1
    print("=== 图像级 ===")
    for k in ("img_POSITIVE", "img_REVIEW", "img_SKIP", "img_NEGATIVE"):
        print(f"  {k[4:]:<10} {st[k]:5d}")
    print("=== 框级 ===")
    for k in ("box_AUTO", "box_REVIEW"):
        print(f"  {k[4:]:<10} {st[k]:5d}")
    for tier in ("AUTO", "REVIEW"):
        print(f"=== {tier} 原因分布 ===")
        for k, v in by_reason[tier].most_common():
            print(f"  {k:<18} {v}")
    return 0


# ============================================================
# ⑤ apply：把确认的框写成标签
# ============================================================
def cmd_apply(args) -> int:
    """把 AUTO + 人工判定为 keep 的 REVIEW 框写成标签。

    安全策略（宁可排除，绝不错标）：
        AUTO 框              -> 直接采纳
        REVIEW 框 + keep     -> 采纳
        REVIEW 框 + drop     -> 丢弃
        REVIEW 框 **未判定** -> 整张图进排除名单（既不标也不当负样本）

    decisions 文件格式：{"path#idx": "keep"} / {"path#idx": "drop"}
    """
    tiers = json.loads(Path(args.tiers).read_text(encoding="utf-8"))
    decisions: dict[str, str] = {}
    if args.decisions:
        raw_dec = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
        # 短 id -> [path#idx, ...]（一个簇可能包含多帧的多个框）
        short2keys: dict[str, list[str]] = {}
        for idxf in sorted(Path(args.review_dir).glob("*_index.json")):
            for sid, v in json.loads(idxf.read_text(encoding="utf-8")).items():
                ks = v.get("keys") or [v.get("key")]
                short2keys[sid] = [k for k in ks if k]
        unknown = []
        for k, v in raw_dec.items():
            if k in short2keys:
                for key in short2keys[k]:
                    decisions[key] = v
            elif "#" in k:
                decisions[k] = v
            else:
                unknown.append(k)
        if unknown:
            print(f"  [!] {len(unknown)} 个决策 id 不在 index 里，已忽略: {unknown[:8]}")
        print(f"决策文件 {Path(args.decisions).name}: {len(raw_dec)} 条 -> 覆盖 {len(decisions)} 个框")

    labels: dict[str, list] = {}
    exclude: list[str] = []
    st = Counter()

    for path, d in tiers.items():
        if d["status"] == "SKIP":
            exclude.append(Path(path).stem)
            st["img_skip"] += 1
            continue
        keep_boxes = []
        unresolved = 0
        ambiguous = False
        for i, b in enumerate(d["boxes"]):
            key = f"{path}#{i}"
            dv = decisions.get(key)
            # 关键：AUTO 也要尊重人工决策。
            # 曾经的 bug：AUTO 无条件采纳，人工标记为 drop 的簇全部失效，
            # 实测导致 28% 的误检率（656 个 AUTO 框一个没筛）。
            if b["tier"] == "AUTO" and dv != "drop" and dv != "skip":
                keep_boxes.append(b["box"])
                st["from_auto"] += 1
            elif dv == "keep":
                keep_boxes.append(b["box"])
                st["from_review_keep"] += 1
            elif dv == "drop":
                st["from_review_drop"] += 1
            elif dv == "skip":
                # 看不清/拿不准 → 整张图排除。不标（怕标错）也不当负样本（怕是漏检）
                ambiguous = True
            else:
                unresolved += 1

        if ambiguous or unresolved:
            exclude.append(Path(path).stem)
            st["img_exclude"] += 1
        elif keep_boxes:
            labels[path] = keep_boxes
            st["img_positive"] += 1
        else:
            st["img_negative"] += 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "person_labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "person_exclude.txt").write_text(
        "\n".join(exclude) + ("\n" if exclude else ""), encoding="utf-8")

    print(f"采纳 AUTO {st['from_auto']} 框，人工保留 {st['from_review_keep']} 框，"
          f"人工删除 {st['from_review_drop']} 框")
    print(f"图像：有 person {st['img_positive']} 张 / 纯负样本 {st['img_negative']} 张 / "
          f"低置信整图排除 {st['img_skip']} 张 / 判不准或未判定排除 {st['img_exclude']} 张")
    print(f"-> {out / 'person_labels.json'}")
    print(f"-> {out / 'person_exclude.txt'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="18cls person 补标流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="三模型推理")
    s.add_argument("--out", default=str(PROJECT / "runs" / "person_raw.json"))
    s.add_argument("--imgsz", type=int, default=640)
    s.add_argument("--device", default="0")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--batch", type=int, default=16)
    s.add_argument("--chunk", type=int, default=32)
    s.add_argument("--half", action="store_true", default=False,
                   help="本机 fp32 更快：8.4.182 已废弃 half，传了也会被忽略")
    s.add_argument("--models", default="", help="只用指定模型，逗号分隔")
    s.add_argument("--paths-from", default="", help="只扫这个 json 里列出的图（相对路径）")
    s.add_argument("--merge", action="store_true", help="合并进已有 --out，不覆盖其它模型的记录")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("tiers", help="聚类分层")
    s.add_argument("--raw", default=str(PROJECT / "runs" / "person_raw.json"))
    s.add_argument("--out", default=str(PROJECT / "runs" / "person_tiers.json"))
    s.set_defaults(func=cmd_tiers)

    s = sub.add_parser("review", help="生成复核图")
    s.add_argument("--tiers", default=str(PROJECT / "runs" / "person_tiers.json"))
    s.add_argument("--out", default=str(PROJECT / "runs" / "review"))
    s.add_argument("--tier", default="REVIEW")
    s.add_argument("--reason", default="", help="只取原因前缀匹配的框，如 static / lowconf")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--sample", type=int, default=0, help="随机抽样审计")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--cols", type=int, default=5)
    s.add_argument("--rows", type=int, default=4)
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("stats", help="统计")
    s.add_argument("--tiers", default=str(PROJECT / "runs" / "person_tiers.json"))
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("apply", help="写标签")
    s.add_argument("--tiers", default=str(PROJECT / "runs" / "person_tiers.json"))
    s.add_argument("--decisions", default="")
    s.add_argument("--review-dir", default=str(PROJECT / "runs" / "review"))
    s.add_argument("--out", default=str(PROJECT / "runs" / "person_final"))
    s.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
