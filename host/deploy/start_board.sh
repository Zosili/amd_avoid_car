#!/usr/bin/env bash
# 板端一键起决策循环（PYNQ 2.7.0 / Python 3.6.9）
# 用法：bash host/deploy/start_board.sh --motor 192.168.1.50:5001
#      bash host/deploy/start_board.sh --radar fake --motor 127.0.0.1:5001 --secs 15
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"          # host/
PY="${PYTHON:-python3}"
LOG_DIR="${LOG_DIR:-$APP/joint/logs}"
mkdir -p "$LOG_DIR"

echo "[start_board] $($PY -V 2>&1)  入口: $APP/app/joint/board_main.py"
cd "$APP/app" || exit 1
exec "$PY" joint/board_main.py --log "$LOG_DIR/board_$(date +%Y%m%d_%H%M%S).jsonl" "$@"
