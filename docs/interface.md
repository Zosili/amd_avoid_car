# 接口定义（协议 / 报文 / 寄存器 / 时序）

> 对应指南 3.2.5.1(1)「接口定义：通信协议、报文格式或寄存器映射、数据类型与时序约定」。
> **改本文件里任何一条规则，必须同时升 `CONTRACT_VERSION`，并让两人都 review 过。**

真源（唯一权威实现，文档与代码冲突时以代码为准）：

| 内容 | 文件 |
|---|---|
| 消息 schema、阈值 `CFG`、动作集合 | [`host/app/car/protocol.py`](../host/app/car/protocol.py) |
| steering → 差速 PWM、检测框 → Vision JSON、频率契约 | [`host/app/integration/contract.py`](../host/app/integration/contract.py)（`CONTRACT_VERSION = 2`） |
| D6 雷达串口帧 | [`host/app/car/radar/d6_protocol.py`](../host/app/car/radar/d6_protocol.py) |
| AXI 电机寄存器 | [`fpga/src/motor/motor_axi.v`](../fpga/src/motor/motor_axi.v) |
| 板端编排（端口、广播内容） | [`host/app/joint/board_main.py`](../host/app/joint/board_main.py) |

---

## 1. 端口与传输总表

| 端口 | 传输 | 谁监听 | 谁连 | 方向 | 内容 | 频率 |
|---|---|---|---|---|---|---|
| **5000** | **TCP**，行分隔 JSON | 板子（`joint/vision_server.py`） | 视觉 PC | PC → 板 | Vision JSON | **≥ 7 Hz（硬要求）** |
| **5001** | TCP，行分隔 JSON | 电机侧（徐） | 板子（`comm/tcp_client.py`，断线 1 Hz 重连） | 双向 | Control JSON ↓ / Status JSON ↑ | 10 Hz |
| **5002** | TCP，行分隔 JSON | 板子（`MonitorBroadcaster`） | 任一 PC 监控端 | 板 → PC | Monitor JSON（只读） | ~10 Hz |
| 1234 | UDP，自定义帧 | PC（`main_ryzenai.py`） | PL（`eth_top`/`udp`） | 板 → PC | 相机帧：`f0 5a a5 0f` + `02 80 01 e0`(640×480) + 614 400 B RGB565（小端） | ~26 fps |
| — | UART 230400 8N1 | 板子 PS | D6 雷达 | 雷达 → 板 | 85 B 定长帧（§5） | 163 帧/s |
| — | AXI4-Lite（PS→PL） | PL `motor_axi` | PS（`pynq.MMIO`） | 双向 | 寄存器读写（§6） | 10 Hz + 100 ms 心跳 |

> ⚠️ **5000 端口有两套互不兼容的历史实现**：本表规定的是 **TCP**（`joint/vision_server.py`，当前方案）；`integration/vision_receiver.py` / `vision_sender.py` 是早期的 **UDP** 方案（文档字符串里还写着「PS IP 192.168.1.20」）。两者不能同时起，接板联调时**只用 TCP 那套**，UDP 那套保留仅作历史参考（建议改端口 5010 以免误连）。

---

## 2. 通用约定

| 项 | 约定 |
|---|---|
| 编码 | UTF-8 JSON，**每行一条**（`\n` 结尾），不做批量数组 |
| 版本字段 | 所有消息带 `"version": 1`；版本不符直接丢弃 |
| 时间 | Unix 毫秒整数（`int(time.time()*1000)`）；**接收方重打到达时间**，不信任发送方时间戳 |
| 距离 | 米（float）；**无有效回波用 `null`，绝不用 `0`**（0 会被当成"贴脸"） |
| 角度 | 雷达角度：度，顺时针为正，0° 见 §5；舵机角度 0–180，90 = 中位 |
| 速度 | `speed` 0–100（百分比）；PWM 满量程 1000（`contract.PWM_MAX`） |
| 数值校验 | 视觉归一化字段必须是 `0.0 ~ 1.0`（含端点）的 `int/float`；`bool` 不算数值 |
| 未知字段 | 忽略（向前兼容）；缺必需字段 → 整条丢弃并计入 `drops` |

---

## 3. Vision JSON（PC → 板，5000/TCP）

```json
{"version":1,"timestamp":1793423322338,"obstacle":true,
 "nearest":{"class":"person","confidence":0.82,
            "cx":0.30,"cy":0.55,"width":0.18,"height":0.42}}
```

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `version` | int | == 1 | 版本不符丢弃 |
| `timestamp` | int | 毫秒 | PC 侧产生时刻；板子**会覆盖**为到达时间 |
| `obstacle` | bool | 必需 | `false` 时可不给 `nearest`（但**必须照发**，不发等于链路断 → 车停） |
| `nearest.class` | str | 必需 | 类别名，如 `person` |
| `nearest.confidence` | float | 0–1 | 置信度 |
| `nearest.cx` / `cy` | float | 0–1 | 框中心**归一化**坐标（不是像素！`cx = (x1+x2)/2/W`） |
| `nearest.width` / `height` | float | 0–1 | 框宽高归一化 |

