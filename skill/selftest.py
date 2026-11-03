#!/usr/bin/env python3
"""skill 自检：把不需要硬件的三项检查串成一条命令。

用法（仓库根目录）：
    python skill/selftest.py              # 只跑离线项（hwh + 时序）
    python skill/selftest.py --with-infer # 额外跑推理基准（需要 onnxruntime + 模型）

设计原则：
  - 只读，不碰硬件、不改文件
  - 任一项 FAIL 退出码非 0，可直接串进 CI
  - 缺文件时明确说"跳过"，不算通过
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def call(script, args):
    """跑同目录下的某个工具，返回 (ok, 尾部输出)。"""
    cmd = [sys.executable, os.path.join(HERE, script)] + args
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip().splitlines()


def show(title, ok, lines, keep=6):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}")
    for ln in lines[-keep:]:
        print("       " + ln)


def main():
    ap = argparse.ArgumentParser(description="skill 自检（离线项）")
    ap.add_argument("--with-infer", action="store_true",
                    help="额外跑 bench_infer（需要 onnxruntime 与模型）")
    ap.add_argument("--model", default=os.path.join(ROOT, "host", "model", "best.onnx"))
    args = ap.parse_args()

    results = []

    # ---- 1) .hwh 结构检查 ----
    hwh = os.path.join(ROOT, "fpga", "pynq", "motor.hwh")
    if not os.path.exists(hwh):
        print(f"[SKIP] hwh_probe：找不到 {hwh}")
        results.append(False)
    else:
        ok, lines = call("hwh_probe.py",
                         [hwh, "--expect", "uartlite", "--baud", "230400"])
        show("hwh_probe（这份 bitstream 里有没有 axi_uartlite）", ok, lines)
        results.append(ok)

    # ---- 2) 时序与资源 ----
    rep = os.path.join(ROOT, "fpga", "report")
    if not os.path.isdir(rep):
        print(f"[SKIP] check_timing：找不到 {rep}")
        results.append(False)
    else:
        ok, lines = call("check_timing.py", ["--reports", rep, "--min-wns", "0.5"])
        show("check_timing（时序收敛 + 资源占用）", ok, lines, keep=4)
        results.append(ok)

    # ---- 3) 推理基准（可选，慢且吃 GPU）----
    if args.with_infer:
        if not os.path.exists(args.model):
            print(f"[SKIP] bench_infer：找不到模型 {args.model}")
        else:
            ok, lines = call("bench_infer.py",
                             ["--model", args.model, "--backend", "dml",
                              "--input", "1x3x640x640", "--n", "30"])
            show("bench_infer（DirectML 纯前向延迟）", ok, lines, keep=5)
            results.append(ok)

    print("-" * 52)
    if results and all(results):
        print("全部通过。注意：这只说明『链路判定工具本身可用』，")
        print("不代表板子在跑、也不代表车能避障——那要上电实测。")
        return 0
    print("有项目未通过，逐项看上面的 FAIL。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
