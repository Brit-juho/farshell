"""Hotswap H1 — account injection into NEW tmux sessions.

Everything runs against a sandbox registry (`VT_STATE_DIR`), fake profile folders with fake
credential strings, and an isolated tmux socket whose shell is a tiny script that dumps its
environment. No real CLI is started, no real keychain is read, no real account is used.
"""
import asyncio
import json
import os
import subprocess
import time
import types
import uuid
from pathlib import Path

import pytest

import agent_profile_local as local
import agent_profiles as profiles
import routes.tmux as tmux_route
import session_profile
import tmux_runner

FAKE_TOKEN = 'sk-ant-oat01-' + 'Zq9x' * 14
FAKE_CREDENTIALS = {'claudeAiOauth': {'accessToken': 'fake-access-' + 'a' * 30, 'refreshToken': 'fake-refresh', 'expiresAt': 4102444800000}}
FAKE_CODEX = {'tokens': {'access_token': 'fake-codex-' + 'b' * 30, 'refresh_token': 'fake-refresh', 'account_id': 'acct-1'}}
DANGEROUS = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL',
             'ANTHROPIC_CUSTOM_HEADERS', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID')


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    workspace = home / 'sample-repo'
    workspace.mkdir()
    for key, value in {'HOME': home, 'VT_STATE_DIR': home / '.vt-test', 'VT_RUN_DIR': home / 'run', 'VT_MCP_HOME': home,
                       'VT_BROWSE_ROOTS': workspace, 'VT_CONFIG': home / 'isolated.env',
                       'VT_WORKSPACE_PATH': home / 'workspace.json', 'VT_TUNNEL_PROVIDER': 'none'}.items():
        monkeypatch.setenv(key, str(value))
    for key in ('VT_TOKEN', 'VT_AUTH_TOKEN', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'VT_CODEX_HOME', *DANGEROUS):
        monkeypatch.delenv(key, raising=False)
    # Never ask the real keychain: pretend this is not macOS for the credential reader.
    import usage.profiles as usage_profiles
    monkeypatch.setattr(usage_profiles, 'sys', types.SimpleNamespace(platform='linux'))
    return home, workspace


def _login(pid, data=None):
    root = profiles.root(pid)
    tool = profiles.get(pid)['tool']
    path = root / ('.credentials.json' if tool == 'claude' else 'auth.json')
    path.write_text(json.dumps(data or (FAKE_CREDENTIALS if tool == 'claude' else FAKE_CODEX)))
    path.chmod(0o600)


def _token(pid, expires_at=None):
    import agent_profile_token
    agent_profile_token.set_token(pid, FAKE_TOKEN, expires_at=expires_at)


def _make(name, tool='claude', login=True):
    pid = profiles.create(name, tool)['id']
    if login:
        _login(pid)
    return pid


# --- state + slot validation -------------------------------------------------------------

def test_picker_reports_states_without_secrets_or_paths(sandbox):
    ok = _make('alice')
    token = _make('work')
    _token(token)
    missing = _make('blank', login=False)
    broken = _make('broken')
    (profiles.root(broken) / '.credentials.json').write_text('{not json')
    (profiles.root(broken) / '.credentials.json').chmod(0o600)
    expired = _make('old')
    _token(expired, expires_at=time.time() - 10)
    codex = _make('personal', 'codex')
    rows = {r['name']: r for r in session_profile.picker()['profiles']}
    assert (rows['alice']['auth'], rows['alice']['auth_kind'], rows['alice']['selectable']) == ('ok', 'login', True)
    assert (rows['work']['auth'], rows['work']['auth_kind'], rows['work']['selectable']) == ('ok', 'token', True)
    assert (rows['blank']['auth'], rows['blank']['selectable']) == ('missing', False)
    assert (rows['broken']['auth'], rows['broken']['selectable']) == ('broken', False)
    assert (rows['old']['auth'], rows['old']['selectable']) == ('broken', False)
    assert rows['personal']['auth'] == 'ok' and rows['personal']['tool'] == 'codex'
    blob = json.dumps(session_profile.picker())
    for secret in (FAKE_TOKEN, 'fake-access-', 'fake-codex-', str(sandbox[0]), '.vt-test'):
        assert secret not in blob
    assert ok and codex


