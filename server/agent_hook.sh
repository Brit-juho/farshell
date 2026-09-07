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
import json, os, sys, urllib.request

event, input_file, server = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(input_file) as f:
        payload = json.load(f)
except Exception:
    payload = {}

try:
    # A2 pane 자기보고: Claude Code 훅 JSON에는 "어느 tmux pane에서 돌고 있나"가
    # 없다. 지금까지 서버는 cwd 문자열 일치로 추측해왔는데, 같은 디렉토리
    # ($HOME 등)에 세션이 둘이면 확신할 수 없어 아무것도 표시하지 못했다.
    # 훅은 pane 셸의 자식이라 TMUX/TMUX_PANE을 그대로 상속받는다 — 그 두 줄을
    # 실어 보내면 추측이 정확 매칭으로 바뀐다. tmux 밖이면 둘 다 None이고
    # 서버가 cwd 폴백으로 내려간다.
    body = json.dumps({
        "event": event,
        "payload": payload,
        "pane": os.environ.get("TMUX_PANE"),
        "tmux": os.environ.get("TMUX"),
    }).encode()
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
