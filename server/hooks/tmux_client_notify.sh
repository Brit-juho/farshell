#!/bin/bash
# tmux_client_notify.sh — client-attached / client-detached 훅 핸들러 (D9).
#
# 왜 필요한가:
#   voice-terminal은 web(WS)·voice(daemon) 경로에선 누가 접속했는지 서버가 알 수
#   있지만, 회사망처럼 화면 원격이 막혀 Tailscale+SSH로 tmux에 순수 텍스트로
#   attach하는 경우(`vt ssh` 참고)엔 서버 입장에서 완전히 "보이지 않는" 클라이언트다.
#   이 스크립트는 tmux 훅으로 attach/detach 시점을 잡아 기존 ntfy/Telegram 푸시
#   브릿지(server/notify.py)로 알림을 대신 보내 접속 가시성을 확보한다.
#
# 등록 방법 (opt-in, 기본 OFF):
#   ~/.vt.env 에 VT_NOTIFY_CLIENT_EVENTS=1 추가 → bin/vt의
#   _maybe_register_client_hooks()가 `vt voice`/`vt mobile`/`vt start`/`vt ssh` 등
#   _ensure_tmux를 호출하는 모든 명령에서 자동으로 tmux client-attached/detached
#   훅을 등록한다. 이 스크립트를 직접 실행할 일은 거의 없다.
#
# 인자:
#   $1 = event        attached | detached
#   $2 = client_tty   tmux의 #{client_tty} (예: /dev/ttys003)
#   $3 = session_name tmux의 #{session_name}
#
# 원격 호스트 판별:
#   tmux는 attach한 클라이언트가 SSH로 왔는지 알려주는 API가 없다. 대신 표준
#   유닉스 `who` 출력이 원격(ssh) 로그인 세션의 origin host를 tty 옆에 "(host)"
#   형태로 보여주므로 이를 매칭해 best-effort로 추출한다. 못 찾으면 "로컬"로 처리.

set -uo pipefail

EVENT="${1:-attached}"
CLIENT_TTY="${2:-}"
SESSION="${3:-?}"

# ~/.vt.env에서 VT_PORT / VT_TOKEN 읽기 (bin/vt와 동일 우선순위: 환경변수가 이미 있으면 유지)
if [ -f "$HOME/.vt.env" ]; then
  # shellcheck disable=SC1090
  source "$HOME/.vt.env" 2>/dev/null || true
fi

VT_PORT="${VT_PORT:-7777}"
PYBIN="${VT_PYTHON:-python3}"
command -v "$PYBIN" >/dev/null 2>&1 || PYBIN="python3"

TTY_SHORT=""
[ -n "$CLIENT_TTY" ] && TTY_SHORT="$(basename "$CLIENT_TTY" 2>/dev/null)"

REMOTE_HOST=""
if command -v who >/dev/null 2>&1 && [ -n "$TTY_SHORT" ]; then
  # `who` 라인 형태(대략): "user   ttys003   2026-07-07 09:00 (100.101.102.103)"
  REMOTE_HOST="$(who 2>/dev/null \
    | awk -v t="$TTY_SHORT" '$2 == t {print $0}' \
    | grep -oE '\([^)]+\)' \
    | tr -d '()' \
    | head -1)"
fi

TOKEN_Q=""
[ -n "${VT_TOKEN:-}" ] && TOKEN_Q="?token=${VT_TOKEN}"

PAYLOAD="$(EVT="$EVENT" SESS="$SESSION" TTYV="$TTY_SHORT" RMT="$REMOTE_HOST" "$PYBIN" - <<'PY' 2>/dev/null
import json, os
print(json.dumps({
    "event": os.environ.get("EVT", "attached"),
    "session": os.environ.get("SESS", "?"),
    "tty": os.environ.get("TTYV", ""),
    "remote_host": os.environ.get("RMT", ""),
}))
PY
)"

# python3조차 없는 극단적 환경 대비 최소 fallback (원격 호스트 정보는 생략)
if [ -z "$PAYLOAD" ]; then
  PAYLOAD="{\"event\":\"${EVENT}\",\"session\":\"${SESSION}\"}"
fi

# 서버가 안 떠 있어도 훅이 tmux를 막지 않도록 짧은 타임아웃 + 백그라운드 실행.
# run-shell -b(background)로 호출되므로 여기서 추가로 &를 걸 필요는 없지만,
# 스크립트 단독 실행(디버깅) 시에도 안전하도록 timeout을 짧게 유지한다.
curl -s -m 3 -X POST "http://127.0.0.1:${VT_PORT}/api/notify/client-event${TOKEN_Q}" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" >/dev/null 2>&1

exit 0
