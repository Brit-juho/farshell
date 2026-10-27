"""Native usage boundary: fixed endpoints, no refresh, bounded/redacted output."""
import json
import time
from urllib.error import HTTPError, URLError

import pytest

import agent_profiles as registry
from usage import profiles as usage


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(home / '.vt-test'))
    monkeypatch.setattr(usage.sys, 'platform', 'linux')
    row = registry.create('development', 'claude')
    credentials = registry.root(row['id']) / '.credentials.json'
    credentials.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'private-sentinel', 'refreshToken': 'refresh-sentinel', 'expiresAt': (time.time() + 3600) * 1000}}))
    monkeypatch.setattr(usage, '_request', lambda url, headers: {'account': {'uuid': 'subject-sample'}, 'organization': {'uuid': 'tenant-sample'}} if url.endswith('/profile') else {'five_hour': {'utilization': 25, 'resets_at': '2030-01-01T00:00:00Z'}, 'unknown_secret': 'do-not-export'})
    return row, credentials


def test_native_usage_redacts_tokens_unknown_fields_and_deduplicates_account(profile, monkeypatch):
    row, credentials = profile
    second = registry.create('review', 'claude', account_id=row['account_id'])
    (registry.root(second['id']) / '.credentials.json').write_bytes(credentials.read_bytes())
    result = usage.snapshot()
    assert len(result) == 1
    assert len(result[0]['profile_ids']) == 2
    assert result[0]['windows'][0]['pct'] == 25
    assert result[0]['identity_state'] == 'provider'
    text = json.dumps(result)
    assert 'private-sentinel' not in text and 'refresh-sentinel' not in text and 'do-not-export' not in text


def test_cache_failures_and_token_change_invalidates_identity(profile, monkeypatch):
    row, credentials = profile
    provider = usage.NativeProfileProvider(row['id'])
    calls = []
    def fail(url, headers):
        calls.append(url)
        raise HTTPError(url, 401, 'unauthorized', {}, None)
    monkeypatch.setattr(usage, '_request', fail)
    assert provider.snapshot()['auth_status'] == 'expired'
    provider.snapshot()
    assert len(calls) == 1
    value = json.loads(credentials.read_text())
    value['claudeAiOauth']['accessToken'] = 'different-private-token'
    credentials.write_text(json.dumps(value))
    provider.snapshot()
    assert len(calls) == 2


def test_transient_error_preserves_stale_windows_without_claiming_auth(profile, monkeypatch):
    row, _ = profile
    provider = usage.NativeProfileProvider(row['id'])
    good = provider.snapshot()
    assert good['auth_ok']
    provider.deadline = 0
    def fail(*args):
        raise URLError('offline')
    monkeypatch.setattr(usage, '_request', fail)
    result = provider.snapshot()
    assert result['stale'] and not result['auth_ok']
    assert result['windows'] == good['windows']


@pytest.mark.parametrize('value', [-1, 101, True, '20', float('nan'), float('inf')])
def test_invalid_quota_never_becomes_zero(value):
    assert usage._window({'utilization': value}, 'five_hour', '5h', time.time()) is None


def test_redirects_are_never_followed():
    assert usage._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://sample.invalid/') is None


def test_native_quota_failure_is_not_a_license_to_use_clauth(profile, monkeypatch):
    row, credentials = profile
    credentials.unlink()
    result = usage.NativeProfileProvider(row['id']).snapshot()
    assert result['auth_status'] == 'missing'
    assert result['windows'] == []


def test_same_label_is_not_provider_identity(profile, monkeypatch):
    row, credentials = profile
    other = registry.create('development', 'claude', account_id=row['account_id'])
    # A missing/unproven identity must remain its own card.
    result = usage.snapshot()
    assert len(result) == 2
    assert next(r for r in result if r['profile_id'] == other['id'])['identity_state'] == 'unverified'


def test_api_profile_does_not_query_stale_oauth_credentials(profile, monkeypatch):
    row, _ = profile
    root = registry.root(row['id'])
    path = root / '.farshell-provider.json'
    path.write_text(json.dumps({'version': 1, 'api_key': 'sample-key', 'env': {}}))
    path.chmod(0o600)
    def forbidden(*args):
        raise AssertionError('API-key profiles must not use an OAuth reader')
    monkeypatch.setattr(usage, '_read_credentials', forbidden)
    result = usage.NativeProfileProvider(row['id']).snapshot()
    assert result['auth_status'] == 'api-key-configured'
    assert result['fetch_status'] == 'Unsupported'
    assert not result['windows']


def test_endpoint_only_preserves_native_oauth_and_binds_configuration(profile):
    row, _ = profile
    from agent_profile_runtime import authentication_fingerprint
    path = registry.root(row['id']) / '.farshell-provider.json'
    original = authentication_fingerprint(row['id'])
    path.write_text(json.dumps({'version': 1, 'env': {'ANTHROPIC_BASE_URL': 'https://proxy.example.invalid'}}))
    path.chmod(0o600)
    reader = usage.NativeProfileProvider(row['id'])
    assert reader.snapshot()['auth_ok']
    assert authentication_fingerprint(row['id']) != original
    with registry.database() as db:
        observation = db.execute('SELECT * FROM observations WHERE profile_id=?', (row['id'],)).fetchone()
    assert observation['last_fingerprint'] == authentication_fingerprint(row['id'])
    assert not observation['rotation_after_expiry']


