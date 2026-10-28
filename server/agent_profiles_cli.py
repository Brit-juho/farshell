"""Local profile commands; no hidden copy of an existing OAuth grant."""
from __future__ import annotations

import argparse
import json
import sys

import agent_profiles
import agent_profile_runtime
import agent_profile_local as local
import agent_profile_migration
import fsguard
from i18n import tr


def main():
    parser = argparse.ArgumentParser(description=tr("FarShell 실행 프로필 관리"))
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("list")
    subs.add_parser("sources")
    subs.add_parser("retirement")
    create = subs.add_parser("create")
    create.add_argument("name")
    create.add_argument("--tool", choices=("claude", "codex"), required=True)
    create.add_argument("--account")
    create.add_argument("--test", action="store_true")
    for action in ("login", "status"):
        sub = subs.add_parser(action)
        sub.add_argument("profile")
    for action in ('rename', 'archive', 'restore'):
        sub = subs.add_parser(action)
        sub.add_argument('profile')
        if action == 'rename':
            sub.add_argument('name')
    cfg = subs.add_parser("import-config", help=tr("원본 설정을 가져옵니다(검토 후 적용)"))
    cfg.add_argument("profile")
    cfg.add_argument("source")
    cfg.add_argument("--private", action="store_true", help=tr("API 키·엔드포인트를 테스트 프로필로 복제"))
    cfg.add_argument("--skip-linked-skills", action="store_true", help=tr("링크가 든 스킬은 빼고 가져옵니다(커버리지는 불완전로 남음)"))
    cfg.add_argument("--yes", action="store_true")
    conv = subs.add_parser("import-conversations", help=tr("소유가 확인된 대화를 일괄 가져옵니다"))
    conv.add_argument("profile")
    conv.add_argument("source")
    conv.add_argument("--yes", action="store_true")
    run = subs.add_parser("launch", help=tr("프로필로 세션을 시작합니다"))
    run.add_argument("profile")
    run.add_argument("cwd")
    run.add_argument("--resume")
    sess = subs.add_parser("session", help=tr("시작한 세션의 검증 상태(서버가 기록한 값)"))
    sess.add_argument("name")
    for action in ("probe", "adopt", "recover"):
        subs.add_parser(action).add_argument("profile")
    args = parser.parse_args()
    try:
        if args.action == "login":
            return agent_profile_runtime.login(args.profile)
        if args.action in ('rename', 'archive', 'restore'):
            profile = agent_profiles.get(args.profile)
            result = agent_profiles.metadata_change(profile['id'], profile['revision'], profile['metadata_revision'], args.action, getattr(args, 'name', None))
        elif args.action == "import-config":
            result = local.import_config(args.profile, args.source, include_private=args.private, skip_linked_skills=args.skip_linked_skills, yes=args.yes)
        elif args.action == "import-conversations":
            result = local.import_conversations(args.profile, args.source, yes=args.yes)
        elif args.action == "launch":
            result = local.launch(args.profile, args.cwd, args.resume)
        elif args.action == "session":
            result = agent_profile_runtime.session_recorded(args.name)
        elif args.action == "probe":
            result = local.probe(args.profile)
        elif args.action == "adopt":
            result = local.adopt(args.profile)
        elif args.action == "recover":
            result = local.recover(args.profile)
        elif args.action == "status":
            result = agent_profile_runtime.auth_status(args.profile)
        elif args.action == "create":
            result = agent_profiles.create(args.name, args.tool, account_id=args.account, purpose="test" if args.test else "work")
        elif args.action == "sources":
            result = agent_profile_migration.source_list()
        elif args.action == "retirement":
            result = agent_profile_migration.retirement_report()
        else:
            result = agent_profiles.listing()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except agent_profiles.Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except fsguard.FsDenied as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print(tr("프로필 작업에 실패했습니다. 설정과 CLI 설치 상태를 확인하세요"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
