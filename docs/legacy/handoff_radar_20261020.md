# AGENT 交接文档：郑的模块 + 雷达选型决策

> **用途**：把 2026-10-13 至 2026-10-20 期间关于"郑的模块怎么做"和"买哪个激光雷达"的全部讨论结论、证据与待验证项，打包给下一个接手的 AI Agent。
>
> **生成时间**：2026-10-20
> **适用项目**：2026 全国大学生嵌入式芯片与系统设计竞赛 · FPGA 创新设计赛道 · AMD 方向
> **本文档的地位**：是 `AMD_FPGA_A7E88981E99CB0E8A6_9CE6A1AFE89D80E793_BEE5B6BBE4BF94%A8.md`（下称"原手记"）的**补充与修正**。凡本文与原手记冲突，**以本文为准**，冲突点见 §9。

---

## 0. 阅读顺序（给接手 Agent）

1. 本文 §1–§4：搞清楚项目、分工、接口、郑要做什么
2. 本文 §5–§7：雷达选型结论与依据（**这是本次讨论最有价值的部分**）
3. 本文 §8：硬件现状（板子/底盘/链接）
4. 本文 §9：待验证清单（**接手后第一件事**）
5. 本文 §10–§11：文档待修正项 + 协作要求

**一句话背景**：三人零基础团队做"视觉为主 + 2D 激光雷达为辅"的 FPGA 避障小车。郑负责雷达处理、融合决策、上位机通信。雷达在 **国科光芯 D6** 与 **YDLIDAR X3 Pro** 之间二选一，结论是 **D6**（有明确翻转条件）。

---

## 1. 项目与分工（已确认）

**比赛**：2026 全国大学生嵌入式芯片与系统设计竞赛 · FPGA 创新设计赛道 · AMD 方向
**目标**：实现"感知 → 决策 → 控制 → 实体执行"闭环，做一辆能真实避障的小车。
**核心原则**：画面优先于炫技；先做能跑的雏形，再增强；控制学习成本与集成风险。

| 成员 | 负责 | 交付物 |
|---|---|---|
| **欧阳** | AI PC 视觉感知 | Camera → YOLO → Vision JSON → AMD 平台部署验证 |
| **郑**（本人） | 雷达处理 / 融合 / 决策 / 通信 | Radar JSON + Vision JSON → 状态机 → Control JSON → TCP → 安全机制 |
| **徐** | FPGA/Zynq/PYNQ 底层 | TCP → PS → AXI → PWM/Servo → 电机/舵机 → 硬件安全 |

**重要设计原则（不可动摇）**
1. **视觉为主、激光雷达为辅。** 类别与位置由视觉主导，雷达只提供距离/空间参考与安全冗余。
2. **郑不写 PWM，徐不做高层视觉决策。**
3. 先在电脑上用模拟数据跑通，再上实体小车。
4. 不能让 PYNQ 只当 USB/串口转接板——PL 必须承担 PWM / 实时控制 / 安全逻辑。
5. 雷达是**距离传感器，不是分类器**；"雷达没检测到" ≠ "没有障碍"。

---

## 2. 系统架构

```
USB 摄像头 → 欧阳：YOLO（AMD AI PC）
                     ↓  Vision JSON
              郑：雷达处理 + 视觉融合 + 状态机
                     ↓  Control JSON（TCP/Ethernet）
              徐：PYNQ PS → AXI → PL（PWM/Servo）→ 电机/舵机
                     ↑  Status JSON
```

雷达（D6 或 X3 Pro）接在同一条链路上：`雷达 → USB/UART → 郑（AI PC）`。

---

## 3. 统一接口（已定，但不完整）

### 3.1 欧阳 → 郑：Vision JSON
```json
{"version":1,"timestamp":10001,"obstacle":true,
 "nearest":{"class":"person","confidence":0.92,"cx":0.52,"cy":0.58,"width":0.20,"height":0.42}}
```

### 3.2 郑 → 徐：Control JSON
```json
{"version":1,"seq":1024,"timestamp":10002,"action":"LEFT","speed":40,"steering":110}
```