def test_picker_is_empty_and_creates_nothing_without_a_registry(sandbox):
    assert session_profile.picker() == {'profiles': [], 'defaults': {}}
    assert session_profile.select('default') is None
    with pytest.raises(session_profile.SlotRefused) as error:
        session_profile.select('0' * 32)
    assert error.value.code == 'profile_not_found'
    assert not (sandbox[0] / '.vt-test' / 'agent-profiles').exists()


def test_picker_shows_only_cached_usage(sandbox):
    pid = _make('alice')
    assert session_profile.picker()['profiles'][0]['usage'] is None
    import usage.profiles as usage_profiles
    provider = usage_profiles.NativeProfileProvider(pid)
    provider.cache = {'windows': [{'id': 'five_hour', 'label': '5h', 'pct': 42.0, 'resets_at': '2099-01-01T00:00:00+00:00', 'resets_in_sec': 1}],
                      'checked_at': 123.0, 'stale': False, 'provider': 'claude', 'subject_hash': 'hidden'}
    usage_profiles._instances[(str(profiles.directory()), pid)] = provider
    try:
        usage = session_profile.picker()['profiles'][0]['usage']
    finally:
        usage_profiles._instances.clear()
    assert usage['source'] == 'oauth-usage' and usage['checked_at'] == 123.0
    assert usage['windows'][0]['pct'] == 42.0 and usage['windows'][0]['resets_in_sec'] > 1000
    assert 'hidden' not in json.dumps(usage)


@pytest.mark.parametrize('case, code', [
    ('archived', 'profile_archived'), ('folder', 'folder_missing'), ('login', 'not_logged_in'),
    ('expired', 'token_expired'), ('unreadable', 'credentials_unreadable'), ('api', 'unsupported_auth'),
])
def test_unusable_profile_is_refused_never_replaced(sandbox, case, code):
    pid = _make('alice', login=case not in ('login',))
    if case == 'archived':
        profile = profiles.get(pid)
        profiles.metadata_change(pid, profile['revision'], profile['metadata_revision'], 'archive', None)
    elif case == 'folder':
        import shutil
        shutil.rmtree(profiles.root(pid))
    elif case == 'expired':
        _token(pid, expires_at=time.time() - 10)
    elif case == 'unreadable':
        (profiles.root(pid) / '.credentials.json').write_text('[1')
    elif case == 'api':
        path = profiles.root(pid) / '.farshell-provider.json'
        path.write_text(json.dumps({'version': 1, 'env': {'ANTHROPIC_BASE_URL': 'https://example.invalid'}, 'api_key': 'fake-key'}))
        path.chmod(0o600)
    with pytest.raises(session_profile.SlotRefused) as error:
        session_profile.select(pid)
    assert error.value.code == code and error.value.status == 409


def test_default_is_explicit_only_and_a_broken_default_is_refused(sandbox):
    only = _make('alice')
    assert session_profile.select('default') is None       # one profile, none marked: nothing is guessed
    local.set_default_for('claude', only)
    assert session_profile.select('default').ids == [only]
    codex = _make('personal', 'codex')
    local.set_default_for('codex', codex)
    assert session_profile.select('default').ids == [only, codex]
    (profiles.root(codex) / 'auth.json').unlink()
    with pytest.raises(session_profile.SlotRefused) as error:
        session_profile.select('default')
    assert error.value.code == 'default_invalid'


def test_invalid_reference_values(sandbox):
    for bad in ('', 'nope', 'A' * 32, '../x', 'default ', 5, None):
        with pytest.raises(session_profile.SlotRefused) as error:
            session_profile.select(bad)
        assert error.value.status == 400


# --- default API -------------------------------------------------------------------------

