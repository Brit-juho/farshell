"""Resume preserves explicit OFF/ON and never borrows another profile's policy."""
import io
import json
import os
from pathlib import Path

import pytest

from session_catalog import execution_policy as policies
from session_catalog.db import Refused

NATIVE = '6f1c1a2e-aaaa-4bbb-8ccc-0123456789ab'


def context(approval='never', sandbox='danger-full-access', reviewer='user', **over):
    return {'type': 'turn_context', 'payload': {'approval_policy': approval,
            'sandbox_policy': {'type': sandbox}, 'approvals_reviewer': reviewer, **over}}


def encoded(*records):
    return b''.join(json.dumps(r).encode() + b'\n' for r in records)


def last(*records):
    raw = encoded(*records)
    return policies._last_context(io.BytesIO(raw), len(raw))


@pytest.mark.parametrize('before,after', [('never', 'on-request'), ('on-request', 'never')])
def test_last_turn_context_wins_in_both_directions(before, after):
    policy = last(context(before), {'type': 'response_item', 'payload': {'text': 'hello'}}, context(after))
    argv = policies.codex_args(policy)
    assert policies.summary(policy)['yolo'] == (after == 'never')
    assert ('--dangerously-bypass-approvals-and-sandbox' in argv) == (after == 'never')
    if after == 'on-request':
        assert argv[:4] == ['--sandbox', 'danger-full-access', '--ask-for-approval', 'on-request']
    assert 'approvals_reviewer="user"' in argv


def test_workspace_policy_restores_all_details_and_reviewer_without_shell_interpolation():
    import shlex, tomllib
    sandbox = {'type': 'workspace-write', 'writable_roots': ['/tmp/a b', '/tmp/$(touch nope)'],
               'network_access': True, 'exclude_slash_tmp': True, 'exclude_tmpdir_env_var': True}
    policy = last(context('on-request', reviewer='auto_review', sandbox_policy=sandbox))
    argv = policies.codex_args(policy)
    assert '--dangerously-bypass-approvals-and-sandbox' not in argv
    assert 'approvals_reviewer="auto_review"' in argv
    table = next(v for v in argv if v.startswith('sandbox_workspace_write='))
    assert tomllib.loads(table)['sandbox_workspace_write'] == {k: v for k, v in sandbox.items() if k != 'type'}
    assert shlex.split(shlex.join(argv)) == argv


def test_granular_approval_is_restored_as_valid_toml():
    import tomllib
    granular = {'sandbox_approval': True, 'rules': False, 'mcp_elicitations': False,
                'skill_approval': True, 'request_permissions': False}
    policy = last(context({'granular': granular}, 'workspace-write'))
    argv = policies.codex_args(policy)
    table = next(v for v in argv if v.startswith('approval_policy='))
    assert tomllib.loads(table)['approval_policy'] == {'granular': granular}
    assert '--ask-for-approval' not in argv
    assert '--dangerously-bypass-approvals-and-sandbox' not in argv


@pytest.mark.parametrize('over', [
    {'approval_policy': 'untrusted'}, {'approval_policy': 'on-failure'}, {'approval_policy': []},
    {'approval_policy': {'granular': {'rules': True}}},
    {'approvals_reviewer': None}, {'approvals_reviewer': 'unknown'},
    {'sandbox_policy': {'type': 'read-only', 'network_access': True}},
    {'sandbox_policy': {'type': 'external-sandbox'}},
    {'sandbox_policy': {'type': 'danger-full-access', 'unrecognized': True}},
    {'sandbox_policy': {'type': 'workspace-write', 'network_access': 'false'}},
    {'sandbox_policy': {'type': 'workspace-write', 'writable_roots': ['relative']}},
    {'sandbox_policy': {'type': 'workspace-write', 'writable_roots': ['/tmp/a\nexit']}},
    {'permission_profile': {'type': 'named', 'name': 'sample'}},
])
def test_latest_unsupported_policy_does_not_revive_an_older_yolo(over):
    with pytest.raises(Refused, match='execution_policy_unsupported'):
        last(context(), context(**over))


@pytest.mark.parametrize('suffix', [b'{broken}\n', b'{"type":"turn_context"', b'[]\n'])
def test_corrupt_or_partial_last_record_never_reverts_to_an_old_policy(suffix):
    raw = encoded(context()) + suffix
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies._last_context(io.BytesIO(raw), len(raw))


def test_reverse_scan_crosses_chunk_boundaries_and_enforces_budget(monkeypatch):
    raw = encoded(context('on-request'), {'type': 'response_item', 'payload': {'text': 'a' * 150000}})
    assert policies._last_context(io.BytesIO(raw), len(raw))['approval_policy'] == 'on-request'
    monkeypatch.setattr(policies, 'MAX_SCAN', 65536)
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies._last_context(io.BytesIO(raw), len(raw))


def test_oversized_new_record_never_exposes_older_yolo(monkeypatch):
    monkeypatch.setattr(policies, 'MAX_LINE', 100)
    raw = encoded(context(), {'type': 'turn_context', 'payload': {'note': 'x' * 1000}})
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies._last_context(io.BytesIO(raw), len(raw))


