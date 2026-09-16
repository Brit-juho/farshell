"""peer 감사 로그 — `~/.vt/host-audit.jsonl`.

"누가 언제 무엇을 했는가"는 원격 기능이 늘어날수록 **더 중요해지는데**
저장소 코드와 섞여 있으면 그 사실이 안 보인다. 실패도 전부 남긴다 —
취소된 상대가 옛 secret으로 계속 두드리는 것이 여기서 드러난다.

경로는 호출 시점에 `VT_STATE_DIR`를 읽는다(모듈 전역에 굳히지 않는다) —
테스트가 환경변수만 바꾸면 격리된다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _chmod_quiet(p: Path, mode: int) -> None:
    """권한 조정 실패로 로그 기록을 포기하지 않는다 — 로그가 본체가 아니다.
    (host_store/__init__.py에도 같은 헬퍼가 있다. 서로 import하지 않으려고
    4줄을 중복해 둔다 — 이 모듈이 저장소 쪽에 의존하면 분리한 의미가 없다.)"""
    try:
        os.chmod(p, mode)
    except OSError:
        pass

# 공유 링크(routes/share.py)가 실패를 logger.warning으로만 남기는 것과 달리, 이쪽은
# 성공까지 전부 남긴다 — 나중에 "저 맥이 언제 뭘 봤나"를 되짚을 수 있어야 하기 때문.
# 한 줄 JSON(JSONL) — 회전은 scrollback_persist와 같은 방식으로 단순하게.

AUDIT_MAX_BYTES = 5 * 1024 * 1024


def _audit_path() -> Path:
    return _state_dir() / "peer_audit.log"


def audit(peer_id: str, action: str, ok: bool, detail: str = "") -> None:
    """peer 요청 1건 기록. 실패해도 요청 처리를 막지 않는다(로그가 본체가 아니다)."""
    line = json.dumps({
        "ts": int(time.time()), "peer": peer_id, "action": action,
        "ok": bool(ok), "detail": detail[:200],
    }, ensure_ascii=False)
    try:
        p = _audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _chmod_quiet(p.parent, 0o700)
        if p.is_file() and p.stat().st_size >= AUDIT_MAX_BYTES:
            os.replace(str(p), str(p.with_suffix(".log.1")))
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning(f"peer 감사 로그 기록 실패: {e}")


def read_audit(peer_id: str = "", limit: int = 50) -> list[dict]:
    """최근 기록부터 limit건. peer_id를 주면 그 상대 것만."""
    p = _audit_path()
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if peer_id and rec.get("peer") != peer_id:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out
