# -*- coding: utf-8 -*-
"""临时：把旧的单模型扫描 JSON 转成 person_relabel 的 raw 格式，用于离线验证 tiers 链路。"""
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
raw = {}
for split in ("train", "val"):
    d = json.loads((PROJECT / f"runs/scan_18cls_{split}.json").read_text(encoding="utf-8"))
    for k, v in d.items():
        key = k.replace("\\", "/")
        p = PROJECT / key
        if not p.exists():
            continue
        rel = p.relative_to(PROJECT).as_posix()
        boxes = [[b["conf"], b["cx"], b["cy"], b["w"], b["h"]]
                 for b in v if b.get("cls") == 18]
        raw.setdefault(rel, {})["yolo11n"] = boxes

out = PROJECT / "runs/_pre_raw.json"
out.write_text(json.dumps(raw, ensure_ascii=False, indent=0), encoding="utf-8")
print("图", len(raw), "有框", sum(1 for v in raw.values() if v["yolo11n"]))
