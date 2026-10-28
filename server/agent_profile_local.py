"""Local-shell profile operations: reviewed imports, launch and session checks.

Same trust tier as `fsh otp setup` and `fsh git-account`: whoever holds this shell.
Everything goes through the same preview/apply code as the web flow, so source
preservation, hash checks and rollback records are shared. Nothing here prints a
token, key or transcript content.
"""
from __future__ import annotations

import json
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


def launch(profile_id, cwd, conversation=None):
    profile = profiles.get(profile_id)
    return runtime.launch(profile["id"], profile["revision"], cwd, conversation)


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


def recover(profile_id):
    """Close interrupted jobs after re-reading the native files; never replays them."""
    import agent_profile_config
    profile = profiles.get(profile_id)
    return agent_profile_config.recover(profile["id"], profile["revision"])