def test_set_default_validates_and_clears(sandbox):
    a = _make('alice')
    c = _make('personal', 'codex')
    assert local.set_default_for('claude', a) == {'tool': 'claude', 'default': a}
    assert local.defaults() == {'claude': a}
    with pytest.raises(profiles.Refused):
        local.set_default_for('claude', c)           # tool mismatch
    with pytest.raises(profiles.Refused):
        local.set_default_for('claude', '1' * 32)    # unknown profile
    with pytest.raises(profiles.Refused):
        local.set_default_for('shell', a)
    local.set_default_for('claude', None)
    assert local.defaults() == {}
    # The CLI reads the same file.
    local.set_default_for('claude', a)
    assert local.set_default() == {'claude': 'alice'}
    assert (profiles.directory() / 'defaults.json').stat().st_mode & 0o777 == 0o600


def _call(route, body, kind='session', monkeypatch=None):
    import auth

    class Request:
        cookies = {}
        headers = {}
        query_params = {}

        async def body(self):
            return json.dumps(body).encode()

    monkeypatch.setattr(auth, 'request_kind', lambda request: kind)
    return Request()


def test_default_route_needs_a_human_session_and_has_no_elevation(sandbox, monkeypatch):
    import auth
    from starlette.exceptions import HTTPException
    import routes.agent_profiles as route
    a = _make('alice')
    for dependency in route.router.dependencies:
        assert dependency.dependency is auth.require_human_session
    default = next(r for r in route.router.routes if r.path == '/api/agent-profiles/default')
    assert not any(d.dependency is auth.require_elevated for d in default.dependencies)
    request = _call(route, {'tool': 'claude', 'profile_id': a}, 'session', monkeypatch)
    assert asyncio.run(route.set_default(request)) == {'tool': 'claude', 'default': a}
    for kind in ('machine', 'none'):
        monkeypatch.setattr(auth, 'request_kind', lambda request, kind=kind: kind)
        with pytest.raises(HTTPException) as error:
            auth.require_human_session(request)
        assert error.value.status_code == 403
    bad = asyncio.run(route.set_default(_call(route, {'tool': 'claude', 'profile_id': 'zz'}, monkeypatch=monkeypatch)))
    assert bad.status_code == 409
    extra = asyncio.run(route.set_default(_call(route, {'tool': 'claude', 'profile_id': a, 'x': 1}, monkeypatch=monkeypatch)))
    assert extra.status_code == 409


def test_picker_action_returns_the_light_list(sandbox, monkeypatch):
    import routes.agent_profiles as route
    _make('alice')
    request = _call(route, {}, monkeypatch=monkeypatch)
    result = asyncio.run(route.read('picker', request))
    assert [r['name'] for r in result['profiles']] == ['alice']
    assert asyncio.run(route.read('picker', _call(route, {'x': 1}, monkeypatch=monkeypatch))).status_code == 409


# --- the tmux session itself (isolated socket, shell = environment dumper) ----------------

@pytest.fixture
def tmux(sandbox, monkeypatch, tmp_path):
    version = subprocess.run(['tmux', '-V'], capture_output=True, text=True).stdout if tmux_runner.is_installed() else ''
    if not version or session_profile.tmux_version() is None or session_profile.tmux_version() < session_profile.MIN_TMUX:
        pytest.skip('tmux >= 3.2 required')
    socket = 'h1t-' + uuid.uuid4().hex[:8]
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_SOCKET', socket)
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_CONF', '/dev/null')
    shell = tmp_path / 'dump-shell.sh'
    shell.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exec /bin/sh -c "$2"; fi\n'
                     'env > "$DUMP_OUT.tmp" && mv "$DUMP_OUT.tmp" "$DUMP_OUT"\nsleep 60\n')
    shell.chmod(0o755)
    monkeypatch.setenv('SHELL', str(shell))
    out = tmp_path / 'env.out'
    monkeypatch.setenv('DUMP_OUT', str(out))
    argv_log = []
    real = tmux_runner.run

    def spy(args, timeout=2.0, input=None):
        argv_log.append(list(args))
        return real(args, timeout=timeout, input=input)
    monkeypatch.setattr(tmux_runner, 'run', spy)
    yield types.SimpleNamespace(socket=socket, out=out, argv=argv_log)
    subprocess.run(['tmux', '-L', socket, 'kill-server'], capture_output=True)


