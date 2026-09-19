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
AGENT="${2:-claude}"
SERVER="${VT_SERVER:-http://localhost:${VT_PORT:-7777}}"

# stdin 백업 — Stop 이벤트는 tts_hook.sh로도 전달해야 함
TMPINPUT=$(mktemp)
trap 'rm -f "$TMPINPUT"' EXIT
cat > "$TMPINPUT"

# 서버에 이벤트 전송 (timeout 짧게 — 훅이 Claude를 막으면 안 됨)
python3 - "$EVENT" "$TMPINPUT" "$SERVER" "$(dirname "$0")" "$AGENT" << 'PYEOF' || true
import json, os, sys, urllib.error, urllib.request

event, input_file, server, hook_dir, agent = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
try:
    with open(input_file) as f:
        payload = json.load(f)
except Exception:
    payload = {}

# ── 인증 ────────────────────────────────────────────────────────────────
# `VT_AUTH_TOKEN`이 설정된 서버는 이 POST를 401로 막는다. main.py의 설계 주석이
# 이미 "기계: clipboard_daemon·tui·hook은 Bearer/query로 VT_TOKEN 직접 전달"이라고
# 적고 있었는데 **훅에만 그 구현이 빠져 있었다** — 2026-09-17에 실측하니
# `POST /api/agent/event`가 1,440번 전부 401이었다(성공 0건). 그동안 에이전트
# 상태(idle/working/waiting/done)·프롬프트 큐 자동 투입·감지 커버리지의
# `trust: high`가 전부 죽어 있었고, 아래 예외 삼킴 때문에 아무도 몰랐다.
# clipboard_daemon.py와 같은 방식으로 읽는다(~/.vt.env 파서 재사용).
#
# ⚠ **토큰만은 파일을 먼저 읽는다** — `vt_env.getenv`의 기본 순서(환경변수 우선)를
# 여기서만 뒤집는다. 프로젝트 규칙은 "호출 시점의 VT_*가 파일보다 우선"이고 포트·
# 경로에는 그게 맞지만, 자격증명에 적용하면 **낡은 값이 조용히 401을 만든다.**
# 2026-09-17에 `fsh password`와 무관하게 살아있던 옛 VT_AUTH_TOKEN을 재발급했더니,
# 그 값을 환경에 들고 기동해 있던 Claude Code 세션 셋이 전부 훅 401이 됐다(490건).
# 훅은 실행 중인 에이전트의 자식이라 그 환경을 스스로 갱신할 방법이 없고, 고치려면
# 작업 중인 세션을 전부 재시작해야 했다. 파일을 먼저 읽으면 다음 훅부터 바로 낫는다.
# 환경변수는 파일에 값이 없을 때의 폴백으로 남긴다(격리 테스트 서버 등).
token = ""
try:
    sys.path.insert(0, hook_dir)
    import vt_env
    _file_env = vt_env.load()
    token = (_file_env.get("VT_AUTH_TOKEN") or _file_env.get("VT_TOKEN")
             or os.environ.get("VT_AUTH_TOKEN") or os.environ.get("VT_TOKEN") or "")
except Exception:
    # 파서를 못 불러도 환경변수로 온 값은 쓴다 — 훅은 어떤 경우에도 죽지 않는다.
    token = os.environ.get("VT_AUTH_TOKEN") or os.environ.get("VT_TOKEN") or ""


# ── 실패를 남긴다 ───────────────────────────────────────────────────────
# 예전에는 `except Exception: pass` 한 줄이었다. 훅이 Claude를 막으면 안 되므로
# **조용히 넘어가는 것 자체는 옳다.** 문제는 아무 흔적도 안 남긴 것이다 —
# 1,440번 실패가 보이지 않았다. 마지막 실패 한 줄만 파일에 남겨
# `fsh doctor`가 읽을 수 있게 한다(계속 덮어써서 무한히 자라지 않는다).
def _note_failure(reason: str) -> None:
    try:
        d = os.path.join(os.path.expanduser("~"), ".vt")
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, "hook-last-error")
        with open(path, "w") as f:
            f.write(f"{event}\t{reason}\n")
        os.chmod(path, 0o600)
    except Exception:
        pass


def _clear_failure() -> None:
    try:
        os.remove(os.path.join(os.path.expanduser("~"), ".vt", "hook-last-error"))
    except Exception:
        pass


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
        "agent": agent,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{server}/api/agent/event",
        data=body,
        headers=headers,
    )
    urllib.request.urlopen(req, timeout=2)
    _clear_failure()
except urllib.error.HTTPError as e:
    # 401/403은 "서버가 안 떠 있다"와 다르다 — 설정이 틀렸다는 뜻이라 반드시 남긴다.
    _note_failure(f"HTTP {e.code} {e.reason}")
except urllib.error.URLError as e:
    # 서버 미실행은 정상 상황이다(터미널만 쓰는 중). 기록은 하되 같은 무게로 두지 않는다.
    _note_failure(f"연결 실패: {e.reason}")
except Exception as e:
    _note_failure(f"{type(e).__name__}: {e}")
PYEOF

# ── Stop → TTS 요약 위임 ────────────────────────────────────────────────
#
# **음성 데몬이 떠 있을 때만 소리를 낸다**(2026-09-17 결정, 원래 설계로 되돌림).
# 그 전에는 조건 없이 위임해서, 음성을 쓰지 않는 동안에도 Claude가 멈출 때마다
# `afplay`/`say`가 응답 끝 200자를 읽었다. 토큰이 드는 일은 아니지만(이 훅은
# LLM을 부르지 않는다 — transcript 파일에서 마지막 text 블록을 잘라낼 뿐이다)
# 듣지 않는 소리를 내는 것 자체가 무의미하다.
#
# ⚠ **위쪽의 `POST /api/agent/event`는 이 조건에 걸지 않는다.** 그건 상태 배지
# (idle/working/waiting/done)·프롬프트 큐 자동 투입·감지 커버리지의 전제라,
# 음성으로 게이팅하면 음성을 안 켠 사람에게서 그 기능들이 통째로 죽는다.
# 소리를 내는 부분만 잠근다.
#
# 판정은 `fsh`의 `_is_running voice`와 **같은 기준**이다(PID 파일 + kill -0).
# 다른 방법(pgrep 등)을 쓰면 한 머신에서 인스턴스를 둘 돌릴 때 남의 데몬을
# 자기 것으로 오인한다 — bin/fsh가 pkill을 버린 것과 같은 이유다.
_voice_running() {
    local pidfile="${VT_RUN_DIR:-/tmp}/vt-pids/voice.pid"
    [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null
}

if [ "$EVENT" = "stop" ] && _voice_running; then
    HOOK_DIR="$(dirname "$0")"
    if [ -x "$HOOK_DIR/tts_hook.sh" ]; then
        cat "$TMPINPUT" | "$HOOK_DIR/tts_hook.sh" || true
    fi
fi

exit 0
