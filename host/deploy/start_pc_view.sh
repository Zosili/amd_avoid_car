#!/usr/bin/env bash
# PC 端：连板上 5002 看雷达云图 + 决策视图（只读，断连不影响车）
# 用法：bash host/deploy/start_pc_view.sh <board_ip>:5002
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"
TARGET="${1:-127.0.0.1:5002}"
echo "[start_pc_view] 连 $TARGET （板上 MonitorBroadcaster）"
cd "$APP/app/integration" || exit 1
exec "$PY" radar_map_view.py --tcp "$TARGET"