def test_invalid_explicit_api_key_never_falls_back_to_oauth(profile, monkeypatch):
    row, _ = profile
    path = registry.root(row['id']) / '.farshell-provider.json'
    path.write_text(json.dumps({'version': 1, 'api_key': '', 'env': {}}))
    path.chmod(0o600)
    monkeypatch.setattr(usage, '_read_credentials', lambda *a: pytest.fail('OAuth fallback'))
    assert not usage.NativeProfileProvider(row['id']).snapshot()['auth_ok']
    from agent_profile_runtime import authentication_fingerprint
    assert authentication_fingerprint(row['id']) is None


def test_known_api_quotas_use_only_exact_origin_and_cache_never_leaks_keys(profile, monkeypatch):
    from agent_profile_secret import FILENAME
    row, _ = profile
    path = registry.root(row['id']) / FILENAME
    path.write_text(json.dumps({'version': 1, 'api_key': 'api-private-sentinel', 'env': {'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic'}}))
    path.chmod(0o600)
    calls = []
    def request(url, headers):
        calls.append((url, headers))
        return {'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '4.5', 'granted_balance': '0', 'topped_up_balance': '4.5'}], 'unknown_secret': 'must-not-leak'}
    monkeypatch.setattr(usage, '_request', request)
    reader = usage.NativeProfileProvider(row['id'])
    for _ in range(2):
        result = reader.snapshot()
        assert result['balances'][0]['remaining'] == 4.5
        assert 'private-sentinel' not in json.dumps(result) and 'must-not-leak' not in json.dumps(result)
        assert not any(k.startswith('_') for k in result)
    assert len(calls) == 1 and calls[0][0] == 'https://api.deepseek.com/user/balance'
    assert calls[0][1]['Authorization'] == 'Bearer api-private-sentinel'
    path.write_text(json.dumps({'version': 1, 'api_key': 'new-api-private-sentinel', 'env': {'ANTHROPIC_BASE_URL': 'https://proxy.example.invalid/anthropic'}}))
    result = reader.snapshot()
    assert result['fetch_status'] == 'Unsupported' and len(calls) == 1
    assert not result.get('balances')


@pytest.mark.parametrize('endpoint', ['https://api.deepseek.com.evil.invalid/v1', 'https://api.deepseek.com:444/v1', 'http://api.deepseek.com/v1', 'https://api.minimaxi.com/anthropic', 'https://coding.dashscope.aliyuncs.com/v1'])
def test_unknown_regional_and_alibaba_endpoints_never_receive_keys(endpoint):
    from usage.api_profiles import fetch
    def forbidden(*args):
        pytest.fail('unsupported endpoint must not receive a request')
    result = fetch({'api_key': 'private-sentinel', 'env': {'ANTHROPIC_BASE_URL': endpoint}}, forbidden, time.time())
    assert result['fetch_status'] == 'Unsupported'


def test_api_quotas_preserve_negative_balance_and_reject_missing_verdict():
    from usage.api_profiles import normalize, RejectedKey
    assert normalize('openrouter', {'data': {'total_credits': 1, 'total_usage': 2}}, 0)['balances'][0]['remaining'] == -1
    for data in ({}, {'base_resp': {'status_code': False}}, {'base_resp': {'status_code': 1000}}):
        with pytest.raises(ValueError):
            normalize('minimax', data, 0)
    with pytest.raises(RejectedKey):
        normalize('minimax', {'base_resp': {'status_code': 2049}}, 0)
    result = normalize('minimax', {'base_resp': {'status_code': 0}, 'model_remains': [{'model_name': 'video', 'current_interval_remaining_percent': 99}, {'model_name': 'general', 'current_interval_remaining_percent': 75, 'current_weekly_remaining_percent': 20}]}, 0)
    assert [w['pct'] for w in result['windows']] == [25, 80]
    result = normalize('zai', {'success': True, 'data': {'limits': [{'type': 'TOKENS_LIMIT', 'unit': 3, 'number': 5, 'percentage': 25}, {'type': 'TIME_LIMIT', 'unit': 5, 'number': 1, 'percentage': 10}]}}, 0)
    assert [w['label'] for w in result['windows']] == ['5h', 'mcp tools']
    with pytest.raises(ValueError):
        normalize('zai', {'success': False, 'data': {'limits': []}}, 0)


def test_identical_api_key_quota_is_not_counted_as_two_pools(profile, monkeypatch):
    from agent_profile_secret import FILENAME
    row, _ = profile
    sibling = registry.create('review', 'claude', account_id=row['account_id'])
    for entry, model in ((row, 'first'), (sibling, 'second')):
        path = registry.root(entry['id']) / FILENAME
        path.write_text(json.dumps({'version': 1, 'api_key': 'shared-private-sentinel', 'env': {'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic', 'MODEL_ALIAS': model}}))
        path.chmod(0o600)
    monkeypatch.setattr(usage, '_request', lambda *args: {'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '10', 'granted_balance': '0', 'topped_up_balance': '10'}]})
    data = usage.snapshot()
    assert len(data) == 1 and set(data[0]['profile_ids']) == {row['id'], sibling['id']}
    assert data[0]['identity_state'] == 'unverified'
    assert 'shared-private-sentinel' not in json.dumps(data)


def test_api_number_overflow_is_not_published_as_infinite_quota():
    from usage.api_profiles import normalize
    with pytest.raises(ValueError):
        normalize('openrouter', {'data': {'total_credits': 1e308, 'total_usage': -1e308}}, 0)
    with pytest.raises(ValueError):
        normalize('openrouter', {'data': {'total_credits': 10**1000, 'total_usage': 0}}, 0)
