# -*- coding: utf-8 -*-
"""
导出 YOLOv8n 为 ONNX（AMD Ryzen AI / Vitis AI 友好格式）
=======================================================
用 ultralytics 导出 yolov8n，生成 opset 12 的 ONNX。
YOLOv8 无 C2PSA 注意力，NPU 兼容性远好于 YOLO11。

用法: python export_yolov8.py
产出: yolov8n.onnx + yolov8n.pt
"""

from ultralytics import YOLO

def main():
    print("[1/2] 加载 YOLOv8n（首次会自动下载 yolov8n.pt）...")
    model = YOLO("yolov8n.pt")

    print("[2/2] 导出 ONNX (opset 12, 动态 batch 关闭)...")
    # 固定 640x640，simplify 简化算子（利于 NPU）
    model.export(
        format="onnx",
        imgsz=640,
        opset=12,
        simplify=True,
        dynamic=False,
    )
    print("导出完成: yolov8n.onnx")

if __name__ == "__main__":
    main()
