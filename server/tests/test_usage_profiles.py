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
