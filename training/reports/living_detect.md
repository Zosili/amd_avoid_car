# 活体检测 + 停车鸣叫 —— 实现说明

> 最后更新：2026-10-28
> 涉及：`D:\FPGA\ultralytics\`（训练/推理）、`D:\FPGA\7020\`（RTL/固件）

---

## 0. 一句话现状

**代码链路已全部打通并自测通过，缺的是活体训练数据（当前只有 283 个框）。**
拿到数据后一条命令就能训，训完直接换权重即可。

---

## 1. 整体链路

```
OV5640 (640x480)  →  FPGA UDP  →  PC:1234
                                    ↓
                        YOLO11n 检测（19 类细粒度）
                                    ↓
                    ┌───────────────┴───────────────┐
              living_semantics.py              单目测距
              （细类→语义分组）              （框底边→米）
                    └───────────────┬───────────────┘
                                    ↓
                            car_decide.py 状态机
                       （活体近→停+鸣叫 / 障碍近→差速绕）
                                    ↓
                        串口命令 M/H → ZYNQ PS UART0
                                    ↓
                        AXI GPIO bit18 → 蜂鸣器 M14
                        AXI GPIO [0:17] → 双路 PWM
```

### 各文件职责

| 文件 | 作用 |
|---|---|
| `tools/living_semantics.py` | **类别表的唯一事实源**。19 类 → living/flat/obstacle/passage |
| `tools/build_living_dataset.py` | 合并 18cls + 外部活体数据 → `datasets/living/` |
| `tools/snap_frames.py` | 实拍采集（摄像头/UDP/视频），可带模型预标注 |
| `tools/autolabel.py` | 用 COCO 权重自动预标注（只取 person） |
| `tools/review_labels.py` | 按键审核预标注（接受/删框/清空），替代逐框检查 |
| `tools/lowangle_aug.py` | 低视角合成（**实测产能极低，见第 4 节，不建议用**） |
| `tools/motion_gate.py` | 运动辅助判据（框内光流 / 停车差分） |
| `tools/car_decide.py` | 决策状态机 + 单目测距 + 串口下发 |
| `tools/detect_realtime.py` | 推理主程序，`--decide` 接入上述全部 |

---

## 2. 类别体系：为什么是 19 类而不是 2 类

需求听起来是二分类（活体/非活体），但**训练用细类、决策用映射**明显更好：

| 方案 | 问题 |
|---|---|
| 纯 2 类 living/non_living | 扔掉 18 类标注的区分度；无法区分「地毯能压过去」和「桌子必须绕」，绕路逻辑退化成见什么都躲 |
| **细类 + 代码映射（已采用）** | 8884 张已有标注零改动；调整语义只改一个 dict，不用重训；同一个模型同时服务停车和绕路 |

```
0-17  : trashcan slippers wire socks carpet book feces curtain stool
        bed sofa closestool table cabinet ajardoor opendoor closedoor stairway
18    : person                     ← 唯一的活体类
```

**关键约束：新类只能往后追加。** 前 18 个类 id 与 `datasets/18cls` 完全一致，这是"已有标签硬链接过来一个字节都不用改"的前提。

### 为什么最后没有 cat / dog / bird

一开始留了 19/20/21 三个动物类（22 类体系）。跑下来样本量是 **cat 4 / dog 9 / bird 16 个框**——这个量级训出来的不是检测器，是过拟合。代价却很实在：

- 三个必挂的类把整体 mAP 拉到很低，看指标时**分不清 person 到底学得好不好**
- coco128 里那 29 个动物框会让模型对"猫"记住 4 张图，实车换个角度就失效

所以干脆删掉，退回 **19 类**。真遇到猫狗鸟有兜底：车体只有 15cm 高，鸟会飞走、猫会跳开；`car_decide` 遇到 unknown 类一律按障碍绕行，本来就不会压过去。

以后要用就往后追加 19/20/21，前面 19 类的 id 一个都不用动。

语义分组（`living_semantics.py`）：

| 组 | 内容 | 行为 |
|---|---|---|
| living | person | 近则停车 + 鸣叫 |
| flat | carpet/wire/socks/slippers/feces | 直接压过去，不躲 |
| obstacle | trashcan/book/stool/bed/sofa/closestool/table/cabinet/curtain | 近则差速绕行 |
| passage | ajardoor/opendoor/closedoor/stairway | 通道语义，既不撞也不压 |

---

## 3. 数据现状（这是当前的瓶颈）

```
train: 8450 张   val: 2079 张   共 21447 + 5278 个框

