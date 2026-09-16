"""워크트리 — git 출력 파싱과 `.env` 포트 치환. **순수 함수만 모았다.**

worktree.py에서 떼어낸 이유: 이 함수들은 파일도 git도 건드리지 않고 문자열만
다룬다. 같은 파일에 있으면 "이건 부작용이 없다"가 코드를 읽어야만 보이는데,
분리해두면 파일 경계가 그 사실을 말한다. 테스트도 픽스처 없이 문자열만
넣으면 된다(tests/test_worktree.py가 이미 그렇게 쓰고 있다).

**상태를 읽지 않으므로 재수출이 안전하다** — worktree.py가 `from`으로 가져가
쓰던 이름 그대로 노출해도, 모듈 전역 설정을 monkeypatch하는 테스트와
어긋날 여지가 없다(auth.py 계열과 갈리는 지점).
"""

from __future__ import annotations

import re

# create_worktree가 새 워크트리의 `.env`에서 바꿔줄 포트 키. 여기 없는 키는
# **추가하지 않는다** — 없던 설정을 만들어주는 건 친절이 아니라 사고다.
_PORT_KEYS = {"PORT", "VITE_PORT", "DEV_PORT", "NEXT_PUBLIC_PORT"}

# --- porcelain 파싱 -----------------------------------------------------------


def parse_worktree_porcelain(text: str) -> list[dict]:
    """`git worktree list --porcelain` 출력을 블록 리스트로 파싱.

    빈 줄이 블록 구분자. 순서(항상 첫 블록 = 메인 워크트리)는 git이 보장한다.
    """
    blocks: list[dict] = []
    cur: dict = {}
    for raw_line in text.split("\n"):
        line = raw_line.rstrip("\r")
        if line == "":
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            if cur:
                blocks.append(cur)
            cur = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
        elif line == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = line[len("locked"):].strip()
        elif line.startswith("prunable"):
            cur["prunable"] = line[len("prunable"):].strip()
        elif line == "bare":
            cur["bare"] = True
    if cur:
        blocks.append(cur)
    return blocks


def _branch_from_block(block: dict) -> str:
    branch_ref = block.get("branch")
    if branch_ref:
        return branch_ref[len("refs/heads/"):] if branch_ref.startswith("refs/heads/") else branch_ref
    if block.get("detached"):
        return "(detached)"
    return "(unknown)"


# --- shortstat 파싱 -------------------------------------------------------------

_RE_FILES = re.compile(r"(\d+) files? changed")
_RE_ADD = re.compile(r"(\d+) insertions?\(\+\)")
_RE_DEL = re.compile(r"(\d+) deletions?\(-\)")


def parse_shortstat(text: str) -> dict:
    files = int(m.group(1)) if (m := _RE_FILES.search(text)) else 0
    add = int(m.group(1)) if (m := _RE_ADD.search(text)) else 0
    dele = int(m.group(1)) if (m := _RE_DEL.search(text)) else 0
    return {"files": files, "add": add, "del": dele}


# --- .env 포트 치환 ------------------------------------------------------------


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def substitute_env_ports(text: str, port_base: int) -> str:
    """PORT/VITE_PORT/DEV_PORT/NEXT_PUBLIC_PORT 키만 치환. 없는 키는 추가하지 않는다."""
    out_lines = []
    for line in text.split("\n"):
        m = _ENV_LINE_RE.match(line)
        if m and m.group(1) in _PORT_KEYS:
            out_lines.append(f"{m.group(1)}={port_base}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