def _dump(tmux):
    deadline = time.time() + 8
    while time.time() < deadline:
        if tmux.out.exists():
            return dict(line.split('=', 1) for line in tmux.out.read_text().splitlines() if '=' in line)
        time.sleep(.05)
    raise AssertionError('the session shell never started')


def _spawn(name, selection, cwd='/tmp'):
    return session_profile.spawn(name, 80, 24, cwd, selection)


def test_session_gets_its_own_account_environment(sandbox, tmux):
    pid = _make('alice')
    selection = session_profile.select(pid)
    rc, reason, info = _spawn('s-login', selection)
    assert rc == 0, reason
    env = _dump(tmux)
    assert env['CLAUDE_CONFIG_DIR'] == str(profiles.root(pid))
    assert env['FSH_PROFILE_ID'] == pid
    assert 'CODEX_HOME' not in env
    out = subprocess.run(['tmux', '-L', tmux.socket, 'show-options', '-t', '=s-login:', '@fsh_profile'], capture_output=True, text=True).stdout
    assert pid in out
    # a long-lived token was not involved for a login profile
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in env


def test_codex_profile_sets_codex_home_only(sandbox, tmux):
    pid = _make('personal', 'codex')
    assert _spawn('s-codex', session_profile.select(pid))[0] == 0
    env = _dump(tmux)
    assert env['CODEX_HOME'] == str(profiles.root(pid)) and 'CLAUDE_CONFIG_DIR' not in env


def test_override_variables_are_really_unset_not_emptied(sandbox, tmux, monkeypatch):
    pid = _make('alice')
    # Present in tmux's GLOBAL environment only (the server was started with them) ...
    for name in DANGEROUS:
        monkeypatch.setenv(name, 'global-' + name)
    assert tmux_runner.run(['new-session', '-d', '-s', 'pre'])[0] == 0
    # ... and not in this process any more, so only `show-environment -g` can tell.
    for name in DANGEROUS:
        monkeypatch.delenv(name)
    rc, _, info = _spawn('s-clean', session_profile.select(pid))
    assert rc == 0
    env = _dump(tmux)
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS'):
        assert name not in env, name          # absent, not an empty value
    # OpenAI variables belong to Codex; a Claude account choice leaves them alone.
    assert env['OPENAI_API_KEY'] == 'global-OPENAI_API_KEY'
    assert set(info['cleared_env']) == {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_CUSTOM_HEADERS'}
    # New windows of that session do not get them back from the global environment either.
    shown = subprocess.run(['tmux', '-L', tmux.socket, 'show-environment', '-t', '=s-clean'], capture_output=True, text=True).stdout
    assert '-ANTHROPIC_API_KEY' in shown.splitlines()


def test_override_in_the_server_environment_is_removed_too(sandbox, tmux, monkeypatch):
    pid = _make('personal', 'codex')
    monkeypatch.setenv('OPENAI_API_KEY', 'from-server-env')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'untouched-for-codex')
    rc, _, info = _spawn('s-server', session_profile.select(pid))
    assert rc == 0
    env = _dump(tmux)
    assert 'OPENAI_API_KEY' not in env and env['ANTHROPIC_API_KEY'] == 'untouched-for-codex'
    assert info['cleared_env'] == ['OPENAI_API_KEY']


def test_long_lived_token_reaches_the_session_but_never_a_command_line_or_log(sandbox, tmux, monkeypatch, caplog):
    pid = _make('work', login=False)
    _token(pid)
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'ambient-token-value')   # an ambient one must lose
    caplog.set_level('DEBUG')
    rc, reason, _ = _spawn('s-token', session_profile.select(pid))
    assert rc == 0, reason
    env = _dump(tmux)
    assert env['CLAUDE_CODE_OAUTH_TOKEN'] == FAKE_TOKEN
    assert not any(FAKE_TOKEN in part for call in tmux.argv for part in call), 'token reached a tmux argv'
    assert FAKE_TOKEN not in caplog.text
    ps = subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True).stdout
    assert FAKE_TOKEN not in ps
    # the one-use config file is gone
    assert not list((profiles.directory() / 'launch').glob('session-*.conf'))


