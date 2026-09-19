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

import codex_cli
import mcp_adapters

logger = logging.getLogger(__name__)

TOOLS = ("claude", "codex")


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


def _codex_skill_names(plugin_root: Path) -> list[str]:
    """manifest가 가리키는 skills 디렉터리에서 실제 SKILL.md만 센다."""
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        data = {}

    raw = data.get("skills") if isinstance(data, dict) else None
    refs = raw if isinstance(raw, list) else [raw or "skills"]
    names: set[str] = set()
    try:
        root = plugin_root.resolve()
    except OSError:
        return []
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            continue
        try:
            skill_root = (root / ref).resolve()
            if not skill_root.is_relative_to(root) or not skill_root.is_dir():
                continue
            for skill_file in skill_root.glob("*/SKILL.md"):
                names.add(skill_file.parent.name)
        except OSError:
            continue
    return sorted(names)[:200]


def _codex_manifest_has_mcp(plugin_root: Path) -> bool:
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("mcpServers"))


def scan_codex_plugins(worktree_path: Optional[str] = None,
                       worktree_id: Optional[str] = None) -> dict:
    """`codex plugin list --json`이 확인한 설치 플러그인과 번들 기능."""
    del worktree_path, worktree_id  # Codex 플러그인은 현재 전역 스코프다.
    data, err = codex_cli.run_json(["plugin", "list", "--json"])
    source = "codex plugin list --json"
    if err:
        return {"plugins": [], "errors": [{"source": source, "reason": err}]}
    if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
        return {"plugins": [], "errors": [{
            "source": source, "reason": "응답 형식이 예상과 다릅니다",
        }]}

    config_path = mcp_adapters.codex_paths()["global"]
    config, config_err = mcp_adapters._read_toml(config_path)
    explicit = mcp_adapters._as_mapping((config or {}).get("plugins"))
    errors = ([{"source": str(config_path), "reason": config_err}] if config_err else [])
    out: list[dict] = []
    for row in data["installed"]:
        if not isinstance(row, dict) or not row.get("pluginId"):
            continue
        plugin_id = str(row["pluginId"])
        plugin_name = str(row.get("name") or plugin_id.split("@", 1)[0])
        marketplace = row.get("marketplaceName")
        source_obj = row.get("source") if isinstance(row.get("source"), dict) else {}
        source_path = source_obj.get("path")
        root = Path(source_path) if isinstance(source_path, str) and source_path else None
        skills = _codex_skill_names(root) if root else []
        entry = _plugin_entry(
            name=plugin_id, tool="codex", scope="global", path=config_path,
            state=bool(row.get("enabled", True)), worktree_id=None,
        )
        entry.update({
            "plugin": plugin_name,
            "marketplace": str(marketplace) if marketplace else entry["marketplace"],
            "version": str(row.get("version") or ""),
            "installed": bool(row.get("installed", True)),
            "explicit": plugin_id in explicit,
            "skills": skills,
            "skill_count": len(skills),
            "bundles_mcp": _codex_manifest_has_mcp(root) if root else False,
            "auth_policy": row.get("authPolicy"),
        })
        out.append(entry)
    out.sort(key=lambda p: (p["plugin"].lower(), p["name"]))
    return {"plugins": out, "errors": errors}


SCANNERS = {"claude": scan_claude_plugins, "codex": scan_codex_plugins}


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
