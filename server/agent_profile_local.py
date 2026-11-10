"""Local-shell profile operations: reviewed imports, launch and session checks.

Same trust tier as `fsh otp setup` and `fsh git-account`: whoever holds this shell.
Everything goes through the same preview/apply code as the web flow, so source
preservation, hash checks and rollback records are shared. Nothing here prints a
token, key or transcript content.
"""
from __future__ import annotations

import json
import os
import sys

import agent_profile_migration as migration
import agent_profile_runtime as runtime
import agent_profiles as profiles
import config_io
import fsguard
from i18n import tr

_SKIPPED = ("unknown", "contested")


def _confirm(yes):
    if yes:
        return
    if not sys.stdin.isatty():
        raise profiles.Refused(tr("대화형 터미널이 아니면 --yes로 검토를 마쳤음을 밝혀야 합니다"))
    if input(tr("적용할까요? [y/N] ")).strip().lower() != "y":
        raise profiles.Refused(tr("취소했습니다"))


def _source(reference, tool):
    rows = [s for s in migration.source_list()["sources"] if s["tool"] == tool and reference in (s["id"], s["name"])]
    if len(rows) != 1:
        raise profiles.Refused(tr("원본을 하나로 특정하지 못했습니다. 원본 ID를 쓰세요") if rows else tr("원본을 찾지 못했습니다"))
    return rows[0]


def _locked(profile_id):
    return config_io.locked(profiles.root(profile_id) / ".profile-operation")


def import_config(profile_id, reference, *, include_private=False, skip_linked_skills=False, yes=False):
    profile = profiles.get(profile_id)
    source = _source(reference, profile["tool"])
    # skill_names=None copies every skill, the only choice that counts as complete coverage.
    skills, left_out = None, []
    if skip_linked_skills:
        report = migration.skill_report(source["id"])
        skills, left_out = report["importable"], report["blocked"]
    plan = migration.config_preview(profile["id"], profile["revision"], source["id"], skills, include_private)
    summary = {"files": plan["files"], "warnings": plan["warnings"], "private_api_configuration_copied": plan["private_api_configuration_copied"],
               "credentials_copied": plan["credentials_copied"], "skills_left_out_for_links": left_out}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    _confirm(yes)
    with _locked(profile["id"]):
        return migration.config_apply(plan["token"])


