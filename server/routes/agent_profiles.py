"""Direct-host human-only profile management, with session-bound review plans."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import agent_profiles as profiles
import agent_profile_config as config
import agent_profile_migration as migration
import agent_profile_runtime as runtime
import auth
import fsguard
import mcp_plans
from i18n import tr

router = APIRouter(dependencies=[Depends(auth.require_human_session)])
elevated_router = APIRouter(dependencies=[Depends(auth.require_human_session), Depends(auth.require_elevated)])


def _public_result(value):
    """Keep CAS evidence server-side when it fingerprints private material."""
    if isinstance(value, list):
        return [_public_result(item) for item in value]
    if not isinstance(value, dict):
        return value
    private_names = {'.farshell-provider.json', '.farshell-hooks-disabled.json',
                     '.credentials.json', 'credentials.json', 'auth.json'}
    path = value.get('path', value.get('relative'))
    private_file = isinstance(path, str) and path.replace('\\', '/').rsplit('/', 1)[-1] in private_names
    hidden = {'auth_fingerprint', 'launch_auth_fingerprint', 'initial_access_fingerprint',
              'last_access_fingerprint', 'credential_fingerprint'}
    if private_file:
        hidden.update({'sha256', 'before_sha256', 'fingerprint', 'before_hash', 'after_hash'})
    result = {key: _public_result(item) for key, item in value.items() if key not in hidden}
    if private_file:
        result['private'] = True
    return result


async def _body(request):
    raw = await request.body()
    if len(raw) > 32 * 1024:
        raise profiles.Refused(tr("요청이 너무 큽니다"))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise profiles.Refused(tr("잘못된 요청입니다"))
    return value


def _actor(request):
    return hashlib.sha256(request.cookies.get("vt_session", "").encode()).hexdigest()


def _keys(body, allowed):
    if set(body) - set(allowed):
        raise profiles.Refused(tr("지원하지 않는 요청 필드가 있습니다"))


def _read(action, body, actor):
    if action == "list":
        _keys(body, [])
        return profiles.listing()
    if action == "sources":
        _keys(body, [])
        return migration.source_list()
    if action == "retirement":
        _keys(body, [])
        return migration.retirement_report()
    if action == "conversations":
        _keys(body, ["source_id"])
        return migration.conversation_list(body.get("source_id"))
    if action == "hooks":
        _keys(body, ["profile_id"])
        return config.inventory(body.get("profile_id"))
    if action == "auth-status":
        _keys(body, ["profile_id"])
        return runtime.auth_status(body.get("profile_id"))
    if action == "session-status":
        _keys(body, ["session"])
        return runtime.session_status(body.get("session"))
    if action == "picker":
        # A light list for the new-session account chooser: names, auth state and the last cached usage.
        # No secrets, no paths, and it never starts a network request.
        _keys(body, [])
        import session_profile
        return session_profile.picker()
    if action == "usage":
        _keys(body, [])
        from usage.profiles import snapshot
        return {"profiles": snapshot()}
    if action == "job":
        _keys(body, ["job_id"])
        return profiles.job_status(body.get("job_id"))
    if action == "inventory":
        _keys(body, ["profile_id", "plugins"])
        import agent_config
        import agent_config_context
        with agent_config_context.use(profiles.context(body.get("profile_id"))):
            return agent_config.full_inventory(None, "all", plugins=body.get("plugins") is True)
    if action != "preview":
        raise profiles.Refused(tr("지원하지 않는 관리 작업입니다"))
    operation = body.get("operation")
    payload = body.get("payload")
    _keys(body, ["operation", "payload"])
    if not isinstance(payload, dict):
        raise profiles.Refused(tr("잘못된 요청입니다"))
    bound_profile = profiles.get(payload['profile_id']) if payload.get('profile_id') else None
    if bound_profile is not None and operation != 'restore':
        profiles.require_active(bound_profile)
    if operation in ('rename', 'archive', 'restore'):
        _keys(payload, ['profile_id', 'revision', 'name'] if operation == 'rename' else ['profile_id', 'revision'])
        if bound_profile is None or type(payload.get('revision')) is not int or payload['revision'] != bound_profile['revision']:
            raise profiles.Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
        if operation == 'rename' and (not isinstance(payload.get('name'), str) or not 1 <= len(payload['name'].strip()) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in payload['name'])):
            raise profiles.Refused(tr("프로필 이름 또는 도구가 올바르지 않습니다"))
        if operation != 'rename':
            profiles.require_no_pending_jobs(bound_profile['id'])
            runtime.require_inactive(bound_profile['id'])
        detail = {'operation': operation, 'profile_id': bound_profile['id'], 'name': payload.get('name', bound_profile['name']),
                  'warning': tr("보관은 로그인·대화를 지우지 않으며 실행 중인 프로필은 보관할 수 없습니다")}
    elif operation == "create":
        _keys(payload, ["name", "tool", "account_id", "account_label", "purpose"])
        if payload.get("tool") not in ("claude", "codex") or not isinstance(payload.get("name"), str) or not 1 <= len(payload["name"].strip()) <= 80:
            raise profiles.Refused(tr("프로필 이름 또는 도구가 올바르지 않습니다"))
        detail = {"name": payload["name"], "tool": payload["tool"], "purpose": payload.get("purpose", "work")}
    elif operation in ("launch", "recover", "refresh-test", "probe", "adopt"):
        allowed = {"launch": ["profile_id", "revision", "cwd", "conversation"],
                   "plugin": ["profile_id", "revision", "action", "plugin"], "recover": ["profile_id", "revision"],
                   "refresh-test": ["profile_id", "revision"], "probe": ["profile_id", "revision"], "adopt": ["profile_id", "revision"]}[operation]
        _keys(payload, allowed)
        profile = profiles.get(payload.get("profile_id"))
        profiles.require_revision(profile, payload.get("revision"))
        if operation == "launch":
            path = fsguard.resolve_under_roots(payload.get("cwd", ""), check_hidden=False)
            if not path.is_dir():
                raise profiles.Refused(tr("작업 디렉터리를 찾지 못했습니다"))
        detail = {"profile_id": profile["id"], "name": profile["name"], "operation": operation}
        if operation == 'adopt':
            detail['warning'] = tr("로그인과 설정을 유지하며 테스트 전용 강제 만료 기능을 끕니다. 대체 검증 결과는 별도로 확인하세요")
        if operation == 'probe':
            detail['warning'] = tr("이 프로필로 짧은 모델 요청을 보냅니다. 제공자 사용료가 발생할 수 있습니다")
        if operation == 'recover':
            detail['warning'] = tr("현재 파일을 유지하고 새 실행을 허용합니다. 원본 복원은 가져오기 되돌리기를 사용하세요")
        if operation == "refresh-test":
            if profile['purpose'] != 'test':
                raise profiles.Refused(tr("갱신 검증은 테스트 프로필에서만 가능합니다"))
            detail['warning'] = tr("테스트 로그인만 만료 상태로 바꿉니다. CLI를 정상 종료한 뒤 진행하세요. 실패하면 재로그인이 필요합니다")
    elif operation == "provider-edit":
        _keys(payload, ["profile_id", "revision", "patch"])
        import agent_profile_provider
        detail = agent_profile_provider.preview(**payload)
        payload = {'profile_id': payload['profile_id'], 'token': detail.pop('token')}
    elif operation == 'source-identity':
        _keys(payload, ['source_id'])
        import agent_profile_identity
        detail = agent_profile_identity.preview(**payload)
        payload = {'token': detail.pop('token')}
    elif operation == 'session-proof':
        _keys(payload, ['profile_id', 'revision', 'session'])
        if bound_profile is None:
            raise profiles.Refused(tr("프로필 ID가 올바르지 않습니다"))
        profiles.require_revision(bound_profile, payload.get('revision'))
        detail = {'session': payload.get('session'), 'model_request_sent': False}
    elif operation == 'plugin-fetch':
        _keys(payload, ['profile_id', 'revision', 'repo', 'commit', 'subdir'])
        if bound_profile is None:
            raise profiles.Refused(tr("프로필 ID가 올바르지 않습니다"))
        profiles.require_revision(bound_profile, payload.get('revision'))
        from agent_profile_download import validate
        repo, commit, subdir = validate(payload.get('repo'), payload.get('commit'), payload.get('subdir', '.'))
        detail = {'repo': repo, 'commit': commit, 'subdir': subdir, 'install_review_required': True}
    elif operation == "plugin":
        _keys(payload, ["profile_id", "revision", "action", "plugin", "path", "download_id", "job_id"])
        import agent_profile_plugins
        detail = agent_profile_plugins.preview(**payload)
        payload = {'profile_id': payload['profile_id'], 'token': detail.pop('token')}
    elif operation == "rollback":
        _keys(payload, ["profile_id", "revision", "job_id"])
        detail = migration.rollback(**payload, preview=True)
    elif operation == "mcp-add":
        _keys(payload, ["profile_id", "revision", "name", "definition"])
        import agent_profile_resources
        detail = agent_profile_resources.add_mcp(**payload, preview=True)
        payload = {**payload, 'file_revision': detail['file_revision']}
    elif operation == "config-import":
        _keys(payload, ["profile_id", "revision", "source_id", "skill_names", "include_private"])
        detail = migration.config_preview(**payload)
        payload = {"profile_id": payload["profile_id"], "token": detail.pop("token")}
    elif operation == "conversation-import":
        _keys(payload, ["profile_id", "revision", "source_id", "conversation_id"])
        detail = migration.conversation_preview(**payload)
        payload = {"profile_id": payload["profile_id"], "token": detail.pop("token")}
    elif operation == "hook":
        _keys(payload, ["profile_id", "revision", "event", "command", "matcher", "timeout", "remove_id", "hook_id", "enabled"])
        detail = config.preview(**payload)
        payload = {"profile_id": payload["profile_id"], "token": detail.pop("token")}
    elif operation in ("native-toggle", "native-edit"):
        _keys(payload, ["profile_id", "revision", "changes", "item_id", "content", "file_revision"])
        profile = profiles.get(payload.get("profile_id"))
        profiles.require_revision(profile, payload.get("revision"))
        import agent_config
        import agent_config_context
        import agent_config_maintenance
        with agent_config_context.use(profiles.context(profile["id"])):
            detail = (agent_config.preview(payload.get("changes"), None, "all") if operation == "native-toggle"
                      else agent_config_maintenance.preview_edit(payload.get("item_id"), None, "all", payload.get("file_revision"), payload.get("content")))
        payload = {"profile_id": profile["id"], "revision": profile["revision"], "token": detail.pop("token")}
    else:
        raise profiles.Refused(tr("지원하지 않는 관리 작업입니다"))
    return {**detail, **mcp_plans.issue("profile-action", {"actor": actor, "operation": operation, "payload": payload, "metadata_revision": bound_profile["metadata_revision"] if bound_profile else None})}


def _apply(body, actor):
    _keys(body, ["token"])
    token = body.get("token")
    if not isinstance(token, str) or len(token) > 256:
        raise profiles.Refused(tr("변경 계획이 올바르지 않습니다"))
    plan = mcp_plans.take(token, "profile-action")
    if not plan or plan["actor"] != actor:
        raise profiles.Refused(tr("변경 계획이 만료됐거나 검토한 로그인 세션과 다릅니다"))
    payload = plan['payload']
    if payload.get('profile_id'):
        import config_io
        profile_id = profiles.identifier(payload['profile_id'])
        with config_io.locked(profiles.root(profile_id) / '.profile-operation'):
            return _execute(plan)
    # Inner migration/hook plans bind a native revision as well as the profile.
    # Their writes use native file locks; standalone create holds the DB lock.
    return _execute(plan)


def _execute(plan):
    operation, payload = plan["operation"], plan["payload"]
    if payload.get('profile_id'):
        current = profiles.get(payload['profile_id'])
        if plan.get('metadata_revision') != current['metadata_revision']:
            raise profiles.Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
        if operation != 'restore':
            profiles.require_active(current)
    if operation in ('rename', 'archive', 'restore'):
        return profiles.metadata_change(**payload, metadata_revision=plan['metadata_revision'], operation=operation)
    if operation == "create":
        return profiles.create(**payload)
    if operation == "launch":
        return runtime.launch(**payload)
    if operation == "adopt":
        return profiles.adopt(**payload)
    if operation == "probe":
        return runtime.probe(**payload)
    if operation == "recover":
        return config.recover(**payload)
    if operation == "refresh-test":
        profile = profiles.get(payload['profile_id'])
        profiles.require_revision(profile, payload['revision'])
        from usage.profiles import prepare_refresh_test
        return prepare_refresh_test(profile['id'])
    if operation == "provider-edit":
        import agent_profile_provider
        return agent_profile_provider.apply(payload['token'])
    if operation == 'source-identity':
        import agent_profile_identity
        return agent_profile_identity.apply(payload['token'])
    if operation == 'session-proof':
        from agent_profile_turn import certify
        return certify(**payload)
    if operation == 'plugin-fetch':
        from agent_profile_download import fetch
        return fetch(**payload)
    if operation == "plugin":
        import agent_profile_plugins
        return agent_profile_plugins.apply(payload['token'])
    if operation == "rollback":
        return migration.rollback(**payload)
    if operation == "mcp-add":
        import agent_profile_resources
        return agent_profile_resources.add_mcp(**payload)
    if operation == "config-import":
        return migration.config_apply(payload["token"])
    if operation == "conversation-import":
        return migration.conversation_apply(payload["token"])
    if operation == "hook":
        return config.apply(payload["token"])
    if operation in ("native-toggle", "native-edit"):
        profile = profiles.get(payload["profile_id"])
        profiles.require_revision(profile, payload["revision"])
        import agent_config
        import agent_config_context
        import agent_config_maintenance
        with agent_config_context.use(profiles.context(profile["id"])):
            result = agent_config.apply(payload["token"]) if operation == "native-toggle" else agent_config_maintenance.apply_edit(payload["token"])
        profiles.bump(profile["id"], profile["revision"], "ready" if result.get("status") == "ok" else "partial")
        return result
    raise profiles.Refused(tr("지원하지 않는 관리 작업입니다"))


def _error(exc):
    reason = str(exc) if isinstance(exc, (profiles.Refused, fsguard.FsDenied)) else tr("프로필 작업을 완료하지 못했습니다. 원본은 보존됩니다")
    return JSONResponse({"error": "profile_refused", "reason": reason}, status_code=409)


@router.post("/api/agent-profiles/read/{action}", response_model=dict)
async def read(action: str, request: Request):
    try:
        if action == "preview":
            # Elevate before binding the preview to this exact signed cookie.
            # Password elevation reissues it, so doing that after preview would
            # invalidate the plan the person just reviewed.
            auth.require_elevated(request)
        return _public_result(await asyncio.to_thread(_read, action, await _body(request), _actor(request)))
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error, fsguard.FsDenied) as exc:
        return _error(exc)


@router.post("/api/agent-profiles/default", response_model=dict)
async def set_default(request: Request):
    """Change which profile NEW sessions of one tool use by default. A person's browser session only
    (the router requires it); no password re-check, because it changes no credential and no running
    session. Peers, machine tokens, the unix socket and agent MCP are refused by `require_human_session`."""
    try:
        body = await _body(request)
        _keys(body, ["tool", "profile_id"])
        import agent_profile_local
        profile_id = body.get("profile_id")
        if profile_id is not None:
            profile_id = profiles.identifier(profile_id)
        return await asyncio.to_thread(agent_profile_local.set_default_for, body.get("tool"), profile_id)
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error, fsguard.FsDenied) as exc:
        return _error(exc)


@elevated_router.post("/api/agent-profiles/apply", response_model=dict)
async def apply(request: Request):
    try:
        return _public_result(await asyncio.to_thread(_apply, await _body(request), _actor(request)))
    except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error, fsguard.FsDenied) as exc:
        return _error(exc)
