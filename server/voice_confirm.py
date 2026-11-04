"""음성 입력 「확인 후 전송」 — 정책 판정, 위험 명령 패턴, 1회용 확인 토큰 저장소.

`/voice/input`은 확인이 필요하면 인식만 하고 pane에 입력하지 않는다. 대신 이 모듈에서 확인 토큰을
받아 돌려주고, 사용자가 텍스트를 고쳐 「입력」을 누르면 `POST /voice/inject`가 토큰을 한 번만 쓴다.

정책(`voice.confirm`, 전역 설정):
  off    확인하지 않는다. 위험 패턴도 확인하지 않는다(사용자가 끈 것).
  shell  (기본) AI 에이전트 pane이면 바로 입력, 일반 셸이거나 **판별이 불확실하면** 확인.
  on     항상 확인.
위험 패턴은 off가 아니면 pane 종류와 무관하게 항상 확인한다.

이 모듈은 tmux·설정 파일만 건드린다. HTTP 계층은 routes/voice.py가 맡는다.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
import unicodedata
from typing import Optional

from i18n import tr

MODES = ("off", "shell", "on")
DEFAULT_MODE = "shell"
SETTING_KEY = "voice.confirm"

CONFIRM_TTL_SEC = 300.0        # 사용자가 텍스트를 고칠 시간. 1회용이라 길어도 재사용되지 않는다.
MAX_PENDING = 256
MAX_TEXT_CHARS = 4000          # 편집 후 텍스트 상한. 60초 녹음 인식 결과보다 넉넉하다.
_DONE_TTL_SEC = 120.0          # 입력이 끝난 토큰·녹음 ID를 기억하는 시간(중복 입력 방지)


# --- 위험 명령 패턴 ---------------------------------------------------------
# 한 곳에 모은다. (사유 코드, 정규식). 입력은 NFKC 정규화 + 공백 압축 후 대소문자 무시로 검사한다.
# 음성 인식 결과는 STT가 소문자·공백으로 정리해 주는 경우가 많아 서식에 덜 의존하게 썼다.
# 한국어로 받아 적힌 명령("수도", "알엠")은 잡지 못한다 — 이 목록은 영문 표기 기준이다.
DANGEROUS_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in (
        # rm -r / -f / -rf / -fr / --recursive / --force (플래그가 어디에 있든)
        ("rm", r"\brm\s+(?:[^|;&\n]*\s)?(?:-[a-z]*[rf][a-z]*|--recursive|--force)\b"),
        ("sudo", r"\bsudo\b"),
        # curl/wget ... | sh|bash|zsh (sudo 경유 포함), sh -c "$(curl ...)"
        ("pipe-to-shell", r"\b(?:curl|wget)\b[^|;&\n]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b"),
        ("pipe-to-shell", r"\b(?:ba|z|da|k)?sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b"),
        # git push -f / --force / --force-with-lease / +refspec
        ("git-force-push", r"\bgit\s+push\b[^|;&\n]*(?:\s-[a-z]*f[a-z]*\b|\s--force(?:-with-lease)?\b|\s\+\S)"),
        ("git-reset-hard", r"\bgit\s+reset\b[^|;&\n]*--hard\b"),
        ("git-clean", r"\bgit\s+clean\b[^|;&\n]*\s-[a-z]*f"),
        ("mkfs", r"\bmkfs(?:\.\w+)?\b"),
        ("dd", r"\bdd\b[^|;&\n]*\bof="),
        # 장치 파일로 리다이렉트. /dev/null·stdout·stderr는 일상적이라 제외한다.
        ("redirect-dev", r">>?\s*/dev/(?!(?:null|stdout|stderr)\b)"),
        ("chmod-chown-recursive", r"\b(?:chmod|chown)\s+(?:[^|;&\n]*\s)?(?:-[a-z]*r[a-z]*|--recursive)\b"),
        ("fork-bomb", r":\s*\(\s*\)\s*\{"),
    )
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


def dangerous_reason(text: str) -> Optional[str]:
    """위험 패턴에 걸리면 사유 코드, 아니면 None."""
    norm = _normalize(text)
    if not norm:
        return None
    for name, pat in DANGEROUS_PATTERNS:
        if pat.search(norm):
            return name
    return None


# --- 정책 ---------------------------------------------------------------------

def normalize_mode(value) -> str:
    return value if value in MODES else DEFAULT_MODE


def read_mode() -> str:
    """저장된 `voice.confirm`. 읽기 실패·알 수 없는 값은 기본값(shell) — 안전한 쪽."""
    try:
        import workspace
        return normalize_mode((workspace.load().get("settings") or {}).get(SETTING_KEY))
    except Exception:
        return DEFAULT_MODE


def pane_kind(pane_id: str) -> str:
    """pane의 포그라운드가 'agent'(AI 코딩 CLI) · 'shell'(빈 셸) · 'unknown'(그 밖/판별 실패).

    agent_detector의 판정(KNOWN_AGENTS, 셸 아래 자식 프로세스 스캔 포함)을 그대로 쓴다.
    vim·python·ssh·docker 같은 그 밖의 프로그램과 tmux 조회 실패는 'unknown' — 호출자가 확인 쪽으로 기운다."""
    try:
        import agent_detector
        import hook_identity
        import tmux_runner
        text = tmux_runner.run_text(
            ["display-message", "-p", "-t", pane_id, "#{pane_current_command}\t#{pane_pid}"], timeout=2.0)
        if not text:
            return "unknown"
        cmd, _, pid = text.strip("\n").partition("\t")
        resolved = agent_detector._resolve_command(int(pid), cmd)
        if resolved in agent_detector.KNOWN_AGENTS:
            return "agent"
        if hook_identity.is_shell(cmd):
            return "shell"
    except Exception:
        pass
    return "unknown"


def decide(mode: str, kind: str, text: str) -> Optional[str]:
    """확인이 필요하면 사유 코드('dangerous'|'always'|'shell'|'unknown'), 필요 없으면 None. 순수 함수."""
    mode = normalize_mode(mode)
    if mode == "off":
        return None
    if dangerous_reason(text):
        return "dangerous"
    if mode == "on":
        return "always"
    if kind == "agent":
        return None
    return "shell" if kind == "shell" else "unknown"


# --- 1회용 확인 토큰 -----------------------------------------------------------

class ConfirmError(Exception):
    """`code`는 클라이언트가 분기하는 안정적인 값: expired | voice_id | busy | done."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_lock = threading.Lock()
