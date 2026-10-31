# SD 卡制作与首次启动 · 操作清单

> 生成时间：2026-10-24 22:50
> 目标：把空卡做成 PYNQ 启动卡，让板子第一次跑起 Linux，并验证雷达

---

## 0. 当前状态（已完成的部分）

| 项 | 状态 |
|---|---|
| PYNQ 镜像 | ✅ 已解压并验证：`D:\dasanshang\fpga竞赛\_sd_image\ZYNQ-7020-2.7.0.img`（7.53 GB） |
| 镜像完整性 | ✅ MBR `0xaa55`；分区1 FAT32 130MB 可引导；分区2 ext4 7.41GB；含 BOOT.BIN / image.ub / boot.scr |
| SD 卡 | ⬜ 空卡 14.84 GB（当前已拔出，不在线） |
| 写盘工具 | ⬜ 本机没有 Win32DiskImager |

---

## 1. 写卡（两条路，选一条）

### 路线 A：自己用 Win32DiskImager（推荐）

1. 下载：`https://sourceforge.net/projects/win32diskimager/`（3 MB，绿色版解压即用）
2. 插读卡器，记下弹出的盘符
3. 打开 Win32DiskImager：
   - **Image File** → `D:\dasanshang\fpga竞赛\_sd_image\ZYNQ-7020-2.7.0.img`
   - **Device** → 选 SD 卡盘符（旁边应显示 ≈14.8 GB）
4. **Write** → 弹窗确认 → 等 10~20 分钟
5. 出现 "Write Successful" → 退出 → 安全弹出

### 路线 B：让 AI 用脚本写

插好读卡器后说一声，AI 会先列出所有物理磁盘做安全确认，再写入。

### ⚠️ 写卡时的三个坑

1. **选盘符看两遍**。写错盘会毁掉 C/D 盘数据。
2. **写完卡后 E: 会从 14.84 GB 变成约 130 MB —— 这是正常的。** 镜像含两个分区：FAT32 的 BOOT 区（Windows 看得见，130 MB）+ ext4 的 rootfs（Windows 看不见，7.41 GB）。看到变小别以为失败了又重写一遍。
3. 如果 Windows 弹「需要格式化」，**点取消**。

---

## 2. 首次启动（板子上）

| 步骤 | 操作 | 判据 |
|---|---|---|
| 1 | Micro SD（TF）卡插板子上**唯一的那个卡槽** | 整板只有一个卡槽，规格是 Micro SD |
| 2 | 拨码开关 **两个都拨到下面（OFF）** | = SD 卡启动（MIO[5:3]=110）⚠️ 不是 ON，见下方说明 |
| 3 | UART 线插**左侧 `USB_UART`** 口（Mini USB） | 不是 JTAG 口、不是 OTG 口 |
| 4 | 串口助手 115200 8N1 打开对应 COM 口 | — |
| 5 | 上电 | 几秒内应看到 u-boot 打印 |

**预期现象**：串口先刷 u-boot 日志，然后内核启动信息滚屏，最后出现登录提示。
**登录**：用户名 `xilinx`，密码 `xilinx`

### ⚠️ 拨码为什么是"两个都 OFF"（这条最容易搞反）

正点原子官方《FPGA 开发指南》原文（V3.6 手册 p116 / V3.7 手册 p113，两版一致）：

| 拨码 1（MIO4） | 拨码 2（MIO5） | 启动源 |
|---|---|---|
| ON | ON | **JTAG** |
| OFF | ON | NAND FLASH（本板无此芯片，无用） |
| ON | OFF | QSPI FLASH |
| **OFF** | **OFF** | **SD Card** ← 你要的 |

**反直觉的地方在于电平定义**：手册原话「OFF 表示断开，断开时由于上拉电阻电平状态为"1"；ON 表示联通接地，电平状态为"0"」。

所以 **ON = 逻辑 0，OFF = 逻辑 1**。SD Card 对应 MIO[5:3]=110，即 MIO5=1、MIO4=1 → 两个开关都断开 → **两个都拨到 OFF（下面）**。

> 我此前按"ON=1"推导，把 SD 判成了"两个都 ON"，方向是反的。已按官方手册修正。

### 启动失败怎么查（按顺序）

| 现象 | 含义 | 处理 |
|---|---|---|
| 串口 0 字节 | u-boot 都没起来 | 先查拨码（**两个都 OFF**）、卡是否插紧 |
| 有乱码 | 波特率不对 | 换 115200 / 1500000 试 |
| 停在 `mmc init` 或报 `Card did not respond` | 卡没被识别 | 拔出重插；卡可能写坏了 |
| 内核 panic 找不到 rootfs | ext4 分区没写进去 | 重写镜像 |

---

## 3. 起来后第一件事：验证 PL 活着

```bash
cat /sys/class/fpga_manager/fpga0/state
```

- 输出 `operating` → PL 已配置，bitstream 加载成功
- 输出 `unknown` → PL 没东西（PYNQ 启动时应该自动加载 base.bit，若失败查 `/var/log`）

再确认 PYNQ 环境：

```bash
python3 -c "import pynq; print(pynq.__version__)"
python3 -c "from pynq import Overlay; print('overlay ok')"
```

---

## 4. 验证雷达（你的核心任务）

```bash
# 1) 插上雷达 USB，确认枚举
ls /dev/ttyUSB*
dmesg | tail -5

# 2) 装依赖（PYNQ 自带 numpy，通常只差 pyserial）
sudo pip3 install pyserial

# 3) 跑最小连通测试
python3 - <<'EOF'
import serial, time
s = serial.Serial('/dev/ttyUSB0', 230400, timeout=1)
t0 = time.time(); n = 0
while time.time() - t0 < 3:
    n += len(s.read(4096))
print(f'3 秒收到 {n} 字节  →  {"OK" if n > 10000 else "异常"}')
EOF
```

**判据**：3 秒应收到 10 万字节以上（D6 是 230400 8N1 持续吐流）。

---

## 5. 把代码传到板子上

**方式一：MobaXterm 的 SFTP**（A 盘 `6_常用软件` 里有）
- 左边是本机，右边拖到 `/home/xilinx/`

**方式二：U 盘**
- PYNQ 会自动挂载到 `/media/`

**方式三：scp**
```bash
scp -r D:\dasanshang\fpga竞赛\zheng xilinx@192.168.2.99:/home/xilinx/
```

---

## 6. 之后的三件事（按优先级）

1. **立刻备份黄金镜像**：卡写成功后，把卡读回成 `.img` 存电脑。全队只有这一张卡，坏了就是几小时停工。
   ```
   Win32DiskImager → Image File 填新文件名 → Read
   ```
2. **跟徐确认 PL 工程怎么打包成 overlay**：PYNQ 用 `Overlay()` 加载 `.bit` + `.hwh`，不是直接烧 bit。
3. **转告欧阳**：`4_Source_Code\5_PYNQ_Design\ZYNQ_7020_PYNQ.zip` 里有 `10_ov5640_otsu/` 官方 OV5640 例程（notebook + vivado 工程），跟他的方案对得上。

---

## 附：本次用到的关键数据

- 镜像：`D:\dasanshang\fpga竞赛\_sd_image\ZYNQ-7020-2.7.0.img` = 8,090,591,232 字节
- 原始压缩包：`…资料盘(A盘)\4_Source_Code\5_PYNQ_Design\IMG\ZYNQ-7020-2.7.0.zip`
- 写完卡后 BOOT 分区大小：**130 MB**（266240 扇区）
- PYNQ 版本：2.7.0
