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
import mcp_sources
import mcp_plans
from i18n import tr


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _portable(raw: dict, tool: str) -> tuple[dict | None, str | None]:
    if tool == "opencode":
        # 보관함은 cwd, timeout, 헤더/OAuth 및 확장 필드를 표현하지 못한다.
        # 이식 중 조용히 버리면 같은 이름의 다른 MCP가 되므로 거절한다.
        if raw.get("type") == "local":
            allowed = {"type", "command", "environment", "enabled"}
        elif raw.get("type") == "remote":
            # oauth=false와 생략은 OpenCode에서 의미가 다르다. 현재 보관함은
            # 이 차이를 보존하지 못하므로 원격 정의의 이식은 보류한다.
            return None, tr("OpenCode 원격 인증 설정을 보관함에서 보존할 수 없습니다")
        else:
            return None, tr("OpenCode MCP type을 확인할 수 없습니다")
        if set(raw) - allowed or raw.get("enabled") is False:
            return None, tr("보관함으로 옮기면 OpenCode 설정 의미가 달라집니다")
        converted, err = mcp_adapters.opencode_to_common(raw)
        if err:
            return None, err
        raw = converted
    else:
        allowed = {"command", "args", "url", "httpUrl", "serverUrl", "env", "env_vars",
                   "headers", "http_headers", "type", "enabled", "disabled"}
        if set(raw) - allowed:
            return None, tr("이식할 수 없는 추가 설정 필드가 있습니다 — 원본 설정을 보존합니다")
        if raw.get("type") not in (None, "stdio", "http", "sse"):
            return None, tr("보관함이 지원하지 않는 전송 방식입니다")
    if raw.get("oauth") or raw.get("auth"):
        return None, tr("OAuth 서버는 보관함으로 복제할 수 없습니다")
    for key in ("env", "headers", "http_headers"):
        if key in raw and (not isinstance(raw[key], dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in raw[key].items())):
            return None, tr("환경변수 또는 헤더 정의 형식이 잘못됐습니다")
    if "env_vars" in raw and (tool != "codex" or not isinstance(raw["env_vars"], list)
                              or any(not isinstance(v, str) for v in raw["env_vars"])):
        return None, tr("이 도구의 환경변수 이름 목록을 보존할 수 없습니다")
    for key in ("enabled", "disabled"):
        if key in raw and not isinstance(raw[key], bool):
            return None, tr("활성화 상태 형식이 잘못됐습니다")
    command = raw.get("command")
    args = raw.get("args") or []
    url = raw.get("url") or raw.get("httpUrl") or raw.get("serverUrl")
    if bool(command) == bool(url) or not isinstance(args, list) or any(not isinstance(x, str) for x in args):
        return None, tr("명령 또는 URL 정의를 확인할 수 없습니다")
    kind = raw.get("type")
    if kind in ("stdio", "http", "sse") and ((kind == "stdio") != bool(command)):
        return None, tr("전송 방식과 실행 정의가 맞지 않습니다")
    if command and not isinstance(command, str):
        return None, tr("명령 형식이 잘못됐습니다")
    if url:
        if not isinstance(url, str):
            return None, tr("URL 형식이 잘못됐습니다")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None, tr("인증 정보가 포함될 수 있는 URL은 보관하지 않습니다")
    if any(re.search(r"(?i)(token|secret|password|api.key)(?:=|$)", x) for x in args):
        return None, tr("인자에 자격증명이 포함될 수 있어 보관하지 않습니다")
    if raw.get("headers") or raw.get("http_headers"):
        return None, tr("헤더 인증이 있는 서버는 도구 간 변환을 지원하지 않습니다")
    env: dict[str, str] = {}
    if tool == "codex":
        if raw.get("env"):
            return None, tr("Codex 설정의 직접 환경변수 값은 보관하지 않습니다")
        for name in mcp_adapters._as_name_list(raw.get("env_vars")):
            env[name] = name
    else:
        style = "brace_env" if tool == "opencode" else "dollar"
        for key, value in mcp_adapters._as_mapping(raw.get("env")).items():
            ref, literal = mcp_adapters._classify_ref(value, style=style)
            if literal or not ref:
                return None, tr("환경변수에 직접 값이 있어 보관하지 않습니다")
            env[str(key)] = ref
    if len(env) > 32 or len(args) > 64:
        return None, tr("정의가 보관함 크기 제한을 넘었습니다")
    definition = {"command": command, "args": args, "url": url, "env": env}
    if raw.get("type") == "sse":
        definition["transport"] = "sse"
    return definition, None