_pending: dict[str, dict] = {}
_done_vids: dict[tuple[str, str], float] = {}   # (principal, voice_id) → 입력 완료 시각


def _purge(now: float) -> None:
    for tok in [k for k, v in _pending.items() if v["expires"] < now and v["state"] != "sending"]:
        del _pending[tok]
    for key in [k for k, ts in _done_vids.items() if now - ts > _DONE_TTL_SEC]:
        del _done_vids[key]


def issue(owner: str, target: dict, voice_id: str, name: str) -> str:
    """확인 토큰을 발급한다. 한도를 넘으면 ValueError."""
    now = time.monotonic()
    with _lock:
        _purge(now)
        if len(_pending) >= MAX_PENDING:
            raise ValueError(tr("확인 대기 중인 음성 입력이 너무 많습니다"))
        token = secrets.token_urlsafe(24)
        _pending[token] = {"owner": owner, "target": dict(target), "voice_id": voice_id, "name": name,
                           "expires": now + CONFIRM_TTL_SEC, "state": "pending"}
        return token


def claim(token: str, owner: str, voice_id: str) -> dict:
    """토큰을 '전송 중'으로 잠근다 — 같은 토큰의 동시 요청은 하나만 통과한다.

    없음·만료·다른 주체의 토큰은 구분 없이 같은 expired를 낸다(존재 여부를 알려 주지 않는다).
    통과하면 {target, name}을 돌려준다. 끝나면 반드시 finish() 또는 release()."""
    now = time.monotonic()
    with _lock:
        _purge(now)
        item = _pending.get(token)
        if not item or item["owner"] != owner or item["expires"] < now:
            raise ConfirmError("expired", tr("확인 시간이 지났습니다. 인식 텍스트를 복사해 새로 녹음해 주세요"))
        if item["state"] == "sending":
            raise ConfirmError("busy", tr("이미 입력 중입니다"))
        if item["state"] == "done":
            raise ConfirmError("done", tr("이미 입력했습니다"))
        if item["voice_id"] != voice_id:
            raise ConfirmError("voice_id", tr("녹음 ID가 맞지 않습니다"))
        if voice_id and (owner, voice_id) in _done_vids:
            raise ConfirmError("done", tr("이미 입력했습니다"))
        item["state"] = "sending"
        return {"target": dict(item["target"]), "name": item["name"]}


def finish(token: str, owner: str, voice_id: str) -> None:
    """입력 성공 — 토큰을 소진하고 녹음 ID를 잠시 기억한다(다른 토큰으로의 중복 입력도 막는다)."""
    with _lock:
        item = _pending.get(token)
        if item:
            item["state"] = "done"
            item["expires"] = time.monotonic() + _DONE_TTL_SEC
        if voice_id:
            _done_vids[(owner, voice_id)] = time.monotonic()


def release(token: str, *, drop: bool = False) -> None:
    """입력하지 못했다. drop이면 토큰을 버리고(대상이 사라짐), 아니면 다시 시도할 수 있게 되돌린다."""
    with _lock:
        item = _pending.get(token)
        if not item:
            return
        if drop:
            del _pending[token]
        else:
            item["state"] = "pending"


def reset_for_tests() -> None:
    with _lock:
        _pending.clear()
        _done_vids.clear()
