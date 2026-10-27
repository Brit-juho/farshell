"""Reviewed identity-only requests with existing source OAuth access tokens.

Never copy a grant, call a refresh endpoint, or write the source credential store.
Only opaque provider identities and private fingerprints are persisted locally.
"""
from __future__ import annotations

import json
import re
import time
import tomllib

import agent_profiles as profiles
import agent_profile_migration as migration
import fsguard
import mcp_plans
from i18n import tr


def _source_auth(source_id):
    source = migration._source(source_id)
    if source['kind'] != 'clauth':
        raise profiles.Refused(tr("기존 clauth OAuth 프로필을 선택하세요"))
    config_raw = fsguard.read_profile_bytes(source['root'], 'config.toml')
    config = tomllib.loads(config_raw.decode())
    env = config.get('env', {})
    if not isinstance(env, dict) or config.get('api_key') or any(env.get(k) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')):
        raise profiles.Refused(tr("API 키 프로필은 원본과 인증 설정을 비교합니다"))
    filename = 'credentials.json' if source['tool'] == 'claude' else 'auth.json'
    try:
        raw = fsguard.read_profile_bytes(source['root'], filename, 256 * 1024)
    except FileNotFoundError:
        raise profiles.Refused(tr("원본 OAuth 인증 정보를 찾지 못했습니다")) from None
    data = json.loads(raw)
    tokens = data.get('claudeAiOauth' if source['tool'] == 'claude' else 'tokens') if isinstance(data, dict) else None
    token = tokens.get('accessToken' if source['tool'] == 'claude' else 'access_token') if isinstance(tokens, dict) else None
    if not isinstance(token, str) or not 1 <= len(token) <= 32768 or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise profiles.Refused(tr("원본 OAuth 인증 정보를 찾지 못했습니다"))
    return source, token, tokens.get('account_id'), profiles.digest(raw), profiles.digest(config_raw)


def preview(source_id):
    source, _, _, fingerprint, config_hash = _source_auth(source_id)
    return {**mcp_plans.issue('source-identity', {'source_id': source_id, 'fingerprint': fingerprint, 'config_hash': config_hash}),
            'source_id': source_id, 'tool': source['tool'], 'name': source['name'],
            'warning': tr("원본의 기존 OAuth 토큰으로 공식 제공자 계정만 확인합니다. 로그인·갱신·복사는 하지 않습니다")}


def apply(token):
    plan = mcp_plans.take(token, 'source-identity')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    source, credential, account, fingerprint, config_hash = _source_auth(plan['source_id'])
    if fingerprint != plan['fingerprint'] or config_hash != plan['config_hash']:
        raise profiles.Refused(tr("이전 원본이 바뀌었습니다. 다시 검토하세요"))
    from usage.profiles import _request
    headers = {'Authorization': 'Bearer ' + credential, 'Accept': 'application/json'}
    if source['tool'] == 'claude':
        headers.update({'anthropic-beta': 'oauth-2025-04-20', 'User-Agent': 'farshell-profile-usage'})
        data = _request('https://api.anthropic.com/api/oauth/profile', headers)
        account_data, organization = data.get('account'), data.get('organization')
        subject = account_data.get('uuid') if isinstance(account_data, dict) else None
        tenant = organization.get('uuid') if isinstance(organization, dict) else None
        if not isinstance(subject, str) or not subject:
            raise profiles.Refused(tr("제공자 계정 신원을 확인하지 못했습니다"))
        identity = profiles.digest(json.dumps(['claude', subject, tenant]).encode())
    else:
        from usage.codex import USAGE_URL, _USER_AGENT
        headers['User-Agent'] = _USER_AGENT
        if isinstance(account, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', account):
            headers['ChatGPT-Account-Id'] = account
        data = _request(USAGE_URL, headers)
        subject = data.get('account_id')
        if not isinstance(subject, str) or not subject:
            raise profiles.Refused(tr("제공자 계정 신원을 확인하지 못했습니다"))
        identity = profiles.digest(('codex:' + subject).encode())
    if _source_auth(plan['source_id'])[3:] != (fingerprint, config_hash):
        raise profiles.Refused(tr("이전 원본이 바뀌었습니다. 다시 검토하세요"))
    with profiles.database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS source_identities (source_id TEXT PRIMARY KEY, subject TEXT NOT NULL, fingerprint TEXT NOT NULL, config_hash TEXT NOT NULL, checked_at REAL NOT NULL)')
        db.execute('INSERT OR REPLACE INTO source_identities VALUES (?,?,?,?,?)', (source['id'], identity, fingerprint, config_hash, time.time()))
    return {'status': 'verified', 'source_id': source['id'], 'identity_verified': True, 'source_preserved': True}


def matches(source_id, observation, fingerprint):
    try:
        source, _, _, source_fp, config_hash = _source_auth(source_id)
        with profiles.database() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_identities'").fetchone():
                return False
            row = db.execute('SELECT * FROM source_identities WHERE source_id=?', (source_id,)).fetchone()
        return bool(row and row['fingerprint'] == source_fp and row['config_hash'] == config_hash
                    and time.time() - row['checked_at'] <= 1800 and observation.get('subject') == row['subject']
                    and observation.get('last_fingerprint') == fingerprint and time.time() - observation.get('last_at', 0) <= 1800)
    except (ValueError, OSError, fsguard.FsDenied):
        return False


def requires_identity(source_id):
    source = migration._source(source_id)
    config = tomllib.loads(fsguard.read_profile_bytes(source['root'], 'config.toml').decode())
    env = config.get('env', {})
    if not isinstance(env, dict):
        raise ValueError('invalid-environment')
    if config.get('api_key') or any(env.get(k) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')):
        return False
    filename = 'credentials.json' if source['tool'] == 'claude' else 'auth.json'
    return fsguard.profile_file(source['root'], filename).exists()
