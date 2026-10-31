# 方案① 单网口实施步骤（视觉结果走串口线）

> 2026-10-24 21:20　｜　对应 `vision_serial.py`
> 目标：**只用一个网口**（PL 口传图像），视觉结果从网线改成串口线，零 Verilog 改动。

---

# 一、这个方案改什么、不改什么

| | 改不改 | 说明 |
|---|---|---|
| 欧阳的 Verilog（摄像头采集 + 图像 UDP） | ❌ 不动 | 一行都不用改 |
| 图像链路（PL 口 → PC） | ❌ 不动 | 还是 192.168.1.10:1234 |
| 郑的五规则决策 | ❌ 不动 | `machine.py` 完全不变 |
| 郑的抗误检 / 点云抗噪 | ❌ 不动 | 同上 |
| **视觉结果的传输方式** | ✅ 改 | UDP:5000 → 串口线 |
| **需要几个网口** | ✅ 改 | 2 个 → 1 个（只剩 PL 口） |

**一句话**：把"结果走 PS 网口"换成"结果走一根串口线"，别的全不动。

---

# 二、硬件清单

| 东西 | 数量 | 价格 | 备注 |
|---|---|---|---|
| USB-TTL 模块（CH340） | **2 个** | ~10 元/个 | 雷达已经占了 1 个，再买 1 个 |
| 杜邦线（母-母） | 3 根 | — | TX / RX / GND |
| USB 口 | 板子上 2 个 | — | 雷达 1 个 + 视觉 1 个，不够就加 USB HUB |

⚠️ **为什么必须两个 USB-TTL**：PC 的 USB 口和板子的 USB 口**都是 Host**，Host 对 Host 直接连线是不通的（没有设备端）。必须中间用两个 USB-TTL 做"设备-设备"转换。

---

# 三、接线（这是最容易错的一步）

```
┌──────────┐   USB 线    ┌────────────┐   3 根杜邦线      ┌────────────┐  USB 线  ┌──────────┐
│    PC    │────────────>│ USB-TTL A  │──────────────────>│ USB-TTL B  │─────────>│  板子    │
│  (COM7)  │             │  (CH340)   │  TX ──> RX        │  (CH340)   │          │ttyUSB1   │
└──────────┘             └────────────┘  RX <── TX        └────────────┘          └──────────┘
                                         GND ── GND
                                         VCC 不接！VCC 不接！VCC 不接！
```

**三条线的规则**：

| 模块 A | 模块 B | 说明 |
|---|---|---|
| TX | **RX** | 交叉 |
| RX | **TX** | 交叉 |
| GND | GND | 直连 |
| VCC/5V | **不接** | 两边各自从 USB 取电，接一起会烧 |

**如果不想用两个模块**，还有一条路：只用一个 USB-TTL，接到板子 PS 的 **UART1（MIO 排针）**上。但要在 Block Design 里使能 UART1 并把引脚约束到排针——这是徐的活，多花半天。

---

# 四、三个人分别做什么

## 欧阳（PC 侧）：改 3 行

`main_ryzenai.py` 里原来是这样：

```python
from vision_sender import VisionSender
sender = VisionSender("192.168.1.20", 5000, class_names=CLASS_NAMES)
...
sender.send(boxes, classes, scores, 640, 480)
```

改成：

```python
from vision_serial import SerialVisionSender
sender = SerialVisionSender("COM7", 460800, class_names=CLASS_NAMES)
...
sender.send(boxes, classes, scores, 640, 480)   # ← 参数完全一样，不用动
```

**只改类名和构造参数，`send()` 一个字母都不用改。** 两个类的接口是完全一样的。

⚠️ `COM7` 换成你实际看到的端口号（设备管理器 → 端口）。两块 USB-TTL 会有两个 COM 口，认准接在**模块 A**上的那个。

## 郑（板子侧）：命令行加一个参数

```bash
# 双网口方案（原来）
python3 run_on_board.py --port /dev/ttyUSB0 --vision udp

# 单网口方案（现在）
python3 run_on_board.py --port /dev/ttyUSB0 --vision serial --vision-serial /dev/ttyUSB1
```

代码一行不用改——`run_on_board.py` 已经把 `SerialVisionReceiver` 接进去了。

## 徐：不用动

前提是板子上有 2 个 USB 口。如果只有 1 个（雷达占了），要么加 USB HUB，要么走 UART1 排针方案。

---

# 五、四步验证（每步都要停一下）

## Step 0：PC 上自测，不需要任何硬件

```bash
cd D:/dasanshang/fpga竞赛/integration
python vision_serial.py --mode selftest
```

期望输出：

```
  发出 20 条 / 解析 20 行 / 坏JSON 0 / 拒收 0
  最终视觉状态: obstacle=True nearest=person conf=0.85
  断包恢复: OK（行数 20→22，坏JSON +1，误拒收 +0）
全部通过
```

这一步验证的是**协议层**（编码/解码/断包重同步），跟硬件无关。挂了就别往下走了。

## Step 1：只验链路，不接 YOLO

两个终端：

