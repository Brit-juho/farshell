"""작업 세션 UUID와 녹음 시점의 pane을 고정한다. 웹 PTY ID는 정책 키가 아니다."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

import auth
import tmux_runner
from i18n import tr

BOOT = secrets.token_hex(16)
_lock = threading.RLock()
_targets: dict[str, dict] = {}


def principal(request) -> str:
    cookie = getattr(request, 'cookies', {}).get('vt_session', '')
    device = auth.session_device(cookie) if cookie else None
    if device and device != "-":
        return 'device:' + device
    credential = (getattr(request, 'headers', {}).get('authorization', '')
                  or getattr(request, 'query_params', {}).get('token', '') or cookie)
    if credential.lower().startswith('bearer '):
        credential = credential[7:].strip()
    return 'credential:' + hashlib.sha256(credential.encode()).hexdigest()


def snapshot(name: str, pane: str = '') -> dict:
    """tmux 세션 이름을 UUID·pane PID로 변환한다. 옵션은 세션 수명 동안 보존된다."""
    with _lock:
        fmt = '#{session_id}\t#{@fsh_voice_id}\t#{pane_id}\t#{pane_pid}\t#{session_name}'
        value = tmux_runner.run_text(['display-message', '-p', '-t', pane or ('=' + name + ':'), fmt])
        if not value:
            raise ValueError(tr('음성 대상 세션이 없습니다'))
        parts = value.rstrip('\n').split('\t')
        if len(parts) != 5 or not parts[0] or not parts[2]:
            raise ValueError(tr('음성 대상 세션을 찾을 수 없습니다'))
        sid, key, pid, process, actual = parts
        if name and actual != name:
            raise ValueError(tr('음성 대상 pane이 다른 세션에 속합니다'))
        if not key or len(key) != 32:
            key = secrets.token_hex(16)
            rc, _, _ = tmux_runner.run(['set-option', '-t', sid, '@fsh_voice_id', key])
            if rc:
                raise ValueError(tr('음성 대상 식별자를 저장할 수 없습니다'))
        return {'key': key, 'name': actual, 'tmux_id': sid, 'pane': pid,
                'generation': process, 'boot': BOOT}


def from_web(session_id: str) -> dict:
    from deps import session_store
    info = session_store.get(session_id)
    if not info or not info.tmux_name:
        raise ValueError(tr('tmux 작업 세션을 먼저 선택하세요'))
    return snapshot(info.tmux_name)


def valid(target: dict) -> bool:
    try:
        current = snapshot('', target['pane'])
        return all(current.get(k) == target.get(k) for k in ('key', 'tmux_id', 'pane', 'generation', 'boot'))
    except (ValueError, KeyError):
        return False


def issue(owner: str, target: dict) -> dict:
    now = time.monotonic()
    for key in list(_targets):
        if _targets[key]['expires'] < now:
            del _targets[key]
    if len(_targets) >= 512:
        raise ValueError(tr('음성 대상 요청이 너무 많습니다'))
    token = secrets.token_urlsafe(24)
    _targets[token] = {'owner': owner, 'target': target, 'expires': now + 180}
    return {'token': token, **target, 'ttl_sec': 180, 'max_record_sec': 60}


def get(token: str, owner: str) -> dict:
    item = _targets.get(token)
    if not item or item['owner'] != owner or item['expires'] < time.monotonic():
        raise ValueError(tr('음성 대상이 만료됐습니다. 새로 녹음해 주세요'))
    return dict(item['target'])