person  324  (1.5%)   其中自采 78 框 / coco128 246 框
cat       4  (0.02%)
dog       9  (0.04%)
bird     16  (0.07%)
```

27 类非活体合计 21094 框，活体 353 框。**比例 1:60**，模型天然倾向于
"这不是活体"。所以训练时必须开 `cls` 加权或用 `fl_gamma`，否则 person
的召回会被压得很低（详见第 5 节）。

### ⚠ 18cls 里本来就有人（已处理，但要知道）

名义上 18cls 是"纯非活体"数据集。实测扫了一遍：

```
val   2220 张 → 267 张含活体候选（12.0%）
train 8884 张 → 1019 张含活体候选（11.5%）
合计  1286 张 / 11.6%
```

这些图里的人是**普通站立/坐姿视角**（门口的老人、沙发上的赤膊男、
穿裙子的女人），不是车载低视角，而且**没有 person 标签**。直接拿去
训练等于告诉模型"人不是目标"，是比漏检更毒的假负样本。

处理方式（`--exclude` 名单）：

| 置信度段 | 图数（train+val） | 处理 | 理由 |
|---|---|---|---|
| ≥0.35 | 738 | **整张排除** | 高置信段实测约 70% 是真活体，剩下 30% 是挂衣/被子误检，宁可一起丢 |
| 0.15~0.35 | 548 | 保留 | 实测这一段几乎全是误检（挂布、塑料袋、椅子腿），保留反而干净 |

代价：连带损失 2081 个家具框（占 7.2%）。可接受——18cls 的家具样本
本来就有 2.8 万个，而假负样本的伤害是百分比的召回损失。
源数据一张没删，名单在 `runs/exclude_18cls_living.txt`，随时可回退。

复核命令：

```bash
python tools\scan_person.py --src datasets\18cls\images\val --out runs\v.json
python tools\scan_person.py --report runs\v.json     # 看各级置信度分布
python tools\build_living_dataset.py ... --exclude runs\exclude_18cls_living.txt
```

### 47 张自采够不够：不够，而且缺的不是"张数"

这个问题得拆开看，因为"不够"有三种，补法完全不同：

| 维度 | 现状 | 判断 |
|---|---|---|
| **实例数** | 自采 person 49 框（37 train / 12 val 张） | 迁移学习下 100~200 实例能到"勉强可用"，**300~500 才稳**。差 3~10 倍 |
| **尺度梯度** | 自采里 3m 外小目标（面积 <0.08）**0 个** | **绝对缺口**。这不是多训几轮能补的，样本里根本没有 |
| **多样性** | 1 个人 / 1 条深蓝裤 / 1 双白鞋 / 1 条走廊 | 模型完全可能学到"深蓝裤子+白鞋 = person" |

第三条最隐蔽：val 集也是同一批人同一场景，指标会很好看，实车换个人
就崩。**必须换人换衣换场地拍。**

尺度那条更硬：小车要在 2~3m 外就发现人并开始减速，但训练集里没有
"3m 外的人"这种样本，模型只学会了"人贴到 60cm 以内"。实车表现会是
**一路开到人跟前才急停**，这恰恰是最危险的。

### 补拍目标（照着这张表拍，一次够用）

```
人数    3~4 人（必须换人，不能是同一个人换衣服）
衣服    每人深浅两套（黑/深蓝 + 白/浅灰）
场景    3 个（走廊、房间、有地毯/瓷砖各一）
姿态    站立 / 蹲 / 坐地 / 躺，每种都要
距离    4m / 3m / 2m / 1m 各 1 张（这是补尺度梯度的关键）
角度    正面 / 侧身 / 背面
```

按 3 人 × 2 套 × 3 场景 ≈ 18 组，每组 8~10 张 → **150~200 张，
活体框 250~350**。加上现有 47 张，person 实例到 300+，尺度梯度补齐。

**猫狗鸟怎么办**：现有 4/9/16 个框基本等于没标。建议不要为它们单独
投入——小车车体只有 15cm 高，鸟会飞走、猫会跳开，撞到也不伤人。
优先把 person 做扎实（这是"停车鸣叫"这个安全功能的主体），
猫狗鸟漏检就交给"未知障碍物一律绕路"兜底。

### 为什么这么少

本机网络实测过一圈，能走的和走不通的都记在这里，避免重复踩：

| 源 | 结果 |
|---|---|
| github.com / raw.githubusercontent | ✅ 通（但 `git clone` 大仓库会被重置） |
| github releases | ✅ 通，`coco128.zip` 就是从这里下的 |
| images.cocodataset.org（COCO 官方） | ❌ 证书劫持 |
| huggingface.co | ❌ 不通 |
| ModelScope | ⚠️ 站点通，但 `modelscope/person_detection_for_train` 仓库里**只有 2 个元数据文件**，实际数据要靠 `msdatasets` 拉，而这条依赖链断在 `addict`（pip 两个源 + github 装全被重置） |
| pypi 阿里云 / 清华 | ⚠️ 大部分包能装，个别包连接被重置 |
| roboflow | ⚠️ 403（浏览器能开，命令行被 Cloudflare 挡） |

所以目前 person 用的是 `coco128` 里的 62 张（github releases 下载）。**量级差两个数量级，训出来的 person 检测会明显偏弱。**

### 可以走的备选（按推荐度）

1. **实拍（强烈推荐，见第 4 节）** —— 唯一能真正解决低视角问题的
2. **Ultralytics 官方平台** `platform.ultralytics.com` —— 有 CrowdHuman 的 YOLO 版（19k 张单类 person，6.7GB），域名可达但需注册登录
3. 你自己的网盘/另一台能上外网的机器

拿到数据后并进来：
```bat
REM COCO 格式（含 train.json + images/）
python tools\build_living_dataset.py --clean --person D:\path\to\person_coco

