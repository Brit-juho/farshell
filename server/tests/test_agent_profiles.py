"""Disposable HOME/state/native files; no production credentials or network."""
import json
import os
import time
from pathlib import Path

import pytest

import agent_profiles as profiles
import agent_profile_config as hooks
import agent_profile_migration as migration
import agent_profile_runtime as runtime
import fsguard


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    state = home / '.vt-test'
    workspace = home / 'sample-repo'
    workspace.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(state))
    monkeypatch.setenv('VT_RUN_DIR', str(home / 'run'))
    monkeypatch.setenv('VT_MCP_HOME', str(home))
    monkeypatch.setenv('VT_BROWSE_ROOTS', str(workspace))
    monkeypatch.setenv('VT_CONFIG', str(home / 'isolated.env'))
    monkeypatch.setenv('VT_WORKSPACE_PATH', str(home / 'workspace.json'))
    monkeypatch.setenv('VT_TMUX_SOCKET', 'profile-tests')
    monkeypatch.setenv('VT_TUNNEL_PROVIDER', 'none')
    for key in ('VT_TOKEN', 'VT_AUTH_TOKEN', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'VT_CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    return home, workspace


def test_same_account_distinct_native_roots_and_no_token_copy(sandbox):
    home, _ = sandbox
    first = profiles.create('development', 'claude')
    second = profiles.create('review', 'claude', account_id=first['account_id'])
    assert first['account_id'] == second['account_id']
    assert profiles.root(first['id']) != profiles.root(second['id'])
    for row in (first, second):
        root = profiles.root(row['id'])
        assert root.stat().st_mode & 0o777 == 0o700
        assert not (root / '.credentials.json').exists()
        assert (root / 'settings.json').stat().st_mode & 0o777 == 0o600
    assert (profiles.directory() / 'registry.sqlite3').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        profiles.create('other', 'codex', account_id=first['account_id'])


def test_source_identity_is_read_only_account_bound_and_invalidated(sandbox, monkeypatch):
    import agent_profile_identity as identity
    from usage import profiles as usage
    home, _ = sandbox
    root = home / '.clauth' / 'profiles' / 'oauth-test'
    root.mkdir(parents=True)
    (root / 'config.toml').write_text('')
    credential = root / 'credentials.json'
    raw = json.dumps({'claudeAiOauth': {'accessToken': 'source-private-sentinel'}}).encode()
    credential.write_bytes(raw)
    source = next(r for r in migration.source_list()['sources'] if r['kind'] == 'clauth')
    calls = []
    def request(url, headers):
        calls.append(url)
        assert headers['Authorization'] == 'Bearer source-private-sentinel'
        return {'account': {'uuid': 'sample-account'}, 'organization': {'uuid': 'sample-org'}}
    monkeypatch.setattr(usage, '_request', request)
    plan = identity.preview(source['id'])
    assert 'source-private-sentinel' not in json.dumps(plan)
    assert not calls
    result = identity.apply(plan['token'])
    assert calls == ['https://api.anthropic.com/api/oauth/profile']
    assert credential.read_bytes() == raw
    assert 'source-private-sentinel' not in json.dumps(result)
    subject = profiles.digest(json.dumps(['claude', 'sample-account', 'sample-org']).encode())
    observed = {'subject': subject, 'last_fingerprint': 'target-binding', 'last_at': time.time()}
    assert identity.matches(source['id'], observed, 'target-binding')
    assert not identity.matches(source['id'], {**observed, 'subject': 'other-account'}, 'target-binding')
    assert not identity.matches(source['id'], observed, 'other-binding')
    stale = identity.preview(source['id'])
    credential.write_bytes(raw + b' ')
    assert not identity.matches(source['id'], observed, 'target-binding')
    with pytest.raises(ValueError):
        identity.apply(stale['token'])
    assert len(calls) == 1


@pytest.mark.parametrize('tool', ['claude', 'codex'])
def test_observed_turn_requires_native_assistant_text_and_zoned_timestamp(tool):
    from agent_profile_turn import _assistant_time
    message = {'content': [{'type': 'text' if tool == 'claude' else 'output_text', 'text': 'sample response'}]}
    if tool == 'codex':
        message.update(type='message', role='assistant')
    row = {'type': 'assistant' if tool == 'claude' else 'response_item',
           'message' if tool == 'claude' else 'payload': message, 'timestamp': '2030-01-01T00:00:00Z'}
    assert _assistant_time(row, tool) is not None
    assert _assistant_time({**row, 'timestamp': '2030-01-01T00:00:00'}, tool) is None
    assert _assistant_time({**row, 'type': 'user'}, tool) is None
    message['content'][0]['text'] = '   '
    assert _assistant_time(row, tool) is None


def test_http_dto_hides_private_file_and_authentication_hashes():
    from routes.agent_profiles import _public_result
    original = {'files': [{'path': '.farshell-provider.json', 'sha256': 'private-hash', 'before_sha256': 'old-private'},
                          {'path': 'settings.json', 'sha256': 'public-config-hash'}],
                'manifest': {'auth_fingerprint': 'authentication-hash', 'status': 'verified'}}
    result = _public_result(original)
    assert result['files'][0] == {'path': '.farshell-provider.json', 'private': True}
    assert result['files'][1]['sha256'] == 'public-config-hash'
    assert result['manifest'] == {'status': 'verified'}
    assert original['files'][0]['sha256'] == 'private-hash'


@pytest.mark.parametrize('bad', ['', '../private', 'x'*33, None, True, []])
def test_profile_ids_cannot_nominate_paths(sandbox, bad):
    with pytest.raises(ValueError):
        profiles.get(bad)


def test_runtime_does_not_inherit_another_account_or_loader(sandbox, monkeypatch):
    row = profiles.create('work', 'codex')
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LD_PRELOAD', 'NODE_OPTIONS', 'OPENAI_BASE_URL'):
        monkeypatch.setenv(key, 'private-sentinel')
    env = runtime.environment(row['id'])
    assert 'private-sentinel' not in json.dumps(env)
    assert env['CODEX_HOME'] == str(profiles.root(row['id']))
    assert env['VT_CONFIG'] == str(sandbox[0] / 'isolated.env')
    assert os.environ['OPENAI_API_KEY'] == 'private-sentinel'


def test_links_hardlinks_special_files_and_large_files_refused(sandbox):
    home, _ = sandbox
    root = fsguard.private_profile_directory(home / 'private')
    target = home / 'secret'
    target.write_text('do-not-read')
    (root / 'link').symlink_to(target)
    with pytest.raises(fsguard.FsDenied):
        fsguard.read_profile_bytes(root, 'link')
    os.link(target, root / 'hardlink')
    with pytest.raises(fsguard.FsDenied):
        fsguard.read_profile_bytes(root, 'hardlink')
    os.mkfifo(root / 'fifo')
    with pytest.raises(fsguard.FsDenied):
        fsguard.read_profile_bytes(root, 'fifo')
    (root / 'large').write_bytes(b'x' * 20)
    with pytest.raises(fsguard.FsDenied):
        fsguard.read_profile_bytes(root, 'large', 10)
    with pytest.raises(fsguard.FsDenied):
        fsguard.profile_file(root, '../secret')


def _native(sandbox):
    home, _ = sandbox
    root = home / '.claude'
    root.mkdir()
    (root / 'settings.json').write_text(json.dumps({'model': 'sample-model', 'env': {'API_KEY': 'private-sentinel'}, 'apiKeyHelper': 'secret-helper', 'permissions': {'defaultMode': 'bypassPermissions'}}))
    (home / '.claude.json').write_text(json.dumps({'oauthAccount': {'email': 'private-sentinel'}, 'mcpServers': {'docs': {'command': 'docs-server', 'env': {'TOKEN': 'private-sentinel', 'REF': '${DOCS_KEY}'}}}}))
    package = root / 'skills' / 'sample'
    package.mkdir(parents=True)
    (package / 'SKILL.md').write_text('---\nname: sample\n---\nSample skill')
    source = next(r for r in migration.source_list()['sources'] if r['tool'] == 'claude')
    return root, source


def test_reviewed_config_import_excludes_auth_and_preserves_source(sandbox):
    root, source = _native(sandbox)
    row = profiles.create('new', 'claude')
    before = (root / 'settings.json').read_bytes()
    plan = migration.config_preview(row['id'], row['revision'], source['id'])
    assert 'private-sentinel' not in json.dumps(plan)
    result = migration.config_apply(plan['token'])
    assert result['status'] == 'ok'
    assert result['credentials_copied'] is False
    dest = profiles.root(row['id'])
    assert (dest / 'skills/sample/SKILL.md').exists()
    data = json.loads((dest / 'settings.json').read_text())
    assert 'permissions' not in data and 'apiKeyHelper' not in data and 'env' not in data
    mcp = (dest / '.claude.json').read_text()
    assert 'private-sentinel' not in mcp and '${DOCS_KEY}' in mcp
    assert (root / 'settings.json').read_bytes() == before
    with pytest.raises(ValueError):
        migration.config_apply(plan['token'])


def test_import_source_and_destination_revision_conflicts(sandbox):
    root, source = _native(sandbox)
    row = profiles.create('new', 'claude')
    plan = migration.config_preview(row['id'], row['revision'], source['id'])
    (root / 'settings.json').write_text('{"model":"changed"}')
    with pytest.raises(ValueError):
        migration.config_apply(plan['token'])
    plan = migration.config_preview(row['id'], row['revision'], source['id'])
    (profiles.root(row['id']) / 'settings.json').write_text('{"model":"user-edited"}')
    with pytest.raises(ValueError):
        migration.config_apply(plan['token'])


@pytest.mark.parametrize('tool', ['claude', 'codex'])
def test_hooks_are_profile_scoped_and_owned_hooks_cannot_be_removed(sandbox, tool):
    first = profiles.create('development', tool)
    second = profiles.create('review', tool, account_id=first['account_id'])
    inv = hooks.inventory(first['id'])
    owned = next(row for row in inv['hooks'] if row['owned'])
    with pytest.raises(ValueError):
        hooks.preview(first['id'], first['revision'], remove_id=owned['id'])
    plan = hooks.preview(first['id'], first['revision'], 'Stop', 'sample-notify --done')
    assert 'sample-notify' not in json.dumps(plan)
    hooks.apply(plan['token'])
    assert any(not row['owned'] for row in hooks.inventory(first['id'])['hooks'])
    assert all(row['owned'] for row in hooks.inventory(second['id'])['hooks'])
    row = next(row for row in hooks.inventory(first['id'])['hooks'] if not row['owned'])
    plan = hooks.preview(first['id'], profiles.get(first['id'])['revision'], remove_id=row['id'])
    hooks.apply(plan['token'])
    assert all(row['owned'] for row in hooks.inventory(first['id'])['hooks'])


def test_conversation_import_is_exact_and_resume_is_not_assumed(sandbox):
    root, source = _native(sandbox)
    sid = 'a' * 32
    transcript = root / 'projects' / '-sample' / (sid + '.jsonl')
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'user', 'sessionId': sid, 'message': {'content': 'sample'}}) + '\n')
    row = profiles.create('new', 'claude')
    conversation = migration.conversation_list(source['id'])['conversations'][0]
    plan = migration.conversation_preview(row['id'], row['revision'], source['id'], conversation['id'])
    result = migration.conversation_apply(plan['token'])
    assert result['source_preserved'] is True
    assert result['resume_verified'] is False
    migration.require_conversation(row['id'], sid)
    assert (profiles.root(row['id']) / 'projects/-sample' / (sid + '.jsonl')).read_bytes() == transcript.read_bytes()
    with pytest.raises(ValueError):
        migration.conversation_preview(row['id'], row['revision'], source['id'], conversation['id'])


