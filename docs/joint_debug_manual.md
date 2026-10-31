# 三端联调手册（雷达/融合 · 视觉 · 电机）

> 面向：郑（雷达+融合决策）、欧阳（视觉）、徐（FPGA/电机）
> 版本：2026-10-26 · 端口与 JSON 字段以本文为准

---

## 0. 一句话讲清架构

**决策程序跑在板子上（Linux/PYNQ），你笔记本只当显示器。**

```
欧阳 PC ──Vision JSON/TCP:5000──►  板子 board_main.py  ──Control JSON/TCP:5001──►  徐 电机
                                     （雷达 /dev/ttyUSB0）  ◄──Status JSON────────
                                            │
                                            └──Monitor JSON/TCP:5002──► 郑 笔记本（云图，只读）
```

为什么不能把桌面上那套搬上板：板子没显示器，ARM 上 matplotlib 一帧要 1~2 秒，
而且雷达插在板子上，笔记本的 COM 口根本读不到。所以 **板上算、PC 看**。

| 端口 | 方向 | 谁监听 | 内容 |
|---|---|---|---|
| 5000 | 欧阳 → 板 | **板子** | Vision JSON，每行一个 |
| 5001 | 板 ↔ 徐 | **徐** | 板发 Control（含差速 `pwm_l/pwm_r`），徐回 Status（seq 原样回显） |
| 5002 | 板 → 郑笔记本 | **板子** | Monitor JSON，只读，含点云+12扇区+规则 |

⚠️ 5000/5002 是板子**监听**（inbound），Windows 防火墙默认可能挡。
笔记本上第一次连不上，先临时关掉专用网络的防火墙试。

---

## 1. 每个人最少要做什么

### 欧阳（视觉）
只需一件事：把每帧检测结果按下面的格式，用 TCP 发到 `板子IP:5000`，**每行一个 JSON**。

```json
{"version":1,"timestamp":1750000000000,"obstacle":true,
 "nearest":{"class":"person","confidence":0.82,
            "cx":0.30,"cy":0.55,"width":0.18,"height":0.42}}
```

- `cx/cy/width/height` 都是**画面归一化 0~1**（不是像素）
- `obstacle:false` 时 `nearest` 可以不给
- 没有目标也要发（`obstacle:false`），**不发等于链路断，车会停**
- 板子不信任你发的 `timestamp`，会用自己的到达时间打戳，所以两边时钟不同步也不会误判
- 🔴 **发送频率必须 ≥ 7Hz**（`contract.py::VISION_HZ_MIN`）。
  决策侧 `vision_timeout_ms = 300`，2Hz 发送会让 **70% 的决策周期在停车**，
  车一顿一顿地走，看着像"程序抽风"，其实是频率没对齐。
  公式：`timeout >= 2 × (1000 / 频率)`。做不到 7Hz 就只能把超时调到 1000ms（视觉真挂了要 1 秒才发现）。
- ⚠️ **不要用「2Hz 推理但 10Hz 重发旧结果」来凑频率** —— 时间戳会骗人，人走了车还在躲

现成参考实现：`joint/mock_vision.py`（可直接改成你的真实数据源），
归一化工具：`integration/contract.py::detections_to_vision()`。

### 徐（电机）
只需两件事：

**① 监听 5001，逐行读 Control JSON 并执行**

```json
{"version":1,"seq":123,"timestamp":1750000000000,
 "action":"RIGHT","speed":35,"steering":45,
 "pwm_l":350,"pwm_r":140,"in1":1,"in2":0,"in3":1,"in4":0}
```

⭐ **`pwm_l / pwm_r / in1..in4` 是郑已经算好的差速指令，直接写寄存器就行** ——
不用再理解 `steering` 的语义（`90`=直行、`>90`=左、`<90`=右）。
映射规则定义在 `integration/contract.py::control_to_diff()`，**只有那一份**，
`TURN_GAIN`、`PWM_MAX`、方向引脚极性都在里面，标定时改那一个文件。

带符号 `pwm` 表示方向：`>0` 正转、`<0` 反转、`0` 惰行。`in1..in4` 是同一信息的电平形式。

**② 每收到一条就回一条 Status（同一连接、同一行格式），`seq` 原样带回**
```json
{"version":1,"seq":123,"timestamp":1750000000000,"ok":1,"vbat":12.1,"enc_l":0,"enc_r":0}
```
郑只用两件事：**有没有收到**（watchdog）、**seq 对不对**（丢包排查）。
`ok/vbat/enc_l/enc_r` 现在只是记录，不用真接编码器也能跑，但字段要在。

> 徐不回 Status 超过 500ms → 郑判链路断 → 强制 STOP。这是安全设计，不是 bug。

现成参考实现：`joint/mock_motor.py`（郑本地联调时扮演你，会打印 `L=350 R=140`）。

