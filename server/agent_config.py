"""Unified, source-preserving inventory and reviewed changes for CLI customizations.

Opaque IDs bind host-local profile + project + source, not just display names.
Plans contain no browser-supplied paths. Existing native files remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from contextlib import contextmanager

import agent_config_context as contexts
import config_io
import mcp_adapters
import mcp_plans
import mcp_plugins
import mcp_scan
import mcp_skills
import mcp_sources
import mcp_write
import vt_paths
import worktree
import fsguard
from i18n import tr

MAX_ITEMS = 64
PAGE_SIZE = 32
MAX_CONTEXTS = 4096


def _path() -> Path:
    return vt_paths.state_dir() / "agent-config.json"


def _read() -> dict:
    path = _path()
    data = json.loads(path.read_text()) if path.exists() else {"version": 1, "metadata": {}, "presets": {}, "environment": {}}
    if not isinstance(data, dict) or data.get("version") != 1 or any(not isinstance(data.get(k), dict) for k in ("metadata", "presets", "environment")):
        raise ValueError(tr("관리 저장소를 읽지 못했습니다 — 덮어쓰지 않습니다"))
    return data


@contextmanager
def transaction():
    _path().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(_path().parent, 0o700)
    with config_io.locked(_path()):
        data = _read()
        yield data
        config_io.atomic_write(_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(config_io.resolve_target(_path()), 0o600)


def _id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def default_context(tool: str, project: str | None = None) -> dict:
    root = (mcp_adapters._home() / (".claude" if tool == "claude" else ".codex")) if tool in ("claude", "codex") else {
        "codex": mcp_adapters.codex_paths, "agy": mcp_adapters.agy_paths,
        "opencode": mcp_adapters.opencode_paths}[tool]()["global"].parent
    return {"tool": tool, "root": str(root.resolve()), "home": str(mcp_adapters._home().resolve()),
            "project": project, "worktree": None, "session": None}


def scope_key(ctx: dict) -> str:
    return _id(ctx["tool"], ctx["root"], ctx.get("project"))


def _scan(ctx: dict, plugins: bool) -> tuple[list[dict], list[dict]]:
    project = ctx.get("project")
    wt = ctx.get("worktree")
    with contexts.use({**ctx, "inventory_only": True}):
        scan = mcp_adapters.ADAPTERS[ctx["tool"]](project, wt)
        mcp_sources.resolve(scan["servers"], project)
        rows, errors = [], scan["errors"][:]
        for raw in scan["servers"]:
            row = {**raw, "kind": "mcp", "native_id": raw["source_id"]}
            row["editable"] = raw.get("writable", False)
            if ctx["tool"] == "claude" and not project:
                row["writable"] = False  # enabled is per-project, not a global truth
                row["enabled"] = None
            if not row.get("policy_effective", True):
                row["writable"] = False
            if ctx["tool"] == "claude" and row.get("effective_status") == "shadowed":
                row["writable"] = False
            rows.append(row)
        # Reuse the exact skill scanner while binding its project via the resolver.
        skill_scan = mcp_skills.scan(wt) if wt and ctx["tool"] in ("claude", "codex") else {"skills": [], "errors": []}
        if not wt:
            for root, level, provider in mcp_skills._roots(ctx["tool"], None):
                if not root.is_dir():
                    continue
                for child in sorted(root.iterdir())[:500]:
                    path = child / "SKILL.md"
                    if path.is_file() and not child.is_symlink():
                        skill_scan["skills"].append({"id": mcp_skills._id(ctx["tool"], path), "name": child.name,
                            "source": str(path), "tool": ctx["tool"], "level": level, "scope": "global",
                            "enabled": str(path) not in mcp_skills._codex_disabled() if ctx["tool"] == "codex" else None,
                            "writable": ctx["tool"] == "codex", "provider": provider})
        for raw in skill_scan["skills"]:
            if raw["tool"] == ctx["tool"]:
                rows.append({**raw, "kind": "skill", "native_id": raw["id"],
                             "scope": "global" if raw["level"] == "user" else "local",
                             "revision": mcp_adapters.file_revision(Path(raw["source"]))})
        # Linked skills are common with skill installers. Show them as owned by
        # their source instead of silently hiding them; never edit through a link.
        tree = mcp_scan.find_worktree(wt) if wt else None
        for root, level, provider in mcp_skills._roots(ctx["tool"], tree):
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir())[:500]:
                path = child / "SKILL.md"
                if child.is_symlink() and path.is_file():
                    rows.append({"kind": "skill", "native_id": mcp_skills._id(ctx["tool"], path), "name": child.name,
                        "source": str(path), "tool": ctx["tool"], "scope": "global" if level == "user" else "local",
                        "level": level, "provider": provider, "enabled": None, "writable": False, "linked": True})
        errors.extend(skill_scan["errors"])
        if plugins and ctx["tool"] in mcp_plugins.SCANNERS:
            found = mcp_plugins.SCANNERS[ctx["tool"]](project, wt)
            errors.extend(found["errors"])
            for raw in found["plugins"]:
                rows.append({**raw, "kind": "plugin", "native_id": raw["name"]})
            if project:
                if ctx["tool"] == "codex":
                    local = Path(project) / ".codex" / "config.toml"
                    config, err = mcp_adapters._read_toml(local)
                    global_config, gerr = mcp_adapters._read_toml(mcp_adapters.codex_paths()["global"])
                    trusted = not gerr and mcp_adapters.codex_trusted(global_config or {}, project)
                    for raw in found["plugins"]:
                        setting = ((config or {}).get("plugins") or {}).get(raw["name"], {})
                        rows.append({**raw, "kind": "plugin", "native_id": raw["name"], "scope": "local",
                                     "level": "project", "source": str(local), "enabled": setting.get("enabled"),
                                     "writable": trusted and not err, "effective": None})
                else:
                    existing = {(r["name"], r["scope"]) for r in found["plugins"]}
                    for raw in list(rows):
                        if raw["kind"] != "plugin":
                            continue
                        if raw["scope"] == "project" and raw.get("installed"):
                            raw["writable"] = True
                        if raw.get("installed") and (raw["name"], "project") not in existing:
                            rows.append({**raw, "scope": "project", "level": "project", "enabled": None,
                                         "source": str(Path(project) / ".claude" / "settings.json"), "writable": True})
                            existing.add((raw["name"], "project"))
        if ctx["tool"] == "claude":
            # 켬·끔은 SKILL.md가 아니라 settings의 skillOverrides다. 링크된 스킬도 이름으로
            # 끄므로 링크를 따라 쓰지 않는다. 프로젝트 기준이면 그 프로젝트의 최종 상태다.
            for row in rows:
                if row["kind"] != "skill":
                    continue
                key, reason = mcp_skills.claude_skill_key(Path(row["source"]))
                enabled, state_reason = mcp_skills.claude_state(key, project) if key else (None, None)
                row["enabled"] = enabled
                row["writable"] = enabled is not None
                if reason or state_reason:
                    row["state_reason"] = reason or state_reason
        for row in rows:
            if ctx.get("project_missing"):
                row["writable"] = False
            row["context"] = {k: ctx.get(k) for k in ("tool", "root", "home", "project", "worktree", "session")}
            # Claude 스킬의 켬·끔은 프로젝트마다 다르므로 전역 스킬도 프로젝트별 행으로 둔다.
            project_scoped = row.get("scope") != "global" or (ctx["tool"] == "claude" and row["kind"] in ("mcp", "skill"))
            row["id"] = _id(ctx["tool"], ctx["root"], project if project_scoped else None,
                            row["kind"], row["source"], row["name"], row.get("scope"), bool(row.get("shared")))
            row["revision"] = mcp_adapters.file_revision(Path(row["source"]))
            if row["kind"] == "skill" and row["tool"] == "codex":
                write_path = mcp_adapters.codex_paths()["global"]
            elif row["kind"] == "skill" and row["tool"] == "claude":
                write_path = mcp_skills.claude_override_target(project)
            elif row["kind"] == "mcp" and row["tool"] == "claude":
                write_path = mcp_adapters.claude_paths()["global"]
            else:
                write_path = Path(row["source"])
            row["write_source"] = str(write_path)
            row["write_revision"] = mcp_adapters.file_revision(write_path)
            row["profile"] = ctx["root"]
            row["project"] = project
    if ctx.get("managed_profile"):
        import agent_profile_resources
        agent_profile_resources.decorate(rows, ctx)
    return rows, errors


def _context_list(session: str | None, view: str) -> tuple[list[dict], dict | None, list[dict]]:
    if contexts.current().get("managed_profile"):
        import agent_profiles
        ctx = agent_profiles.context(contexts.current()["managed_profile"])
        return [ctx], ctx, []
    errors, selected = [], None
    if session:
        try:
            selected = contexts.identify(session)
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append({"source": session, "reason": str(exc) if isinstance(exc, ValueError) else tr("프로세스 정보를 읽지 못했습니다")})
    if selected:
        key = _id(selected["tool"], selected["root"])
        value = {"tool": selected["tool"], "root": selected["root"]}
        if _read().get("profiles", {}).get(key) != value:
            with transaction() as data:
                data.setdefault("profiles", {})[key] = value
    if view == "session":
        return ([selected] if selected else []), selected, errors
    result = [default_context(tool) for tool in mcp_scan.TOOLS]
    home = mcp_adapters._home().resolve()
    # Remember only roots previously verified against a live session; never
    # recurse through arbitrary home directories or inspect login credentials.
    for saved in _read().get("profiles", {}).values():
        if not isinstance(saved, dict) or saved.get("tool") not in ("claude", "codex"):
            continue
        try:
            root = contexts._safe_root(saved["root"], home)
            if root.is_dir():
                result.append({**default_context(saved["tool"]), "root": str(root)})
        except (OSError, ValueError, KeyError):
            continue
    trees = worktree.list_worktrees_or_empty()
    for tree in trees:
        for tool in ("claude", "codex", "opencode"):
            result.append({**default_context(tool, tree["path"]), "worktree": tree["id"]})
    for pane in tmux_runner_panes():
        try:
            result.append(contexts.identify(pane.session))
        except (OSError, ValueError, UnicodeError):
            continue
    if selected:
        result.append(selected)
    for ctx in list(result):
        if ctx["tool"] in ("claude", "codex"):
            result.append({**default_context(ctx["tool"]), "root": ctx["root"]})
    roots = {(ctx["tool"], ctx["root"]): ctx for ctx in result if ctx["tool"] in ("claude", "codex")}
    registered = {str(Path(tree["path"]).resolve()): tree["id"] for tree in trees}
    for (tool, root), template in roots.items():
        with contexts.use({**template, "root": root}):
            path = mcp_adapters.claude_paths()["global"] if tool == "claude" else mcp_adapters.codex_paths()["global"]
            native, error = (mcp_adapters._read_json if tool == "claude" else mcp_adapters._read_toml)(path)
        projects = (native or {}).get("projects", {}) if not error else {}
        if not isinstance(projects, dict):
            continue
        for project in projects:
            try:
                safe = fsguard.resolve_under_roots(project)
            except (fsguard.FsDenied, TypeError, ValueError):
                continue
            result.append({"tool": tool, "root": root, "home": template["home"], "project": str(safe),
                           "worktree": registered.get(str(safe)) or "config:" + _id(str(safe)), "session": None,
                           "project_missing": not safe.is_dir()})
    discovered = {_id(ctx["tool"], ctx["root"]): {"tool": ctx["tool"], "root": ctx["root"]}
                  for ctx in result if ctx["tool"] in ("claude", "codex")}
    saved_profiles = _read().get("profiles", {})
    if any(saved_profiles.get(key) != value for key, value in discovered.items()):
        with transaction() as data:
            data.setdefault("profiles", {}).update(discovered)
    unique = {}
    for ctx in result:
        unique[(ctx["tool"], ctx["root"], ctx.get("project"))] = ctx
    if len(unique) > MAX_CONTEXTS:
        raise ValueError(tr("프로필과 프로젝트가 너무 많습니다. 선택한 세션으로 범위를 좁히세요"))
    return [unique[k] for k in sorted(unique, key=lambda k: (k[0], k[1], k[2] or ""))], selected, errors


def tmux_runner_panes():
    import tmux_runner
    return tmux_runner.get_all_panes()


def inventory(session: str | None = None, view: str = "all", *, plugins: bool = False,
              offset: int = 0, inventory_revision: str | None = None) -> dict:
    if view not in ("all", "session"):
        raise ValueError(tr("잘못된 관리 범위입니다"))
    contexts_list, selected, errors = _context_list(session, view)
    if type(offset) is not int or offset < 0 or offset > MAX_CONTEXTS:
        raise ValueError(tr("잘못된 관리 범위입니다"))
    revision = _id([(ctx["tool"], ctx["root"], ctx.get("project")) for ctx in contexts_list])
    if offset and revision != inventory_revision:
        raise ValueError(tr("관리 대상이 바뀌었습니다. 새로고침하세요"))
    state = _read()
    entries = {}
    for ctx in contexts_list[offset:offset + PAGE_SIZE]:
        try:
            rows, scan_errors = _scan(ctx, plugins)
            errors.extend(scan_errors)
            for row in rows:
                metadata = state["metadata"].get(row["id"], {})
                row["metadata"] = metadata
                row["origin"] = metadata.get("origin") or ("marketplace" if row["kind"] == "plugin" and row.get("marketplace") else "unknown")
                row["update"] = state.get("updates", {}).get(row["id"], {"state": "unchecked"})
                entries[row["id"]] = row
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append({"source": ctx["root"], "reason": tr("설정을 읽지 못했습니다: {error}", error=type(exc).__name__)})
    # Persist only metadata; inventory is always reconstructed from native sources.
    return {"items": list(entries.values()), "errors": errors,
            "selected": contexts.public(selected) if selected else None,
            "presets": [{"id": k, **v} for k, v in state["presets"].items()],
            "plugins_loaded": plugins, "view": view, "total_contexts": len(contexts_list),
            "inventory_revision": revision,
            "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < len(contexts_list) else None}


def full_inventory(session=None, view="all", *, plugins=False):
    result = inventory(session, view, plugins=plugins)
    rows = {r["id"]: r for r in result["items"]}
    while result["next_offset"] is not None:
        page = inventory(session, view, plugins=plugins, offset=result["next_offset"], inventory_revision=result["inventory_revision"])
        rows.update({r["id"]: r for r in page["items"]})
        result["errors"].extend(page["errors"])
        result["next_offset"] = page["next_offset"]
    result["items"] = list(rows.values())
    return result


def locate(item_id: str, session: str | None, view: str) -> dict:
    found = full_inventory(session, view, plugins=True)
    item = next((r for r in found["items"] if r["id"] == item_id), None)
    if item is None:
        raise ValueError(tr("항목이 변경되었거나 더 이상 존재하지 않습니다"))
    return item


def metadata(item_id: str, values: dict, session: str | None, view: str) -> dict:
    locate(item_id, session, view)
    if not isinstance(values, dict) or set(values) - {"category", "description", "origin"}:
        raise ValueError(tr("잘못된 분류 정보입니다"))
    clean = {}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > (1000 if key == "description" else 80) or "\0" in value:
            raise ValueError(tr("분류 정보가 너무 길거나 올바르지 않습니다"))
        clean[key] = value.strip()
    if clean.get("origin", "unknown") not in ("personal", "git", "marketplace", "local", "unknown"):
        raise ValueError(tr("잘못된 출처입니다"))
    with transaction() as data:
        data["metadata"][item_id] = clean
    return {"status": "ok"}


def metadata_category(ids, category, session, view):
    if not isinstance(ids, list) or not 0 < len(ids) <= MAX_ITEMS or any(not isinstance(k, str) for k in ids):
        raise ValueError(tr("잘못된 분류 정보입니다"))
    if not isinstance(category, str) or len(category) > 80 or "\0" in category:
        raise ValueError(tr("분류 정보가 너무 길거나 올바르지 않습니다"))
    snapshot = full_inventory(session, view, plugins=True)
    known = {row["id"] for row in snapshot["items"]}
    if any(key not in known for key in ids):
        raise ValueError(tr("항목이 변경되었거나 더 이상 존재하지 않습니다"))
    with transaction() as data:
        for key in ids:
            data["metadata"].setdefault(key, {})["category"] = category.strip()
    return {"status": "ok"}


def save_preset(name: str, changes: list, session: str | None, view: str) -> dict:
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or not isinstance(changes, list) or not 0 < len(changes) <= MAX_ITEMS:
        raise ValueError(tr("프리셋 이름과 선택 항목을 확인하세요"))
    snapshot = full_inventory(session, view, plugins=True)
    by_id = {r["id"]: r for r in snapshot["items"]}
    cleaned = validate_changes(changes, by_id)
    key = _id(name.strip())
    with transaction() as data:
        data["presets"][key] = {"name": name.strip(), "changes": cleaned, "updated_at": time.time()}
    return {"status": "ok", "id": key}


def delete_preset(preset_id: str) -> dict:
    with transaction() as data:
        if preset_id not in data["presets"]:
            raise ValueError(tr("프리셋을 찾지 못했습니다"))
        del data["presets"][preset_id]
    return {"status": "ok"}


def validate_changes(changes, by_id) -> list:
    if not isinstance(changes, list) or not 0 < len(changes) <= MAX_ITEMS:
        raise ValueError(tr("한 번에 1개부터 64개까지 선택하세요"))
    seen, out = set(), []
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("id"), str) or type(change.get("enabled")) is not bool or change["id"] in seen:
            raise ValueError(tr("잘못된 변경 항목입니다"))
        row = by_id.get(change["id"])
        if row is None or not row.get("writable", False):
            raise ValueError(tr("변경할 수 없는 항목이 포함되어 있습니다"))
        seen.add(change["id"])
        out.append({"id": change["id"], "enabled": change["enabled"]})
    return out


def preview(changes, session: str | None, view: str, preset_id: str | None = None) -> dict:
    if preset_id:
        preset = _read()["presets"].get(preset_id)
        if not preset:
            raise ValueError(tr("프리셋을 찾지 못했습니다"))
        changes = preset["changes"]
    snapshot = full_inventory(session, view, plugins=True)
    by_id = {r["id"]: r for r in snapshot["items"]}
    cleaned = validate_changes(changes, by_id)
    rows = [{"item": by_id[c["id"]], "enabled": c["enabled"]} for c in cleaned]
    selected = snapshot.get("selected")
    return {**mcp_plans.issue("agent-config", {"rows": rows, "session": session, "view": view,
                                             "identity": (selected or {}).get("identity")}),
            "changes": [{"id": r["item"]["id"], "name": r["item"]["name"], "kind": r["item"]["kind"],
                         "source": r["item"]["write_source"], "definition_source": r["item"]["source"],
                         "scope": r["item"]["scope"], "profile": r["item"]["profile"],
                         "project": r["item"].get("project"), "enabled": r["enabled"],
                         "previous": r["item"].get("enabled")} for r in rows]}


def apply(token: str) -> dict:
    plan = mcp_plans.take(token, "agent-config")
    if not plan:
        raise ValueError(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    snapshot = full_inventory(plan["session"], plan["view"], plugins=True)
    if plan["identity"] and plan["identity"] != (snapshot.get("selected") or {}).get("identity"):
        raise ValueError(tr("선택한 세션이 바뀌었습니다. 다시 검토하세요"))
    by_id = {r["id"]: r for r in snapshot["items"]}
    # Validate the entire plan before its first write. Later per-file rechecks
    # still detect cooperating/external edits; a batch is not falsely atomic.
    revisions = {}
    for change in plan["rows"]:
        row = change["item"]
        if (row["id"] not in by_id or not by_id[row["id"]].get("writable")
                or row["revision"] != by_id[row["id"]]["revision"]
                or row["write_revision"] != by_id[row["id"]]["write_revision"]
                or row.get("managed_digest") != by_id[row["id"]].get("managed_digest")):
            raise ValueError(tr("설정 파일이 바뀌었습니다. 다시 검토하세요"))
        revisions[row["source"]] = row["revision"]
        revisions[row["write_source"]] = row["write_revision"]
    results = []
    for change in plan["rows"]:
        row, enabled = change["item"], change["enabled"]
        with contexts.use(row["context"]), config_io.reviewed_revisions(revisions):
            if (mcp_adapters.file_revision(Path(row["source"])) != revisions[row["source"]]
                    or mcp_adapters.file_revision(Path(row["write_source"])) != revisions[row["write_source"]]):
                result = {"status": "failed", "reason": tr("설정 파일이 바뀌었습니다. 다시 검토하세요")}
            else:
                try:
                    _backup(Path(row["write_source"]))
                    if row.get("managed_kind"):
                        import agent_profile_resources
                        result = agent_profile_resources.toggle(row, enabled)
                    elif row["kind"] == "mcp":
                        result = mcp_write.set_enabled(row["tool"], row["name"], enabled, scope=row["scope"],
                            worktree_id=row["context"].get("worktree"), shared=bool(row.get("shared")))
                    elif row["kind"] == "skill" and row["tool"] == "claude":
                        key, reason = mcp_skills.claude_skill_key(Path(row["source"]))
                        if not key:
                            raise ValueError(reason)
                        result = mcp_skills.set_claude_enabled(Path(row["write_source"]), key, enabled,
                                                               local=bool(row.get("project")))
                    elif row["kind"] == "skill":
                        result = mcp_skills.set_enabled(row["native_id"], enabled, row["context"].get("worktree")) if row["context"].get("worktree") else _toggle_user_skill(row, enabled)
                    elif row["scope"] == "project" or (row["tool"] == "codex" and row["scope"] == "local"):
                        result = _toggle_project_plugin(row, enabled)
                    else:
                        result = mcp_write.set_plugin_enabled(row["name"], enabled, tool=row["tool"], scope=row["scope"], worktree_id=row["context"].get("worktree"))
                except (OSError, ValueError, config_io.ConfigWriteRefused) as exc:
                    result = {"status": "failed", "changed": False, "reason": str(exc) if isinstance(exc, (ValueError, config_io.ConfigWriteRefused)) else tr("파일을 변경하지 못했습니다")}
                if result.get("status") == "ok":
                    revisions[row["source"]] = mcp_adapters.file_revision(Path(row["source"]))
                    revisions[row["write_source"]] = mcp_adapters.file_revision(Path(row["write_source"]))
        results.append({"id": row["id"], "name": row["name"], "source": row["write_source"], "scope": row["scope"], **result})
    return {"status": "ok" if all(r["status"] == "ok" for r in results) else "partial", "results": results,
            "runtime_state": "unknown", "restart_required": any(r.get("changed") is not False for r in results)}


def _toggle_user_skill(row, enabled):
    path = mcp_adapters.codex_paths()["global"]
    with config_io.locked(path):
        stamp = config_io.stamp(path)
        text = path.read_text() if path.exists() else ""
        updated = mcp_skills._set_codex_config(text, row["source"], enabled)
        parsed, error = mcp_adapters._read_toml_text(updated)
        if error or parsed is None:
            raise ValueError(tr("수정 결과가 올바른 TOML이 아닙니다"))
        config_io.ensure_unchanged(path, stamp)
        config_io.atomic_write(path, updated)
    return {"status": "ok", "changed": text != updated}


def _toggle_project_plugin(row, enabled):
    path = Path(row["source"])
    with config_io.locked(path):
        stamp = config_io.stamp(path)
        text = path.read_text() if path.exists() else ""
        if row["tool"] == "claude":
            before = json.loads(text or "{}")
            if not isinstance(before, dict) or not isinstance(before.get("enabledPlugins", {}), dict):
                raise ValueError(tr("설정 파일의 형식이 잘못됐습니다"))
            before.setdefault("enabledPlugins", {})[row["name"]] = enabled
            updated = json.dumps(before, ensure_ascii=False, indent=2) + "\n"
        else:
            before, error = mcp_adapters._read_toml_text(text)
            if error or before is None:
                raise ValueError(tr("설정 파일의 형식이 잘못됐습니다"))
            updated, found = mcp_write.toml_set_enabled(text, row["name"], enabled, table="plugins")
            if not found:
                updated = text + "\n[plugins." + json.dumps(row["name"]) + "]\nenabled = " + str(enabled).lower() + "\n"
            parsed, error = mcp_adapters._read_toml_text(updated)
            expected = json.loads(json.dumps(before, default=str))
            expected.setdefault("plugins", {}).setdefault(row["name"], {})["enabled"] = enabled
            if error or json.loads(json.dumps(parsed, default=str)) != expected:
                raise ValueError(tr("플러그인 이외의 설정이 바뀌어 중단했습니다"))
        config_io.ensure_unchanged(path, stamp)
        config_io.atomic_write(path, updated)
        if path.read_text() != updated:
            return {"status": "unknown", "changed": None}
    return {"status": "ok", "changed": text != updated}


def _backup(path: Path) -> None:
    if not path.exists():
        return
    folder = vt_paths.state_dir() / "agent-config-backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / (_id(str(path)) + "." + mcp_adapters.file_revision(path) + ".bak")
    if not target.exists():
        config_io.atomic_write(target, path.read_text())
        os.chmod(target, 0o600)


def environment(session: str) -> dict:
    ctx = contexts.identify(session)
    data = _read()["environment"]
    scopes = []
    for scope, key in environment_keys(ctx):
        scopes.append({"scope": scope, "id": key, "variables": [{"name": name, "present": True} for name in sorted(data.get(key, {}))]})
    return {"context": contexts.public(ctx), "scopes": scopes,
            "note": tr("FarShell 실행 환경입니다. 기존 셸 파일은 수정하지 않으며 재시작 후 적용됩니다")}


def environment_keys(ctx):
    return [("machine", "machine"), ("profile", _id(ctx["tool"], ctx["root"])),
            ("project", scope_key(ctx))]


def set_environment(session: str, scope: str, name: str, value: str | None, revision: str) -> dict:
    ctx = contexts.identify(session)
    if ctx["identity"] != revision or scope not in ("machine", "profile", "project"):
        raise ValueError(tr("세션 또는 적용 범위가 바뀌었습니다"))
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", name):
        raise ValueError(tr("올바른 환경변수 이름을 입력하세요"))
    # Execution/security roots and dynamic loader variables must never become a
    # hidden way to change the target of a reviewed settings operation.
    if name in {"HOME", "PATH", "SHELL", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMUX", "TMUX_PANE", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "BASH_ENV", "ENV", "ZDOTDIR"} or name.startswith(("VT_", "FSH_", "LD_", "DYLD_")):
        raise ValueError(tr("실행 경계 변수는 이 화면에서 변경할 수 없습니다"))
    if value is not None and (not isinstance(value, str) or len(value) > 8192 or "\0" in value):
        raise ValueError(tr("환경변수 값이 올바르지 않습니다"))
    key = dict(environment_keys(ctx))[scope]
    with transaction() as data:
        values = data["environment"].setdefault(key, {})
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    return {"status": "ok", "restart_required": True}


def launch_environment(ctx: dict) -> dict:
    out = dict(ctx["env"])
    state = _read()
    previous = state.get("environment_baselines", {}).get(out.get("FSH_AGENT_ENV_BASELINE", ""))
    if isinstance(previous, dict):
        for key, value in previous.get("original", {}).items():
            if value is None:
                out.pop(key, None)
            else:
                out[key] = value
    saved = state["environment"]
    for _, key in environment_keys(ctx):
        out.update(saved.get(key, {}))
    return out


def prepare_launch_environment(ctx: dict) -> dict:
    """Remember overridden values so removing a variable really removes its overlay.

    Only values being overridden are retained, not an extra copy of every process
    credential. The opaque marker is inherited by the resumed CLI.
    """
    out = dict(ctx["env"])
    with transaction() as state:
        records = state.setdefault("environment_baselines", {})
        old = records.get(out.get("FSH_AGENT_ENV_BASELINE", ""), {})
        for key, value in old.get("original", {}).items():
            if value is None:
                out.pop(key, None)
            else:
                out[key] = value
        overlay = {}
        for _, key in environment_keys(ctx):
            overlay.update(state["environment"].get(key, {}))
        out.pop("FSH_AGENT_ENV_BASELINE", None)
        if overlay:
            original = {key: out.get(key) for key in overlay}
            scope = scope_key(ctx)
            token = next((key for key, row in records.items() if row.get("scope") == scope and row.get("original") == original), None)
            if token is None and len(records) >= 512:
                raise ValueError(tr("실행 환경 기록이 많아 자동 재시작을 중단했습니다"))
            token = token or secrets.token_hex(16)
            records[token] = {"original": original, "scope": scope, "created_at": time.time()}
            out.update(overlay)
            out["FSH_AGENT_ENV_BASELINE"] = token
    return out
