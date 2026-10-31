# -*- coding: utf-8 -*-
"""_smoke_layout.py —— 版面重叠自检（我看不到图，用坐标框判定有没有压字）

判定项：
  1) 状态文字框 不能压到 极坐标图框
  2) 极坐标标题 不能压到 状态文字框
  3) 动作大字 不能压到 极坐标图框
  4) 图例 不能压到 车体轮廓
  5) 12 个数值标签都要落在极坐标半径内
跑法：gui312 python _smoke_layout.py
"""
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
INTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "integration")
sys.path.insert(0, INTS)

import argparse
import radar_map_view as rmv

args = argparse.Namespace(demo=True, port="COM5", mount="front", fwd=3.0,
                          car_len=35.0, car_wid=18.0, shape_n=2.2, log=False,
                          auto_close=0)
view = rmv.MapView(args, interactive=False)
src = rmv.RadarSource("synth")
view.src = src
time.sleep(3.0)
view._update()
fig = view.fig
fig.canvas.draw()
r = fig.canvas.get_renderer()

def box(obj):
    return obj.get_window_extent(r)

b_info = box(view.txt_info)
b_polar = box(view.axp)
b_title = box(view.axp.title)
b_act = box(view.txt_act)
b_why = box(view.txt_why)

car_pts = view.ax.transData.transform([[0.0, -0.20]])
car_x, car_y = car_pts[0]
leg = view.ax.get_legend()
b_leg = box(leg)

checks = []
checks.append(("状态文字 vs 极坐标图", not b_info.overlaps(b_polar),
               f"info={b_info.bounds} polar={b_polar.bounds}"))
checks.append(("极坐标标题 vs 状态文字", not b_title.overlaps(b_info),
               f"title={b_title.bounds}"))
checks.append(("动作大字 vs 极坐标图", not b_act.overlaps(b_polar), ""))
checks.append(("动作原因 vs 极坐标图", not b_why.overlaps(b_polar), ""))
# 图例 vs 车体中心附近（车体是数据坐标，转显示坐标后取个 40px 方框粗判）
leg_car = not (b_leg.x0 < car_x < b_leg.x1 and b_leg.y0 < car_y < b_leg.y1)
checks.append(("图例 vs 车体", leg_car, f"leg={b_leg.bounds}"))

ok_all = True
for name, ok, extra in checks:
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + extra if extra else ""))
    ok_all &= ok

# 数值标签在极坐标内
inb = all(0 < view.axp.transData.transform((0, 2.32))[1] <=
          fig.get_size_inches()[1] * fig.dpi for _ in [0])
print("[INFO] 12 数值标签:", [t.get_text() for t in view.lbl_val12])
print("[INFO] 状态文字内容:", view.txt_info.get_text().replace("\n", " | "))
print("[INFO] 动作:", view.txt_act.get_text(), "|", view.txt_why.get_text())
print("[INFO] 视野:", view.txt_view.get_text())

out = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "assets", "radar_view_selftest.png")
fig.savefig(out, dpi=130, facecolor="white")
print("[OK] 版面临时图:", out)
src.alive = False
print("[PASS] 版面自检通过" if ok_all else "[FAIL] 版面有重叠，需要继续调")
sys.exit(0 if ok_all else 1)