**③ 如果电机驱动就跑在同一块 PYNQ 上**
`integration/mock_board.py` 的注释写着「把 `_apply()` 里的打印换成 `mmio.write` 就是最终实现」——
那种情况下其实不需要 TCP：郑的决策和你的驱动在同一个进程里，
直接 `ctrl = contract.control_to_diff(...)` 然后写 AXI 寄存器即可，
`board_main.py --motor` 留空（`--motor` 不填就只写日志不外发）。
**用 TCP 还是同进程，取决于你们俩的代码要不要独立调试。**

### 郑（自己）
板子上跑 `joint/board_main.py`；笔记本上跑 `integration/radar_map_view.py --tcp`。

---

## 2. 三种联调场景

### 场景 A：你一个人，没板子没雷达（现在就能做）

验证**决策逻辑 + 网络链路 + 界面**，不需要任何硬件。

```bash
cd D:/dasanshang/fpga竞赛

# 终端1：假徐（扮演电机侧，回 Status）
python joint/mock_motor.py --port 5001

# 终端2：板入口（假雷达 front=1.5m 一路畅通）
python joint/board_main.py --radar fake --motor 127.0.0.1:5001

# 终端3：假欧阳，换个场景看看
python joint/mock_vision.py --scenario person_left
```

**看什么**：终端2 每行输出 `R4 RIGHT vision=left L=1.0 R=1.0 -> RIGHT` 这类，
说明「视觉说左边有人 → 决策往右打」整条链路通了。

**再开一个终端4看云图**（弹窗口）：
```bash
python integration/radar_map_view.py --tcp 127.0.0.1:5002
```

一键跑完整套（含断言）：
```bash
bash joint/_run_it.sh person_left     # 决策链
bash joint/_run_net.sh person_left    # PC 端数据源（无窗口自检）
bash joint/_run_monitor.sh            # 监控广播格式
```

### 场景 B：有板子，队友还没来

板子上（先确认 IP）：

```bash
# 板上
ip addr show                       # 记下 wlan0/eth0 的 IP，比如 192.168.2.99
ls -l /dev/ttyUSB*                 # 雷达设备名，通常 ttyUSB0
cd ~/car
python3 joint/board_main.py --radar-port /dev/ttyUSB0 --motor 192.168.2.50:5001
```

笔记本上：

```bash
# 假欧阳，往板子推视觉
python joint/mock_vision.py --host 192.168.2.99 --scenario person_left

# 云图，看板上真实点云
python integration/radar_map_view.py --tcp 192.168.2.99:5002
```

**这时云图右栏显示的是板上的真实规则和动作**（`R1/R2/R3/R4/R5` + `why`），
不是笔记本自己猜的 —— 屏幕上是「车实际会怎么做」。

### 场景 C：三个人齐（比赛现场）

**启动顺序不能乱**（谁先谁后有原因）：

1. **徐**先起电机侧监听（5001）。不起 → 板子一直重连、一直 STOP，看着像"程序坏了"
2. **郑**起 `board_main.py`。它会打印 `[motor] 已连上 …`，看到这行才算通
3. **欧阳**起视觉，往 `板子IP:5000` 推。**郑这边必须看到第一帧，车才可能动**
   （决策机 R5：`no vision frame ever` → 无条件停车）
4. **郑**开云图 `--tcp 板子IP:5002` 监视

关机顺序反过来：先停欧阳 → 再停 board_main（Ctrl-C 会自动补一条 STOP）→ 最后停徐。

---

## 3. 板上环境准备（一次性）

PYNQ 2.7 的底子是 **Ubuntu 18.04 + Python 3.6.9**，代码已确认兼容（无 3.7+ 语法）。

```bash
# 1) 串口库（板上默认没有）
sudo pip3 install pyserial
#    没网时：把 pyserial 的 .whl 拷到板上，pip3 install --no-index --find-links=. pyserial

# 2) 串口权限（否则 /dev/ttyUSB0 打不开）
sudo usermod -aG dialout $USER
#    然后必须重新登录（或重启），组权限才生效
#    临时验证：sudo chmod 666 /dev/ttyUSB0

# 3) 目录结构（board_main 靠这个相对位置找郑的代码）
#    ~/car/
#    ├── zheng/       ← 整个拷过去（protocol.py / fusion / radar / comm / vision）
#    ├── joint/       ← board_main.py / vision_server.py
#    └── integration/ ← 只放 contract.py
```

不需要在板上装 numpy / matplotlib / opencv —— `board_main.py` 一个都不用。

**自检**（不接任何硬件）：
```bash
python3 joint/board_main.py --selftest
```

---

## 4. 验收清单

跑场景 A 或 B 时逐条对：

| # | 检查项 | 期望看到 |
|---|---|---|
| 1 | 板子连上电机侧 | `[motor] 已连上 192.168.2.50:5001` |
| 2 | 徐收到控制 | 徐终端刷 `seq=… action=… speed=…` |
| 3 | 徐回 Status | 郑这边不再输出 `status fault=true` |
| 3b | **差速方向对** | 看到 `RIGHT` 时 `L` 数值 > `R` 数值（左轮快才右转）；`LEFT` 反之。反了改 `contract.py` 的方向引脚或 `steer_left/steer_right` |
| 4 | 欧阳连上 | 板上 `[vision] 客户端接入 192.168.2.x` |
| 5 | 视觉生效 | 规则从 `R5 no vision frame ever` 变成 `R4/R2` |
| 6 | 云图有数据 | 左图有点云、右图 12 个蓝条、顶部显示 `车上 192.168.2.99:5002` |
| 7 | 断视觉会停车 | 停掉欧阳 → 300ms 内变 `R5 vision stale` |
| 8 | 断链路会停车 | 停掉徐 → 500ms 内变 `R5 status fault=true` |
| 9 | 退出补 STOP | Ctrl-C board_main → 徐最后收到一条 `STOP` |