def _source_state(item: dict) -> str:
    source = item.get("source")
    if not isinstance(source, dict):
        return "snapshot"
    entry, raw, err = mcp_sources.find(source["source_id"], source.get("worktree_id"))
    if err or raw is None:
        return "missing"
    _, blocked = _eligible(entry, raw)
    if blocked:
        return "blocked"
    return "current" if mcp_adapters.definition_hash(raw) == source["raw_hash"] else "changed"


def _eligible(entry: dict, raw: dict) -> tuple[dict | None, str | None]:
    if entry.get("ownership") in ("read_only_plugin", "read_only_account", "policy_locked"):
        return None, tr("원본 제품이 소유한 MCP입니다")
    if not entry.get("enabled") or entry.get("pending_approval") or entry.get("effective_status") == "policy_blocked":
        return None, tr("꺼져 있거나 승인/신뢰가 필요한 원본입니다")
    if entry.get("effective_status") == "merged":
        return None, tr("여러 출처가 병합된 정의입니다 — 단일 원본으로 가져올 수 없습니다")
    return _portable(raw, entry["tool"])


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
                    "audience": "common" if len(targets) > 1 else (targets[0] if targets else "unknown"),
                    "source": item.get("source"), "source_state": _source_state(item)})
    return {"servers": sorted(out, key=lambda x: str(x["name"]).casefold())}


def list_candidates(worktree_id: str | None = None) -> dict:
    """기존 도구 설정 중 보관할 수 있는 정의만 메타데이터로 보여준다."""
    scanned = mcp_scan.scan(worktree_id)
    wt = scanned.get("worktree") or {}
    stored = {(item.get("fingerprint"), (item.get("source") or {}).get("source_id"))
              for item in _read().values() if isinstance(item, dict)}
    out = []
    for entry in scanned.get("servers", []):
        tool, scope, name = entry["tool"], entry["scope"], entry["name"]
        raw, error = mcp_sources.read_entry(entry, wt.get("path"))
        candidate_id = entry["source_id"]
        if error or not raw:
            out.append({"id": candidate_id, "name": name, "tool": tool, "scope": scope,
                        "eligible": False, "saved": False, "reason": tr("원본 설정을 특정할 수 없습니다")})
            continue
        definition, reason = _eligible(entry, raw)
        fingerprint = _digest({"name": name, "definition": definition}) if definition else None
        out.append({"id": candidate_id, "name": name, "tool": tool, "scope": scope,
                    "eligible": definition is not None, "saved": (fingerprint, candidate_id) in stored,
                    "reason": reason, "source": entry["source"], "source_id": candidate_id,
                    "raw_hash": entry["raw_hash"], "level": entry["level"],
                    "owner": entry["owner"], "effective_status": entry["effective_status"]})
    return {"candidates": out}


