#!/bin/bash
# Claude Code Stop hook — 작업 완료 알림(완료음 또는 응답 요약 낭독) + ntfy 푸시
# stdin: JSON {"session_id":"...","transcript_path":"..."}
#
# 무엇을 할지는 설정 「음성 및 알림」(voice.notifyMode 등)이 정한다 — 판정 로직은
# voice_notify.py(순수 함수). 기본은 off라 아무 소리도 안 난다.
# agent_hook.sh가 Stop마다 이 스크립트를 부른다(음성 데몬 실행 여부와 무관).

set -euo pipefail

TMPINPUT=$(mktemp)
cat > "$TMPINPUT"

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
# 시스템 python3 대신 저장소 venv를 먼저 쓴다(설치 환경마다 python3가 다를 수 있다).
PY="$HOOK_DIR/../.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || exit 0

HOOK_DIR="$HOOK_DIR" "$PY" - "$TMPINPUT" << 'PYEOF'
import json, sys, subprocess, os, urllib.request, re, shutil, tempfile, time
from datetime import datetime, timezone

HOOK_DIR = os.environ["HOOK_DIR"]
sys.path.insert(0, HOOK_DIR)
import voice_notify

SERVER   = os.environ.get("VT_SERVER", "http://localhost:7777")
MAX_CHARS = 200
TMP_AUDIO = os.path.join(tempfile.gettempdir(), f"fsh_tts_{os.getuid()}.mp3")
WS_PATH = os.environ.get("VT_WORKSPACE_PATH") or os.path.expanduser("~/.config/vt/workspace.json")


def _vt_auth_token():
    """VT_AUTH_TOKEN(또는 레거시 VT_TOKEN) — **~/.vt.env를 먼저** 읽고, 없으면 프로세스 env.

    이 훅은 Claude Code가 띄운 프로세스의 자식이라 사용자 셸의 export 여부에
    기대면 안 된다(실측: 이 값이 셸 rc에서 export되지 않아 env로는 안 보였다).
    인증이 켜진 서버에 토큰 없이 /voice/output을 부르면 401이 나 항상
    macOS 'say' 폴백만 타게 된다 — 서버 TTS 엔진(edge-tts)을 못 씀.

    2026-09-17: 순서를 뒤집었다. 예전엔 프로세스 env가 우선이라, 토큰을 재발급하면
    **옛 값을 들고 기동해 있던 에이전트 세션이 전부 조용히 401**이 됐다. 자격증명은
    낡은 사본이 이기면 안 된다 — agent_hook.sh가 같은 이유로 같은 순서를 쓴다.
    """
    found = {}
    try:
        with open(os.path.expanduser("~/.vt.env")) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key in ("VT_AUTH_TOKEN", "VT_TOKEN"):
                    found[key] = value.strip().strip("'\"")
    except OSError:
        pass
    return (found.get("VT_AUTH_TOKEN") or found.get("VT_TOKEN")
            or os.environ.get("VT_AUTH_TOKEN") or os.environ.get("VT_TOKEN") or "")


VT_AUTH_TOKEN = _vt_auth_token()


def _auth_headers(extra=None):
    h = dict(extra or {})
    if VT_AUTH_TOKEN:
        h["Authorization"] = f"Bearer {VT_AUTH_TOKEN}"
    return h


def _load_policy():
    """workspace.json의 voice.notify* 설정 → 정규화된 정책. 읽을 수 없으면 off."""
    try:
        with open(WS_PATH) as f:
            return voice_notify.normalize((json.load(f).get("settings") or {}))
    except Exception:
        return voice_notify.normalize({})


def _web_clients():
    """웹 화면에 붙어 있는 클라이언트 수. 서버에 못 닿으면 0(=아무도 안 보고 있다고 본다)."""
    try:
        req = urllib.request.Request(f"{SERVER}/voice/notify/clients", headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=2) as r:
            return int(json.load(r).get("web_clients", 0))
    except Exception:
        return 0


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_user_prompt(msg):
    """도구 결과·메타가 아닌, 사람이 친 프롬프트인가."""
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
                   for p in content)
    return False


def _read_transcript(path):
    """(마지막 assistant 객체, 마지막 사용자 프롬프트 시각, 이번 턴의 마지막 응답 본문).

    본문은 마지막 프롬프트 이후 text가 있는 assistant 메시지 중 마지막 것이다 — 턴이
    도구 호출 메시지로 끝나도 직전 text를 읽는다.

    Claude Code 트랜스크립트는 줄마다 {"type": "assistant"|"user", "message": {"role", "content"},
    "timestamp"} 형태다. 예전 가정(최상위 "role")도 같이 받는다.
    """
    last_assistant, last_user_ts, last_text = None, None, ""
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("isSidechain"):
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            role = msg.get("role") or obj.get("type")
            if role == "assistant":
                last_assistant = {"msg": msg, "obj": obj}
                t = _last_text(msg)
                if t:
                    last_text = t
            elif role == "user" and not obj.get("isMeta") and _is_user_prompt(msg):
                ts = _parse_ts(obj.get("timestamp"))
                if ts:
                    last_user_ts = ts
                last_text = ""
    return last_assistant, last_user_ts, last_text


