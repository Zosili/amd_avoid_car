# -*- coding: utf-8 -*-
"""
YOLO11n INT8 量化（AMD Ryzen AI / Vitis AI 标准流程）
==================================================
用 onnxruntime 自带的 quantize_static 做 INT8 量化，
产出 NPU(VitisAIExecutionProvider) 能高效执行的模型。

量化类型：QuantType.QInt8  (Vitis AI NPU 推荐 INT8)
格式：     QuantFormat.QDQ  (QDQ 格式，NPU 能直接消费)
"""

import os
import numpy as np
import cv2
import onnxruntime as ort
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantFormat, QuantType, CalibrationMethod

MODEL   = r"D:\FPGA\42_ov5640_udp_pc\yolov8n.onnx"
OUT     = r"D:\FPGA\42_ov5640_udp_pc\yolov8n_int8.onnx"
CAL_IMG = r"D:\FPGA\42_ov5640_udp_pc\test_frame_swap.png"   # 校准参考图

class YoloCalibReader(CalibrationDataReader):
    """校准数据读取器：喂一批 640x640 RGB 归一化图"""
    def __init__(self, img_path, n=32):
        self.n = n
        self.idx = 0
        # 读一张真实图做基础，加随机扰动扩充到 n 张
        base = cv2.imread(img_path)
        if base is None:
            base = np.random.randint(0, 255, (640, 640, 3), np.uint8)
        base = cv2.resize(base, (640, 640))
        self.data = []
        for _ in range(n):
            img = base.copy()
            # 轻微扰动模拟不同帧
            noise = np.random.randint(-15, 15, img.shape).astype(np.int16)
            img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            x = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
            self.data.append(x[None])
        self.input_name = "images"

    def get_next(self):
        if self.idx >= self.n:
            return None
        d = {self.input_name: self.data[self.idx]}
        self.idx += 1
        return d

def main():
    print("[1/2] 开始 INT8 量化（校准 + QDQ 导出）...")
    reader = YoloCalibReader(CAL_IMG, n=32)
    quantize_static(
        model_input=MODEL,
        model_output=OUT,
        calibration_data_reader=reader,
        quant_format=QuantFormat.QDQ,
        activation_type=QuantType.QUInt8,      # 无符号，Vitis AI 更友好
        weight_type=QuantType.QInt8,
        calibrate_method=CalibrationMethod.MinMax,
        per_channel=False,                     # per-tensor，避免 DequantizeLinear 带 axis
    )
    sz = os.path.getsize(OUT) / 1e6
    print(f"[完成] 量化模型已保存: {OUT}  ({sz:.1f} MB)")

    # 对比原始大小
    sz0 = os.path.getsize(MODEL) / 1e6
    print(f"       原始 {sz0:.1f} MB -> 量化 {sz:.1f} MB")

if __name__ == "__main__":
    main()
