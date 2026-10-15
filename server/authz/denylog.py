"""거부 기록 — `~/.vt/audit/denials.jsonl` (0600, 해시 체인 없음).

거부(정책 거절·속도 제한·잠금·인증 실패)는 폭주할 수 있다. 주 감사 체인(`audit/authz.jsonl`)에 같이 쓰면 정당한 변경 이력이
회전으로 밀려나므로 **별도 파일**에 쓴다. 주 체인에는 허용 판정과 모든 authz·pair·lease·grant 변경(사람의 거절 결정 포함)이 남는다.

  - 1MB 회전, 3세대(현재 + 이전 둘). 오래된 것부터 지운다.
  - 분당 60줄 상한. 넘긴 것은 세지 않고 버리며, 다음 분의 첫 기록 앞에 `{"action":"denylog.dropped","dropped":N}` 한 줄을 남긴다.
  - **기록 실패는 요청을 막지 않는다**(예외를 삼킨다). 변경을 `503 audit_unavailable` 로 막는 주 체인과 반대다.
  - 내용은 기록하지 않는다 — 필드는 `audit.scrub` 가 거른다(입력 텍스트·화면·메시지·URL query·쿠키·비밀은 버려진다).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import store_lock
import vt_paths

from . import audit

logger = logging.getLogger(__name__)

MAX_BYTES = 1024 * 1024
GENERATIONS = 3                 # 현재 파일 + 회전본 둘
PER_MINUTE = 60

# 주 체인에 남는 거절 — 사람이 요청을 거절한 결정·회수 같은 변경은 거부 판정이 아니라 변경 이력이다.
MUTATION_SUFFIXES = (".decide", ".revoke", ".grant", ".panic", ".end", ".cascade", ".adopt")


def path() -> Path:
    return audit.path().with_name("denials.jsonl")


def is_denial(rec: dict) -> bool:
    """이 감사 기록을 거부 기록으로 보내는가: `decision == deny` 이고 변경(결정·회수·…)이 아닐 때."""
    if str(rec.get("decision") or "") != "deny":
        return False
    return not str(rec.get("action") or "").endswith(MUTATION_SUFFIXES)


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.minute = -1
        self.count = 0
        self.dropped = 0
        self.total_dropped = 0
        self.failed = 0                       # 쓰지 못한 횟수(fsh doctor·테스트용 메모리 카운터)


STATE = _State()
_clock: Callable[[], float] = time.time


def reset() -> None:
    """프로세스 안 카운터를 비운다. 테스트 격리용."""
    global STATE
    STATE = _State()


def _rotated(p: Path, i: int) -> Path:
    return p.with_name(f"{p.name}.{i}")


def _rotate(p: Path) -> None:
    keep = GENERATIONS - 1                    # 회전본 개수
    for i in range(keep, 1, -1):              # .1 → .2 (기존 .2 는 덮여 사라진다)
        src = _rotated(p, i - 1)
        if src.exists():
            os.replace(src, _rotated(p, i))
    os.replace(p, _rotated(p, 1))


def _write_line(p: Path, rec: dict) -> None:
    line = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(p.with_name("denials.lock")):
        try:
            if p.exists() and p.stat().st_size + len(line) > MAX_BYTES:
                _rotate(p)
        except OSError:
            pass
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as f:
            f.write(line)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def record(rec: dict, *, now: Optional[float] = None) -> bool:
    """거부 한 건. 쓰면 True, 상한에 걸려 버렸거나 쓰지 못하면 False. **절대 예외를 내지 않는다.**"""
    try:
        now = _clock() if now is None else now
        st = STATE
        with st.lock:
            minute = int(now // 60)
            if minute != st.minute:
                dropped, prev = st.dropped, st.minute
                st.minute, st.count, st.dropped = minute, 0, 0
                if dropped:
                    try:
                        _write_line(path(), {"ts": round(now, 3), "action": "denylog.dropped", "dropped": dropped,
                                             "minute": prev * 60, "decision": "deny", "source": "denials"})
                    except Exception as e:  # noqa: BLE001
                        st.failed += 1
                        logger.debug(f"거부 기록 누락 줄 쓰기 실패: {e}")
            if st.count >= PER_MINUTE:
                st.dropped += 1
                st.total_dropped += 1
                return False
            st.count += 1
        out = {**audit.scrub(rec), "ts": round(now, 3), "source": "denials"}
        out.pop("seq", None)
        out.pop("prev_sha", None)
        _write_line(path(), out)
        return True
    except Exception as e:  # noqa: BLE001 — 거부 기록 실패가 요청을 막으면 안 된다
        try:
            STATE.failed += 1
        except Exception:  # noqa: BLE001
            pass
        logger.debug(f"거부 기록 실패: {e}")
        return False


def read_recent(limit: int) -> list[dict]:
    """최근 줄(새 것 먼저). 현재 파일과 회전본을 본다."""
    limit = max(1, min(int(limit), 1000))
    out: list[dict] = []
    p = path()
    for fp in [p] + [_rotated(p, i) for i in range(1, GENERATIONS)]:
        try:
            lines = fp.read_bytes().splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
                if len(out) >= limit:
                    return out
    return out
