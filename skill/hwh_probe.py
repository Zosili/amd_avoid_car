#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hwh_probe.py —— 只读解析 .hwh，不碰 /dev/mem（安全）

这是板端接入任何 AXI 外设的**第一步**：先确认 bitstream 里到底有没有那个 IP、
基址是多少、关键参数对不对。做完这一步再谈读写寄存器。

为什么单独拆出来：对未时钟化（aclk 没跑）的 AXI slave 做任何一次寄存器读，
都会让 AXI 总线挂死，板子当场失联、只能断电重启。所以"先只读、后碰硬件"
必须分成两个命令，而不是一个脚本里顺手做掉。

用法：
    python hwh_probe.py motor.hwh
    python hwh_probe.py motor.hwh --expect uartlite --baud 230400

退出码：判据全通过 0，否则 1。
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET

# HWH 里的 IP 类型名 → 人类可读名
KNOWN = {
    "axi_uartlite": "AXI UART Lite（串口）",
    "axi_gpio": "AXI GPIO（寄存器）",
    "processing_system7": "Zynq PS",
    "axi_intc": "中断控制器",
    "xlconcat": "位拼接",
    "proc_sys_reset": "系统复位",
    "clk_wiz": "时钟向导",
}


def localname(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def parse(path):
    """返回 [{name, vlnv, base, params}]，兼容 2019~2024 各版 hwh 结构。"""
    tree = ET.parse(path)
    root = tree.getroot()
    ips = []

    for mod in root.iter():
        if localname(mod.tag) != "MODULE":
            continue
        name = mod.get("INSTANCE") or mod.get("FULLNAME") or mod.get("name") or "?"
        vlnv = mod.get("VLNV", "")
        base = None
        params = {}

        for child in mod.iter():
            ln = localname(child.tag)
            nm = child.get("NAME", "")
            val = child.get("VALUE")
            if val is None:
                continue
            if ln in ("PARAMETER", "PARAM"):
                params[nm] = val
                if nm.upper() in ("C_BASEADDR", "BASEADDR"):
                    try:
                        base = int(val, 0)
                    except ValueError:
                        pass
            elif ln == "MEMRANGE":
                if nm.upper() in ("C_BASEADDR", "BASEADDR") and base is None:
                    try:
                        base = int(val, 0)
                    except ValueError:
                        pass

        ips.append({"name": name, "vlnv": vlnv, "base": base, "params": params})

    return ips


def kind_of(ip):
    v = ip["vlnv"].lower()
    for k in KNOWN:
        if k in v:
            return k
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="只读解析 .hwh")
    ap.add_argument("hwh")
    ap.add_argument("--expect", help="必须存在的 IP 类型，如 uartlite")
    ap.add_argument("--baud", type=int, help="要求的波特率，如 230400")
    args = ap.parse_args()

    try:
        ips = parse(args.hwh)
    except ET.ParseError as e:
        sys.exit(f".hwh 不是合法 XML：{e}（文件可能没导全）")
    except FileNotFoundError:
        sys.exit(f"找不到 {args.hwh}")

    print(f"=== {args.hwh} 解析到 {len(ips)} 个模块 ===\n")
    has_ip = {}
    for ip in ips:
        k = kind_of(ip)
        has_ip.setdefault(k, []).append(ip)
        if ip["base"] is None and k == "unknown":
            continue
        base = f"@0x{ip['base']:08X}" if ip["base"] is not None else "@--"
        extra = ""
        if k == "axi_uartlite":
            baud = ip["params"].get("C_BAUDRATE", "?")
            freq = ip["params"].get("C_FREQ_HZ", "?")
            extra = f"  baud={baud}  C_FREQ_HZ={freq}"
        elif k == "axi_gpio":
            w = ip["params"].get("C_GPIO_WIDTH", "?")
            extra = f"  位宽={w}"
        print(f"  {ip['name']:<28s} {KNOWN.get(k, ip['vlnv'][:34]):<26s} {base}{extra}")

    # ---------- 判据 ----------
    fails = []
    if args.expect:
        want = args.expect.lower()
        got = [k for k in has_ip if want in k]
        if not got:
            fails.append(f"没有包含“{want}”的 IP —— bitstream 里根本没接这个外设")
    if args.baud:
        uarts = has_ip.get("axi_uartlite", [])
        if uarts:
            b = uarts[0]["params"].get("C_BAUDRATE")
            if b and str(b) != str(args.baud):
                fails.append(f"C_BAUDRATE={b}，要求 {args.baud}（要重出 bitstream）")
            f = uarts[0]["params"].get("C_FREQ_HZ")
            if f and str(f) not in ("100000000", "50000000"):
                fails.append(f"C_FREQ_HZ={f}，请确认它等于 s_axi_aclk 的实际频率，"
                             f"否则波特率会偏")
        else:
            fails.append("没有 axi_uartlite，无法做波特率检查")

    print("\n=== 判据 ===")
    if fails:
        for f in fails:
            print("  ✗ " + f)
        print("\n提醒：本脚本只读 .hwh，没有碰寄存器。修好 bitstream 后再跑读写测试。")
        return 1

    print("  ✓ 结构检查通过")
    print("\n下一步（会碰硬件，务必先确认 PL 已配置成功）：")
    print("  1. cat /sys/class/fpga_manager/fpga0/state   → 必须是 operating")
    print("  2. 短接 TX-RX 做回环，验证 IP/引脚/波特率")
    print("  3. 再接传感器，看字节率与帧率")
    return 0


if __name__ == "__main__":
    sys.exit(main())