### 3.3 徐 → 郑：Status JSON
```json
{"seq":1024,"motor":"RUNNING","steering":110,"fault":false}
```

### 3.4 郑内部：Radar JSON（简化输出）
```json
{"valid": true, "front_distance": 0.79, "left_distance": 1.23, "right_distance": 1.51}
```

**硬性约定**
- 距离单位统一 **米**。
- 无有效回波时用 `null` 或 `valid:false`，**绝对不要填 0**（填 0 会让状态机把"看不见"当成"没障碍"）。
- 时间戳单位必须在 protocol 文档里写死（毫秒或微秒，目前未定）。

### 3.5 ⚠️ 接口未决项（接手后需推动确认）
- **动作集合不一致**：总方案第一版写的是 `FORWARD / LEFT / RIGHT / STOP`；原手记里多了一个 `SLOW`。**必须拍板一个版本**。
- `speed` / `steering` 的**取值范围和单位**未与徐对齐（示例 `steering:110` 明显是舵机角度，不是归一化值）。
- 超时阈值未定：雷达多少毫秒无新帧算失效、TCP 多久没收到 Status 算断连。

---

## 4. 郑的模块：要做什么、怎么做

### 4.1 交付物清单（验收口径）
- [ ] 雷达数据可解析（角度—距离 → 前/左/右距离）
- [ ] Vision + Radar 融合
- [ ] 状态机决策（含转向策略）
- [ ] TCP 通信（向 PYNQ 发 Control JSON）
- [ ] **超时 STOP** 安全机制
- [ ] 关键模块有独立 Demo / README / 测试记录

### 4.2 分阶段做法（对应总方案的阶段 1→4）

**阶段 1｜纯软件，不碰硬件（现在就能开工）**
1. Python 基础：字典、列表、函数、类、异常处理、JSON。
2. 写脚本：读入写死的 Vision JSON + Radar JSON → 输出 Control JSON。
3. 每帧写日志（后面答辩要靠日志证明闭环）。

**阶段 1.5｜雷达未到货，用 CSV/模拟数据**
- 按角度区间取距离：前 ±30°、左 30°~90°、右 -30°~-90°，取区间内**最小有效距离**。
- 过滤无回波点 + 滑动平均/中值滤波抑抖动。

**阶段 2｜接真实雷达**
1. 先跑厂家上位机，确认雷达能扫描（易卡在串口驱动/权限）。
2. Python 读角度-距离 → 转 Radar JSON。
3. 真实 Vision JSON + 真实 Radar JSON 一起喂状态机。

**阶段 3｜状态机 + 融合**
- 状态至少：`FORWARD / SLOW / LEFT / RIGHT / STOP`。
- 融合规则（来自两份文档，务必落实）：
  - 视觉发现障碍 + 雷达也近 → 提高风险，减速/停车。
  - **视觉发现障碍但雷达无有效数据 → 仍按视觉保守动作**。
  - 视觉没发现但雷达近距有目标 → 先减速/停车确认，**不凭单次回波急转**。
  - 转向要综合"视觉目标位置 + 左右可通行空间"，不是"哪边雷达距离大就转哪边"。
  - 数据过期 / TCP 超时 / Status `fault:true` → **STOP**。

**阶段 4｜TCP 通信**
- 发带 `version`/`seq` 的 Control JSON；自己实现 watchdog；联调先架空车轮或低速。

### 4.3 建议的独立小项目（A→D 递进）
- A：用 CSV/模拟数据测激光距离处理
- B：雷达读取 → 简化 JSON
- C：模拟 Vision JSON + 激光 JSON → 决策
- D：TCP 发给模拟服务 / PYNQ

---

## 5. 雷达选型：结论

### 5.1 最终结论（2026-10-20）

> **工程主线是避障 → 选国科光芯 D6。**
> **若 SLAM/建图变成要交付的目标 → 翻回 YDLIDAR X3 Pro。**

