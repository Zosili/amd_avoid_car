# -*- coding: utf-8 -*-
"""
导出 18 类检测模型为 ONNX（静态 shape，给 Ryzen AI NPU / FPGA 用）

用法（必须用 yolo-rocm 环境）：
    C:\\Users\\shang\\.conda\\envs\\yolo-rocm\\python.exe D:\\FPGA\\ultralytics\\tools\\export_onnx.py

产物：
    D:\\FPGA\\ultralytics\\export\\yolo11n_18cls_640.onnx

为什么这么选参数：
  - opset=12    : Ryzen AI (Vitis AI EP) 对高 opset 支持不全，12 是最稳的一档
  - dynamic=False: NPU 要求静态 shape；动态维度会直接导致编译失败
  - simplify=True: 用 onnxslim 折叠常量、合并冗余节点，能显著减少算子数
  - half=False  : 保持 fp32。NPU 侧量化由 Vitis AI 自己做，先给全精度再量化
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# 与训练脚本一致的环境隔离
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ.pop("PYTHONPATH", None)

DEFAULT_BEST = PROJECT / "runs" / "18cls_yolo11n" / "weights" / "best.pt"
OUT_DIR = PROJECT / "export"
DEFAULT_OUT_NAME = "yolo11n_18cls_640.onnx"
IMGSZ = int(sys.argv[4]) if len(sys.argv) > 4 else 640
# opset 12 够 NPU 编译用，但不支持 QDQ 的 per-channel（axis 属性 opset 13 才有）。
# 需要 a8w8 量化时传 13。
OPSET = int(sys.argv[3]) if len(sys.argv) > 3 else 12

# 支持命令行覆盖：export_onnx.py [权重路径] [输出文件名]
# 例：export_onnx.py D:\FPGA\42_ov5640_udp_pc\yolov8n.pt yolov8n_coco_640.onnx
BEST = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BEST
OUT_NAME = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT_NAME


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not BEST.exists():
        print(f"[×] 权重不存在: {BEST}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 62)
    print("导出 ONNX")
    print("=" * 62)
    print(f"  权重   : {BEST}  ({BEST.stat().st_size / 1024 ** 2:.1f} MB)")
    print(f"  imgsz  : {IMGSZ}")
    print(f"  opset  : {OPSET}")
    print(f"  dynamic: False（NPU 要求静态 shape）")
    print(f"  simplify: True（onnxslim）")
    print()

    from ultralytics import YOLO

    model = YOLO(str(BEST))
    exported = model.export(
        format="onnx",
        imgsz=IMGSZ,
        opset=OPSET,
        simplify=True,
        dynamic=False,
        half=False,
    )

    src = Path(exported)
    dst = OUT_DIR / OUT_NAME
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)

    print()
    print("=" * 62)
    print("完成")
    print("=" * 62)
    print(f"  输出    : {dst}")
    print(f"  大小    : {dst.stat().st_size / 1024 ** 2:.2f} MB")
    print()
    print("下一步：用 ryzen-ai-1.6.0 环境跑算子检查")
    print(f"  D:\\miniconda\\envs\\ryzen-ai-1.6.0\\python.exe "
          f"D:\\FPGA\\ultralytics\\tools\\check_onnx.py \"{dst}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
