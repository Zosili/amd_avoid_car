# -*- coding: utf-8 -*-
"""雷达控制台 —— COIN-D6 图形控制面板（不用敲命令行）

架构
----
本窗口用**系统 Python 3.12（自带 tkinter）**运行；
所有雷达计算（读串口、画云图、算几何）都交给 **venv Python 子进程**，
两边依赖不混。GUI 只负责：选串口 → 点按钮 → 看输出。

按钮
----
  打开实时云图   启动 radar_map_view.py（接雷达 COM 口）
  演示模式       合成场景，不接雷达（先熟悉界面用）
  带 CSV 日志    实时云图 + 5Hz 记 12 扇区数据到 CSV
  停止           结束当前实例（释放串口）
  体检 3 秒      不开窗口，读 3 秒直接报帧率/坏帧/12 扇区数字
  计算视野       按车体参数算净空角、遮挡区间、与 3 扇区的重叠

  上车联调区（2026-10-26 加）
  --------------------------
  看板上云图     起 radar_map_view.py --tcp 板子IP:5002（板上算、本机看）
  假装欧阳       起 mock_vision.py 往板子推视觉，验证视觉链路
  本机全套自检   一键起 假电机 + 板入口(假雷达) + 假视觉，不需要任何硬件
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 必须用带 tkinter 的解释器，否则 matplotlib 退回 Agg、窗口不显示（2026-10-25 踩过）：
#    envs\default（py3.13）没有 tkinter；gui312（系统 py3.12 派生）有 tkinter + matplotlib
# 用哪个解释器拉起子进程：默认用当前解释器；本机专用解释器可用环境变量覆盖
VENV_PY = os.environ.get("RADAR_CONSOLE_PYTHON") or sys.executable
INTEG = os.path.join(os.path.dirname(HERE), "integration")
JOINT = os.path.join(os.path.dirname(HERE), "joint")
APP = os.path.join(INTEG, "radar_map_view.py")
HELPER = os.path.join(HERE, "radar_helper.py")
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")
CREATE_NO_WINDOW = 0x08000000
SCENES = ["clear", "person_center", "person_left", "person_right",
          "far_small", "low_conf", "flicker", "bad"]

BG = "#f6f7f9"
CARD = "#ffffff"
ACCENT = "#1f6feb"
OKC = "#1f8a4c"
WARNC = "#c0392b"


class RadarConsole:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        self.busy = False
        self.jprocs = []            # 联调用的多进程（假电机 / 板入口 / 假视觉）
        self._joint_on = False
        root.title("雷达控制台 · COIN-D6")
        root.geometry("960x800")
        root.configure(bg=BG)
        root.minsize(840, 660)
        self._style()
        self._build()
        self.refresh_ports()
        self.root.after(120, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ 外观
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, font=("Microsoft YaHei UI", 10))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TLabel", background=BG, foreground="#222")
        st.configure("H1.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        st.configure("Sub.TLabel", foreground="#666", font=("Microsoft YaHei UI", 9))
        st.configure("TButton", padding=(10, 6))
        st.configure("TLabelframe", background=BG)
        st.configure("TLabelframe.Label", background=BG, foreground="#444",
                     font=("Microsoft YaHei UI", 9, "bold"))
        st.configure("TEntry", padding=4)
        st.configure("TCombobox", padding=4)

    # ------------------------------------------------------------ 布局
    def _build(self):
        pad = dict(padx=16)
        # 标题
        head = ttk.Frame(self.root)
        head.pack(fill="x", pady=(14, 6), **pad)
        ttk.Label(head, text="雷达控制台", style="H1.TLabel").pack(side="left")
        ttk.Label(head, text="  COIN-D6 · 12 扇区研究模式 · 车上仍是 3 扇区",
                  style="Sub.TLabel").pack(side="left", pady=(6, 0))

        # 串口行
        row = ttk.Frame(self.root)
        row.pack(fill="x", pady=6, **pad)
        ttk.Label(row, text="串口：").pack(side="left")
        self.port_var = tk.StringVar(value="COM5")
        self.cmb_port = ttk.Combobox(row, textvariable=self.port_var, width=16,
                                     state="readonly", values=[])
        self.cmb_port.pack(side="left")
        ttk.Button(row, text="刷新", width=6,
                   command=self.refresh_ports).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="体检 3 秒",
                   command=self.probe).pack(side="left", padx=(8, 0))
        self.lbl_port_hint = ttk.Label(row, text="", style="Sub.TLabel")
        self.lbl_port_hint.pack(side="left", padx=(10, 0))

        # 主按钮区
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", pady=(4, 10), **pad)
        self.b_live = tk.Button(btns, text="▶  打开实时云图", font=("Microsoft YaHei UI", 12, "bold"),
                                bg=ACCENT, fg="white", activebackground="#1558c0",
                                activeforeground="white", relief="flat", bd=0,
                                padx=18, pady=12, cursor="hand2",
                                command=lambda: self.launch([], "实时云图"))
        self.b_live.pack(side="left")
        self.b_demo = ttk.Button(btns, text="演示模式（无雷达）",
                                 command=lambda: self.launch(["--demo"], "演示模式"))
        self.b_demo.pack(side="left", padx=(10, 0))
        self.b_log = ttk.Button(btns, text="带 CSV 日志启动",
                                command=lambda: self.launch(["--log"], "实时云图+CSV"))
        self.b_log.pack(side="left", padx=(8, 0))
        self.b_stop = ttk.Button(btns, text="■ 停止", command=self.stop,
                                 state="disabled")
        self.b_stop.pack(side="left", padx=(8, 0))

        # 车体几何
        gf = ttk.Labelframe(self.root, text=" 车体几何（椭圆变体：超椭圆 n=2 纯椭圆，越大越方） ")
        gf.pack(fill="x", pady=(0, 10), **pad)
        inner = ttk.Frame(gf)
        inner.pack(fill="x", padx=12, pady=10)
        self.v_len = tk.StringVar(value="35")
        self.v_wid = tk.StringVar(value="18")
        self.v_fwd = tk.StringVar(value="3")
        self.v_n = tk.StringVar(value="2.2")
        self.v_mount = tk.StringVar(value="front")
        for lbl, var, w in (("车长cm", self.v_len, 5), ("车宽cm", self.v_wid, 5),
                            ("前伸cm", self.v_fwd, 5), ("形状n", self.v_n, 5)):
            ttk.Label(inner, text=lbl).pack(side="left")
            ttk.Entry(inner, textvariable=var, width=w).pack(side="left", padx=(4, 12))
        ttk.Radiobutton(inner, text="装前极点", variable=self.v_mount,
                        value="front").pack(side="left")
        ttk.Radiobutton(inner, text="装几何中心", variable=self.v_mount,
                        value="center").pack(side="left", padx=(6, 12))
        ttk.Button(inner, text="计算视野", command=self.geom).pack(side="left")
        ttk.Button(inner, text="打开工程目录",
                   command=lambda: os.startfile(INTEG)).pack(side="right")
        ttk.Button(inner, text="建桌面快捷方式",
                   command=self.make_shortcut).pack(side="right", padx=(0, 8))

        # 上车联调
        jf = ttk.Labelframe(
            self.root,
            text=" 上车联调（决策跑在板子上；本机只推视觉 / 只看云图） ")
        jf.pack(fill="x", pady=(0, 10), **pad)
        j1 = ttk.Frame(jf)
        j1.pack(fill="x", padx=12, pady=(8, 4))
        ttk.Label(j1, text="板子IP").pack(side="left")
        self.v_ip = tk.StringVar(value="192.168.2.99")
        ttk.Entry(j1, textvariable=self.v_ip, width=15).pack(side="left", padx=(4, 12))
        ttk.Label(j1, text="视觉场景").pack(side="left")
        self.v_scene = tk.StringVar(value="person_left")
        ttk.Combobox(j1, textvariable=self.v_scene, width=13, state="readonly",
                     values=SCENES).pack(side="left", padx=(4, 12))
        self.b_jview = ttk.Button(j1, text="看板上云图",
                                  command=self.joint_view)
        self.b_jview.pack(side="left")
        self.b_jvis = ttk.Button(j1, text="假装欧阳（推视觉到板子）",
                                 command=self.joint_vision)
        self.b_jvis.pack(side="left", padx=(8, 0))
        j2 = ttk.Frame(jf)
        j2.pack(fill="x", padx=12, pady=(0, 10))
        self.b_jlocal = ttk.Button(
            j2, text="本机全套自检（假雷达 + 假电机 + 假视觉）",
            command=self.joint_local)
        self.b_jlocal.pack(side="left")
        ttk.Label(j2, text="  不需要任何硬件；三个进程的日志会合并到下面",
                  style="Sub.TLabel").pack(side="left")

        # 输出
        outf = ttk.Frame(self.root)
        outf.pack(fill="both", expand=True, pady=(0, 8), **pad)
        self.txt = tk.Text(outf, bg="#ffffff", fg="#1a1a1a", relief="flat",
                           font=("Consolas", 10), wrap="none", padx=12, pady=10,
                           insertbackground="#333")
        sb = ttk.Scrollbar(outf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt.tag_configure("h", font=("Microsoft YaHei UI", 10, "bold"),
                               foreground="#111", spacing1=6)
        self.txt.tag_configure("ok", foreground=OKC)
        self.txt.tag_configure("warn", foreground=WARNC)
        self.txt.tag_configure("dim", foreground="#777")
        self.txt.configure(state="disabled")

        # 状态栏
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", pady=(0, 8), **pad)
        self.lbl_state = ttk.Label(bar, text="● 空闲", foreground="#888",
                                   font=("Microsoft YaHei UI", 9, "bold"))
        self.lbl_state.pack(side="left")
        ttk.Label(bar, text="提示：窗口开着时串口被占用，其它程序读不了；关窗口即释放",
                  style="Sub.TLabel").pack(side="right")

        self.log("雷达控制台已就绪。", "h")
        self.log(f"雷达解释器: {os.path.basename(os.path.dirname(os.path.dirname(VENV_PY)))}"
                 f"\\{os.path.basename(VENV_PY)}（带 tkinter，绘图后端 TkAgg）", "dim")
        self.log("① 选串口 → ② 点「体检 3 秒」确认雷达健康 → ③ 点「打开实时云图」", "dim")
        self.log("云图窗口里：a 累积/拖尾 · z 缩放视野 · c 清空 · s 截图 · q 退出", "dim")
        self.log("上车联调：没硬件先点「本机全套自检」；有板子就填 IP → "
                 "「看板上云图」/「假装欧阳」", "dim")

    # ------------------------------------------------------------ 工具
    def log(self, s, tag=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", s + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _set_running(self, on):
        self.busy = on
        self.b_live.configure(state="disabled" if on else "normal")
        self.b_demo.configure(state="disabled" if on else "normal")
        self.b_log.configure(state="disabled" if on else "normal")
        self.b_stop.configure(state="normal" if on else "disabled")
        if on:
            pid = self.proc.pid if self.proc else "?"
            self.lbl_state.configure(text=f"● 运行中 (PID {pid})", foreground=OKC)
        else:
            self.lbl_state.configure(text="● 空闲", foreground="#888")

    def _env(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self.log(f"— 进程结束（返回码 {payload}）—", "dim")
                    if not self._joint_on:
                        self._set_running(False)
                elif kind == "jdone":
                    tag, rc = payload
                    self.log(f"[{tag}] — 进程结束（返回码 {rc}）—", "dim")
                    if not self._joint_alive():
                        self._joint_on = False
                        self._set_joint(False)
                        self._set_running(False)
                        self.log("[联调] 全部进程已结束", "dim")
                elif kind == "helper":
                    fn, out = payload
                    fn(out)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    # ------------------------------------------------------------ 串口
    def refresh_ports(self):
        def work():
            out = self._run_helper(["ports"], timeout=15)
            self.q.put(("helper", (self._apply_ports, out)))
        threading.Thread(target=work, daemon=True).start()

    def _apply_ports(self, out):
        ports = (out or {}).get("ports") or []
        devs = [p["device"] for p in ports]
        self.cmb_port.configure(values=devs)
        ch340 = [p for p in ports if "CH340" in (p.get("desc") or "").upper()
                 or "USB-SERIAL" in (p.get("desc") or "").upper()]
        if ch340:
            self.port_var.set(ch340[0]["device"])
            self.lbl_port_hint.configure(text="已识别 CH340（雷达）",
                                         foreground=OKC)
        elif devs:
            self.port_var.set(devs[0])
            self.lbl_port_hint.configure(text=f"找到 {len(devs)} 个串口，未见 CH340",
                                         foreground="#a06000")
        else:
            self.lbl_port_hint.configure(text="没找到任何串口（雷达插了吗？）",
                                         foreground=WARNC)

    def _run_helper(self, args, timeout=30):
        try:
            r = subprocess.run([VENV_PY, HELPER] + args, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=self._env(),
                               creationflags=CREATE_NO_WINDOW)
            line = (r.stdout or "").strip().splitlines()
            if not line:
                return {"ok": False, "msg": (r.stderr or "helper 无输出")[-400:]}
            return json.loads(line[-1])
        except Exception as e:
            return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------ 体检
    def probe(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("先停止", "实时云图正在跑并占用串口，先点「停止」再体检。")
            return
        port = self.port_var.get().strip()
        self.log(f"[体检] 读 {port} 3 秒 …", "h")
        self.lbl_state.configure(text="● 体检中…", foreground="#a06000")

        def work():
            out = self._run_helper(["probe", "--port", port, "--secs", "3"],
                                   timeout=40)
            self.q.put(("helper", (self._show_probe, out)))
        threading.Thread(target=work, daemon=True).start()

    def _show_probe(self, d):
        self.lbl_state.configure(text="● 空闲", foreground="#888")
        if not d.get("ok"):
            self.log("  ✗ " + str(d.get("msg")), "warn")
            return
        n, v = d["npts"], d["valid"]
        pct = 100.0 * v / n if n else 0
        tag = "ok" if (d["frames_bad"] == 0 and pct > 50) else "warn"
        self.log(f"  帧 OK {d['frames_ok']} / 坏 {d['frames_bad']}    "
                 f"帧率 {d['hz']}/s    有效点 {v}/{n} ({pct:.0f}%)", tag)
        self.log(f"  距离 中位 {d['d_med']}m  最短 {d['d_min']}  最长 {d['d_max']}")
        s3 = d["s3"]
        f = lambda k: "--" if s3[k][0] is None else f"{s3[k][0]:.2f}m"
        self.log(f"  三扇区(车上口径)  前 {f('front')}({s3['front'][1]}点)   "
                 f"左 {f('left')}({s3['left'][1]}点)  右 {f('right')}({s3['right'][1]}点)")
        self.log("  12 扇区（s0=345~15°，每 30°）：")
        cells = [("--" if m is None else f"{m:.2f}") for m, _n in d["s12"]]
        for i in range(0, 12, 4):
            seg = "   ".join(f"s{j:<2} {cells[j]:>5}" for j in range(i, i + 4))
            self.log("    " + seg)
        short = [j for j, (m, _n) in enumerate(d["s12"]) if m is not None and m < 0.30]
        if short:
            self.log(f"  注意：s{short} 小于 0.30m —— 近距离固定反射"
                     f"（支架/桌面/车体），不是障碍", "warn")

    # ------------------------------------------------------------ 几何
    def geom(self):
        try:
            a = ["geom", "--len", self.v_len.get(), "--wid", self.v_wid.get(),
                 "--fwd", self.v_fwd.get(), "--n", self.v_n.get(),
                 "--mount", self.v_mount.get()]
            [float(self.v_len.get()), float(self.v_wid.get()),
             float(self.v_fwd.get()), float(self.v_n.get())]
        except ValueError:
            messagebox.showerror("参数错误", "车体参数必须是数字")
            return
        self.log("[几何] 计算中 …", "h")

        def work():
            out = self._run_helper(a, timeout=60)
            self.q.put(("helper", (self._show_geom, out)))
        threading.Thread(target=work, daemon=True).start()

    def _show_geom(self, d):
        if not d.get("clear_fov") and not d.get("ok", True):
            self.log("  ✗ " + str(d.get("msg")), "warn")
            return
        mount = "几何中心" if self.v_mount.get() == "center" else \
            f"前极点外 {self.v_fwd.get()}cm"
        self.log(f"  {self.v_len.get()}×{self.v_wid.get()}cm  n={self.v_n.get()}  "
                 f"雷达装{mount}")
        tag = "ok" if d["clear_fov"] > 180 else "warn"
        self.log(f"  前方净空 {d['clear_fov']}°   遮挡 "
                 f"{'、'.join(f'{lo}°~{hi}°' for lo, hi in d['occlusion']) or '无'}"
                 f"   扫掠半径 {d['sweep_cm']}cm", tag)
        ov = d["overlap"]
        self.log("  三扇区重叠: " + "  ".join(f"{k} {v}°" for k, v in ov.items()))
        for w in d.get("warn") or []:
            self.log("  ⚠ " + w, "warn")
        if not d.get("warn"):
            self.log("  ✓ 当前 3 扇区都落在净空区内，可以直接用", "ok")

    # ------------------------------------------------------------ 启动/停止
    def launch(self, extra, label):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("正在运行", "已有实例在跑，先点「停止」。")
            return
        port = self.port_var.get().strip()
        args = [VENV_PY, APP, "--port", port] + list(extra)
        self.log(f"[启动] {label}  →  {os.path.basename(APP)} {' '.join(extra)}", "h")
        try:
            self.proc = subprocess.Popen(
                args, cwd=INTEG, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=self._env(), creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self.log(f"  ✗ 启动失败：{e}", "warn")
            return
        self._set_running(True)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc):
        try:
            for line in proc.stdout:
                self.q.put(("log", line.rstrip()))
        except Exception:
            pass
        try:
            rc = proc.wait(timeout=10)      # 等真正退出，避免 poll() 取到 None
        except Exception:
            rc = -1
        self.q.put(("done", rc))

    def stop(self):
        self._joint_on = False
        if self.proc and self.proc.poll() is None:
            self.log("[停止] 结束进程 …", "dim")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.jprocs:
            self.log(f"[停止] 结束联调 {len(self.jprocs)} 个进程 …", "dim")
            for p in self.jprocs:
                if p.poll() is None:
                    try:
                        p.terminate()
                    except Exception:
                        pass
            for p in self.jprocs:
                try:
                    p.wait(timeout=3)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            self.jprocs = []
        self._set_joint(False)
        self._set_running(False)

    # ------------------------------------------------------------ 联调
    def _joint_alive(self):
        return any(p.poll() is None for p in self.jprocs)

    def _spawn(self, args, tag):
        try:
            p = subprocess.Popen(
                args, cwd=JOINT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=self._env(),
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self.q.put(("log", f"[{tag}] ✗ 启动失败：{e}"))
            return None
        threading.Thread(target=self._reader_tagged, args=(p, tag),
                         daemon=True).start()
        return p

    def _reader_tagged(self, proc, tag):
        try:
            for line in proc.stdout:
                self.q.put(("log", f"[{tag}] {line.rstrip()}"))
        except Exception:
            pass
        try:
            rc = proc.wait(timeout=10)
        except Exception:
            rc = -1
        self.q.put(("jdone", (tag, rc)))

    def _spawn_if_on(self, args, tag):
        if not self._joint_on:
            return
        p = self._spawn(args, tag)
        if p:
            self.jprocs.append(p)

    def joint_local(self):
        """本机三进程自检：不需要板子、不需要雷达、不需要队友。"""
        if self._joint_alive() or (self.proc and self.proc.poll() is None):
            messagebox.showinfo("正在运行", "还有进程在跑，先点「停止」。")
            return
        self.jprocs = []
        self._joint_on = True
        self._set_joint(True)
        self.log("[联调] 本机全套自检：假电机(5001) → 板入口(假雷达) → 假视觉(5000)",
                 "h")
        self.log("       期望：先几行 R5(还没视觉) → 变成 R4 LEFT/RIGHT → "
                 "视觉停后回到 R5", "dim")
        steps = [
            (0, [VENV_PY, os.path.join(JOINT, "mock_motor.py"),
                 "--port", "5001", "--every", "10"], "电机"),
            (1200, [VENV_PY, os.path.join(JOINT, "board_main.py"),
                    "--radar", "fake", "--fake-front", "1.50",
                    "--motor", "127.0.0.1:5001"], "板子"),
            (2600, [VENV_PY, os.path.join(JOINT, "mock_vision.py"),
                    "--scenario", self.v_scene.get(), "--secs", "20"], "视觉"),
        ]
        for delay, args, tag in steps:
            self.root.after(delay, lambda a=args, t=tag: self._spawn_if_on(a, t))
        self.root.after(4000, self._joint_report)

    def _joint_report(self):
        if not self._joint_on:
            return
        alive = [p for p in self.jprocs if p.poll() is None]
        self.log(f"[联调] 3 个进程里存活 {len(alive)} 个。"
                 f"看上面有没有 '已连上 127.0.0.1:5001' 和 '客户端接入'", "dim")
        if len(alive) < 3:
            self.log("[联调] ✗ 有进程退出了，往上翻日志找 [FAIL]/Traceback", "warn")

    def joint_view(self):
        """连板子的监控端口，本机只当显示器。"""
        ip = self.v_ip.get().strip()
        if not ip:
            messagebox.showwarning("缺参数", "先填板子 IP")
            return
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("正在运行", "云图已经开着，先点「停止」。")
            return
        args = [VENV_PY, APP, "--tcp", f"{ip}:5002"]
        self.log(f"[启动] 看板上云图  →  radar_map_view.py --tcp {ip}:5002", "h")
        self.log("       连不上：确认板子上 board_main.py 在跑、IP 对、"
                 "Windows 防火墙没挡 5002", "dim")
        try:
            self.proc = subprocess.Popen(
                args, cwd=INTEG, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=self._env(), creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self.log(f"  ✗ 启动失败：{e}", "warn")
            return
        self._set_running(True)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def joint_vision(self):
        """往板子推假视觉，验证 5000 端口和决策的视觉分支。"""
        if self._joint_alive():
            messagebox.showinfo("正在运行", "联调进程在跑，先点「停止」。")
            return
        ip = self.v_ip.get().strip()
        if not ip:
            messagebox.showwarning("缺参数", "先填板子 IP")
            return
        self._joint_on = True
        self._set_joint(True)
        scene = self.v_scene.get()
        self.log(f"[联调] 假装欧阳 → {ip}:5000 场景={scene}", "h")
        p = self._spawn([VENV_PY, os.path.join(JOINT, "mock_vision.py"),
                         "--host", ip, "--scenario", scene], "视觉")
        if p:
            self.jprocs.append(p)

    def _set_joint(self, on):
        st = "disabled" if on else "normal"
        for b in (self.b_jview, self.b_jvis, self.b_jlocal,
                  self.b_live, self.b_demo, self.b_log):
            b.configure(state=st)
        if on:
            self.b_stop.configure(state="normal")
            self.lbl_state.configure(text="● 联调中", foreground=OKC)

    # ------------------------------------------------------------ 快捷方式
    def make_shortcut(self):
        lnk = os.path.join(DESKTOP_DIR, "雷达控制台.lnk")
        # 用当前解释器推 pythonw（Windows 下同目录有 pythonw.exe；没有就用自身）
        _pw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        sys_pyw = _pw if os.path.exists(_pw) else sys.executable
        vbs = os.path.join(HERE, "_mklnk.vbs")
        body = (
            'Set s = CreateObject("WScript.Shell")\r\n'
            f'Set l = s.CreateShortcut("{lnk}")\r\n'
            f'l.TargetPath = "{sys_pyw}"\r\n'
            f'l.Arguments = Chr(34) & "{os.path.join(HERE, "radar_console.py")}" & Chr(34)\r\n'
            f'l.WorkingDirectory = "{HERE}"\r\n'
            'l.Description = "雷达控制台 - COIN-D6"\r\n'
            'l.Save\r\n')
        try:
            os.makedirs(DESKTOP_DIR, exist_ok=True)
            with open(vbs, "w", encoding="gbk") as f:
                f.write(body)
            subprocess.run(["cscript", "//nologo", vbs], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
            if os.path.exists(lnk):
                self.log(f"  ✓ 快捷方式已建：{lnk}", "ok")
            else:
                self.log("  ✗ 创建失败（目标目录不存在？）", "warn")
        except Exception as e:
            self.log(f"  ✗ 创建失败：{e}", "warn")
        finally:
            try:
                os.remove(vbs)
            except OSError:
                pass

    def _on_close(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    RadarConsole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
