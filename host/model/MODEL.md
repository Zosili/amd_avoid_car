# 模型说明（来源 / 精度 / 后端 / 导出）

> 对应 3.2.5.5 `host/model/`：模型文件或**下载脚本** + `MODEL.md`。
> **模型不进 git**（`.gitignore` 已忽略 `*.pt/*.onnx/*.engine`），用下面命令现取现导。

## 1. 用的是哪些模型

| 模型 | 用途 | 来源 | 输入 | 文件体积（实测） | 精度 |
|---|---|---|---|---|---|
| `yolo11n.pt` | 训练/导出源（Ultralytics 官方权重） | Ultralytics | — | 5.6 MB | 官方指标，本任务**未评测** |
| `yolo11n.onnx` | FP32 ONNX，PC/NPU 推理 | 由 `.pt` 导出 | 1×3×H×W | 10.7 MB | 同上 |
| `yolo11n_int8.onnx` | INT8 量化，NPU 推理 | `quantize_yolo.py` 量化 | 1×3×H×W | 3.1 MB | 待测（量化后需重评 mAP） |
| `yolov8n.pt` / `.onnx` / `_int8.onnx` | 上一代对照基线 | Ultralytics | — | 6.5 / 12.8 / 3.5 MB | 待测 |
| 官方 quicktest 模型 | NPU 通路自检 | Ryzen AI SDK | 1×3×32×32 | 小 | 只验证「NPU 能跑通」，不作效果 |

> 体积是导出现场的真实文件大小；**精度一律写「待测」**，没跑过验证集就不许填 mAP（见 `metrics/README.md` §1）。

## 2. 怎么取模型 / 重新导出

```bash
# ① 取官方权重（需要 ultralytics）
python -m pip install ultralytics
python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"

# ② 导出 ONNX（640×480 链路；脚本在 host/app/vision_pc/）
python host/app/vision_pc/export_yolov8.py --weights yolo11n.pt --imgsz 640

# ③ INT8 量化（产物 *_int8.onnx）
python host/app/vision_pc/quantize_yolo.py --onnx yolo11n.onnx
```

导出的文件放本目录（`host/model/`）即可，`git status` 不会看到它们。

## 3. 推理后端（两套，状态不同）

| 后端 | 配置 | 状态 | 说明 |
|---|---|---|---|
| **Vitis AI EP（AMD NPU）** | Ryzen AI 1.6.0；`onnxruntime 1.23.0`；`provider_options` 传 `cache_dir`/`cache_key`，PHX/HPT 还需 `xclbin`（`phoenix/4x4.xclbin`） | ✅ 单机跑通 | 入口 `host/app/vision_pc/ryzenai/main_ryzenai.py`（`npu_infer.py` 封装 `NpuInfer`） |
| **ROCm EP / MIGraphX** | ROCm ≥ 7.14.0（指南 3.2.3.1 硬要求） | ⚠️ 未落地 | 必须在提交前补：装 ROCm、跑 `rocminfo`、记录版本，模型走 ROCm EP |

**这是当前最大的合规缺口**：指南要求「AI PC 侧软件栈须使用 ROCm 7.14.0 及以上版本，并在提交物中声明实际使用的版本」。Vitis AI EP 走的是 XDNA/NPU 通路，不等于 ROCm 栈。补齐后把版本、模型、延迟写进 `metrics/metrics.csv` 与 `docs/hardware_setup.md`。

## 4. 推理链路现状（别把两条链路搞混）

```
A. 视觉采集链路（已跑通）：OV5640 ──UDP 1234──► PC 收图 ──► NPU 推理 ──► 窗口显示
                                                      └──► 结果回发帧 a5 0f f0 5a（PL 侧尚无解析模块）

B. 决策输入链路（契约要求）：PC ──TCP 5000 行 JSON──► 板上 board_main.py（Vision JSON，≥7 Hz）
```
现状：**A 与 B 还没接起来**。要把 A 的输出按 `contract.detections_to_vision()` 转成 Vision JSON，
以 **≥7 Hz** 发到板的 5000 端口（`integration/vision_sender.py` 是参考实现，但它是 UDP 老版本，需改成 TCP）。

## 5. 预处理口径（必须与训练一致）

```
640×480 BGR（RGB565 解码后）→ resize 到模型输入 → BGR→RGB → /255 归一化
→ HWC→CHW → 加 batch 维 (1,3,H,W) float32
```
若换模型输入尺寸或需要 ImageNet 均值方差，改 `ryzenai/main_ryzenai.py::preprocess()`。
**检测框回传时必须是归一化 0–1**（`cx/cy/w/h`），不是像素——见 [`docs/interface.md`](../../docs/interface.md) §3。

## 6. 已知坑

1. **RGB565 小端**：不交换字节会得到花屏/绿屏（`((x & 0xFF) << 8) | (x >> 8)`）。
2. **NPU 首次推理慢**：要编译并写 `~/.ryzenai_cache`，第二次起才快；报延迟必须报**稳态**并说明是否含首次。
3. **INT8 量化需要校准集**：用错校准图会让小目标（远处行人）掉点，量化后必须重测。
4. **别每帧都推理**：26 fps 全推会堵住收图线程；收图与推理分线程，推理按需节流——但**节流下限是 7 Hz**（对应 `vision_timeout_ms=300`）。