### 5.2 决策理由（按权重）
1. **融合层与雷达无关。** 所有融合方案只吃标准 `/scan`（LaserScan）。所以两款雷达的差异**只存在于"把 /scan 弄出来"这一步（驱动）**，不存在于融合/决策层。
2. **D6 的物理优势作用在长期使用上**：40g vs 135g、52.7×45.2 vs ~66×63.5 mm、功耗更低（X3 Pro 最大 500mA）。
3. **D6 硬件更强**：dTOF，对深色/远距更稳，12m 量程。
4. **D6 唯一短板（无 Python SDK）被 vibecoding 降级为一次性工作量**（半天~一天），且有 **7 天无理由退货**兜底。

### 5.3 翻转到 X3 Pro 的三个条件
1. **SLAM/建图是交付目标**（不是"以后可能"）——融合项目常要求 SLAM/AMCL 提供 TF，而 YDLIDAR 的 SLAM 教程遍地。
2. **底盘是大板（≥250mm）**——尺寸不再重要，选软件最省心的。
3. **团队极度怕卡在驱动上**——愿意用 95g 和体积换开箱即用。

### 5.4 候选雷达完整对比（含已淘汰项）

| 型号 | 原理 | 量程 | 采样/扫描 | 角分辨率 | 抗光 | 尺寸 (mm) | 重量 | 接口 | 参考价 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| **国科光芯 COIN-D6** | dTOF | 0.05–12m | 4000/s, 10Hz 固定 | 0.9° | 60Klux | **52.7×45.2×34.4** | **40g** | UART 230400 | ~249 元 | ✅ **选定** |
| **YDLIDAR X3 Pro** | 三角测距 | 0.12–8m | 4000/s, 5–10Hz | 0.6–1.2° | 40Klux | ~66×63.5×40.5（部分资料 94.08×66×40.5） | ~135g | UART 115200 | ~229 元 | 备选（SLAM 时翻转） |
| 乐动 STL-19P / D500 | dTOF | 0.03–12m | 5000/s, 6–13Hz | 0.43–0.94° | 60Klux | **38.6×38.6×33.5** | ~42–45g | UART 230400 | ~499 元(套件) | 尺寸最优但超预算，已排除 |
| 乐动 LD14P | 三角测距 | 0.1–8m | 4000/s, 默认 6Hz | 0.54° | 标称 80Klux（三角测距实际怕强光） | 96.3×59.8×38.8 | ~101g | UART 230400 | ~300–500 元 | ❌ 又大又重又贵，淘汰 |

**尺寸排序（底面积）**：STL-19P < D6 < LD14P ≈ X3 Pro
**重量排序**：D6 (40g) < STL-19P (~45g) < LD14P (~101g) < X3 Pro (~135g)

> ⚠️ 以上参数来自公开资料/渠道页，**商品页宣传 ≠ 独立验证**。下单前核对型号、配件、SDK 支持与实时价格。

### 5.5 商品链接（用户提供）
- **D6**：淘宝，标题"国科光芯激光雷达TOF测距12米 支持ROS2 SLAM建图导航 机器人小车"，**7 天无理由退货**，~249 元
- **X3 Pro**：淘宝亚博智能，标题写"YDLIDAR X3"，**实际变体是 X3 Pro**，假一赔四，~229 元
  - ⚠️ **务必在规格里选 X3 Pro**（4000 次/s、40Klux）；老 X3 只有 3000 次/s、2Klux（仅室内），更贵更差
- D6 驱动/SDK 资料页：https://wit-motion.yuque.com/wumwnr/wf4p82/izsls87bneer98w0 （及二次开发资料页 `nee1z0mt3vnmtm5z`）

---

## 6. 关键技术发现（本次讨论最有价值的部分）

### 6.1 融合层"雷达无关"——这是压缩难度差异的核心
GitHub 上的视觉-雷达融合项目**全部只依赖标准 `sensor_msgs/LaserScan`（`/scan`）**，不关心雷达品牌。
→ **两款雷达在融合/决策层的难度 = 完全相同。差异只在驱动这一步。**

