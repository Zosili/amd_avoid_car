# -*- coding: utf-8 -*-
"""
兼容性冒烟用的 INT8 QDQ 量化（不追求精度，只为回答一个问题：
Vitis AI EP 是不是只认量化模型）

用法：
    python quant_smoke.py <in.onnx> <out.onnx> [act] [wgt] [样本数] [per_channel]

    act/wgt 取值 QUInt8 或 QInt8。Vitis AI 标准 a8w8 = 激活 QUInt8 + 权重 QInt8。
    per_channel 需要 opset >= 13，opset 12 会报 axis 属性非法。

校准数据用随机噪声，只用于验证"能不能被 NPU 认领"。
真实部署必须换成真实图片做校准，否则 mAP 会掉很多。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import (
    CalibrationDataReader,
    QuantFormat,
    QuantType,
    quantize_static,
)


class RandomReader(CalibrationDataReader):
    def __init__(self, input_name: str, shape: list[int], n: int = 8):
        self.input_name = input_name
        self.shape = shape
        self.n = n
        self.i = 0
        rng = np.random.default_rng(0)
        # 用 0~1 均匀噪声模拟归一化后的图像，足够让量化器算出合理的 scale
        self.data = [rng.random(shape, dtype=np.float32) for _ in range(n)]

    def get_next(self):
        if self.i >= self.n:
            return None
        d = {self.input_name: self.data[self.i]}
        self.i += 1
        return d

    def rewind(self):
        self.i = 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    act = sys.argv[3] if len(sys.argv) > 3 else "QUInt8"
    wgt = sys.argv[4] if len(sys.argv) > 4 else "QInt8"
    n = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    per_ch = (sys.argv[6].lower() in ("1", "true", "yes")) if len(sys.argv) > 6 else True

    def qt(name: str) -> QuantType:
        return QuantType.QUInt8 if name.upper() == "QUINT8" else QuantType.QInt8

    if not src.exists():
        print(f"[x] 不存在: {src}")
        return 1

    sess = ort.InferenceSession(str(src), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    print("=" * 62)
    print("INT8 QDQ 量化（兼容性冒烟）")
    print("=" * 62)
    print(f"  输入   : {src}")
    print(f"  输出   : {dst}")
    print(f"  输入名 : {inp.name}  shape={shape}")
    print(f"  activation: {act}   weight: {wgt}   per_channel: {per_ch}")
    print(f"  校准样本  : {n} 张随机噪声（仅供兼容性测试）")
    print()

    quantize_static(
        model_input=str(src),
        model_output=str(dst),
        calibration_data_reader=RandomReader(inp.name, shape, n),
        quant_format=QuantFormat.QDQ,
        per_channel=per_ch,
        activation_type=qt(act),
        weight_type=qt(wgt),
        op_types_to_quantize=None,
    )

    import onnx

    m = onnx.load(str(dst))
    kinds = {}
    for node in m.graph.node:
        kinds[node.op_type] = kinds.get(node.op_type, 0) + 1
    q = {k: v for k, v in kinds.items() if "Quant" in k or "Dequant" in k}
    print()
    print(f"  [OK] 已生成  {dst.stat().st_size / 1024 ** 2:.2f} MB")
    print(f"  量化算子: {q}")
    print(f"  节点总数: {sum(kinds.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