def test_management_routes_refuse_machine_peer_and_unreviewed_writes(sandbox, monkeypatch):
    import auth
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from routes import agent_profiles as routes
    monkeypatch.setattr(auth, 'VT_AUTH_SESSION_KEY', 'test-signing-key')
    monkeypatch.setattr(auth, 'VT_AUTH_PASSWORD_HASH', 'configured')
    monkeypatch.setattr(auth, 'VT_AUTH_TOKEN', 'machine-only')
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.elevated_router)
    with TestClient(app) as client:
        for path in ('/api/agent-profiles/read/list', '/api/agent-profiles/read/preview', '/api/agent-profiles/apply'):
            assert client.post(path, json={}).status_code == 403
            assert client.post(path, json={}, headers={'Authorization': 'Bearer machine-only'}).status_code == 403
        client.cookies.set('vt_session', auth.make_session())
        assert client.post('/api/agent-profiles/read/list', json={}).status_code == 200
        request = {'operation': 'create', 'payload': {'name': 'new', 'tool': 'claude'}}
        assert client.post('/api/agent-profiles/read/preview', json=request).status_code == 401
        client.cookies.set('vt_session', auth.make_session(elev_exp=int(time.time()) + 60))
        preview = client.post('/api/agent-profiles/read/preview', json=request)
        assert preview.status_code == 200, preview.text
        result = client.post('/api/agent-profiles/apply', json={'token': preview.json()['token']})
        assert result.status_code == 200, result.text
        assert result.json()['tool'] == 'claude'
        assert client.post('/api/agent-profiles/apply', json={'token': preview.json()['token']}).status_code == 409
        assert client.post('/api/agent-profiles/read/list', json={'root': '/private'}).status_code == 409


def test_private_api_clone_preserves_source_and_never_returns_key(sandbox, monkeypatch):
    import subprocess
    home, _ = sandbox
    source_root = home / '.clauth' / 'profiles' / 'api-test'
    source_root.mkdir(parents=True)
    raw = b'api_key = "private-api-sentinel"\nbase_url = "https://api.example.invalid/v1"\n[env]\nSAMPLE_FLAG = "preserved"\nANTHROPIC_DEFAULT_SONNET_MODEL = "explicit-model"\n[models]\ndefault = "sample-model"\nsonnet = "default-model"\n'
    (source_root / 'config.toml').write_bytes(raw)
    source_id = next(r['id'] for r in migration.source_list()['sources'] if r['kind'] == 'clauth')
    profile = profiles.create('private test', 'claude', purpose='test')
    preview = migration.config_preview(profile['id'], 1, source_id, skill_names=[], include_private=True)
    assert 'private-api-sentinel' not in json.dumps(preview)
    result = migration.config_apply(preview['token'])
    assert 'private-api-sentinel' not in json.dumps(result)
    assert result['private_api_configuration_copied'] is True
    assert (source_root / 'config.toml').read_bytes() == raw
    root = profiles.root(profile['id'])
    assert (root / '.farshell-provider.json').stat().st_mode & 0o777 == 0o600
    settings = json.loads((root / 'settings.json').read_text())
    assert 'private-api-sentinel' not in json.dumps(settings)
    assert 'clauth' not in settings['apiKeyHelper']
    assert not (root / '.credentials.json').exists()
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'ambient-secret')
    env = runtime.environment(profile['id'])
    assert env['ANTHROPIC_BASE_URL'] == 'https://api.example.invalid/v1'
    assert env['SAMPLE_FLAG'] == 'preserved'
    assert env['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'explicit-model'
    assert 'private-api-sentinel' not in json.dumps(env)
    assert 'ambient-secret' not in json.dumps(env)
    import shlex
    native = subprocess.run(shlex.split(settings['apiKeyHelper']), capture_output=True)
    assert native.returncode == 0 and native.stdout == b'private-api-sentinel'
    assert native.stderr == b''
    other = profiles.create('ordinary', 'claude')
    with pytest.raises(ValueError):
        migration.config_preview(other['id'], 1, source_id, skill_names=[], include_private=True)
    # Source edits invalidate review without writing either destination file.
    preview = migration.config_preview(profile['id'], 2, source_id, skill_names=[], include_private=True)
    (source_root / 'config.toml').write_bytes(raw.replace(b'private-api-sentinel', b'changed-secret'))
    with pytest.raises(ValueError):
        migration.config_apply(preview['token'])
    assert b'changed-secret' not in (root / '.farshell-provider.json').read_bytes()
    # No sibling profile observes this private configuration.
    assert 'SAMPLE_FLAG' not in runtime.environment(other['id'])
    (root / '.farshell-provider.json').chmod(0o644)
    with pytest.raises(ValueError):
        runtime.environment(profile['id'])


@pytest.mark.parametrize('name,value', [('NODE_OPTIONS', '--require bad'), ('CLAUDE_CONFIG_DIR', '/tmp/other'), ('VT_CONFIG', '/tmp/other'), ('LD_PRELOAD', 'bad'), ('FSH_PROFILE_ID', 'bad')])
def test_private_clone_cannot_override_isolation(sandbox, name, value):
    with pytest.raises(ValueError):
        migration._private_configuration({'api_key': 'sample-key', 'env': {name: value}}, sandbox[0])


@pytest.mark.parametrize('tool', ['claude', 'codex'])
def test_mcp_add_preserves_native_fields_and_rejects_stale_preview(sandbox, tool):
    import agent_profile_resources as resources
    row = profiles.create('mcp-test', tool)
    pid = row['id']
    definition = {'command': 'sample-mcp', 'args': ['--mode', 'sample']}
    detail = resources.add_mcp(pid, 1, 'sample', definition, preview=True)
    resources.add_mcp(pid, 1, 'sample', definition, file_revision=detail['file_revision'])
    with pytest.raises(ValueError):
        resources.add_mcp(pid, 2, 'sample', definition)
    root = profiles.root(pid)
    path = root / ('.claude.json' if tool == 'claude' else 'config.toml')
    preview = resources.add_mcp(pid, 2, 'second', definition, preview=True)
    path.write_text(path.read_text() + '\n ')
    with pytest.raises(ValueError):
        resources.add_mcp(pid, 2, 'second', definition, file_revision=preview['file_revision'])
    assert hooks.inventory(pid)['hooks']
    with pytest.raises(ValueError):
        resources.add_mcp(pid, 2, '../bad', definition)


def test_import_rollback_retains_external_edits_and_restores_untouched_files(sandbox):
    _, source = _native(sandbox)
    row = profiles.create('rollback-test', 'claude')
    root = profiles.root(row['id'])
    original = (root / 'settings.json').read_bytes()
    plan = migration.config_preview(row['id'], 1, source['id'])
    migration.config_apply(plan['token'])
    job = next(j for j in profiles.listing()['jobs'] if j['profile_id'] == row['id'])
    assert job['state'] == 'done'
    skill = root / 'skills' / 'sample' / 'SKILL.md'
    imported = skill.read_bytes()
    skill.write_text('external edit')
    with pytest.raises(ValueError):
        migration.rollback(row['id'], 2, job['id'])
    assert skill.read_text() == 'external edit'
    skill.write_bytes(imported)
    migration.rollback(row['id'], 2, job['id'])
    assert (root / 'settings.json').read_bytes() == original
    assert not skill.exists()
    assert profiles.job_status(job['id'])['state'] == 'rolled-back'


def test_verified_resume_accepts_only_appends_to_imported_prefix(sandbox):
    _, source = _native(sandbox)
    source_root = sandbox[0] / '.claude'
    sid = '10000000-0000-4000-8000-000000000001'
    path = source_root / 'projects' / 'sample' / (sid + '.jsonl')
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"user"}\n')
    pid = profiles.create('resume-test', 'claude')['id']
    conversation = migration.conversation_list(source['id'])['conversations'][0]
    plan = migration.conversation_preview(pid, 1, source['id'], conversation['id'])
    migration.conversation_apply(plan['token'])
    target = profiles.root(pid) / path.relative_to(source_root)
    target.write_bytes(target.read_bytes() + b'{"type":"assistant"}\n')
    with pytest.raises(ValueError):
        migration.require_conversation(pid, sid)
    migration.mark_resumed(pid, sid)
    migration.require_conversation(pid, sid)
    target.write_bytes(b'{"type":"replacement"}\n')
    with pytest.raises(ValueError):
        migration.require_conversation(pid, sid)


def test_shared_clauth_conversations_use_ownership_map(sandbox):
    home, _ = sandbox
    native = home / '.claude' / 'projects' / 'sample'
    native.mkdir(parents=True)
    owner = home / '.clauth'
    for name in ('first', 'second'):
        folder = owner / 'profiles' / name
        folder.mkdir(parents=True)
        (folder / 'config.toml').write_text('')
    ids = ['10000000-0000-4000-8000-' + str(i).zfill(12) for i in range(4)]
    for sid in ids:
        (native / (sid + '.jsonl')).write_text('{"type":"user"}\n')
    (owner / 'session_profiles.json').write_text(json.dumps({'sessions': {ids[0]: {'known': 'first'}, ids[1]: {'known': 'second'}, ids[2]: 'contested'}}))
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    rows = migration.conversation_list(source['id'])['conversations']
    assert {r['conversation'] for r in rows} == {ids[0], ids[2], ids[3]}
    assert {r['ownership'] for r in rows} == {'known', 'contested', 'unknown'}
    pid = profiles.create('ownership-test', 'claude')['id']
    for row in rows:
        if row['ownership'] != 'known':
            with pytest.raises(ValueError):
                migration.conversation_preview(pid, 1, source['id'], row['id'])


def test_conversation_listing_does_not_load_transcripts_or_cap_total_size(sandbox, monkeypatch):
    home, _ = sandbox
    native = home / '.claude' / 'projects' / 'sample'
    native.mkdir(parents=True)
    owner = home / '.clauth' / 'profiles' / 'first'
    owner.mkdir(parents=True)
    (owner / 'config.toml').write_text('')
    monkeypatch.setattr(migration, 'MAX_BYTES', 2048)
    ids = ['20000000-0000-4000-8000-' + str(i).zfill(12) for i in range(3)]
    for sid in ids:
        # Each file fits the cap but the shared root is far over it in total.
        (native / (sid + '.jsonl')).write_text('{"type":"user"}\n' + 'x' * 1500)
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    rows = migration.conversation_list(source['id'])['conversations']
    assert {r['conversation'] for r in rows} == set(ids)
    assert all(r['bytes'] > 1500 and 'sha256' not in r for r in rows)
    assert migration.retirement_report()['ready_to_retire'] is False


def test_retirement_verifies_only_recent_conversations_and_lists_unowned(sandbox):
    home, _ = sandbox
    native = home / '.claude' / 'projects' / 'sample'
    native.mkdir(parents=True)
    owner = home / '.clauth'
    folder = owner / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    ids = ['30000000-0000-4000-8000-' + str(i).zfill(12) for i in range(8)]
    for number, sid in enumerate(ids):
        path = native / (sid + '.jsonl')
        path.write_text('{"type":"user"}\n')
        os.utime(path, (1_000_000 + number, 1_000_000 + number))
    # The last conversation has no recorded owner: excluded, listed as a limitation.
    (owner / 'session_profiles.json').write_text(json.dumps({'sessions': {sid: {'known': 'first'} for sid in ids[:7]}}))
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    pid = profiles.create('recent-test', 'claude')['id']

    def report():
        return next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')

    first = report()
    assert 'conversation-resume-not-verified' in first['blockers'] and 'conversation-not-imported' in first['blockers']
    assert first['accepted_limitations'] == ['conversation-ownership-unresolved']
    for row in migration.conversation_list(source['id'])['conversations']:
        if row['ownership'] == 'known':
            migration.conversation_apply(migration.conversation_preview(pid, 1, source['id'], row['id'])['token'])
    second = report()
    assert 'conversation-not-imported' not in second['blockers'] and 'conversation-resume-not-verified' in second['blockers']
    for sid in ids[2:7]:  # the five newest owned conversations
        migration.mark_resumed(pid, sid)
    third = report()
    assert 'conversation-resume-not-verified' not in third['blockers'] and 'conversation-not-imported' not in third['blockers']


