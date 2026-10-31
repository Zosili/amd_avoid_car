# PYNQ 镜像烧录方法（完善版）· 2026-10-31

> 用途：把 `ZYNQ-7020-2.7.0.img`（8,090,591,232 B）可靠地写进 microSD，并**证明**写对了。
> 本文替代之前那份 `SD卡制作与首次启动_操作清单.md` 里的写卡部分（那份没写校验，也没处理 Windows 抢盘）。

## 0. 三条硬事实（都是今天实测踩出来的）

| # | 事实 | 后果 |
|---|---|---|
| 1 | 镜像的 MBR 一写到偏移 0，Windows 挂载管理器就会**重新发现分区并挂载 `PYNQ(E:)`** | 裸写中途被踢：`device not ready` / `EBADF`，在 6 GB / 121 MB / 64 MB 处随机失败 |
| 2 | `diskpart automount disable` 只挡"新插入的盘"，**挡不住同一块盘重扫后的重新挂载** | 光靠它不够 |
| 3 | 正解是**独占打开物理盘**（`dwShareMode=0`）或锁卷（`FSCTL_LOCK_VOLUME`+`FSCTL_DISMOUNT_VOLUME`） | 打开后 Windows 再也插不进来，写入稳定 |
| 4 | **更彻底：MBR 最后写** —— 主体写 `[1MB, 末尾]` 时磁盘 MBR 一直为 0，Windows 认为"空盘"不会挂载；写完后 0.2 秒补写 `[0, 1MB)` | 整个 8 GB 写入期间零干扰（v9 方案，实测 7.1 MB/s 连续无掉线） |

## 1. 官方方法（PYNQ 文档推荐）

PYNQ 官方《Writing an SD Card Image》Windows 部分明确写的是 **Win32DiskImager**：

- 以**管理员身份**运行 Win32DiskImager
- Image File 选 `.img`
- Device 选 SD 卡对应盘符（**看清容量，别选错盘**）
- 点 **Write**，等完成

参考：<https://pynq.readthedocs.io/en/latest/appendix/sdcard.html>
（Linux 用 `sudo dd bs=4M if=xxx.img of=/dev/sdX`；macOS 用 `dd` + `rdiskN`）

> Win32DiskImager 下载：SourceForge 项目页 <https://sourceforge.net/projects/win32diskimager/>
> ⚠️ 用命令行 `Invoke-WebRequest` 抓这个站会拿到 HTML 跳转页（今天就踩了），**请在浏览器里点 Download**。

## 2. 本项目自用方法（可脚本化、可校验）

工具都在 `D:\agentworkspace\02_contest\fpga_car\hw_ref\`：

| 脚本 | 作用 | 耗时 |
|---|---|---|
| `_preflight.py` | **烧录前 30 秒硬件自检**：独占句柄在 0 / 64 MB / 512 MB 各写 1 MB 再读回比对 | 30 s |
| `_write_v9.py` | **MBR 最后写**的整盘烧录（推荐）：先写 1MB..末尾，最后补 0..1MB | ≈19 min |
| `_write_v8.py` | 独占句柄 + 全量读回校验（v9 写完可用它复查） | 18 + 18 min |
| `_sd_compare.py` | 卡上 FAT 分区 5 个文件与官方 zip 逐字节 SHA-256 比对 | 5 s |
| `_rawio.py` | 定点裸读比对（MBR / BOOT.BIN / ext4 超级块，读循环已修正） | 10 s |

### 标准流程（按顺序，任一步不过就别往下走）

```powershell
# 0) 盘上不能有挂载的卷（独占打开的前提）
"select disk 1`r`nclean`r`nexit" | diskpart

# 1) 硬件自检（30 秒）—— 必须 3/3 PASS
python _preflight.py            # 需管理员

# 2) 烧录 + 全量校验（约 35 分钟）
python _write_v8.py             # 需管理员

# 3) 文件系统层复核：5 个文件哈希必须与 zip 一致
python _sd_compare.py
```

### 判据

| 步骤 | 通过标准 |
|---|---|
| preflight | `VERDICT: 3/3 PASS` |
| 写入 | `write done 7.53 GB`，无 FATAL |
| 全量校验 | `VERDICT: OK`（0 个块不一致） |
| 文件比对 | `BOOT.BIN / BOOT.SCR / IMAGE.UB / REVISION / BOOT.PY` 全部 `[SAME]` |
| 板上验证 | 串口出现 `xilinx@` 登录提示（最终判据） |

## 3. 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `device not ready` / `EBADF`，随机偏移 | Windows 中途挂载卷 | 用独占句柄（`_write_v8.py`）；先 `diskpart clean` |
| preflight 0/3，但 FAT 文件能读 | **卡只能读、写不进** | 换读卡器（最常见）；再不行换卡 |
| 前 100 MB 能写、之后全废 | 卡真实容量远小于标称（假卡/坏卡） | 换卡，别再试 |
| 写卡速度只有 6~7 MB/s | 廉价 USB2.0 读卡器（VID_14CD:1212 这类） | 换 USB3.0 读卡器，8 GB 只要 2~4 分钟 |
| 板子停在 `Kernel panic ... unknown-block(179,2)` | 根分区没有有效 ext4 | 重写卡；确认写完整 |
| 板子连 u-boot 都不出 | BOOT.BIN 坏了 | 重写卡 |

## 4. 烧完之后的板端检查

```bash
cat /sys/class/fpga_manager/fpga0/state     # 期望 operating
python3 -c "import pynq; print(pynq.__version__)"
ls -l /dev/ttyUSB0                          # 雷达（PL 侧或用前的 USB）
```

## 5. 附：为什么不用 `dd`/`Win32DiskImager` 也能行

`_write_v8.py` 用 `CreateFileW("\\.\PhysicalDrive1", GENERIC_READ|GENERIC_WRITE, dwShareMode=0, ...)`
独占打开，等价于 Linux 上先 `umount` 再 `dd`：Windows 挂载管理器拿不到句柄，就无法重扫/重挂，
写入因此稳定（实测 512 MB 连续写 7.1 MB/s 无掉线）。
