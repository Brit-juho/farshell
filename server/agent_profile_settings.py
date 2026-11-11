"""Follow the global Claude settings from a managed profile without sharing its account.

The global files (~/.claude/settings.json and the mcpServers of ~/.claude.json) are only ever
READ. Each key is merged three ways: the global value now, the profile's value now, and a hash of
what was last applied (`.farshell-settings-base.json`, no values stored).

  * profile untouched since the last apply  -> follow the global value (a removal included)
  * profile changed the key                 -> the profile's value stays and the key becomes its own
  * both changed                            -> the profile wins

So turning a plugin, skill, hook or MCP server on or off in one profile is just a profile-owned
key; every other setting, plugin configuration and MCP definition (with its keys) keeps following
the global one. Account, endpoint and credential keys are never touched in either direction.
MCP servers that log in through OAuth keep that login per profile: it lives in the keychain item
of the profile's own config directory, and copying a refresh token would put two holders on one
single-use chain.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import mcp_adapters
from i18n import tr

BASE = ".farshell-settings-base.json"
MAX_BYTES = 4 * 1024 * 1024
OWN = "own"  # base marker: this key belongs to the profile for good

# Keys that identify, authenticate or route one account (clauth's criterion): never copied.
PER_PROFILE_TOP = {"apiKeyHelper", "proxyAuthHelper", "awsCredentialExport", "awsAuthRefresh", "gcpAuthRefresh",
                   "otelHeadersHelper", "forceLoginMethod", "forceLoginGatewayUrl", "forceLoginOrgUUID", "model"}
NESTED = ("env", "enabledPlugins", "skillOverrides", "permissions", "extraKnownMarketplaces")
_SECRET = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|HEADERS|AUTH", re.I)
_PRIVATE_PREFIX = ("ANTHROPIC_", "AWS_", "GOOGLE_", "GCP_", "VERTEX_", "OPENAI_", "CLAUDE_CODE_OAUTH", "CLAUDE_CODE_USE_")
_MISSING = object()


STATUSLINE = "statusLine"
WRAPPER = Path(__file__).resolve().parent.parent / "bin" / "fsh-statusline"
_WRAPPED = re.compile(r"(?:'[^']*fsh-statusline'|[^\s']*fsh-statusline) --profile [a-f0-9]{32}(?: --dir (?:'[^']*'|\S+))? --(?: (?P<rest>.*))?", re.S)


def wrap_command(profile_id, command):
    """The statusLine command that records this profile's rate limits, then runs `command` unchanged."""
    from usage import statusline
    head = f"{shlex.quote(str(WRAPPER))} --profile {profile_id} --dir {shlex.quote(str(statusline.directory()))} --"
    return f"{head} {shlex.quote(command)}" if command else head


def _wrapped(value):
    """(True, original command or None) when `value` is a statusLine that FarShell's wrapper put there."""
    command = value.get("command") if isinstance(value, dict) else None
    match = _WRAPPED.fullmatch(command) if isinstance(command, str) else None
    if not match:
        return False, None
    try:
        parts = shlex.split(match.group("rest") or "")
    except ValueError:
        return True, None
    return True, parts[0] if len(parts) == 1 else None


def _statusline_want(profile_id, glob):
    """The profile's value for the global statusLine `glob`: the same object with its command wrapped.

    No global statusLine -> the wrapper alone (it collects usage and prints nothing). A global value that
    is not a plain command is left as it is, and when the wrapper file is missing nothing is wrapped.
    """
    if not WRAPPER.is_file():
        return glob
    if glob is _MISSING:
        return {"type": "command", "command": wrap_command(profile_id, None)}
    if not isinstance(glob, dict) or glob.get("type") != "command" or not isinstance(glob.get("command"), str):
        return glob
    return {**glob, "command": wrap_command(profile_id, glob["command"])}


def _digest(value):
    return profiles.digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def _private_env(name, value):
    return (not isinstance(name, str) or name.startswith(_PRIVATE_PREFIX) or bool(_SECRET.search(name))
            or (isinstance(value, str) and value.startswith("sk-")))


def _strip_owned(groups):
    """Hook groups without FarShell's own status hooks, which are installed per profile."""
    import claude_hooks
    out = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        kept = [h for h in group.get("hooks") or []
                if isinstance(h, dict) and not claude_hooks._is_ours(h.get("command", "")) and "runtime-" not in str(h.get("command", ""))]
        if kept:
            out.append({**group, "hooks": kept})
    return out