def test_two_defaults_inject_both_tools(sandbox, tmux):
    a, c = _make('alice'), _make('personal', 'codex')
    local.set_default_for('claude', a)
    local.set_default_for('codex', c)
    assert _spawn('s-both', session_profile.select('default'))[0] == 0
    env = _dump(tmux)
    assert env['CLAUDE_CONFIG_DIR'] == str(profiles.root(a)) and env['CODEX_HOME'] == str(profiles.root(c))
    assert env['FSH_PROFILE_ID'] == f'{a},{c}'


def test_a_failed_spawn_leaves_no_half_configured_session(sandbox, tmux):
    pid = _make('alice')
    assert tmux_runner.run(['new-session', '-d', '-s', 'taken'])[0] == 0
    rc, reason, _ = _spawn('taken', session_profile.select(pid))          # duplicate name
    assert rc != 0 and reason == ''
    assert tmux_runner.has_session('taken')   # the pre-existing session is not what we cleaned up ...
    shown = subprocess.run(['tmux', '-L', tmux.socket, 'show-options', '-t', '=taken:', '@fsh_profile'], capture_output=True, text=True).stdout
    assert pid not in shown                   # ... and it never got another account's marker


def test_hostile_values_cannot_break_the_config_line(sandbox, tmux, tmp_path):
    pid = _make('alice')
    selection = session_profile.select(pid)
    folder = tmp_path / 'dir with "quote" and $HOME and ; semi'
    folder.mkdir()
    rc, _, _ = _spawn('s-odd', selection, cwd=str(folder))
    assert rc == 0
    assert _dump(tmux)['PWD'] == str(folder)
    with pytest.raises(session_profile.SlotRefused):
        session_profile._quote('a\nnew-session -d')


# --- /api/tmux/create and /api/tmux/sessions -----------------------------------------------

class _Request:
    cookies = {}

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def create(sandbox, tmux, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'request_kind', lambda request: 'session')
    monkeypatch.setattr(auth, 'VT_AUTH_PASSWORD_HASH', 'x')

    async def attach(name, cols, rows, request=None, screen_id=None, catalog_binding=None):
        return {'id': 'web-1', 'name': name, 'tmux_session': name}
    monkeypatch.setattr(tmux_route, '_attach_tmux', attach)
    monkeypatch.setattr(tmux_route, '_kick_sessions', lambda: None)

    def call(body):
        return asyncio.run(tmux_route.create_tmux_session(_Request(body)))
    return call


def _body(response):
    return json.loads(response.body)


def test_create_without_profile_is_unchanged(create, tmux):
    result = create({'name': 'plain'})
    assert result['tmux_session'] == 'plain' and 'profile' not in result
    assert not any('source-file' in call for call in tmux.argv)
    assert subprocess.run(['tmux', '-L', tmux.socket, 'show-options', '-t', '=plain:', '@fsh_profile'], capture_output=True, text=True).stdout.strip() in ('', '@fsh_profile ""')


def test_create_with_profile_injects_and_records(create, tmux, sandbox):
    pid = _make('alice')
    result = create({'name': 'mine', 'profile': pid})
    assert result['profile'] == {'id': pid, 'name': 'alice', 'tool': 'claude'}
    assert _dump(tmux)['CLAUDE_CONFIG_DIR'] == str(profiles.root(pid))
    with profiles.database() as db:
        rows = db.execute('SELECT name, profile_id FROM injected_sessions').fetchall()
        assert [(r['name'], r['profile_id']) for r in rows] == [('mine', pid)]
        # The launch panel's table is not touched.
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0