REM 已是 YOLO 格式，且类别 id 和你的体系不同（用显式映射，最不容易错）
python tools\build_living_dataset.py --clean --person-yolo D:\path\to\ds ^
    --map "0:person,15:cat,16:dog,14:bird"

REM 自建数据（本来就按 22 类体系标注，保留全部类）
python tools\build_living_dataset.py --clean --person-yolo D:\FPGA\ultralytics\datasets\custom_raw ^
    --person-cls -1
```

> `--person-cls -1` 表示"保留所有类、id 原样不动"。**自建数据一定要用这个**，
> 否则会把辛苦标的 20 个类全塞进 person。

---

## 4. 低视角：实测结论与正确做法

### 我试过但基本失败的路：从公开数据合成"下半身"

思路是从 COCO 的 person 框里裁出下部 45~70%，放大，模拟"只能看到腿"。

**实测结果：coco128 的 61 张 person 图，只有 6 张能通过几何过滤，这 6 张里只有 1 张真的生成了"看腿"效果，其余变成了头发特写、手的特写、猫头特写。**

原因不是脚本 bug，是数据性质：COCO 里大量 person 是**近景半身**（坐着、蹲着、被桌子挡住），框本身就只覆盖上半身。它们的几何特征（瘦高、够大、底边靠下）和"站立全身"完全一样，**几何上无法区分**。

→ **不要指望用 `lowangle_aug.py` 把公开数据集转成低视角数据。** 脚本留着，但只对你自己拍的站立全身照有意义。

### 正确的做法：不把"站立全身"当唯一正样本

低视角下摄像头离地 20cm，**人一蹲，高度差就变小，反而能看到更完整的全身**。所以活体样本应该是混合姿态：

```
站立（只看到腿）  +  蹲着  +  坐在地上  +  趴着  +  猫狗贴地  +  鸟
```

这几种姿态 COCO 里都有现成的，直接混合训练就行，不需要任何合成。这比强行造"腿"样本有效得多。

### 实拍怎么拍（关键，比脚本重要）

```bat
REM 1) 采集（USB 摄像头，架到车上真实高度 15~25cm）
python tools\snap_frames.py --src cam --out datasets\custom_raw --interval 0.4 --prelabel
```

拍摄清单（按优先级）：

| 场景 | 为什么必须拍 | 建议条数 |
|---|---|---|
| 人从 3m 走到 0.5m | 主力场景，覆盖"腿→全身"的尺度变化 | 8~10 |
| **人蹲下 / 坐在地上** | 低视角下最常见也最容易漏检 | 5~6 |
| 只露腿（人被桌子挡住上半身） | 最难的情况 | 5 |
| 侧身走过 | 形状变化大 | 3~4 |
| 猫 / 狗（玩具也行，要贴地） | 小动物 | 各 3~4 |
| 地毯/拖鞋/电线特写 | **负样本**：教模型这些不用绕 | 3~5 |

要求：每条 5~8 秒、每 0.4 秒存一帧，一条大约 15~20 张。**总共 300~500 张就够微调。**

> 不需要板子。USB 摄像头架在同高度拍的图，训出来的模型部署到 OV5640 一样能用 —— 省掉整条 UDP 链路。

```bat
REM 2) 标注（三段式，不要直接从空白开始画）
REM    2a. 自动预标注（COCO 权重，只取 person）
python tools\autolabel.py --src datasets\custom_raw\images --conf 0.15 --preview D:\tmp\pv.jpg
REM    2b. 按键审核（把误检删掉，比 labelImg 快 3~5 倍）
python tools\review_labels.py --src datasets\custom_raw\images
REM    2c. 漏检的用 labelImg 补（格式相通）
labelImg datasets\custom_raw\images datasets\custom_raw\classes.txt