def _read_json(path):
    value = json.loads(path.read_bytes()) if path.exists() else {}
    if not isinstance(value, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    return value


def _global_settings():
    path = fsguard.global_claude_dir() / "settings.json"
    if path.is_symlink() or (path.exists() and path.stat().st_size > MAX_BYTES):
        raise profiles.Refused(tr("전역 설정을 안전하게 읽지 못했습니다"))
    return _read_json(path)


def _global_mcp():
    path = mcp_adapters._home() / ".claude.json"
    if path.is_symlink() or (path.exists() and path.stat().st_size > 64 * MAX_BYTES):
        raise profiles.Refused(tr("전역 설정을 안전하게 읽지 못했습니다"))
    servers = _read_json(path).get("mcpServers", {})
    if not isinstance(servers, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    return servers


def _base(root):
    path = fsguard.profile_file(root, BASE)
    if not path.exists():
        return {"version": 1, "settings": {}, "mcp": {}}
    value = json.loads(fsguard.read_profile_bytes(root, BASE, 1024 * 1024))
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("settings"), dict) or not isinstance(value.get("mcp"), dict):
        raise profiles.Refused(tr("설정 동기화 기록의 형식이 올바르지 않습니다"))
    return value


def _decide(path, glob, mine, recorded, take_global):
    """-> (action, new value or _MISSING). Actions: same, follow, remove, keep, own."""
    if (glob is _MISSING) == (mine is _MISSING) and (glob is _MISSING or _digest(glob) == _digest(mine)):
        return "same", mine
    if path in take_global:
        return ("remove", _MISSING) if glob is _MISSING else ("follow", glob)
    mine_hash = "absent" if mine is _MISSING else _digest(mine)
    if recorded == OWN:
        return "keep", mine
    if recorded is None:
        # Never applied before: a value the profile already has is treated as its own.
        return ("follow", glob) if mine is _MISSING else ("own", mine)
    if mine_hash == recorded:
        return ("remove", _MISSING) if glob is _MISSING else ("follow", glob)
    return "keep", mine


def _merge_map(prefix, glob, mine, recorded, take_global, report, skip=lambda k, v: False):
    """Merge one flat mapping key by key, returning the profile's new mapping."""
    result = dict(mine)
    for key in sorted(set(glob) | set(mine)):
        path = f"{prefix}.{key}"
        g = glob.get(key, _MISSING)
        if skip(key, glob.get(key) if g is not _MISSING else mine.get(key)):
            continue
        p = mine.get(key, _MISSING)
        rec = recorded.get(path)
        action, value = _decide(path, g, p, rec, take_global)
        if action in ("follow",):
            result[key] = value
            recorded[path] = _digest(g)
        elif action == "remove":
            result.pop(key, None)
            recorded.pop(path, None)
        elif action == "same":
            recorded[path] = "absent" if g is _MISSING else _digest(g)
        elif action == "own":
            recorded[path] = OWN
        if action in ("follow", "remove", "own", "keep"):
            report[action].append(path)
    return result


def _active(profile_id):
    from agent_profile_runtime import require_inactive
    try:
        require_inactive(profile_id)
        return False
    except profiles.Refused:
        return True


def sync(profile_id, *, dry_run=False, take_global=(), include_mcp=None, statusline_only=False):
    """Bring the profile's settings.json (and MCP server definitions) in line with the global ones.

    The report names key paths only, never values. MCP definitions are written to the
    profile's .claude.json only while no session of the profile is running.
    `statusline_only` touches nothing but the usage-collecting statusLine wrapper (no other settings key,
    no hooks, no MCP definitions, `.claude.json` is not even read) — what the automatic call at session
    creation uses.
    """
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    if profile["tool"] != "claude":
        raise profiles.Refused(tr("설정 동기화는 Claude 프로필에서만 쓸 수 있습니다"))
    take = set(take_global)
    root = profiles.root(profile["id"])
    report = {k: [] for k in ("follow", "remove", "keep", "own")} | {"skipped": [], "mcp_deferred": False}
    with config_io.locked(root / ".profile-operation"):
        base = _base(root)
        glob = _global_settings()
        spath = fsguard.profile_file(root, "settings.json")
        mine = json.loads(fsguard.read_profile_bytes(root, "settings.json")) if spath.exists() else {}
        if not isinstance(mine, dict):
            raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
        recorded, updated = base["settings"], dict(mine)
        for key in sorted(set(glob) | set(mine) | {STATUSLINE}):   # the wrapper is placed even with no global statusLine
            if key in PER_PROFILE_TOP or key == "hooks":
                continue
            if statusline_only and key != STATUSLINE:
                continue
            g, p = glob.get(key, _MISSING), mine.get(key, _MISSING)
            if key in NESTED and isinstance(g if g is not _MISSING else {}, dict) and isinstance(p if p is not _MISSING else {}, dict):
                skip = (lambda k, v: _private_env(k, v)) if key == "env" else (lambda k, v: False)
                merged = _merge_map(key, {} if g is _MISSING else g, {} if p is _MISSING else p, recorded, take, report, skip)
                if merged or key in mine:
                    updated[key] = merged
                if not merged:
                    updated.pop(key, None)
                continue
            if key == STATUSLINE:
                # Global G -> profile wrap(G). A value that is G itself or an earlier wrap (any wrapper
                # path, any earlier G) is the profile following, not the profile's own choice, unless the
                # key is already marked as its own.
                raw = g
                g = _statusline_want(profile["id"], raw)
                following = (p is not _MISSING and recorded.get(key) != OWN
                             and (_wrapped(p)[0] or (raw is not _MISSING and _digest(p) == _digest(raw))))
                if following and g is _MISSING:
                    action, value = "keep", p
                elif following:
                    action, value = ("same", p) if _digest(p) == _digest(g) else ("follow", g)
                else:
                    action, value = _decide(key, g, p, recorded.get(key), take)
            else:
                action, value = _decide(key, g, p, recorded.get(key), take)
            if action == "follow":
                updated[key] = value
                recorded[key] = _digest(g)
            elif action == "remove":
                updated.pop(key, None)
                recorded.pop(key, None)
            elif action == "own":
                recorded[key] = OWN
            elif action == "same":
                recorded[key] = "absent" if g is _MISSING else _digest(g)
            if action != "same":
                report[action].append(key)
        # Hooks: event by event, leaving FarShell's own status hooks to its installer.
        vault = {}
        vpath = fsguard.profile_file(root, ".farshell-hooks-disabled.json")
        if vpath.exists() and not statusline_only:
            vault = json.loads(fsguard.read_profile_bytes(root, vpath.name))
        g_hooks = glob.get("hooks", {}) if isinstance(glob.get("hooks", {}), dict) else {}
        p_hooks = mine.get("hooks", {}) if isinstance(mine.get("hooks", {}), dict) else {}
        new_hooks = dict(p_hooks)
        for event in (() if statusline_only else sorted(set(g_hooks) | set(p_hooks))):
            path = f"hooks.{event}"
            if f"settings.json:{event}" in vault:
                report["skipped"].append(path)  # some of its hooks are switched off here: edit it in the manager
                continue
            g = _strip_owned(g_hooks.get(event, [])) or _MISSING
            p = _strip_owned(p_hooks.get(event, [])) or _MISSING
            action, value = _decide(path, g, p, recorded.get(path), take)
            ours = [grp for grp in p_hooks.get(event, []) if isinstance(grp, dict) and not _strip_owned([grp])]
            if action == "follow":
                new_hooks[event] = list(value) + ours
                recorded[path] = _digest(g)
            elif action == "remove":
                if ours:
                    new_hooks[event] = ours
                else:
                    new_hooks.pop(event, None)
                recorded.pop(path, None)
            elif action == "own":
                recorded[path] = OWN
            elif action == "same":
                recorded[path] = "absent" if g is _MISSING else _digest(g)
            if action != "same":
                report[action].append(path)
        if new_hooks != p_hooks or "hooks" in mine:
            updated["hooks"] = new_hooks
        if not dry_run:
            if updated != mine:
                import claude_hooks
                # Session-creation path: refresh the paths of hooks already there, never (re)add one.
                refresh = claude_hooks.refresh_installed if statusline_only else claude_hooks.apply_install
                updated, _ = refresh(updated)
                from agent_profile_migration import _write_bytes
                _write_bytes(spath, json.dumps(updated, ensure_ascii=False, indent=2).encode(), 0o600)

        if statusline_only:
            if not dry_run:
                from agent_profile_migration import _write_bytes
                _write_bytes(fsguard.profile_file(root, BASE), json.dumps(base).encode(), 0o600)
            return {k: (sorted(v) if isinstance(v, list) else v) for k, v in report.items()} | {"dry_run": dry_run}
        # MCP server definitions (with the keys they carry) follow the global ones too.
        wants_mcp = (not dry_run) if include_mcp is None else include_mcp
        servers = _global_mcp()
        if wants_mcp and _active(profile["id"]):
            report["mcp_deferred"] = True
            wants_mcp = False
        cpath = fsguard.profile_file(root, ".claude.json")
        native = json.loads(fsguard.read_profile_bytes(root, ".claude.json")) if cpath.exists() else {}
        if not isinstance(native, dict) or not isinstance(native.get("mcpServers", {}), dict):
            raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
        from agent_profile_resources import _disabled
        parked = set(_disabled(root))
        mcp_report = {k: [] for k in ("follow", "remove", "keep", "own")}
        mcp_recorded = dict(base["mcp"])
        merged = _merge_map("mcp", servers, native.get("mcpServers", {}), mcp_recorded, take, mcp_report,
                            lambda k, v: k in parked or not isinstance(v, dict))
        for action, names in mcp_report.items():
            report[action].extend(names)
        from agent_profile_migration import _write_bytes
        if wants_mcp and not dry_run:
            # Only a write that actually happened (or nothing to write) is recorded as applied.
            if merged != native.get("mcpServers", {}):
                native["mcpServers"] = merged
                _write_bytes(cpath, json.dumps(native, ensure_ascii=False, indent=2).encode(), 0o600)
            base["mcp"] = mcp_recorded
        if not dry_run:
            _write_bytes(fsguard.profile_file(root, BASE), json.dumps(base).encode(), 0o600)
    return {k: (sorted(v) if isinstance(v, list) else v) for k, v in report.items()} | {"dry_run": dry_run}


def following(profile_id):
    """Whether the profile has ever been synchronized (its settings are then compared live)."""
    try:
        return fsguard.profile_file(profiles.root(profile_id), BASE).exists()
    except (OSError, fsguard.FsDenied, profiles.Refused):
        return False


_PLUGIN = re.compile(r"[A-Za-z0-9_.@-]{1,200}")
_SKILL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,120}")


