#!/bin/bash
# Claude Code 훅 통합 진입점
#
# 사용법: ~/.claude/settings.json hooks에 등록
#   "PreToolUse":  [{ "command": "<repo>/server/agent_hook.sh pre"  }],
#   "PostToolUse": [{ "command": "<repo>/server/agent_hook.sh post" }],
#   "Stop":        [{ "command": "<repo>/server/agent_hook.sh stop" }]
#
# stdin: Claude Code hook JSON
# 동작: 서버에 이벤트 POST + Stop의 경우 기존 tts_hook.sh 위임

set -uo pipefail

EVENT="${1:-stop}"
SERVER="${VT_SERVER:-http://localhost:${VT_PORT:-7777}}"

# stdin 백업 — Stop 이벤트는 tts_hook.sh로도 전달해야 함
TMPINPUT=$(mktemp)
trap 'rm -f "$TMPINPUT"' EXIT
cat > "$TMPINPUT"

# 서버에 이벤트 전송 (timeout 짧게 — 훅이 Claude를 막으면 안 됨)
python3 - "$EVENT" "$TMPINPUT" "$SERVER" << 'PYEOF' || true
import json, sys, urllib.request

event, input_file, server = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(input_file) as f:
        payload = json.load(f)
except Exception:
    payload = {}

try:
    body = json.dumps({"event": event, "payload": payload}).encode()
    req = urllib.request.Request(
        f"{server}/api/agent/event",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=2)
except Exception:
    pass
PYEOF

# Stop 이벤트는 기존 tts_hook.sh에 위임 (TTS 재생 + ntfy 푸시)
if [ "$EVENT" = "stop" ]; then
    HOOK_DIR="$(dirname "$0")"
    if [ -x "$HOOK_DIR/tts_hook.sh" ]; then
        cat "$TMPINPUT" | "$HOOK_DIR/tts_hook.sh" || true
    fi
fi

exit 0
