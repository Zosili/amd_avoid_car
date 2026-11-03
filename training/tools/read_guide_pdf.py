# -*- coding: utf-8 -*-
"""抽取比赛选题指南 PDF 的 10-18 页正文"""
import sys
from pathlib import Path

from pypdf import PdfReader

PDF = Path(r"C:\Users\shang\Downloads") / "全国大学生嵌入式芯片与系统设计竞赛'2026FPGA赛道选题指南-AMD.pdf"

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = PdfReader(str(PDF))
    n = len(r.pages)
    print(f"文件: {PDF.name}")
    print(f"总页数: {n}")
    print("=" * 70)
    for i in range(n):
        # 1-based 页号
        if i + 1 < 10 or i + 1 > 18:
            continue
        txt = r.pages[i].extract_text() or ""
        print(f"\n----- 第 {i + 1} 页 -----")
        print(txt.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
