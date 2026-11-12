"""Hotswap H2 — usage from the official statusline input and from Codex's app-server.

Disposable HOME/state, fake profile folders and fake credential strings. The Codex source is only ever
exercised against a FAKE `codex` executable placed first on PATH: the real CLI is never started.
"""
import json
import os
import stat
import sys
import time
import types

import pytest

import agent_profile_runtime
import agent_profiles as profiles
import session_profile
from usage import appserver, statusline
from usage import profiles as usage_profiles

FAKE_CREDENTIALS = {'claudeAiOauth': {'accessToken': 'fake-access-' + 'a' * 30, 'refreshToken': 'fake-refresh', 'expiresAt': 4102444800000}}
FAKE_CODEX = {'tokens': {'access_token': 'fake-codex-' + 'b' * 30, 'refresh_token': 'fake-refresh', 'account_id': 'acct-1'}}
FUTURE = 4102444800  # 2100-01-01


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    for key, value in {'HOME': home, 'VT_STATE_DIR': home / '.vt-test', 'VT_RUN_DIR': home / 'run', 'VT_MCP_HOME': home,
                       'VT_CONFIG': home / 'isolated.env', 'VT_WORKSPACE_PATH': home / 'workspace.json',
                       'VT_TUNNEL_PROVIDER': 'none'}.items():
        monkeypatch.setenv(key, str(value))
    for key in ('VT_TOKEN', 'VT_AUTH_TOKEN', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'VT_CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(usage_profiles, 'sys', types.SimpleNamespace(platform='linux'))   # never the real keychain
    yield home
    usage_profiles._instances.clear()


def _profile(name, tool='claude', login=True):
    pid = profiles.create(name, tool)['id']
    if login:
        path = profiles.root(pid) / ('.credentials.json' if tool == 'claude' else 'auth.json')
        path.write_text(json.dumps(FAKE_CREDENTIALS if tool == 'claude' else FAKE_CODEX))
        path.chmod(0o600)
    return pid


def _statusline_file(pid, limits, mtime=None):
    folder = statusline.directory()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    path = folder / f'{pid}.json'
    path.write_text(limits if isinstance(limits, str) else json.dumps({'cwd': '/work', 'transcript_path': '/t', 'rate_limits': limits}))
    path.chmod(0o600)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


# --- statusline reader ------------------------------------------------------------------------

def test_statusline_windows_are_read_with_reset_and_the_file_time(sandbox):
    pid = _profile('alice')
    now = time.time()
    _statusline_file(pid, {'five_hour': {'used_percentage': 41.5, 'resets_at': now + 3600},
                           'seven_day': {'used_percentage': 7, 'resets_at': now + 86400}}, mtime=now - 120)
    value = statusline.read(pid, now)
    assert [w['id'] for w in value['windows']] == ['five_hour', 'seven_day']
    five = value['windows'][0]
    assert five['pct'] == 41.5 and five['label'] == '5h' and 3590 <= five['resets_in_sec'] <= 3600 and five['source'] == 'statusline'
    assert abs(value['updated_at'] - (now - 120)) < 1 and value['source'] == 'statusline'
    blob = json.dumps(value)
    assert '/work' not in blob and '/t' not in blob   # only the two windows leave the module


def test_a_window_whose_reset_has_passed_is_dropped_and_none_left_means_no_value(sandbox):
    pid = _profile('alice')
    now = time.time()
    _statusline_file(pid, {'five_hour': {'used_percentage': 90, 'resets_at': now - 5}, 'seven_day': {'used_percentage': 30, 'resets_at': now + 100}})
    assert [w['id'] for w in statusline.read(pid, now)['windows']] == ['seven_day']
    _statusline_file(pid, {'five_hour': {'used_percentage': 90, 'resets_at': now - 5}})
    assert statusline.read(pid, now) is None


@pytest.mark.parametrize('text', ['{not json', '[]', '{}', '{"rate_limits": 5}', '{"rate_limits": {"five_hour": {"used_percentage": "50"}}}',
                                  '{"rate_limits": {"five_hour": {"used_percentage": 101}}}', '{"rate_limits": {"five_hour": {"used_percentage": -1}}}',
                                  '{"rate_limits": {"five_hour": {"used_percentage": true}}}', '{"rate_limits": {"five_hour": {"used_percentage": NaN}}}'])
def test_broken_or_invalid_files_mean_no_value_never_zero(sandbox, text):
    pid = _profile('alice')
    _statusline_file(pid, text)
    assert statusline.read(pid, time.time()) is None


def test_missing_oversized_symlinked_and_foreign_files_are_not_read(sandbox, tmp_path):
    pid = _profile('alice')
    assert statusline.read(pid, time.time()) is None
    now = time.time()
    good = {'five_hour': {'used_percentage': 5, 'resets_at': now + 100}}
    path = _statusline_file(pid, good)
    assert statusline.read(pid, now)
    path.write_text(json.dumps({'pad': 'x' * (statusline.MAX_BYTES + 10), 'rate_limits': good}))
    assert statusline.read(pid, now) is None
    path.unlink()
    target = tmp_path / 'elsewhere.json'
    target.write_text(json.dumps({'rate_limits': good}))
    path.symlink_to(target)
    assert statusline.read(pid, now) is None


# --- merging ----------------------------------------------------------------------------------

def _provider_row(pid, **extra):
    now = time.time()
    return {'name': 'alice', 'profile_id': pid, 'provider': 'claude', 'quota_scope': 'account', 'checked_at': now - 900, 'stale': False,
            'fetch_status': 'Fresh', 'windows': [{'id': 'five_hour', 'label': '5h', 'pct': 10.0, 'resets_at': None, 'resets_in_sec': None},
                                                 {'id': 'seven_day_opus', 'label': 'Opus weekly', 'pct': 3.0, 'resets_at': None, 'resets_in_sec': None}], **extra}


def test_statusline_wins_per_window_and_the_rest_keeps_its_provider_source(sandbox):
    pid = _profile('alice')
    now = time.time()
    _statusline_file(pid, {'five_hour': {'used_percentage': 55, 'resets_at': now + 600}}, mtime=now - 60)
    row = usage_profiles.overlay(_provider_row(pid), now)
    by = {w['id']: w for w in row['windows']}
    assert by['five_hour']['pct'] == 55 and by['five_hour']['source'] == 'statusline'
    assert by['seven_day_opus']['pct'] == 3.0 and by['seven_day_opus']['source'] == 'oauth-usage'
    assert row['source'] == 'statusline' and abs(row['checked_at'] - (now - 60)) < 1 and row['stale'] is False


def test_without_a_statusline_value_the_provider_value_stands_and_names_its_source(sandbox):
    pid = _profile('alice')
    row = usage_profiles.overlay(_provider_row(pid, stale=True), time.time())
    assert row['source'] == 'oauth-usage' and row['stale'] is True and row['windows'][0]['pct'] == 10.0
    assert all(w['source'] == 'oauth-usage' for w in row['windows'])


def test_a_long_lived_token_profile_gets_windows_from_the_statusline_only(sandbox):
    import agent_profile_token
    pid = _profile('token', login=False)
    agent_profile_token.set_token(pid, 'sk-ant-oat01-' + 'Zq9x' * 14, expires_at=None)
    now = time.time()
    _statusline_file(pid, {'seven_day': {'used_percentage': 62, 'resets_at': now + 5000}})
    rows = {r['profile_id']: r for r in usage_profiles.snapshot()}
    row = rows[pid]
    assert row['fetch_status'] == 'Fresh' and row['source'] == 'statusline' and row['windows'][0]['pct'] == 62
    assert 'sk-ant-oat01' not in json.dumps(row)


def test_a_profile_whose_folder_is_gone_has_no_card_and_does_not_break_the_list(sandbox):
    import shutil
    gone = _profile('gone')
    kept = _profile('kept')
    shutil.rmtree(profiles.root(gone))
    rows = usage_profiles.snapshot()
    assert [r['profile_id'] for r in rows] == [kept]


def test_native_mode_usage_snapshot_carries_the_statusline_source(sandbox, monkeypatch):
    import usage
    monkeypatch.setenv('VT_USAGE_PROVIDER', 'native')
    usage._reset_for_tests()
    pid = _profile('alice')
    monkeypatch.setattr(usage_profiles, '_request', lambda url, headers: {'five_hour': {'utilization': 10, 'resets_at': '2100-01-01T00:00:00Z'}}
                        if 'usage' in url else {'account': {'uuid': 'u'}, 'organization': {'uuid': 'o'}})
    _statusline_file(pid, {'five_hour': {'used_percentage': 77, 'resets_at': time.time() + 600}})
    merged = usage.merged_snapshot()
    card = next(p for p in merged['profiles'] if p.get('profile_id') == pid)
    assert merged['provider'] == 'native' and card['source'] == 'statusline' and card['windows'][0]['pct'] == 77
    usage._reset_for_tests()


def test_picker_shows_the_statusline_value_source_and_time_without_any_provider_call(sandbox):
    pid = _profile('alice')
    now = time.time()
    _statusline_file(pid, {'five_hour': {'used_percentage': 33, 'resets_at': now + 900}}, mtime=now - 300)
    row = session_profile.picker()['profiles'][0]
    assert row['usage']['source'] == 'statusline' and abs(row['usage']['checked_at'] - (now - 300)) < 1
    assert row['usage']['windows'][0]['pct'] == 33 and 800 < row['usage']['windows'][0]['resets_in_sec'] <= 900
    assert row['usage']['stale'] is False
    blob = json.dumps(row)
    assert '/work' not in blob and 'transcript' not in blob


# --- Codex app-server -------------------------------------------------------------------------

FAKE_CODEX_SCRIPT = '''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip()
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "home": os.environ.get("CODEX_HOME"), "pid": os.getpid(),
                          "leak": [k for k in os.environ if k.startswith(("OPENAI", "ANTHROPIC"))]}}) + "\\n")
if mode == "old":
    sys.stderr.write("unknown subcommand\\n")
    sys.exit(2)
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        home = os.environ["CODEX_HOME"] if mode != "wrong-home" else "/definitely/not/it"
        if mode == "no-home":
            send({{"id": msg["id"], "result": {{"userAgent": "fake"}}}})
        else:
            send({{"id": msg["id"], "result": {{"codexHome": home, "userAgent": "fake"}}}})
    elif msg.get("method") == "account/rateLimits/read":
        if mode == "hang":
            time.sleep(300)
        elif mode == "unsupported":
            send({{"id": msg["id"], "error": {{"code": -32601, "message": "Method not found"}}}})
        elif mode == "error":
            send({{"id": msg["id"], "error": {{"code": -32000, "message": "not logged in"}}}})
        else:
            send({{"method": "account/rateLimits/updated", "params": {{}}}})
            send({{"id": msg["id"], "result": {{"rateLimits": {{
                "primary": {{"usedPercent": 34, "windowDurationMins": 300, "resetsAt": {five}}},
                "secondary": {{"usedPercent": 8, "windowDurationMins": 10080, "resetsAt": {week}}}}}}}}})
        break
'''


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    folder = tmp_path / 'fakebin'
    folder.mkdir()
    script = folder / 'codex'
    script.write_text(FAKE_CODEX_SCRIPT.format(python=sys.executable, five=int(time.time()) + 7200, week=int(time.time()) + 400000))
    script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{folder}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('VT_USAGE_APPSERVER', '1')
    monkeypatch.setattr(agent_profile_runtime, 'require_inactive', lambda pid: None)
    appserver._skipped.clear()

    class Fake:
        def mode(self, value):
            (folder / 'mode').write_text(value)

        def calls(self):
            log = folder / 'calls.log'
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    fake = Fake()
    fake.mode('ok')
    yield fake
    # 백그라운드 갱신(refresh_async)이 끝날 때까지 기다린다 — 남은 스레드가 다음 테스트의 HOME(환경변수는 전역)에
    # 프로필 폴더를 같이 만들다 FileExistsError로 부딪혔다.
    deadline = time.monotonic() + 10
    while appserver._running and time.monotonic() < deadline:
        time.sleep(0.05)


def test_app_server_result_is_stored_and_read_with_codex_window_names(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    now = time.time()
    assert appserver.refresh(profiles.get(pid), now) == 'ok'
    call = fake_codex.calls()[0]
    assert call['argv'] == ['app-server'] and call['home'] == str(profiles.root(pid)) and call['leak'] == []
    value = appserver.read(pid, now)
    ids = {w['id']: w for w in value['windows']}
    assert set(ids) == {'session', 'weekly'} and ids['session']['pct'] == 34 and ids['session']['label'] == '5h'
    assert ids['weekly']['pct'] == 8 and ids['session']['resets_in_sec'] > 7000 and value['source'] == 'app-server'
    stored = appserver._file(pid)
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600 and stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    row = session_profile.picker()['profiles'][0]
    assert row['usage']['source'] == 'app-server' and row['usage']['windows'][0]['pct'] == 34


def test_windows_past_their_reset_are_not_shown_from_the_stored_value(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    now = time.time()
    appserver.refresh(profiles.get(pid), now)
    assert appserver.read(pid, now + 8000)['windows'][0]['id'] == 'weekly'
    assert appserver.read(pid, now + 500000) is None


def test_at_most_one_attempt_per_interval_and_failure_keeps_the_last_value(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    profile = profiles.get(pid)
    now = time.time()
    assert appserver.refresh(profile, now) == 'ok'
    assert appserver.refresh(profile, now + appserver.INTERVAL - 1) == 'not-due'
    assert len(fake_codex.calls()) == 1
    fake_codex.mode('error')
    assert appserver.refresh(profile, now + appserver.INTERVAL + 1) == 'failed'
    assert appserver.read(pid, now + appserver.INTERVAL + 2)['windows'][0]['pct'] == 34   # last good value stays
    assert appserver.refresh(profile, now + appserver.INTERVAL + 5) == 'not-due'          # a failure also waits the interval
    assert len(fake_codex.calls()) == 2


def test_an_unsupported_cli_is_remembered_and_retried_rarely(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    profile = profiles.get(pid)
    now = time.time()
    fake_codex.mode('unsupported')
    assert appserver.refresh(profile, now) == 'unsupported'
    assert appserver.read(pid, now) is None
    assert appserver.refresh(profile, now + appserver.INTERVAL + 1) == 'not-due'
    assert appserver.refresh(profile, now + appserver.UNSUPPORTED_INTERVAL + 1) == 'unsupported'
    assert len(fake_codex.calls()) == 2
    fake_codex.mode('old')   # no app-server command at all (usage error)
    assert appserver.refresh(profile, now + 3 * appserver.UNSUPPORTED_INTERVAL) == 'unsupported'


def test_a_codex_session_on_that_home_means_nothing_is_started(sandbox, fake_codex, monkeypatch):
    pid = _profile('personal', 'codex')
    def busy(profile_id):
        raise profiles.Refused('in use')
    monkeypatch.setattr(agent_profile_runtime, 'require_inactive', busy)
    assert appserver.refresh(profiles.get(pid), time.time()) == 'busy'
    assert appserver.refresh(profiles.get(pid), time.time()) == 'busy'
    assert fake_codex.calls() == [] and appserver.read(pid, time.time()) is None


def test_no_login_file_or_no_cli_or_disabled_starts_nothing(sandbox, fake_codex, monkeypatch):
    blank = _profile('blank', 'codex', login=False)
    assert appserver.refresh(profiles.get(blank), time.time()) == 'no-login'
    pid = _profile('personal', 'codex')
    monkeypatch.setenv('VT_USAGE_APPSERVER', '0')
    assert appserver.refresh(profiles.get(pid), time.time()) == 'disabled'
    monkeypatch.setenv('VT_USAGE_APPSERVER', '1')
    monkeypatch.setenv('PATH', '/nonexistent')
    assert appserver.refresh(profiles.get(pid), time.time()) == 'no-cli'
    assert fake_codex.calls() == []
    claude = _profile('claude-one')
    assert appserver.refresh(profiles.get(claude), time.time()) == 'disabled'


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_hanging_app_server_is_cut_off_at_the_deadline_and_the_process_is_gone(sandbox, fake_codex, monkeypatch):
    pid = _profile('personal', 'codex')
    monkeypatch.setattr(appserver, 'TIMEOUT', 1.5)
    fake_codex.mode('hang')
    started = time.monotonic()
    assert appserver.refresh(profiles.get(pid), time.time()) == 'failed'
    assert time.monotonic() - started < 8
    child = fake_codex.calls()[0]['pid']
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)


def test_an_app_server_that_reports_another_home_is_never_trusted(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    fake_codex.mode('wrong-home')
    assert appserver.refresh(profiles.get(pid), time.time()) == 'failed'
    assert appserver.read(pid, time.time()) is None


def test_usage_poll_starts_the_refresh_in_the_background_and_the_next_read_has_the_value(sandbox, fake_codex, monkeypatch):
    pid = _profile('personal', 'codex')
    monkeypatch.setattr(usage_profiles.NativeProfileProvider, 'snapshot',
                        lambda self: {'name': 'personal', 'profile_id': self.id, 'provider': 'codex', 'quota_scope': 'account', 'windows': [], 'account_id': 'a', 'identity_state': 'unverified',
                                      'checked_at': time.time(), 'stale': False, 'fetch_status': 'Unsupported'})
    usage_profiles.snapshot()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and appserver.read(pid, time.time()) is None:
        time.sleep(0.1)
    row = next(r for r in usage_profiles.snapshot() if r['profile_id'] == pid)
    assert row['source'] == 'app-server' and row['windows'][0]['pct'] == 34


# --- independent security review (hotswap-review) -------------------------------------------

def test_an_app_server_that_does_not_say_which_home_it_serves_is_never_trusted(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    fake_codex.mode('no-home')
    assert appserver.refresh(profiles.get(pid), time.time()) == 'failed'
    assert appserver.read(pid, time.time()) is None
    fake_codex.mode('ok')
    appserver._skipped.clear()
    assert appserver.refresh(profiles.get(pid), time.time() + 10_000) == 'ok'     # the same fake with a home still works


def test_the_app_server_is_not_started_while_a_session_is_being_created_for_the_home(sandbox, fake_codex):
    pid = _profile('personal', 'codex')
    assert appserver.hold(pid, 0)                   # session creation owns the guard
    try:
        assert appserver.refresh(profiles.get(pid), time.time()) == 'busy'
        assert fake_codex.calls() == []             # no process was started
    finally:
        appserver.release(pid)
    assert appserver.refresh(profiles.get(pid), time.time()) == 'ok'


def test_a_session_creation_waits_for_a_running_fake_app_server(sandbox, fake_codex, monkeypatch):
    """Both directions with a real (fake) process: the app-server holds the guard while it runs."""
    import threading
    pid = _profile('personal', 'codex')
    monkeypatch.setattr(appserver, 'TIMEOUT', 3.0)
    fake_codex.mode('hang')
    runner = threading.Thread(target=lambda: appserver.refresh(profiles.get(pid), time.time()))
    runner.start()
    deadline = time.monotonic() + 5
    while not fake_codex.calls() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake_codex.calls()                        # the app-server process is up
    assert appserver.hold(pid, 0) is False           # so a session creation cannot take the home right now
    runner.join(10)
    assert appserver.hold(pid, 0) is True            # and can once it is over
    appserver.release(pid)


def _drop_in(path, data, mode):
    path.write_text(data)
    path.chmod(mode)


@pytest.mark.parametrize('reader', ['statusline', 'appserver'])
def test_usage_files_are_read_only_when_private_and_not_linked(sandbox, tmp_path, reader):
    pid = _profile('alice', 'claude' if reader == 'statusline' else 'codex')
    now = time.time()
    if reader == 'statusline':
        document = json.dumps({'rate_limits': {'five_hour': {'used_percentage': 5, 'resets_at': now + 100}}})
        target, read = statusline.path(pid), lambda: statusline.read(pid, now)
    else:
        document = json.dumps({'status': 'ok', 'updated_at': now, 'windows': [{'id': 'session', 'label': '5h', 'pct': 5, 'resets_at_epoch': now + 100}]})
        target, read = appserver._file(pid), lambda: appserver.read(pid, now)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    _drop_in(target, document, 0o600)
    assert read()                                                 # baseline: a private regular file is read
    target.chmod(0o640)                                           # group-readable: someone else could have written it
    assert read() is None
    target.chmod(0o666)
    assert read() is None
    target.chmod(0o400)                                           # "0600 or tighter" is fine
    assert read()
    target.chmod(0o600)
    target.parent.chmod(0o755)                                    # the folder is open to others: they could swap the file
    assert read() is None
    target.parent.chmod(0o700)
    assert read()
    target.unlink()
    elsewhere = tmp_path / 'elsewhere.json'
    _drop_in(elsewhere, document, 0o600)
    target.symlink_to(elsewhere)                                  # a link is never followed (O_NOFOLLOW, no check-then-open race)
    assert read() is None
    target.unlink()
    os.mkfifo(target)                                             # a FIFO must neither be read nor hang the reader
    started = time.monotonic()
    assert read() is None and time.monotonic() - started < 2


def test_the_stored_appserver_folder_is_closed_when_it_already_existed_wide_open(sandbox):
    pid = _profile('personal', 'codex')
    appserver._file(pid).parent.mkdir(parents=True, exist_ok=True)
    appserver._file(pid).parent.chmod(0o755)
    appserver._save(pid, {'status': 'ok', 'windows': []})
    assert stat.S_IMODE(appserver._file(pid).parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(appserver._file(pid).stat().st_mode) == 0o600