def import_conversations(profile_id, reference, *, yes=False):
    profile = profiles.get(profile_id)
    from agent_profile_share import is_history_shared
    if is_history_shared(profile["id"]):
        raise profiles.Refused(tr("대화 기록을 전역과 공유하는 프로필은 대화를 가져올 필요가 없습니다"))
    source = _source(reference, profile["tool"])
    rows = sorted(migration.conversations(source["id"]).values(), key=lambda r: r["mtime"], reverse=True)
    eligible = [r for r in rows if r["ownership"] not in _SKIPPED]
    skipped = len(rows) - len(eligible)
    print(tr("가져올 대화 {n}개 ({mib} MiB) · 소유 불명으로 제외 {skipped}개 · 원본은 그대로 둡니다",
             n=len(eligible), mib=sum(r["bytes"] for r in eligible) // (1024 * 1024), skipped=skipped))
    _confirm(yes)
    imported, existing, failed = 0, 0, []
    for row in eligible:
        current = profiles.get(profile["id"])
        try:
            plan = migration.conversation_preview(current["id"], current["revision"], source["id"], row["id"])
            with _locked(current["id"]):
                migration.conversation_apply(plan["token"])
            imported += 1
        except (profiles.Refused, ValueError, OSError, fsguard.FsDenied) as exc:
            reason = str(exc)
            if reason == tr("대상에 대화가 이미 있습니다. 덮어쓰지 않습니다"):
                existing += 1
            else:
                failed.append({"conversation": row["conversation"], "bytes": row["bytes"], "reason": reason})
    return {"imported": imported, "already_present": existing, "failed": failed, "excluded_unowned": skipped}


_DEFAULTS = "defaults.json"


def _defaults():
    path = fsguard.profile_file(profiles.directory(), _DEFAULTS)
    if not path.exists():
        return {}
    value = json.loads(fsguard.read_profile_bytes(profiles.directory(), _DEFAULTS, 16 * 1024))
    return value if isinstance(value, dict) else {}


def _active(tool=None):
    return [p for p in profiles.listing()["profiles"] if p["archived_at"] is None and (tool is None or p["tool"] == tool)]


def resolve(reference, tool=None):
    """A profile by exact name or ID. An unknown or ambiguous reference is refused, never guessed."""
    rows = [p for p in _active(tool) if reference in (p["name"], p["id"])]
    if len(rows) == 1:
        return rows[0]
    names = ", ".join(sorted(p["name"] for p in _active(tool))) or tr("없음")
    if rows:
        raise profiles.Refused(tr("이름이 같은 프로필이 둘 이상입니다. ID를 쓰세요: {names}", names=names))
    raise profiles.Refused(tr("그런 프로필이 없습니다. 있는 프로필: {names}", names=names))


def defaults():
    """The marked default profile id per tool (only what was explicitly chosen; archived ones included as-is)."""
    out = {}
    for tool, pid in _defaults().items():
        if tool in ("claude", "codex") and isinstance(pid, str) and profiles.ID.fullmatch(pid):
            out[tool] = pid
    return out


def set_default_for(tool, profile_id):
    """Mark (or, with `profile_id=None`, clear) the default profile of one tool, for NEW sessions only.

    Same file the CLI's `fsh profile default` writes. Running sessions are never touched.
    """
    import config_io
    from agent_profile_migration import _write_bytes
    if tool not in ("claude", "codex"):
        raise profiles.Refused(tr("지원하지 않는 CLI입니다"))
    path = fsguard.profile_file(profiles.directory(), _DEFAULTS)
    with config_io.locked(path):
        values = _defaults()
        if profile_id is None:
            values.pop(tool, None)
        else:
            profile = profiles.get(profile_id)
            profiles.require_active(profile)
            if profile["tool"] != tool:
                raise profiles.Refused(tr("프로필의 도구가 일치하지 않습니다"))
            values[tool] = profile["id"]
        _write_bytes(path, json.dumps(values).encode(), 0o600)
    return {"tool": tool, "default": profile_id}


def default_for(tool):
    """The profile `fsh claude` / `fsh codex` use without a name: the one marked default, else the only one."""
    rows = _active(tool)
    wanted = _defaults().get(tool)
    chosen = [p for p in rows if p["id"] == wanted]
    if chosen:
        return chosen[0]
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise profiles.Refused(tr("{tool} 프로필이 없습니다. `fsh profile create <이름> --tool {tool}`로 만드세요", tool=tool))
    names = ", ".join(sorted(p["name"] for p in rows))
    raise profiles.Refused(tr("기본 프로필이 없습니다. `fsh profile default <이름>`으로 정하거나 이름을 쓰세요: {names}", names=names))


def set_default(reference=None):
    if reference is None:
        return {tool: next((p["name"] for p in _active(tool) if p["id"] == pid), None) for tool, pid in _defaults().items()}
    chosen = resolve(reference)
    from agent_profile_migration import _write_bytes
    values = _defaults()
    values[chosen["tool"]] = chosen["id"]
    _write_bytes(fsguard.profile_file(profiles.directory(), _DEFAULTS), json.dumps(values).encode(), 0o600)
    return {"tool": chosen["tool"], "default": chosen["name"]}


def run(tool, reference=None, *, cwd, conversation=None, bypass=False, tmux=False):
    """Start the CLI with the profile right here, like typing `claude`.

    `tmux=True` (or no terminal at all) keeps the old behaviour: a session in FarShell's tmux that
    survives detaching and is recorded by the server. The direct run is not recorded as a session.
    """
    chosen = resolve(reference, tool) if reference else default_for(tool)
    if not tmux and sys.stdin.isatty() and sys.stdout.isatty():
        _, directory, env, argv, _, _ = runtime.prepare_launch(chosen["id"], profiles.get(chosen["id"])["revision"], cwd, conversation, bypass=bypass)
        os.chdir(directory)
        sys.stdout.flush()
        os.execvpe(argv[0], argv, env)
    started = launch(chosen["id"], cwd, conversation, bypass=bypass)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return {**started, "profile": chosen["name"], "attached": False}
    import tmux_runner
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}  # a different tmux server may be nested
    sys.stdout.flush()
    os.execvpe("tmux", ["tmux", "-u", "-L", tmux_runner.VT_TMUX_SOCKET, "attach-session", "-t", "=" + started["session"]], env)


def launch(profile_id, cwd, conversation=None, *, bypass=False):
    profile = profiles.get(profile_id)
    return runtime.launch(profile["id"], profile["revision"], cwd, conversation, bypass=bypass)


def probe(profile_id):
    profile = profiles.get(profile_id)
    return runtime.probe(profile["id"], profile["revision"])


def adopt(profile_id):
    profile = profiles.get(profile_id)
    return profiles.adopt(profile["id"], profile["revision"])


def share(profile_id):
    """Link the profile to the global ~/.claude (replacing identical copies; the rest is kept)."""
    import agent_profile_share
    profile = profiles.get(profile_id)
    return agent_profile_share.sync(profile["id"])


def sync_settings(profile_id, *, dry_run=False, take_global=()):
    import agent_profile_settings
    profile = profiles.get(profile_id)
    return agent_profile_settings.sync(profile["id"], dry_run=dry_run, take_global=take_global, include_mcp=None if not dry_run else False)


def toggle(profile_id, kind, name, state):
    import agent_profile_settings
    profile = profiles.get(profile_id)
    if state not in ("on", "off"):
        raise profiles.Refused(tr("on 또는 off만 쓸 수 있습니다"))
    return agent_profile_settings.set_state(profile["id"], kind, name, state == "on")


def recover(profile_id):
    """Close interrupted jobs after re-reading the native files; never replays them."""
    import agent_profile_config
    profile = profiles.get(profile_id)
    return agent_profile_config.recover(profile["id"], profile["revision"])
