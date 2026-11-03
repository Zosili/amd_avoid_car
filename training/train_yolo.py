# -*- coding: utf-8 -*-
"""
============================================================
 18 类室内物体检测 —— YOLO11 训练脚本（ROCm / 780M 版）
============================================================

怎么跑（不用命令行敲参数，直接运行本文件即可）：

   方式一：VS Code 打开本文件，右上角 ▷ Run Python File
   方式二：双击同目录的  run_train.bat
   方式三：命令行只指定解释器，不带任何训练参数：
           C:\\Users\\shang\\.conda\\envs\\yolo-rocm\\python.exe D:\\FPGA\\ultralytics\\train_yolo.py

⚠ 必须用 yolo-rocm 环境的 python 运行（Py3.12 + torch 2.10+rocm）。
  如果用别的 python 跑，脚本会在「环境自检」阶段直接报错退出，不会白跑。

所有可调参数都在下面的【配置区】，改完保存再运行即可。

⚠ 首次运行时 HIP 要现场编译 kernel，会额外花 10~20 秒，属正常现象。

作者：欧阳博怀    日期：2026-10-19
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ============================================================
# 【配置区】—— 只改这里
# ============================================================

# 本脚本所在目录 = 工作根目录
PROJECT = Path(__file__).resolve().parent

# 预训练权重（相对 PROJECT，也可以写绝对路径）
MODEL = PROJECT / "weights" / "yolo11n.pt"

# ---- 选数据集 ----
#   "18cls"  : 原来的 18 类纯非活体（11104 张，贴地视角）
#   "living" : 18cls + person 合并，19 类，带活体（由 tools/build_living_dataset.py 生成）
# 切换后 EXP_NAME 会自动跟着变，两个实验的输出互不覆盖。
DATASET = "living"

# 数据集配置（YOLO 格式，类别顺序的唯一事实源是 tools/living_semantics.py）
DATA = PROJECT / "datasets" / DATASET / "data.yaml"

# 实验名（输出目录 runs/<EXP_NAME>/）
#   带数据集名做前缀：以后加动物、换数据集都不会撞名覆盖历史结果
EXP_NAME = f"{DATASET}_yolo11n"

# ---- 训练超参 ----
EPOCHS = 15           # 训练轮数。本次只验链路：15 轮够看 person 召回趋势和 18 类
                      # mAP 有没有掉。workers=0 下约 8 分钟/轮，总共约 2 小时。
IMGSZ = 640           # 输入尺寸。416 是 780M 的甜点；改 640 会让 CPU 预处理涨 2.4 倍，见 README
BATCH = 16            # 批大小。实测（AMP fp16 / 780M / 640）batch 8/16/32 每张耗时
                      # 7.68 / 7.43 / 7.55 ms，吞吐恒定 ~130 img/s —— GPU 已完全饱和，
                      # 加大 batch 不提速（详见 living_detect.md「训练速度实测」）。
                      # 保留 16 的理由不是速度，而是 BatchNorm 统计更稳、梯度噪声更小。
                      # OOM 就回退 8 / 4 / 2。
PATIENCE = 30         # 连续多少轮 mAP 不涨就早停
WORKERS = 0           # 数据加载子进程数。
                      # 2026-10-29 实测：设为 3 时 Windows 下 spawn worker 不释放，
                      # 第 1 轮剩 3 个、第 2 轮变 9 个，每个占 0.4~0.5 GB，整机可用内存
                      # 从 6.7 GB 掉到 0.7 GB（96%），第 1 轮被换页拖到 888 秒/轮。
                      # dataloader 本来只占一步耗时的 6%，串行也值得，改 0 彻底断根。
                      # 若哪天换 Linux 再改回 4~8。
DEVICE = 0            # 0 = 第一个 GPU（ROCm 下会映射到 780M）
AMP = True            # 混合精度。780M 上开着更快，若出现 NaN 就改 False

# ---- 断点续训 ----
RESUME = True         # True = 从 runs/<EXP_NAME>/weights/last.pt 接着练（中断后改这个就行）
SAVE_PERIOD = 0       # >0 = 每 N 轮额外存一个 epochN.pt 作兜底备份；0 = 只留 last/best

# ---- 其他开关 ----
CACHE = "disk"        # "disk"/"ram"/False。因为 WORKERS=0（躲 Windows worker 泄漏），
                      # dataloader 变成串行，mosaic 每个样本要读 4 张 JPEG，
                      # 不缓存时每 batch 光解码就要 ~500 ms，会和 GPU 抢时间。
                      # 用 disk 缓存把图预先解成 npy（占 ~8 GB 磁盘，首次生成约 2 分钟），
                      # 之后靠 memmap 读，单张 2~3 ms。"ram" 更快但本机不够内存。
SEED = 0              # 随机种子，固定后划分可复现
PLOTS = True          # 是否生成混淆矩阵/曲线图

# ---- 训练完是否自动在验证集上再跑一遍 ----
RUN_FINAL_VAL = True

# ============================================================
# 环境准备（必须在 import ultralytics 之前执行）
# ============================================================

# 1) 隔离 ultralytics 配置目录，避免和 CUDA 环境互相改写 settings.json
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))

# 2) 屏蔽用户级 site-packages（历史上被污染过，装着 CPU 版 torch）
os.environ["PYTHONNOUSERSITE"] = "1"

# 3) 去掉 workbuddy 注入的 PYTHONPATH，避免干扰
os.environ.pop("PYTHONPATH", None)

# 4) Windows 控制台中文输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================
# 单实例保护
# ============================================================
# VS Code 里连点两次 ▷ 会起两个训练进程，抢同一张 GPU、写同一个 weights 目录，
# 结果两边都极慢、results.csv 根本写不出来（实测踩过：18 分钟跑不完第 1 轮）。
LOCK_FILE = PROJECT / "runs" / f".{EXP_NAME}.lock"


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否还活着。

    Windows 上不能用 os.kill(pid, 0)：那会直接调 TerminateProcess，
    等于把别的进程杀掉，判断不了存活。
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def release_lock() -> None:
    """只删自己写的锁，别误删别的实例的。"""
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def _arg_value(flag: str, default: str | None = None) -> str | None:
    """从 sys.argv 里取 `--flag value` 的 value；没有就返回 default。

    刻意不用 argparse：本脚本被设计成"直接按 ▷ 运行、不带参数"，
    argparse 遇到未知参数会报错退出，反而挡住 VS Code 的调试参数注入。
    """
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            return sys.argv[i + 1]
    for a in sys.argv:
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


def acquire_lock() -> bool:
    """拿到锁返回 True；已有实例在跑则打印说明并返回 False。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            old = -1
        if old > 0 and old != os.getpid() and _pid_alive(old):
            print("!" * 62)
            print(f"[阻止] 已有训练在跑（PID {old}），本次启动已取消。")
            print("       同一实验目录不能同时跑两个实例，否则会互相覆盖 weights 和 results.csv。")
            print(f"       要强制重开：先结束 PID {old}，再删除 {LOCK_FILE}")
            print("!" * 62)
            return False
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(release_lock)
    return True