### 6.2 D6 的协议存在不确定性（**接手后第一件要验证的事**）
证据矛盾，无法从公开资料定论：

| 说法 | 内容 |
|---|---|
| 说法 A | D6 数据帧 = LD06 那套：`0x54` 帧头 + `0x2C` VerLen，47 字节，12 点 × 3 字节，结束角 + 时间戳 + CRC-8 |
| 说法 B | D6 数据帧**不含 `0x54`**，另有协议；且必须先发 `AA 55 FE 01` 启动 |

**最可能的真相**：`AA 55 FE 01` 是**启动指令**，启动后输出的是 **0x54/0x2C 的 LD06 式数据帧**。

**👉 5 分钟自测（接手后立刻做）**
1. USB-TTL 接 D6，波特率 **230400**，1 停止位
2. 发送 `AA 55 FE 01`（停止是 `AA 55 FD 02`）
3. 看回传字节流里有没有以 **`54 2C`** 开头的 **47 字节**帧

| 结果 | 含义 |
|---|---|
| **有 `54 2C`** | D6 数据兼容 LD06 → 可直接借用 LD06/LD19 的整套生态（见 6.4）→ 生态差距几乎消失，**D6 选择更稳** |
| **没有** | 只能照官方 C++ 校验参考自己写解析 |

> 另一处冲突：启动指令在不同资料里写成 `AA 55 FE 01` / `AA 55 FD 02`，也有写成 `AA 55 F0 0F` / `AA 55 F5 0A`。**以实物实测为准。**

### 6.3 D6 生态盘点（"是不是真的那么少"）

**有的：**
- 官方 ROS1 驱动：`cspc_lidar_sdk_ros1_*.tar.gz`
- 官方 ROS2 驱动：`cspc_lidar_sdk_ros2_D4_20250731.tar.gz`（`ros2 launch cspc_lidar lidar_launch.py`，参数 `params/cspc_lidar.yaml` 里设 `port`）
- ROS 驱动使用指导 PDF
- Windows 上位机：CSPC 上位机 V2.1.36 + CH340 驱动
- STM32F103RCT6 例程
- **可正常校验的 C++ 参考脚本**：`lidar_data_processing.cpp`
- 至少一篇 CSDN 教程证明其在树莓派 ROS2 上跑通并 `ros2 topic echo /scan`

**缺的：**
- **GitHub 上没有公开仓库**（驱动通过语雀发 `.tar.gz`，不是 git repo → 没有 issue、没有社区、没法搜 bug）
- 几乎无第三方项目/教程；SLAM 配置要自己啃
- **无官方 Python SDK**

**已知坑**：官方《数据格式标准说明》的校验和描述有误，按文档手写解析会**反复出现校验错误**（多位客户反馈）。必须用官方 C++ 脚本对照。

### 6.4 D6 可能被遗漏的资源（取决于 6.2 的测试结果）
若 D6 与 LD06 数据兼容，下列成熟生态可直接借：
- `ldrobotSensorTeam/ldlidar_stl_ros2`（官方 ROS2，支持 LD06/LD19/STL-27L）
- `Myzhar/ldrobot-lidar-ros2`（Lifecycle 节点，Humble/Jazzy，SLAM Toolbox 兼容）
- `henjin0/LIDAR_LD06_python_loader`（纯 Python 加载器）
- `Robot-Maker-SAS/LD06`、`linorobot/ldlidar`

### 6.5 X3 Pro 的软件优势（对比用）
- **纯 Python 开箱即用**：`pip install lds2d`，原生支持 `YDLIDAR-X3-PRO`（115200）
- 官方 `YDLIDAR/YDLidar-SDK`（C/C++/Python/C#）
- 官方 `YDLIDAR/ydlidar_ros2_driver`（**必须用 `humble` 分支**，master 在 Humble 上编译报错）
- 大量教程、SLAM 配置、亚博官方 X3/X3 Pro 教程

### 6.6 vibecoding 能抹平多少差距（分层结论）

