#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_repo_layout.py — 提交仓库的命名与结构自检（对应指南 3.2.5 / 3.2.5.5）

检查项：
  1. 路径命名：目录名与文件名只用小写字母、数字、点、下划线、连字符
     （指南 3.2.5：不得出现中文、空格或特殊字符，以保证跨平台复现与脚本自动化）
     例外：README.md / MODEL.md / CONTRIBUTING.md / NOTICE.md / LICENSE 等
     指南自身规定的标准文件名保持原有大小写。
  2. 结构：3.2.5.5 推荐目录是否齐全（缺了只报 warning，因为指南允许自定义结构，
     但必须在 README.md 里给出目录对照说明）。
  3. metrics/metrics.csv：表头是否为指南规定的 7 列。
  4. 体积：单个文件 > 20 MB 报错（该走 Git LFS 或不要进 git）。

用法：
    python tools/check_repo_layout.py            # 在仓库根目录跑
    python tools/check_repo_layout.py --root .   # 指定根目录
退出码：0 = 通过（可能有 warning）；1 = 有 error
"""
import argparse
import csv
import io
import os
import re
import sys

ALLOWED_NAME = re.compile(r"^[a-z0-9._-]+$")
UPPERCASE_WHITELIST = {
    "README.md", "MODEL.md", "CONTRIBUTING.md", "NOTICE.md", "LICENSE",
    "LICENSE.md", "CHANGELOG.md", "AUTHORS.md", "CODEOWNERS",
}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv",
             "node_modules", ".idea", ".vscode"}

REQUIRED_PATHS = [
    ("README.md", "file"),
    ("docs/architecture.md", "file"),
    ("docs/interface.md", "file"),
    ("docs/hardware_setup.md", "file"),
    ("fpga/src", "dir"),
    ("fpga/build", "dir"),
    ("fpga/report", "dir"),
    ("host/model", "dir"),
    ("host/app", "dir"),
    ("host/deploy", "dir"),
    ("metrics/metrics.csv", "file"),
    ("metrics/logs", "dir"),
    ("metrics/scripts", "dir"),
    ("metrics/evidence", "dir"),
    ("skill/README.md", "file"),
    ("report", "dir"),
]

METRICS_HEADER = ["指标名称", "类别", "数值", "单位", "测量条件", "测试次数/时长", "证据文件"]
BIG_FILE_MB = 20.0

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def check_links(root, errors, warnings):
    """Markdown 里的相对链接必须指向真实文件（避免文档互相指向不存在的路径）"""
    n_files, n_links = 0, 0
    for rel_dir, f, full in walk(root):
        if not f.endswith(".md"):
            continue
        n_files += 1
        with io.open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            n_links += 1
            if target.startswith("/"):
                p = os.path.join(root, target.lstrip("/"))
            else:
                p = os.path.join(os.path.dirname(full), target)
            if not os.path.exists(p):
                errors.append("文档链接指向不存在的路径：%s -> %s"
                              % (norm(os.path.relpath(full, root)), target))
    print("  %d 个 md 文件、%d 条相对链接" % (n_files, n_links))


def norm(p):
    return p.replace("\\", "/")


def walk(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        rel_dir = norm(os.path.relpath(dp, root))
        if rel_dir == ".":
            rel_dir = ""
        for f in sorted(fn):
            yield rel_dir, f, os.path.join(dp, f)


def check_names(root, errors, warnings):
    for rel_dir, f, full in walk(root):
        parts = [p for p in (rel_dir.split("/") if rel_dir else []) + [f] if p]
        for part in parts:
            if part in UPPERCASE_WHITELIST:
                continue
            if not ALLOWED_NAME.match(part):
                errors.append("命名不合规（只允许小写字母/数字/._-）：%s"
                              % norm(os.path.relpath(full, root)))


def check_structure(root, errors, warnings):
    for rel, kind in REQUIRED_PATHS:
        p = os.path.join(root, rel.replace("/", os.sep))
        ok = os.path.isfile(p) if kind == "file" else os.path.isdir(p)
        if not ok:
            warnings.append("缺少 %s 推荐项：%s（若采用自定义结构，请在 README.md 写明目录对照）"
                            % ("文件" if kind == "file" else "目录", rel))


def check_metrics(root, errors, warnings):
    p = os.path.join(root, "metrics", "metrics.csv")
    if not os.path.isfile(p):
        return
    with io.open(p, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        errors.append("metrics/metrics.csv 是空文件")
        return
    header = [c.strip() for c in rows[0]]
    if header != METRICS_HEADER:
        errors.append("metrics/metrics.csv 表头不符（3.2.5.2 规定 7 列）：\n"
                      "  期望：%s\n  实际：%s" % ("/".join(METRICS_HEADER), "/".join(header)))
    bad_cols = [i for i, r in enumerate(rows[1:], 2) if len(r) != len(METRICS_HEADER)]
    for i in bad_cols:
        errors.append("metrics/metrics.csv 第 %d 行列数不是 %d" % (i, len(METRICS_HEADER)))
    todo = len([r for r in rows[1:] if len(r) > 2 and r[2].strip().startswith("待测")])
    print("  metrics.csv: %d 行指标，其中 %d 行待测" % (len(rows) - 1, todo))


def check_size(root, errors, warnings):
    for rel_dir, f, full in walk(root):
        mb = os.path.getsize(full) / 1048576.0
        rel = norm(os.path.relpath(full, root))
        if mb > BIG_FILE_MB:
            errors.append("大文件 %.1f MB：%s（请走 Git LFS 或移出仓库）" % (mb, rel))
        elif mb > 5.0:
            warnings.append("偏大 %.1f MB：%s" % (mb, rel))


def main(argv=None):
    ap = argparse.ArgumentParser(description="仓库命名与结构自检（指南 3.2.5）")
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    errors, warnings = [], []
    print("[1/5] 路径命名（纯英文小写）…")
    check_names(root, errors, warnings)
    print("[2/5] 目录结构（3.2.5.5 推荐项）…")
    check_structure(root, errors, warnings)
    print("[3/5] metrics/metrics.csv 表头…")
    check_metrics(root, errors, warnings)
    print("[4/5] 单文件体积…")
    check_size(root, errors, warnings)
    print("[5/5] 文档相对链接…")
    check_links(root, errors, warnings)

    for w in warnings:
        print("  [WARN] " + w)
    for e in errors:
        print("  [ERR ] " + e)
    print("\n结果：%d 个 error，%d 个 warning。%s"
          % (len(errors), len(warnings), "通过。" if not errors else "未通过。"))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