**`nearest` 选取规则**：取 `cy` 最大的框（画面最靠下 = 离车最近），不用「置信度最高」。
**左右判定**（`protocol.vision_side`）：`cx < 0.45` → `left`；`cx > 0.55` → `right`；其余 `center`。

**频率契约（最容易踩的坑）**

```
vision_timeout_ms >= 2 × (1000 / 发送频率Hz)
   2 Hz → 1000 ms      5 Hz → 400 ms      10 Hz → 200 ms
```
当前 `vision_timeout_ms = 300` ⇒ 要求发送频率 **≥ 6.7 Hz**，取整 **7 Hz**（`contract.VISION_HZ_MIN = 7.0`）。
❌ 不要用「2 Hz 推理 + 10 Hz 重发旧结果」凑频率：时间戳骗人，人走了车还在躲。

参考实现：[`host/app/integration/vision_sender.py`](../host/app/integration/vision_sender.py)（`detections_to_vision()` 负责像素 → 归一化）。

---

## 4. Control / Status JSON（板 ↔ 电机，5001/TCP）

### 4.1 Control（板 → 电机侧）

```json
{"version":1,"seq":123,"timestamp":1793423322338,
 "action":"RIGHT","speed":35,"steering":45,
 "pwm_l":350,"pwm_r":140,"in1":1,"in2":0,"in3":1,"in4":0}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `seq` | int | 0–65535 循环；Status 必须原样回显，用于丢包排查 |
| `action` | str | `FORWARD` / `SLOW` / `LEFT` / `RIGHT` / `STOP` |
| `speed` | int | 0–100 |
| `steering` | int | 0–180，90 = 直行（`>90` 左、`<90` 右）；**电机侧不需要理解它** |
| `pwm_l` / `pwm_r` | int | **已算好的差速量**，带符号：`>0` 正转、`<0` 反转、`0` 惰行；量程 ±`PWM_MAX`(1000) |
| `in1..in4` | 0/1 | 同上信息的电平形式：左轮 `in1/in2`、右轮 `in3/in4`；`pwm>0 → (1,0)`，`pwm<0 → (0,1)`，`pwm=0 → (0,0)` |

**差速映射（唯一实现：`contract.control_to_diff`）**

```
base = clamp(speed/100 × PWM_MAX, 0, PWM_MAX)
k    = clamp((steering - 90) / 45, -1, +1)        # k>0 左转，k<0 右转
k>=0: pwm_r = base, pwm_l = base × (1 - TURN_GAIN×k)   # 外侧轮不超速，内侧最多停转
k<0 : pwm_l = base, pwm_r = base × (1 + TURN_GAIN×k)
|pwm| < DEADBAND_PWM(20) → 0                       # 防电机嗡嗡响不转
action == "STOP" → 四个 pwm/in 全 0（惰行）
```
参数：`PWM_MAX=1000`、`TURN_GAIN=0.6`、`DEADBAND_PWM=20`。**标定只改这三个数**，别在电机侧另写一套。

### 4.2 Status（电机侧 → 板）

```json
{"version":1,"seq":123,"timestamp":1793423322410,"ok":1,"vbat":12.1,"enc_l":0,"enc_r":0}
```

| 字段 | 说明 |
|---|---|
| `seq` | 原样回显对应 Control 的 `seq` |
| `ok` | 1 = 执行成功 |
| `vbat` | 电池电压（V，没有采样就填固定值也要给字段） |
| `enc_l` / `enc_r` | 编码器计数；没接编码器时恒 0 |

**看门狗约定**：板子超过 **500 ms** 收不到 Status → 判链路断 → **强制 STOP**（安全设计，不是 bug）。板子退出时也会主动补发一条 STOP。

---

## 5. D6 激光雷达串口帧（雷达 → 板）

来源三源交叉验证：官方《COIN-D6 数据格式标准说明 V1.0》+ 官方 STM32 例程 `ax_laser.c` + 官方 ROS2 SDK `lidar_data_processing.cpp`；实测抓包 1110 帧，官方 CS 校验命中 **1109/1110**（唯一失败帧为 UART 误码）。样本见 [`docs/refs/d6_official_frames.txt`](refs/d6_official_frames.txt)。

```
帧长 85 B = AA 55 | M&T(1B) | LSN(1B) | FSA(2B) | LSA(2B) | CS(2B) | LSN × (Si_L, Si_2nd, Si_H)(3B)
```

| 字段 | 含义 |
|---|---|
| `AA 55` | 包头（参与校验，`PH = 0x55AA`） |
| `M&T` | bit0 = 包类型（0 点云包 / 1 起始包，起始包 `LSN=1`）；bit7: 1 = 扫描频率 |
| `LSN` | 本包采样点数（点云包 25） |
| `FSA` / `LSA` | 起止角度原始值，小端 u16，**最低位是固定为 1 的 C 校验位** |
| `CS` | 双字节异或校验，小端存放 |

**解包公式**

```
角度 Angle(deg) = (raw >> 1) / 64.0
距离 dist_mm    = Si_H*64 + (Si_2nd >> 2)              # 14 bit
强度 it         = (Si_2nd & 0x03)*64 + (Si_L >> 2)      # 8 bit
Si_L bit0       = 远距/弱回波标志（低置信，默认丢）
CS = 0x55AA ^ (M&T + LSN*256) ^ FSA_raw ^ LSA_raw ^ XOR_k( Si_L ^ (Si_2nd + Si_H*256) )
```

**物理与几何**：波特率 230400 8N1；上电即吐流（部分固件需先发 `AA 55 F0 0F` 启动）；400 点/圈、0.9° 分辨率；量程 0.05–12 m（<6 cm 一律返回 0 = 无回波）。
**0° 零点**：官方定义 0° 指向外壳上**单个安装耳**的方向（不是接线束方向），装车偏角用 `ZERO_OFFSET_DEG` 补偿。

**扇区**（顺时针，度）：`FRONT (345,15)` 跨 0°、`LEFT (30,90)`、`RIGHT (270,330)`；每扇区取**第 k 小**（`SECTOR_ROBUST_K=2`）而非 `min()`，避免单点噪声把 3 m 拉到 0.2 m；有效点 < 4 时退回取最小。

---

## 6. AXI4-Lite 电机寄存器（PS ↔ PL）

实现：[`fpga/src/motor/motor_axi.v`](../fpga/src/motor/motor_axi.v)（参数 `CLK_HZ=50 MHz`、`PWM_HZ=20 kHz`、`WDT_MS=300`）。PS 侧：`pynq.MMIO(base, length)`（见 `integration/run_on_board.py::MotorBridge`）。

| 偏移 | 名称 | 读写 | 位/范围 | 含义 |
|---|---|---|---|---|
| `0x00` | `CTRL` | W | bit0 使能（0 = 强制停车）；bit1 清急停 | 总开关 |
| `0x04` | `SPEED_L` | W | 有符号 16 位，−1000 ~ +1000 | 左轮速度，负数反转 |
| `0x08` | `SPEED_R` | W | 同上 | 右轮速度 |
| `0x0C` | `HEARTBEAT` | W | 32 位递增 | 每 100 ms 写一次；**300 ms 不变 → 硬件急停** |
| `0x10` | `ENC_L` | R | u32 | 左编码器计数（4 倍频；没接恒 0） |
| `0x14` | `ENC_R` | R | u32 | 右编码器计数 |
| `0x18` | `STATUS` | R | bit0 看门狗超时；bit1 急停锁死；bit2 电机使能中 | 状态回读 |

**时序约定**：`PWM_PERIOD = CLK_HZ / PWM_HZ = 2500` 周期；`WDT_LIMIT = CLK_HZ/1000 × WDT_MS = 15e6` 周期。**改 `CLK_HZ` 必须和实际 AXI 时钟一致**，否则 PWM 频率与看门狗时间一起偏。

---

## 7. Monitor JSON（板 → PC，5002/TCP，只读）

```json
{"version":1,"t":1793423322338,"seq":123,"rule":"R4","why":"vision left + left blocked",
 "action":"RIGHT","speed":35,"steering":45,
 "radar":{"valid":true,"front_distance":1.02,"left_distance":0.55,"right_distance":1.40,"t":1793423322300},
 "sectors":[[1.02,18],[1.02,22],...],  // 12 个 30° 扇区：[距离m, 点数]，s0 跨 0°
 "pts":[[a, d_mm, it, weak], ...],     // 点云，仅在 --monitor-pts 时下发
 "vision":{"obstacle":true,"class":"person","cx":0.30,"cy":0.55,"conf":0.82,"side":"left"},
 "fault":false,"status":{"watchdog_timeout":false,"estop":false},
 "vision_age_ms":87,"drops":0}
```

- 12 扇区公式与 PC 端 `radar_map_view.py::_sector12` 完全一致：`s_k = [k*30-15, k*30+15)`，`front = min(s11, s0, s1)`。
- 广播端**只写不读**：某个监控客户端卡住只丢它，绝不阻塞 10 Hz 决策循环。

---

## 8. 变更策略

1. 改 schema / 阈值 / 映射 → `contract.CONTRACT_VERSION += 1`，在 PR 描述里写「破坏性变更」与影响面。
2. 三处同时改：发送方、接收方、`metrics/scripts` 里引用的字段名。
3. 改完必须跑：`python -m pytest car -q`、`cd host/app && bash joint/_run_it.sh person_left`、`python metrics/scripts/latency_stats.py`。
4. 端口/传输方式变更（例如 5000 TCP↔UDP）属于**破坏性变更**，需在 `docs/architecture.md` §9 风险表里登记。
