# -*- coding: utf-8 -*-
"""
RyzenAI NPU 推理封装（AMD Ryzen AI 1.6.0 / Vitis AI ONNX Runtime）
==============================================================
提供一个通用类 NpuInfer，把「ONNX 模型 → NPU 推理」封装成三行调用：
    npu = NpuInfer(model_path)          # 加载模型到 NPU
    out = npu.run(feed_dict)            # 单次推理
    ...                                 # 循环里反复 run

底层：onnxruntime 的 VitisAIExecutionProvider（AMD NPU / AI Engine）
"""

import os
import numpy as np
import onnxruntime as ort

# AMD Ryzen AI 安装路径（按你的机器，1.6.0）
RYZEN_AI_INSTALL = r"C:\Program Files\RyzenAI\1.6.0"
# Vitis AI 配置文件（NPU 运行时必需）
VAIP_CONFIG = os.path.join(RYZEN_AI_INSTALL, "voe-4.0-win_amd64", "vaip_config.json")

# 检测 NPU 型号：PHX/HPT(1502) 或 STX/KRK(17F0)
def _detect_npu():
    import subprocess
    try:
        out = subprocess.run(
            ["pnputil", "/enum-devices", "/bus", "PCI", "/deviceids"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        ).stdout.decode("utf-8", errors="replace")
    except Exception:
        return "unknown"
    if "VEN_1022&DEV_1502" in out:
        return "PHX/HPT"
    if "VEN_1022&DEV_17F0" in out:
        return "STX"
    return "unknown"


class NpuInfer:
    def __init__(self, model_path, target="auto", cache_dir=None):
        """
        model_path : ONNX 模型文件路径
        target     : NPU 目标代号，'auto' 自动检测
        cache_dir  : 编译缓存目录（首次会慢，缓存后变快）
        """
        self.model_path = model_path
        os.environ["RYZEN_AI_INSTALLATION_PATH"] = RYZEN_AI_INSTALL
        os.environ.setdefault("XLNX_VART_FIRMWARE", "")

        npu = _detect_npu() if target == "auto" else target
        self.npu = npu
        print(f"[NpuInfer] 检测到 NPU: {npu}")

        if cache_dir is None:
            # 默认放用户目录（避免 Program Files 无写权限）
            cache_dir = os.path.join(
                os.path.expanduser("~"), ".ryzenai_cache", os.path.basename(model_path)
            )
        os.makedirs(cache_dir, exist_ok=True)
        self.cache_dir = cache_dir
        self.cache_key = os.path.basename(model_path)

        # 构造 VitisAIExecutionProvider 的选项
        provider_options = [{
            "cache_dir": cache_dir,
            "cache_key": self.cache_key,
        }]
        if npu == "PHX/HPT":
            xclbin = os.path.join(
                RYZEN_AI_INSTALL, "voe-4.0-win_amd64", "xclbins", "phoenix", "4x4.xclbin"
            )
            provider_options[0]["target"] = "X1"
            provider_options[0]["xclbin"] = xclbin
        # STX/KRK 不需要额外 xclbin，保持 cache 选项即可

        providers = ["VitisAIExecutionProvider"]
        try:
            self.session = ort.InferenceSession(
                model_path,
                providers=providers,
                provider_options=provider_options,
            )
            print("[NpuInfer] 模型已加载到 NPU (VitisAIExecutionProvider)")
        except Exception as e:
            print(f"[NpuInfer] NPU 加载失败，回退 CPU: {e}")
            self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

        # 记录输入输出名与形状，方便调用
        self.input_names  = [i.name for i in self.session.get_inputs()]
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.input_shape  = self.session.get_inputs()[0].shape
        self.output_shape = self.session.get_outputs()[0].shape
        print(f"[NpuInfer] 输入 {self.input_names[0]} {self.input_shape}")
        print(f"[NpuInfer] 输出 {self.output_names[0]} {self.output_shape}")

    def run(self, feed_dict):
        """feed_dict: {输入名: numpy数组}，返回输出数组列表"""
        return self.session.run(self.output_names, feed_dict)


if __name__ == "__main__":
    # 自测：用官方 quicktest 模型跑一帧随机数据
    m = os.path.join(RYZEN_AI_INSTALL, "quicktest", "test_model.onnx")
    npu = NpuInfer(m)
    x = np.random.rand(1, 3, 32, 32).astype(np.float32)
    out = npu.run({npu.input_names[0]: x})
    print("[自测] 输出 shape:", out[0].shape)
    print("[自测] 输出前5个值:", out[0][0][:5])
    print("[自测] 预测类别:", int(np.argmax(out[0])))