def test_create_none_and_unset_default_inject_nothing(create, tmux, sandbox):
    _make('alice')
    for ref in ('none', 'default'):
        result = create({'name': 'x-' + ref, 'profile': ref})
        assert 'profile' not in result
    assert not any('source-file' in call for call in tmux.argv)


def test_create_refuses_instead_of_falling_back(create, tmux, sandbox):
    blank = _make('blank', login=False)
    cases = [({'profile': blank}, 409, 'not_logged_in'), ({'profile': '2' * 32}, 409, 'profile_not_found'),
             ({'profile': 'junk'}, 400, 'invalid_profile'), ({'profile': 7}, 400, 'invalid_profile')]
    for extra, status, code in cases:
        response = create({'name': 'nope', **extra})
        assert (response.status_code, _body(response)['error']) == (status, code)
    assert not tmux_runner.has_session('nope')


def test_create_with_profile_needs_a_human_session(create, tmux, sandbox, monkeypatch):
    import auth
    from starlette.exceptions import HTTPException
    pid = _make('alice')
    for kind in ('machine', 'none'):
        monkeypatch.setattr(auth, 'request_kind', lambda request, kind=kind: kind)
        with pytest.raises(HTTPException) as error:
            create({'name': 'agent', 'profile': pid})
        assert error.value.status_code == 403
        with pytest.raises(HTTPException):
            create({'name': 'agent', 'profile': 'none'})       # even "none": the field is a human choice
        assert create({'name': 'agent-plain-' + kind})['tmux_session'] == 'agent-plain-' + kind   # the old call still works
    assert not tmux_runner.has_session('agent')


def test_create_through_the_session_catalog_injects_too(create, tmux, sandbox):
    pid = _make('alice')
    opid = uuid.uuid4().hex
    body = {'name': 'cat', 'profile': pid, 'operation_id': opid, 'cwd': str(sandbox[1])}
    result = create(body)
    assert result['profile']['id'] == pid
    assert _dump(tmux)['CLAUDE_CONFIG_DIR'] == str(profiles.root(pid))
    # Replaying the same operation never spawns a second session; another profile for it conflicts.
    again = create(body)
    assert 'error' not in (again if isinstance(again, dict) else _body(again))
    other = _make('bob')
    conflict = create({**body, 'profile': other})
    assert _body(conflict)['error'] == 'idempotency_conflict'


def test_session_list_names_the_creation_account(sandbox, monkeypatch):
    pid = _make('alice')
    gone = '9' * 32
    seen = []

    async def fake(args, timeout=None):
        seen.append(list(args))
        if args[0] == 'list-sessions':
            return (f'a\t1\t0\t\tlocal\t\t\t$1\t1\t\t{pid}\n'
                    f'b\t1\t0\t\tlocal\t\t\t$2\t2\t\t\n'
                    f'c\t1\t0\t\tlocal\t\t\t$3\t3\t\t{gone}\n')
        return 'a\tzsh\t/x\t%1\nb\tzsh\t/y\t%2\nc\tzsh\t/z\t%3\n'
    monkeypatch.setattr(tmux_runner, 'run_text_async', fake)
    out = {s['name']: s for s in asyncio.run(tmux_route.collect_tmux_sessions())}
    assert out['a']['profile'] == {'id': pid, 'name': 'alice', 'tool': 'claude'}
    assert out['b']['profile'] is None and out['b']['profiles'] == []
    assert out['c']['profile'] == {'id': gone, 'name': None, 'tool': None}   # a name is never guessed
    assert len(seen) == 2


def test_session_list_never_touches_the_registry_without_tags(sandbox, monkeypatch):
    async def fake(args, timeout=None):
        return 'a\t1\t0\t\tlocal\t\t\t$1\t1\t\t\n' if args[0] == 'list-sessions' else 'a\tzsh\t/x\t%1\n'
    monkeypatch.setattr(tmux_runner, 'run_text_async', fake)
    assert asyncio.run(tmux_route.collect_tmux_sessions())[0]['profile'] is None
    assert not (sandbox[0] / '.vt-test' / 'agent-profiles').exists()