# ============================================================
# 环境自检
# ============================================================

EXPECT_ENV_MARK = "yolo-rocm"      # 期望运行在哪个 conda 环境
EXPECT_BACKEND = "rocm"            # 期望的 torch 后端标记
CONDA_ENV_ROOT = Path(r"C:\Users\shang\.conda\envs")
AUTO_REEXEC = True                 # True = 解释器选错时自动切到 yolo-rocm 重跑


def in_expected_env() -> bool:
    """精确判断当前解释器是否属于目标 conda 环境。

    不能只用子串匹配：'yolo' 是 'yolo-rocm' 的子串，
    C:\\...\\envs\\yolo-rocm\\python.exe 会被误判成 yolo 环境。
    conda 环境的 python.exe 一定在 <envs>\\<name>\\python.exe，比较父目录名最准。
    """
    return Path(sys.executable).parent.name.lower() == EXPECT_ENV_MARK.lower()


def reexec_with_expected_env() -> None:
    """VS Code 里很容易选到系统 Python（3.14 那种，没装 torch）。

    这里直接换成 yolo-rocm 的 python 重启自己，避免用户每次手动切解释器。
    子进程继承 stdio，所以终端里看到的输出和直接运行一模一样。
    """
    if not AUTO_REEXEC or in_expected_env():
        return
    target = CONDA_ENV_ROOT / EXPECT_ENV_MARK / "python.exe"
    if not target.exists():
        return  # 没有该环境就交给自检去报错，别在这里静默失败

    print("=" * 62)
    print(f"[切换] 当前解释器 : {sys.executable}")
    print(f"[切换] 不是 {EXPECT_ENV_MARK} 环境，自动改用 : {target}")
    print("=" * 62)
    print()
    # 必须手动刷缓冲：子进程继承同一个 stdout，不刷的话这几行会跑到子进程输出之后
    sys.stdout.flush()
    rc = subprocess.run(
        [str(target), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(PROJECT),
    )
    sys.exit(rc.returncode)


def report_dataset() -> None:
    """把数据集里每个类有多少个框打出来。

    为什么值得单独做：合并数据集之后最容易出的问题不是报错，而是
    「某几类一个框都没有」——训练照样跑完，mAP 照样出数，但模型永远
    不会预测那几类。不打印出来根本发现不了。
    """
    if not Path(DATA).exists():
        return
    try:
        import yaml

        cfg = yaml.safe_load(Path(DATA).read_text(encoding="utf-8")) or {}
        names = cfg.get("names") or {}
        root = Path(cfg.get("path") or Path(DATA).parent)
        lab_dir = root / "labels" / "train"
        counts: dict[int, int] = {}
        n_img = 0
        if lab_dir.is_dir():
            for lab in lab_dir.glob("*.txt"):
                n_img += 1
                for line in lab.read_text().splitlines():
                    if line.strip():
                        counts[int(line.split()[0])] = counts.get(int(line.split()[0]), 0) + 1

        print()
        print("  数据集内容")
        print("  " + "-" * 50)
        print(f"  名称     : {DATASET}")
        print(f"  类别数   : {cfg.get('nc', '?')}")
        print(f"  训练图   : {n_img}")
        total = sum(counts.values()) or 1
        missing = []
        for i in range(len(names)):
            c = counts.get(i, 0)
            nm = names.get(i, names.get(str(i), f"#{i}"))
            if c == 0:
                missing.append(nm)
            print(f"    {i:2d} {str(nm):<12s} {c:7d}  {c / total * 100:5.2f}%")
        if missing:
            print()
            print(f"  [!] 这些类 0 个样本，模型永远不会预测它们：{', '.join(missing)}")
            if "person" in [str(m) for m in missing]:
                print("      person 缺失 = 活体检测不起来，先补活体数据：")
                print("      python tools\\build_living_dataset.py --person <COCO格式目录>")
    except Exception as exc:                      # noqa: BLE001
        print(f"  [dataset] 读取数据集统计失败：{type(exc).__name__}: {exc}")


def check_env() -> list[str]:
    """返回阻断性问题列表；空列表表示可以开训。"""
    problems: list[str] = []

    def report(probs: list[str]) -> list[str]:
        """统一打印问题清单。

        任何提前 return 的分支都必须走这里 —— 之前 torch 导入失败时直接
        return 而没打印，屏幕只剩一句「请先解决上面的问题」，看不到问题是什么。
        """
        print()
        for p in probs:
            print(f"  [×] {p}")
        print()
        return probs

    print("=" * 62)
    print("环境自检")
    print("=" * 62)
    print(f"  解释器   : {sys.executable}")
    print(f"  Python   : {sys.version.split()[0]}")
    print(f"  工作目录 : {PROJECT}")

    if not in_expected_env():
        problems.append(
            f"当前 python 不在 {EXPECT_ENV_MARK} 环境：{sys.executable}"
        )
        problems.append(
            f"正确解释器：{CONDA_ENV_ROOT / EXPECT_ENV_MARK / 'python.exe'}"
        )
        problems.append(
            "VS Code 里切换：Ctrl+Shift+P -> Python: Select Interpreter -> "
            "选 yolo-rocm；或直接双击本目录 run_train.bat"
        )

    try:
        import torch
    except ImportError as exc:
        problems.append(f"torch 导入失败：{exc}")
        return report(problems)

    print(f"  torch    : {torch.__version__}")
    print(f"  hip      : {torch.version.hip}")
    print(f"  cuda 可用: {torch.cuda.is_available()}")

    if EXPECT_BACKEND == "rocm" and not torch.version.hip:
        problems.append(
            "torch 不是 ROCm 版（torch.version.hip 为空），当前是 CPU/CUDA 版"
        )

    if not torch.cuda.is_available():
        problems.append("torch.cuda.is_available() 为 False，GPU 后端没起来")
    else:
        for i in range(torch.cuda.device_count()):
            prop = torch.cuda.get_device_properties(i)
            arch = getattr(prop, "gcnArchName", "N/A")
            print(
                f"  设备[{i}]  : {torch.cuda.get_device_name(i)}  "
                f"({arch}, {prop.total_memory / 1024 ** 3:.2f} GB)"
            )

    # polars：ultralytics 8.4.x 保存权重时会调用，缺了会在训练最后一刻崩
    try:
        import polars

        print(f"  polars   : {polars.__version__}")
    except ImportError:
        problems.append(
            "缺少 polars —— 训练会在保存权重时崩溃。"
            "修复：python -m pip install polars"
        )

    # ultralytics
    try:
        import ultralytics

        print(f"  ultra    : {ultralytics.__version__}")
        print(f"  ultra路径: {ultralytics.__file__}")
    except ImportError as exc:
        problems.append(f"ultralytics 导入失败：{exc}")

    # 路径检查
    if not Path(MODEL).exists():
        problems.append(f"预训练权重不存在：{MODEL}")
    if not Path(DATA).exists():
        problems.append(f"数据集配置不存在：{DATA}")
        problems.append(
            f"       先跑：python tools\\build_living_dataset.py   （生成 datasets/{DATASET}/）"
        )

    if problems:
        return report(problems)
    else:
        print()
        print("  [✓] 全部通过")
        report_dataset()
    print()
    return problems


# ============================================================
# 训练
# ============================================================


def main() -> int:
    # 解释器不在 yolo-rocm 里就自动换正确的 python 重启自己
    # （VS Code 默认常选到系统 Python 3.14，那个环境没有 torch）
    reexec_with_expected_env()

    problems = check_env()
    if problems:
        print("自检未通过，已终止。请先解决上面的问题。")
        return 1

    # 只体检不开训：python train_yolo.py --check
    if "--check" in sys.argv:
        print("仅体检模式（--check），不开始训练。")
        return 0

    # ---- 命令行临时覆盖（不动上面的配置区，保证默认行为不变）----
    #   作用：一次跑多个数据集的对照实验时，不必反复改文件、也就不会
    #   因为忘了改回来把 A 组的结果覆盖掉。
    #     python train_yolo.py --dataset livingB --epochs 15
    global DATASET, DATA, EXP_NAME, EPOCHS
    _ds = _arg_value("--dataset")
    if _ds:
        DATASET = _ds
        DATA = PROJECT / "datasets" / _ds / "data.yaml"
        EXP_NAME = f"{DATASET}_yolo11n"
    _ep = _arg_value("--epochs")
    if _ep:
        EPOCHS = int(_ep)
    _nm = _arg_value("--name")
    if _nm:
        EXP_NAME = _nm
    print(f"[配置] 数据集={DATASET}  轮数={EPOCHS}  实验名={EXP_NAME}")

    # 同一实验目录只允许一个训练进程（防止连点两次 ▷ 起两个实例）
    if not acquire_lock():
        return 1

    from ultralytics import YOLO

    # 断点续训
    # ---- 断点续训 ----
    # 坑：不能写 resume=True。True 时 ultralytics 会走 get_latest_run()，
    # 在工作目录下递归搜最新的 last*.pt；runs/ 里有别的历史实验时可能恢复错，
    # 或者直接报 "no checkpoint found"。传字符串路径才会精确命中本实验的断点
    # （trainer.check_resume 里 exists 分支）。
    resume_ckpt = None
    last_ckpt = PROJECT / "runs" / EXP_NAME / "weights" / "last.pt"
    if RESUME:
        if last_ckpt.exists():
            resume_ckpt = str(last_ckpt)
            mt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_ckpt.stat().st_mtime))
            print(f"[续训] 断点  : {last_ckpt}")
            print(f"[续训] 保存于: {mt}  (中断发生在两轮之间，最多回退 1 轮)")
        else:
            print(f"[续训] RESUME=True 但没找到 {last_ckpt}，改为从头训练")
    elif last_ckpt.exists():
        # 防呆：RESUME=False 但目录里已有断点，直接跑会把 last.pt/best.pt 覆盖掉
        print("!" * 62)
        print(f"[警告] 发现已有断点: {last_ckpt}")
        print("       当前 RESUME=False，从头训练会覆盖掉上面的断点和 best.pt。")
        print("       想接着练 -> 把脚本顶部 RESUME 改成 True")
        print("!" * 62)
        try:
            ans = input("确认从头覆盖? 输入 yes 回车继续: ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("yes", "y"):
            print("已取消，未改动任何文件。")
            return 0

    print("=" * 62)
    print("开始训练")
    print("=" * 62)
    print(f"  权重   : {MODEL}")
    print(f"  数据   : {DATA}")
    print(f"  轮数   : {EPOCHS}   imgsz={IMGSZ}   batch={BATCH}   patience={PATIENCE}")
    print(f"  输出   : {PROJECT / 'runs' / EXP_NAME}")
    print()

    t0 = time.time()
    model = YOLO(resume_ckpt or str(MODEL))

    results = model.train(
        data=str(DATA),
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        patience=PATIENCE,
        workers=WORKERS,
        device=DEVICE,
        amp=AMP,
        cache=CACHE,
        seed=SEED,
        plots=PLOTS,
        save_period=SAVE_PERIOD,
        resume=resume_ckpt if resume_ckpt else False,
        project=str(PROJECT / "runs"),
        name=EXP_NAME,
        exist_ok=True,
        verbose=True,
    )
    elapsed = time.time() - t0

    # 注意：不同 ultralytics 版本 model.train() 的返回值不一样
    #   8.3.x 返回 metrics 对象（没有 save_dir）
    #   8.4.x 返回带 save_dir 的对象
    # 统一从 model.trainer 上取，两个版本都可用
    save_dir = Path(model.trainer.save_dir)
    best = save_dir / "weights" / "best.pt"
    last = save_dir / "weights" / "last.pt"

    print()
    print("=" * 62)
    print("训练结束")
    print("=" * 62)
    print(f"  耗时    : {elapsed / 60:.1f} 分钟")
    print(f"  输出目录: {save_dir}")
    print(f"  best.pt : {best}  {'[存在]' if best.exists() else '[缺失!]'}")
    print(f"  last.pt : {last}  {'[存在]' if last.exists() else '[缺失!]'}")

    summary = {
        "exp_name": EXP_NAME,
        "elapsed_min": round(elapsed / 60, 2),
        "save_dir": str(save_dir),
        "epochs": EPOCHS,
        "imgsz": IMGSZ,
        "batch": BATCH,
        "best": str(best),
        "best_exists": best.exists(),
    }

    # 用 best.pt 在验证集上复核一遍
    if RUN_FINAL_VAL and best.exists():
        print()
        print("=" * 62)
        print("用 best.pt 复核验证集")
        print("=" * 62)
        try:
            m = YOLO(str(best))
            metrics = m.val(data=str(DATA), imgsz=IMGSZ, batch=BATCH, device=DEVICE)
            summary["metrics"] = {
                "precision": round(float(metrics.box.mp), 4),
                "recall": round(float(metrics.box.mr), 4),
                "mAP50": round(float(metrics.box.map50), 4),
                "mAP50-95": round(float(metrics.box.map), 4),
            }
            print()
            print(f"  Precision : {summary['metrics']['precision']}")
            print(f"  Recall    : {summary['metrics']['recall']}")
            print(f"  mAP@50    : {summary['metrics']['mAP50']}")
            print(f"  mAP@50-95 : {summary['metrics']['mAP50-95']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [警告] 复核失败：{type(exc).__name__}: {exc}")

    # 摘要落盘
    try:
        out = save_dir / "train_summary.json"
        out.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print()
        print(f"  摘要已保存：{out}")
    except Exception:
        pass

    print()
    print("=" * 62)
    print("下一步：用训练好的权重做推理")
    print("=" * 62)
    print(f'  from ultralytics import YOLO')
    print(f'  model = YOLO(r"{best}")')
    print(f'  model.predict(source=r"某张图.jpg", save=True)')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
