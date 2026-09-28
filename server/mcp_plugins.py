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


# ---------------------------------------------------------------- claude
#
# 2026-09-28 정정 — 예전 구현은 `~/.claude.json`의 `enabledPlugins`를 읽었는데
# Claude Code는 그 파일에 플러그인 상태를 두지 않는다. 그래서 Claude 플러그인은
# 한 번도 화면에 나온 적이 없었다. 실제 위치:
#
#   설치 목록  ~/.claude/plugins/installed_plugins.json  (스코프·projectPath·버전)
#   켬/끔      ~/.claude/settings.json                    user    — 나, 모든 프로젝트
#              <프로젝트>/.claude/settings.json           project — 저장소에 커밋, 팀 공유
#              <프로젝트>/.claude/settings.local.json     local   — 나, 이 프로젝트
#
# 우선순위는 local > project > user. project는 **읽기만 한다**(2026-09-28 확정)
# — 폰에서 누른 토글 하나가 팀원 환경까지 바꾸는 커밋으로 새면 안 된다.

CLAUDE_LEVELS = (("user", "global"), ("project", "project"), ("local", "local"))


def claude_settings_paths(worktree_path: Optional[str]) -> dict:
    out = {"user": mcp_adapters.claude_settings_dir() / "settings.json"}
    if worktree_path:
        base = Path(worktree_path) / ".claude"
        out["project"] = base / "settings.json"
        out["local"] = base / "settings.local.json"
    return out


def _same_path(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def claude_installed(worktree_path: Optional[str]) -> tuple[dict, Optional[str]]:
    """{플러그인 id: [적용되는 설치 기록]}. user 설치는 어디서나, project/local
    설치는 그 projectPath에서만 적용된다."""
    path = mcp_adapters.claude_settings_dir() / "plugins" / "installed_plugins.json"
    data, err = mcp_adapters._read_json(path)
    if err:
        return {}, f"{path}: {err}"
    out: dict[str, list[dict]] = {}
    for pid, rows in mcp_adapters._as_mapping((data or {}).get("plugins")).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            scope = row.get("scope")
            if scope == "user" or _same_path(row.get("projectPath"), worktree_path):
                out.setdefault(str(pid), []).append(row)
    return out, None


def _claude_bundle(install_path: Optional[str]) -> tuple[list[str], bool]:
    """설치 디렉터리의 skills/*/SKILL.md 이름과 MCP 번들 여부."""
    if not isinstance(install_path, str) or not install_path:
        return [], False
    try:
        root = Path(install_path).resolve()
        skills_dir = root / "skills"
        skills = sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md")) \
            if skills_dir.is_dir() else []
        has_mcp = (root / ".mcp.json").is_file()
        if not has_mcp:
            manifest = root / ".claude-plugin" / "plugin.json"
            try:
                m = json.loads(manifest.read_text(encoding="utf-8"))
                has_mcp = isinstance(m, dict) and bool(m.get("mcpServers"))
            except (OSError, UnicodeDecodeError, ValueError):
                pass
    except OSError:
        return [], False
    return skills[:200], has_mcp


def scan_claude_plugins(worktree_path: Optional[str] = None,
                        worktree_id: Optional[str] = None) -> dict:
    errors: list[dict] = []
    installed, ierr = claude_installed(worktree_path)
    if ierr:
        errors.append({"source": "installed_plugins.json", "reason": ierr})

    paths = claude_settings_paths(worktree_path)
    values: dict[str, dict] = {}
    for level, path in paths.items():
        data, err = mcp_adapters._read_json(path)
        if err:
            errors.append({"source": str(path), "reason": err})
            continue
        values[level] = {str(k): bool(v) for k, v in
                         mcp_adapters._as_mapping((data or {}).get("enabledPlugins")).items()}

    names = set(installed)
    for v in values.values():
        names.update(v)

    out: list[dict] = []
    for name in sorted(names, key=str.lower):
        rows = installed.get(name, [])
        latest = max(rows, key=lambda r: str(r.get("lastUpdated") or ""), default={})
        skills, has_mcp = _claude_bundle(latest.get("installPath"))
        explicit = {lv: values[lv][name] for lv in values if name in values[lv]}
        effective = next((explicit[lv] for lv in ("local", "project", "user") if lv in explicit), None)
        for level, scope in CLAUDE_LEVELS:
            if level not in paths:
                continue
            # project는 읽기 전용이라 값이 적혀 있을 때만 보여준다.
            if level == "project" and level not in explicit:
                continue
            state = explicit.get(level)
            entry = _plugin_entry(
                name=name, tool="claude", scope=scope, path=paths[level], state=state,
                worktree_id=None if level == "user" else worktree_id,
            )
            entry.update({
                "level": level,
                "writable": level != "project" and bool(rows),
                "installed": bool(rows),
                "install_scopes": sorted({str(r.get("scope")) for r in rows if r.get("scope")}),
                "version": str(latest.get("version") or ""),
                "skills": skills,
                "skill_count": len(skills),
                "bundles_mcp": has_mcp,
                "effective": effective,
            })
            out.append(entry)
    return {"plugins": out, "errors": errors}


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
                       worktree_id: Optional[str] = None, *, refresh: bool = False) -> dict:
    """`codex plugin list --json`이 확인한 설치 플러그인과 번들 기능.
    설치 목록은 캐시(`codex_cli.plugin_list`), 켬/끔은 매번 config.toml에서 읽는다."""
    del worktree_path, worktree_id  # Codex 플러그인은 현재 전역 스코프다.
    data, err = codex_cli.plugin_list(refresh=refresh)
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
        # 캐시된 행의 enabled는 옛 값일 수 있다 — 지금 파일에 적힌 값이 이긴다.
        table = explicit.get(plugin_id)
        if isinstance(table, dict) and "enabled" in table:
            state = table.get("enabled") is not False
        else:
            state = bool(row.get("enabled", True))
        entry = _plugin_entry(
            name=plugin_id, tool="codex", scope="global", path=config_path,
            state=state, worktree_id=None,
        )
        entry.update({
            "level": "user",
            "writable": True,
            "effective": state,
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


def scan(worktree_id: Optional[str] = None, *, refresh: bool = False) -> dict:
    import mcp_scan

    wt = mcp_scan.find_worktree(worktree_id)
    wt_path = wt.get("path") if wt else None

    plugins: list[dict] = []
    errors: list[dict] = []
    for tool in TOOLS:
        fn = SCANNERS.get(tool)
        if fn is None:
            continue
        kwargs = {"refresh": refresh} if fn is scan_codex_plugins else {}
        result = fn(wt_path, wt.get("id") if wt else None, **kwargs)
        plugins.extend(result["plugins"])
        errors.extend(result["errors"])
    return {"plugins": plugins, "errors": errors}