@pytest.fixture
def private_policy(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(home / 'state'))
    monkeypatch.setenv('VT_BROWSE_ROOTS', str(home))
    from session_catalog import db, permission_modes
    db.snapshot(); permission_modes.forget_cache()
    yield home
    permission_modes.forget_cache()


def register(provider, root):
    from session_history import archive
    from session_catalog.db import database
    result = archive.register_source(provider, str(root))
    with database() as db:
        return dict(db.execute('SELECT * FROM sources WHERE id=?', (result['id'],)).fetchone())


def native_file(home, profile='codex', native=NATIVE, parent=None):
    root = home / profile; (root / 'sessions').mkdir(parents=True, exist_ok=True)
    path = root / 'sessions' / 'sample.jsonl'
    meta = {'id': native, 'cwd': str(home)}
    if parent:
        meta['source'] = {'subagent': {'thread_spawn': {'parent_thread_id': parent}}}
    path.write_bytes(encoded({'type': 'session_meta', 'payload': meta}, context()))
    return root, path


def test_native_identity_and_parent_are_checked_before_using_policy(private_policy):
    home = private_policy
    for native, parent in [('other', None), (NATIVE, 'parent')]:
        root, path = native_file(home, native=native, parent=parent)
        with pytest.raises(Refused, match='execution_policy_unavailable'):
            policies.read_codex(root, ['sessions/sample.jsonl'], NATIVE)


def test_policy_is_scoped_to_original_profile_and_rechecks_file_generation(private_policy):
    home = private_policy
    root, path = native_file(home)
    other, other_path = native_file(home, 'other-codex')
    other_path.write_bytes(encoded({'type': 'session_meta', 'payload': {'id': NATIVE}}, context('on-request')))
    policy, witness = policies.read_codex(root, ['sessions/sample.jsonl'], NATIVE)
    assert policies.summary(policy)['yolo'] is True
    policies.recheck(witness)
    path.write_bytes(path.read_bytes() + encoded(context('on-request')))
    with pytest.raises(Refused, match='execution_policy_changed'):
        policies.recheck(witness)
    assert policies.summary(policies.read_codex(other, ['sessions/sample.jsonl'], NATIVE)[0])['yolo'] is False


def test_conflicting_native_copies_are_not_guessed(private_policy):
    root, path = native_file(private_policy)
    (root / 'sessions' / 'copy.jsonl').write_bytes(encoded({'type': 'session_meta', 'payload': {'id': NATIVE}}, context('on-request')))
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies.read_codex(root, ['sessions/sample.jsonl', 'sessions/copy.jsonl'], NATIVE)


def test_symlink_is_not_a_permission_source(private_policy):
    root, path = native_file(private_policy)
    outside = private_policy / 'outside.jsonl'; outside.write_bytes(path.read_bytes())
    path.unlink(); path.symlink_to(outside)
    with pytest.raises((Refused, PermissionError)):
        policies.read_codex(root, ['sessions/sample.jsonl'], NATIVE)


def test_claude_scoped_default_and_invalid_modes_never_borrow_legacy_or_other_profile(private_policy):
    from session_catalog import permission_modes as modes
    from session_catalog.db import database
    first, second = str(private_policy / 'claude'), str(private_policy / 'other-claude')
    modes.record(NATIVE, 'bypassPermissions')
    with database() as db:
        assert modes.lookup(db, NATIVE, root=first) is None
    modes.record(NATIVE, 'bypassPermissions', root=first)
    modes.record(NATIVE, 'default', root=second)
    with database() as db:
        assert modes.lookup(db, NATIVE, root=first) == 'bypassPermissions'
        assert modes.lookup(db, NATIVE, root=second) == 'default'
    modes.record(NATIVE, 'new-unsupported-mode', root=first)
    with database() as db:
        assert modes.lookup(db, NATIVE, root=first) is None
        assert modes.lookup(db, NATIVE, root=second) == 'default'
    modes.invalidate(NATIVE)
    with database() as db:
        assert modes.lookup(db, NATIVE, root=second) is None


def test_resume_unknown_policy_creates_neither_operation_nor_tmux(private_policy, monkeypatch):
    from session_catalog import lifecycle
    from session_catalog.db import database
    from session_history import archive, query
    home = private_policy
    root = home / 'claude'; (root / 'projects' / 'sample').mkdir(parents=True)
    (root / 'projects' / 'sample' / 'sample.jsonl').write_bytes(encoded({'sessionId': NATIVE, 'cwd': str(home)}))
    source = register('claude', root)
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    monkeypatch.setattr(lifecycle.shutil, 'which', lambda name: '/bin/echo')
    monkeypatch.setattr(policies, 'require_cli', lambda binary: None)
    monkeypatch.setattr(lifecycle, '_launch_resume', lambda *args: pytest.fail('must not launch'))
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        lifecycle.resume(cid, 'a' * 32)
    with database() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


