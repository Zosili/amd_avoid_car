# -*- coding: utf-8 -*-
"""临时自测：验证 import_18cls 的补标分支不会改动原始 18cls 标签。"""
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "tools"))
import build_living_dataset as B

img = next((PROJECT / "datasets/18cls/images/train").glob("000001.jpg"))
lab = PROJECT / "datasets/18cls/labels/train/000001.txt"
orig = lab.read_text(encoding="utf-8")
print("原标签行数", len(orig.splitlines()), flush=True)

tmp = PROJECT / "datasets/_tmpB"
if tmp.exists():
    shutil.rmtree(tmp)
B.OUT = tmp
B.ensure_dirs()
st = B.import_18cls(None, {img.stem: [[0.5, 0.5, 0.2, 0.4]]})
print("stat", {k: v for k, v in st.items() if v}, flush=True)

new = (tmp / "labels/train/000001.txt").read_text(encoding="utf-8")
print("新标签行数", len(new.splitlines()), "| 末行:", new.splitlines()[-1], flush=True)
print("原文件被改动:", "是(危险!)" if lab.read_text(encoding="utf-8") != orig else "否 OK", flush=True)
print("图片硬链接:", os.stat(tmp / "images/train/000001.jpg").st_ino == os.stat(img).st_ino, flush=True)
print("标签是独立文件:", os.stat(tmp / "labels/train/000001.txt").st_ino != os.stat(lab).st_ino, flush=True)
shutil.rmtree(tmp)
print("清理完成", flush=True)
