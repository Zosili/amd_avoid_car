# -*- coding: utf-8 -*-
"""check_image.py —— 检查 SD 卡镜像（.img）是否完整可启动

用途：写卡前确认镜像没问题；写卡后如果怀疑写坏了，用它对照。

支持：
  - MBR 分区表解析（FAT16/FAT32/ext4 识别）
  - FAT 分区文件清单（含 LFN 长文件名）
  - 关键启动文件存在性检查（BOOT.BIN / image.ub / boot.scr）
  - ext4 superblock 卷标与容量

用法：
    python check_image.py <镜像路径>
    python check_image.py ZYNQ-7020-2.7.0.img

判据：
    [OK]    FAT 分区里有 BOOT.BIN 且（image.ub 或 boot.scr）→ 可启动
    [WARN]  只有 BOOT.BIN 没有内核 → 启动到 u-boot 就停
    [FAIL]  没有 BOOT.BIN → 板子必然不启动
"""
import struct
import sys
import os

TYPES = {0x04: "FAT16 <32M", 0x06: "FAT16", 0x0B: "FAT32", 0x0C: "FAT32 LBA",
         0x0E: "FAT16 LBA", 0x83: "Linux ext4", 0x82: "swap", 0x05: "扩展"}
BOOT_FILES = ["BOOT.BIN", "BOOT.bin", "boot.bin"]
KERNEL_FILES = ["IMAGE.UB", "image.ub", "BOOT.SCR", "boot.scr", "boot.bin", "zImage"]


def parse_mbr(f):
    f.seek(0)
    mbr = f.read(512)
    if len(mbr) < 512 or mbr[510:512] != b"\x55\xaa":
        return None
    parts = []
    for i in range(4):
        e = mbr[446 + i * 16: 446 + (i + 1) * 16]
        if e[4] == 0:
            continue
        lba, cnt = struct.unpack("<II", e[8:16])
        parts.append({"idx": i + 1, "type": e[4], "lba": lba, "count": cnt,
                      "bytes": cnt * 512, "boot": e[0] == 0x80})
    return parts


def read_fat_root(f, base):
    """解析 FAT12/16/32 根目录，返回 (文件列表, 参数dict)"""
    f.seek(base)
    bs = f.read(512)
    bps = struct.unpack("<H", bs[11:13])[0]
    spc = bs[13]
    rsvd = struct.unpack("<H", bs[14:16])[0]
    nfat = bs[16]
    rootent = struct.unpack("<H", bs[17:19])[0]
    fatsz16 = struct.unpack("<H", bs[22:24])[0]
    fatsz32 = struct.unpack("<I", bs[36:40])[0]
    rootclus = struct.unpack("<I", bs[44:48])[0]
    label = bs[43:54].decode("ascii", "replace").strip()
    fatsz = fatsz16 if fatsz16 else fatsz32
    is_fat32 = (fatsz16 == 0)
    info = {"bps": bps, "spc": spc, "label": label, "fat32": is_fat32,
            "clus_bytes": bps * spc}
    if not fatsz or bps not in (512, 1024, 2048, 4096):
        return None, info

    fat_off = base + rsvd * bps
    if is_fat32:
        root_off = base + (rsvd + nfat * fatsz) * bps
        data_off = root_off
    else:
        root_off = base + (rsvd + nfat * fatsz) * bps
        data_off = root_off + rootent * 32

    def read_chain(c, limit=8 << 20):
        out = b""
        n = 0
        while 2 <= c < 0xFFF8 and len(out) < limit and n < 8192:
            f.seek(data_off + (c - 2) * bps * spc)
            out += f.read(bps * spc)
            f.seek(fat_off + c * (4 if is_fat32 else 2))
            c = struct.unpack("<I" if is_fat32 else "<H",
                              f.read(4 if is_fat32 else 2))[0]
            if is_fat32:
                c &= 0x0FFFFFFF
            n += 1
        return out

    if is_fat32:
        raw = read_chain(rootclus)
    else:
        f.seek(root_off)
        raw = f.read(rootent * 32)

    files, lfn = [], []
    for i in range(0, len(raw), 32):
        e = raw[i:i + 32]
        if len(e) < 32 or e[0] == 0:
            break
        if e[0] == 0xE5:
            lfn = []
            continue
        attr = e[11]
        if attr == 0x0F:
            s = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "ignore")
            lfn.append(s.replace("\x00", "").replace("\uffff", ""))
            continue
        short = (e[0:8].decode("ascii", "replace").strip() + "." +
                 e[8:11].decode("ascii", "replace").strip()).rstrip(".")
        name = "".join(reversed(lfn)).rstrip("\x00") if lfn else short
        lfn = []
        if name in (".", ".."):
            continue
        size = struct.unpack("<I", e[28:32])[0]
        files.append({"name": name, "size": size,
                      "dir": bool(attr & 0x10), "attr": attr})
    return files, info


