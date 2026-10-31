#!/usr/bin/env bash
# 三进程联调：假电机(5001) + 板入口(board_main) + 假视觉(推 5000)
# 用法: bash _run_it.sh [场景] [板入口额外参数...]
# 依赖: pyserial/numpy 的 python3；可用 PYTHON=/path/to/python 覆盖
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # host/app/joint
APP="$(cd "$HERE/.." && pwd)"                          # host/app
PY="${PYTHON:-python3}"
T="$HERE/logs/_it"
SCENE="${1:-person_left}"
shift || true

rm -rf "$T"
mkdir -p "$T"
cd "$APP" || exit 1

"$PY" joint/mock_motor.py --port 5001 --log "$T/motor.csv" \
    ${MOTOR_ARGS:-} > "$T/motor.log" 2>&1 &
MPID=$!
sleep 1.2

"$PY" joint/board_main.py --radar fake --fake-front 1.50 --fake-left 1.00 \
    --fake-right 1.00 --motor 127.0.0.1:5001 --secs 9 \
    --log "$T/board.jsonl" "$@" > "$T/board.log" 2>&1 &
BPID=$!
sleep 1.8

"$PY" joint/mock_vision.py --scenario "$SCENE" --secs 5 > "$T/vision.log" 2>&1

wait $BPID
kill $MPID 2>/dev/null
sleep 0.3

echo "===== board.log ====="
cat "$T/board.log"
echo "===== motor.log (末 12 行) ====="
tail -12 "$T/motor.log"
echo "===== vision.log (末 2 行) ====="
tail -2 "$T/vision.log"
echo "===== motor.csv ====="
head -6 "$T/motor.csv"
echo "..."
tail -4 "$T/motor.csv"
echo "===== 动作分布 ====="
awk -F, 'NR>1{c[$3]++} END{for(k in c) printf "  %-8s %d\n", k, c[k]}' "$T/motor.csv"
