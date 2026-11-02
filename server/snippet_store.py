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

import json
import logging
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import fsguard
import git_safe
import store_lock
import vt_paths
from i18n import tr

logger = logging.getLogger(__name__)

MAX_ITEMS = 100
MAX_TEXT_LEN = 8000
MAX_LABEL_LEN = 60

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
VALID_SCOPES = {SCOPE_GLOBAL, SCOPE_PROJECT}

# 실행 방식은 스니펫이 아니라 **실행하는 순간** 고른다(2026-10-23): 이 세션에 입력 /
# 새 세션에서 실행. 저장할 때 미리 고르게 하면 같은 문구를 두 방식으로 쓰고 싶을
# 때 두 번 저장해야 했다. 대신 "마지막 줄까지 Enter를 칠지"는 본문이 정한다 —
# 본문 끝의 줄바꿈이 곧 마지막 Enter다(키보드에서 Enter를 친 것과 같은 모델).
# 옛 파일의 `mode`는 읽을 때 한 번 본문으로 옮기고 버린다(_read_unlocked).


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
            git_safe.argv("rev-parse", "--show-toplevel", cwd=p),
            capture_output=True, text=True, timeout=3, env=git_safe.env(),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def normalize_text(text: str | None) -> str:
    """앞쪽 빈 줄은 버리고, 끝의 줄바꿈은 **의미 있으므로** 한 개로 접어 보존한다.

    `"ls\n\n\n"`과 `"ls\n"`은 같은 뜻(마지막 줄까지 Enter)이다. 줄 끝 공백 때문에
    문자열이 달라지지 않게 `\r\n`도 `\n`으로 맞춘다.
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\n")
    body = t.rstrip("\n")
    if not body.strip():
        return ""
    return body + ("\n" if t.endswith("\n") else "")


def _state_dir() -> Path:
    # P2-4(ADR-41) — 예전엔 `Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()`.
    # `VT_STATE_DIR=""`은 폴백이 안 걸려 서버 CWD가 됐고 공백 패딩은 안 잘렸다. 단일 해석기로 교체한다.
    return vt_paths.state_dir()


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
    with store_lock.exclusive(_lock_path()):
        yield


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
    # 구 형식 마이그레이션 — scope 없음은 전체 스코프, mode는 본문으로 옮긴다:
    # 「새 섹션 자동 실행」이던 항목은 끝에 줄바꿈을 붙여 지금까지와 똑같이 Enter까지
    # 가게 하고, 「붙여넣기만」이던 항목은 본문 그대로(끝 줄바꿈 없음).
    for x in items:
        if x.get("scope") not in VALID_SCOPES:
            x["scope"] = SCOPE_GLOBAL
            x["project"] = None
        mode = x.pop("mode", None)
        text = normalize_text(x.get("text"))
        if mode == "new_section" and text and not text.endswith("\n"):
            text += "\n"
        x["text"] = text
    return [x for x in items if x["text"]]


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
    # `mode`는 옛 클라이언트가 보내도 받아주기만 하고 쓰지 않는다(위 주석).
    text = normalize_text(text)
    if not text:
        return {"ok": False, "error": "empty", "reason": tr("빈 내용은 저장할 수 없습니다")}
    if len(text) > MAX_TEXT_LEN:
        return {"ok": False, "error": "too_long",
                "reason": tr("내용이 너무 깁니다 (최대 {max}자)", max=MAX_TEXT_LEN)}
    label = (label or "").strip()[:MAX_LABEL_LEN]
    scope = scope if scope in VALID_SCOPES else SCOPE_GLOBAL
    project = None
    if scope == SCOPE_PROJECT:
        project = resolve_project_key(cwd)
        if not project:
            return {"ok": False, "error": "no_project",
                    "reason": tr("현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 저장할 수 있습니다")}
    with _locked():
        items = _read_unlocked()
        if len(items) >= MAX_ITEMS:
            return {"ok": False, "error": "full",
                    "reason": tr("스니펫이 가득 찼습니다 (최대 {max}개)", max=MAX_ITEMS)}
        item = {
            "id": uuid.uuid4().hex[:12],
            "label": label,
            "text": text,
            "scope": scope,
            "project": project,
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
            return {"ok": False, "error": "not_found", "reason": tr("항목이 없습니다")}
        _write_unlocked(rest)
    return {"ok": True, "count": len(rest)}


def update(item_id: str, *, text: str | None = None, label: str | None = None,
           scope: str | None = None, cwd: str | None = None) -> dict:
    """이름·본문·스코프를 고친다. 넘기지 않은(None) 항목은 그대로 둔다.

    프로젝트 스코프로 바꿀 때만 cwd가 필요하다(저장할 때와 같은 판정). 이미 프로젝트
    스니펫인데 스코프를 다시 project로 보내면 기존 project 키를 유지한다.
    """
    new_text = None
    if text is not None:
        new_text = normalize_text(text)
        if not new_text:
            return {"ok": False, "error": "empty", "reason": tr("빈 내용은 저장할 수 없습니다")}
        if len(new_text) > MAX_TEXT_LEN:
            return {"ok": False, "error": "too_long",
                    "reason": tr("내용이 너무 깁니다 (최대 {max}자)", max=MAX_TEXT_LEN)}
    if scope is not None and scope not in VALID_SCOPES:
        return {"ok": False, "error": "bad_scope", "reason": tr("scope는 {scopes} 중 하나", scopes=sorted(VALID_SCOPES))}
    with _locked():
        items = _read_unlocked()
        for x in items:
            if x.get("id") != item_id:
                continue
            if scope == SCOPE_PROJECT and x.get("scope") != SCOPE_PROJECT:
                project = resolve_project_key(cwd)
                if not project:
                    return {"ok": False, "error": "no_project",
                            "reason": tr("현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 저장할 수 있습니다")}
                x["scope"], x["project"] = SCOPE_PROJECT, project
            elif scope == SCOPE_GLOBAL:
                x["scope"], x["project"] = SCOPE_GLOBAL, None
            if new_text is not None:
                x["text"] = new_text
            if label is not None:
                x["label"] = (label or "").strip()[:MAX_LABEL_LEN]
            _write_unlocked(items)
            return {"ok": True, "item": x}
    return {"ok": False, "error": "not_found", "reason": tr("항목이 없습니다")}
