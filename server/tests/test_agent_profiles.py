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
    plan = plugins.preview(pid, 1, 'install', 'sample@sample-market', str(source))
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
    monkeypatch.setattr(runtime, 'session_status', lambda name: {'profile_id': first['id'] if name.endswith('0') else second['id'], 'state': 'running', 'applied_revision': 1, 'desired_revision': 1})
    monkeypatch.setattr(runtime, 'authentication_fingerprint', lambda _: 'current-auth')
    monkeypatch.setattr(migration, '_legacy_runtime_state', lambda: 'absent')
    result = migration.retirement_report()
    assert result['concurrency_certified'] is True
    assert result['ready_to_retire'] is False
    assert 'same-account-concurrency-not-certified' in result['profiles'][0]['candidates'][0]['blockers']