def test_local_bulk_import_needs_confirmation_skips_unowned_and_reports(sandbox, monkeypatch):
    import agent_profile_local as local
    home, _ = sandbox
    native = home / '.claude' / 'projects' / 'sample'
    native.mkdir(parents=True)
    owner = home / '.clauth'
    folder = owner / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    ids = ['40000000-0000-4000-8000-' + str(i).zfill(12) for i in range(4)]
    for sid in ids:
        (native / (sid + '.jsonl')).write_text('{"type":"user"}\n')
    (owner / 'session_profiles.json').write_text(json.dumps({'sessions': {ids[0]: {'known': 'first'}, ids[1]: {'known': 'first'}, ids[2]: 'contested'}}))
    pid = profiles.create('bulk-test', 'claude')['id']
    monkeypatch.setattr('sys.stdin', type('NoTty', (), {'isatty': lambda self: False})())
    with pytest.raises(profiles.Refused):
        local.import_conversations(pid, 'first')
    assert not (profiles.root(pid) / 'projects').exists()
    result = local.import_conversations(pid, 'first', yes=True)
    assert (result['imported'], result['already_present'], result['failed'], result['excluded_unowned']) == (2, 0, [], 2)
    again = local.import_conversations(pid, 'first', yes=True)
    assert (again['imported'], again['already_present']) == (0, 2)
    with pytest.raises(profiles.Refused):
        local.import_conversations(pid, 'missing', yes=True)


def test_skill_report_separates_skills_that_contain_links(sandbox):
    home, _ = sandbox
    skills = home / '.claude' / 'skills'
    (skills / 'plain').mkdir(parents=True)
    (skills / 'plain' / 'SKILL.md').write_text('# plain\n')
    (skills / 'linked').mkdir()
    (skills / 'linked' / 'SKILL.md').write_text('# linked\n')
    (skills / 'linked' / 'shared.md').symlink_to(skills / 'plain' / 'SKILL.md')
    (home / '.clauth' / 'profiles' / 'first').mkdir(parents=True)
    (home / '.clauth' / 'profiles' / 'first' / 'config.toml').write_text('')
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    report = migration.skill_report(source['id'])
    assert report == {'importable': ['plain'], 'blocked': ['linked']}
    pid = profiles.create('skill-test', 'claude')['id']
    with pytest.raises(fsguard.FsDenied):
        migration.config_preview(pid, 1, source['id'])
    plan = migration.config_preview(pid, 1, source['id'], report['importable'])
    assert any(f['path'].startswith('skills/plain/') for f in plan['files'])


def test_session_reconcile_runs_server_side_without_creating_a_registry(sandbox, monkeypatch):
    import agent_profile_runtime as runtime
    home, _ = sandbox
    import vt_paths
    registry = vt_paths.state_dir() / 'agent-profiles' / 'registry.sqlite3'
    registry.unlink(missing_ok=True)
    assert runtime.reconcile_sessions() == 0 and not registry.exists()
    pid = profiles.create('reconcile-test', 'claude')['id']
    now = time.time()
    with profiles.database() as db:
        for name, state, age in (('profile-' + 'a' * 16, 'starting', 10), ('profile-' + 'b' * 16, 'unknown', 10),
                                 ('profile-' + 'c' * 16, 'running', 10), ('profile-' + 'd' * 16, 'starting', 7200)):
            db.execute("INSERT INTO sessions (name,profile_id,created,profile_revision,conversation,started_at,state,launch_auth_fingerprint,launch_subject) VALUES (?,?,?,?,?,?,?,?,?)",
                       (name, pid, '$1:1', 1, None, now - age, state, None, None))
    seen = []
    monkeypatch.setattr(runtime, 'session_status', lambda name: seen.append(name))
    assert runtime.reconcile_sessions() == 2
    assert sorted(seen) == ['profile-' + 'a' * 16, 'profile-' + 'b' * 16]
    assert runtime.session_recorded('profile-' + 'c' * 16)['state'] == 'running'
    with pytest.raises(profiles.Refused):
        runtime.session_recorded('not-a-session')


def test_login_closes_its_job_when_the_terminal_hangs_up_and_recover_clears_interrupted(sandbox, monkeypatch):
    import signal
    import agent_profile_local as local
    import agent_profile_runtime as runtime
    pid = profiles.create('login-hup', 'claude')['id']
    monkeypatch.setattr('sys.stdin', type('Tty', (), {'isatty': lambda self: True})())
    monkeypatch.setattr(runtime, 'validate_native_files', lambda profile_id: None)
    monkeypatch.setattr(runtime, 'require_inactive', lambda profile_id: None)
    monkeypatch.setattr(runtime, 'binary', lambda tool: '/bin/true')
    monkeypatch.setattr(runtime, 'environment', lambda profile_id: {})
    monkeypatch.setattr(runtime.subprocess, 'call', lambda *a, **k: signal.raise_signal(signal.SIGHUP))
    before = signal.getsignal(signal.SIGHUP)
    with pytest.raises(SystemExit):
        runtime.login(pid)
    assert signal.getsignal(signal.SIGHUP) == before
    with profiles.database() as db:
        assert [r['state'] for r in db.execute("SELECT state FROM jobs WHERE profile_id=? AND kind='login'", (pid,))] == ['interrupted']
    # A job left "running" (older crash) blocks the profile until recover closes it.
    jid = profiles.job_begin(pid, 'login')
    with pytest.raises(profiles.Refused):
        profiles.require_no_pending_jobs(pid)
    local.recover(pid)
    profiles.require_no_pending_jobs(pid)
    with profiles.database() as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()['state'] == 'interrupted'


def test_retirement_recomputes_the_recorded_skill_selection(sandbox):
    home, _ = sandbox
    skills = home / '.claude' / 'skills'
    (skills / 'plain').mkdir(parents=True)
    (skills / 'plain' / 'SKILL.md').write_text('# plain\n')
    (skills / 'linked').mkdir()
    (skills / 'linked' / 'SKILL.md').write_text('# linked\n')
    (skills / 'linked' / 'shared.md').symlink_to(skills / 'plain' / 'SKILL.md')
    (home / '.clauth' / 'profiles' / 'first').mkdir(parents=True)
    (home / '.clauth' / 'profiles' / 'first' / 'config.toml').write_text('')
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    pid = profiles.create('selection-test', 'claude')['id']
    selection = migration.skill_report(source['id'])['importable']
    migration.config_apply(migration.config_preview(pid, 1, source['id'], selection)['token'])
    item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
    reasons = {b for c in item['candidates'] for b in c['blockers']}
    assert 'source-verification-unavailable' not in reasons and 'configuration-verification-unavailable' not in reasons
    assert 'skill-coverage-incomplete' in reasons  # still reported: the linked package was not imported


def test_retirement_keeps_each_source_receipts_apart(sandbox):
    home, _ = sandbox
    native = home / '.claude' / 'projects' / 'sample'
    native.mkdir(parents=True)
    owner = home / '.clauth'
    for name in ('first', 'second'):
        folder = owner / 'profiles' / name
        folder.mkdir(parents=True)
        (folder / 'config.toml').write_text('')
    ids = ['50000000-0000-4000-8000-' + str(i).zfill(12) for i in range(2)]
    for sid in ids:
        (native / (sid + '.jsonl')).write_text('{"type":"user"}\n')
    (owner / 'session_profiles.json').write_text(json.dumps({'sessions': {ids[0]: {'known': 'first'}, ids[1]: {'known': 'second'}}}))
    sources = {s['name']: s for s in migration.source_list()['sources'] if s['name'] in ('first', 'second')}
    for name, sid in (('first', ids[0]), ('second', ids[1])):
        pid = profiles.create(name + '-profile', 'claude')['id']
        migration.config_apply(migration.config_preview(pid, 1, sources[name]['id'])['token'])
        row = next(r for r in migration.conversation_list(sources[name]['id'])['conversations'] if r['conversation'] == sid)
        revision = profiles.get(pid)['revision']  # the configuration import advanced it
        migration.conversation_apply(migration.conversation_preview(pid, revision, sources[name]['id'], row['id'])['token'])
        migration.mark_resumed(pid, sid)
    report = {i['name']: i for i in migration.retirement_report()['profiles']}
    for name in ('first', 'second'):
        # The second source is evaluated after the first one's conversation loop ran.
        assert 'configuration-not-migrated' not in report[name]['blockers'], name
        assert 'conversation-not-imported' not in report[name]['blockers'], name
        assert 'conversation-resume-not-verified' not in report[name]['blockers'], name
        assert report[name]['candidates'], name


def test_long_lived_token_is_validated_stored_privately_and_injected_only_for_its_profile(sandbox, monkeypatch):
    import agent_profile_runtime as runtime
    import agent_profile_token as long_lived
    mint = 'sk-ant-oat01-' + 'A1b2' * 12
    pid = profiles.create('token-test', 'claude')['id']
    other = profiles.create('other-test', 'claude')['id']
    for bad in ('', '   ', 'sk-ant-api03-' + 'x' * 50, 'not-a-token' * 8, 'sk-ant-oat01-short', 'sk-ant-oat01-' + 'a b' * 20):
        with pytest.raises(profiles.Refused):
            long_lived.set_token(pid, bad)
    assert not (profiles.root(pid) / long_lived.FILENAME).exists()
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'ambient-should-never-pass')
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in runtime.environment(pid)
    summary = long_lived.set_token(pid, '  ' + mint + '\n')
    assert summary['configured'] and 360 <= summary['days_left'] <= 365 and not summary['expiring']
    assert mint not in json.dumps(summary) and mint not in json.dumps(long_lived.status(pid))
    path = profiles.root(pid) / long_lived.FILENAME
    assert path.stat().st_mode & 0o777 == 0o600
    assert runtime.environment(pid)['CLAUDE_CODE_OAUTH_TOKEN'] == mint
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in runtime.environment(other)
    monkeypatch.setattr(runtime, 'run', lambda profile_id, args, timeout=30: (0, b'{"loggedIn": true, "authMethod": "oauth_token"}'))
    status = runtime.auth_status(pid)
    assert status['auth_mode'] == 'long-lived-token' and status['authenticated'] and 'token set' in status['login_command']
    assert mint not in json.dumps(status)
    # Authentication binding changes when the token does, and never contains it.
    first = runtime.authentication_fingerprint(pid)
    long_lived.set_token(pid, 'sk-ant-oat01-' + 'Z9y8' * 12)
    assert first and runtime.authentication_fingerprint(pid) != first and mint not in runtime.authentication_fingerprint(pid)
    monkeypatch.setattr('sys.stdin', type('Tty', (), {'isatty': lambda self: True})())
    monkeypatch.setattr(runtime, 'validate_native_files', lambda profile_id: None)
    with pytest.raises(profiles.Refused):
        runtime.login(pid)  # a token profile is re-registered with `token set`, not logged in again
    long_lived.clear(pid)
    assert not path.exists() and 'CLAUDE_CODE_OAUTH_TOKEN' not in runtime.environment(pid)


def test_long_lived_token_expiry_warning_and_usage_row(sandbox):
    import agent_profile_token as long_lived
    from usage import profiles as usage_profiles
    pid = profiles.create('expiry-test', 'claude')['id']
    long_lived.set_token(pid, 'sk-ant-oat01-' + 'Q7w8' * 12)
    path = profiles.root(pid) / long_lived.FILENAME
    value = json.loads(path.read_text())
    value['expires_at'] = time.time() + 10 * 86400
    path.write_text(json.dumps(value))
    assert long_lived.status(pid)['expiring'] and not long_lived.status(pid)['expired']
    row = usage_profiles.NativeProfileProvider(pid).snapshot()
    assert row['auth_status'] == 'static-token' and row['fetch_status'] == 'Unsupported' and row['windows'] == []
    assert 'sk-ant' not in json.dumps(row)
    value['expires_at'] = time.time() - 5
    path.write_text(json.dumps(value))
    assert long_lived.status(pid)['expired']
    assert usage_profiles.NativeProfileProvider(pid).snapshot()['auth_status'] == 'expired'


