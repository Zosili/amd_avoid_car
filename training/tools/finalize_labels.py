# -*- coding: utf-8 -*-
"""把"逐框目视复核"的结论落成最终标签。

与 person_relabel.py apply 的区别：apply 依赖分层(AUTO/REVIEW)＋簇级决策，实测
AUTO 层仍有约 50% 误检（椅背、桌腿、挂着的衣服、电视里的人物、墙上的人像照片
都被 80 类 COCO 模型高置信判成 person）。这里改成最笨也最可靠的做法：

    只有被人工明确标为 keep 的框才写进标签，其余一律不写。

排除策略（宁可少一张负样本，也不教模型"人不是人"）：
    整条 person 框都没留下，且被丢的框里有"高置信 + 不算太小"的 ——
    说明模型很确信那儿有个人形，我又判断不是人，这多半是电视/照片/海报里的
    人像，或者我没看清。这种图整张排除，既不当正样本也不当负样本。
    单纯的家具误检（椅背、桌腿、水桶、拖鞋）则保留为纯负样本 —— 正是要让
    模型学会"椅子不是人"。
"""

import argparse
import json
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONF_EXCL = 0.50
H_EXCL = 0.06


def same(a, b, tol: float = 2e-3) -> bool:
    return all(abs(a[k] - b[k]) <= tol for k in range(4))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default=str(PROJECT / "runs/person_tiers.json"))
    ap.add_argument("--keep", default=str(PROJECT / "runs/person_final/manual_keep.json"))
    ap.add_argument("--out", default=str(PROJECT / "runs/person_final"))
    args = ap.parse_args()

    tiers = json.loads(Path(args.tiers).read_text(encoding="utf-8"))
    keep: dict[str, list] = json.loads(Path(args.keep).read_text(encoding="utf-8"))
    print(f"人工保留 {sum(len(v) for v in keep.values())} 个框 / {len(keep)} 张图")

    labels: dict[str, list] = {}
    exclude: list[str] = []
    st = Counter()
    unmatched = 0

    for path, d in tiers.items():
        stem = Path(path).stem
        if d["status"] == "SKIP":
            exclude.append(stem)
            st["img_skip"] += 1
            continue
        want = list(keep.get(stem, []))
        kept, dropped = [], []
        for b in d["boxes"]:
            hit = next((k for k in want if same(k, b["box"])), None)
            if hit is not None:
                kept.append(b)
                want.remove(hit)
            else:
                dropped.append(b)
        unmatched += len(want)
        if kept:
            labels[path] = [b["box"] for b in kept]
            st["img_positive"] += 1
            st["box_positive"] += len(kept)
            continue
        risky = [b for b in dropped
                 if b.get("maxconf", b.get("conf", 0)) >= CONF_EXCL
                 and b["box"][3] >= H_EXCL]
        if risky:
            exclude.append(stem)
            st["img_exclude_risky"] += 1
        else:
            st["img_negative"] += 1

    if unmatched:
        print(f"  [!] {unmatched} 个保留框在 tiers 里没找到对应检测，已忽略")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "person_labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "person_exclude.txt").write_text(
        "\n".join(exclude) + ("\n" if exclude else ""), encoding="utf-8")
    print(dict(st))
    print(f"-> {out/'person_labels.json'}  （{len(labels)} 张图）")
    print(f"-> {out/'person_exclude.txt'}   （{len(exclude)} 张图）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
