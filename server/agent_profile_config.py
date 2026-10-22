"""Profile-scoped hook inventory and source-preserving reviewed edits."""
from __future__ import annotations

import json
import re
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import mcp_plans
from i18n import tr


def _read(profile_id, *, hook_file=False):
    import tomllib
    profile = profiles.get(profile_id)
    root = profiles.root(profile_id)
    filename = "settings.json" if profile["tool"] == "claude" else "hooks.json" if hook_file else "config.toml"
    path = fsguard.profile_file(root, filename)
    raw = fsguard.read_profile_bytes(root, filename) if path.exists() else b"{}" if filename.endswith('.json') else b""
    data = json.loads(raw) if filename.endswith('.json') else tomllib.loads(raw.decode())
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    return profile, path, raw, data


def inventory(profile_id):
    profile, path, raw, data = _read(profile_id)
    import claude_hooks
    import codex_hooks
    installer = claude_hooks if profile["tool"] == "claude" else codex_hooks
    owned = {installer.hook_command(event) for event in installer.EVENTS}
    items = []
    documents = [(path, data)]
    if profile["tool"] == "codex":
        _, extra_path, _, extra_data = _read(profile_id, hook_file=True)
        documents.append((extra_path, extra_data))
    for source_path, document in documents:
      for event, groups in document.get("hooks", {}).items():
        if event == "state":
            continue
        if not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                continue
            for hi, hook in enumerate(group.get("hooks", [])):
                if not isinstance(hook, dict):
                    continue
                digest = profiles.digest(json.dumps([source_path.name, event, gi, hi, hook], sort_keys=True).encode())
                ours = hook.get("command") in owned
                items.append({"id": digest[:32], "event": event, "type": hook.get("type", "unknown"),
                              "owned": ours, "writable": not ours and source_path.suffix == '.json', "source": source_path.name, "group": gi, "index": hi,
                              "sha256": digest, "enabled": hook.get("disabled") is not True})
    return {"profile_id": profile_id, "revision": profile["revision"], "file_revision": profiles.digest(raw), "hooks": items,
            "note": tr("공통·프로젝트·플러그인 훅은 각 출처에서 별도로 상속될 수 있습니다")}


def preview(profile_id, revision, event=None, command=None, matcher="*", timeout=10, remove_id=None):
    profile, path, raw, data = _read(profile_id, hook_file=True)
    profiles.require_revision(profile, revision)
    if remove_id is not None:
        profiles.identifier(remove_id)
        match = next((row for row in inventory(profile_id)["hooks"] if row["id"] == remove_id), None)
        if not match or not match["writable"]:
            raise profiles.Refused(tr("이 훅은 제거할 수 없습니다"))
        plan = {"profile_id": profile_id, "revision": revision, "file_revision": profiles.digest(raw), "remove": match}
    else:
        import claude_hooks
        import codex_hooks
        supported = (claude_hooks if profile["tool"] == "claude" else codex_hooks).EVENTS
        if event not in supported or not isinstance(command, str) or not 1 <= len(command) <= 8192 or any(ord(c) < 32 for c in command):
            raise profiles.Refused(tr("훅 이벤트 또는 명령이 올바르지 않습니다"))
        if "runtime-" in command or not isinstance(matcher, str) or not 1 <= len(matcher) <= 256 or any(ord(c) < 32 for c in matcher) or type(timeout) is not int or not 1 <= timeout <= 600:
            raise profiles.Refused(tr("훅 경로·matcher·timeout을 확인하세요"))
        plan = {"profile_id": profile_id, "revision": revision, "file_revision": profiles.digest(raw),
                "event": event, "entry": {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": timeout}]}}
    return {**mcp_plans.issue("profile-hook", plan), "event": event, "removing": remove_id is not None,
            "warning": tr("훅은 이 계정의 권한으로 명령을 실행합니다")}


def apply(token):
    plan = mcp_plans.take(token, "profile-hook")
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    profile, path, raw, data = _read(plan["profile_id"], hook_file=True)
    profiles.require_revision(profile, plan["revision"])
    with config_io.locked(path):
        current_raw = fsguard.read_profile_bytes(path.parent, path.name) if path.exists() else b"{}"
        if profiles.digest(current_raw) != plan["file_revision"]:
            raise profiles.Refused(tr("훅 설정이 바뀌었습니다. 다시 검토하세요"))
        hooks = data.setdefault("hooks", {})
        if plan.get("remove"):
            row = plan["remove"]
            groups = hooks[row["event"]]
            del groups[row["group"]]["hooks"][row["index"]]
            if not groups[row["group"]]["hooks"]:
                del groups[row["group"]]
        else:
            hooks.setdefault(plan["event"], []).append(plan["entry"])
        text = json.dumps(data, ensure_ascii=False, indent=2)
        backup = profiles.directory() / "hook-backups"
        fsguard.private_profile_directory(backup)
        config_io.atomic_write(backup / (profiles.digest(raw) + ".json"), raw.decode())
        config_io.atomic_write(path, text)
    profiles.bump(profile["id"], profile["revision"])
    return {"status": "ok", "restart_required": True}


def recover(profile_id, revision):
    """Explicit recovery: re-parse native configuration, never replay a job."""
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    from agent_profile_runtime import validate_native_files, environment
    validate_native_files(profile_id)
    environment(profile_id)
    with profiles.database() as db:
        pending = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? AND state IN ('running','partial','partial-rollback')", (profile_id,))]
    for job in pending:
        if job['kind'].startswith('plugin-'):
            metadata = json.loads(job['result'])
            plugin = metadata.get('plugin')
            if not plugin:
                raise profiles.Refused(tr("중단된 플러그인 작업은 네이티브 상태를 먼저 복구해야 합니다"))
            from agent_profile_plugins import _installed
            if _installed(profile, plugin) is not None:
                _, _, _, config = _read(profile_id)
                enabled = config.get('enabledPlugins', {}).get(plugin) if profile['tool'] == 'claude' else config.get('plugins', {}).get(plugin, {}).get('enabled')
                if enabled is not False:
                    raise profiles.Refused(tr("중단된 플러그인을 먼저 끈 뒤 복구를 확인하세요"))
    with profiles.database() as db:
        db.execute("UPDATE jobs SET state='interrupted' WHERE profile_id=? AND state IN ('running','partial','partial-rollback')", (profile_id,))
    profiles.bump(profile_id, revision)
    return {"status": "ok", "note": tr("설정을 다시 읽었습니다. 중단된 작업은 자동 재실행하지 않습니다")}
