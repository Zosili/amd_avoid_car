# -*- coding: utf-8 -*-
"""
GPU 归属自检 —— 确认推理到底跑在哪块显卡上。

背景（本机实测教训）：
    DirectML 的默认设备**不是**核显，而是独显。这台机器三块 DXGI 适配器：
        [0] AMD Radeon 780M      luid 0x0177f57f   ← 赛题要求的 Ryzen AI 平台
        [1] NVIDIA RTX 4060      luid 0x0113d8     ← DML 的默认设备
        [2] AMD Radeon 780M(二)  luid 0x018561
        [3] Microsoft Basic Render Driver (WARP 软件)
    不指定 device_id 就会静默跑在 4060 上：快 3.5 倍，但**不合规**，
    而且报告里的延迟数据全是假的（测的不是目标平台）。

用法：
    python gpu_check.py                    # 枚举 + 对每个适配器测 DML 延迟
    python gpu_check.py --onnx export/yolo11n_18cls_480.onnx

外部交叉验证（另开一个 PowerShell）：
    $p = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
          Where-Object { $_.CommandLine -like '*detect_realtime*' }).ProcessId
    (Get-Counter '\\GPU Engine(*)\\Utilization Percentage').CounterSamples |
      Where-Object { $_.InstanceName -like "*pid_$p*" -and $_.CookedValue -gt 0.5 }
    # 打印出来的 luid_0x... 应该等于本脚本给出的 AMD 适配器 LUID
    nvidia-smi --query-compute-apps=pid,process_name --format=csv
    # 若 python 进程出现在这里，说明跑到独显上了
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dxgi_adapters import enum_adapters          # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent


def bench_dml(sess, imgsz: int, n: int = 60) -> float:
    name = sess.get_inputs()[0].name
    x = np.zeros((1, 3, imgsz, imgsz), np.float32)
    for _ in range(5):
        sess.run(None, {name: x})
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, {name: x})
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return ts[len(ts) // 2]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", default=str(PROJECT / "export" / "yolo11n_18cls_480.onnx"))
    ap.add_argument("--imgsz", type=int, default=480)
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args()

    print("=" * 78)
    print("1) DXGI 适配器枚举（onnxruntime 的 device_id 就是这个下标）")
    print("=" * 78)
    ads = enum_adapters()
    if not ads:
        print("  枚举失败")
        return 1
    for x in ads:
        tag = ""
        if x["vendor_id"] == 0x1002:
            tag = "  <- AMD / Ryzen AI"
        elif x["vendor_id"] == 0x10DE:
            tag = "  <- NVIDIA / 不合规"
        elif x["vendor_id"] == 0x1414:
            tag = "  <- 软件渲染"
        print(f"  [{x['device_id']}] {x['name']:<40s} "
              f"vendor=0x{x['vendor_id']:04X}({x['vendor']})  "
              f"luid={x['luid_str']}  显存 {x['dedicated_mb']} MB{tag}")

    print()
    print("=" * 78)
    print("2) torch / ROCm 设备（这一路只认 AMD，天然正确）")
    print("=" * 78)
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            arch = getattr(p, "gcnArchName", "n/a")
            print(f"  torch {torch.__version__}  device 0 = {torch.cuda.get_device_name(0)}"
                  f"  arch={arch}")
        else:
            print("  当前环境没有可用 torch 加速设备")
    except ImportError:
        print("  当前环境没有 torch（正常，DML 环境里不需要）")

    print()
    print("=" * 78)
    print(f"3) 逐个适配器测 DML 推理（{Path(a.onnx).name}, imgsz={a.imgsz}）")
    print("=" * 78)
    try:
        import onnxruntime as ort
    except ImportError:
        print("  当前环境没有 onnxruntime，跳过")
        return 0

    if "DmlExecutionProvider" not in ort.get_available_providers():
        print(f"  当前环境的 ort 不支持 DML：{ort.get_available_providers()}")
        return 0

    results = []
    for x in ads:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        try:
            sess = ort.InferenceSession(
                a.onnx, so,
                providers=["DmlExecutionProvider", "CPUExecutionProvider"],
                provider_options=[{"enable_fp16": "1",
                                   "device_id": str(x["device_id"])}, {}])
            ms = bench_dml(sess, a.imgsz, a.n)
            who = "AMD" if x["vendor_id"] == 0x1002 else x["vendor"]
            print(f"  device_id={x['device_id']}  {x['name']:<40s} "
                  f"P50 {ms:6.2f} ms  ({who})")
            results.append((ms, x))
        except Exception as e:
            print(f"  device_id={x['device_id']}  {x['name']:<40s} 失败："
                  f"{type(e).__name__}: {e}")

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    amd = [r for r in results if r[1]["vendor_id"] == 0x1002]
    if amd:
        amd.sort(key=lambda t: t[0])
        ms, x = amd[0]
        print(f"  最快的 AMD 适配器：device_id={x['device_id']}  {x['name']}"
              f"  (luid {x['luid_str']})  {ms:.2f} ms")
        print(f"  → 建议加参数：--dml-device {x['device_id']}")
    else:
        print("  [!] 没有可用的 AMD 适配器")

    nv = [r for r in results if r[1]["vendor_id"] == 0x10DE]
    if nv:
        ms, x = nv[0]
        print(f"  [!] 独显 device_id={x['device_id']} 也能跑（{ms:.2f} ms），")
        print(f"      而且通常比核显快。**别用**——赛题要求 Ryzen AI 平台，")
        print(f"      在独显上测出来的延迟不能作为 AMD 平台的成绩上报。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
