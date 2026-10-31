# -*- coding: utf-8 -*-
"""
preflight.py — 上板前自检（郑用；PC 和板子都能跑）
=================================================
回答两个问题，都给出**可量化的证据**，不靠感觉：

  1. 我的代码能不能上板？   → 环境 / 依赖 / 模块 / 串口权限 / 网络 / PL / AXI
  2. 雷达能不能正常工作？   → 真读 N 秒，统计帧率、一圈点数、有效点比例、三扇区读数

用法：
    python3 preflight.py                       # 全查（PC 上跑也行，AXI/PL 自动跳过）
    python3 preflight.py --port COM5           # 指定雷达口（Windows）
    python3 preflight.py --port /dev/ttyUSB0 --secs 8
    python3 preflight.py --no-radar            # 只查环境，不读雷达（雷达不在手上时用）
    python3 preflight.py --json out.json       # 结果存 JSON，方便发给队友

退出码：0 = 全过或只有警告；1 = 有硬失败项（照着"下一步"改完再跑）

判据（雷达正常的量化标准，来自 D6 实测）：
    帧率        ≈ 163 Hz      （400 点/圈 × 10.2 圈/s ÷ 25 点每帧）
    一圈覆盖    ≈ 360°        （覆盖不到 300° = 没在转，或被卡住）
    校验通过率  > 95%
    有效点比例  > 30%         （室内 12m 量程基本都能打到东西）
    弱回波比例  < 50%         （过高说明环境杂光/雨雾，或 DROP_WEAK_ECHO 需要放宽）
"""
import argparse
import json
import os
import platform
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

RADAR_BAUD = 230400
AXI_BASE = 0x43C00000
EXPECT_HZ = 163.0
EXPECT_COVER_DEG = 360.0


# ---------------------------------------------------------------- 报告器
class Report:
    """收集所有检查项，最后统一打印。先跑完再输出，避免中途刷屏打断读雷达。"""

    def __init__(self):
        self.items = []

    def add(self, level, name, detail="", fix=""):
        self.items.append({"level": level, "name": name, "detail": detail, "fix": fix})

    def ok(self, name, detail=""):
        self.add("ok", name, detail)

    def warn(self, name, detail="", fix=""):
        self.add("warn", name, detail, fix)

    def fail(self, name, detail="", fix=""):
        self.add("fail", name, detail, fix)

    def skip(self, name, detail=""):
        self.add("skip", name, detail)

    def info(self, name, detail=""):
        self.add("info", name, detail)

    def counts(self):
        c = {"ok": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0}
        for it in self.items:
            c[it["level"]] += 1
        return c

    def render(self):
        mark = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[ -- ]", "info": "[INFO]"}
        lines = []
        for it in self.items:
            lines.append(f"{mark[it['level']]} {it['name']}"
                         + (f"  —  {it['detail']}" if it["detail"] else ""))
            if it["fix"] and it["level"] in ("fail", "warn"):
                for ln in it["fix"].splitlines():
                    lines.append(f"         → {ln}")
        return "\n".join(lines)


def _is_board():
    """PYNQ / Zynq 板子判定：有 fpga_manager 或有 pynq 库"""
    return (os.path.exists("/sys/class/fpga_manager")
            or platform.machine() in ("armv7l", "aarch64"))