def test_long_lived_token_refuses_api_profiles_and_codex(sandbox):
    import agent_profile_token as long_lived
    codex = profiles.create('codex-token', 'codex')['id']
    with pytest.raises(profiles.Refused):
        long_lived.set_token(codex, 'sk-ant-oat01-' + 'A1b2' * 12)


def test_retirement_reports_token_profiles_as_limitations_not_unclearable_blockers(sandbox):
    import agent_profile_token as long_lived
    home, _ = sandbox
    (home / '.claude').mkdir(parents=True, exist_ok=True)
    folder = home / '.clauth' / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    oauth = profiles.create('oauth-profile', 'claude')['id']
    mint = profiles.create('mint-profile', 'claude')['id']
    for pid in (oauth, mint):
        migration.config_apply(migration.config_preview(pid, 1, source['id'])['token'])
    long_lived.set_token(mint, 'sk-ant-oat01-' + 'M3n4' * 12)
    item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
    by_profile = {c['profile_id']: set(c['blockers']) for c in item['candidates']}
    assert {'native-refresh-not-certified', 'native-usage-not-certified'} <= by_profile[oauth]
    assert not {'native-refresh-not-certified', 'native-usage-not-certified', 'source-account-not-certified'} & by_profile[mint]
    assert 'native-inference-not-certified' in by_profile[mint]  # a real request is still required
    assert 'long-lived-token-usage-and-identity-unverified' in item['accepted_limitations']


def test_long_lived_token_import_from_clauth_takes_only_a_real_mint(sandbox):
    import agent_profile_runtime as runtime
    import agent_profile_token as long_lived
    home, _ = sandbox
    folder = home / '.clauth' / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    sidecar = folder / 'session-token.static.json'
    mint = 'sk-ant-oat01-' + 'K5l6' * 12
    expires_ms = int((time.time() + 200 * 86400) * 1000)

    def write(oauth):
        sidecar.write_text(json.dumps({'claudeAiOauth': oauth}))
        sidecar.chmod(0o600)

    pid = profiles.create('import-test', 'claude')['id']
    write({'accessToken': mint, 'expiresAt': expires_ms, 'scopes': ['user:inference', 'user:sessions:claude_code']})
    before = sidecar.read_bytes()
    summary = long_lived.import_from_clauth(pid, 'first')
    assert 195 <= summary['days_left'] <= 200 and mint not in json.dumps(summary)
    assert runtime.environment(pid)['CLAUDE_CODE_OAUTH_TOKEN'] == mint
    assert sidecar.read_bytes() == before  # the clauth original is never modified
    long_lived.clear(pid)
    # A rotating pair or a wider scope would create a second holder of a single-use chain.
    write({'accessToken': mint, 'refreshToken': 'r' * 40, 'expiresAt': expires_ms, 'scopes': ['user:inference']})
    with pytest.raises(profiles.Refused):
        long_lived.import_from_clauth(pid, 'first')
    write({'accessToken': mint, 'expiresAt': expires_ms, 'scopes': ['user:inference', 'user:profile']})
    with pytest.raises(profiles.Refused):
        long_lived.import_from_clauth(pid, 'first')
    write({'accessToken': mint, 'expiresAt': int((time.time() - 60) * 1000), 'scopes': ['user:inference']})
    with pytest.raises(profiles.Refused):
        long_lived.import_from_clauth(pid, 'first')
    write({'accessToken': 'sk-ant-api03-' + 'x' * 50, 'expiresAt': expires_ms, 'scopes': ['user:inference']})
    with pytest.raises(profiles.Refused):
        long_lived.import_from_clauth(pid, 'first')
    for name in ('missing', '../first', ''):
        with pytest.raises(profiles.Refused):
            long_lived.import_from_clauth(pid, name)
    assert not long_lived.configured(pid)


def _global_claude(home):
    base = home / '.claude'
    for kind, names in (('skills', ('alpha', 'beta')), ('agents', ('a1.md',)), ('commands', ('c1.md',)), ('hooks', ('h1.sh',))):
        (base / kind).mkdir(parents=True, exist_ok=True)
        for name in names:
            path = base / kind / name
            if '.' in name:
                path.write_text('global ' + name)
            else:
                path.mkdir(exist_ok=True)
                (path / 'SKILL.md').write_text('# ' + name + '\n')
    (base / 'CLAUDE.md').write_text('global instructions\n')
    (base / 'plugins').mkdir(exist_ok=True)
    (base / 'plugins' / 'installed_plugins.json').write_text('{}')
    (base / 'projects' / 'sample').mkdir(parents=True, exist_ok=True)
    return base


def test_share_links_global_entries_and_keeps_the_profiles_own_extensions(sandbox):
    import agent_profile_share as share
    home, _ = sandbox
    base = _global_claude(home)
    pid = profiles.create('share-test', 'claude')['id']
    root = profiles.root(pid)
    (root / 'skills' / 'alpha').mkdir(parents=True)  # an identical copy of the global one
    (root / 'skills' / 'alpha' / 'SKILL.md').write_text('# alpha\n')
    (root / 'skills' / 'beta').mkdir()
    (root / 'skills' / 'beta' / 'SKILL.md').write_text('# beta, edited for this account\n')
    (root / 'skills' / 'mine').mkdir()
    (root / 'skills' / 'mine' / 'SKILL.md').write_text('# account only\n')
    (root / 'projects' / 'old').mkdir(parents=True)
    (root / 'projects' / 'old' / 'x.jsonl').write_text('imported copy')
    (root / 'CLAUDE.md').write_text('account specific instructions\n')
    report = share.sync(pid)
    link = lambda path: os.readlink(path) if path.is_symlink() else None
    assert link(root / 'skills' / 'alpha') == str(base / 'skills' / 'alpha') and 'skills/alpha' in report['moved_aside']
    assert not (root / 'skills' / 'beta').is_symlink() and 'skills/beta' in report['kept_own']
    assert (root / 'skills' / 'mine' / 'SKILL.md').read_text() == '# account only\n' and not (root / 'skills' / 'mine').is_symlink()
    assert not (root / 'CLAUDE.md').is_symlink() and 'CLAUDE.md' in report['kept_own']
    for kind, name in (('agents', 'a1.md'), ('commands', 'c1.md'), ('hooks', 'h1.sh')):
        assert link(root / kind / name) == str(base / kind / name)
    assert link(root / 'plugins') == str(base / 'plugins') and link(root / 'projects') == str(base / 'projects')
    assert share.is_history_shared(pid)
    moved = list((root / '.pre-share').glob('*/*'))  # nothing is deleted: replaced copies sit aside
    assert any(m.name == 'projects' and (m / 'old' / 'x.jsonl').read_text() == 'imported copy' for m in moved)
    # Stable on a second run, picks up new global entries and drops links whose target vanished.
    (base / 'skills' / 'gamma').mkdir()
    (base / 'skills' / 'gamma' / 'SKILL.md').write_text('# gamma\n')
    (base / 'agents' / 'a1.md').unlink()
    again = share.sync(pid, replace=False)
    assert 'skills/gamma' in again['linked'] and 'agents/a1.md' in again['removed_stale'] and not again['moved_aside']
    assert fsguard.read_profile_bytes(root, 'skills/gamma/SKILL.md') == b'# gamma\n'


def test_share_replaces_a_copy_of_a_global_entry_that_is_itself_a_link(sandbox):
    import agent_profile_share as share
    home, _ = sandbox
    base = _global_claude(home)
    repo = home / 'repo' / 'delta'
    repo.mkdir(parents=True)
    (repo / 'SKILL.md').write_text('# delta\n')
    (base / 'skills' / 'delta').symlink_to(repo)
    pid = profiles.create('link-target', 'claude')['id']
    root = profiles.root(pid)
    (root / 'skills' / 'delta').mkdir(parents=True)
    (root / 'skills' / 'delta' / 'SKILL.md').write_text('# delta\n')  # the copy an earlier import made
    (root / 'skills' / 'epsilon').mkdir()
    (root / 'skills' / 'epsilon' / 'SKILL.md').write_text('# differs\n')
    (base / 'skills' / 'epsilon').symlink_to(repo)
    report = share.sync(pid)
    assert os.readlink(root / 'skills' / 'delta') == str(base / 'skills' / 'delta') and 'skills/delta' in report['moved_aside']
    assert 'skills/epsilon' in report['kept_own'] and not (root / 'skills' / 'epsilon').is_symlink()
    assert (root / 'skills' / 'delta' / 'SKILL.md').read_text() == '# delta\n'  # CC follows the chain


def test_shared_skills_and_plugins_clear_their_retirement_blockers(sandbox):
    import agent_profile_share as share
    home, _ = sandbox
    _global_claude(home)
    (home / '.claude' / 'settings.json').write_text('{"enabledPlugins": {"x@y": true}}')
    folder = home / '.clauth' / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    pid = profiles.create('coverage-test', 'claude')['id']
    # Imported without the skills that contain links: coverage is incomplete and plugins need reinstalling.
    (home / '.claude' / 'skills' / 'linked').mkdir()
    (home / '.claude' / 'skills' / 'linked' / 'SKILL.md').write_text('# linked\n')
    (home / '.claude' / 'skills' / 'linked' / 'l.md').symlink_to(home / '.claude' / 'skills' / 'alpha' / 'SKILL.md')
    migration.config_apply(migration.config_preview(pid, 1, source['id'], migration.skill_report(source['id'])['importable'])['token'])

    def reasons():
        item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
        return {b for c in item['candidates'] for b in c['blockers']}

    before = reasons()
    assert {'skill-coverage-incomplete', 'plugins-reinstall-required'} <= before
    share.sync(pid)
    after = reasons()
    assert 'skill-coverage-incomplete' not in after and 'plugins-reinstall-required' not in after
    assert 'configuration-verification-unavailable' not in after and 'source-verification-unavailable' not in after


def test_share_refuses_a_foreign_link_and_non_claude_profiles(sandbox):
    import agent_profile_share as share
    home, _ = sandbox
    _global_claude(home)
    pid = profiles.create('foreign-link', 'claude')['id']
    root = profiles.root(pid)
    root.mkdir(parents=True, exist_ok=True)
    elsewhere = home / 'elsewhere'
    elsewhere.mkdir()
    (root / 'projects').symlink_to(elsewhere)
    report = share.sync(pid)
    assert 'projects' in report['conflicts'] and os.readlink(root / 'projects') == str(elsewhere)
    assert not share.is_history_shared(pid)
    with pytest.raises(profiles.Refused):
        share.sync(profiles.create('codex-share', 'codex')['id'])


def test_shared_history_resume_needs_no_import_but_is_still_certified(sandbox):
    import agent_profile_share as share
    home, _ = sandbox
    base = _global_claude(home)
    owner = home / '.clauth' / 'profiles' / 'first'
    owner.mkdir(parents=True)
    (owner / 'config.toml').write_text('')
    sid = '60000000-0000-4000-8000-000000000001'
    (base / 'projects' / 'sample' / (sid + '.jsonl')).write_text('{"type":"user"}\n')
    (home / '.clauth' / 'session_profiles.json').write_text(json.dumps({'sessions': {sid: {'known': 'first'}}}))
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    pid = profiles.create('shared-resume', 'claude')['id']
    with pytest.raises(profiles.Refused):
        migration.require_conversation(pid, sid)  # not shared yet: an import receipt is required
    migration.config_apply(migration.config_preview(pid, 1, source['id'])['token'])
    share.sync(pid)
    row = next(r for r in migration.conversation_list(source['id'])['conversations'] if r['conversation'] == sid)
    with pytest.raises(profiles.Refused):
        migration.conversation_preview(pid, profiles.get(pid)['revision'], source['id'], row['id'])  # nothing to copy
    migration.require_conversation(pid, sid)  # recorded, so a verified resume can be certified
    with pytest.raises(profiles.Refused):
        migration.require_conversation(pid, '60000000-0000-4000-8000-000000000099')  # not in the shared history
    item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
    assert 'conversation-resume-not-verified' in item['blockers'] and 'conversation-not-imported' not in item['blockers']
    migration.mark_resumed(pid, sid)
    item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
    assert 'conversation-resume-not-verified' not in item['blockers']


