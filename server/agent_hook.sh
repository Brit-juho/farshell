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

# 백그라운드로 돌린 Claude 세션은 그 작업을 띄운 pane의 환경(TMUX_PANE)을 물려받았을 뿐 그
# pane에서 돌지 않는다 — 보내면 엉뚱한 세션 상태가 된다.
if [ -n "${CLAUDE_JOB_DIR:-}" ]; then
    exit 0
fi

# stdin 백업 — Stop 이벤트는 tts_hook.sh로도 전달해야 함
TMPINPUT=$(mktemp)
trap 'rm -f "$TMPINPUT"' EXIT
cat > "$TMPINPUT"

# 서버에 이벤트 전송 (timeout 짧게 — 훅이 Claude를 막으면 안 됨)
python3 - "$EVENT" "$TMPINPUT" "$SERVER" "$(dirname "$0")" "$AGENT" << 'PYEOF' || true
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

# 이벤트를 받은 시각 — 서버가 늦게 도착한 이벤트를 버리는 기준(agent_status.on_event `ts`).
received_ns = time.time_ns()

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
    # Claude Code 훅은 pane 셸의 자식이라 TMUX/TMUX_PANE을 상속받는다.
    # Codex는 공유 app-server 데몬에서 훅을 실행할 수 있어 이 값이 다른 pane의
    # 것일 수 있다. 호환성을 위해 필드는 그대로 보내고 서버가 CLI별로 판정한다.
    body = json.dumps({
        "event": event,
        "payload": payload,
        "pane": os.environ.get("TMUX_PANE"),
        "tmux": os.environ.get("TMUX"),
        "agent": agent,
        # 이 훅을 돌리는 셸의 pid. 서버가 요청을 처리하는 동안(이 스크립트는 응답을 기다린다)
        # 프로세스 표에서 그 위의 에이전트가 정말 그 pane의 포그라운드인지 대조한다
        # (server/hook_identity.py — 같은 pane을 물려받은 claude -p·데몬을 가려낸다).
        "hook_pid": os.getppid(),
        "ts": received_ns,
    }).encode()
    # 소켓 우선(plan-2.6 2단계): ~/.vt/run/fsh.sock 이 있으면 peer pid 로 서버가 이 훅의 pane·에이전트를 안다 —
    # 토큰이 필요 없다. 소켓이 없거나(옛 서버·VT_AUTHZ_SOCKET=0) 소켓 쪽이 거부하면 예전처럼 TCP + Bearer.
    # fsh_client 를 못 불러와도(설치가 깨진 경우) 훅은 죽지 않는다 — 아래 예전 경로로 내려간다.
    try:
        import fsh_client
    except Exception:
        fsh_client = None
    if fsh_client is not None:
        try:
            _port = urllib.parse.urlsplit(server).port
        except ValueError:
            _port = None
        fsh_client.call("POST", "/api/agent/event", body=body, timeout=2, svc="hook", server=server,
                        token=token, port=_port or os.environ.get("VT_PORT"), raise_for_status=True)
    else:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(f"{server}/api/agent/event", data=body, headers=headers)
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

# 완료 음성은 상태 이벤트를 받은 서버가 세션 정책에 따라 처리한다.
# 서버가 없으면 자동 OS 낭독으로 우회하지 않는다.
if [ "$EVENT" = "stop" ] && [ -x "$(dirname "$0")/tts_hook.sh" ]; then
    cat "$TMPINPUT" | "$(dirname "$0")/tts_hook.sh" "$AGENT" || true
fi
exit 0
