# A 盘镜像全盘点与选型结论

> 2026-10-25 实测。数据全部来自本地 A 盘文件解析，不是网上抄的。
> 结论先行：**用 PYNQ 2.7.0（7020 版）**，理由见第 3 节。

---

## 1. A 盘里到底有几套能启动的东西

不是只有 PYNQ。逐个数清楚，一共 **6 个候选**（其中 2 个是同一套的 7010/7020 两版）：

| # | 名称 | 路径 | 实测大小 | 形态 | 能跑 Python |
|---|---|---|---|---|---|
| **A** | **PYNQ 2.7.0 (7020)** | `4_Source_Code/5_PYNQ_Design/IMG/ZYNQ-7020-2.7.0.zip` | **7.53 GB** | 完整 SD 镜像 | ✅ 自带 |
| B | PYNQ 2.7.0 (7010) | 同目录 `ZYNQ-7010-2.7.0.zip` | 7.5 GB | 完整 SD 镜像 | ❌ 芯片不对 |
| C | 出厂自检 Navigator | `1_领航者ZYNQ开发板出厂综合测试/IMG/Navigator.rar` | 900 MB | 裸机 `.img` | ❌ 无系统 |
| D | 出厂自检 V3.7 | `9_...V3.7.../1_.../IMG/Navigator_3.7_user.zip` | 800 MB | 裸机 `.img` | ❌ 无系统 |
| E | PetaLinux 四套 | `4_Source_Code/3_Embedded_Linux/zynq_petalinux.rar` | 350 MB | 分体（4 个文件） | ⚠️ 极简 rootfs |
| F | 出厂 Linux rootfs | `4_Source_Code/3_Embedded_Linux/资源文件.rar` → `出厂镜像相关/` | 203 MB | **只有 rootfs，缺启动文件** | ⚠️ 不完整 |

**B 直接排除**：`ZYNQ-7010` 是给 xc7z010 芯片编译的，你的板子是 **xc7z020**。FSBL 里的 DDR 参数和位流都不匹配，写进去起不来。

---

## 2. PYNQ 镜像内部解剖（逐字节实测）

这部分是我直接解析 `ZYNQ-7020-2.7.0.img` 得到的，不是推测。

### 2.1 分区表

| 分区 | 类型 | 起始 LBA | 大小 | 卷标 | Windows 可见 |
|---|---|---|---|---|---|
| 1 | **FAT16**（标记 0x0c） | 8 | **130 MB** | `PYNQ` | ✅ 可见 |
| 2 | Linux ext4 | 268288 | **7.41 GB** | `root` | ❌ 不可见 |

> ⚠️ 注意是 **FAT16 不是 FAT32**（根目录固定 512 项、`FATSz16=136`、卷标直接写着 `FAT16`）。
> 这不影响使用，但如果哪天你要往 BOOT 区塞文件，记住根目录只能放 **512 个条目**。

### 2.2 BOOT 区文件清单（全部 6 项）

| 文件 | 大小 | 说明 |
|---|---|---|
| `BOOT.BIN` | **860,396 B（0.82 MB）** | FSBL + u-boot，**不含 bitstream** |
| `IMAGE.UB` | **5.62 MB** | 内核 + 设备树 + rootfs 描述（FIT 格式） |
| `BOOT.SCR` | 1,636 B | u-boot 启动脚本，支持 `mmc0` / `mmc1` / `jtag` 三种 boot_target |
| `BOOT.PY` | 小 | PYNQ 用户启动脚本模板（可自己加开机动作） |
| `REVISION` | 小 | **`Release 2025_08_27 59515a9`** |
| `PYNQ` | 0 | 卷标占位项 |

### 2.3 两个关键发现

**发现一：`BOOT.BIN` 只有 0.82 MB，说明里面没有 bitstream。**

正常的「FSBL + bit + u-boot」打包一般是 4~5 MB（bitstream 单独就 3~4 MB）。这里只有 0.82 MB，说明：

```
上电 → FSBL 只加载 u-boot → u-boot 加载 image.ub → Linux 起来
                                              ↓
                          PYNQ 的启动脚本从 rootfs 里加载 base.bit 进 PL
```