def _global_settings_fixture(home):
    base = home / '.claude'
    base.mkdir(parents=True, exist_ok=True)
    settings = {
        'theme': 'dark', 'language': 'korean', 'model': 'global-model', 'apiKeyHelper': '/bin/global-helper',
        'permissions': {'allow': ['Bash(ls:*)'], 'defaultMode': 'default'},
        'enabledPlugins': {'alpha@mk': True, 'beta@mk': True},
        'skillOverrides': {'noisy': 'off'},
        'env': {'ENABLE_TOOL_SEARCH': 'true', 'ANTHROPIC_AUTH_TOKEN': 'global-account-secret', 'MY_API_KEY': 'k', 'PLAIN': 'p'},
        'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': '/opt/gstack/session-update'}]}],
                  'Stop': [{'hooks': [{'type': 'command', 'command': '/x/.clauth/profiles/a/runtime-1-0/stale-hook.sh'}]}]},
    }
    (base / 'settings.json').write_text(json.dumps(settings))
    (home / '.claude.json').write_text(json.dumps({'mcpServers': {
        'notion': {'type': 'stdio', 'command': 'npx', 'args': ['x'], 'env': {'NOTION_TOKEN': 'shared-mcp-key'}},
        'remote': {'type': 'http', 'url': 'https://example.invalid/mcp'}}}))
    return base / 'settings.json'


def test_settings_sync_follows_global_keeps_per_account_keys_and_per_profile_switches(sandbox, monkeypatch):
    import agent_profile_settings as sync
    home, _ = sandbox
    gpath = _global_settings_fixture(home)
    monkeypatch.setattr(sync, '_active', lambda profile_id: False)
    pid = profiles.create('settings-sync', 'claude')['id']
    root = profiles.root(pid)
    (root / 'skills').mkdir(parents=True, exist_ok=True)
    (root / 'skills' / 'noisy').mkdir()
    (root / 'settings.json').write_text(json.dumps({'model': 'profile-model', 'language': 'english'}))
    before = (gpath.read_bytes(), (home / '.claude.json').read_bytes())
    dry = sync.sync(pid, dry_run=True)
    assert json.loads((root / 'settings.json').read_text())['model'] == 'profile-model' and not (root / sync.BASE).exists()
    report = sync.sync(pid)
    mine = json.loads((root / 'settings.json').read_text())
    # Followed: shared preferences, plugin switches, permissions, plain env, third-party hooks.
    assert mine['theme'] == 'dark' and mine['enabledPlugins'] == {'alpha@mk': True, 'beta@mk': True}
    assert mine['permissions']['allow'] == ['Bash(ls:*)'] and mine['env'] == {'ENABLE_TOOL_SEARCH': 'true', 'PLAIN': 'p'}
    assert mine['skillOverrides'] == {'noisy': 'off'}
    assert any('gstack' in h['command'] for g in mine['hooks']['SessionStart'] for h in g['hooks'])
    assert any('agent_hook.sh' in h['command'] for g in mine['hooks']['SessionStart'] for h in g['hooks'])  # FarShell's own stay
    assert 'Stop' not in json.dumps([g for g in mine['hooks'].get('Stop', []) if 'runtime-' in json.dumps(g)])  # stale clauth path never copied
    # Account keys are never touched: not copied in, not overwritten, not reported.
    assert mine['model'] == 'profile-model' and 'apiKeyHelper' not in mine and 'ANTHROPIC_AUTH_TOKEN' not in mine['env'] and 'MY_API_KEY' not in mine['env']
    # A value the profile already had counts as its own.
    assert mine['language'] == 'english' and 'language' in report['own']
    # MCP definitions follow with the keys they carry.
    native = json.loads((root / '.claude.json').read_text())
    assert native['mcpServers']['notion']['env']['NOTION_TOKEN'] == 'shared-mcp-key' and native['mcpServers']['remote']['url'].startswith('https://')
    assert (root / '.claude.json').stat().st_mode & 0o777 == 0o600 and (root / sync.BASE).stat().st_mode & 0o777 == 0o600
    # The report names keys only; the global files are untouched.
    assert 'shared-mcp-key' not in json.dumps(report) and 'global-account-secret' not in json.dumps(report) and 'dark' not in json.dumps(report)
    assert 'model' not in report['follow'] + report['keep'] + report['own']
    assert (gpath.read_bytes(), (home / '.claude.json').read_bytes()) == before and dry['dry_run']
    assert 'shared-mcp-key' not in (root / sync.BASE).read_text()
    # Per-profile switches: off here, and it stays off while everything else keeps following.
    sync.set_state(pid, 'plugin', 'beta@mk', False)
    sync.set_state(pid, 'skill', 'noisy', True)
    changed = json.loads(gpath.read_text())
    changed['theme'] = 'light'
    changed['enabledPlugins'] = {'alpha@mk': False, 'beta@mk': True, 'gamma@mk': True}
    changed['skillOverrides'] = {'noisy': 'off', 'other': 'off'}
    gpath.write_text(json.dumps(changed))
    sync.sync(pid)
    mine = json.loads((root / 'settings.json').read_text())
    assert mine['theme'] == 'light'
    assert mine['enabledPlugins'] == {'alpha@mk': False, 'beta@mk': False, 'gamma@mk': True}
    assert mine['skillOverrides'] == {'noisy': 'on', 'other': 'off'}
    assert mine['language'] == 'english'  # the profile's own choice survives a global change
    # The global value can be taken back explicitly.
    sync.sync(pid, take_global=['enabledPlugins.beta@mk', 'language'])
    mine = json.loads((root / 'settings.json').read_text())
    assert mine['enabledPlugins']['beta@mk'] is True and mine['language'] == 'korean'
    with pytest.raises(profiles.Refused):
        sync.set_state(pid, 'plugin', 'missing@mk', False)
    with pytest.raises(profiles.Refused):
        sync.set_state(pid, 'skill', '../escape', False)


def test_settings_sync_respects_parked_mcp_and_hooks_and_defers_mcp_while_running(sandbox, monkeypatch):
    import agent_profile_settings as sync
    home, _ = sandbox
    gpath = _global_settings_fixture(home)
    pid = profiles.create('settings-parked', 'claude')['id']
    root = profiles.root(pid)
    (root / '.farshell-disabled-mcp.json').write_text(json.dumps({'remote': {'type': 'http', 'url': 'https://example.invalid/mcp'}}))
    monkeypatch.setattr(sync, '_active', lambda profile_id: True)
    report = sync.sync(pid)
    assert report['mcp_deferred'] is True and not (root / '.claude.json').exists()  # a running session owns that file
    assert json.loads((root / sync.BASE).read_text())['mcp'] == {}  # nothing is recorded as applied
    monkeypatch.setattr(sync, '_active', lambda profile_id: False)
    sync.sync(pid)
    names = set(json.loads((root / '.claude.json').read_text())['mcpServers'])
    assert names == {'notion'}  # the one switched off in this profile is not brought back
    # An event with hooks switched off in this profile is left to the manager.
    vault = {'settings.json:SessionStart': {'base': [{'hooks': [{'type': 'command', 'command': '/opt/gstack/session-update'}]}], 'disabled': ['0:0'], 'active_hash': 'x'}}
    (root / '.farshell-hooks-disabled.json').write_text(json.dumps(vault))
    (root / '.farshell-hooks-disabled.json').chmod(0o600)
    changed = json.loads(gpath.read_text())
    changed['hooks']['SessionStart'] = [{'hooks': [{'type': 'command', 'command': '/opt/gstack/new-hook'}]}]
    gpath.write_text(json.dumps(changed))
    again = sync.sync(pid)
    assert 'hooks.SessionStart' in again['skipped']
    assert 'new-hook' not in (root / 'settings.json').read_text()


def test_settings_sync_rejects_codex_and_a_following_profile_is_not_flagged_as_stale(sandbox, monkeypatch):
    import agent_profile_settings as sync
    home, _ = sandbox
    _global_settings_fixture(home)
    with pytest.raises(profiles.Refused):
        sync.sync(profiles.create('codex-sync', 'codex')['id'])
    pid = profiles.create('follow-flag', 'claude')['id']
    assert not sync.following(pid)
    monkeypatch.setattr(sync, '_active', lambda profile_id: False)
    sync.sync(pid)
    assert sync.following(pid)


def _fake_sessions(pid, count, state='running', created='$1:1', revision=None):
    now = time.time()
    names = []
    revision = revision or profiles.get(pid)['revision']
    with profiles.database() as db:
        for index in range(count):
            name = 'profile-' + format(abs(hash((pid, index, state, now))) % (16 ** 16), '016x')
            db.execute('INSERT INTO sessions (name,profile_id,created,profile_revision,started_at,state) VALUES (?,?,?,?,?,?)',
                       (name, pid, created, revision, now, state))
            names.append(name)
    return names


def test_concurrent_sessions_of_a_token_profile_certify_once_and_are_remembered(sandbox, monkeypatch):
    import agent_profile_runtime as runtime
    import agent_profile_token as long_lived
    home, _ = sandbox
    folder = home / '.clauth' / 'profiles' / 'first'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    source = next(s for s in migration.source_list()['sources'] if s['name'] == 'first')
    pid = profiles.create('token-concurrency', 'claude')['id']
    migration.config_apply(migration.config_preview(pid, 1, source['id'])['token'])
    long_lived.set_token(pid, 'sk-ant-oat01-' + 'C4d5' * 12)
    monkeypatch.setattr(migration, '_legacy_runtime_state', lambda: 'absent')
    alive = {'value': True}
    monkeypatch.setattr(runtime, '_created', lambda name: '$1:1' if alive['value'] else (_ for _ in ()).throw(profiles.Refused('gone')))

    def blockers():
        item = next(i for i in migration.retirement_report()['profiles'] if i['name'] == 'first')
        return {b for c in item['candidates'] for b in c['blockers']}

    _fake_sessions(pid, 1)
    one = blockers()
    assert 'same-account-concurrency-not-certified' in one and 'live-profile-session-not-certified' not in one  # one session is pinned, not concurrent
    _fake_sessions(pid, 1)
    two = blockers()
    assert 'same-account-concurrency-not-certified' not in two
    with profiles.database() as db:
        assert db.execute("SELECT COUNT(*) FROM receipts WHERE kind='concurrency' AND profile_id=? AND verified=1", (pid,)).fetchone()[0] == 1
    # The observation is remembered, but a tmux session that no longer exists is no live evidence.
    alive['value'] = False
    after = blockers()
    assert 'same-account-concurrency-not-certified' not in after and 'live-profile-session-not-certified' in after
    # A new token is a different authentication: the old observation does not carry over.
    long_lived.set_token(pid, 'sk-ant-oat01-' + 'E6f7' * 12)
    assert 'same-account-concurrency-not-certified' in blockers()


def test_concurrency_is_not_taken_from_a_refreshing_login_profile(sandbox, monkeypatch):
    import agent_profile_runtime as runtime
    pid = profiles.create('login-concurrency', 'claude')['id']
    monkeypatch.setattr(runtime, '_created', lambda name: '$1:1')
    monkeypatch.setattr(runtime, 'authentication_fingerprint', lambda _: 'a-real-looking-login-fingerprint')  # so only the rule can refuse it
    monkeypatch.setattr(migration, '_legacy_runtime_state', lambda: 'absent')
    _fake_sessions(pid, 3)
    migration.retirement_report()
    with profiles.database() as db:
        assert db.execute("SELECT COUNT(*) FROM receipts WHERE kind='concurrency'").fetchone()[0] == 0