第 7、8 条是**安全底线**，一定要亲眼看一次。用 `--drop-after` 可以模拟：
```bash
python joint/mock_motor.py --port 5001 --drop-after 20   # 第20帧后不回Status
```

---

## 5. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `[FAIL] 雷达串口打不开` | 设备名错 / 没插 / 权限 | 看它列出的可用串口；板上 `ls /dev/ttyUSB*` |
| 板子上一个串口都没有 | USB 线是充电线 / 没供电 | 换线；雷达单独供电 |
| `[motor] 连不上 …，1s 后重试` | 徐没起 / IP 错 / 不同网段 | 徐先起监听；两边 `ping` 通 |
| 一直 `R5 status fault=true` | 徐不回 Status，或 seq 没回显 | 查徐的发送代码有没有写回同一连接 |
| 一直 `R5 no vision frame ever` | 欧阳没连上 / 没发过一帧 | 板上看 `[vision] 客户端接入` 有没有打印 |
| 一直 `R5 vision stale` | 欧阳发了但停了 / 帧率太低 | 视觉端 >3.3Hz 才够（超时 300ms） |
| 云图连不上 5002 | 防火墙 / 板子没起 board_main | 关专用网络防火墙；确认板上有 `[monitor] 监听` |
| 云图有点云但不动 | `--monitor-pts 0` | 去掉这个参数 |
| 车不动但日志正常 | 视觉没数据（R5 优先级最高） | 先让欧阳推一帧 |
| 板子时间不对 | 无 RTC | 不影响，时间戳只用于日志排序 |

---

## 6. 还需要和队友拍板的三件事

1. **板子 IP 和网段**（现在手册里用 `192.168.2.99` 占位）——三个人要在同一网段
2. **徐的 Status 字段是否同意最小集** `seq/ok/vbat/enc_l/enc_r`（不同意就改 `mock_motor.py` 和文档）
3. **`contract.py` 的三个标定常数**（`PWM_MAX=1000`、`TURN_GAIN=0.6`、方向引脚极性）
   必须实车标定后定死。`steering` 语义已定：**90=直行、135=左、45=右**，
   差速映射由 `control_to_diff()` 负责，徐不需要再自己映射。

---

## 7. 两套 mock 的关系（别用混）

| 脚本 | 扮演谁 | 端口 | 什么时候用 |
|---|---|---|---|
| `joint/mock_motor.py` | 徐的**电机侧** | 5001 | 配合 `board_main.py --motor`，验控制+watchdog |
| `integration/mock_board.py` | 整块**板子**（含差速翻译） | 8000 | 配合 `zheng/run.py --pynq`，验老的 sim 链路 |

`board_main.py` 是新架构（雷达+视觉+决策+控制全在板上），联调优先用它。
`run.py` 是郑的单机版（视觉走 stdin），只适合 `--source sim` 回放场景。

---

## 7. 文件清单

| 文件 | 谁用 | 作用 |
|---|---|---|
| `joint/board_main.py` | **板上** | 唯一入口：雷达 + 视觉TCP + 决策 + 控制TCP + 监控广播 |
| `joint/vision_server.py` | 板上（被 import） | 视觉 TCP 接收，转成 `VisionProvider.push()` |
| `joint/mock_vision.py` | 郑 / 欧阳 | 扮演视觉端，场景可切（也当格式参考） |
| `joint/mock_motor.py` | 郑 / 徐 | 扮演电机侧，回 Status + 打印差速（也当格式参考） |
| `integration/contract.py` | **三方共用** | 接口契约：steering→差速、YOLO→Vision JSON。**改这里要三方一起改** |
| `integration/radar_map_view.py` | 郑笔记本 | 云图；`--tcp HOST:PORT` 连板上监控 |
| `joint/联调手册.md` | 三方 | 本文 |
| `joint/_run_it.sh` | 郑 | 一键跑场景 A（决策链） |
| `joint/_run_net.sh` | 郑 | 一键跑 PC 端数据源自检（无窗口） |
| `joint/_run_monitor.sh` | 郑 | 一键验监控广播格式 |
| `joint/_run_live.sh` | 郑 | 真雷达 + 假电机（上车前最后一步） |

板上要拷的东西（最小集）：
```
~/car/zheng/                    ← 整个目录
~/car/joint/board_main.py
~/car/joint/vision_server.py
~/car/integration/contract.py   ← 只拷这一个文件（⚠️ 别在 joint/ 下留副本，两份必然漂移）
```
