# 模型说明

## 文件

| 文件 | 说明 |
|---|---|
| `best.onnx` | YOLO11n，19 类，输入 1×3×640×640（RGB，0~1 归一化），输出 `[1, 23, 8400]` |
| `best.onnx` md5 | `20287a00baf9…`（完整值见 `metrics/metrics.csv` 对应记录） |

`best.onnx` 与 `host/app/detect_fusion_live.py` 里的后处理是配套的：输出 23 = 4（xywh）+ 19（类别分数），无 objectness 分支。

## 类别

0~17 为常见室内物品，**18 为 `person`**。项目只关心 person，其余类别保留作干扰项训练，能压低"把挂着的衣服/椅子当人"的误报。

## 指标（训练记录见 `metrics/logs/train_40ep_results.csv`）

| 指标 | 数值 |
|---|---|
| 训练轮数 | 40 |
| mAP50（19 类平均） | 0.8812 |
| mAP50-95 | 0.7435 |
| Precision | 0.8594 |
| Recall | 0.8150 |
| person 类 mAP50 | 0.857 |
| 训练耗时 | 3646.84 s ≈ 1 h 01 m |

对应 `metrics/metrics.csv` 的 M01~M07。

## 为什么是 40 轮而不是更多

实测"加轮数对看人无效"：15 轮 → 50 轮，person 类 mAP50 只涨 0.004、召回反而降 0.027，实图逐张比对 24 张里都是 19 张检出，**完全打平**。瓶颈是 person 样本量（275 图 / 502 框，占 9.6 %），不是训练轮数。要提升只能补数据。

## 换模型要注意

1. 类别顺序必须与 `rtl` 侧的语义表一致（person 恒为第 18 类）；
2. 输入尺寸改了要同步 `detect_fusion_live.py` 里的 `letterbox(..., 640, 32)` 和后处理；
3. 导出 ONNX 时用 opset 12、`dynamic=False`，DirectML 兼容性最好；
4. 换模型后先跑 `python host/app/smoke_onnx.py` 确认能加载、输出 shape 对得上。

## 训练用的数据集约定（供复现）

- 19 类，类别表唯一事实源是 `training/tools/living_semantics.py`，**只能往后追加**；
- 剔除"18 类原数据集里有人但没有 person 标签"的图（否则等于教模型"人=背景"）；
- 预标注可用 COCO 权重，但必须做**静止物过滤**（时间中值背景差），否则会把挂着的衣服学成人。