def test_session_view_trusts_the_recorded_state_only_while_tmux_still_has_the_session(sandbox, monkeypatch):
    import agent_profile_runtime as runtime
    pid = profiles.create('view-test', 'claude')['id']
    name = _fake_sessions(pid, 1)[0]
    monkeypatch.setattr(runtime, '_created', lambda n: '$1:1')
    assert runtime.session_view(name)['state'] == 'running'
    monkeypatch.setattr(runtime, '_created', lambda n: '$9:9')  # the name was reused by another tmux session
    assert runtime.session_view(name)['state'] == 'ended'
    monkeypatch.setattr(runtime, '_created', lambda n: (_ for _ in ()).throw(profiles.Refused('gone')))
    assert runtime.session_view(name)['state'] == 'ended'
    other = _fake_sessions(pid, 1, state='unknown')[0]
    assert runtime.session_view(other)['state'] == 'unknown'


def test_plugin_review_uses_fixed_snapshot_and_refuses_tampering(sandbox, monkeypatch):
    import agent_profile_plugins as plugins
    home, _ = sandbox
    source = home / 'plugin-source'
    marker = source / '.claude-plugin'
    marker.mkdir(parents=True)
    (marker / 'plugin.json').write_text('{"name":"sample","version":"1.0.0"}')
    (source / 'README.md').write_text('reviewed package')
    pid = profiles.create('plugin-test', 'claude')['id']
    calls = []
    monkeypatch.setattr(runtime, 'run', lambda *a, **k: calls.append(a))
    source_mode = source.stat().st_mode
    plan = plugins.preview(pid, 1, 'install', 'sample@sample-market', str(source))
    assert source.stat().st_mode == source_mode
    assert not calls
    stage = Path(plan['staged_path'])
    (source / 'README.md').write_text('changed original')
    assert (stage / 'package' / 'README.md').read_text() == 'reviewed package'
    (stage / 'package' / 'README.md').write_text('changed snapshot')
    with pytest.raises(ValueError):
        plugins.apply(plan['token'])
    assert not calls
    assert profiles.get(pid)['revision'] == 1
    (source / '.env').write_text('private-sentinel')
    with pytest.raises(ValueError):
        plugins.preview(pid, 1, 'install', 'sample@sample-market', str(source))


def test_unexpected_parser_errors_do_not_export_private_values(sandbox):
    from routes.agent_profiles import _error
    for exc in (ValueError('private-sentinel'), TypeError('private-sentinel'), UnicodeError('private-sentinel')):
        assert b'private-sentinel' not in _error(exc).body


def test_visible_custom_state_is_private_to_file_viewer_and_search(sandbox, monkeypatch):
    home, workspace = sandbox
    monkeypatch.setenv('VT_STATE_DIR', str(workspace / 'visible-state'))
    pid = profiles.create('private-state', 'claude')['id']
    root = profiles.root(pid)
    backup = profiles.directory() / 'backups' / 'sample.bak'
    backup.parent.mkdir()
    backup.write_text('private-sentinel')
    for path in (root / 'settings.json', backup):
        with pytest.raises(fsguard.FsDenied):
            fsguard.resolve_under_roots(str(path), check_hidden=False)
    from routes.files import _list_dir
    listing = _list_dir(profiles.directory().parent)
    assert 'agent-profiles' not in [r['name'] for r in listing['entries']]


def test_claude_conversation_bundle_copies_sidecars_and_rejects_source_changes(sandbox):
    source_root, source = _native(sandbox)
    sid = '10000000-0000-4000-8000-000000000011'
    rel = Path('projects/sample') / (sid + '.jsonl')
    main = source_root / rel
    main.parent.mkdir(parents=True)
    main.write_text('{"type":"user"}\n')
    sidecar = source_root / rel.with_suffix('') / 'tool-results' / 'result.txt'
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('reviewed tool result')
    history = source_root / 'file-history' / sid / 'sample@v1'
    history.parent.mkdir(parents=True)
    history.write_text('reviewed file history')
    pid = profiles.create('bundle-test', 'claude')['id']
    row = next(r for r in migration.conversation_list(source['id'])['conversations'] if r['conversation'] == sid)
    plan = migration.conversation_preview(pid, 1, source['id'], row['id'])
    assert plan['files'] == 3
    sidecar.write_text('changed source')
    with pytest.raises(ValueError):
        migration.conversation_apply(plan['token'])
    assert not (profiles.root(pid) / rel).exists()
    plan = migration.conversation_preview(pid, 1, source['id'], row['id'])
    migration.conversation_apply(plan['token'])
    for original in (main, sidecar, history):
        assert (profiles.root(pid) / original.relative_to(source_root)).read_bytes() == original.read_bytes()
    migration.mark_resumed(pid, sid)
    migration.require_conversation(pid, sid)
    (profiles.root(pid) / sidecar.relative_to(source_root)).write_text('changed target')
    with pytest.raises(ValueError):
        migration.require_conversation(pid, sid)


def test_large_conversation_is_streamed_beyond_the_memory_bound(sandbox, monkeypatch):
    source_root, source = _native(sandbox)
    monkeypatch.setattr(migration, 'MAX_BYTES', 1024)  # configuration/memory bound; conversations stream past it
    sid = '10000000-0000-4000-8000-000000000021'
    rel = Path('projects/sample') / (sid + '.jsonl')
    main = source_root / rel
    main.parent.mkdir(parents=True)
    main.write_bytes(b'{"type":"user"}\n' + b'a' * 300_000)
    sidecar = source_root / rel.with_suffix('') / 'subagents' / 'agent-1.jsonl'
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b'{"type":"user"}\n' + b'b' * 200_000)
    pid = profiles.create('stream-test', 'claude')['id']
    row = next(r for r in migration.conversation_list(source['id'])['conversations'] if r['conversation'] == sid)
    plan = migration.conversation_preview(pid, 1, source['id'], row['id'])
    assert plan['bytes'] > 500_000
    sidecar.write_bytes(sidecar.read_bytes() + b'c')  # source changes after review: refused, nothing half-copied
    with pytest.raises(ValueError):
        migration.conversation_apply(plan['token'])
    assert not (profiles.root(pid) / rel).exists()
    migration.conversation_apply(migration.conversation_preview(pid, 1, source['id'], row['id'])['token'])
    for original in (main, sidecar):
        assert (profiles.root(pid) / original.relative_to(source_root)).read_bytes() == original.read_bytes()
    migration.require_conversation(pid, sid)
    target = profiles.root(pid) / rel
    target.write_bytes(target.read_bytes() + b'turn')  # appended turns after a verified resume are accepted
    with pytest.raises(ValueError):
        migration.require_conversation(pid, sid)
    migration.mark_resumed(pid, sid)
    migration.require_conversation(pid, sid)


def test_codex_history_base_follows_rollout_filename_not_stable_thread_id(sandbox, monkeypatch):
    home, _ = sandbox
    root = home / '.codex'
    folder = root / 'sessions' / '2026' / '10' / '22'
    folder.mkdir(parents=True)
    sid = '10000000-0000-4000-8000-000000000012'
    parent_rollout = '10000000-0000-4000-8000-000000000013'
    child_rollout = '10000000-0000-4000-8000-000000000014'
    parent = folder / ('rollout-' + parent_rollout + '.jsonl')
    child = folder / ('rollout-' + child_rollout + '.jsonl')
    parent.write_text(json.dumps({'type':'session_meta','payload':{'id':sid}})+'\n')
    child.write_text(json.dumps({'type':'session_meta','payload':{'id':sid,'history_base':{'thread_id':parent_rollout,'end_byte_offset':parent.stat().st_size,'end_ordinal_exclusive':1}}})+'\n')
    source = next(r for r in migration.source_list()['sources'] if r['tool'] == 'codex')
    row = next(r for r in migration.conversations(source['id']).values() if r['rollout_id'] == child_rollout)
    pid = profiles.create('inherited-thread-test', 'codex')['id']
    monkeypatch.setattr(runtime, 'codex_read', lambda *args: True)
    plan = migration.conversation_preview(pid, 1, source['id'], row['id'])
    assert plan['files'] == 2
    result = migration.conversation_apply(plan['token'])
    assert result['native_read_verified'] and not result['resume_verified']
    for original in (parent, child):
        assert (profiles.root(pid) / original.relative_to(root)).read_bytes() == original.read_bytes()
    # The inherited prefix is required, even though both metas use one stable ID.
    other = profiles.create('missing-prefix-test', 'codex')['id']
    parent.unlink()
    with pytest.raises(ValueError):
        migration.conversation_preview(other, 1, source['id'], row['id'])


def test_import_alone_never_certifies_retirement(sandbox, monkeypatch):
    home, _ = sandbox
    folder = home / '.clauth' / 'profiles' / 'sample'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    source = migration.source_list()['sources'][0]
    pid = profiles.create('uncertified', 'claude')['id']
    migration.config_apply(migration.config_preview(pid, 1, source['id'])['token'])
    monkeypatch.setattr(migration, '_legacy_runtime_state', lambda: 'absent')
    monkeypatch.setattr(runtime, 'authentication_fingerprint', lambda _: None)
    result = migration.retirement_report()
    assert result['ready_to_retire'] is False
    assert 'native-inference-not-certified' in result['profiles'][0]['blockers']
    assert 'same-account-concurrency-not-certified' in result['profiles'][0]['blockers']


def test_custom_api_quota_parity_never_sends_key_to_an_unrelated_provider(sandbox):
    home, _ = sandbox
    folder = home / '.clauth' / 'profiles' / 'sample'
    folder.mkdir(parents=True)
    path = folder / 'config.toml'
    path.write_text('base_url="https://proxy.example.invalid/v1"\n')
    source = {'root':folder}
    assert migration._source_has_api_quota(source) is False
    path.write_text('base_url="https://api.deepseek.com"\n[env]\nANTHROPIC_BASE_URL="https://proxy.example.invalid/v1"\n')
    assert migration._source_has_api_quota(source) is True


def test_crashed_job_blocks_launch_before_native_auth(sandbox, monkeypatch):
    row = profiles.create('interrupted', 'claude')
    profiles.job_begin(row['id'], 'config-import')
    monkeypatch.setattr(runtime, 'auth_status', lambda _: pytest.fail('must not start native work before recovery'))
    with pytest.raises(ValueError):
        runtime.launch(row['id'], 1, str(sandbox[1]))


def test_write_only_provider_edits_clear_and_rollback_without_exposing_keys(sandbox, monkeypatch):
    import agent_profile_provider as provider
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    pid = profiles.create('api-work', 'claude')['id']
    plan = provider.preview(pid, 1, {'api_key':'test-private-key','base_url':'https://api.example.invalid'})
    assert 'test-private-key' not in json.dumps(plan)
    result = provider.apply(plan['token'])
    assert 'test-private-key' not in json.dumps(result)
    root = profiles.root(pid)
    assert json.loads((root/'.farshell-provider.json').read_text())['api_key'] == 'test-private-key'
    assert 'test-private-key' not in (root/'settings.json').read_text()
    clear = provider.preview(pid, 2, {'clear':True})
    provider.apply(clear['token'])
    assert not (root/'.farshell-provider.json').exists()
    job = next(j for j in profiles.listing()['jobs'] if j['profile_id'] == pid and j['kind'] == 'provider-edit')
    migration.rollback(pid, 3, job['id'])
    assert json.loads((root/'.farshell-provider.json').read_text())['api_key'] == 'test-private-key'
    assert 'apiKeyHelper' in json.loads((root/'settings.json').read_text())


def test_authentication_change_refuses_live_native_root(sandbox, monkeypatch):
    import hook_identity
    import agent_config_context
    pid = profiles.create('busy-auth', 'claude')['id']
    monkeypatch.setattr(hook_identity, '_ps_table', lambda: {100:(1,100,100,'claude')})
    monkeypatch.setattr(agent_config_context, 'process_args', lambda _: (['claude'],{'CLAUDE_CONFIG_DIR':str(profiles.root(pid))}))
    with pytest.raises(ValueError):
        runtime.require_inactive(pid)