**这正是 PYNQ 能做热换 bitstream 的物理基础**——PL 不是在上电时被写死的，而是 Linux 起来之后由软件灌进去的。所以徐改完 Verilog，只要 `Overlay("new.bit")` 就能换，不用重烧卡、不用重打包 BOOT.BIN。

**发现二：`REVISION` 是 2025-08-27。**

不是 2021 年的老古董。PYNQ 2.7 是 LTS 分支，正点原子这个包是去年 8 月重新构建的。`bionic` 字符串出现在镜像 788 MB 处，证实底子是 **Ubuntu 18.04 LTS**。

---

## 3. 六维对比

| 维度 | A. PYNQ 2.7 | C/D. 出厂自检 | E. PetaLinux | F. 出厂 Linux rootfs |
|---|---|---|---|---|
| **Windows 上直接写卡** | ✅ Win32DiskImager 一步 | ✅ 同左 | ❌ 要 Linux 手动分区 | ❌ 缺 BOOT.BIN，做不出来 |
| **起来就有 Python3** | ✅ 自带 3.6 | ❌ 裸机无 OS | ⚠️ 有但极简 | ⚠️ 有 |
| **numpy / OpenCV** | ✅ **预装** | ❌ | ❌ 要自己交叉编译 | ⚠️ 可能有 |
| **apt / pip 装包** | ✅（源要改，见 4.1） | ❌ | ⚠️ 无 apt | ⚠️ |
| **徐热换 bitstream** | ✅ `Overlay()` 几秒 | ❌ 只能 JTAG | ❌ | ❌ |
| **Jupyter 网页写代码** | ✅ 浏览器直连 | ❌ | ❌ | ❌ |
| **体积 / 写卡时间** | 7.5 GB / 10~20 min | 0.9 GB / 3 min | 350 MB / 5 min + 手工 | — |
| **官方适配领航者** | ✅ 正点原子提供 | ✅ | ✅ | ⚠️ |

### E 和 F 为什么不行（说清楚，免得以后又想起它们）

**E. PetaLinux 那四套**（`1_customize_linux` / `2_linux_lcd` / `3_linux_lcd_qt` / `4_linux_driver_boot`）看起来最诱人——体积小、官方出品、四个功能版本。但：

- 它们是**分体式**：`BOOT.BIN` + `image.ub` + `boot.scr` + `rootfs.tar.gz`，要你在 SD 卡上手工建两个分区、分别拷文件。**Windows 建不了 ext4 分区**，必须开 Ubuntu 虚拟机。
- rootfs 是 **PetaLinux 的最小根文件系统**（busybox 风格）。`4_linux_driver_boot` 的 rootfs 压缩包只有 **6.9 MB**——里面连 `python3` 都未必有，更别说 numpy。
- 想装 numpy？得在 x86 上交叉编译 ARM 版，或者板子上自己编（几小时）。

**F. `出厂镜像相关/rootfs.tar.gz`（203 MB）** 是正点原子出厂 Linux 的完整根文件系统，内容确实全。但**同目录里没有 `BOOT.BIN` 和 `image.ub`**——它只有 rootfs 和 u-boot/内核**源码**（`atk-zynq-uboot-xlnx.tar.gz` 19 MB、`atk-zynq-linux-xlnx.tar.gz` 230 MB）。要用它得先自己编译内核和 u-boot，再打包 BOOT.BIN。这是「从源码重建系统」，不是「用镜像」。

> 💡 补充：正点原子还有一套**带桌面的完整 Ubuntu 镜像**，但只放在百度网盘，A 盘里没有。你以后如果想要图形桌面，去网盘下；现在跑雷达和 Python 不需要。

---

## 4. 选 PYNQ 的三个代价（必须提前知道）

### 4.1 apt 源已失效 —— 第一次 `apt update` 就会报 404

Ubuntu 18.04（bionic）已于 2023 年结束标准支持，官方把仓库迁到了 `old-releases`。板子起来后执行 `sudo apt update` 会看到：