# ---------------------------------------------------------------- 1. 环境
def check_env(rep):
    rep.info("平台", f"{platform.system()} {platform.machine()}  Python {platform.python_version()}"
                     + ("  （板子）" if _is_board() else "  （PC）"))

    if sys.version_info < (3, 7):
        rep.fail("Python 版本", f"{platform.python_version()}，需要 ≥3.7",
                 "板子上的 PYNQ 镜像自带 3.6+；如果是 3.6，f-string 里的 = 语法不能用，"
                 "本目录代码已避免，但建议升到 3.7+")
    else:
        rep.ok("Python 版本", platform.python_version())

    # 依赖分必需/可选
    try:
        import serial
        rep.ok("pyserial", getattr(serial, "__version__", "已装"))
    except ImportError:
        rep.fail("pyserial 未安装", "雷达和视觉串口都靠它",
                 "有网：pip3 install pyserial\n"
                 "没网：PC 上 pip3 download pyserial -d ./whl → scp → "
                 "pip3 install --no-index --find-links=/home/xilinx/whl pyserial")

    try:
        import numpy
        rep.ok("numpy", numpy.__version__)
    except ImportError:
        rep.skip("numpy 未安装", "郑的代码除视觉外不用 numpy；PYNQ 镜像自带")

    if _is_board():
        try:
            import pynq
            rep.ok("pynq", getattr(pynq, "__version__", "已装"))
        except ImportError:
            rep.fail("pynq 未安装", "AXI 控电机必须用它",
                     "PYNQ 镜像自带；如果用的是普通 Ubuntu 镜像，"
                     "pip3 install pynq（还需要装 XRT，比较麻烦）")
    else:
        rep.skip("pynq", "PC 上没有，正常")


# ---------------------------------------------------------------- 2. 代码完整性
def check_code(rep):
    """能不能 import 全部模块 —— 上板后才发现缺文件最浪费时间"""
    mods = [
        ("protocol", "car/protocol.py", True),
        ("fusion.machine", "car/fusion/machine.py", True),
        ("radar.d6_protocol", "car/radar/d6_protocol.py", True),
        ("radar.radar_provider", "car/radar/radar_provider.py", True),
        ("vision.vision_provider", "car/vision/vision_provider.py", True),
        ("contract", "integration/contract.py", True),
        ("vision_receiver", "integration/vision_receiver.py", True),
        ("vision_serial", "integration/vision_serial.py", True),
    ]
    bad = []
    for name, path, required in mods:
        try:
            __import__(name)
        except Exception as e:
            bad.append((name, path, f"{type(e).__name__}: {e}"))
    if bad:
        rep.fail("模块导入", f"{len(bad)}/{len(mods)} 失败",
                 "\n".join(f"{n}（{p}）→ {e}" for n, p, e in bad)
                 + "\n检查：integration/ 和 car/ 是不是同一个父目录下；缺文件就 scp 补")
    else:
        rep.ok("模块导入", f"{len(mods)}/{len(mods)} 全部成功")

    # 契约版本要三方一致
    try:
        from contract import CONTRACT_VERSION
        rep.info("契约版本", f"v{CONTRACT_VERSION}")
    except Exception:
        pass

    # 决策阈值的关键键必须在（防止传了旧版 protocol.py）
    try:
        import protocol
        need = ["side_confirm_frames", "r2_confirm_frames", "vision_single_conf"]
        miss = [k for k in need if k not in protocol.CFG]
        if miss:
            rep.fail("抗误检参数缺失", f"CFG 里没有 {miss}",
                     "传上去的是旧版 car/protocol.py，重新 scp")
        else:
            rep.ok("抗误检参数", "三个键都在")
    except Exception as e:
        rep.skip("抗误检参数", f"读不到 CFG: {e}")


# ---------------------------------------------------------------- 3. 串口设备
def find_ports():
    """列出所有串口。返回 [(device, desc)]"""
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except Exception:
        return []


def check_ports(rep, want_port):
    ports = find_ports()
    if not ports:
        rep.fail("串口设备", "一个都没找到",
                 "① USB 线插了吗 ② Linux 上 lsusb 看有没有 CH340/CP210x\n"
                 "③ 板子上 dmesg | tail -20 看有没有 ttyUSB 枚举\n"
                 "④ 换个 USB 口（前面板的 USB 口供电可能不够）")
        return None
    desc = "，".join(f"{d}({s})" if s else d for d, s in ports)
    rep.ok("串口设备", f"{len(ports)} 个：{desc}")

    if want_port:
        if any(d == want_port for d, _ in ports):
            rep.ok("指定端口存在", want_port)
            return want_port
        rep.fail("指定端口不存在", f"{want_port} 不在列表里",
                 f"从上面这几个里挑一个：{[d for d, _ in ports]}")
        return None

    # 自动挑：优先 ttyUSB / COM
    cand = [d for d, _ in ports if "ttyUSB" in d or d.upper().startswith("COM")]
    if cand:
        rep.info("自动选中", cand[0] + "（想指定用 --port）")
        return cand[0]
    rep.warn("无法自动选端口", f"候选 {[d for d, _ in ports]} 都不像雷达口",
             "用 --port 手动指定")
    return None


