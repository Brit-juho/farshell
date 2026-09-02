"""프롬프트 스니펫 라이브러리 (L3) — 자주 쓰는 지시문을 저장해뒀다 원탭 실행.

프롬프트 큐(queue_store.py)와는 목적이 다르다. 큐는 "순서를 기다렸다가" 나가는
대기열이고, 스니펫은 대기 개념이 없다 — 저장해둔 텍스트를 지금 보고 있는
세션에 바로 주입한다(iTerm2 Snippets와 같은 개념). 그래서 이쪽엔 큐의 status/
target/drain 같은 상태 기계가 없다 — 순수 CRUD.

저장은 ~/.vt/snippets.json. queue_store.py와 동일한 규칙(0700 디렉토리 +
0600 파일 + atomic replace + flock)을 그대로 따른다 — 동시 쓰기(웹 여러 탭)에도
lost update가 안 나야 한다.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ITEMS = 100
MAX_TEXT_LEN = 8000
MAX_LABEL_LEN = 60


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "snippets.json"


def _lock_path() -> Path:
    return _state_dir() / "snippets.lock"


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    lp = _lock_path()
    fd = os.open(str(lp), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_unlocked() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"스니펫 파일 읽기 실패({e}) — 빈 목록으로 시작")
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("text")]


def _write_unlocked(items: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- 공개 API ---------------------------------------------------------------


def list_items() -> list[dict]:
    with _locked():
        return _read_unlocked()


def add(text: str, label: str | None = None) -> dict:
    text = (text or "").strip("\n")
    if not text.strip():
        return {"ok": False, "error": "empty", "reason": "빈 내용은 저장할 수 없습니다"}
    if len(text) > MAX_TEXT_LEN:
        return {"ok": False, "error": "too_long",
                "reason": f"내용이 너무 깁니다 (최대 {MAX_TEXT_LEN}자)"}
    label = (label or "").strip()[:MAX_LABEL_LEN]
    with _locked():
        items = _read_unlocked()
        if len(items) >= MAX_ITEMS:
            return {"ok": False, "error": "full",
                    "reason": f"스니펫이 가득 찼습니다 (최대 {MAX_ITEMS}개)"}
        item = {
            "id": uuid.uuid4().hex[:12],
            "label": label,
            "text": text,
            "created_at": time.time(),
        }
        items.append(item)
        _write_unlocked(items)
    return {"ok": True, "item": item, "count": len(items)}


def remove(item_id: str) -> dict:
    with _locked():
        items = _read_unlocked()
        rest = [x for x in items if x.get("id") != item_id]
        if len(rest) == len(items):
            return {"ok": False, "error": "not_found", "reason": "항목이 없습니다"}
        _write_unlocked(rest)
    return {"ok": True, "count": len(rest)}