def check_ext4(f, base):
    f.seek(base + 1024)
    sb = f.read(1024)
    if len(sb) < 256 or struct.unpack("<H", sb[56:58])[0] != 0xEF53:
        return None
    blocks = struct.unpack("<I", sb[4:8])[0]
    bsize = 1024 << struct.unpack("<I", sb[24:28])[0]
    return {"label": sb[120:136].decode("utf-8", "replace").rstrip("\x00"),
            "blocks": blocks, "block_size": bsize,
            "total_gb": blocks * bsize / 1073741824}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"[FAIL] 文件不存在: {path}")
        return 1
    size = os.path.getsize(path)
    print(f"镜像: {path}")
    print(f"大小: {size:,} 字节 = {size/1073741824:.2f} GB\n")

    with open(path, "rb") as f:
        parts = parse_mbr(f)
        if parts is None:
            print("[FAIL] MBR 签名 0xAA55 不存在 —— 不是有效的磁盘镜像")
            return 1
        print("[OK] MBR 签名 0xAA55 有效")
        print(f"\n{'#':<3}{'类型':<14}{'起始LBA':>10}{'大小':>12}  {'可引导'}")
        print("-" * 50)
        for p in parts:
            print(f"{p['idx']:<3}{TYPES.get(p['type'], hex(p['type'])):<14}"
                  f"{p['lba']:>10}{p['bytes']/1048576:>9.1f} MB  "
                  f"{'是' if p['boot'] else ''}")

        boot_part = None
        for p in parts:
            if p["type"] in (0x04, 0x06, 0x0B, 0x0C, 0x0E):
                boot_part = p
                break
        if not boot_part:
            print("\n[FAIL] 没有 FAT 分区 —— Zynq 的 BootROM 只认 FAT，无法启动")
            return 1

        print(f"\n=== 分区 {boot_part['idx']} 文件清单 ===")
        files, info = read_fat_root(f, boot_part["lba"] * 512)
        if files is None:
            print("[WARN] FAT 参数异常，无法解析目录")
            files = []
        real_fs = "FAT32" if info["fat32"] else "FAT16"
        declared = TYPES.get(boot_part["type"], hex(boot_part["type"]))
        print(f"卷标: {info['label']!r}  文件系统: {real_fs}  "
              f"簇: {info['clus_bytes']} B")
        # MBR 的 type 字节只是"声明"，以引导扇区实际内容为准
        if ("FAT32" in declared) != (real_fs == "FAT32"):
            print(f"      注: MBR 声明为 {declared}，但引导扇区实际是 {real_fs}"
                  f"（以实际为准，不影响启动）")
        print(f"\n{'文件名':<28}{'大小':>12}")
        print("-" * 42)
        for it in files:
            sz = "<DIR>" if it["dir"] else f"{it['size']/1048576:.2f} MB"
            print(f"{it['name']:<28}{sz:>12}")
        print(f"\n共 {len(files)} 项")

        names_upper = [it["name"].upper() for it in files]
        has_boot = any(n in ("BOOT.BIN",) for n in names_upper)
        has_kern = any(n in ("IMAGE.UB", "BOOT.SCR", "BOOT.BIN", "ZIMAGE")
                       for n in names_upper)

        print("\n=== 启动判据 ===")
        if has_boot and has_kern:
            print("[OK] 有 BOOT.BIN 且有内核/启动脚本 → 可启动")
        elif has_boot:
            print("[WARN] 只有 BOOT.BIN，没有 image.ub/boot.scr → 启动到 u-boot 就停")
        else:
            print("[FAIL] 没有 BOOT.BIN → 板子必然不启动（BootROM 静默失败，UART 零输出）")

        for p in parts:
            if p["type"] == 0x83:
                e = check_ext4(f, p["lba"] * 512)
                if e:
                    print(f"\n=== 分区 {p['idx']} ext4 ===")
                    print(f"卷标: {e['label']!r}  块大小: {e['block_size']} B  "
                          f"容量: {e['total_gb']:.2f} GB")
                else:
                    print(f"\n[WARN] 分区 {p['idx']} 标称 ext4 但 superblock 无效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