```
E: The repository 'http://ports.ubuntu.com/ubuntu-ports bionic InRelease' ...
404  Not Found
```

**改法**（板子上执行，一条命令）：

```bash
sudo sed -i 's|http://ports.ubuntu.com/ubuntu-ports|http://old-releases.ubuntu.com/ubuntu|g; s|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' /etc/apt/sources.list
sudo apt update
```

> Zynq 是 **ARM 架构**，所以原本走的是 `ports.ubuntu.com`，不是 `archive.ubuntu.com`。上面这条命令两个都替换了，一次到位。

### 4.2 Python 是 3.6，新库装不上

Ubuntu 18.04 自带 Python 3.6.9。影响：

| 库 | 状态 |
|---|---|
| `pyserial` | ✅ 随便装 |
| `numpy` | ✅ 能装（会锁到 1.19.x，够用） |
| `opencv-python` | ⚠️ 只能装老版本，但 PYNQ 已经预装了 `cv2` |
| `scipy` | ✅ 能装 |
| 2024 年后的新库（要求 Python ≥3.8） | ❌ 装不上 |

**对你们这个项目没影响**：雷达解析只用 `pyserial` + 标准库，点云处理用 `numpy`，都在支持范围内。

### 4.3 空间：镜像 7.5 GB，卡 14.83 GB

PYNQ 镜像通常会在**首次启动时自动把 rootfs 扩展到整张卡**（7.41 GB → 约 14.7 GB），用 `df -h` 一查就知道。

如果没自动扩，手动扩一次：

```bash
sudo resize2fs /dev/mmcblk0p2
```

**无论扩没扩，7.4 GB 都够你们用**——除非要存大量录制的点云数据。

---

## 5. 写卡与首次启动

### 5.1 写卡

```
镜像：D:\dasanshang\fpga竞赛\_sd_image\ZYNQ-7020-2.7.0.img   (7.53 GB，已验证)
工具：Win32DiskImager
目标：SD 卡盘符（容量显示 ≈14.8 GB 的那个）
```

**写完后卡在 Windows 里会变成约 130 MB**，这是对的，别慌、别格式化。

### 5.2 首次启动

1. Micro SD（TF）卡插板子上**唯一的卡槽**
2. 拨码**两个开关都拨到下面（OFF）** = SD 卡启动（MIO[5:3]=110）
   > ⚠️ **ON = 接地 = 逻辑 0，OFF = 上拉 = 逻辑 1**。官方手册（V3.6 p116 / V3.7 p113）原文：
   > 「拨码 1 与 2 都设置为 OFF 时，启动源为 SD Card」。按"ON=1"推会得到相反结论。
3. UART 线插**左侧 `USB_UART`** 口
4. 上电，串口 **115200** 看日志
5. 登录：`xilinx` / `xilinx`

### 5.3 起来后按顺序验四件事

```bash
# ① PL 状态（有 bitstream 才会是 operating）
cat /sys/class/fpga_manager/fpga0/state

# ② 雷达是否枚举
ls /dev/ttyUSB*

# ③ Python 环境
python3 -c "import serial, numpy; print('serial', serial.VERSION, '| numpy', numpy.__version__)"

# ④ 修 apt 源（见 4.1），然后才能装东西
```

### 5.4 一个省事的调试方式

PYNQ 自带 **Jupyter Notebook**，板子起来后在 PC 浏览器打开：

```
http://<板子IP>:9090
```

密码 `xilinx`。你可以在浏览器里直接写 Python、看结果、画图，代码实际跑在板子上——不用每次 `scp` 传文件再 `ssh` 跑。调雷达解析这种活，这个方式效率高很多。

---

## 6. 一句话

**A 盘里只有 PYNQ 2.7.0（7020 版）这一套是「写完卡插上电就能跑 Python」的**，其余要么是裸机自检，要么是半成品，要么需要 Linux 环境手工组装。用它。

代价是 Ubuntu 18.04 偏老（apt 源要改一次、Python 3.6），但你们的依赖全在支持范围内。
