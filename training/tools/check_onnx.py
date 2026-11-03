# -*- coding: utf-8 -*-
"""
ONNX 算子检查 —— 判断能不能上 Ryzen AI NPU（Vitis AI EP）

用法（必须用 ryzen-ai-1.6.0 环境，那个才有 VitisAIExecutionProvider）：
    D:\\miniconda\\envs\\ryzen-ai-1.6.0\\python.exe D:\\FPGA\\ultralytics\\tools\\check_onnx.py
    （可省略参数，默认检查 export\\yolo11n_18cls_640.onnx）

检查三段：
  1) 静态分析 —— 结构、算子清单、动态维度（一定成功）
  2) NPU 实测 —— 用 VitisAIExecutionProvider 建 session，统计多少节点真的落在 NPU
  3) CPU 验证 —— 跑一遍前向，确认模型本身能出正确 shape 的输出
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import onnx

# Vitis AI EP 初始化时会读这个变量，缺了直接 KeyError 或找不到 xclbin
os.environ.setdefault("RYZEN_AI_INSTALLATION_PATH", r"C:\Program Files\RyzenAI\1.6.0")

DEFAULT_ONNX = Path(r"D:\FPGA\ultralytics\export\yolo11n_18cls_640.onnx")

# Vitis AI EP 必须显式告诉它跑在哪块 NPU 上，否则会拿默认配置去编译：
# 编译能过，但生成的指令在真实硬件上执行会报 ERT_CMD_STATE_ERROR。
# 这几项完全照 AMD 官方 quicktest.py 的写法取（PHX/HPT 分支）。
RYZEN_AI_DIR = Path(
    os.environ.get("RYZEN_AI_INSTALLATION_PATH", r"C:\Program Files\RyzenAI\1.6.0")
)
VOE_DIR = RYZEN_AI_DIR / "voe-4.0-win_amd64"
XCLBIN = VOE_DIR / "xclbins" / "phoenix" / "4x4.xclbin"
VAIP_CONFIG = VOE_DIR / "vaip_config.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "export" / "vaip_cache"
# cache_key 必须随模型变。用同一个 key 会命中旧模型的编译缓存，
# 拿到的是上一个模型的分配结果，检查结论会完全错。
CACHE_KEY = os.environ.get("VAIP_CACHE_KEY", "yolo11n_18cls_640")
NPU_TARGET = "X1"  # XDNA1（Phoenix / Hawk Point 8845H）

# 按"对 NPU 的友好程度"分类。注意：这只是静态预判，
# 真正的判据是下面第 2 段 VitisAI EP 实测分配结果。
CORE_OPS = {
    "Conv", "BatchNormalization", "Relu", "Silu", "Sigmoid", "Hardswish", "HardSigmoid",
    "Add", "Mul", "Sub", "Div", "MaxPool", "AveragePool", "GlobalAveragePool",
    "Concat", "Resize", "Upsample", "Pad", "LeakyRelu",
}
LAYOUT_OPS = {
    "Shape", "Gather", "Reshape", "Transpose", "Split", "Slice", "Squeeze", "Unsqueeze",
    "Cast", "Expand", "ConstantOfShape", "Range", "Where", "Tile",
}
RISKY_OPS = {
    "GridSample", "Softmax", "MatMul", "Erf", "TopK", "NonMaxSuppression",
    "ScatterND", "GatherND", "Einsum", "ReduceMean", "ReduceSum", "ReduceMax",
    "Pow", "Sqrt", "Exp", "Log", "Floor", "Ceil", "Round", "Mod",
}


def fmt_bytes(n: int) -> str:
    return f"{n / 1024 ** 2:.2f} MB"


def static_analysis(path: Path) -> dict:
    print("=" * 66)
    print("1) 静态分析")
    print("=" * 66)
    print(f"  文件      : {path}")
    print(f"  大小      : {fmt_bytes(path.stat().st_size)}")

    model = onnx.load(str(path))
    onnx.checker.check_model(model)

    print(f"  IR version: {model.ir_version}")
    opsets = {o.domain or "ai.onnx": o.version for o in model.opset_import}
    print(f"  opset     : {opsets}")

    g = model.graph
    print()
    print("  输入:")
    for i in g.input:
        dims = [d.dim_value if d.dim_value else (d.dim_param or "?") for d in i.type.tensor_type.shape.dim]
        dt = onnx.TensorProto.DataType.Name(i.type.tensor_type.elem_type)
        print(f"    {i.name:20s} {dt:8s} {dims}")
    print("  输出:")
    for o in g.output:
        dims = [d.dim_value if d.dim_value else (d.dim_param or "?") for d in o.type.tensor_type.shape.dim]
        dt = onnx.TensorProto.DataType.Name(o.type.tensor_type.elem_type)
        print(f"    {o.name:20s} {dt:8s} {dims}")

    # 动态维度检查：NPU 编译要求全静态
    dyn = []
    for t in list(g.input) + list(g.output):
        for d in t.type.tensor_type.shape.dim:
            if d.dim_param:
                dyn.append(f"{t.name}:{d.dim_param}")
    print(f"  动态维度  : {'无（全静态，NPU 友好）' if not dyn else dyn}")

    ops = Counter(n.op_type for n in g.node)
    total = sum(ops.values())
    print()
    print(f"  节点总数  : {total}   算子种类: {len(ops)}")
    print()
    print(f"  {'算子':<24s} {'次数':>5s}  分类")
    print(f"  {'-' * 24} {'-' * 5}  {'-' * 12}")
    for op, cnt in ops.most_common():
        if op in CORE_OPS:
            tag = "核心"
        elif op in RISKY_OPS:
            tag = "需实测"
        elif op in LAYOUT_OPS:
            tag = "布局/控制"
        else:
            tag = "其他"
        print(f"  {op:<24s} {cnt:5d}  {tag}")

    n_core = sum(c for o, c in ops.items() if o in CORE_OPS)
    n_layout = sum(c for o, c in ops.items() if o in LAYOUT_OPS)
    n_risky = sum(c for o, c in ops.items() if o in RISKY_OPS)
    n_other = total - n_core - n_layout - n_risky
    print()
    print(f"  占比: 核心 {n_core / total * 100:.1f}% | 布局/控制 {n_layout / total * 100:.1f}% "
          f"| 需实测 {n_risky / total * 100:.1f}% | 其他 {n_other / total * 100:.1f}%")
    if n_risky:
        risky_list = [f"{o}×{c}" for o, c in ops.items() if o in RISKY_OPS]
        print(f"  ⚠ 需实测算子: {', '.join(risky_list)}")
    print()
    return {"path": path, "ops": ops, "total": total}


def npu_probe(path: Path) -> None:
    print("=" * 66)
    print("2) NPU 实测（VitisAIExecutionProvider）")
    print("=" * 66)

    try:
        import onnxruntime as ort
    except ImportError as e:
        print(f"  [×] onnxruntime 导入失败: {e}")
        return

    avail = ort.get_available_providers()
    print(f"  可用 providers: {avail}")
    if "VitisAIExecutionProvider" not in avail:
        print("  [×] 当前环境没有 Vitis AI EP，跳过 NPU 实测")
        return

    so = ort.SessionOptions()
    so.enable_profiling = True
    # 3=ERROR 只报错；0=VERBOSE 会把 Vitis AI 编译器每个 pass 的细节打出来。
    # 排查"为什么回退 CPU"时必须用 0，否则看不到 export_to_xir.cpp 的 FATAL。
    so.log_severity_level = int(os.environ.get("VAIP_LOG_SEVERITY", "3"))

    tmpdir = tempfile.mkdtemp(prefix="vai_prof_")
    print(f"  profile 临时目录: {tmpdir}")

    if not XCLBIN.exists():
        print(f"  [×] 找不到 xclbin: {XCLBIN}")
        print("      检查 RYZEN_AI_INSTALLATION_PATH 是否指向 RyzenAI 安装目录")
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    provider_options = [{
        "cache_dir": str(CACHE_DIR),
        "cache_key": CACHE_KEY,
        "target": NPU_TARGET,
        "xclbin": str(XCLBIN),
    }]
    print(f"  target    : {NPU_TARGET}")
    print(f"  xclbin    : {XCLBIN.name}")
    print(f"  cache_dir : {CACHE_DIR}")
    print("  （首次运行要对整图编译，640x640 大约 40~50 分钟；"
          "编译结果会缓存，之后再跑只要几秒）")
    print()

    try:
        sess = ort.InferenceSession(
            str(path),
            so,
            providers=["VitisAIExecutionProvider"],
            provider_options=provider_options,
        )
    except Exception as e:
        print("  [×] Vitis AI EP 初始化失败：")
        print(f"      {type(e).__name__}: {e}")
        print()
        print("  常见原因：缺 vaip_config.json / 缺 xclbin / target 不匹配 / 驱动版本不符")
        print("  这不代表模型不兼容，只说明本机 EP 配置没配好。静态分析结果仍然有效。")
        return

    inp = sess.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    feed = {inp.name: np.random.rand(*shape).astype(np.float32)}

    try:
        outs = sess.run(None, feed)
    except Exception as e:
        print(f"  [×] 推理失败: {type(e).__name__}: {e}")
        return

    print(f"  推理成功，输出 {len(outs)} 个:")
    for i, o in enumerate(outs):
        print(f"    out[{i}] shape={o.shape} dtype={o.dtype}")

    prof_file = sess.end_profiling()
    try:
        events = json.loads(Path(prof_file).read_text(encoding="utf-8"))
        providers = Counter(
            e.get("args", {}).get("provider", "unknown")
            for e in events
            if e.get("cat") == "Node"
        )
        total = sum(providers.values())
        print()
        print("  节点分配（这是判断能否上 NPU 的关键证据）:")
        for p, c in providers.most_common():
            print(f"    {p:<32s} {c:5d} 节点  ({c / total * 100:.1f}%)")
        npu_cnt = sum(c for p, c in providers.items() if "VitisAI" in p)
        print()
        if npu_cnt == 0:
            print("  [×] 没有任何节点落在 NPU —— 全部回退 CPU，等于没加速")
        elif npu_cnt / total < 0.8:
            print(f"  [!] 只有 {npu_cnt / total * 100:.1f}% 的节点在 NPU，其余回退 CPU，"
                  f"收益会被数据搬运吃掉")
        else:
            print(f"  [✓] {npu_cnt / total * 100:.1f}% 节点在 NPU，加速效果应该明显")
    except Exception as e:
        print(f"  [!] profile 解析失败: {e}")
    finally:
        try:
            os.remove(prof_file)
        except OSError:
            pass


def cpu_verify(path: Path) -> None:
    print()
    print("=" * 66)
    print("3) CPU 验证")
    print("=" * 66)
    import onnxruntime as ort

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    feed = {inp.name: np.random.rand(*shape).astype(np.float32)}
    outs = sess.run(None, feed)
    for i, o in enumerate(outs):
        print(f"  out[{i}] shape={o.shape}  范围=[{o.min():.3f}, {o.max():.3f}]  "
              f"含 NaN: {bool(np.isnan(o).any())}")
    print("  [✓] 模型可正常前向")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ONNX
    if not path.exists():
        print(f"[×] 文件不存在: {path}")
        print("    先跑导出: yolo-rocm\\python.exe D:\\FPGA\\ultralytics\\tools\\export_onnx.py")
        return 1

    static_analysis(path)
    npu_probe(path)
    try:
        cpu_verify(path)
    except Exception as e:
        print(f"  [×] CPU 验证失败: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
