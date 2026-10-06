"""사용자가 선택한 원본 → FarShell이 배포한 대상의 단방향 변경 검토.

목록 조회마다 변경을 발견한다. 파일 쓰기는 만료 계획에 대한 명시적 적용만
수행하며 대상의 직접 편집과 자격증명/필드 손실을 자동 덮어쓰지 않는다.
"""
from __future__ import annotations

import mcp_adapters as adapters
import mcp_catalog as catalog
import mcp_library as library
import mcp_plans as plans
import mcp_scan
import mcp_sources as sources
import mcp_write


def _read() -> dict:
    with catalog._locked():
        return catalog._read_unlocked()


def _state(rule: dict, item: dict | None) -> tuple[str, str | None]:
    if not rule.get("enabled"):
        return "paused", None
    if not item or not isinstance(item.get("source"), dict):
        return "missing_source", "추적할 원본이 없습니다"
    source = item["source"]
    entry, raw, error = sources.find(source["source_id"], source.get("worktree_id"))
    if error or raw is None:
        return "missing_source", error
    _, error = library._eligible(entry, raw)
    if error:
        return "source_blocked", error
    target, error = library.target_raw(rule["tool"], rule["scope"], item["name"], rule.get("worktree"))
    if error or target is None:
        return "missing_target", error
    if adapters.definition_hash(target) != rule["target_hash"]:
        return "diverged", "대상에서 직접 수정했습니다 — 기존 정의를 보존합니다"
    return ("pending" if adapters.definition_hash(raw) != rule["source_hash"] else "current"), None


def list_rules() -> dict:
    data = _read()
    items = data.get("library", {})
    rules = []
    for rule in data.get("sync_rules", {}).values():
        state, reason = _state(rule, items.get(rule["item_id"]))
        rules.append({k: rule.get(k) for k in ("id", "item_id", "name", "tool", "scope", "worktree", "enabled")}
                     | {"state": state, "reason": reason})
    deployments = [{k: d.get(k) for k in ("id", "item_id", "name", "tool", "scope", "worktree")}
                   for d in data.get("library_deployments", {}).values()]
    return {"rules": rules, "deployments": deployments}


def set_enabled(deployment: str, enabled: bool) -> dict:
    if not isinstance(enabled, bool):
        return {"status": "failed", "reason": "동기화 상태가 올바르지 않습니다"}
    data = _read()
    tracked = data.get("library_deployments", {}).get(deployment)
    if not tracked:
        return {"status": "failed", "reason": "FarShell이 배포한 대상만 동기화할 수 있습니다"}
    item = data.get("library", {}).get(tracked["item_id"])
    if not item or not isinstance(item.get("source"), dict):
        return {"status": "failed", "reason": "추적할 원본이 없습니다"}
    rule = {**tracked, "id": deployment, "enabled": enabled}
    if enabled:
        state, reason = _state(rule, item)
        if state not in ("current", "pending"):
            return {"status": "failed", "reason": reason, "state": state}
    with catalog._locked():
        current = catalog._read_unlocked()
        if current.get("library_deployments", {}).get(deployment) != tracked:
            return {"status": "failed", "reason": "동기화 대상 기록이 바뀌었습니다"}
        current.setdefault("sync_rules", {})[deployment] = rule
        catalog._write_unlocked(current)
    return {"status": "ok", "enabled": enabled}


def _snapshot(rule_id: str) -> dict:
    data = _read()
    rule = data.get("sync_rules", {}).get(rule_id)
    if not rule:
        return {"status": "failed", "reason": "동기화 규칙이 없습니다"}
    item = data.get("library", {}).get(rule["item_id"])
    state, reason = _state(rule, item)
    if state != "pending":
        return {"status": "failed", "state": state, "reason": reason or "적용할 원본 변경이 없습니다"}
    source = item["source"]
    entry, raw, error = sources.find(source["source_id"], source.get("worktree_id"))
    if error or entry is None or raw is None:
        return {"status": "failed", "reason": error or "원본이 없습니다"}
    definition, error = library._eligible(entry, raw)
    if error:
        return {"status": "failed", "reason": error}
    transient = {**item, "definition": definition}
    rendered, error = library._definition(transient, rule["tool"])
    if error:
        return {"status": "failed", "reason": error}
    wt = mcp_scan.find_worktree(rule.get("worktree"))
    target_path = library._target(rule["tool"], rule["scope"], wt)
    if target_path is None:
        return {"status": "failed", "reason": "대상 파일이 쓰기 범위를 벗어났습니다"}
    target, error = library.target_raw(rule["tool"], rule["scope"], item["name"], rule.get("worktree"))
    if error or target is None:
        return {"status": "failed", "reason": error or "대상이 없습니다"}
    diff = library.masked_definition(rendered, rule["tool"])
    if rule["tool"] in ("codex", "opencode"):
        diff["enabled"] = target.get("enabled", True)
    elif rule["tool"] == "agy":
        diff["disabled"] = target.get("disabled", False)
    else:
        diff.pop("disabledMcpServers", None)
        diff["activation"] = "기존 프로젝트별 상태 유지"
    return {"status": "ok", "rule_revision": library._digest(rule), "item_revision": library._digest(item),
            "source_hash": adapters.definition_hash(raw), "source_id": source["source_id"],
            "target_hash": rule["target_hash"], "target_revision": adapters.file_revision(target_path),
            "target": str(target_path), "diff": diff,
            "definition": definition}


