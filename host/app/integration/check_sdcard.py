# -*- coding: utf-8 -*-
"""
check_sdcard.py — 检查 SD 卡里有没有可启动的 Zynq 系统（郑用；PC 上跑）
=====================================================================
板子接 UART 却一个字节都不打印，最常见的原因是 **卡里没有 BOOT.BIN**：
没有 BOOT.BIN → FSBL 不跑 → PS 不启动 → 没有 Linux → UART 零输出。

用法：
    1. 把 SD 卡从板子上拔下来，插到电脑的读卡器里
    2. python check_sdcard.py
    3. 看输出里的判断

只读不写，不会动卡上的任何东西。

判据（Zynq 启动卡必须有的）：
    BOOT.BIN     FSBL + bitstream + u-boot 的打包，缺它必然不启动
    image.ub     Linux 内核 + 设备树
    boot.scr     u-boot 启动脚本（可选，有的镜像用 boot.bin 或没有）

PYNQ 镜像的布局：第一个分区 FAT32（Windows 能读，放上面三个文件），
第二个分区 ext4（Windows 读不了，放 /home/xilinx 等）。
所以"Windows 里只能看到一个小分区"是正常的。
"""
import ctypes
import os
import shutil
import string
import sys

KEY_FILES = ["BOOT.BIN", "image.ub", "boot.scr", "system.dtb", "system.bit",
             "boot.bin", "Image", "zImage", "rootfs.tar.gz"]
MARKERS = ["pynq", "xilinx", "zynq", "PYNQ", "Xilinx"]

DRIVE_TYPES = {0: "未知", 1: "无效", 2: "可移动", 3: "本地磁盘",
               4: "网络", 5: "光驱", 6: "内存盘"}


def drive_type(root):
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(root)
    except Exception:
        return 0


def volume_label(root):
    try:
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(
            root, name, 261, None, None, None, fs, 261)
        return name.value, fs.value
    except Exception:
        return "", ""


def main():
    print("=" * 66)
    print("  SD 卡启动性检查")
    print("=" * 66)

    found_any = False
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        t = drive_type(root)
        if t not in (2, 3):
            continue

        label, fs = volume_label(root)
        try:
            usage = shutil.disk_usage(root)
            total = usage.total / (1024 ** 3)
            free = usage.free / (1024 ** 3)
        except Exception:
            total = free = 0.0

        # 跳过系统盘（C:）和明显不是卡的本地盘，但保留小容量的可移动盘
        is_c = root.upper().startswith("C:")
        if t == 3 and (is_c or total > 200):
            continue

        try:
            entries = os.listdir(root)
        except Exception as e:
            print(f"\n[{root}] {DRIVE_TYPES.get(t, '?')}  {label}  {fs}  "
                  f"{total:.1f}GB  —— 读不了: {e}")
            print("  ⚠ Windows 读不了这个分区 → 很可能是 ext4（Linux 根分区）。")
            print("    PYNQ 镜像的 FAT32 启动分区应该还有另一个盘符，往下看。")
            found_any = True
            continue

        found_any = True
        print(f"\n[{root}] {DRIVE_TYPES.get(t, '?')}  卷标={label or '(无)'}  "
              f"格式={fs or '?'}  容量={total:.1f}GB  可用={free:.1f}GB")
        print(f"  根目录 {len(entries)} 项：")

        hits = []
        for name in sorted(entries)[:40]:
            p = os.path.join(root, name)
            try:
                sz = os.path.getsize(p) / (1024 ** 2) if os.path.isfile(p) else 0
                kind = "目录" if os.path.isdir(p) else f"{sz:.1f}MB"
            except Exception:
                kind = "?"
            mark = ""
            if name.upper() in [k.upper() for k in KEY_FILES]:
                mark = "   <<< 启动关键文件"
                hits.append(name)
            print(f"    {name:<32} {kind}{mark}")

        # 判断
        up = {n.upper() for n in entries}
        has_boot = "BOOT.BIN" in up
        has_kernel = "IMAGE.UB" in up or "IMAGE" in up or "ZIMAGE" in up
        has_marker = any(any(m.lower() in n.lower() for m in MARKERS) for n in entries)

        print("  " + "-" * 60)
        if has_boot and has_kernel:
            print("  ✓ 看起来是可启动的 Zynq 卡：BOOT.BIN + 内核都在")
            if has_marker:
                print("    并且有 pynq/xilinx 相关文件，大概率是 PYNQ 系镜像")
        elif has_boot and not has_kernel:
            print("  ⚠ 有 BOOT.BIN 但没有内核文件 —— 可能启动到 u-boot 就停了")
        elif not has_boot and entries:
            print("  ✗ 没有 BOOT.BIN —— 板子必然不启动")
            print("    这张卡要么是普通数据卡，要么只写了 rootfs 没写启动分区")
        else:
            print("  ✗ 空分区")

    if not found_any:
        print("\n没找到任何可移动盘。确认读卡器插好了、卡插进读卡器了。")

    print("\n" + "=" * 66)
    print("  说明：如果 Windows 弹出「需要格式化」的对话框，**点取消**，")
    print("        那说明是 ext4 分区，格式化会把系统删掉。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
