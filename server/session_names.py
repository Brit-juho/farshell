"""tmux 세션 이름 짓기 — 한 곳(세션 로직 점검 단계 1).

이름은 세션을 만들 때 한 번 정하고 서버가 나중에 바꾸지 않는다. 세션을 가리키는 열쇠는 이름이 아니라
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


def candidates(base: str):
    base = slug(base) or "session"
    yield base
    for n in range(2, _MAX_SUFFIX + 1):
        yield f"{base}-{n}"
    yield f"{base}-{secrets.token_hex(2)}"


def allocate(base: str) -> str:
    """아직 없는 첫 이름. 같은 프로세스 안의 경합은 호출부가 잠금으로 막는다 — 다른 프로세스(`bin/fsh`, 사용자가 직접
    띄운 tmux)와의 경합까지 막으려면 `spawn_unique`를 쓴다."""
    last = ""
    for name in candidates(base):
        last = name
        if not tmux_runner.has_session(name):
            return name
    return last


def spawn_unique(base: str, spawn: Callable[[str], tuple[int, bytes]], *, start: Optional[str] = None) -> tuple[int, str, bytes]:
    """`spawn(name) -> (rc, stderr)`를 `base`, `base-2`, … 순으로 부르다가 tmux가 「duplicate session」으로 거절하면
    다음 이름으로 넘어간다. 이름을 고르고 만드는 사이에 다른 프로세스가 같은 이름을 가져가도 안전하다.
    `start`(이미 `allocate`로 고른 이름)가 있으면 그 이름부터 시도한다. 돌려주는 값: (rc, 쓴 이름, stderr)."""
    rc, err, name = 1, b"", ""
    names = list(candidates(base))
    if start in names:
        names = names[names.index(start):]
    for name in names:
        rc, err = spawn(name)
        if rc == 0 or b"duplicate session" not in (err or b""):
            return rc, name, err
    return rc, name, err