| 环节 | 能否抹平 | 说明 |
|---|---|---|
| 写解析 | ✅ 能 | 一次性 ~50–100 行，喂"帧文档 + 官方 C++ 校验脚本 + LD06 Python 例程"，半天~一天出活 |
| 编译/打通 ROS2 驱动 | ✅ 大概率能 | 驱动包已存在，可帮改 CMake/yaml/修编译错 |
| 融合 + 状态机 | ✅ 能（强项） | 逻辑自己写，GitHub 项目当参考，与雷达无关 |
| SLAM/建图调优 | ⚠️ 部分 | 能配 `slam_toolbox`，但"地图糊了/漂了"靠社区经验 |

**两个关键点**
1. **vibecoding 对两款雷达同样有效** → 它降低绝对难度，不改变相对差距；**不会让 D6 反超 X3 Pro**。
2. X3 Pro 剩下的真实优势是"**有人已经用这套雷达跑通过**"——顺利时无用，**踩怪 bug 时才值钱**。

---

## 7. GitHub 融合项目清单（可借鉴算法，不可直接套工程）

| 项目 | 做什么 | 依赖 | 套用判断 |
|---|---|---|---|
| [**HexboxRC/scan_detection_fusion**](https://github.com/HexboxRC/scan_detection_fusion) | ⭐ 最对口。2D 检测框（`vision_msgs/Detection2DArray`）与 `/scan` 匹配，输出带类别+距离的障碍物、供 Nav2 的足迹多边形 | ROS2 Jazzy；**核心类 `LidarCameraFuser` 是纯 Python、不依赖 ROS**；需 `map→base_footprint` TF（由 SLAM Toolbox 或 AMCL 提供） | **算法可借鉴**（正是郑的核心逻辑）；工程不能整体套 |
| [Triton-AI/sick_lidar_fusion_project](https://github.com/Triton-AI/sick_lidar_fusion_project) | Python+ROS2，YOLOv8 检测 + 激光点云取深度，输出融合图 | ROS2 Humble/Iron、ultralytics、绑定 SICK 雷达点云话题 | 思路可借鉴，绑定特定雷达 |
| [cristianooo1/mecanum_ros2_yolo](https://github.com/cristianooo1/mecanum_ros2_yolo) | 完整 ROS2 机器人：RPLIDAR + slam_toolbox + YOLOv8 + Nav2 | ROS2 全家桶、树莓派 | 作为架构参考；整体套用需全面转 ROS2 |
| [AbdullahGM1/l2i_fusion_detection](https://github.com/AbdullahGM1/l2i_fusion_detection) | C++，360° 雷达 + YOLOv11 + PCL，3D 定位 | ROS2 Humble、PCL、tf2 | 偏 3D，与单线 2D 雷达不匹配 |

**套用结论**
- **直接套用：低。** 它们假定"单机机器人电脑 + ROS2 + TF 树"，而本项目是"AI PC 视觉 → TCP → PYNQ 控制"，架构不同，且多数隐含要求 SLAM/AMCL。
- **借鉴算法：高。** 尤其 `LidarCameraFuser`（纯 Python、可脱离 ROS 运行）：把检测框的水平角度窗口映射到雷达扇区、取该扇区距离、做视差/时间对齐——**就是郑要写的融合核心**。
- **立即可以做的事（与买哪个雷达无关）**：先把 `LidarCameraFuser` 的算法用模拟 `/scan` + 模拟检测框验证一遍。

---

## 8. 硬件现状（有变更，务必注意）

| 部件 | 现状 | 影响 |
|---|---|---|
| **主控板** | **正点原子启明星 ZYNQ（XC7Z010 / XC7Z020）**（淘宝 MF168）——**不是文档里写的 PYNQ-Z2** | 可用 PYNQ，但**必须用正点原子官方 PYNQ 镜像**（《启明星ZYNQ之PYNQ开发指南 V2.0》），不能直接刷 PYNQ-Z2 官方镜像；7010/7020 镜像要对应核心板型号。**建议买 7020**（PL 资源多一倍） |
| **小车底盘** | 拼多多（`goods_id=943266007399`），页面强制登录，**尺寸未能核实** | 按拼多多常见智能小车底盘推断：宽 130–155mm、长 150–260mm、整车约 236g；**若实际是大板（≥250mm），雷达选型结论会变** |
| **雷达** | 二选一：国科光芯 D6（~249，7天无理由）/ YDLIDAR X3 Pro（~229） | 结论见 §5 |
| **摄像头** | 已有可复用 USB 摄像头 | — |
| **其他** | PYNQ-Z2 文档里列的 TB6612FNG、TT 减速电机、SG90 舵机、MicroSD、杜邦线、降压模块、USB-TTL 等 | 电机不得由 PYNQ 3.3V IO 直供；共地/电平按数据手册核实 |

**SLAM 的前置条件（重要）**：光有雷达做不了可靠 2D SLAM，需要**里程计**（轮式编码器或 IMU）。当前底盘是 TT 减速马达，**通常不带编码器** → 若后续要 SLAM，需换带编码器电机 + 加 IMU。**这比选哪个雷达更影响 SLAM 成败。**

---

## 9. 待验证清单（按优先级，接手后逐项打勾）

| # | 事项 | 方法 | 判定 |
|---|---|---|---|
| 1 | **D6 数据帧是否 LD06 兼容** | 230400 发 `AA 55 FE 01`，看是否有 `54 2C` 开头 47 字节帧 | 有→可借 LD06 生态；无→自写解析 |
| 2 | **底盘实际尺寸** | 量长宽 / 问客服 / 解淘口令 | 宽 ≤160mm → D6；≥250mm → 重新评估 |
| 3 | **D6 启动指令** | 实物实测（资料里有 `AA 55 FE 01` 和 `AA 55 F0 0F` 两种写法） | 以实测为准 |
| 4 | **D6 校验和坑** | 用官方 `lidar_data_processing.cpp` 对照，别只照文档 | 能对上官方上位机数据 |
| 5 | **动作集合** | 队内拍板 `SLOW` 是否保留；写进 protocol 文档 | FORWARD/LEFT/RIGHT/STOP (+SLOW) |
| 6 | **speed/steering 范围** | 与徐对齐（单位、上下限、舵机中位） | 写入 protocol |
| 7 | **时间戳单位 + 超时阈值** | 队内定，写进 protocol | ms + 明确阈值 |
| 8 | **PYNQ 镜像** | 确认板子是 7010 还是 7020；用正点原子官方 PYNQ 镜像 | 能启动 PYNQ |
| 9 | **里程计方案（仅 SLAM 需要）** | 编码器电机 + IMU | 后续再定 |

---

## 10. 已知文档不一致 / 待修正

| 位置 | 现状 | 应改为 |
|---|---|---|
| 总方案 docx **标题与硬件清单** | 写"视觉**毫米波**雷达" | 实际选型是 **2D 激光雷达**（D6），需统一 |
| 总方案 docx **板子** | 写 **PYNQ-Z2** | **正点原子启明星 ZYNQ 7020** |
| 总方案 docx vs 原手记 **动作集合** | docx = 4 个动作；手记多 `SLOW` | 拍板统一 |
| 验收清单 | 只写"雷达数据可解析" | 补上具体型号 + 接口（UART 230400） |
| 原手记 §5.1 | 称考虑 YDLIDAR X3 Pro | 已改为在 D6 / X3 Pro 间二选一，结论 D6 |

---

## 11. 给接手 Agent 的协作要求

沿用原手记 §8，并补充本次结论：

1. 用简体中文回答，面向零基础成员，给**可执行步骤**。
2. 优先给"能在电脑上独立完成的小项目"，再逐步接硬件。
3. **不要擅自把 ROS2 / Linux / SLAM / 强化学习加入必选路线**——本项目主线是避障，ROS2 是可选。
4. **不要把"雷达未检测到目标"等价为"没有障碍"。**
5. 对硬件规格、型号兼容性、价格、SDK 支持等动态信息，**先查官方资料并标注不确定性**；淘宝/拼多多页面需登录，抓不到时不要编造。
6. **区分：已确认事实 / 商品宣传 / 推断 / 待验证**（本文 §5.4、§6.2、§8 已按此标注）。
7. 不要让 PYNQ 仅当接口转换器；必须体现 PS/PL/AXI 与 PL 硬件逻辑。
8. 保持"**视觉为主、雷达为辅**"，不要改成纯激光导航。
9. **融合层只需标准 `/scan`**——不要把雷达品牌绑进融合/决策逻辑。
10. **vibecoding 是降低工作量的手段，不是消除风险的手段**：对两款雷达同等有效，不要据此得出"D6 生态无忧"的结论。
11. 雷达选型已有结论（D6），**除非 §5.3 的翻转条件成立，否则不要反复重开会话重新选型**。
12. 控制范围：先做可靠雏形，再决定是否加 ROS2 / SLAM。

---

## 12. 参考链接

**雷达资料**
- 维特智能 COIN-D6 说明书：https://wit-motion.yuque.com/wumwnr/wf4p82/izsls87bneer98w0
- 维特智能 COIN-D6 二次开发资料：https://wit-motion.yuque.com/wumwnr/wf4p82/nee1z0mt3vnmtm5z
- CSDN：树莓派 ROS2 COIN-D6 驱动：https://blog.csdn.net/2502_92009732/article/details/162523894
- YDLIDAR 官方 ROS2 驱动：https://github.com/YDLIDAR/ydlidar_ros2_driver
- YDLidar-SDK：https://github.com/YDLIDAR/YDLidar-SDK
- lds2d（纯 Python，支持 X3 Pro）：https://pypi.org/project/lds2d/
- 亚博智能 YDLIDAR X3 资料：https://www.yahboom.com/study/YDLIDAR-X3
- LD06/LD19 ROS2 驱动：https://github.com/ldrobotSensorTeam/ldlidar_stl_ros2
- LD06 Python 加载器：https://github.com/henjin0/LIDAR_LD06_python_loader

**融合项目**
- https://github.com/HexboxRC/scan_detection_fusion
- https://github.com/Triton-AI/sick_lidar_fusion_project
- https://github.com/cristianooo1/mecanum_ros2_yolo
- https://github.com/AbdullahGM1/l2i_fusion_detection

**平台/框架**
- AMD Ryzen AI：https://ryzenai.docs.amd.com/en/latest/
- PYNQ 入门：https://pynq.readthedocs.io/v3.1/getting_started.html
- PYNQ Overlay：https://pynq.readthedocs.io/en/latest/overlay_design_methodology/overlay_design.html
- Ultralytics Python：https://docs.ultralytics.com/usage/python/
- HDLBits：https://hdlbits.01xz.net/wiki/Main_Page

**项目内文档**
- `04_AMD_FPGA视觉毫米波智能避障小车_三人项目总方案.docx`（总方案，含"毫米波/PYNQ-Z2"待修正项）
- `AMD_FPGA_A7E88981E99CB0E8A6_9CE6A1AFE89D80E793_BEE5B6BBE4BF94%A8.md`（原手记，2026-10-13）

---

## 13. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-13 | 原手记：确定"视觉为主 + 雷达为辅"路线，考虑 YDLIDAR X3 Pro，结论不强制 Linux/ROS2 |
| 2026-10-20 | 本次讨论：<br>• 新增候选：乐动 LD14P / STL-19P(D500)，因尺寸/价格排除<br>• 确认板子改为**正点原子启明星 ZYNQ**（非 PYNQ-Z2）<br>• 雷达二选一缩小为 **D6 vs X3 Pro**<br>• **结论：主线避障选 D6**；SLAM 交付化 / 大底盘 / 极度怕风险 → 翻 X3 Pro<br>• 发现**融合层雷达无关**，难度差异仅限驱动<br>• 发现 **D6 协议兼容性存疑**（`54 2C` 自测项）<br>• 盘点 D6 生态 + GitHub 融合项目 + vibecoding 分层结论 |
