"""발견한 MCP 출처의 선택 규칙. 클라이언트 경로로 원본을 읽지 않는다."""
from __future__ import annotations

from pathlib import Path

import mcp_adapters as adapters


def read_entry(entry: dict, worktree_path: str | None) -> tuple[dict | None, str | None]:
    path = Path(entry["source"])
    reader = adapters._read_toml if path.suffix == ".toml" else (
        adapters._read_jsonc if path.suffix == ".jsonc" else adapters._read_json)
    data, error = reader(path)
    if error or data is None:
        return None, error or "원본 파일이 없습니다"
    if entry["tool"] == "claude":
        if entry["scope"] == "local" and not entry.get("shared"):
            data = adapters._claude_project_entry(data, worktree_path)
        key = "mcpServers"
    else:
        key = {"codex": "mcp_servers", "agy": "mcpServers", "opencode": "mcp"}[entry["tool"]]
    raw = adapters._as_mapping(data.get(key)).get(entry["name"])
    return (raw, None) if isinstance(raw, dict) else (None, "원본 정의가 없습니다")


def resolve(servers: list[dict], worktree_path: str | None) -> None:
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in servers:
        entry.setdefault("ownership", "writable_direct" if entry.get("writable", True)
                         else "unknown_owner")
        entry["enabled_in_source"] = entry["enabled"]
        groups.setdefault((entry["tool"], entry["name"]), []).append(entry)
    for (tool, _name), entries in groups.items():
        eligible = [e for e in entries if e.get("policy_effective", True)]
        if tool == "claude":
            priority = lambda e: 1 if e["scope"] == "global" else (2 if e.get("shared") else 3)
        elif tool == "opencode":
            priority = lambda e: e.get("source_priority", 0)
        else:
            priority = lambda e: 1 if e["scope"] == "global" else 2
        winner = max(eligible, key=priority) if eligible else None
        distinct = len({e["fingerprint"] for e in entries}) > 1
        for entry in entries:
            entry["conflicts"] = [e["source_id"] for e in entries if e is not entry] if distinct else []
            entry["effective_status"] = ("policy_blocked" if entry not in eligible else
                                         "selected" if entry is winner else "shadowed")
            entry["effective_source_id"] = winner["source_id"] if winner else None
        # OpenCode는 같은 이름의 객체 필드도 병합한다. 전체 정의 우선순위로
        # 표현하면 잘못된 선택 결과이므로 병합 참여 출처를 그대로 표시한다.
        if tool == "opencode" and len(eligible) > 1:
            for entry in eligible:
                entry["effective_status"] = "merged"
                entry["effective_sources"] = [e["source_id"] for e in sorted(eligible, key=priority)]
                entry["notes"].append("같은 이름의 정의가 필드별로 병합됩니다 — 원본별 항목을 표시합니다")


def find(source_id: str, worktree_id: str | None) -> tuple[dict | None, dict | None, str | None]:
    import mcp_scan
    scanned = mcp_scan.scan(worktree_id)
    entry = next((e for e in scanned["servers"] if e["source_id"] == source_id), None)
    if entry is None:
        return None, None, "출처를 찾지 못했습니다"
    raw, error = read_entry(entry, (scanned.get("worktree") or {}).get("path"))
    return entry, raw, error
