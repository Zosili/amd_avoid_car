# -*- coding: utf-8 -*-
"""GUI 全链路冒烟：窗口隐藏运行，依次测 刷新串口 → 计算视野 → 体检 → 启动自检"""
import sys
import time
import tkinter as tk

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_console as rc  # noqa: E402


def pump(root, secs, until=None, app=None):
    t0 = time.time()
    while time.time() - t0 < secs:
        root.update()
        time.sleep(0.05)
        if until and app and until in app.txt.get("1.0", "end"):
            return True
    return False


root = tk.Tk()
root.withdraw()                      # 不占用户桌面
app = rc.RadarConsole(root)
pump(root, 4.0)                      # 等串口列表刷新
print("串口下拉值:", app.cmb_port.cget("values"), "| 选中:", app.port_var.get())

app.geom()
ok1 = pump(root, 30, "净空", app)
print("几何计算完成:", ok1)

app.probe()
ok2 = pump(root, 45, "12 扇区", app)
print("体检完成:", ok2)

app.launch(["--selftest"], "自检")
ok3 = pump(root, 60, "进程结束", app)
print("启动+子进程结束:", ok3, "| 运行态:", app.busy)

txt = app.txt.get("1.0", "end")
print("\n================ 控制台输出 ================")
print(txt.strip()[-1400:])
print("===========================================")
assert ok1 and ok2 and ok3, "有环节未完成"
assert "PASS" in txt and "净空" in txt
print("[PASS] 控制台全链路正常")
root.destroy()