# ---------------------------------------------------------------- 4. 雷达实测
def check_radar(rep, port, secs):
    """真读 secs 秒，统计帧率 / 一圈覆盖 / 有效点 / 三扇区"""
    if not port:
        rep.skip("雷达实测", "没有可用端口")
        return None
    try:
        import serial
    except ImportError:
        rep.skip("雷达实测", "pyserial 没装")
        return None

    try:
        import radar.d6_protocol as d6
    except Exception as e:
        rep.fail("雷达解析器", f"import 失败 {type(e).__name__}: {e}",
                 "d6_protocol.py 在 car/radar/ 下；确认 sys.path 和文件都在")
        return None

    try:
        ser = serial.Serial(port, RADAR_BAUD, timeout=0.5)
    except Exception as e:
        msg = str(e)
        hint = ("端口被占用 → 关掉串口助手 / 另一个 python 进程\n"
                "权限不够 → sudo usermod -aG dialout $USER 然后重新登录\n"
                "设备不存在 → 拔插一次 USB")
        rep.fail("打开串口", f"{port}: {msg}", hint)
        return None

    rep.ok("打开串口", f"{port} @ {RADAR_BAUD} 8N1")

    # 兜底发一次启动指令（部分固件上电不自动吐流）
    try:
        ser.write(d6.START_CMD_1)
        ser.flush()
    except Exception:
        pass

    n_ok = n_bad = 0
    n_pts = n_none = n_weak = 0
    deg_hit = bytearray(360)
    # ⚠️ 必须累积一整圈（400 点）再统计，和 LiveRadarProvider 的行为保持一致。
    #    单帧只有 25 点、覆盖 22.5°，前扇区（30°）里落不到几个点 ——
    #    拿单帧统计会得到"前扇区 0 点"，那是统计口径的错，不是雷达坏了。
    scan = []
    sectors = []           # 每次统计的结果，最后取中位数
    last_stat = 0.0
    t0 = time.time()
    while time.time() - t0 < secs:
        r = d6.read_frame(ser)
        if r is None:
            continue
        ct, n, body = r
        pts = d6.parse_frame_ex(ct, n, body)
        if pts is None:
            n_bad += 1
            continue
        n_ok += 1
        for ang, dist, it, weak in pts:
            n_pts += 1
            deg_hit[int(ang) % 360] = 1
            if weak:
                n_weak += 1
            if dist is None:
                n_none += 1
        scan.extend(pts)
        if len(scan) > 400:
            scan = scan[-400:]
        # 攒满约 3/4 圈才开始统计，之后按 10Hz 采样（与决策频率一致）
        now = time.time()
        if len(scan) >= 300 and now - last_stat >= 0.1:
            sectors.append(d6.scan_stats(scan))
            last_stat = now
    dt = time.time() - t0
    try:
        ser.close()
    except Exception:
        pass

    if n_ok == 0:
        rep.fail("雷达无数据", f"{dt:.1f}s 内收到 0 个合法帧",
                 "① 线序：雷达红(TXD) → 板子 RXD，必须交叉\n"
                 "② 雷达上电了吗（黄=5V 白=GND，量一下有没有 5V）\n"
                 "③ 波特率对不对（D6 是 230400）\n"
                 "④ 换根 USB 线，有的线只供电不传数据")
        return None

    hz = n_ok / dt
    cover = sum(deg_hit)
    tot = n_ok + n_bad
    cr = 100.0 * n_ok / tot if tot else 0.0
    valid = 100.0 * (n_pts - n_none) / n_pts if n_pts else 0.0
    weakp = 100.0 * n_weak / n_pts if n_pts else 0.0

    # 帧率
    if hz >= 0.7 * EXPECT_HZ:
        rep.ok("帧率", f"{hz:.1f} Hz（期望 ≈{EXPECT_HZ:.0f}）")
    elif hz >= 0.3 * EXPECT_HZ:
        rep.warn("帧率偏低", f"{hz:.1f} Hz，期望 ≈{EXPECT_HZ:.0f}",
                 "USB 转串口芯片带宽不够（CH340 在 230400 下偶尔丢字节）\n"
                 "或雷达转速被降过（发过 0F 指令？）")
    else:
        rep.fail("帧率过低", f"{hz:.1f} Hz，期望 ≈{EXPECT_HZ:.0f}",
                 "多半是丢帧严重：换 FT232 芯片的转接板，或降低线长")

    # 一圈覆盖
    if cover >= 300:
        rep.ok("一圈覆盖", f"{cover}°（{cover/EXPECT_COVER_DEG*100:.0f}%）")
    elif cover >= 180:
        rep.warn("一圈覆盖不足", f"{cover}°",
                 "雷达在转但漏了大半圈：检查供电电流（转起来瞬间掉压）")
    else:
        rep.fail("雷达没在转", f"只覆盖 {cover}°",
                 "① 雷达电机没供电（黄线 5V 电流不够）\n"
                 "② 雷达被卡住了\n③ 曾经发过停止指令 AA55F50A，重新上电")

    # 校验通过率
    if cr >= 95:
        rep.ok("校验通过率", f"{cr:.1f}%（{n_ok} 好 / {n_bad} 坏）")
    elif cr >= 80:
        rep.warn("校验通过率偏低", f"{cr:.1f}%",
                 "串口误码：缩短 USB 线、远离电机电源线、降低波特率试试")
    else:
        rep.fail("校验通过率过低", f"{cr:.1f}%",
                 "波特率不对的可能性最大（D6 是 230400，别用 115200）")

    # 有效点比例
    if valid >= 30:
        rep.ok("有效点比例", f"{valid:.1f}%（{n_pts} 点采样）")
    elif valid >= 10:
        rep.warn("有效点偏少", f"{valid:.1f}%",
                 "前方太空旷（超 12m 量程）属正常；室内应该 >30%\n"
                 "如果室内还这么低，检查雷达镜片有没有脏/贴膜")
    else:
        rep.fail("几乎无有效回波", f"{valid:.1f}%",
                 "镜片脏 / 雷达朝墙外 / 距离全超 12m")

    # 弱回波比例
    if weakp < 50:
        rep.ok("弱回波比例", f"{weakp:.1f}%（DROP_WEAK_ECHO={d6.DROP_WEAK_ECHO}）")
    else:
        rep.warn("弱回波比例过高", f"{weakp:.1f}%",
                 "雨雾/强光/深色吸光物体会大量产生弱回波\n"
                 "调试时可临时设 DROP_WEAK_ECHO=False 看原始点")

    # 三扇区（整圈统计，取中位数抗瞬时抖动）
    if not sectors:
        rep.warn("整圈样本不足", f"{dt:.1f}s 内没攒够一圈",
                 "一圈 400 点约需 2.5s，把 --secs 调到 6 以上再跑")
        fr = lf = rt = None
    else:
        def vals_of(key):
            return sorted(s[key][0] for s in sectors if s[key][0] is not None)

        def med(key):
            v = vals_of(key)
            return v[len(v) // 2] if v else None

        def spread(key):
            v = vals_of(key)
            return (v[-1] - v[0]) if len(v) >= 2 else 0.0

        fr, lf, rt = med("front"), med("left"), med("right")
        fmt = lambda v: "无回波" if v is None else f"{v:.2f}m"
        rep.info("三扇区读数", f"前={fmt(fr)}  左={fmt(lf)}  右={fmt(rt)}"
                              f"   （{len(sectors)} 次整圈统计）")

        pts_f = sorted(s["front"][1] for s in sectors)
        pmed = pts_f[len(pts_f) // 2]
        # 前扇区 30° / 步进 0.9° ≈ 33 点/圈；远低于这个数说明那片区域打不到东西
        if pmed >= 20:
            rep.ok("前扇区点数", f"{pmed} 个/圈（30° 应约 33 个）")
        elif pmed >= 5:
            rep.warn("前扇区点数偏少", f"{pmed} 个/圈，期望约 33",
                     "正前方太空旷（超 12m）属正常；室内应该更多\n"
                     "如果正前方明明有墙却这么少，查 SECTOR_FRONT 角度定义")
        else:
            rep.warn("前扇区几乎无点", f"{pmed} 个/圈",
                     "0° 方向可能被自己的外壳/USB 线挡住，或 ZERO_OFFSET_DEG 需要标定\n"
                     "跑 car/calibrate_zero.py --survey 20")

        if fr is not None:
            sp = spread("front")
            if sp <= 0.3:
                rep.ok("前扇区稳定性", f"极差 {sp*100:.0f}cm（静止场景）")
            elif sp <= 1.0:
                rep.warn("前扇区有抖动", f"极差 {sp*100:.0f}cm",
                         "轻微抖动正常（人/物体在动）；静止场景超过 1m 要查抗噪参数")
            else:
                rep.warn("前扇区抖动大", f"极差 {sp:.2f}m",
                         "抗噪没完全压住。可把 SECTOR_ROBUST_K 提到 3，"
                         "或确认 DROP_WEAK_ECHO=True")

    if fr is None:
        rep.warn("前扇区无回波", "正前方 12m 内没有东西",
                 "把雷达对着墙再跑一次；如果对墙还是无回波，就是前扇区角度定义和实际装反了")
    return {"hz": hz, "cover": cover, "checksum": cr, "valid": valid,
            "weak": weakp, "front": fr, "left": lf, "right": rt,
            "stats_n": len(sectors)}


# ---------------------------------------------------------------- 5. PL / AXI
def check_pl(rep):
    if not _is_board():
        rep.skip("PL 状态", "PC 上没有 fpga_manager")
        rep.skip("AXI 电机寄存器", "PC 上没有 PL")
        return
    p = "/sys/class/fpga_manager/fpga0/state"
    if not os.path.exists(p):
        rep.fail("PL 状态", f"{p} 不存在",
                 "内核没使能 FPGA manager；PYNQ 镜像应该自带，"
                 "如果是自制镜像要开 CONFIG_FPGA_MGR_ZYNQ_FPGA")
        return
    try:
        st = open(p).read().strip()
    except Exception as e:
        rep.fail("PL 状态", f"读不了: {e}")
        return
    if st == "operating":
        rep.ok("PL 状态", "operating（bit 已加载）")
    else:
        rep.fail("PL 状态", st,
                 "徐的 bit 没烧进去。三条路：\n"
                 "① 让徐 JTAG 现场补烧（最快，3 分钟）\n"
                 "② 板子上 Overlay('/home/xilinx/design_1.bit') 热加载\n"
                 "③ 重做 SD 卡的 BOOT.BIN（最慢，别先做这个）")
        return

    try:
        from pynq import MMIO
    except Exception as e:
        rep.skip("AXI 电机寄存器", f"pynq 不可用: {e}")
        return
    try:
        m = MMIO(AXI_BASE, 0x1000)
        v = m.read(0x18)
    except Exception as e:
        rep.fail("AXI 访问", f"MMIO(0x{AXI_BASE:08X}) 失败: {e}",
                 "PL 里的 AXI 从机地址可能不是 0x43C00000，"
                 "去 Vivado 的 Address Editor 抄实际地址，然后用 --axi-base 传")
        return
    if v == 0xFFFFFFFF:
        rep.fail("AXI 读全 1", f"0x18 = 0x{v:08X}",
                 "地址上没人应答 = PL 里没有这个从机。\n"
                 "① 地址错了（Address Editor 核对）\n"
                 "② 徐的 motor_axi 没接到 PS 的 M_AXI_GP0\n"
                 "③ bit 是旧的（不含电机控制器）")
    elif v == 0:
        rep.warn("AXI 读到 0", "0x18 = 0x0（STATUS 全 0）",
                 "有应答但状态位全 0。可能是正常的初始状态，"
                 "写一次 0x00=1 使能后再读，期望 bit2 置位")
    else:
        rep.ok("AXI 电机寄存器", f"0x18 = 0x{v:08X}（有应答）")


# ---------------------------------------------------------------- 6. 网络
def check_net(rep, vis_port):
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    if ips:
        rep.info("本机 IP", "，".join(ips))
        board_like = [i for i in ips if i.startswith("192.168.1.") or i.startswith("192.168.2.")]
        if board_like:
            rep.ok("网口 IP", f"{board_like[0]}（在工程网段内）")
        else:
            rep.warn("网口 IP 不在工程网段", f"{ips}",
                     "PS 口应该是 192.168.1.20/24（双网口方案）\n"
                     "sudo ip addr add 192.168.1.20/24 dev eth0\n"
                     "⚠️ 这是临时生效，重启就没了")
    else:
        rep.warn("没有非环回 IP", "网线没插或网口没起",
                 "sudo ip link set eth0 up")

    # UDP 端口能不能 bind（不真收，只测占用）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", vis_port))
        s.close()
        rep.ok("UDP 端口", f":{vis_port} 可绑定")
    except Exception as e:
        rep.fail("UDP 端口被占", f":{vis_port}: {e}",
                 "fuser -k 5000/udp 或换个端口 --vision-port 5001")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="雷达串口；不给就自动找")
    ap.add_argument("--secs", type=float, default=5.0, help="雷达采样秒数")
    ap.add_argument("--no-radar", action="store_true", help="跳过雷达实测（雷达不在手上）")
    ap.add_argument("--vision-port", type=int, default=5000)
    ap.add_argument("--json", default=None, help="结果另存 JSON")
    a = ap.parse_args()

    print("=" * 66)
    print("  preflight · 上板前自检")
    print("=" * 66)

    rep = Report()
    check_env(rep)
    check_code(rep)
    port = check_ports(rep, a.port)

    radar = None
    if a.no_radar:
        rep.skip("雷达实测", "--no-radar")
    else:
        print(f"\n  正在读雷达 {a.secs:.0f} 秒（手放前面挡一下能看出变化）…\n")
        radar = check_radar(rep, port, a.secs)

    check_pl(rep)
    check_net(rep, a.vision_port)

    print(rep.render())

    c = rep.counts()
    print("-" * 66)
    print(f"  通过 {c['ok']}   警告 {c['warn']}   失败 {c['fail']}   跳过 {c['skip']}")

    # 下一步
    if c["fail"]:
        print("\n  下一步：按上面 [FAIL] 后面的 → 逐条修，改完重跑本脚本。")
        print("          别急着上板 —— 这些在 PC 上就能全修好。")
    elif c["warn"]:
        print("\n  下一步：警告项可以先带着上板，但要在日志里记一笔。")
    else:
        print("\n  下一步：环境没问题。")
        if radar and radar.get("front") is not None:
            print(f"          雷达前扇区 {radar['front']:.2f}m —— 可以跑 run_on_board.py 了：")
            print(f"          python3 run_on_board.py --port {port} --vision mock --motor mock")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"items": rep.items, "radar": radar,
                       "platform": platform.platform()}, f, ensure_ascii=False, indent=2)
        print(f"\n  结果已存 {a.json}（可以直接发给队友）")

    return 1 if c["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
