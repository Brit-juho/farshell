#!/bin/bash
# 구 이름 호환: 완료 음성은 서버의 세션 정책이 처리한다. 이 스크립트는 ntfy만 맡는다.
set -euo pipefail
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOOK_DIR/../.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || exit 0
# stdin을 파이썬 프로그램과 분리한다.
TMPINPUT=$(mktemp)
trap 'rm -f "$TMPINPUT"' EXIT
cat > "$TMPINPUT"
HOOK_DIR="$HOOK_DIR" "$PY" - "$TMPINPUT" "${1:-claude}" <<'PYEOF'
import json, os, sys, urllib.request
url = os.environ.get('VT_NOTIFY_URL', '').strip()
if not url:
    sys.exit(0)
sys.path.insert(0, os.environ['HOOK_DIR'])
from voice_completion import extract
try:
    with open(sys.argv[1]) as f:
        payload = json.load(f)
    result = extract(sys.argv[2], payload)
    if not result or not result['text']:
        sys.exit(0)
    req = urllib.request.Request(url, data=result['text'].encode(), method='POST', headers={
        'Title': 'FarShell', 'Priority': 'default', 'Tags': 'robot', 'Content-Type': 'text/plain; charset=utf-8'})
    urllib.request.urlopen(req, timeout=3)
except Exception:
    pass
PYEOF
