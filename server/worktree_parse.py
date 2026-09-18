"""워크트리 — git 출력 파싱과 `.env` 포트 치환. **순수 함수만 모았다.**

worktree.py에서 떼어낸 이유: 이 함수들은 파일도 git도 건드리지 않고 문자열만
다룬다. 같은 파일에 있으면 "이건 부작용이 없다"가 코드를 읽어야만 보이는데,
분리해두면 파일 경계가 그 사실을 말한다. 테스트도 픽스처 없이 문자열만
넣으면 된다(tests/test_worktree.py가 이미 그렇게 쓰고 있다).

**상태를 읽지 않으므로 재수출이 안전하다** — worktree.py가 `from`으로 가져가
쓰던 이름 그대로 노출해도, 모듈 전역 설정을 monkeypatch하는 테스트와
어긋날 여지가 없다(auth 패키지 계열과 갈리는 지점).
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


# --- 원격 URL 파싱 (98-rail-repos-2.1.6.md §4) ----------------------------------
#
# ⚠ `git_account_store.parse_remote_url`이 같은 일을 한다. **일부러 합치지
# 않았다** — 그쪽은 계정 바인딩(ADR-27로 미사용)용이고 `(host, owner, name)`
# 튜플에 호스트를 FQDN 그대로 담는다. 여기는 레일이 매 렌더에 쓰는 표시용이라
# 호스트를 짧은 이름으로 접고, 자격증명 제거를 수용 기준으로 못 박았다.
# 레일 렌더 경로에 미사용 인증 레이어를 끌어들이지 않는다는 §4의 C안 기각
# 사유와 같은 이유다. 한쪽 정규식을 고치면 다른 쪽도 확인할 것.

_RE_REMOTE_HTTPS = re.compile(
    r"^https?://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)/([^/]+?)(?:\.git)?/?$"
)
_RE_REMOTE_SCP = re.compile(r"^[\w.\-]+@([^:/]+):(.+?)/([^/]+?)(?:\.git)?/?$")
_RE_REMOTE_SSH = re.compile(
    r"^ssh://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+?)/([^/]+?)(?:\.git)?/?$"
)

# 짧게 접는 호스트. 여기 없으면 호스트명을 그대로 쓴다(`gitlab.self.host`).
_HOST_SHORT = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket",
    "www.bitbucket.org": "bitbucket",
    "ssh.dev.azure.com": "azure",
    "dev.azure.com": "azure",
    "codeberg.org": "codeberg",
}


def parse_remote_url(url: str | None) -> dict | None:
    """git 원격 URL → `{"host", "owner", "name"}`. 못 읽으면 None.

    **자격증명은 절대 싣지 않는다.** `https://user:token@github.com/o/r.git`
    형태가 실재하고, 그대로 응답에 실으면 토큰이 브라우저로 나간다. 정규식이
    `user:token@`를 캡처 밖에서 버리므로 결과에 남을 수 없다(테스트로 고정).

    owner는 마지막 경로 세그먼트 바로 앞까지를 합친다 — GitLab의 중첩 그룹
    (`gitlab.com/group/sub/proj`)이 실재하고, 거기서 앞 한 조각만 쓰면 다른
    그룹의 저장소가 같은 소유자로 뭉쳐 색까지 같아진다.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    for pattern in (_RE_REMOTE_HTTPS, _RE_REMOTE_SSH, _RE_REMOTE_SCP):
        m = pattern.match(url)
        if not m:
            continue
        host, owner, name = (g.strip("/") for g in m.groups())
        if not host or not owner or not name:
            return None
        host = host.lower()
        return {
            "host": _HOST_SHORT.get(host, host),
            "owner": owner,
            "name": name,
        }
    return None


_RE_GIT_CONFIG_SECTION = re.compile(r'^\[([^\]"]+?)(?:\s+"([^"]*)")?\]$')


def parse_git_config_origin(text: str) -> str | None:
    """`.git/config` 본문에서 `[remote "origin"]`의 url만 뽑는다.

    `git remote get-url origin`을 쓰지 않는 이유: 저장소마다 서브프로세스가
    하나씩 더 늘어난다. `_discover_all()`은 이미 저장소 25개에 1.3초를 쓰고
    있어서 거기에 25번을 더 얹을 수 없다(§4 수용 기준).

    origin이 없으면 None — 다른 remote로 대체하지 않는다. "origin이 아닌 것"을
    소유자라고 말하면 틀린 정보를 자신 있게 그리는 셈이다.
    """
    section: str | None = None
    subsection: str | None = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            m = _RE_GIT_CONFIG_SECTION.match(line)
            if not m:
                section = subsection = None
                continue
            section = m.group(1).strip().lower()
            subsection = m.group(2)
            continue
        if section != "remote" or subsection != "origin":
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "url":
            return value.strip() or None
    return None
