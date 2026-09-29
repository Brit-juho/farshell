"""FarShell MCP 보관함. 원본 정의를 서버에서 읽고 이식 가능한 필드만 보관한다.

카탈로그 응답에는 명령 인자·URL·환경변수 값이 절대 포함되지 않는다. 클라이언트는
불변 ID만 전달하며 대상 파일 해시가 바뀌면 미리보기를 다시 요구한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import mcp_adapters
import mcp_catalog
import mcp_scan
import mcp_write


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _portable(raw: dict, tool: str) -> tuple[dict | None, str | None]:
    if raw.get("oauth") or raw.get("auth"):
        return None, "OAuth 서버는 보관함으로 복제할 수 없습니다"
    command = raw.get("command")
    args = raw.get("args") or []
    url = raw.get("url") or raw.get("httpUrl") or raw.get("serverUrl")
    if bool(command) == bool(url) or not isinstance(args, list) or any(not isinstance(x, str) for x in args):
        return None, "명령 또는 URL 정의를 확인할 수 없습니다"
    if command and not isinstance(command, str):
        return None, "명령 형식이 잘못됐습니다"
    if url:
        if not isinstance(url, str):
            return None, "URL 형식이 잘못됐습니다"
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None, "인증 정보가 포함될 수 있는 URL은 보관하지 않습니다"
    if any(re.search(r"(?i)(token|secret|password|api.key)(?:=|$)", x) for x in args):
        return None, "인자에 자격증명이 포함될 수 있어 보관하지 않습니다"
    if raw.get("headers") or raw.get("http_headers"):
        return None, "헤더 인증이 있는 서버는 도구 간 변환을 지원하지 않습니다"
    env: dict[str, str] = {}
    if tool == "codex":
        if raw.get("env"):
            return None, "Codex 설정의 직접 환경변수 값은 보관하지 않습니다"
        for name in mcp_adapters._as_name_list(raw.get("env_vars")):
            env[name] = name
    else:
        style = "brace_env" if tool == "opencode" else "dollar"
        for key, value in mcp_adapters._as_mapping(raw.get("env")).items():
            ref, literal = mcp_adapters._classify_ref(value, style=style)
            if literal or not ref:
                return None, "환경변수에 직접 값이 있어 보관하지 않습니다"
            env[str(key)] = ref
    if len(env) > 32 or len(args) > 64:
        return None, "정의가 보관함 크기 제한을 넘었습니다"
    return {"command": command, "args": args, "url": url, "env": env}, None


def _read() -> dict:
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        return data.get("library") if isinstance(data.get("library"), dict) else {}


def list_items() -> dict:
    out = []
    for item_id, item in _read().items():
        if not isinstance(item, dict) or not isinstance(item.get("definition"), dict):
            continue
        d = item["definition"]
        compatible = _compatible(item)
        targets = _targets(item, compatible)
        out.append({"id": item_id, "name": item.get("name"), "source_tool": item.get("source_tool"),
                    "transport": "http" if d.get("url") else "stdio",
                    "env_names": sorted(d.get("env", {}).values()),
                    "compatible_tools": compatible, "targets": targets,
                    "audience": "common" if len(targets) > 1 else (targets[0] if targets else "unknown")})
    return {"servers": sorted(out, key=lambda x: str(x["name"]).casefold())}


def list_candidates(worktree_id: str | None = None) -> dict:
    """기존 도구 설정 중 보관할 수 있는 정의만 메타데이터로 보여준다."""
    scanned = mcp_scan.scan(worktree_id)
    wt = scanned.get("worktree") or {}
    stored = {item.get("fingerprint") for item in _read().values() if isinstance(item, dict)}
    out = []
    for entry in scanned.get("servers", []):
        tool, scope, name = entry["tool"], entry["scope"], entry["name"]
        raw, path = mcp_adapters.find_raw_defn(tool, scope, name,
                          worktree_path=wt.get("path"))
        candidate_id = _digest([tool, scope, name, entry.get("source")])[:24]
        if not raw or str(path) != str(entry.get("source")):
            out.append({"id": candidate_id, "name": name, "tool": tool, "scope": scope,
                        "eligible": False, "saved": False, "reason": "원본 설정을 특정할 수 없습니다"})
            continue
        definition, reason = _portable(raw, tool)
        fingerprint = _digest({"name": name, "definition": definition}) if definition else None
        out.append({"id": candidate_id, "name": name, "tool": tool, "scope": scope,
                    "eligible": definition is not None, "saved": fingerprint in stored,
                    "reason": reason})
    return {"candidates": out}


def import_candidates(ids: list[str], worktree_id: str | None = None) -> dict:
    if (not isinstance(ids, list) or not ids or len(ids) > 64
            or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
        return {"status": "failed", "reason": "가져올 MCP를 선택하세요"}
    candidates = {item["id"]: item for item in list_candidates(worktree_id)["candidates"]}
    results = []
    for candidate_id in ids:
        item = candidates.get(candidate_id)
        if not item or not item["eligible"]:
            results.append({"id": candidate_id, "status": "failed", "reason": "안전하게 보관할 수 없는 정의입니다"})
            continue
        result = save(item["tool"], item["scope"], item["name"], worktree_id)
        results.append({"id": candidate_id, "status": result["status"],
                        "changed": result.get("changed", False), "reason": result.get("reason")})
    return {"status": "ok" if all(r["status"] == "ok" for r in results) else "partial",
            "saved": sum(r.get("changed") is True for r in results), "results": results}


def save(tool: str, scope: str, name: str, worktree_id: str | None) -> dict:
    if tool not in mcp_scan.TOOLS or scope not in ("global", "local") or not isinstance(name, str) or not name:
        return {"status": "failed", "reason": "원본을 지정할 수 없습니다"}
    wt = mcp_scan.find_worktree(worktree_id)
    raw, _ = mcp_adapters.find_raw_defn(tool, scope, name,
                                         worktree_path=wt.get("path") if wt else None)
    if not raw:
        return {"status": "failed", "reason": "원본 MCP를 찾지 못했습니다"}
    definition, err = _portable(raw, tool)
    if err:
        return {"status": "failed", "reason": err}
    fingerprint = _digest({"name": name, "definition": definition})
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        library = data.setdefault("library", {})
        if not isinstance(library, dict):
            return {"status": "failed", "reason": "보관함 형식을 읽을 수 없습니다"}
        for item_id, item in library.items():
            if isinstance(item, dict) and item.get("fingerprint") == fingerprint:
                return {"status": "ok", "changed": False, "id": item_id}
        item_id = uuid.uuid4().hex
        item = {"name": name, "source_tool": tool,
                "definition": definition, "fingerprint": fingerprint}
        item["targets"] = _compatible(item)
        library[item_id] = item
        mcp_catalog._write_unlocked(data)
    return {"status": "ok", "changed": True, "id": item_id}


def _target(tool: str, scope: str, wt: dict | None) -> Path | None:
    if scope == "local" and not wt:
        return None
    if tool == "codex":
        return (Path(wt["path"]) / ".codex" / "config.toml") if scope == "local" else mcp_adapters.codex_paths()["global"]
    try:
        path, _, _ = mcp_write._deploy_target_path(tool, scope,
                      worktree_path=wt.get("path") if wt else None, shared=False)
        return path
    except mcp_write.WriteRefused:
        return None


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"


def _definition(item: dict, tool: str) -> tuple[dict | None, str | None]:
    d = item["definition"]
    out = {k: d[k] for k in ("command", "args", "url") if d.get(k)}
    if tool == "codex":
        if d["env"]:
            out["env_vars"] = sorted(set(d["env"].values()))
    elif tool == "agy":
        if d["env"]:
            return None, "agy는 환경변수 참조를 지원하지 않습니다"
    else:
        if d["env"]:
            out["env"] = {k: mcp_adapters.render_ref(tool, v) for k, v in d["env"].items()}
    return out, None


def _compatible(item: dict) -> list[str]:
    return [tool for tool in mcp_scan.TOOLS if _definition(item, tool)[1] is None]


def _targets(item: dict, compatible: list[str]) -> list[str]:
    raw = item.get("targets")
    if not isinstance(raw, list):
        return compatible  # 이전 보관함 항목은 이식 가능한 모든 도구에서 공용으로 본다.
    return [tool for tool in compatible if tool in raw]


def set_targets(item_id: str, targets: list[str]) -> dict:
    if not isinstance(targets, list) or not targets or len(set(targets)) != len(targets):
        return {"status": "failed", "reason": "대상 도구를 선택하세요"}
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        library = data.get("library")
        item = library.get(item_id) if isinstance(library, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("definition"), dict):
            return {"status": "failed", "reason": "보관함 항목을 찾지 못했습니다"}
        compatible = _compatible(item)
        if any(tool not in compatible for tool in targets):
            return {"status": "failed", "reason": "이 MCP가 지원하지 않는 도구가 포함됐습니다"}
        ordered = [tool for tool in compatible if tool in targets]
        if _targets(item, compatible) == ordered:
            return {"status": "ok", "changed": False}
        item["targets"] = ordered
        mcp_catalog._write_unlocked(data)
    return {"status": "ok", "changed": True}


def preview(item_id: str, tool: str, scope: str, worktree_id: str | None) -> dict:
    library = _read()
    item = library.get(item_id)
    if (not isinstance(item, dict) or not isinstance(item.get("definition"), dict)
            or not isinstance(item.get("name"), str) or not isinstance(item.get("fingerprint"), str)
            or not isinstance(item["definition"].get("env"), dict)
            or tool not in mcp_scan.TOOLS or scope not in ("global", "local")):
        return {"status": "failed", "reason": "보관함 항목 또는 대상이 없습니다"}
    if tool not in _targets(item, _compatible(item)):
        return {"status": "failed", "reason": "이 MCP의 관리 대상 도구가 아닙니다"}
    wt = mcp_scan.find_worktree(worktree_id)
    target = _target(tool, scope, wt)
    if target is None:
        return {"status": "failed", "reason": "대상 범위를 지원하지 않습니다"}
    defn, err = _definition(item, tool)
    if err:
        return {"status": "failed", "reason": err}
    existing, _ = mcp_adapters.find_raw_defn(tool, scope, item["name"],
                                             worktree_path=wt.get("path") if wt else None)
    if existing is not None:
        return {"status": "failed", "reason": "같은 이름이 대상에 이미 있습니다", "conflict": True}
    if tool == "codex" and scope == "local":
        global_config, gerr = mcp_adapters._read_toml(mcp_adapters.codex_paths()["global"])
        if gerr or not mcp_adapters.codex_trusted(global_config or {}, wt["path"]):
            return {"status": "failed", "reason": "Codex가 이 프로젝트를 신뢰하지 않습니다"}
    digest = _file_digest(target)
    token = _digest([item_id, item.get("fingerprint"), tool, scope, worktree_id, digest])
    return {"status": "ok", "id": item_id, "name": item["name"], "tool": tool,
            "scope": scope, "target": str(target), "env_names": sorted(set(item["definition"]["env"].values())),
            "token": token, "apply": "다음 세션부터 반영됩니다"}


def apply(item_id: str, tool: str, scope: str, worktree_id: str | None, token: str) -> dict:
    fresh = preview(item_id, tool, scope, worktree_id)
    if fresh["status"] != "ok":
        return fresh
    if not token or token != fresh["token"]:
        return {"status": "failed", "reason": "미리보기 뒤 설정이 바뀌었습니다. 다시 확인하세요"}
    item = _read()[item_id]
    defn, err = _definition(item, tool)
    if err:
        return {"status": "failed", "reason": err}
    wt = mcp_scan.find_worktree(worktree_id)
    return mcp_write.deploy(item["name"], defn, tool=tool, scope=scope,
                            worktree_path=wt.get("path") if wt else None)