```bash
# 终端 1（板子上）
python3 vision_serial.py --mode recv --port /dev/ttyUSB1 --baud 460800

# 终端 2（PC 上）
python vision_serial.py --mode send --port COM7 --hz 10
```

期望板子侧输出：

```
[   10s] 行  100 10.0Hz 坏JSON  0 拒收  0 积压丢  0 | 最新: person (25ms 前)
```

**三条硬指标**：
- 频率 ≈ 10Hz（低于 7Hz 会告警）
- 坏JSON = 0
- 拒收 = 0

## Step 2：接上真 YOLO

欧阳把 `main_ryzenai.py` 改成用 `SerialVisionSender`，人站在摄像头前走动，看板子侧 `最新:` 的类别和 `conf` 有没有跟着变。

## Step 3：接上决策

```bash
python3 run_on_board.py --port /dev/ttyUSB0 --vision serial --vision-serial /dev/ttyUSB1 --motor mock
```

规则号应该随障碍变化。到这里方案①就通了。

---

# 六、SSH 怎么办（这个方案最大的坑）

单网口 = 只剩 PL 口 = **没有 SSH**。三个办法：

| 办法 | 怎么操作 | 适用 |
|---|---|---|
| **① 开发时临时插 PS 口** | 调试时两根网线都插，演示前拔掉 PS 口 | ⭐ 最实用 |
| ② 板载 console 串口登录 | 板子上的 USB 转串口（接 UART0）插到 PC，用串口终端登录 | 板子有这个口的话 |
| ③ 拔 SD 卡改代码 | 断电拔卡 → 读卡器 → 拷文件 → 插回 → 上电 | 最慢，应急用 |

**推荐 ①**：视觉走串口这件事本身和"PS 口插不插"完全独立。开发阶段插着 PS 口用 SSH，比赛演示时拔掉，两种模式共用同一份代码，只是命令行参数不同。

---

# 七、什么时候该选这个方案

```
有交换机 / 能买一个（60 块）
   └─> 用方案 A（双网口），别折腾

没有交换机，但手头已经有 2 个 USB-TTL
   └─> 用方案①

车上空间极度紧张，多一个巴掌大的交换机都塞不下
   └─> 用方案①
```

**老实说**：方案①的硬件成本（2 个 USB-TTL ≈ 20 块 + 3 根杜邦线）和交换机（60 块）差不多，但接线更麻烦、还丢了 SSH。**它真正的价值是"不用买新东西"和"车上少一个盒子"，不是"更简单"。**

---

# 八、踩坑清单

| 坑 | 现象 | 解法 |
|---|---|---|
| **Host-Host 直连** | 一根 USB 线连过去，板子完全没反应 | 必须两个 USB-TTL 背靠背 |
| **VCC 接上了** | 模块发烫、可能烧 | 只接 TX/RX/GND 三根 |
| **TX/RX 没交叉** | 能连上但收不到数据 | A 的 TX 接 B 的 RX |
| **波特率不一致** | 全是乱码（坏JSON 暴涨） | 两端都设 460800 |
| **`/dev/ttyUSB` 编号会变** | 这次 ttyUSB1，下次 ttyUSB0，程序报错 | 用 `ls -l /dev/serial/by-id/` 找稳定名字，传那个路径 |
| **抢了 console 口** | 登录的终端里突然出现 JSON | 别用 UART0 / 板载 console 那个 |
| **板子忙时串口积压** | 车对着空气躲（旧数据装成新的） | 代码已处理：积压 >2KB 时只保留最新一帧，看 `积压丢` 计数 |
| **选了雷达那个串口** | 雷达数据变成乱码 | 雷达是 `/dev/ttyUSB0`，视觉用 `/dev/ttyUSB1`，别搞混 |

## 关于 `/dev/ttyUSB` 编号漂移

USB 设备的编号是按**枚举顺序**分配的，两个 USB-TTL 插拔顺序不同，编号就换。解决办法：

```bash
ls -l /dev/serial/by-id/
# usb-1a86_USB_Serial-if00-port0 -> ../../ttyUSB0
# usb-1a86_USB_Serial-if00-port1 -> ../../ttyUSB1
```

用 `by-id` 的完整路径传进去，编号怎么变都不影响：

```bash
python3 run_on_board.py --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
                        --vision serial \
                        --vision-serial /dev/serial/by-id/usb-1a86_USB_Serial-if00-port1
```

---

# 九、带宽算一下（确认够用）

| 项 | 数值 |
|---|---|
| 一包 Vision JSON | ~200 字节（只有 nearest 一个目标） |
| 发送频率 | 10 Hz |
| 需要带宽 | 2 KB/s |
| 460800 波特能跑 | 46 KB/s |
| **余量** | **23 倍** |
| 一包传输耗时 | 4.3 ms |
| YOLO 推理本身 | 30~140 ms |

即使障碍物多到 JSON 涨到 1KB/包，460800 也还剩 4 倍余量。**带宽不是问题。**

用 115200 也能跑（5.7 倍余量），但没什么好处，直接用 460800。
