"""Certify an observed completed native turn; never send a model/tool request."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import agent_profiles as profiles
import agent_profile_runtime as runtime
import fsguard
from i18n import tr


def _assistant_time(row, tool):
    message = row.get('message') if tool == 'claude' and row.get('type') == 'assistant' else row.get('payload') if tool == 'codex' and row.get('type') == 'response_item' else None
    if not isinstance(message, dict) or tool == 'codex' and (message.get('type') != 'message' or message.get('role') != 'assistant'):
        return None
    content = message.get('content')
    if not isinstance(content, list) or not any(isinstance(c, dict) and c.get('type') in ('text', 'output_text') and isinstance(c.get('text'), str) and c['text'].strip() for c in content):
        return None
    timestamp = row.get('timestamp')
    try:
        stamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if isinstance(timestamp, str) else None
        return stamp.timestamp() if stamp and stamp.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def certify(profile_id, revision, session):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    profiles.require_no_pending_jobs(profile_id)
    state = runtime.session_status(session)
    if state['profile_id'] != profile_id or state['state'] != 'running' or state['applied_revision'] != revision:
        raise profiles.Refused(tr("현재 프로필과 검증된 실행 세션이 일치하지 않습니다"))
    import agent_config_context
    import agent_config_restart
    ctx = agent_config_context.identify(session)
    owner = agent_config_restart._owner(ctx)
    if owner.get('status') not in ('idle', 'done'):
        raise profiles.Refused(tr("실제 모델 응답이 끝난 뒤 확인하세요"))
    sid = owner.get('session_id')
    import re
    if not isinstance(sid, str) or not re.fullmatch(r'[a-fA-F0-9-]{32,36}', sid):
        raise profiles.Refused(tr("이어갈 대화 ID를 확인하지 못했습니다"))
    with profiles.database() as db:
        saved = dict(db.execute('SELECT * FROM sessions WHERE name=?', (session,)).fetchone())
    fingerprint = runtime.authentication_fingerprint(profile_id)
    if not fingerprint or not saved.get('launch_auth_fingerprint'):
        raise profiles.Refused(tr("이 세션의 인증 증거가 없습니다. 새 프로필 세션을 여세요"))
    credential_seen_at = None
    if fingerprint != saved['launch_auth_fingerprint']:
        with profiles.database() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
            fields = {r[1] for r in db.execute('PRAGMA table_info(observations)')} if exists else set()
            observation = db.execute('SELECT * FROM observations WHERE profile_id=?', (profile_id,)).fetchone() if 'last_fingerprint' in fields else None
        if not observation or not saved.get('launch_subject') or observation['subject'] != saved['launch_subject'] or observation['last_fingerprint'] != fingerprint or time.time() - observation['last_at'] > 1800 or 'credential_seen_at' not in fields or observation['credential_seen_at'] is None:
            raise profiles.Refused(tr("세션 인증이 바뀌었습니다. 같은 계정의 최신 사용량을 확인하거나 새 세션을 여세요"))
        credential_seen_at = observation['credential_seen_at']
    root = profiles.root(profile_id)
    found = None
    scanned = size = 0
    folders = ('projects',) if profile['tool'] == 'claude' else ('sessions', 'archived_sessions')
    for folder in folders:
        base = root / folder
        if not base.is_dir():
            continue
        for path in fsguard.profile_tree(base):
            scanned += 1
            if scanned > 2000:
                raise profiles.Refused(tr("대화가 너무 많습니다. 원본 범위를 좁히세요"))
            if path.suffix != '.jsonl' or profile['tool'] == 'claude' and path.name != sid + '.jsonl':
                continue
            raw = fsguard.read_profile_bytes(root, str(path.relative_to(root)), 64 * 1024 * 1024 - size)
            size += len(raw)
            lines = raw.splitlines()
            if profile['tool'] == 'codex':
                try:
                    meta = json.loads(lines[0])
                except (IndexError, ValueError):
                    continue
                if not isinstance(meta, dict) or meta.get('type') != 'session_meta' or not isinstance(meta.get('payload'), dict) or meta['payload'].get('id') != sid:
                    continue
            for line in lines:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                timestamp = _assistant_time(item, profile['tool'])
                if timestamp is not None and max(saved['started_at'], credential_seen_at or saved['started_at']) <= timestamp <= time.time() + 5:
                    found = max(found or timestamp, timestamp)
    if found is None:
        raise profiles.Refused(tr("이 세션이 시작된 뒤의 실제 모델 응답을 찾지 못했습니다"))
    # Recheck process generation, auth and revision after bounded transcript IO.
    current = runtime.session_status(session)
    if current['profile_id'] != profile_id or current['state'] != 'running' or current['applied_revision'] != revision or runtime.authentication_fingerprint(profile_id) != fingerprint:
        raise profiles.Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
    profiles.require_revision(profiles.get(profile_id), revision)
    from agent_profile_migration import _receipt
    receipt = _receipt(profile_id, '', 'native-probe', {'auth_fingerprint': fingerprint, 'native_tool': profile['tool'],
                       'mode': 'observed-native-turn', 'session': session, 'conversation': sid, 'assistant_at': found,
                       'profile_revision': revision}, True)
    return {'status': 'verified', 'native_inference_verified': True, 'receipt': receipt, 'model_request_sent': False}
