"""Write-only, reviewed Claude API authentication and environment edits."""
from __future__ import annotations

import json

import agent_profiles as profiles
import agent_profile_migration as migration
import agent_profile_secret as secret
import config_io
import fsguard
import mcp_plans
from i18n import tr


def preview(profile_id, revision, patch):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    if profile['tool'] != 'claude' or not isinstance(patch, dict) or not patch or set(patch) - {'api_key', 'base_url', 'env', 'clear'}:
        raise profiles.Refused(tr("API 설정 변경 형식이 올바르지 않습니다"))
    from agent_profile_runtime import require_inactive
    profiles.require_no_pending_jobs(profile_id)
    require_inactive(profile_id)
    root = profiles.root(profile_id)
    data = secret.read(root) or {'api_key': None, 'env': {}}
    data = {'api_key': data.get('api_key'), 'env': dict(data['env'])}
    if 'clear' in patch and (patch['clear'] is not True or len(patch) != 1):
        raise profiles.Refused(tr("API 설정 제거는 별도로 검토하세요"))
    settings = json.loads(fsguard.read_profile_bytes(root, 'settings.json'))
    if not isinstance(settings, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    if patch.get('clear'):
        private = None
        settings.pop('apiKeyHelper', None)
    else:
        if 'api_key' in patch:
            data['api_key'] = patch['api_key']
        custom = patch.get('env', {})
        if not isinstance(custom, dict):
            raise profiles.Refused(tr("프로필 환경변수 형식이 올바르지 않습니다"))
        for key, value in custom.items():
            if value is None:
                data['env'].pop(key, None)
            else:
                data['env'][key] = value
        if 'base_url' in patch:
            if patch['base_url'] is None:
                data['env'].pop('ANTHROPIC_BASE_URL', None)
            else:
                data['env']['ANTHROPIC_BASE_URL'] = patch['base_url']
        private, helper = migration._private_configuration(data, root)
        if helper:
            settings['apiKeyHelper'] = helper
        else:
            settings.pop('apiKeyHelper', None)
    writes = {secret.FILENAME: private, 'settings.json': json.dumps(settings, ensure_ascii=False, indent=2).encode()}
    before = {}
    for name in writes:
        path = fsguard.profile_file(root, name)
        before[name] = profiles.digest(fsguard.read_profile_bytes(root, name)) if path.exists() else None
    plan = {'profile_id': profile_id, 'revision': revision, 'before': before,
            'writes': {name: raw.decode() if raw is not None else None for name, raw in writes.items()}}
    return {**mcp_plans.issue('profile-provider', plan), 'profile_id': profile_id,
            'clear': patch.get('clear') is True, 'environment_keys': sorted(patch.get('env', {})),
            'warning': tr("이 프로필의 API 인증·환경만 변경합니다. 비밀값은 다시 표시하지 않습니다")}


def apply(token):
    plan = mcp_plans.take(token, 'profile-provider')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    pid = plan['profile_id']
    profile = profiles.get(pid)
    profiles.require_revision(profile, plan['revision'])
    profiles.require_no_pending_jobs(pid)
    from agent_profile_runtime import require_inactive
    require_inactive(pid)
    root = profiles.root(pid)
    for name, expected in plan['before'].items():
        path = fsguard.profile_file(root, name)
        actual = profiles.digest(fsguard.read_profile_bytes(root, name)) if path.exists() else None
        if actual != expected:
            raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
    jid = profiles.job_begin(pid, 'provider-edit')
    files = []
    try:
        for name, text in plan['writes'].items():
            path = fsguard.profile_file(root, name)
            with config_io.locked(path):
                raw = fsguard.read_profile_bytes(root, name) if path.exists() else None
                if (profiles.digest(raw) if raw is not None else None) != plan['before'][name]:
                    raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
                if raw is not None:
                    backup = fsguard.private_profile_directory(profiles.directory() / 'backups' / jid)
                    migration._write_bytes(backup / (profiles.digest(name.encode()) + '.bak'), raw)
                after = text.encode() if text is not None else None
                files.append({'path': name, 'before_sha256': plan['before'][name], 'sha256': profiles.digest(after) if after is not None else None})
                profiles.job_finish(jid, 'running', {'files': files})
                if after is None:
                    path.unlink(missing_ok=True)
                else:
                    migration._write_bytes(path, after)
        return profiles.complete_job(pid, plan['revision'], jid, {'status': 'ok', 'files': files, 'restart_required': True})
    except BaseException:
        if files:
            profiles.bump(pid, plan['revision'], 'partial')
        profiles.job_finish(jid, 'partial' if files else 'failed', {'files': files, 'recovery_required': bool(files)})
        raise