def import_preview(ids: list[str], worktree_id: str | None = None) -> dict:
    if (not isinstance(ids, list) or not ids or len(ids) > 64
            or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
        return {"status": "failed", "reason": tr("가져올 MCP를 선택하세요")}
    candidates = {item["id"]: item for item in list_candidates(worktree_id)["candidates"]}
    sources = []
    for candidate_id in ids:
        item = candidates.get(candidate_id)
        if not item or not item["eligible"]:
            return {"status": "failed", "reason": tr("안전하게 보관할 수 없는 정의입니다")}
        sources.append(item)
    return {"status": "ok", "sources": sources,
            **mcp_plans.issue("import", {"sources": sources, "ids": ids, "worktree": worktree_id})}


def import_candidates(ids: list[str], worktree_id: str | None = None, token: str = "") -> dict:
    plan = mcp_plans.take(token, "import")
    if not plan or plan["ids"] != ids or plan["worktree"] != worktree_id:
        return {"status": "failed", "reason": tr("가져오기 미리보기가 없거나 만료됐습니다")}
    # 선택 항목을 모두 확인하고 나서 보관한다. 중간 원본 변경을 부분 성공으로 숨기지 않는다.
    for source in plan["sources"]:
        entry, raw, error = mcp_sources.find(source["source_id"], worktree_id)
        if error or raw is None or mcp_adapters.definition_hash(raw) != source["raw_hash"]:
            return {"status": "failed", "reason": tr("미리보기 뒤 원본이 바뀌었습니다"), "code": "source_changed"}
    results = []
    for source in plan["sources"]:
        result = save(source["tool"], source["scope"], source["name"], worktree_id,
                      source_id=source["source_id"], expected_hash=source["raw_hash"])
        results.append({"id": source["id"], "status": result["status"],
                        "changed": result.get("changed", False), "reason": result.get("reason")})
    return {"status": "ok" if all(r["status"] == "ok" for r in results) else "partial",
            "saved": sum(r.get("changed") is True for r in results), "results": results}


def save(tool: str, scope: str, name: str, worktree_id: str | None,
         source_id: str | None = None, expected_hash: str | None = None) -> dict:
    if tool not in mcp_scan.TOOLS or scope not in ("global", "local") or not isinstance(name, str) or not name:
        return {"status": "failed", "reason": tr("원본을 지정할 수 없습니다")}
    scanned = mcp_scan.scan(worktree_id)
    matches = [e for e in scanned["servers"] if e["tool"] == tool and e["scope"] == scope
               and e["name"] == name and (source_id is None or e["source_id"] == source_id)]
    if len(matches) != 1:
        return {"status": "failed", "reason": tr("원본 출처를 하나 선택하세요")}
    entry = matches[0]
    raw, error = mcp_sources.read_entry(entry, (scanned.get("worktree") or {}).get("path"))
    if error or not raw:
        return {"status": "failed", "reason": tr("원본 MCP를 찾지 못했습니다")}
    if expected_hash is not None and mcp_adapters.definition_hash(raw) != expected_hash:
        return {"status": "failed", "reason": tr("원본 정의가 바뀌었습니다")}
    definition, err = _eligible(entry, raw)
    if err:
        return {"status": "failed", "reason": err}
    fingerprint = _digest({"name": name, "definition": definition})
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        library = data.setdefault("library", {})
        if not isinstance(library, dict):
            return {"status": "failed", "reason": tr("보관함 형식을 읽을 수 없습니다")}
        for item_id, item in library.items():
            if (isinstance(item, dict) and item.get("fingerprint") == fingerprint
                    and (item.get("source") or {}).get("source_id") == entry["source_id"]):
                return {"status": "ok", "changed": False, "id": item_id}
        item_id = uuid.uuid4().hex
        item = {"name": name, "source_tool": tool,
                "definition": definition, "fingerprint": fingerprint,
                "source": {"source_id": entry["source_id"], "tool": tool, "scope": scope,
                           "path": entry["source"], "level": entry["level"], "owner": entry["owner"],
                           "worktree_id": worktree_id, "raw_hash": mcp_adapters.definition_hash(raw)}}
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
    return mcp_adapters.file_revision(path)


def _definition(item: dict, tool: str) -> tuple[dict | None, str | None]:
    d = item["definition"]
    out = {k: d[k] for k in ("command", "args", "url") if d.get(k)}
    if tool == "codex":
        if any(k != v for k, v in d["env"].items()):
            return None, tr("Codex는 다른 이름의 환경변수 참조를 매핑할 수 없습니다")
        if d["env"]:
            out["env_vars"] = sorted(set(d["env"].values()))
    elif tool == "agy":
        if d["env"]:
            return None, tr("agy는 환경변수 참조를 지원하지 않습니다")
    else:
        if d["env"]:
            out["env"] = {k: mcp_adapters.render_ref(tool, v) for k, v in d["env"].items()}
    if tool == "opencode":
        if out.get("url"):
            return None, tr("OpenCode 원격 인증 설정을 보관함에서 보존할 수 없습니다")
        _, err = mcp_adapters.common_to_opencode(out)
        if err:
            return None, err
    if d.get("transport") == "sse":
        if tool not in ("claude", "agy"):
            return None, tr("대상 도구에서 SSE 전송을 보존할 수 없습니다")
        out["type"] = "sse"
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
        return {"status": "failed", "reason": tr("대상 도구를 선택하세요")}
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        library = data.get("library")
        item = library.get(item_id) if isinstance(library, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("definition"), dict):
            return {"status": "failed", "reason": tr("보관함 항목을 찾지 못했습니다")}
        compatible = _compatible(item)
        if any(tool not in compatible for tool in targets):
            return {"status": "failed", "reason": tr("이 MCP가 지원하지 않는 도구가 포함됐습니다")}
        ordered = [tool for tool in compatible if tool in targets]
        if _targets(item, compatible) == ordered:
            return {"status": "ok", "changed": False}
        item["targets"] = ordered
        mcp_catalog._write_unlocked(data)
    return {"status": "ok", "changed": True}


def _deployment_snapshot(item_id: str, tool: str, scope: str, worktree_id: str | None) -> dict:
    library = _read()
    item = library.get(item_id)
    if (not isinstance(item, dict) or not isinstance(item.get("definition"), dict)
            or not isinstance(item.get("name"), str) or not isinstance(item.get("fingerprint"), str)
            or not isinstance(item["definition"].get("env"), dict)
            or tool not in mcp_scan.TOOLS or scope not in ("global", "local")):
        return {"status": "failed", "reason": tr("보관함 항목 또는 대상이 없습니다")}
    if tool not in _targets(item, _compatible(item)):
        return {"status": "failed", "reason": tr("이 MCP의 관리 대상 도구가 아닙니다")}
    if tool == "agy" and scope != "global":
        return {"status": "failed", "reason": tr("agy는 전역 범위만 지원합니다")}
    if tool == "claude" and scope != "local":
        return {"status": "failed", "reason": tr("꺼진 상태로 가져오려면 Claude의 이 프로젝트 설정을 선택하세요")}
    source_state = _source_state(item)
    if source_state not in ("current", "snapshot"):
        return {"status": "failed", "reason": tr("보관한 원본이 바뀌었거나 없어졌습니다 — 원본 갱신을 먼저 검토하세요"),
                "code": "source_changed"}
    wt = mcp_scan.find_worktree(worktree_id)
    target = _target(tool, scope, wt)
    if target is None:
        return {"status": "failed", "reason": tr("대상 범위를 지원하지 않습니다")}
    _, parse_error = (mcp_adapters._read_toml if target.suffix == ".toml"
                      else mcp_adapters._read_json)(target)
    if parse_error:
        return {"status": "failed", "reason": parse_error, "code": "target_invalid"}
    defn, err = _definition(item, tool)
    if err:
        return {"status": "failed", "reason": err}
    existing, _ = mcp_adapters.find_raw_defn(tool, scope, item["name"],
                                             worktree_path=wt.get("path") if wt else None)
    if existing is not None:
        return {"status": "failed", "reason": tr("같은 이름이 대상에 이미 있습니다"), "conflict": True}
    if tool == "codex" and scope == "local":
        global_config, gerr = mcp_adapters._read_toml(mcp_adapters.codex_paths()["global"])
        if gerr or not mcp_adapters.codex_trusted(global_config or {}, wt["path"]):
            return {"status": "failed", "reason": tr("Codex가 이 프로젝트를 신뢰하지 않습니다")}
    digest = _file_digest(target)
    return {"status": "ok", "id": item_id, "name": item["name"], "tool": tool,
            "scope": scope, "target": str(target), "env_names": sorted(set(item["definition"]["env"].values())),
            "item_revision": _digest(item), "target_revision": digest, "source": item.get("source"),
            "diff": masked_definition(defn, tool), "enabled": False,
            "warnings": [tr("대상 도구의 인증은 대상 도구에서 진행하세요")] if defn.get("url") else [],
            "apply": tr("꺼진 상태로 추가합니다 — 켠 뒤 다음 시작에 반영됩니다")}


def masked_definition(defn: dict, tool: str) -> dict:
    """실행 인자와 URL 원문은 계획 응답에 싣지 않는다."""
    if tool == "opencode":
        raw, _ = mcp_adapters.common_to_opencode(defn)
        raw = raw or defn
    else:
        raw = defn
    out = {}
    for key, value in raw.items():
        if key == "command":
            out[key] = [tr("<실행 파일>"), tr("<인자>")] if isinstance(value, list) and len(value) > 1 else (
                [tr("<실행 파일>")] if isinstance(value, list) else tr("<실행 파일>"))
        elif key == "args":
            out[key] = [tr("<인자>") for _ in value]
        elif key == "url":
            out[key] = tr("<원격 URL>")
        elif key in ("env", "environment", "headers"):
            out[key] = {k: tr("<환경변수 참조>") for k in value}
        else:
            out[key] = value
    if tool == "agy":
        out["disabled"] = True
    elif tool != "claude":
        out["enabled"] = False
    else:
        out["disabledMcpServers"] = [tr("<서버 이름>")]
    return out


def preview(item_id: str, tool: str, scope: str, worktree_id: str | None) -> dict:
    snapshot = _deployment_snapshot(item_id, tool, scope, worktree_id)
    if snapshot["status"] != "ok":
        return snapshot
    return {**snapshot, **mcp_plans.issue("deploy", {"snapshot": snapshot, "id": item_id,
                                                    "tool": tool, "scope": scope, "worktree": worktree_id})}


def source_guard(source: dict | None):
    def check():
        if not source:
            return True
        entry, raw, error = mcp_sources.find(source["source_id"], source.get("worktree_id"))
        return (not error and raw is not None and entry is not None
                and _eligible(entry, raw)[1] is None
                and mcp_adapters.definition_hash(raw) == source["raw_hash"])
    return check


def apply(item_id: str, tool: str, scope: str, worktree_id: str | None, token: str) -> dict:
    plan = mcp_plans.take(token, "deploy")
    if not plan or (plan["id"], plan["tool"], plan["scope"], plan["worktree"]) != (
            item_id, tool, scope, worktree_id):
        return {"status": "failed", "reason": tr("미리보기 계획이 없거나 만료됐습니다")}
    fresh = _deployment_snapshot(item_id, tool, scope, worktree_id)
    if fresh["status"] != "ok":
        return fresh
    if plan["snapshot"] != fresh:
        return {"status": "failed", "reason": tr("미리보기 뒤 설정이 바뀌었습니다. 다시 확인하세요")}
    item = _read()[item_id]
    defn, err = _definition(item, tool)
    if err:
        return {"status": "failed", "reason": err}
    wt = mcp_scan.find_worktree(worktree_id)
    def guard():
        live = _read().get(item_id)
        return live is not None and _digest(live) == fresh["item_revision"] and source_guard(item.get("source"))()
    result = mcp_write.deploy(item["name"], defn, tool=tool, scope=scope,
                            worktree_path=wt.get("path") if wt else None,
                            expected_revision=fresh["target_revision"], initial_enabled=False,
                            source_guard=guard)
    if result["status"] == "ok":
        result.update({"configuration_state": "disabled", "runtime_state": "unknown",
                       "apply": fresh["apply"], "source_id": (item.get("source") or {}).get("source_id")})
        raw, error = target_raw(tool, scope, item["name"], worktree_id)
        if raw is not None and not error:
            key = deployment_id(item_id, tool, scope, worktree_id)
            with mcp_catalog._locked():
                data = mcp_catalog._read_unlocked()
                data.setdefault("library_deployments", {})[key] = {
                    "id": key, "item_id": item_id, "tool": tool, "scope": scope,
                    "worktree": worktree_id, "name": item["name"],
                    "target_hash": mcp_adapters.definition_hash(raw),
                    "source_hash": (item.get("source") or {}).get("raw_hash"),
                }
                mcp_catalog._write_unlocked(data)
    return result


def deployment_id(item_id: str, tool: str, scope: str, worktree_id: str | None) -> str:
    return _digest([item_id, tool, scope, worktree_id])[:24]


def target_raw(tool: str, scope: str, name: str, worktree_id: str | None) -> tuple[dict | None, str | None]:
    wt = mcp_scan.find_worktree(worktree_id)
    target = _target(tool, scope, wt)
    if target is None:
        return None, tr("대상 설정 파일이 지원 범위를 벗어났습니다")
    data, error = (mcp_adapters._read_toml if target.suffix == ".toml" else mcp_adapters._read_json)(target)
    if error or data is None:
        return None, error or tr("대상 설정 파일이 없습니다")
    if tool == "claude" and scope == "local":
        data = mcp_adapters._claude_project_entry(data, wt["path"])
    key = {"claude": "mcpServers", "codex": "mcp_servers", "agy": "mcpServers", "opencode": "mcp"}[tool]
    raw = mcp_adapters._as_mapping(data.get(key)).get(name)
    return (raw, None) if isinstance(raw, dict) else (None, tr("대상 정의가 없습니다"))


def refresh_preview(item_id: str) -> dict:
    item = _read().get(item_id)
    source = (item or {}).get("source")
    if not isinstance(source, dict):
        return {"status": "failed", "reason": tr("이 항목에는 추적할 원본이 없습니다")}
    entry, raw, error = mcp_sources.find(source["source_id"], source.get("worktree_id"))
    if error or raw is None:
        return {"status": "failed", "reason": error}
    definition, error = _eligible(entry, raw)
    if error:
        return {"status": "failed", "reason": error}
    plan = {"id": item_id, "item_revision": _digest(item), "source": source,
            "source_hash": mcp_adapters.definition_hash(raw)}
    return {"status": "ok", "name": item["name"], "source": source,
            "diff": masked_definition(_definition({"definition": definition}, entry["tool"])[0] or {}, entry["tool"]),
            **mcp_plans.issue("refresh", plan)}


def refresh_source(item_id: str, token: str) -> dict:
    plan = mcp_plans.take(token, "refresh")
    if not plan or plan["id"] != item_id:
        return {"status": "failed", "reason": tr("원본 갱신 미리보기가 없거나 만료됐습니다")}
    source = plan["source"]
    entry, raw, error = mcp_sources.find(source["source_id"], source.get("worktree_id"))
    if error or raw is None or mcp_adapters.definition_hash(raw) != plan["source_hash"]:
        return {"status": "failed", "reason": tr("미리보기 뒤 원본이 바뀌었습니다")}
    definition, error = _eligible(entry, raw)
    if error:
        return {"status": "failed", "reason": error}
    with mcp_catalog._locked():
        data = mcp_catalog._read_unlocked()
        item = data.get("library", {}).get(item_id)
        if item is None or _digest(item) != plan["item_revision"]:
            return {"status": "failed", "reason": tr("보관함 항목이 바뀌었습니다")}
        item["definition"] = definition
        item["fingerprint"] = _digest({"name": item["name"], "definition": definition})
        item["source"]["raw_hash"] = plan["source_hash"]
        item["targets"] = [t for t in item.get("targets", []) if t in _compatible(item)]
        mcp_catalog._write_unlocked(data)
    return {"status": "ok", "changed": True}
