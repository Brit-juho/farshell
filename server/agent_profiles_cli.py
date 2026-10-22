"""Local profile commands; no hidden copy of an existing OAuth grant."""
from __future__ import annotations

import argparse
import json
import sys

import agent_profiles
import agent_profile_runtime
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
    args = parser.parse_args()
    try:
        if args.action == "login":
            return agent_profile_runtime.login(args.profile)
        if args.action == "status":
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
    except (ValueError, OSError, fsguard.FsDenied):
        print(tr("프로필 작업에 실패했습니다. 설정과 CLI 설치 상태를 확인하세요"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
