"""레일에서 숨긴 저장소 목록 (98-rail-repos-2.1.6.md §3).

`~/powerlevel10k`(zsh 테마)나 `_tp-qa2-*`(QA 임시 워크트리)처럼 탐색에는
걸리지만 프로젝트가 아닌 것들을 목록에서 빼기 위한 저장소다. 지금까지는
`VT_BROWSE_ROOTS`로 경계를 통째로 좁히는 것 말고 방법이 없었다.

**기기 공유(서버 보관)다.** §2의 그룹 접힘이 device 스코프인 것과 반대 판단이다 —
접힘은 화면 상태지만 "이게 내 프로젝트인가"는 사람의 판단이라 폰에서 숨긴 것이
맥에서도 숨겨져야 한다.

**키는 경로다.** 이름은 중복되고(`v1`이 여럿) id는 경로 해시라, 경로가 진짜 키다.
저장소가 삭제돼 경로가 사라져도 목록에 남은 항목은 조용히 무시된다 — 없는 경로를
지우는 정리 작업을 따로 돌리지 않는다(다음 `set_hidden`에서 자연히 정리된다).

저장 규칙은 snippet_store·queue_store와 같다: 0700 디렉터리 + 0600 파일 +
atomic replace + flock.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1
# 숨김 목록 자체의 상한. 저장소 상한(worktree.MAX_REPOS = 200)과 같은 수를 쓴다 —
# 전부 숨기는 것도 가능해야 하고, 그 이상은 잘못 쓰이고 있다는 뜻이다.
MAX_HIDDEN = 200


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "rail-repos.json"


def _lock_path() -> Path:
    return _state_dir() / "rail-repos.lock"


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(str(_lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _normalize(path: str) -> str:
    """경로 키 정규화 — `~` 전개 + 끝 슬래시 제거.

    `resolve()`는 **하지 않는다.** 저장소가 지워진 뒤에도 목록의 항목을 그대로
    두고 비교할 수 있어야 하고, resolve는 없는 경로에서 심링크 해석이 달라진다.
    실제로 비교되는 쪽(`worktree.list_worktrees()`의 `path`)은 이미
    `fsguard.resolve_under_roots`를 통과한 값이라 같은 모양이다.
    """
    p = (path or "").strip()
    if not p:
        return ""
    p = str(Path(p).expanduser())
    return p.rstrip("/") or "/"


def _read_unlocked() -> list[str]:
    p = _path()
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"레일 저장소 설정 읽기 실패({e}) — 빈 목록으로 시작")
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("hidden")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        norm = _normalize(item)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:MAX_HIDDEN]


def _write_unlocked(hidden: list[str]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "hidden": hidden}, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- 공개 API ---------------------------------------------------------------


def list_hidden() -> list[str]:
    with _locked():
        return _read_unlocked()


def hidden_set() -> set[str]:
    return set(list_hidden())


def set_hidden(path: str, hidden: bool) -> dict:
    """한 저장소의 숨김 여부를 바꾼다. 결과는 `{ok, hidden: [...]}`."""
    norm = _normalize(path)
    if not norm:
        return {"ok": False, "error": "empty_path", "reason": "경로가 비어 있습니다"}
    with _locked():
        items = _read_unlocked()
        if hidden:
            if norm in items:
                return {"ok": True, "hidden": items}
            if len(items) >= MAX_HIDDEN:
                return {"ok": False, "error": "too_many",
                        "reason": f"숨길 수 있는 저장소는 최대 {MAX_HIDDEN}개입니다"}
            items.append(norm)
        else:
            if norm not in items:
                return {"ok": True, "hidden": items}
            items = [x for x in items if x != norm]
        _write_unlocked(items)
        return {"ok": True, "hidden": items}


def is_hidden(entry: dict, hidden: set[str]) -> bool:
    """워크트리 항목 하나가 숨김 대상인가.

    저장소 본체(`repo`)를 숨기면 그 저장소의 **부가 워크트리까지** 같이 숨는다 —
    사용자가 숨기는 단위는 "이 저장소"이지 "이 체크아웃 하나"가 아니다.
    """
    if not hidden:
        return False
    for key in ("path", "repo"):
        value = entry.get(key)
        if isinstance(value, str) and _normalize(value) in hidden:
            return True
    return False