@pytest.mark.parametrize('approval', ['never', 'on-request'])
def test_catalog_resume_replays_original_codex_policy_and_retry_does_not_launch_twice(private_policy, monkeypatch, approval):
    from session_catalog import lifecycle
    from session_catalog.db import database
    from session_history import archive, query
    root, path = native_file(private_policy)
    path.write_bytes(encoded({'type': 'session_meta', 'payload': {'id': NATIVE, 'cwd': str(private_policy)}}, context(approval)))
    source = register('codex', root); archive.scan_source(source)
    cid = query.listing()['items'][0]['id']; calls = []
    monkeypatch.setattr(lifecycle.shutil, 'which', lambda name: '/bin/echo')
    monkeypatch.setattr(policies, 'require_cli', lambda binary: None)
    monkeypatch.setattr(lifecycle, '_launch_resume', lambda *args: calls.append(args) or False)
    opid = 'b' * 32
    with pytest.raises(Refused, match='launch_needs_review'):
        lifecycle.resume(cid, opid)
    assert len(calls) == 1
    assert ('--dangerously-bypass-approvals-and-sandbox' in calls[0][2]) == (approval == 'never')
    retry = lifecycle.resume(cid, opid)
    assert retry['state'] == 'needs_review' and len(calls) == 1
    assert retry['execution_policy']['yolo'] == (approval == 'never')
    with database() as db:
        assert db.execute('SELECT count(*) FROM operations').fetchone()[0] == 1


def test_compressed_native_context_and_bounded_decompression(private_policy, monkeypatch):
    import zstandard
    root, path = native_file(private_policy)
    zipped = path.with_suffix('.jsonl.zst')
    zipped.write_bytes(zstandard.ZstdCompressor().compress(path.read_bytes()))
    assert policies.summary(policies.read_codex(root, ['sessions/sample.jsonl.zst'], NATIVE)[0])['yolo'] is True
    monkeypatch.setattr(policies, 'MAX_SCAN', 50)
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies.read_codex(root, ['sessions/sample.jsonl.zst'], NATIVE)


def test_claude_late_older_hook_and_invalidation_cannot_replace_newer_off(private_policy):
    from session_catalog import permission_modes as modes
    from session_catalog.db import database
    root = str(private_policy / 'claude')
    modes.record(NATIVE, 'default', root=root, observed_ns=200)
    assert modes.record(NATIVE, 'bypassPermissions', root=root, observed_ns=100) is False
    modes.invalidate(NATIVE, observed_ns=150)
    with database() as db:
        assert modes.lookup(db, NATIVE, root=root) == 'default'
    modes.forget_cache()
    assert modes.record(NATIVE, 'bypassPermissions', root=root, observed_ns=199) is False
    with database() as db:
        assert modes.lookup(db, NATIVE, root=root) == 'default'
    assert modes.record(NATIVE, 'bypassPermissions', root=root, observed_ns=201) is True


def test_policy_change_during_resume_is_refused_before_tmux(private_policy, monkeypatch):
    from session_catalog import lifecycle
    from session_catalog.db import database
    from session_history import archive, query
    root, path = native_file(private_policy)
    source = register('codex', root); archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    monkeypatch.setattr(lifecycle.shutil, 'which', lambda name: '/bin/echo')
    monkeypatch.setattr(policies, 'require_cli', lambda binary: None)
    original = lifecycle.inventory.inspect_workspace
    def changed(cwd):
        path.write_bytes(path.read_bytes() + encoded(context('on-request')))
        return original(cwd)
    monkeypatch.setattr(lifecycle.inventory, 'inspect_workspace', changed)
    monkeypatch.setattr(lifecycle, '_launch_resume', lambda *args: pytest.fail('must not launch'))
    with pytest.raises(Refused, match='execution_policy_changed'):
        lifecycle.resume(cid, 'c' * 32)
    with database() as db:
        assert db.execute('SELECT state FROM operations').fetchone()[0] == 'needs_review'


@pytest.mark.parametrize('source', [{'subagent': 'review'}, {'subagent': 'compact'}, 'subagent'])
def test_child_without_explicit_parent_id_is_not_a_resume_policy(private_policy, source):
    root, path = native_file(private_policy)
    path.write_bytes(encoded({'type': 'session_meta', 'payload': {'id': NATIVE, 'source': source}}, context()))
    with pytest.raises(Refused, match='execution_policy_unavailable'):
        policies.read_codex(root, ['sessions/sample.jsonl'], NATIVE)


@pytest.mark.parametrize('field', ['permissions', 'approval_policy_v2', 'sandbox_permissions'])
def test_unrecognized_new_policy_fields_are_not_silently_dropped(field):
    with pytest.raises(Refused, match='execution_policy_unsupported'):
        last(context(**{field: {'custom': True}}))


@pytest.mark.parametrize('version,accepted', [('codex-cli 0.163.0', False), ('codex-cli 0.164.0', True),
    ('codex-cli 0.164.1', True), ('codex-cli 0.164.0-beta', False), ('not-a-version', False)])
def test_cli_capability_floor_refuses_unknown_or_older_runtime(monkeypatch, version, accepted):
    from types import SimpleNamespace
    monkeypatch.setattr(policies.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=version))
    if accepted:
        policies.require_cli('/bin/codex')
    else:
        with pytest.raises(Refused, match='execution_policy_unsupported'):
            policies.require_cli('/bin/codex')
