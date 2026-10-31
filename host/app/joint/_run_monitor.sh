#!/usr/bin/env bash
# 验证监控广播：board_main 起来后，连 5002 读 3 行，打印 JSON 字段
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"
T="$HERE/logs/_mon"
rm -rf "$T"; mkdir -p "$T"
cd "$APP" || exit 1

"$PY" joint/mock_motor.py --port 5001 --quiet > "$T/motor.log" 2>&1 &
MPID=$!
sleep 1.0
"$PY" joint/board_main.py --radar fake --fake-front 0.90 --motor 127.0.0.1:5001 \
    --secs 7 --quiet --log "$T/board.jsonl" > "$T/board.log" 2>&1 &
BPID=$!
sleep 1.2
"$PY" joint/mock_vision.py --scenario person_left --secs 4 > "$T/vision.log" 2>&1 &
VPID=$!
sleep 0.5

"$PY" - <<'EOF'
import json, socket, sys
s = socket.create_connection(("127.0.0.1", 5002), timeout=3)
s.settimeout(3)
buf = b""
got = []
while len(got) < 3:
    d = s.recv(65536)
    if not d:
        break
    buf += d
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        if line.strip():
            got.append(json.loads(line.decode()))
s.close()
if not got:
    print("[FAIL] 监控端口没收到任何数据")
    sys.exit(1)
f = got[-1]
print("[OK] 收到 %d 帧，字段：%s" % (len(got), sorted(f.keys())))
print("     rule=%s action=%s fault=%s" % (f["rule"], f["action"], f["fault"]))
print("     radar=%s" % {k: f["radar"][k] for k in ("front_distance", "left_distance", "right_distance")})
print("     sectors=%d 个：" % len(f["sectors"]))
for k, (d, n) in enumerate(f["sectors"]):
    print("       s%-2d %s  %s点" % (k, ("%.2fm" % d) if d is not None else "  --  ", n))
print("     vision=%s" % f["vision"])
print("     status=%s" % f["status"])
EOF
RC=$?
kill $VPID $BPID $MPID 2>/dev/null
wait $BPID 2>/dev/null
exit $RC
