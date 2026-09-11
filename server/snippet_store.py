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
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import fsguard

logger = logging.getLogger(__name__)

MAX_ITEMS = 100
MAX_TEXT_LEN = 8000
MAX_LABEL_LEN = 60

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
VALID_SCOPES = {SCOPE_GLOBAL, SCOPE_PROJECT}

# 실사용 요청(2026-09-11) — 스니펫마다 "누르면 뭘 하는지"가 갈린다.
#   new_section: 새 섹션(페인)을 만들어 텍스트를 넣고 Enter까지 눌러 바로 실행한다.
#   paste:       지금 보고 있는 세션에 텍스트만 넣는다 — Enter는 안 누른다. 값을
#                끼워 넣거나 실행 전에 고쳐야 하는 스니펫(경로·플래그가 매번
#                달라지는 것 등)은 자동 실행되면 오히려 위험하다.
# 행마다 버튼 두 개(여기서/새 섹션)를 매번 고르게 하는 대신, 저장할 때 한 번
# 고르고 그 뒤로는 버튼 하나로 "이 스니펫이 하는 일"이 고정되게 한다.
MODE_NEW_SECTION = "new_section"
MODE_PASTE = "paste"
VALID_MODES = {MODE_NEW_SECTION, MODE_PASTE}
DEFAULT_MODE = MODE_PASTE  # 모르면 안전한 쪽 — 자동 실행보다 "일단 넣기만"이 사고가 적다.


def resolve_project_key(cwd: str | None) -> str | None:
    """cwd → 그 저장소의 top 경로(60 §4 "프로젝트 = 활성 페인의 저장소 top 경로 키").

    fsguard 경계 밖이거나 git 저장소가 아니면 None — 실패를 에러로 올리지 않는다.
    이 판정을 쓰는 두 곳(스니펫 저장 시 project 채우기, 목록 화면의 「프로젝트」
    탭 필터)이 항상 같은 값을 봐야 탭에 안 걸리는 항목이 안 생긴다.

    routes/files.py의 `_git_toplevel`과 같은 명령(`git rev-parse --show-toplevel`)을
    쓰지만 별도로 둔다 — 그쪽은 이미 fsguard로 검증된 Path를 받는 반면, 여기는
    클라이언트가 보낸 원시 cwd 문자열부터 fsguard 검증까지 직접 해야 한다.
    """
    if not cwd:
        return None
    try:
        p = fsguard.resolve_under_roots(cwd)
    except fsguard.FsDenied:
        return None
    if not p.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


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
    items = [x for x in data if isinstance(x, dict) and x.get("text")]
    # 구 형식(scope/mode 없음) 마이그레이션 — 전부 전체 스코프·기본 모드였다.
    for x in items:
        if x.get("scope") not in VALID_SCOPES:
            x["scope"] = SCOPE_GLOBAL
            x["project"] = None
        if x.get("mode") not in VALID_MODES:
            x["mode"] = DEFAULT_MODE
    return items


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


def add(text: str, label: str | None = None, scope: str = SCOPE_GLOBAL,
        cwd: str | None = None, mode: str | None = None) -> dict:
    text = (text or "").strip("\n")
    if not text.strip():
        return {"ok": False, "error": "empty", "reason": "빈 내용은 저장할 수 없습니다"}
    if len(text) > MAX_TEXT_LEN:
        return {"ok": False, "error": "too_long",
                "reason": f"내용이 너무 깁니다 (최대 {MAX_TEXT_LEN}자)"}
    label = (label or "").strip()[:MAX_LABEL_LEN]
    scope = scope if scope in VALID_SCOPES else SCOPE_GLOBAL
    mode = mode if mode in VALID_MODES else DEFAULT_MODE
    project = None
    if scope == SCOPE_PROJECT:
        project = resolve_project_key(cwd)
        if not project:
            return {"ok": False, "error": "no_project",
                    "reason": "현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 저장할 수 있습니다"}
    with _locked():
        items = _read_unlocked()
        if len(items) >= MAX_ITEMS:
            return {"ok": False, "error": "full",
                    "reason": f"스니펫이 가득 찼습니다 (최대 {MAX_ITEMS}개)"}
        item = {
            "id": uuid.uuid4().hex[:12],
            "label": label,
            "text": text,
            "scope": scope,
            "project": project,
            "mode": mode,
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


def set_mode(item_id: str, mode: str) -> dict:
    """저장된 스니펫의 실행 방식(새 섹션 자동 실행 / 붙여넣기만)을 나중에 바꾼다
    — 처음 저장할 때 고른 모드를 되돌릴 수 없으면 "관리"가 아니라 "한 번 선택"이다."""
    if mode not in VALID_MODES:
        return {"ok": False, "error": "bad_mode", "reason": f"mode는 {sorted(VALID_MODES)} 중 하나"}
    with _locked():
        items = _read_unlocked()
        for x in items:
            if x.get("id") == item_id:
                x["mode"] = mode
                _write_unlocked(items)
                return {"ok": True, "item": x}
    return {"ok": False, "error": "not_found", "reason": "항목이 없습니다"}