def test_test_profile_adoption_keeps_native_root_and_disables_forced_expiry(sandbox):
    row = profiles.create('adopted', 'claude', purpose='test')
    root = profiles.root(row['id'])
    before = (root/'settings.json').read_bytes()
    adopted = profiles.adopt(row['id'], 1)
    assert adopted['purpose'] == 'work' and adopted['revision'] == 2
    assert profiles.root(row['id']) == root
    assert (root/'settings.json').read_bytes() == before
    from usage.profiles import prepare_refresh_test
    with pytest.raises(ValueError):
        prepare_refresh_test(row['id'])


def test_unrelated_account_concurrency_never_certifies_migrated_profile(sandbox, monkeypatch):
    home, _ = sandbox
    folder = home / '.clauth' / 'profiles' / 'sample'
    folder.mkdir(parents=True)
    (folder / 'config.toml').write_text('')
    source = migration.source_list()['sources'][0]
    target = profiles.create('migration-target', 'claude')
    migration.config_apply(migration.config_preview(target['id'], 1, source['id'])['token'])
    first = profiles.create('unrelated-first', 'claude')
    second = profiles.create('unrelated-second', 'claude', account_id=first['account_id'])
    now = time.time()
    with profiles.database() as db:
        db.execute('CREATE TABLE observations (profile_id TEXT, subject TEXT, last_at REAL, last_fingerprint TEXT)')
        for index, row in enumerate((first, second)):
            name = 'profile-' + str(index) * 16
            db.execute('INSERT INTO sessions (name,profile_id,created,profile_revision,started_at,state) VALUES (?,?,?,?,?,?)', (name, row['id'], 'fixture', 1, now, 'running'))
            db.execute('INSERT INTO observations VALUES (?,?,?,?)', (row['id'], 'same-proven-subject', now, 'current-auth'))
    monkeypatch.setattr(runtime, 'session_view', lambda name: {'profile_id': first['id'] if name.endswith('0') else second['id'], 'state': 'running', 'applied_revision': 1, 'desired_revision': 1})
    monkeypatch.setattr(runtime, 'authentication_fingerprint', lambda _: 'current-auth')
    monkeypatch.setattr(migration, '_legacy_runtime_state', lambda: 'absent')
    result = migration.retirement_report()
    assert result['concurrency_certified'] is True
    assert result['ready_to_retire'] is False
    assert 'same-account-concurrency-not-certified' in result['profiles'][0]['candidates'][0]['blockers']


def test_profile_lifecycle_preserves_native_root_login_and_config_revision(sandbox, monkeypatch):
    row = profiles.create('before', 'claude')
    root = profiles.root(row['id'])
    auth = root / '.credentials.json'
    auth.write_text('{"fixture":"preserved"}')
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    renamed = profiles.metadata_change(row['id'], 1, 1, 'rename', 'after')
    assert renamed['name'] == 'after' and renamed['revision'] == 1 and renamed['metadata_revision'] == 2
    with pytest.raises(ValueError):
        profiles.metadata_change(row['id'], 1, 1, 'archive')
    archived = profiles.metadata_change(row['id'], 1, 2, 'archive')
    assert not profiles.listing()['profiles'] and profiles.listing()['archived_profiles'][0]['id'] == row['id']
    assert profiles.root(row['id']) == root and auth.read_text() == '{"fixture":"preserved"}'
    with pytest.raises(ValueError):
        runtime.environment(row['id'])
    with pytest.raises(ValueError):
        profiles.require_revision(archived, 1)
    restored = profiles.metadata_change(row['id'], 1, 3, 'restore')
    assert restored['archived_at'] is None and restored['revision'] == 1
    assert auth.read_text() == '{"fixture":"preserved"}'


def test_archive_refuses_live_partial_or_invalid_native_files(sandbox, monkeypatch):
    row = profiles.create('safe', 'claude')
    def live(_):
        raise profiles.Refused('fixture live')
    monkeypatch.setattr(runtime, 'require_inactive', live)
    with pytest.raises(ValueError):
        profiles.metadata_change(row['id'], 1, 1, 'archive')
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    job = profiles.job_begin(row['id'], 'fixture')
    profiles.job_finish(job, 'partial', {})
    with pytest.raises(ValueError):
        profiles.metadata_change(row['id'], 1, 1, 'archive')
    profiles.job_finish(job, 'interrupted', {})
    profiles.metadata_change(row['id'], 1, 1, 'archive')
    (profiles.root(row['id']) / 'settings.json').write_text('{invalid')
    with pytest.raises(ValueError):
        profiles.metadata_change(row['id'], 1, 2, 'restore')
    assert profiles.get(row['id'])['archived_at'] is not None


def test_review_before_archive_restore_cannot_be_replayed(sandbox, monkeypatch):
    from routes import agent_profiles as routes
    row = profiles.create('epoch', 'claude', purpose='test')
    plan = routes._read('preview', {'operation': 'adopt', 'payload': {'profile_id': row['id'], 'revision': 1}}, 'fixture-actor')
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    profiles.metadata_change(row['id'], 1, 1, 'archive')
    profiles.metadata_change(row['id'], 1, 2, 'restore')
    with pytest.raises(ValueError):
        routes._apply({'token': plan['token']}, 'fixture-actor')


@pytest.mark.parametrize('tool,filename', [('claude', 'settings.json'), ('codex', 'hooks.json'), ('codex', 'config.toml')])
def test_hook_toggle_preserves_groups_options_order_and_native_trust(sandbox, tool, filename):
    import tomllib
    row = profiles.create('hooks-toggle', tool)
    root = profiles.root(row['id'])
    path = root / filename
    custom = [{'matcher': 'sample:*', 'customOption': 'preserve', 'hooks': [
        {'type': 'command', 'command': 'sample-a', 'timeout': 21},
        {'type': 'command', 'command': 'sample-b', 'timeout': 22}]},
        {'matcher': 'another:*', 'hooks': [{'type': 'command', 'command': 'sample-c'}]}]
    if filename.endswith('.json'):
        data = json.loads(path.read_text()) if path.exists() else {}
        data.setdefault('hooks', {}).setdefault('Stop', []).extend(custom)
        path.write_text(json.dumps(data))
        parse = json.loads
    else:
        path.write_text(path.read_text() + '\n# sample-user-comment\n' + migration._toml({'hooks': {'Stop': custom}}))
        # A dotted inline assignment after existing tables would be relative;
        # append actual TOML array tables instead.
        original = path.read_text().split('\n# sample-user-comment')[0]
        path.write_text(original + '\n# sample-user-comment\n[[hooks.Stop]]\nmatcher = "sample:*"\ncustomOption = "preserve"\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "sample-a"\ntimeout = 21\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "sample-b"\ntimeout = 22\n[[hooks.Stop]]\nmatcher = "another:*"\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "sample-c"\n')
        parse = tomllib.loads
    before = parse(path.read_text())
    for _ in range(3):
        target = next(r for r in hooks.inventory(row['id'])['hooks'] if not r['owned'] and r['enabled'])
        plan = hooks.preview(row['id'], profiles.get(row['id'])['revision'], hook_id=target['id'], enabled=False)
        hooks.apply(plan['token'])
    remaining = parse(path.read_text())['hooks']['Stop']
    assert all(h.get('command', '').startswith('sample-') is False for g in remaining for h in g['hooks'])
    disabled = [r for r in hooks.inventory(row['id'])['hooks'] if not r['owned']]
    for index in (1, 0, 2):
        target = disabled[index]
        hooks.apply(hooks.preview(row['id'], profiles.get(row['id'])['revision'], hook_id=target['id'], enabled=True)['token'])
    assert parse(path.read_text()) == before
    assert not json.loads((root / '.farshell-hooks-disabled.json').read_text())
    if filename.endswith('.toml'):
        import codex_hooks
        for marker in (codex_hooks.BEGIN, codex_hooks.STATE_BOUNDARY, codex_hooks.END, '# sample-user-comment'):
            assert path.read_text().count(marker) == 1


def test_hook_restore_refuses_external_edits_and_protects_owned_hook(sandbox):
    row = profiles.create('conflict', 'claude')
    root = profiles.root(row['id'])
    hooks.apply(hooks.preview(row['id'], 1, 'Stop', 'sample-notify')['token'])
    owned = next(r for r in hooks.inventory(row['id'])['hooks'] if r['owned'])
    with pytest.raises(ValueError):
        hooks.preview(row['id'], 2, hook_id=owned['id'], enabled=False)
    own = next(r for r in hooks.inventory(row['id'])['hooks'] if not r['owned'])
    hooks.apply(hooks.preview(row['id'], 2, hook_id=own['id'], enabled=False)['token'])
    disabled = next(r for r in hooks.inventory(row['id'])['hooks'] if not r['owned'])
    data = json.loads((root / 'settings.json').read_text())
    data['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': 'external-edit'}]})
    (root / 'settings.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        hooks.preview(row['id'], 3, hook_id=disabled['id'], enabled=True)
    assert 'external-edit' in (root / 'settings.json').read_text()


def test_hook_partial_write_is_journaled_and_rollback_restores_both_files(sandbox, monkeypatch):
    row = profiles.create('partial-hook', 'claude')
    root = profiles.root(row['id'])
    before = (root / 'settings.json').read_bytes()
    plan = hooks.preview(row['id'], 1, 'Stop', 'sample-notify')
    real_write = migration._write_bytes
    def fail_vault(path, raw, *args, **kwargs):
        if path.name == '.farshell-hooks-disabled.json':
            raise OSError('fixture-write-failure')
        return real_write(path, raw, *args, **kwargs)
    monkeypatch.setattr(migration, '_write_bytes', fail_vault)
    with pytest.raises(OSError):
        hooks.apply(plan['token'])
    assert profiles.get(row['id'])['state'] == 'partial'
    job = profiles.listing()['jobs'][0]
    assert job['state'] == 'partial' and job['kind'] == 'hook-edit'
    monkeypatch.setattr(migration, '_write_bytes', real_write)
    migration.rollback(row['id'], 2, job['id'])
    assert (root / 'settings.json').read_bytes() == before
    assert not (root / '.farshell-hooks-disabled.json').exists()


def test_private_remediation_resolves_only_matching_api_and_environment_warnings(sandbox, monkeypatch):
    from agent_profile_parity import compare
    import agent_profile_provider as provider
    home, _ = sandbox
    (home / '.claude').mkdir()
    (home / '.claude/settings.json').write_text(json.dumps({'env': {'TOKEN': 'source-env-secret'}, 'apiKeyHelper': 'clauth helper'}))
    source_root = home / '.clauth/profiles/sample'
    source_root.mkdir(parents=True)
    (source_root / 'config.toml').write_text('api_key="source-api-secret"\nbase_url="https://proxy.example.invalid"\n[env]\nFEATURE="on"\n')
    source = next(s for s in migration.sources().values() if s['kind'] == 'clauth')
    row = profiles.create('remediation', 'claude')
    migration.config_apply(migration.config_preview(row['id'], 1, source['id'])['token'])
    with profiles.database() as db:
        manifest = json.loads(db.execute("SELECT manifest FROM receipts WHERE kind='config'").fetchone()[0])
    first = compare(source, row['id'], manifest)
    assert 'api-key-login-required' in first['blockers'] and 'credentials-not-copied' in first['blockers']
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    patch = {'api_key': 'source-api-secret', 'base_url': 'https://proxy.example.invalid', 'env': {'TOKEN': 'source-env-secret', 'FEATURE': 'on'}}
    provider.apply(provider.preview(row['id'], 2, patch)['token'])
    result = compare(source, row['id'], manifest)
    assert result['blockers'] == []
    assert {'api-key-login-required', 'credentials-not-copied', 'profile-environment-requires-setup'} <= set(result['resolved_warnings'])
    patch['env']['TOKEN'] = 'different-private-value'
    provider.apply(provider.preview(row['id'], 3, patch)['token'])
    assert 'credentials-not-copied' in compare(source, row['id'], manifest)['blockers']
    assert 'private-value' not in json.dumps(result) and 'source-api-secret' not in json.dumps(result)


def test_plugin_name_alone_cannot_resolve_migration_warning(sandbox):
    from agent_profile_parity import compare
    import shutil
    home, _ = sandbox
    base = home / '.claude'
    base.mkdir()
    selector = 'sample@fixture'
    (base / 'settings.json').write_text(json.dumps({'enabledPlugins': {selector: True}}))
    original = base / 'plugins/cache/fixture/sample/1'
    (original / '.claude-plugin').mkdir(parents=True)
    (original / '.claude-plugin/plugin.json').write_text('{"name":"sample"}')
    (original / 'content.txt').write_text('original-content')
    (base / 'plugins/installed_plugins.json').write_text(json.dumps({'plugins': {selector: [{'scope': 'user', 'installPath': str(original)}]}}))
    source = next(s for s in migration.sources().values() if s['tool'] == 'claude')
    row = profiles.create('plugin-parity', 'claude')
    migration.config_apply(migration.config_preview(row['id'], 1, source['id'])['token'])
    with profiles.database() as db:
        manifest = json.loads(db.execute("SELECT manifest FROM receipts WHERE kind='config'").fetchone()[0])
    assert 'plugins-reinstall-required' in compare(source, row['id'], manifest)['blockers']
    root = profiles.root(row['id'])
    target = root / 'plugins/cache/fixture/sample/1'
    shutil.copytree(original, target)
    (root / 'plugins/installed_plugins.json').write_text(json.dumps({'plugins': {selector: [{'scope': 'user', 'installPath': str(target)}]}}))
    settings = json.loads((root / 'settings.json').read_text())
    settings['enabledPlugins'] = {selector: True}
    (root / 'settings.json').write_text(json.dumps(settings))
    assert 'plugins-reinstall-required' not in compare(source, row['id'], manifest)['blockers']
    (target / 'content.txt').write_text('different-content')
    assert 'plugins-reinstall-required' in compare(source, row['id'], manifest)['blockers']


def _plugin_archive(entries):
    import io
    import tarfile
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, value, kind in entries:
            entry = tarfile.TarInfo('sample-' + 'a' * 40 + '/' + name)
            entry.mode = 0o755 if name.endswith('.sh') else 0o644
            if kind == 'link':
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/outside'
                archive.addfile(entry)
            else:
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
    return output.getvalue()


def test_remote_download_is_fixed_private_reviewed_and_bound_to_profile(sandbox, monkeypatch):
    import agent_profile_download as download
    import agent_profile_plugins as plugins
    row = profiles.create('download', 'claude')
    other = profiles.create('other', 'claude')
    raw = _plugin_archive([('plugin/.claude-plugin/plugin.json', b'{"name":"sample"}', 'file'), ('plugin/run.sh', b'#!/bin/sh\nexit 0\n', 'file')])
    monkeypatch.setattr(download, '_fetch', lambda *args: raw)
    result = download.fetch(row['id'], 1, 'fixture/sample', 'a' * 40, 'plugin')
    assert result['install_review_required'] is True and profiles.get(row['id'])['revision'] == 1
    tree = download.resolve(row['id'], result['download_id'])
    assert (tree / 'run.sh').stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError):
        download.resolve(other['id'], result['download_id'])
    plan = plugins.preview(row['id'], 1, 'install', 'sample@fixture', download_id=result['download_id'])
    assert (Path(plan['staged_path']) / 'package/run.sh').read_bytes().startswith(b'#!/bin/sh')
    (tree / 'run.sh').write_text('changed after download')
    with pytest.raises(ValueError):
        download.resolve(row['id'], result['download_id'])