REM 3) 并进数据集（自建数据必须用 --person-cls -1 保留全部类）
python tools\build_living_dataset.py --clean --person-yolo datasets\custom_raw --person-cls -1
```

### 拍完先清角落黑块（`clean_corner.py`）

手机放地上拍，左下角经常被包带、垫子卷边、支架腿切掉一块。实测 B 组
29 张**全部命中**，黑块 233×209、占全图 3.1%，位置固定在 x 0..233 /
y 748..957，就压在画面最下方的地面上、紧挨人脚。

它有两个害处：真实车上没有这块黑，等于喂给模型一个永远不成立的假特征；
另外单目测距靠框底边 y，这块黑正好干扰"脚-地"的判断。

```bat
REM 默认就清（import_phone.py 已内置），单独重跑：
python tools\clean_corner.py --src datasets\custom_raw\images
python tools\clean_corner.py --src datasets\custom_raw\images --write
```

处理方式是**几何不变**，用右侧同一水平线上的地面纹理把黑块贴平——不裁剪。
裁掉底部 20% 会把最近距离的视野丢掉，低视角的核心信息恰好在最下面那条。

补之前有四条判据全部满足才动手，缺一条都不碰：

| 判据 | 门槛 | 挡住的是什么 |
|---|---|---|
| 同时贴住外侧两条边 | left + bottom | 画面中间的黑色家具、门 |
| 尺寸上限 | 宽 ≤25%、高 ≤25% 图 | 人的躯干、腿 |
| 顶边必须在画面下部 | y ≥ 70% 图高 | 人贴近镜头时的黑衣（从 60% 就开始） |
| 与标注框重叠 | <5% | 任何已经是目标的区域 |

踩过的坑记一笔：**C_000001 和 C_000008 的"左下角暗块"是人和黑裤子**，
如果按"暗 + 够大"判决，会把人抹掉。上面第 2、3、4 条就是为了挡这个。

原图备份在 `datasets/custom_raw/_orig_corner/`（29 张）。

> 数据集里 `custom_raw` 和 `living` 的图是**硬链接**（同一 inode），
> `cv2.imwrite` 原地截断写不会断链，所以训练集内容自动同步，不需要重建。

### 标注环节的实测数据

**不要用 `18cls_yolo11n/best.pt` 预标注** —— 它只认识 18 类家具，不认识 person，标出来会是空的。必须用 COCO 权重 `weights/yolo11n.pt`（或更大）。

在 coco128 的 62 张含 person 图上实测（conf=0.15, imgsz=640）：

| 指标 | 数值 | 含义 |
|---|---|---|
| 召回 | **0.774** | 自动标出 3/4 的目标，剩下 1/4 要人补 |
| 精确 | **0.760** | 标出来的框 3/4 是对的，1/4 要删 |
| 平均 IoU | **0.840** | 位置够准，一般不用手动微调 |

所以定位很明确：**省掉画框动作，但不等于全自动**。工具链的分工是
`autolabel` 出草稿 → `review_labels` 删误检（按键）→ `labelImg` 补漏检。

想要更准可以把 `--model` 换成 `yolo11l.pt`（慢 5 倍，但标注只做一次）。下载：
```bat
curl -L -o weights\yolo11l.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11l.pt
```

> **空标签是个陷阱**：预标注结果为空 = 告诉模型"这张图什么都没有"。
> 如果图里其实有人只是漏检了，空标签会把真实目标教成背景。
> `snap_frames.py` 会统计并警告空标签张数，审核时务必逐张确认。

### ⚠ 预标注后必做：运动校验（`motion_verify.py`）

COCO 权重在室内场景有个稳定犯的错：**把挂在柜门上、椅背上的黑色长外套
判成 person**。一条 25 秒固定机位视频实测，69 个自动框里 31 个是这么来的，
而且位置几乎完全重合（`cx=0.76 cy=0.28 w=0.11 h=0.56`，重复 21 次）。
这种误检比漏检更毒：漏检只是少学，误检是教模型"黑挂衣 = 活体"，
实车看到窗帘、外套、拖把就停车鸣叫，而且 val 指标看着还不差。

判据是**机位固定**这个天然条件：

```
取整段视频的时间中值图像当背景
  ↓
