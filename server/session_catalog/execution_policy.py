"""Restore recorded Codex policy, never current defaults or arbitrary transcript flags."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
import re
import subprocess

import fsguard
import zstandard
from session_history import adapters
from .db import Refused

MAX_SCAN = 64 * 1024 * 1024
MAX_LINE = 1024 * 1024
REVIEWERS = frozenset({'user', 'auto_review', 'guardian_subagent'})
GRANULAR = frozenset({'sandbox_approval', 'rules', 'mcp_elicitations', 'request_permissions', 'skill_approval'})


def require_cli(binary):
    """Do not silently drop reviewer/config overrides on an older native CLI."""
    try:
        env = {key: value for key, value in os.environ.items()
               if key not in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN')}
        result = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=3, env=env)
        match = re.fullmatch(r'codex-cli ([0-9]+)\.([0-9]+)\.([0-9]+)', result.stdout.strip())
        if result.returncode or not match or tuple(map(int, match.groups())) < (0, 164, 0):
            _unsupported()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        _unsupported()


def _unsupported():
    raise Refused('execution_policy_unsupported')


def validate_codex(payload):
    fields = {'approval_policy', 'approvals_reviewer', 'sandbox_policy', 'permission_profile'}
    if any(not isinstance(key, str) or (key.startswith(('approval', 'sandbox', 'permission')) and key not in fields)
           for key in payload):
        _unsupported()
    approval = payload.get('approval_policy')
    if isinstance(approval, dict):
        granular = approval.get('granular')
        if (set(approval) != {'granular'} or not isinstance(granular, dict)
                or set(granular) - GRANULAR
                or not {'sandbox_approval', 'rules', 'mcp_elicitations'} <= set(granular)
                or any(type(v) is not bool for v in granular.values())):
            _unsupported()
        approval = {'granular': {key: granular.get(key, False) for key in sorted(GRANULAR)}}
    elif not isinstance(approval, str) or approval not in ('never', 'on-request'):
        _unsupported()
    reviewer = payload.get('approvals_reviewer')
    if not isinstance(reviewer, str) or reviewer not in REVIEWERS:
        _unsupported()
    profile = payload.get('permission_profile')
    if profile is not None and profile != {'type': 'disabled'}:
        # Named/custom filesystem profiles cannot be faithfully reduced to legacy CLI flags.
        _unsupported()
    sandbox = payload.get('sandbox_policy')
    if not isinstance(sandbox, dict):
        _unsupported()
    kind = sandbox.get('type')
    if kind == 'danger-full-access':
        if set(sandbox) != {'type'}:
            _unsupported()
        sandbox = {'type': kind}
    elif kind == 'read-only':
        if set(sandbox) - {'type', 'network_access'} or sandbox.get('network_access', False) is not False:
            _unsupported()
        sandbox = {'type': kind}
    elif kind == 'workspace-write':
        fields = ('network_access', 'exclude_tmpdir_env_var', 'exclude_slash_tmp')
        if set(sandbox) - {'type', 'writable_roots', *fields}:
            _unsupported()
        if any(type(sandbox.get(key, False)) is not bool for key in fields):
            _unsupported()
        roots = sandbox.get('writable_roots', [])
        if (not isinstance(roots, list) or len(roots) > 64
                or any(not isinstance(path, str) or not Path(path).is_absolute() or len(path) > 4096
                       or any(ord(char) < 32 or ord(char) == 127 for char in path) for path in roots)):
            _unsupported()
        try:
            for path in roots:
                path.encode('utf-8', errors='strict')
        except UnicodeError:
            _unsupported()
        sandbox = {'type': kind, 'writable_roots': list(roots), **{key: sandbox.get(key, False) for key in fields}}
    else:
        _unsupported()
    return {'approval_policy': approval, 'approvals_reviewer': reviewer, 'sandbox_policy': sandbox}


def _toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(key + '=' + _toml(item) for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def codex_args(policy):
    policy = validate_codex(policy)
    approval, sandbox = policy['approval_policy'], policy['sandbox_policy']
    yolo = approval == 'never' and sandbox['type'] == 'danger-full-access'
    args = ['--dangerously-bypass-approvals-and-sandbox'] if yolo else ['--sandbox', sandbox['type']]
    if not yolo:
        if isinstance(approval, str):
            args += ['--ask-for-approval', approval]
        else:
            args += ['-c', 'approval_policy=' + _toml(approval)]
    args += ['-c', 'approvals_reviewer=' + _toml(policy['approvals_reviewer'])]
    if sandbox['type'] == 'workspace-write':
        args += ['-c', 'sandbox_workspace_write=' + _toml({k: v for k, v in sandbox.items() if k != 'type'})]
    return args


def summary(policy):
    return {'provider': 'codex', 'yolo': policy['approval_policy'] == 'never'
            and policy['sandbox_policy']['type'] == 'danger-full-access'}


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _last_context(stream, size):
    """Walk backwards with bounded reads; malformed/new unsupported records never revive an old mode."""
    if not size:
        raise Refused('execution_policy_unavailable')
    stream.seek(size - 1)
    if stream.read(1) != b'\n':
        raise Refused('execution_policy_unavailable')
    position, lower, pending = size, max(0, size - MAX_SCAN), b''
    while position > lower:
        start = max(lower, position - 64 * 1024)
        stream.seek(start)
        parts = (stream.read(position - start) + pending).split(b'\n')
        pending = parts.pop(0)
        for line in reversed(parts):
            if not line:
                continue
            if len(line) > MAX_LINE:
                raise Refused('execution_policy_unavailable')
            policy = _context(line)
            if policy is not None:
                return policy
        if len(pending) > MAX_LINE:
            raise Refused('execution_policy_unavailable')
        position = start
    if lower == 0 and pending:
        policy = _context(pending)
        if policy is not None:
            return policy
    raise Refused('execution_policy_unavailable')


def _context(line):
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        raise Refused('execution_policy_unavailable') from None
    if not isinstance(row, dict):
        raise Refused('execution_policy_unavailable')
    if row.get('type') != 'turn_context':
        return None
    payload = row.get('payload')
    if not isinstance(payload, dict):
        _unsupported()
    return validate_codex(payload)


def _parent_identity(probe, relative, native):
    identity = adapters.identity(probe, 'codex', relative)
    if not identity or identity['native_id'] != native or identity['parent_native_id']:
        return False
    metas = []
    for line in probe.splitlines()[:256]:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict) and row.get('type') == 'session_meta':
            metas.append(row.get('payload'))
    if len(metas) != 1 or not isinstance(metas[0], dict):
        return False
    meta = metas[0]
    # Review/compact subagents need not carry a thread_spawn parent ID.
    return (meta.get('id') == native and not isinstance(meta.get('source'), dict)
            and meta.get('source') != 'subagent' and meta.get('parent_thread_id') is None)


def read_codex(root, relatives, native):
    """Only catalog-bound native files in the original profile can supply policy."""
    policies, witnesses = [], []
    for relative in relatives:
        if fsguard.history_capture_kind(relative, 'codex') != 'transcript':
            continue
        try:
            path = fsguard.history_source_path(root, relative, 'codex')
            with fsguard.open_subagent_file(path) as stream:
                before = os.fstat(stream.fileno())
                decoded = None
                if relative.endswith('.zst'):
                    with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(stream, closefd=False) as reader:
                        decoded = reader.read(MAX_SCAN + 1)
                    if len(decoded) > MAX_SCAN:
                        raise Refused('execution_policy_unavailable')
                probe = decoded[:MAX_LINE] if decoded is not None else stream.read(MAX_LINE)
                if not _parent_identity(probe, relative, native):
                    continue
                policy = _last_context(io.BytesIO(decoded), len(decoded)) if decoded is not None else _last_context(stream, before.st_size)
                if _stamp(before) != _stamp(os.fstat(stream.fileno())):
                    raise Refused('execution_policy_changed')
                policies.append(policy)
                witnesses.append((path, _stamp(before)))
        except (OSError, fsguard.FsDenied, zstandard.ZstdError):
            raise Refused('execution_policy_unavailable') from None
    if not policies:
        raise Refused('execution_policy_unavailable')
    if any(policy != policies[0] for policy in policies[1:]):
        raise Refused('execution_policy_unavailable')
    return policies[0], witnesses


def recheck(witnesses):
    for path, expected in witnesses:
        try:
            with fsguard.open_subagent_file(path) as stream:
                if _stamp(os.fstat(stream.fileno())) != expected:
                    raise Refused('execution_policy_changed')
        except (OSError, fsguard.FsDenied):
            raise Refused('execution_policy_changed') from None
