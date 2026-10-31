# -*- coding: utf-8 -*-
"""
run_on_board.py — 板子 PS 上的主程序（郑用，在 PYNQ 的 Linux 里跑）
==================================================================
这是 `car/run.py --source live` 的"上车版"。为什么另写一个而不改 run.py：
  · run.py 的 live 模式把视觉当 stdin 输入、电机走 TCP —— 那是**没板子时的替身**
  · 板子上视觉走 UDP、电机走 AXI —— 数据源和出口都换了

本文件只做"接线"，决策逻辑仍然调 car/fusion/machine.py，一行没改。

用法（在板子上）：
    python3 run_on_board.py --port /dev/ttyUSB0 --motor mock
        # 先跑 mock：只打印 PWM，验证雷达 + 视觉链路通不通，不碰硬件
    python3 run_on_board.py --port /dev/ttyUSB0 --motor axi --axi-base 0x43C00000
        # 真控电机（基地址从 Vivado 的 Address Editor 里抄）

依赖（板子上一次性装）：
    pip3 install pyserial
    # pynq 库是 PYNQ 镜像自带的，不用装
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "car"))

import protocol                                          # noqa: E402
from fusion.machine import DecisionMachine               # noqa: E402
from radar.radar_provider import LiveRadarProvider       # noqa: E402
from vision.vision_provider import VisionProvider        # noqa: E402
from contract import control_to_diff, CONTRACT_VERSION   # noqa: E402
from vision_receiver import VisionReceiver               # noqa: E402
from vision_serial import SerialVisionReceiver           # noqa: E402

CYCLE_MS = 100            # 决策 10Hz


class MotorBridge:
    """郑的 control → 徐的 AXI 寄存器

    寄存器映射（与 integration/motor_axi.v 对应，改一处必须改两处）：
        0x00 CTRL      W  bit0=使能  bit1=清急停
        0x04 SPEED_L   W  有符号 16 位，-1000~+1000
        0x08 SPEED_R   W  同上
        0x0C HEARTBEAT W  每周期写一次递增数；PL 侧 300ms 没变化就硬件急停
        0x10 ENC_L     R  左编码器计数
        0x14 ENC_R     R  右编码器计数
        0x18 STATUS    R  bit0=看门狗超时 bit1=急停中
    """

    def __init__(self, backend="mock", base=0x43C00000, verbose=False):
        self.backend = backend
        self.verbose = verbose
        self.mmio = None
        self.hb = 0
        self.count = 0
        if backend == "axi":
            from pynq import MMIO                     # 只在板子上有
            self.mmio = MMIO(base, 0x1000)
            self.mmio.write(0x00, 0x1)                # 使能
            print(f"[motor] AXI 已连 基地址 0x{base:08X}")

    def apply(self, ctrl):
        m = control_to_diff(ctrl)
        self.hb = (self.hb + 1) & 0xFFFFFFFF
        self.count += 1

        if self.mmio is not None:
            # 负数按 16 位补码写进去，Verilog 侧用 $signed 读
            self.mmio.write(0x04, m["pwm_l"] & 0xFFFF)
            self.mmio.write(0x08, m["pwm_r"] & 0xFFFF)
            self.mmio.write(0x0C, self.hb)

        if self.verbose:
            print(f"  [{self.count:5d}] {ctrl['action']:8s} "
                  f"pwm_l={m['pwm_l']:5d} pwm_r={m['pwm_r']:5d}  {m['why']}")
        return m

    def stop(self):
        if self.mmio is not None:
            self.mmio.write(0x04, 0)
            self.mmio.write(0x08, 0)

    def status(self):
        if self.mmio is None:
            return None
        v = self.mmio.read(0x18)
        return {"watchdog_timeout": bool(v & 1), "estop": bool(v & 2)}


class MockRadar:
    """没接雷达时用。模拟"前方 3m 有面墙慢慢靠近到 0.4m"，
    用来单独验证 视觉链路 + 决策 + 电机 这三段。"""

    def __init__(self, period_s=8.0):
        self.t0 = time.time()
        self.period = period_s

    def poll(self):
        el = (time.time() - self.t0) % self.period
        front = 3.0 - (el / self.period) * 2.8        # 3.0m → 0.2m
        return protocol.make_radar_json(max(front, 0.15), 2.0, 2.0)

    def close(self):
        pass


class MockVision:
    """没接 PC 的 YOLO 时用。永远报"无障碍"，只为了让 R5 不触发。
    ⚠️ 只能用来测链路，不能用来验证安全性（真实场景必须用 --vision udp）。"""

    def poll(self, now=None):
        return {"version": 1, "timestamp": protocol.now_ms(),
                "obstacle": False, "t": protocol.now_ms()}

    def push(self, raw):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radar", choices=["live", "mock"], default="live")
    ap.add_argument("--vision", choices=["udp", "serial", "mock"], default="udp",
                    help="udp=双网口方案；serial=单网口方案（结果走串口线）；"
                         "mock 只用于测链路，永远报无障碍")
    ap.add_argument("--port", default="/dev/ttyUSB0", help="雷达串口")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--vision-port", type=int, default=5000, help="收 PC 视觉结果的 UDP 端口")
    ap.add_argument("--vision-serial", default="/dev/ttyUSB1",
                    help="--vision serial 时用的串口（别用 console 那个）")
    ap.add_argument("--vision-baud", type=int, default=460800)
    ap.add_argument("--motor", choices=["mock", "axi"], default="mock")
    ap.add_argument("--axi-base", default="0x43C00000")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    print(f"run_on_board  contract v{CONTRACT_VERSION}")
    print("  雷达   " + (f"{a.port} @ {a.baud}" if a.radar == "live" else "mock（假数据）"))
    print("  视觉   " + {"udp": f"UDP :{a.vision_port}（双网口方案）",
                         "serial": f"{a.vision_serial} @ {a.vision_baud}（单网口方案）",
                         "mock": "mock（永远无障碍）"}[a.vision])
    print(f"  电机   {a.motor}" + (f" @ {a.axi_base}" if a.motor == "axi" else "（只打印）"))

    # ---- 三路数据源 ----
    radar_p = LiveRadarProvider(port=a.port, baud=a.baud) if a.radar == "live" else MockRadar()
    if a.vision == "udp":
        vision_p = VisionProvider(on_drop=lambda w: print(f"  [vision drop] {w}"))
        rx = VisionReceiver(a.vision_port, vision_p).start()
    elif a.vision == "serial":
        # 方案①：视觉结果走一根 USB-TTL 串口线，不需要第二个网口
        vision_p = VisionProvider(on_drop=lambda w: print(f"  [vision drop] {w}"))
        try:
            rx = SerialVisionReceiver(a.vision_serial, a.vision_baud, vision_p).start()
        except Exception as e:
            print(f"  ✗ 打不开视觉串口 {a.vision_serial}: {e}")
            print("    查三件事：① ls /dev/ttyUSB* 设备在不在 ② USB-TTL 插没插")
            print("    ③ 是不是选错口了（雷达占一个，别抢同一个）")
            sys.exit(1)
    else:
        vision_p = MockVision()
        rx = None
    motor = MotorBridge(a.motor, int(a.axi_base, 16), a.verbose)

    machine = DecisionMachine()
    log_dir = os.path.join(HERE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = open(os.path.join(log_dir, time.strftime("board_%Y%m%d_%H%M%S.jsonl")),
               "a", encoding="utf-8")

    print("\n开始循环（Ctrl-C 停车退出）\n")
    n = 0
    try:
        while True:
            t0 = protocol.now_ms()
            radar = radar_p.poll()
            vision = vision_p.poll()
            ctrl, rule, why = machine.step(radar, vision, fault=False, now=t0)

            m = motor.apply(ctrl)
            log.write(json.dumps({"t": t0, "rule": rule, "why": why,
                                  "control": ctrl, "pwm": [m["pwm_l"], m["pwm_r"]]},
                                 ensure_ascii=False) + "\n")
            log.flush()

            n += 1
            if n % 10 == 0:                       # 每秒一行摘要，别刷屏
                fr = radar.get("front_distance") if radar else None
                fs = "无回波" if fr is None else f"{fr:.2f}m"
                st = motor.status()
                ex = f" 状态={st}" if st else ""
                print(f"[{n:5d}] {rule:3s} {ctrl['action']:8s} 前={fs:>7s} "
                      f"pwm=({m['pwm_l']:5d},{m['pwm_r']:5d}){ex}", flush=True)

            # 固定周期（sleep 扣掉本周期已用时间）
            dt = CYCLE_MS - (protocol.now_ms() - t0)
            if dt > 0:
                time.sleep(dt / 1000.0)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C")
    except Exception as e:
        print(f"\n异常: {e}")
    finally:
        motor.stop()                              # 无论如何先停车
        radar_p.close()
        if rx is not None:
            rx.stop()
        log.close()
        print(f"已停车，共 {n} 个决策周期")


if __name__ == "__main__":
    main()
