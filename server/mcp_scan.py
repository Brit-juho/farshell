"""97번 계획서 1단계 1/n — 워크트리 하나를 기준으로 MCP 설정을 모아 본다.

FarShell이 값어치를 더하는 지점은 "여러 프로젝트에 흩어진 공식 설정을 한
화면에서 본다"는 것뿐이다(97번 §1-1). 그래서 이 모듈은 **아무것도 저장하지
않는다** — 부를 때마다 실제 파일을 읽는다. 저장하지 않으니 "우리 기록과 실제
파일이 어긋난다"는 문제 자체가 없다.

## 켰다고 다 켜진 게 아니다 (97번 §4)

네 도구 모두 설정 파일을 **세션 시작 시 한 번만** 읽는다. 이미 떠 있는 세션은
파일이 바뀐 걸 모른다. 그런데 도구마다 사정이 또 달라서(agy는 반영 시점을
아직 확인하지 못했다) 토글 스위치 하나로 그리면 사용자에게 사실과 다른
상태를 보여주게 된다. 그래서 도구별 사실을 `TOOL_FACTS`로 함께 내려보내
화면이 정직하게 말하게 한다 — **모르는 항목은 False가 아니라 None**이다.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import fsguard
import mcp_adapters
import mcp_sources
import tmux_runner
import worktree
from i18n import tr

logger = logging.getLogger(__name__)

# 포커스된 칸 기준(2026-09-28) — 화면은 경로 대신 `session:<FarShell 세션 id>`를
# 워크트리 id 자리에 넘긴다. 경로는 서버가 tmux에 직접 묻는다 — 클라이언트가
# 임의 경로를 지정하는 길을 만들지 않기 위해서다(mcp_write 머리말 「경로는
# 클라이언트에서 받지 않는다」). 기존 호출부는 전부 find_worktree()를 거치므로
# 쓰기·검증·배포 경로가 손대지 않고 같은 기준을 쓴다.
SESSION_PREFIX = "session:"
_SESSION_NAME_MAX = 256
_GIT_TIMEOUT = 2.0

# 도구별로 "지금 이 순간"이 어떻게 다른지. 화면이 안내 문구를 만드는 근거이고,
# 전부 공식 문서나 소스 확인으로 뒷받침된 것만 적는다(97번 §0-4).
def tool_facts() -> dict:
    """tr()는 호출 시점의 언어로 나가야 하므로 요청마다 만든다."""
    return {
        "claude": {
            "hot_reload": False,
            "apply": tr("다음 세션부터 — 실행 중인 창은 재시작해야 반영된다"),
            "off_is_immediate": None,  # 공식 문서에 없음. 모르는 건 모른다고 한다.
        },
        "codex": {
            "hot_reload": False,
            "apply": tr("다음 세션부터 — 실행 중인 창은 재시작해야 반영된다"),
            "off_is_immediate": None,
        },
        # agy(Antigravity CLI)는 설정이 전역 파일 하나뿐이라 워크트리 개념이 없다.
        # 핫리로드 여부는 **확인하지 못했다** — 구글의 이전 CLI 조사 결과는 다른
        # 제품의 것이라 그대로 가져다 쓰지 않는다(2026-09-16 정정).
        "agy": {
            "hot_reload": None,
            "apply": tr("반영 시점이 확인되지 않았다 — 실행 중인 창은 재시작을 권한다"),
            "off_is_immediate": None,
            "scopes": ["global"],
        },
        # opencode — 전역 + 프로젝트 파일이 **병합**된다. `enabled: true/false`로
        # codex와 같은 방향이다. ⚠ 꺼둔 서버가 조용히 다시 켜지는 upstream 버그가
        # 보고돼 있어(§6 4단계) "껐다"를 확정으로 말하지 않는다 — 이 화면의
        # 값어치는 정직함이므로 그 사실을 그대로 표시한다.
        "opencode": {
            "hot_reload": False,
            "apply": tr("다음 세션부터 — 실행 중인 창은 재시작해야 반영된다"),
            "off_is_immediate": None,
            "off_warning": tr("opencode는 꺼둔 서버가 다시 켜지는 문제가 보고돼 있습니다 — 끈 뒤 실제로 꺼졌는지 확인하세요"),
        },
    }


TOOLS = ("claude", "codex", "agy", "opencode")


def _git_toplevel(path: Path) -> Optional[Path]:
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = r.stdout.strip()
    return Path(top) if r.returncode == 0 and top else None


def _git_branch(path: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = r.stdout.strip()
    return out if r.returncode == 0 and out != "HEAD" else ""


def _tmux_name_for(session: str) -> str:
    """FarShell 세션 id → tmux 세션 이름. 저장소에 없으면 받은 값을 tmux 이름으로
    본다(CLI·테스트). 어느 쪽이든 tmux에 cwd를 **읽기만** 한다."""
    try:
        from deps import session_store
        info = session_store.get(session)
    except Exception:
        info = None
    return (getattr(info, "tmux_name", None) or session)


def _session_context(session: str) -> Optional[dict]:
    """tmux 세션의 활성 칸 cwd → git 루트(아니면 cwd 그대로) → 허용 경계 검사.

    Claude가 프로젝트 설정(`projects[...]`, `.mcp.json`)을 git 루트 기준으로
    찾으므로 하위 폴더에서 연 칸도 루트로 올린다. 경계 밖이면 None — 전역만 본다.
    """
    if not session or len(session) > _SESSION_NAME_MAX:
        return None
    name = _tmux_name_for(session)
    # `=`는 정확 일치 — 없으면 tmux가 접두어로 다른 세션을 골라버린다.
    cwd = tmux_runner.run_text(
        ["display-message", "-p", "-t", f"={name}:", "#{pane_current_path}"])
    cwd = (cwd or "").strip()
    if not cwd:
        return None
    base = Path(cwd)
    root = _git_toplevel(base) or base
    try:
        safe = fsguard.resolve_under_roots(str(root))
        safe_cwd = fsguard.resolve_under_roots(str(base))
    except fsguard.FsDenied:
        logger.info("MCP 기준 경로가 허용 경계 밖이라 전역만 본다: session=%s", name)
        return None
    if not safe.is_dir():
        return None
    # 이미 아는 워크트리면 그 항목을 그대로 쓴다 — 라벨·id가 워크트리 화면과 일치한다.
    for wt in worktree.list_worktrees():
        try:
            if Path(wt.get("path") or "").resolve() == safe:
                return {**wt, "session": name, "cwd": str(safe_cwd), "id": wt["id"]}
        except OSError:
            continue
    return {
        "id": f"{SESSION_PREFIX}{session}",
        "path": str(safe),
        "repoName": safe.name,
        "branch": _git_branch(safe),
        "session": name,
        "cwd": str(safe_cwd),
    }


def find_worktree(worktree_id: Optional[str]) -> Optional[dict]:
    if not worktree_id:
        return None
    # A manager context can refer to a native CLI project that is not registered
    # as a FarShell worktree. The browser never supplies its path; it is bound
    # server-side and rechecked against the normal filesystem boundary.
    if worktree_id.startswith("config:"):
        from agent_config_context import current
        ctx = current()
        if ctx.get("worktree") != worktree_id or not ctx.get("project"):
            return None
        try:
            path = fsguard.resolve_under_roots(ctx["project"])
        except fsguard.FsDenied:
            return None
        if not path.is_dir():
            return None
        return {"id": worktree_id, "path": str(path), "cwd": str(path), "repoName": path.name}
    if worktree_id.startswith(SESSION_PREFIX):
        return _session_context(worktree_id[len(SESSION_PREFIX):])
    for wt in worktree.list_worktrees():
        if wt.get("id") == worktree_id:
            return wt
    return None


def scan(worktree_id: Optional[str] = None, tools: Optional[tuple] = None) -> dict:
    """워크트리 하나(+ 전역)를 기준으로 정의된 MCP 서버를 전부 모은다.

    `worktree_id`가 없으면 전역만 본다. claude는 켜짐/꺼짐이 프로젝트별로
    기록되므로, 워크트리 없이는 전역 서버의 상태를 단정하지 않는다
    (어댑터가 `enabled_basis: "unknown"`으로 표시한다).
    """
    wt = find_worktree(worktree_id)
    wt_path = wt.get("path") if wt else None
    wt_id = wt.get("id") if wt else None

    servers: list[dict] = []
    errors: list[dict] = []
    for tool in (tools or TOOLS):
        fn = mcp_adapters.ADAPTERS.get(tool)
        if fn is None:
            continue
        result = fn(wt_path, wt_id)
        servers.extend(result["servers"])
        errors.extend(result["errors"])

    mcp_sources.resolve(servers, wt_path)
    facts = tool_facts()
    return {
        "worktree": _public(wt),
        "servers": servers,
        "errors": errors,
        "facts": {t: facts[t] for t in (tools or TOOLS) if t in facts},
    }


def _public(wt: Optional[dict]) -> Optional[dict]:
    if not wt:
        return None
    return {"id": wt.get("id"), "path": wt.get("path"), "label": _label(wt),
            "session": wt.get("session"), "cwd": wt.get("cwd")}


def _label(wt: Optional[dict]) -> Optional[str]:
    if not wt:
        return None
    repo = wt.get("repoName") or ""
    branch = wt.get("branch") or ""
    return f"{repo}/{branch}" if repo and branch else (repo or branch or None)


def group_by_name(servers: list[dict]) -> list[dict]:
    """화면이 쓰기 좋게 이름으로 묶는다. 같은 이름이 도구·스코프마다 따로
    나타나는 건 합치지 않는다 — 그게 실제 상태다."""
    out: dict[str, dict] = {}
    for s in servers:
        g = out.setdefault(s["name"], {"name": s["name"], "entries": []})
        g["entries"].append(s)
    return [out[k] for k in sorted(out)]
