#!/usr/bin/env bash
# COIN-D6 雷达工具 · Linux / macOS 入口（Windows 用 启动.bat）
# 用法：./run.sh            → 菜单
#       ./run.sh selftest   → 自检 3 秒
#       ./run.sh stream     → 发扫描给融合
#       ./run.sh view       → 云图窗口
#       ./run.sh save out.png
set -u
cd "$(dirname "$0")" || exit 1

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "找不到 python3/python，请先装 Python 3.7+"; exit 1; }

case "${1:-menu}" in
  selftest) exec "$PY" d6_stream.py --selftest --secs 3 ;;
  stream)   exec "$PY" d6_stream.py ;;
  stream-pl) shift; exec "$PY" d6_stream.py --dist-udp "${1:-192.168.1.10:5000}" ;;
  view)     exec "$PY" radar_view.py ;;
  demo)     exec "$PY" radar_view.py --demo ;;
  save)     shift; exec "$PY" radar_view.py --save "${1:-cloud.png}" --secs 4 ;;
  ports)    exec "$PY" d6_stream.py --list-ports ;;
  menu)
    echo "1) 自检  2) 发扫描  3) 发扫描+距离帧  4) 云图  5) 云图存 PNG  6) demo  7) 串口列表"
    printf "选择: "; read -r c
    case "$c" in
      1) exec "$PY" d6_stream.py --selftest --secs 3 ;;
      2) exec "$PY" d6_stream.py ;;
      3) printf "板子 IP [192.168.1.10]: "; read -r ip; exec "$PY" d6_stream.py --dist-udp "${ip:-192.168.1.10}:5000" ;;
      4) exec "$PY" radar_view.py ;;
      5) exec "$PY" radar_view.py --save cloud.png --secs 4 ;;
      6) exec "$PY" radar_view.py --demo ;;
      7) exec "$PY" d6_stream.py --list-ports ;;
      *) echo "无效"; exit 1 ;;
    esac ;;
  *) echo "用法: ./run.sh [selftest|stream|stream-pl IP|view|demo|save FILE|ports]"; exit 1 ;;
esac
