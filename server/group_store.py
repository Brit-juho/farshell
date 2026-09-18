"""세션 그룹 이름·순서 — ADR-29(세션 중심 재설계) A단계.

**소속 자체는 여기 없다.** "어느 세션이 이 그룹인가"는 tmux 세션 자신의
`@fsh_grp` 커스텀 옵션에 적힌다(`tmux_runner.set_option`, `@fsh_wt`와 같은
메커니즘) — 세션이 자고 있어도(웹 세션이 없어도) 살아남고, 세션이 죽으면
같이 사라져서 따로 정리할 게 없다.

이 파일이 들고 있는 건 **사용자가 직접 지은 것**뿐이다: 그룹 이름과 화면
순서. 그룹의 정체(id)는 두 갈래다 — (1) 아직 아무도 안 만진 그룹은
`repo_store.repo_id(host, path)`를 그대로 쓴다(레일이 저장소별로 자동
제안하는 그 값), (2) 사용자가 이름을 바꾸거나 새로 만들면 그때부터 이 파일에
레코드가 생긴다. 그래서 "한 번도 안 만지면 지금과 같은 화면"이 자동으로
성립한다 — 이 저장소가 비어 있어도 레일은 저장소별 그룹으로 정상 동작한다.

저장 규칙은 repo_store.py와 같다: 0700 디렉터리 + 0600 파일 + flock +
atomic replace.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1
# 그룹 상한 — repo_store.MAX_REPOS와 같은 수. 그 이상은 화면이 아니라
# 실수(자동화 스크립트가 계속 새 그룹을 만드는 등)로 본다.
MAX_GROUPS = 200


def new_group_id() -> str:
    """사용자가 새로 묶을 때만 부른다(기존 저장소 자동 제안은 repo_id를 그대로 쓴다).
    저장소·워크트리 id(sha1 12자)와 자릿수만 맞춘 임의 id — 그룹은 내용에서
    유도할 안정적인 입력이 없으므로(사용자가 그냥 두 세션을 겹쳤을 뿐) 해시가
    아니라 난수다."""
    return uuid.uuid4().hex[:12]


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "groups.json"


def _lock_path() -> Path:
    return _state_dir() / "groups.lock"


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


def _read_unlocked() -> dict:
    """`{order: [id, ...], labels: {id: label}}`. order와 labels를 나눈 이유:
    이름을 아직 안 지은(그냥 순서만 바뀐) 그룹에 가짜 라벨을 채우지 않기 위해서다."""
    p = _path()
    if not p.is_file():
        return {"order": [], "labels": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"그룹 설정 읽기 실패({e}) — 빈 목록으로 시작")
        return {"order": [], "labels": {}}
    if not isinstance(data, dict):
        return {"order": [], "labels": {}}
    order = [i for i in data.get("order", []) if isinstance(i, str) and i][:MAX_GROUPS]
    labels_raw = data.get("labels", {})
    labels = {k: v for k, v in labels_raw.items() if isinstance(k, str) and isinstance(v, str) and v} \
        if isinstance(labels_raw, dict) else {}
    return {"order": order, "labels": labels}


def _write_unlocked(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "order": data["order"], "labels": data["labels"]}, f,
                   indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- 공개 API ----------------------------------------------------------------


def list_groups() -> list[dict]:
    """저장된 순서를 따르는 `{id, label}` 목록. 이름을 아직 안 지었으면
    `label`은 `None`(호출자가 저장소 이름 등으로 채운다)."""
    with _locked():
        data = _read_unlocked()
        return [{"id": i, "label": data["labels"].get(i)} for i in data["order"]]


def get_label(group_id: str) -> str | None:
    with _locked():
        return _read_unlocked()["labels"].get(group_id)


def set_label(group_id: str, label: str) -> dict:
    """그룹 이름을 짓거나 바꾼다. 처음 만지는 그룹(예: 저장소 자동 제안 id)이면
    이 호출로 처음 레코드가 생긴다 — order에 없었으면 맨 뒤에 붙는다."""
    group_id = (group_id or "").strip()
    label = (label or "").strip()
    if not group_id:
        return {"ok": False, "error": "empty_id", "reason": "그룹 id가 비어 있습니다"}
    if not label:
        return {"ok": False, "error": "empty_label", "reason": "이름이 비어 있습니다"}
    with _locked():
        data = _read_unlocked()
        if group_id not in data["order"]:
            if len(data["order"]) >= MAX_GROUPS:
                return {"ok": False, "error": "too_many",
                        "reason": f"그룹은 최대 {MAX_GROUPS}개까지 만들 수 있습니다"}
            data["order"].append(group_id)
        data["labels"][group_id] = label
        _write_unlocked(data)
        return {"ok": True, "id": group_id, "label": label}


def set_order(order: list) -> dict:
    """화면 순서를 통째로 갈아끼운다(드래그 재배열). 중복은 첫 등장만 남기고,
    라벨이 있던 그룹이 이 목록에서 빠지면 라벨도 함께 잊는다 — order가
    "지금 존재하는 그룹 전체"의 단일 진실이다."""
    if not isinstance(order, list):
        return {"ok": False, "error": "invalid_order", "reason": "order는 배열이어야 합니다"}
    with _locked():
        data = _read_unlocked()
        seen: set[str] = set()
        clean: list[str] = []
        for i in order:
            if isinstance(i, str) and i and i not in seen:
                seen.add(i)
                clean.append(i)
            if len(clean) >= MAX_GROUPS:
                break
        data["order"] = clean
        data["labels"] = {k: v for k, v in data["labels"].items() if k in seen}
        _write_unlocked(data)
        return {"ok": True, "order": clean}
