# training/ —— 数据与训练管线

> 这里放的是**模型是怎么来的**：从手机实拍到 `best.onnx` 的全过程。
> 上板与推理代码在 `host/`，FPGA 在 `fpga/`，不在本目录。

## 0 为什么单独放

`host/model/best.onnx` 只有 10 MB，但它背后是一条完整的自制数据管线：
**低视角**（车高 20 cm）拍摄、**活体/非活体**区分、**19 类**语义、少量 person 样本的精细标注。
这部分代码和推理代码没有运行依赖关系，分开放更好读。

## 1 目录

```
training/
├── train_yolo.py        训练入口（配置全在文件顶部，无命令行参数）
├── data_living.yaml     19 类数据集配置（path 需按本机改）
├── tools/               43 个脚本：采集 / 清洗 / 标注 / 审计 / 评测
└── reports/             三份训练与选型分析报告
```

## 2 完整管线（按顺序）

| 阶段 | 脚本 | 做什么 |
|---|---|---|
| ① 采集 | `import_phone.py`、`snap_frames.py` | 手机视频/照片 → YOLO 数据集（去重、4:3 裁切、清左下角黑三角） |
| ② 找有用片段 | `scan_motion.py`、`scan_long_video.py`、`scan_person.py` | 长视频里只有人有用的那几秒，别拿几十小时喂模型 |
| ③ 清洗 | `clean_corner.py`、`filter_person.py` | 修固定机位伪影（黑三角占 3.09%）；从抽帧里挑出含人的 |
| ④ 预标注 | `autolabel.py` | 用 COCO 权重先画粗框，把「画框」变成「删框」 |
| ⑤ **活体过滤** | `motion_verify.py`、`motion_gate.py` | 时间中值背景差区分「真人在动」和「挂着的衣服」 |
| ⑥ 人工复核 | `review_labels.py`、`audit_precision.py`、`verify_keep.py`、`finalize_labels.py` | 抽样渲染裁剪图，逐框确认 |
| ⑦ 补 person | `person_relabel.py`、`import_18cls` 流程 | 给原 18 类数据补 person 标注 |
| ⑧ 增强 | `lowangle_aug.py` | 把「全身 person」合成成「只有下半身」，补低视角视角分布 |
| ⑨ 建数据集 | `build_living_dataset.py` + `living_semantics.py` | 合并成 19 类，**类别顺序的唯一事实源是 `living_semantics.py`** |
| ⑩ 训练 | `train_yolo.py` + `data_living.yaml` | YOLO11n，imgsz 640 |
| ⑪ 导出 | `export_onnx.py`、`check_onnx.py` | 导 ONNX；顺带查算子能不能上 NPU |
| ⑫ 评测 | `eval_report.py`、`detect_compare.py`、`video_check.py`、`cmp_boxes.py` | mAP、并排检出对比、实拍视频对比 |
| ⑬ 后端选型 | `bench_deploy.py`、`bench_dml.py`、`bench_npu.py`、`check_dml_num.py`、`gpu_check.py`、`dxgi_adapters.py` | 别在「以为在用核显、实际在用独显」的情况下出数据 |

`tools/` 里另有十来个**一次性诊断脚本**（`_conv_raw.py`、`_test_relabel.py`、`inspect_ids.py`、
`read_guide_pdf.py` 等），是排查过程中的临时工具，留着只为可追溯，不属于主流程。

## 3 环境

| 用途 | 环境 |
|---|---|
| 训练 | Python 3.12 + `torch 2.10+rocm`（ROCm 7.14 / Radeon 780M），见 `reports/living_detect.md` |
| 推理/导出 | `onnxruntime`（DirectML 后端跑 780M） |
| 依赖 | `ultralytics`、`opencv-python`、`numpy`、`pyyaml`、`pyserial`（雷达相关） |

> **CUDA 与 ROCm 两套环境必须隔离**。本项目训练走 ROCm，遇到速度问题时的实测结论在
> `reports/living_detect.md` 第 5 节（GPU 算力是唯一瓶颈，加 batch、加 workers、改分辨率都无效）。

## 4 复现

```bash
# 1) 准备数据（数据集本身不随代码提交，几万张图太大）
python tools/build_living_dataset.py --help        # 看它要哪些输入目录
#    产出的目录结构： living/images/{train,val}  living/labels/{train,val}

# 2) 改 data_living.yaml 的 path 指向你的 living 目录

# 3) 训练
python train_yolo.py                                # 参数在文件顶部配置区

# 4) 导出 + 检查
python tools/export_onnx.py
python tools/check_onnx.py export/xxx.onnx
```

## 5 没包含什么

| 没包含 | 原因 |
|---|---|
| 数据集原图与标签 | 几万张，体积远超提交上限；重建脚本在 `tools/` |
| 训练权重 `*.pt` | `best.onnx` 已随 `host/model/` 提交，`best.pt` 是中间产物 |
| `runs/` 训练输出 | 每次训练都会重生成（曲线、混淆矩阵在 `metrics/logs/` 有副本） |
| `vendor/`（107 MB） | 依赖的第三方运行时，用 pip 装更快 |

## 6 一个值得写进报告的结论

person 类只有 **275 张图 / 502 个框（占 9.6%）**，这是 mAP 上不去的真正瓶颈：

- 15 轮 → 50 轮，person 的 mAP50 只涨 **0.004**，召回反而掉 0.027；
- 实图检出 19/24 对 19/24，**完全打平**；
- 18 类物品在同样加轮数下有 17 涨 1 跌。

所以「加训练轮数」对看人无效，只能补数据。详证见 `reports/living_50ep_report.md`。