def _play_sound():
    """완료음. 못 내면 터미널 벨이 아니라 조용히 포기한다(훅은 어떤 경우에도 죽지 않는다)."""
    try:
        if shutil.which("afplay") and os.path.exists("/System/Library/Sounds/Glass.aiff"):
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        elif shutil.which("canberra-gtk-play"):
            subprocess.Popen(["canberra-gtk-play", "-i", "complete"])
        elif shutil.which("paplay") and os.path.exists("/usr/share/sounds/freedesktop/stereo/complete.oga"):
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"])
    except Exception:
        pass


def _say_fallback(text):
    if shutil.which("say"):
        subprocess.Popen(["say", "-v", "Yuna", text])
    elif shutil.which("espeak-ng"):
        subprocess.Popen(["espeak-ng", "-v", "ko", text])
    elif shutil.which("spd-say"):
        subprocess.Popen(["spd-say", text])


def _speak(text):
    """서버 TTS → 재생. 서버가 안 되면 OS 음성으로 폴백."""
    try:
        req = urllib.request.Request(
            f"{SERVER}/voice/output",
            data=json.dumps({"text": text}).encode(),
            headers=_auth_headers({"Content-Type": "application/json"}),
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            with open(TMP_AUDIO, "wb") as f:
                f.write(resp.read())
        if shutil.which("afplay"):
            subprocess.Popen(["afplay", TMP_AUDIO])
        elif shutil.which("mpg123"):
            subprocess.Popen(["mpg123", "-q", TMP_AUDIO])
        else:
            _say_fallback(text)
    except Exception:
        _say_fallback(text)


def _last_text(msg):
    """마지막 text 블록만 — tool_use/tool_result/thinking 제외. 여러 블록이면 마지막이 최종 답."""
    content = msg.get("content", "")
    if isinstance(content, list):
        blocks = [p.get("text", "").strip() for p in content
                  if isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()]
        return blocks[-1] if blocks else ""
    if isinstance(content, str):
        return content.strip()
    return ""


def _tail_sentence(text):
    """길이 제한: 마지막 MAX_CHARS 문자(결론은 끝부분에). 첫 불완전 문장은 잘라낸다."""
    if len(text) <= MAX_CHARS:
        return text
    tail = text[-MAX_CHARS:]
    m = re.search(r'[.!?\n]', tail)
    if m and m.start() < MAX_CHARS // 2:
        tail = tail[m.start() + 1:].lstrip()
    return tail if tail else text[-MAX_CHARS:]


input_file = sys.argv[1]
try:
    with open(input_file) as f:
        input_data = json.load(f)
finally:
    os.unlink(input_file)

policy = _load_policy()
notify_url = os.environ.get("VT_NOTIFY_URL", "").strip()
# 알림이 꺼져 있고 푸시 대상도 없으면 트랜스크립트를 읽을 이유가 없다.
if policy["mode"] == "off" and not notify_url:
    sys.exit(0)

transcript_path = input_data.get("transcript_path", "")
if not transcript_path or not os.path.isfile(transcript_path):
    sys.exit(0)

last, user_ts, last_text = _read_transcript(transcript_path)
if not last:
    sys.exit(0)

text = _tail_sentence(last_text)
elapsed = None
if user_ts:
    elapsed = (datetime.now(timezone.utc) - user_ts).total_seconds()
ok = not last["obj"].get("isApiErrorMessage", False)

action = voice_notify.decide(
    policy, elapsed, ok,
    _web_clients() if (policy["mode"] != "off" and policy["only_unfocused"]) else 0,
)

if action == "sound":
    _play_sound()
elif action == "speak" and text:
    _speak(text)
elif action == "speak":
    _play_sound()   # 읽을 본문이 없으면(도구 호출로만 끝난 턴) 소리만

# ── 모바일 푸시 (ntfy) — VT_NOTIFY_URL을 둔 사람의 명시적 설정이라 알림 방식과 무관 ──
if notify_url and text:
    try:
        push_req = urllib.request.Request(
            notify_url,
            data=text.encode("utf-8"),
            headers={
                "Title": "Claude 완료".encode("utf-8"),
                "Priority": "default",
                "Tags": "robot",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        urllib.request.urlopen(push_req, timeout=3)
    except Exception:
        pass
PYEOF