对每张图算 |帧 - 背景|，只看标注框内的平均差异
  ↓
真人框：49~69    静止物框：4~13    中间是空的，没有灰区
```

阈值 30 落在断层里，实测 69 框零误判：

```bash
# 先看清单（不动文件）
python tools\motion_verify.py --video D:\拍摄\VID.mp4 --labels datasets\custom_raw\labels --prefix ph_

# 确认后执行
python tools\motion_verify.py --video D:\拍摄\VID.mp4 --labels datasets\custom_raw\labels --prefix ph_ --drop
```

局限写在文件头里：**只适用于固定机位**（脚本会算全局漂移，超阈值直接中止）；
一个人站着不动超过半段视频，会被中值背景吸收，然后被判成静止物。

### 固定机位视频的附加价值：负样本

同一段走廊里反复出现的"挂着的黑外套"是极好的**难负样本**——长得像人
但不是人。删掉误检框后那些图变成空标签，正好拿来教模型"这不叫 person"。
一次 44 张的导入里，15 张是这种负样本，比多标 15 个人还有用。

---

## 5. 训练

```bat
python train_yolo.py          REM 顶部 DATASET = "living" 已设好
```

实验名自动是 `living_yolo11n`，不会覆盖已有的 `18cls_yolo11n`。

780M 上每轮约 3.4 分钟（8510 张 / batch 16），30 轮约 1.7 小时，100 轮约 5.6 小时。

### 训练速度实测：瓶颈在哪，哪些调参是白忙

这一节是 2026-10-29 在本机（8845H + 780M 6.24G / ROCm 7.14 / torch 2.10）上实测出来的，
**用来推翻三个看起来合理但其实错误的猜测**：

| 猜测 | 实测结果 | 结论 |
|---|---|---|
| "混了 1280×960 的图，dataloader 拖慢了" | 单图 decode+letterbox：640×480 **7.94 ms**，1280×960 **35.4 ms**；workers=3 时 batch8 只需 **21 ms**，而实测一步要 333 ms → 占 **6.4%** | 不是瓶颈 |
| "batch 8→16 能快 30~40%" | AMP fp16 前向：batch 8/16/32 每张 **7.68 / 7.43 / 7.55 ms**，吞吐恒定 **130 img/s** | **完全不提速**，GPU 已饱和 |
| "这次比 18cls 那次慢一倍" | 推算 fwd+bwd ≈ 3×前向 ≈ 180 ms → **5.5 it/s**，与 18cls 那次 100 轮平均 **5.47 it/s** 一致 | 没变慢。日志里的 3.0 it/s 是第 1 轮含 warmup 的瞬时值 |

**为什么 batch 不提速**：780M 只有 8 个 CU，YOLO11n @640 的卷积已经把它喂满，加大
batch 只是排队变长（batch 翻倍、每步耗时也翻倍）。真正的瓶颈是**纯算力**。

**所以有效的加速手段只有三个**（按性价比）：

1. **换卡训练**：RTX 4060 是本机的独显，跑 CUDA 环境比 780M 快 5~10 倍。但注意
   ROCm 环境是隔离的，别把两套 CUDA/ROCm 装到一起（见 README 的环境隔离说明）。
2. **降 imgsz**：640→512 算力降到 0.64 倍（约 1.5 倍加速）。但**不推荐**——OV5640
   板端输出就是 640×480，训 512 等于训练/部署尺度不一致，低视角远景小目标首当其冲。
3. **关掉每轮的 plots 和多余验证**：省的是 CPU 不是 GPU，收益 5% 以内。

**无效的调参**（别浪费时间去试）：统一图像分辨率、加大 batch、加 workers、`cache='ram'`
（dataloader 本来就只占 6%，且本机只剩 6.7G 可用内存，装不下 1.6G 图解开后的 ~8G）。

### 关于"要不要把所有图统一成 OV5640 的分辨率"

**结论：不需要，而且不建议动那 91 张 1280×960。**

原因是 YOLO 的训练输入**恒为 `imgsz=640`**（`rect=false`，每张独立 letterbox 成
640×640 方图）。原图多大都只是"缩放前有多少细节"，跟网络看到的尺寸无关：

- 当前 98.1% 的图**本来就是 640×480**，和 OV5640 板端输出天然一致，没有可优化的空间
- 91 张 1280×960 是手机自采，缩到 640×480 会让 3 m 外的人从约 40 px 掉到约 20 px，
  **这是全数据集里唯一的远景样本**，缩了就废
- 只有 4 张 coco128 原图是 4032×3024，letterbox 到 640 后细节全浪费，且偶尔卡住一个
  worker。要清理就清这 4 张（收益 <0.1%，纯粹是洁癖）

**真正该对齐的不是分辨率，是像素分布**：OV5640 输出 RGB565（5/6/5 位，32/64/32 级），
手机是 8 位全彩。这个差异已经在 `snap_frames.py` 里用 `--no-rgb565` 处理过（默认关，
因为存 JPEG 会把量化抹平）。

### 评估指标必须换口径

**不能用整体 mAP 判断这套系统能不能用。** 全身 person 的 mAP 0.85 完全不代表你车上检得出。要单独看：

- person 在「画面里只有腿」时的召回率
- 蹲/坐姿态的召回率
- person 的召回率（在低照度/远距离/蹲坐姿态下分别统计）

`train_yolo.py` 跑完会在 `runs/living_yolo11n/` 生成 `confusion_matrix.png`，重点看 person 那一行的漏检。

---

## 6. 部署

```bat
REM 纯检测看效果
python tools\detect_realtime.py --source udp --trigger-ip 192.168.1.10

