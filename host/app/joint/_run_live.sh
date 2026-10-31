#!/usr/bin/env bash
# 真实雷达 + 假电机：验证 BoardRadarProvider 子类化（scan_snapshot 取 _lock）没问题
# 用法: bash _run_live.sh [串口, 默认 /dev/ttyUSB0]
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"
T="$HERE/logs/_live"
PORT="${1:-/dev/ttyUSB0}"
rm -rf "$T"; mkdir -p "$T"
cd "$APP" || exit 1

"$PY" joint/mock_motor.py --port 5001 --quiet --log "$T/motor.csv" > "$T/motor.log" 2>&1 &
MPID=$!
sleep 1.2
"$PY" joint/board_main.py --radar live --radar-port "$PORT" \
    --motor 127.0.0.1:5001 --secs 8 --quiet --log "$T/board.jsonl" \
    > "$T/board.log" 2>&1 &
BPID=$!
sleep 2.5

MPLBACKEND=Agg "$PY" integration/_selftest_net.py 127.0.0.1:5002
RC=$?
wait $BPID
kill $MPID 2>/dev/null
echo "----- board.log -----"
tail -8 "$T/board.log"
echo "----- 动作分布 -----"
awk -F, 'NR>1{c[$3]++} END{for(k in c) printf "  %-8s %d\n", k, c[k]}' "$T/motor.csv"
exit $RC
