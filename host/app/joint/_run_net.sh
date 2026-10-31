#!/usr/bin/env bash
# 端到端：假电机 + 板入口(带点云广播) + 假视觉 + PC 端 net 数据源自检
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"
T="$HERE/logs/_net"
SCENE="${1:-person_left}"
rm -rf "$T"; mkdir -p "$T"
cd "$APP" || exit 1

"$PY" joint/mock_motor.py --port 5001 --quiet > "$T/motor.log" 2>&1 &
MPID=$!
sleep 1.2
"$PY" joint/board_main.py --radar fake --fake-front 0.85 --fake-left 1.10 \
    --fake-right 0.70 --motor 127.0.0.1:5001 --secs 14 --quiet \
    --log "$T/board.jsonl" > "$T/board.log" 2>&1 &
BPID=$!
sleep 1.8
"$PY" joint/mock_vision.py --scenario "$SCENE" --secs 10 > "$T/vision.log" 2>&1 &
VPID=$!
sleep 0.6

MPLBACKEND=Agg "$PY" integration/_selftest_net.py 127.0.0.1:5002
RC=$?
kill $VPID 2>/dev/null
wait $BPID
kill $MPID 2>/dev/null
echo "----- board.log -----"
tail -6 "$T/board.log"
exit $RC