@pytest.mark.parametrize('entries', [
    [('plugin/../../outside', b'escape', 'file')],
    [('plugin/link', b'', 'link')],
    [('plugin/A', b'a', 'file'), ('plugin/a', b'b', 'file')],
    [('plugin/.env', b'private-sentinel', 'file')],
])
def test_remote_archive_refuses_traversal_links_collisions_and_secrets(sandbox, monkeypatch, entries):
    import agent_profile_download as download
    row = profiles.create('bad-download', 'claude')
    monkeypatch.setattr(download, '_fetch', lambda *args: _plugin_archive(entries))
    with pytest.raises(ValueError):
        download.fetch(row['id'], 1, 'fixture/sample', 'a' * 40, 'plugin')
    assert profiles.listing()['jobs'][0]['state'] == 'failed'
    assert profiles.get(row['id'])['state'] == 'ready'
    assert not list((profiles.directory() / 'plugin-downloads').iterdir())


@pytest.mark.parametrize('repo,commit,subdir', [('https://github.com/fixture/sample', 'a' * 40, '.'), ('fixture/sample', 'main', '.'), ('fixture/sample', 'a' * 40, '../outside')])
def test_remote_download_refuses_unpinned_and_arbitrary_sources(repo, commit, subdir):
    from agent_profile_download import validate
    with pytest.raises(ValueError):
        validate(repo, commit, subdir)


def test_dependency_bundle_failure_restores_old_content_without_touching_peers_or_login(sandbox, monkeypatch):
    import shutil
    import agent_profile_plugins as plugins
    home, _ = sandbox
    row = profiles.create('bundle-recovery', 'claude')
    root = profiles.root(row['id'])
    auth = root / '.credentials.json'
    auth.write_text('{"fixture-auth":"unchanged"}')
    source = home / 'bundle'
    (source / '.claude-plugin').mkdir(parents=True)
    (source / '.claude-plugin/marketplace.json').write_text(json.dumps({'name': 'fixture', 'plugins': [
        {'name': 'helper', 'source': './helper'}, {'name': 'main', 'source': './main', 'dependencies': ['helper@fixture']}]}))
    for name in ('helper', 'main'):
        folder = source / name
        (folder / '.claude-plugin').mkdir(parents=True)
        (folder / '.claude-plugin/plugin.json').write_text(json.dumps({'name': name, 'version': '1.0.0'}))
        (folder / 'content.txt').write_text(name + '-old')
    installed, marketplaces, failure = {}, {}, {'update': False}
    def enabled(selector, value):
        config = json.loads((root / 'settings.json').read_text())
        config.setdefault('enabledPlugins', {})[selector] = value
        (root / 'settings.json').write_text(json.dumps(config))
    def native(pid, args, **kwargs):
        if args[:3] == ['plugin', 'marketplace', 'add']:
            stage = Path(args[3])
            data = json.loads((stage / '.claude-plugin/marketplace.json').read_text())
            marketplaces[data['name']] = (stage, data)
        elif args[:2] in (['plugin', 'install'], ['plugin', 'update']):
            selector = args[2]
            name, market = selector.split('@')
            stage, data = marketplaces[market]
            entry = next(p for p in data['plugins'] if p['name'] == name)
            package = stage / entry['source']
            manifest = json.loads((package / '.claude-plugin/plugin.json').read_text())
            dest = root / 'plugins/cache' / market / name / manifest['version']
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(package, dest)
            installed[selector] = dest
            enabled(selector, True)
            if args[1] == 'update' and failure['update']:
                return 1, b''
        elif args[:2] == ['plugin', 'uninstall']:
            installed.pop(args[2], None)
        elif args[:2] == ['plugin', 'disable']:
            enabled(args[2], False)
        else:
            pytest.fail('unexpected native command')
        return 0, b'{}'
    monkeypatch.setattr(runtime, 'run', native)
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    monkeypatch.setattr(plugins, '_installed', lambda p, selector, **kwargs: installed.get(selector))
    monkeypatch.setattr(plugins, '_marketplace_peers', lambda p, market: sorted(k for k in installed if k.endswith('@' + market)))
    result = plugins.apply(plugins.preview(row['id'], 1, 'install', 'main@fixture', str(source))['token'])
    assert [p['plugin'] for p in result['progress']] == ['helper@fixture', 'main@fixture']
    peer = root / 'plugins/cache/fixture/retained/1.0.0'
    (peer / '.claude-plugin').mkdir(parents=True)
    (peer / '.claude-plugin/plugin.json').write_text('{"name":"retained","version":"1.0.0"}')
    (peer / 'content.txt').write_text('peer-stays')
    installed['retained@fixture'] = peer
    enabled('retained@fixture', True)
    marker = source / 'main/.claude-plugin/plugin.json'
    marker.write_text('{"name":"main","version":"1.1.0"}')
    (source / 'main/content.txt').write_text('main-new')
    failure['update'] = True
    plan = plugins.preview(row['id'], 2, 'update', 'main@fixture', str(source))
    assert any(p['plugin'] == 'retained@fixture' and p['operation'] == 'reuse' for p in plan['packages'])
    with pytest.raises(ValueError):
        plugins.apply(plan['token'])
    job = next(j for j in profiles.listing()['jobs'] if j['kind'] == 'plugin-update')
    assert job['state'] == 'partial' and profiles.get(row['id'])['state'] == 'partial'
    with pytest.raises(ValueError):
        profiles.require_no_pending_jobs(row['id'])
    failure['update'] = False
    restored = plugins.apply(plugins.preview(row['id'], 3, 'restore', job_id=job['id'])['token'])
    assert restored['status'] == 'ok' and profiles.get(row['id'])['state'] == 'ready'
    assert profiles.job_status(job['id'])['state'] == 'rolled-back'
    assert (installed['main@fixture'] / 'content.txt').read_text() == 'main-old'
    assert plugins._enabled(profiles.get(row['id']), 'main@fixture') is False
    assert plugins._enabled(profiles.get(row['id']), 'retained@fixture') is True
    assert (installed['retained@fixture'] / 'content.txt').read_text() == 'peer-stays'
    assert auth.read_text() == '{"fixture-auth":"unchanged"}'


def test_bundle_dependency_cycle_is_rejected_before_any_native_change(sandbox, monkeypatch):
    import agent_profile_plugins as plugins
    home, _ = sandbox
    row = profiles.create('cycle', 'claude')
    source = home / 'cycle-bundle'
    (source / '.claude-plugin').mkdir(parents=True)
    entries = []
    for name, dependency in (('first', 'second'), ('second', 'first')):
        folder = source / name
        (folder / '.claude-plugin').mkdir(parents=True)
        (folder / '.claude-plugin/plugin.json').write_text(json.dumps({'name': name}))
        entries.append({'name': name, 'source': './' + name, 'dependencies': [dependency + '@fixture']})
    (source / '.claude-plugin/marketplace.json').write_text(json.dumps({'name': 'fixture', 'plugins': entries}))
    monkeypatch.setattr(runtime, 'require_inactive', lambda _: None)
    monkeypatch.setattr(runtime, 'run', lambda *args, **kwargs: pytest.fail('must not invoke native CLI'))
    with pytest.raises(ValueError):
        plugins.preview(row['id'], 1, 'install', 'first@fixture', str(source))
    assert profiles.get(row['id'])['revision'] == 1 and not profiles.listing()['jobs']


def test_codex_install_verification_uses_actual_cache_not_marketplace_source(sandbox, monkeypatch):
    import agent_profile_plugins as plugins
    row = profiles.create('cache-check', 'codex')
    root = profiles.root(row['id'])
    cache = root / 'plugins/cache/fixture/sample/1.0.0'
    cache.mkdir(parents=True)
    (cache / 'content.txt').write_text('installed-old')
    source = profiles.directory() / 'plugin-staging' / 'sample-source'
    source.mkdir(parents=True)
    (source / 'content.txt').write_text('marketplace-new')
    body = {'installed': [{'pluginId': 'sample@fixture', 'version': '1.0.0', 'source': {'path': str(source)}}]}
    monkeypatch.setattr(runtime, 'run', lambda *args, **kwargs: (0, json.dumps(body).encode()))
    assert plugins._installed(row, 'sample@fixture') == cache
    with pytest.raises(ValueError):
        plugins._installed(row, 'sample@fixture', verify_source=True)