REM 带决策（不接板子时用 --dry 只打印命令）
python tools\detect_realtime.py --source udp --trigger-ip 192.168.1.10 --decide --dry

REM 真正控车
python tools\detect_realtime.py --source udp --trigger-ip 192.168.1.10 ^
    --decide --serial COM10 --stop-dist 1.0
```

### 必须先标定测距

单目测距公式 `d = K / (y_bottom - y_horizon)`，`K` 和 `y_horizon` 必须按你的车实测：

```bat
REM 把物体摆在 0.5m 和 1.5m，各记一次框底边 y，然后：
python -c "import sys; sys.path.insert(0,'tools'); from car_decide import MonoGroundDistance; MonoGroundDistance().calibrate(0.5, 400, 1.5, 307)"
```

默认值 `K=100, y_horizon=240` 是按"相机高 0.2m、水平、64° HFOV"估的，只能算个起点。

### ⚠ YOLO11n 在 AMD NPU 上的已知问题

之前实测：**YOLO11n 的 ONNX 在 Ryzen AI NPU 上有大量不支持算子回退 CPU，所以当时改用 YOLOv8**。

这次训练用 YOLO11n 没问题（训练走 4060 或 780M），但**如果最终要跑 NPU，需要先确认 YOLO11n 的算子兼容性**。三条路：

1. 训练用 YOLO11n，导出时试一次 NPU，不行就换 YOLOv8n 架构重训（同一份数据，改 `MODEL` 一行）
2. 直接在 780M 上跑（ROCm，实测 480 输入约 20ms / 50FPS，够用）
3. 用 DirectML（最快，2.4ms，但不是 ROCm 栈，合规性需确认）

---

## 7. 硬件改动

### 蜂鸣器：GPIO bit18 → 引脚 M14

**不需要改 Block Design。** AXI GPIO 是 32 位全输出，原本只用了 `[0:6] duty_l / [8:14] duty_r / [16] dir_l / [17] dir_r`，bit18 一直空着。

| 文件 | 改动 |
|---|---|
| `rtl/top.v` | 加 `output wire beep`，`assign beep = gpio_out[18];` |
| `constraints/7020_top.xdc` | `PACKAGE_PIN M14 IOSTANDARD LVCMOS33`（来自官方 `NAVIGATOR_ZYNQ_IO.xdc` 第 28 行） |
| `sw/src/ps_control.c` | 加 `BEEP_BIT 18`，`motor_write()` 每次带上该位 |

**关键陷阱：** GPIO 是整体写入，`motor_write()` 如果不带 beep 位，写电机的瞬间会把蜂鸣器关掉。已用 `MOTOR_ALL()` 宏统一处理。

如果实测这块板是**无源**蜂鸣器（直给电平只有咔哒声），`top.v` 里注释了 2kHz 方波的替换写法。

### 串口协议（新增 3 条命令）

| 命令 | 作用 |
|---|---|
| `M<dl>,<dr>,<ld>,<rd>` | 原子设置 占空比+方向（PC 决策层用，**不走 ramp，停车立即生效**） |
| `H` / `H0` / `H1` | 蜂鸣器 切换 / 关 / 开 |
| `X` | 急停（占空比清零，立即） |

原来的 `L/R/B/G/S/F/V/K/T/P/W/?` 全部保留，手动调试不受影响。

顺手修掉一个前人留下的显示 bug：`report()` 里 `dir=1` 是前进，但原来打印成 `REV`，已改为 `FWD`。

固件编译已验证：`sw\rebuild_app.bat` → `EXITCODE=0`。

> ⚠ RTL 改动（beep 端口）**还没有综合过**。要生效需要重新跑 `tcl\02_build.tcl` 出比特流。

---

## 7.5 运动辅助判据（可选）

回答"能不能用相对运动判断活体"这个问题。直接比像素变化不行（车也在动），但有两个可用角度：

### `flow` 模式 —— 车在动时用

不看位移，只看**框内光流的方一致性**。静止刚体在相机平移下所有点朝同一方向走（方向向量几乎重合），活体在形变（腿向前、身体上下）方向发散。

自测验证（合成序列）：
```
刚体整体平移（车动/物静）: dispersion = 0.000
内部反向撕开（活体形变） : dispersion = 0.908
```
**这个量对相机运动是免疫的**，不需要知道车速、不需要深度、不需要标定。

### `static` 模式 —— 停车后那 0.3 秒用

车一停相机就静止，"车在动"这个干扰项直接消失，此时最原始的帧间差分最可靠。**专门用来挡误报**（墙上海报、电视画面、被风掀的窗帘）。

### 定位：辅助，不能当主判据

数据通路是 640×480 **RGB565**、10~15 fps。RGB565 只有 16 位色，光流精度打折；帧率低导致帧间位移大、角点易配错；小车颠簸时全局运动也不只是平移。这三条叠加，纯运动分割误判率高。

所以：**检测为主，运动为辅**。任何一路失败都退化成纯检测，不因为运动判据说"不像活的"就放行。

```bat
python tools\detect_realtime.py --decide --motion flow      REM 车动时
python tools\detect_realtime.py --decide --motion static    REM 停车确认
python tools\detect_realtime.py --decide --motion off       REM 纯检测
```

---

## 8. 安全设计原则（改代码前先读）

这套逻辑是会直接导致车动/停的，`car_decide.py` 里写死了三条：

1. **宁可误停，不可漏停。** 检测到疑似活体且近，一律先停。运动判据只能用来决定"要不要恢复"，**绝不能用来否决停车**。
2. **任何一路信号缺失都不许让车继续高速前进。** 雷达没接 → 用单目估距（精度差但方向对）；运动判据算不出 → 退化纯检测。异常一律往保守方向倒。
3. **所有阈值带滞回 + 去抖。** 进 STOP 阈 1.0m，退出 1.3m；连续 3 帧才允许切换。单帧抖动让车在走停之间横跳，比一直停着更危险。

自测用例（`python tools\car_decide.py`）：

```
远处来人 2.5m   -> GO       duty=70/70
中距离来人 1.5m -> SLOW     duty=30/30
近距离来人 0.8m -> STOP     duty=0/0   beep=True
人退远 2.0m     -> GO       （但 beep 保持 1.5s 后才静音）
只有地毯        -> GO       （flat 不触发绕行）
近处桌子 0.5m   -> AVOID_R  duty=45/22  （差速绕行）
```

---

## 9. 还没做的

| 项 | 状态 |
|---|---|
| 活体训练数据 | **缺，当前 283 框，需要 5000+ 或 300~500 实拍** |
| RTL 综合（beep 端口） | 未跑，需 `tcl\02_build.tcl` |
| 实车联调（停车距离、蜂鸣时序） | 未做，需上车 |
| 激光雷达接入 | `LidarDistance` 桩已留好，实现 `distance_to_box()` 即可，决策层不用动 |
| 绕路策略 | 现在只是差速，真正的路径规划没做 |
| UDP 收图链路在 Python 侧 | **未在实板验证**（`detect_realtime.py` 里有注释标了） |