def set_state(profile_id, kind, name, on):
    """Turn one plugin or skill on or off for this profile only; the key then stays the profile's own.

    Everything else about it (its configuration, files, MCP keys) is still the shared one.
    """
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    if profile["tool"] != "claude" or kind not in ("plugin", "skill") or not isinstance(on, bool):
        raise profiles.Refused(tr("켜기/끄기를 바꿀 수 없는 항목입니다"))
    root = profiles.root(profile["id"])
    with config_io.locked(root / ".profile-operation"):
        spath = fsguard.profile_file(root, "settings.json")
        mine = json.loads(fsguard.read_profile_bytes(root, "settings.json")) if spath.exists() else {}
        glob = _global_settings()
        if kind == "plugin":
            known = set(mine.get("enabledPlugins", {})) | set(glob.get("enabledPlugins", {}))
            field, value = "enabledPlugins", on
            valid = isinstance(name, str) and _PLUGIN.fullmatch(name) and name in known
        else:
            field, value = "skillOverrides", "on" if on else "off"
            valid = isinstance(name, str) and _SKILL.fullmatch(name) and (root / "skills" / name).exists()
        if not valid:
            raise profiles.Refused(tr("그런 플러그인이나 스킬이 없습니다"))
        mine.setdefault(field, {})
        if not isinstance(mine[field], dict):
            raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
        mine[field][name] = value
        base = _base(root)
        base["settings"][f"{field}.{name}"] = OWN
        from agent_profile_migration import _write_bytes
        _write_bytes(spath, json.dumps(mine, ensure_ascii=False, indent=2).encode(), 0o600)
        _write_bytes(fsguard.profile_file(root, BASE), json.dumps(base).encode(), 0o600)
    return {"kind": kind, "name": name, "enabled": on}
