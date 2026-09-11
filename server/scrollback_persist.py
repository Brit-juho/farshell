"""스크롤백 영속화 (N13, 80-multihost-agents.md §3) — 옵트인, 기본 OFF.

pty_manager의 인메모리 scrollback(재접속 시 최근 256KB만 복원)과는 별개다.
이건 디스크에 계속 쌓아서 재접속 "이후에도" 검색·「더 불러오기」로 과거 출력을
읽을 수 있게 하는 용도 — pty_manager._flush_session이 출력 청크를 만들 때마다
`append()`를 호출해준다.

- 저장 위치: `~/.vt/scrollback/<session_id>.log` (0600). 20MB 넘으면 `.log.1`로
  회전(기존 .1은 버림 — 세대는 1개만 유지, 무한정 쌓이는 걸 막는다).
- 보관 기간 7일: `cleanup_old()`가 mtime 기준으로 오래된 파일을 지운다. 별도
  "세션 종료" 이벤트를 추적하지 않는다 — 살아있는 세션은 계속 append돼 mtime이
  갱신되므로 자연히 죽거나 오래 쉰 세션의 로그만 늙는다.
- **입력은 절대 기록하지 않는다.** append()는 PTY 출력 스트림(_flush_session)에서만
  호출된다 — 사용자가 타이핑한 입력(session.write 경로)은 이 모듈을 거치지 않는다.
  비밀번호 프롬프트 등이 로그에 남지 않게 하는 설계상 경계다.
- on/off 판정(`is_enabled`)은 매 flush마다(빈번, 2ms 배치 창) workspace.json을
  다시 읽으면 비용이 크므로 TTLCache로 짧게(3초) 캐싱한다 — 토글은 몇 초 안에
  반영되면 충분하다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

MAX_LOG_BYTES = 20 * 1024 * 1024  # 20MB — 넘으면 회전
RETENTION_DAYS = 7
_SETTING_KEY = "scrollback.persist"
_enabled_cache: TTLCache[bool] = TTLCache(ttl=3.0)


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def scrollback_dir() -> Path:
    return _state_dir() / "scrollback"


def _log_path(session_id: str) -> Path:
    return scrollback_dir() / f"{session_id}.log"


def is_enabled() -> bool:
    def _fetch() -> bool:
        import workspace
        return bool(workspace.load().get("settings", {}).get(_SETTING_KEY, False))
    return _enabled_cache.get_or_fetch("enabled", _fetch)


def append(session_id: str, data: bytes) -> None:
    """PTY 출력 청크를 영속 로그에 이어붙인다. persist가 꺼져 있으면 아무 것도 안 한다."""
    if not data or not is_enabled():
        return
    d = scrollback_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        p = _log_path(session_id)
        _rotate_if_needed(p)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as f:
            f.write(data)
    except OSError as e:
        logger.warning(f"scrollback 영속화 실패({session_id}): {e}")


def _rotate_if_needed(p: Path) -> None:
    try:
        if p.is_file() and p.stat().st_size >= MAX_LOG_BYTES:
            rotated = p.with_suffix(p.suffix + ".1")
            os.replace(str(p), str(rotated))
    except OSError:
        pass


def read_before(session_id: str, before: int | None, limit: int) -> dict:
    """「더 불러오기」— 현재 로그(.log) 뒤에서부터, `.1` 세대까지 이어서 읽는다.

    `before`는 "파일 끝에서부터의 오프셋"(이전 응답이 돌려준 `next_before`)이다.
    처음 호출은 before=None → 파일 맨 끝부터. limit 바이트만큼 잘라 반환하고,
    아직 더 남았으면 `next_before`를 함께 준다(없으면 None — 더 없음).
    """
    p = _log_path(session_id)
    rotated = p.with_suffix(p.suffix + ".1")
    # 논리적으로 하나로 이어붙인 스트림 취급: [.1의 내용][.log의 내용]
    parts = []
    for f in (rotated, p):
        if f.is_file():
            parts.append(f.read_bytes())
    blob = b"".join(parts)
    total = len(blob)
    if total == 0:
        return {"data": b"", "next_before": None, "total": 0}
    end = total if before is None else max(0, min(before, total))
    start = max(0, end - limit)
    chunk = blob[start:end]
    next_before = start if start > 0 else None
    return {"data": chunk, "next_before": next_before, "total": total}


def disk_usage_bytes() -> int:
    """설정 화면(4e)의 "디스크 사용량" 표시용."""
    d = scrollback_dir()
    if not d.is_dir():
        return 0
    total = 0
    for f in d.iterdir():
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def cleanup_old(days: int = RETENTION_DAYS) -> int:
    """mtime 기준 `days`일 넘은 로그(.log/.log.1)를 지운다. 삭제 수 반환."""
    import time
    d = scrollback_dir()
    if not d.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"scrollback_persist cleanup: {removed}개 삭제(7일 초과)")
    return removed
