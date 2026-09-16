"""97번 4단계 — 플러그인 조회·토글.

## 왜 MCP와 같은 파일에서 다루는가 (§0-2)

세 도구 다 플러그인 안에 MCP 서버를 번들할 수 있어(`mcpServers` 필드)
**플러그인 관리와 MCP 관리가 파일 스키마 차원에서 이미 겹친다.** "이름 +
스코프 + enabled"라는 같은 메커니즘으로 둘 다 처리된다. 정의 파일 포맷이
도구마다 완전히 달라서 "이 플러그인은 Claude 전용"이 자동으로 드러난다.

## 하는 것 / 안 하는 것

**설치는 하지 않는다.** 마켓플레이스 등록이 선행돼야 하고 설치는 네트워크
행위이며, 무엇보다 **미설치 플러그인은 `enabled` 값만으론 켜지지 않는다** —
켰다고 표시해놓고 실제로는 아무 일도 안 일어나는 게 이 화면이 가장 피해야 할
상태다(§4 "정직함"). 그래서 이 모듈은 **이미 설치된 것의 on/off만** 다루고,
설치는 각 CLI의 공식 명령으로 안내한다.

`enabledPlugins`의 키는 `플러그인@마켓플레이스` 꼴이고, 값은 bool이다.
"목록에 없음"과 "false"는 다르다 — 전자는 한 번도 건드린 적 없음이라
도구의 기본값을 따르고, 후자는 사용자가 명시적으로 끈 것이다. 그 구분을
뭉개면 "끈 적 없는데 꺼져 보인다"가 된다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import mcp_adapters

logger = logging.getLogger(__name__)

TOOLS = ("claude",)   # codex 플러그인은 2026-03 신설이라 포맷 확인 후 추가한다


def _plugin_entry(*, name: str, tool: str, scope: str, path: Path,
                  state: Optional[bool], worktree_id: Optional[str]) -> dict:
    marketplace = name.split("@", 1)[1] if "@" in name else None
    return {
        "name": name,
        "plugin": name.split("@", 1)[0],
        "marketplace": marketplace,
        "tool": tool,
        "scope": scope,
        "source": str(path),
        "worktree_id": worktree_id,
        # None = 목록에 없음(도구 기본값). True/False = 명시적으로 켬/끔.
        "enabled": state,
        "explicit": state is not None,
    }


def scan_claude_plugins(worktree_path: Optional[str] = None,
                        worktree_id: Optional[str] = None) -> dict:
    """`~/.claude.json`의 `enabledPlugins` — 전역 + 프로젝트 블록."""
    path = mcp_adapters.claude_paths()["global"]
    data, err = mcp_adapters._read_json(path)
    if err:
        return {"plugins": [], "errors": [{"source": str(path), "reason": err}]}
    if data is None:
        return {"plugins": [], "errors": []}

    out: list[dict] = []
    for name, state in sorted(mcp_adapters._as_mapping(data.get("enabledPlugins")).items()):
        out.append(_plugin_entry(name=name, tool="claude", scope="global", path=path,
                                 state=bool(state), worktree_id=None))

    project = mcp_adapters._claude_project_entry(data, worktree_path)
    for name, state in sorted(mcp_adapters._as_mapping(project.get("enabledPlugins")).items()):
        out.append(_plugin_entry(name=name, tool="claude", scope="local", path=path,
                                 state=bool(state), worktree_id=worktree_id))
    return {"plugins": out, "errors": []}


SCANNERS = {"claude": scan_claude_plugins}


def scan(worktree_id: Optional[str] = None) -> dict:
    import mcp_scan

    wt = mcp_scan.find_worktree(worktree_id)
    wt_path = wt.get("path") if wt else None

    plugins: list[dict] = []
    errors: list[dict] = []
    for tool in TOOLS:
        fn = SCANNERS.get(tool)
        if fn is None:
            continue
        result = fn(wt_path, wt.get("id") if wt else None)
        plugins.extend(result["plugins"])
        errors.extend(result["errors"])
    return {"plugins": plugins, "errors": errors}
