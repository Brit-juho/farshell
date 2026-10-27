"""Fixed-origin API quota adapters; never discover endpoints using a key.

Wire contracts compared against clauth providers at
72b9f43aae28fc60b9ac865badbcfaaca14815f4. Alibaba console credentials
are intentionally unsupported; regional origins never share credentials.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

ORIGINS = {
    'api.deepseek.com': ('deepseek', '/user/balance'),
    'openrouter.ai': ('openrouter', '/api/v1/credits'),
    'api.z.ai': ('zai', '/api/monitor/usage/quota/limit'),
    'api.minimax.io': ('minimax', '/v1/token_plan/remains'),
}
ALIBABA = frozenset({'token-plan.ap-southeast-1.maas.aliyuncs.com', 'token-plan.cn-beijing.maas.aliyuncs.com',
                     'coding-intl.dashscope.aliyuncs.com', 'coding.dashscope.aliyuncs.com', 'token-plan.maas.qwencloudapi.com'})


class RejectedKey(ValueError):
    pass


def target(private):
    value = private.get('env', {}).get('ANTHROPIC_BASE_URL')
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password or url.fragment:
            return None
    except ValueError:
        return None
    return url.hostname if url.hostname in ORIGINS or url.hostname in ALIBABA else None


def declares_api_credentials(private):
    return private.get('api_key') is not None or any(k in private.get('env', {}) for k in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'))


def credential(private):
    env = private.get('env', {})
    # Claude native precedence: a bearer token overrides API-key authentication.
    key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or private.get('api_key')
    if not isinstance(key, str) or not 1 <= len(key) <= 32768 or any(ord(c) < 33 or ord(c) == 127 for c in key):
        return None
    return key


def _number(value, *, string=False):
    if string and isinstance(value, str) and len(value) <= 128:
        try:
            value = float(value)
        except ValueError:
            pass
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('invalid-number')
    return float(value)


def _window(identity, label, pct, reset, now, used=None, total=None):
    pct = _number(pct)
    if not 0 <= pct <= 100:
        raise ValueError('invalid-percentage')
    timestamp = None
    if reset is not None:
        try:
            timestamp = _number(reset) / 1000
            iso = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            timestamp, iso = None, None
    else:
        iso = None
    row = {'id': identity, 'label': label, 'pct': pct, 'resets_at': iso,
           'resets_in_sec': max(0, int(timestamp - now)) if timestamp is not None else None}
    if used is not None:
        row['used'] = _number(used)
    if total is not None:
        row['total'] = _number(total)
    return row


def normalize(provider, data, now):
    windows, balances, details = [], [], []
    available = None
    if provider == 'deepseek':
        if type(data.get('is_available')) is not bool or not isinstance(data.get('balance_infos'), list):
            raise ValueError('invalid-balance')
        available = data['is_available']
        if len(data['balance_infos']) > 32:
            raise ValueError('too-many-balances')
        for row in data['balance_infos']:
            currency = row.get('currency') if isinstance(row, dict) else None
            if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{2,5}', currency):
                raise ValueError('invalid-currency')
            balances.append({'currency': currency, 'remaining': _number(row.get('total_balance'), string=True),
                             'granted': _number(row.get('granted_balance'), string=True), 'purchased': _number(row.get('topped_up_balance'), string=True)})
    elif provider == 'openrouter':
        wallet = data.get('data')
        if not isinstance(wallet, dict):
            raise ValueError('invalid-wallet')
        purchased, used = _number(wallet.get('total_credits')), _number(wallet.get('total_usage'))
        balances.append({'currency': 'USD', 'remaining': _number(purchased - used), 'purchased': purchased, 'used': used})
        available = purchased - used >= .005
    elif provider == 'minimax':
        verdict = data.get('base_resp')
        code = verdict.get('status_code') if isinstance(verdict, dict) else None
        if type(code) is not int:
            raise ValueError('invalid-verdict')
        if code in (1004, 2049):
            raise RejectedKey('key-rejected')
        if code != 0:
            raise ValueError('provider-error')
        models = data.get('model_remains')
        if not isinstance(models, list) or len(models) > 64 or any(not isinstance(m, dict) for m in models):
            raise ValueError('invalid-models')
        model = next((m for m in models if m.get('model_name') == 'general'), models[0] if len(models) == 1 else None)
        if model is not None:
            for key, label, reset in (('current_interval_remaining_percent', '5h', 'end_time'), ('current_weekly_remaining_percent', 'weekly', 'weekly_end_time')):
                remaining = model.get(key)
                if remaining is not None:
                    remaining = _number(remaining)
                    if not 0 <= remaining <= 100:
                        raise ValueError('invalid-percentage')
                    windows.append(_window(key, label, 100 - remaining, model.get(reset), now))
    elif provider == 'zai':
        if data.get('success') is not True or not isinstance(data.get('data'), dict):
            raise ValueError('provider-error')
        limits = data['data'].get('limits')
        if not isinstance(limits, list) or len(limits) > 64:
            raise ValueError('invalid-limits')
        for index, limit in enumerate(limits):
            if not isinstance(limit, dict):
                raise ValueError('invalid-limit')
            kind = limit.get('type')
            if not isinstance(kind, str) or not re.fullmatch(r'[A-Z_]{1,64}', kind):
                raise ValueError('invalid-limit-type')
            seconds = {3: 3600, 6: 604800, 5: 2592000}.get(limit.get('unit')) if type(limit.get('unit')) is int else None
            number = limit.get('number')
            seconds = seconds * number if seconds and type(number) is int and 0 < number <= 10000 else None
            label = 'mcp tools' if kind == 'TIME_LIMIT' else (str(seconds // 3600) + 'h' if seconds < 86400 else str(seconds // 86400) + 'd') if seconds else kind.lower().replace('_', ' ')
            used, remaining = limit.get('currentValue'), limit.get('remaining')
            total = _number(used) + _number(remaining) if used is not None and remaining is not None else limit.get('usage')
            windows.append(_window('limit-' + str(index), label, limit.get('percentage'), limit.get('nextResetTime'), now, used, total))
    else:
        raise ValueError('unsupported-provider')
    return {'windows': windows, 'balances': balances, 'details': details, 'provider_available': available}


def fetch(private, request, now):
    host = target(private)
    if host not in ORIGINS:
        return {'auth_status': 'api-key-configured', 'fetch_status': 'Unsupported', 'quota_scope': 'unsupported',
                'quota_reason': 'alibaba-console-unsupported' if host in ALIBABA else 'unknown-api-provider'}
    key = credential(private)
    if key is None:
        return {'auth_status': 'missing-api-key', 'fetch_status': 'NoAuth', 'quota_scope': 'api-key'}
    provider, path = ORIGINS[host]
    data = request('https://' + host + path, {'Authorization': 'Bearer ' + key, 'Accept': 'application/json', 'User-Agent': 'farshell-profile-usage'})
    return {**normalize(provider, data, now), 'auth_ok': True, 'auth_status': 'ok', 'fetch_status': 'Fresh',
            'quota_scope': 'api-key', 'api_provider': provider}
