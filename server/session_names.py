"""tmux 세션 이름 짓기 — 한 곳(세션 로직 점검 단계 1).

이름(ID)은 세션을 만들 때 한 번 정하고 서버가 나중에 바꾸지 않는다. 새 세션은 랜덤 ID다(아래 「ID와 별칭」). 세션을 가리키는 열쇠는 이름이 아니라
정체성 키(tmux 세션 id와 생성 시각)다. 사람이 보는 이름(별칭)은 따로 둔다(설계 S1).

규칙
- 기준(base): 시작 폴더가 Git 저장소 안이면 그 저장소(메인 작업 트리) 폴더 이름, 링크드 워크트리 안이면 그
  워크트리 폴더 이름, 저장소 밖이면 그 폴더 이름, 홈이거나 폴더가 없으면 `home`. 서버 프로세스의 cwd는 쓰지
  않는다(예전 기본 이름이 `server`였던 원인).
- 겹치면 `base`, `base-2`, `base-3` … 99를 넘기면 `base-<4hex>`. 겹침은 tmux 정확 매칭(`=이름`)으로 본다.
- 규칙 밖 글자는 `-`로 바꾸고 첫 글자는 영숫자·`_`, 40자. 남는 게 없으면 `session`.

제외: `bin/fsh`의 `dev`(사용자 계약), 관리 프로필 세션 `profile-<hex>`(이름이 열쇠).
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Callable, Optional

import git_safe
import tmux_runner

_BAD = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_BASE = 40
_MAX_SUFFIX = 99


def slug(raw: str) -> str:
    """규칙 안의 기준 이름. 비면 빈 문자열."""
    text = _BAD.sub("-", raw or "").strip("-")[:_MAX_BASE].strip("-")
    while text.startswith("-"):
        text = text[1:]
    return text


def _git_roots(cwd: str) -> Optional[tuple[str, str]]:
    try:
        result = subprocess.run(
            git_safe.argv("rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir", cwd=cwd),
            capture_output=True, timeout=2, check=False, env=git_safe.env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    return (lines[0], lines[1]) if len(lines) == 2 else None


def base_for(cwd: Optional[str]) -> str:
    """시작 폴더로 정한 기준 이름(번호 붙이기 전)."""
    if not cwd or not os.path.isdir(cwd):
        return "home"
    path = Path(cwd).resolve()
    try:
        if path == Path.home().resolve():
            return "home"
    except (OSError, RuntimeError):
        pass
    roots = _git_roots(str(path))
    if roots:
        top, common = Path(roots[0]), Path(roots[1])
        # 메인 작업 트리의 common-dir는 `<저장소>/.git`이다. 링크드 워크트리는 같은 common-dir를 쓰지만 top이 다르다.
        main = common.parent if common.name == ".git" else top
        name = main.name if top == main else top.name
    else:
        name = path.name
    return slug(name) or "session"


# ── ID와 별칭(2026-11 설계, docs/local/plans/session-alias-id-design.md) ─────────────────────────────────────
# 새 세션의 tmux 이름은 사람에게 보이지 않는 랜덤 ID(`s-` + 16진 8자리)다. 사람이 보는 이름(별칭)은 세션 기록
# (session_catalog `sessions.title`)에 있고, 기준 이름(위 base_for — 저장소 이름)이나 사용자가 친 이름은 **별칭 요청**으로
# 이 프로세스 메모리에 잠깐 들고 있다가 기록이 그 세션을 처음 볼 때(inventory.reconcile) 가져가 겹치지 않는 별칭으로 만든다.
# 요청이 사라져도(서버 재시작) 기록은 시작 폴더 기준 이름으로 별칭을 짓는다 — 별칭이 ID로 남지 않는다.
ID_RE = re.compile(r"s-[0-9a-f]{8}")
_REQUEST_TTL = 600.0
_requests: dict[str, tuple[str, float]] = {}


def new_id() -> str:
    return "s-" + secrets.token_hex(4)


def is_id(name: str) -> bool:
    return isinstance(name, str) and ID_RE.fullmatch(name) is not None


def request_alias(session_id: str, alias: Optional[str]) -> None:
    """그 ID의 세션이 기록에 처음 올라갈 때 쓸 별칭 기준. 빈 값이면 아무것도 하지 않는다."""
    import time
    text = " ".join(str(alias or "").split())[:128]
    if not session_id or not text:
        return
    now = time.monotonic()
    for key in [k for k, (_, at) in _requests.items() if now - at > _REQUEST_TTL]:
        del _requests[key]
    _requests[session_id] = (text, now)


def take_alias_request(session_id: str) -> Optional[str]:
    import time
    hit = _requests.pop(session_id, None)
    if not hit or time.monotonic() - hit[1] > _REQUEST_TTL:
        return None
    return hit[0]


def allocate(base: str) -> str:
    """아직 없는 새 ID. `base`(기준 이름)는 그 세션의 별칭 요청이 된다. 같은 프로세스 안의 경합은 호출부가 잠금으로
    막는다 — 다른 프로세스와의 경합까지 막으려면 `spawn_unique`를 쓴다."""
    name = new_id()
    for _ in range(8):
        if not tmux_runner.has_session(name):
            break
        name = new_id()
    request_alias(name, base)
    return name


def spawn_unique(base: str, spawn: Callable[[str], tuple[int, bytes]], *, start: Optional[str] = None) -> tuple[int, str, bytes]:
    """`spawn(name) -> (rc, stderr)`를 새 ID로 부르다가 tmux가 「duplicate session」으로 거절하면 다른 ID로 다시 한다.
    `start`(이미 `allocate`로 고른 ID)가 있으면 그것부터. 별칭 요청(`base`)은 실제로 쓴 ID로 옮긴다.
    돌려주는 값: (rc, 쓴 ID, stderr)."""
    name = start or allocate(base)
    rc, err = 1, b""
    for _ in range(8):
        rc, err = spawn(name)
        if rc == 0 or b"duplicate session" not in (err or b""):
            if rc == 0 and name not in _requests:
                request_alias(name, base)
            return rc, name, err
        _requests.pop(name, None)
        name = new_id()
        request_alias(name, base)
    return rc, name, err


def preview_alias(base: str) -> str:
    """새 세션이 받을 별칭 미리보기(아무것도 만들지 않는다). 기록을 못 읽으면 기준 이름 그대로."""
    try:
        from session_catalog import db as catalog
        return catalog.preview_alias(base)
    except Exception:  # noqa: BLE001 — 미리보기일 뿐이다
        return base
