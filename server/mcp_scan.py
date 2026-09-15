"""97번 계획서 1단계 1/n — 워크트리 하나를 기준으로 MCP 설정을 모아 본다.

FarShell이 값어치를 더하는 지점은 "여러 프로젝트에 흩어진 공식 설정을 한
화면에서 본다"는 것뿐이다(97번 §1-1). 그래서 이 모듈은 **아무것도 저장하지
않는다** — 부를 때마다 실제 파일을 읽는다. 저장하지 않으니 "우리 기록과 실제
파일이 어긋난다"는 문제 자체가 없다.

## 켰다고 다 켜진 게 아니다 (97번 §4)

네 도구 모두 설정 파일을 **세션 시작 시 한 번만** 읽는다. 이미 떠 있는 세션은
파일이 바뀐 걸 모른다. 게다가 gemini는 외부에서 설정을 지워도 실행 중인
세션이 그 서버를 계속 호출할 수 있다(소스 확인). 이걸 토글 스위치 하나로
그리면 사용자에게 사실과 다른 상태를 보여주게 되므로, 도구별 사실을
`TOOL_FACTS`로 함께 내려보내 화면이 정직하게 말하게 한다.
"""

from __future__ import annotations

from typing import Optional

import mcp_adapters
import worktree

# 도구별로 "지금 이 순간"이 어떻게 다른지. 화면이 안내 문구를 만드는 근거이고,
# 전부 공식 문서나 소스 확인으로 뒷받침된 것만 적는다(97번 §0-4).
TOOL_FACTS = {
    "claude": {
        "hot_reload": False,
        "apply": "다음 세션부터 — 실행 중인 창은 재시작해야 반영된다",
        "off_is_immediate": None,  # 공식 문서에 없음. 모르는 건 모른다고 한다.
    },
    "codex": {
        "hot_reload": False,
        "apply": "다음 세션부터 — 실행 중인 창은 재시작해야 반영된다",
        "off_is_immediate": None,
    },
    "gemini": {
        "hot_reload": False,
        "apply": "다음 세션부터 — 실행 중인 창은 재시작해야 반영된다",
        "off_is_immediate": False,
        "off_warning": "이미 열려 있는 세션은 재시작 전까지 이 서버를 계속 호출할 수 있다",
        "session_command": "/mcp disable",  # 세션 안에서 직접 꺼야 즉시 반영된다
    },
}

TOOLS = ("claude", "codex", "gemini")


def find_worktree(worktree_id: Optional[str]) -> Optional[dict]:
    if not worktree_id:
        return None
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

    return {
        "worktree": {"id": wt_id, "path": wt_path, "label": _label(wt)} if wt else None,
        "servers": servers,
        "errors": errors,
        "facts": {t: TOOL_FACTS[t] for t in (tools or TOOLS) if t in TOOL_FACTS},
    }


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