def preview(rule_id: str) -> dict:
    snapshot = _snapshot(rule_id)
    if snapshot["status"] != "ok":
        return snapshot
    public = {k: v for k, v in snapshot.items() if k != "definition"}
    return {**public, "apply": "대상의 켬/끔 상태를 유지합니다 — 다음 시작에 반영됩니다",
            **plans.issue("sync", {"id": rule_id, "snapshot": snapshot})}


def apply(rule_id: str, token: str) -> dict:
    plan = plans.take(token, "sync")
    if not plan or plan["id"] != rule_id:
        return {"status": "failed", "reason": "동기화 미리보기가 없거나 만료됐습니다"}
    snapshot = _snapshot(rule_id)
    if snapshot != plan["snapshot"]:
        return {"status": "failed", "reason": "미리보기 뒤 원본이나 대상이 바뀌었습니다"}
    data = _read()
    rule = data.get("sync_rules", {}).get(rule_id)
    item = data.get("library", {}).get((rule or {}).get("item_id"))
    if rule is None or item is None:
        return {"status": "failed", "reason": "적용 직전 동기화 항목이 없어졌습니다"}
    if library._digest(rule) != snapshot["rule_revision"] or library._digest(item) != snapshot["item_revision"]:
        return {"status": "failed", "reason": "적용 직전 보관함이나 동기화 규칙이 바뀌었습니다"}
    rendered, error = library._definition({**item, "definition": snapshot["definition"]}, rule["tool"])
    target, error = library.target_raw(rule["tool"], rule["scope"], item["name"], rule.get("worktree"))
    if error:
        return {"status": "failed", "reason": error}
    # 동기화는 새로 활성화하지 않는다. 기존 대상의 상태만 이어받는다.
    if rule["tool"] in ("codex", "opencode"):
        rendered["enabled"] = target.get("enabled", True)
    elif rule["tool"] == "agy" and "disabled" in target:
        rendered["disabled"] = target["disabled"]
    wt = mcp_scan.find_worktree(rule.get("worktree"))
    def guard():
        live = _read()
        live_rule = live.get("sync_rules", {}).get(rule_id)
        live_item = live.get("library", {}).get(rule["item_id"])
        return (live_rule is not None and live_item is not None
                and library._digest(live_rule) == snapshot["rule_revision"]
                and library._digest(live_item) == snapshot["item_revision"]
                and library.source_guard({**item["source"], "raw_hash": snapshot["source_hash"]})())
    result = mcp_write.deploy(item["name"], rendered, tool=rule["tool"], scope=rule["scope"],
                              worktree_path=(wt or {}).get("path"),
                              expected_revision=snapshot["target_revision"], replace_hash=snapshot["target_hash"],
                              source_guard=guard)
    if result["status"] != "ok":
        return result
    written, error = library.target_raw(rule["tool"], rule["scope"], item["name"], rule.get("worktree"))
    if error:
        return {"status": "unknown", "reason": error}
    with catalog._locked():
        data = catalog._read_unlocked()
        live_rule = data.get("sync_rules", {}).get(rule_id)
        if not live_rule or library._digest(live_rule) != snapshot["rule_revision"]:
            return {"status": "unknown", "reason": "설정을 쓴 뒤 동기화 규칙이 바뀌었습니다"}
        live_rule.update({"source_hash": snapshot["source_hash"], "target_hash": adapters.definition_hash(written)})
        data["library_deployments"][rule_id].update({"source_hash": snapshot["source_hash"],
                                                   "target_hash": adapters.definition_hash(written)})
        live_item = data["library"][rule["item_id"]]
        if library._digest(live_item) == snapshot["item_revision"]:
            live_item["definition"] = snapshot["definition"]
            live_item["source"]["raw_hash"] = snapshot["source_hash"]
            live_item["fingerprint"] = library._digest({"name": item["name"], "definition": snapshot["definition"]})
        catalog._write_unlocked(data)
    return {**result, "runtime_state": "unknown", "apply": "다음 시작에 반영됩니다"}


def record_activation(tool: str, scope: str, name: str, worktree_id: str | None, before: dict | None) -> None:
    """FarShell의 명시적인 켬/끔만 추적 기준에 반영한다. 외부 정의 수정은 보존한다."""
    if before is None:
        return
    after, error = library.target_raw(tool, scope, name, worktree_id)
    if error or after is None:
        return
    without_state = lambda raw: {k: v for k, v in raw.items() if k not in ("enabled", "disabled")}
    if without_state(before) != without_state(after):
        return
    with catalog._locked():
        data = catalog._read_unlocked()
        changed = False
        for key, deployment in data.get("library_deployments", {}).items():
            if ((deployment["tool"], deployment["scope"], deployment["name"], deployment.get("worktree"))
                    == (tool, scope, name, worktree_id)
                    and deployment["target_hash"] == adapters.definition_hash(before)):
                deployment["target_hash"] = adapters.definition_hash(after)
                rule = data.get("sync_rules", {}).get(key)
                if rule and rule["target_hash"] == adapters.definition_hash(before):
                    rule["target_hash"] = deployment["target_hash"]
                changed = True
        if changed:
            catalog._write_unlocked(data)
