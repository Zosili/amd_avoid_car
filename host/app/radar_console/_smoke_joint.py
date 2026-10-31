# -*- coding: utf-8 -*-
"""_smoke_joint.py —— 联调区的全链路冒烟（窗口隐藏，不占桌面）

验的是「本机全套自检」按钮真的能起 3 个进程、日志能合并、停止能全部收掉。
不需要硬件：假电机 + 板入口(假雷达) + 假视觉。
"""
import sys
import time
import tkinter as tk

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_console as rc  # noqa: E402


def pump(root, secs, app, need=()):
    """转事件循环；need 里的字符串全出现就提前返回。"""
    t0 = time.time()
    while time.time() - t0 < secs:
        root.update()
        time.sleep(0.05)
        txt = app.txt.get("1.0", "end")
        if need and all(k in txt for k in need):
            return True
    return False


root = tk.Tk()
root.withdraw()                       # 不占用户桌面
app = rc.RadarConsole(root)
pump(root, 1.5, app)

print("联调控件:", [b.cget("text") for b in
                    (app.b_jview, app.b_jvis, app.b_jlocal)])
print("默认板子IP:", app.v_ip.get(), "| 默认场景:", app.v_scene.get())

app.joint_local()
ok = pump(root, 30, app, need=("已连上 127.0.0.1:5001", "客户端接入", "R4"))
txt = app.txt.get("1.0", "end")

checks = [
    ("3 个进程都起来了", len(app.jprocs) == 3),
    ("电机侧连上", "已连上 127.0.0.1:5001" in txt),
    ("视觉客户端接入", "客户端接入" in txt),
    ("决策走到 R4", "R4" in txt),
    ("没有 FAIL/Traceback", "Traceback" not in txt and "[FAIL]" not in txt),
]
print()
for name, good in checks:
    print(("[PASS] " if good else "[FAIL] ") + name)

app.stop()
time.sleep(0.5)
left = [p for p in app.jprocs if p.poll() is None]
print(("[PASS] 停止后无残留进程" if not left else f"[FAIL] 还有 {len(left)} 个没死"))

print("\n============ 控制台输出（末 1600 字）============")
print(txt.strip()[-1600:])
print("==============================================")

ok_all = ok and all(g for _, g in checks) and not left
print("[PASS] 联调区全链路正常" if ok_all else "[FAIL] 见上")
root.destroy()
sys.exit(0 if ok_all else 1)
