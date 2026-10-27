"""Profile-scoped hook inventory and source-preserving reviewed edits."""
from __future__ import annotations

import json
import re
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import mcp_plans
from i18n import tr


def _read(profile_id, *, hook_file=False):
    import tomllib
    profile = profiles.get(profile_id)
    root = profiles.root(profile_id)
    filename = "settings.json" if profile["tool"] == "claude" else "hooks.json" if hook_file else "config.toml"
    path = fsguard.profile_file(root, filename)
    raw = fsguard.read_profile_bytes(root, filename) if path.exists() else b"{}" if filename.endswith('.json') else b""
    data = json.loads(raw) if filename.endswith('.json') else tomllib.loads(raw.decode())
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    return profile, path, raw, data


def _vault(profile_id):
    path = fsguard.profile_file(profiles.root(profile_id), '.farshell-hooks-disabled.json')
    raw = fsguard.read_profile_bytes(path.parent, path.name) if path.exists() else None
    data = json.loads(raw) if raw is not None else {}
    if not isinstance(data, dict) or len(data) > 256:
        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    for key, row in data.items():
        if not isinstance(key, str) or ':' not in key or key.split(':', 1)[0] not in ('settings.json', 'hooks.json', 'config.toml') or not key.split(':', 1)[1] or key.endswith(':state') or not isinstance(row, dict) or set(row) != {'base', 'disabled', 'active_hash'}:
            raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
        _validate_groups(row['base'])
        if not isinstance(row['disabled'], list) or len(row['disabled']) != len(set(map(str, row['disabled']))):
            raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
        positions = {f'{gi}:{hi}' for gi, g in enumerate(row['base']) for hi, _ in enumerate(g['hooks'])}
        if any(not isinstance(v, str) or v not in positions for v in row['disabled']) or row['active_hash'] != _hash(_render(row['base'], row['disabled'])):
            raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    return path, raw, data


def _validate_groups(groups):
    if not isinstance(groups, list) or len(groups) > 1000:
        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('hooks'), list) or len(group['hooks']) > 1000 or any(not isinstance(h, dict) for h in group['hooks']):
            raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))


def _hash(value):
    return profiles.digest(json.dumps(value, sort_keys=True).encode())


def _render(base, disabled):
    result = []
    for gi, group in enumerate(base):
        hooks = [h for hi, h in enumerate(group['hooks']) if f'{gi}:{hi}' not in disabled]
        if hooks or not group['hooks']:
            result.append({**group, 'hooks': hooks})
    return result


def _event(document, vault, source, event):
    groups = document.get('hooks', {}).get(event, [])
    _validate_groups(groups)
    entry = vault.get(source + ':' + event)
    if entry is not None:
        if entry['active_hash'] != _hash(groups):
            raise profiles.Refused(tr("훅 설정이 바뀌었습니다. 다시 검토하세요"))
        return entry['base'], set(entry['disabled'])
    return groups, set()


def inventory(profile_id):
    profile, path, raw, data = _read(profile_id)
    import claude_hooks
    import codex_hooks
    installer = claude_hooks if profile['tool'] == 'claude' else codex_hooks
    owned = {installer.hook_command(event) for event in installer.EVENTS}
    _, _, vault = _vault(profile_id)
    items = []
    documents = [(path, data)]
    if profile['tool'] == 'codex':
        _, extra_path, _, extra_data = _read(profile_id, hook_file=True)
        documents.append((extra_path, extra_data))
    for source_path, document in documents:
        events = set(document.get('hooks', {})) | {k.split(':', 1)[1] for k in vault if k.startswith(source_path.name + ':')}
        for event in sorted(events - {'state'}):
            groups, disabled = _event(document, vault, source_path.name, event)
            for gi, group in enumerate(groups):
                for hi, hook in enumerate(group['hooks']):
                    digest = _hash([source_path.name, event, gi, hi, hook])
                    ours = hook.get('command') in owned
                    if ours and f'{gi}:{hi}' in disabled:
                        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
                    items.append({'id': digest[:32], 'event': event, 'type': hook.get('type', 'unknown'),
                                  'owned': ours, 'writable': not ours, 'source': source_path.name, 'group': gi,
                                  'index': hi, 'sha256': digest, 'enabled': f'{gi}:{hi}' not in disabled})
    return {'profile_id': profile_id, 'revision': profile['revision'], 'file_revision': profiles.digest(raw), 'hooks': items,
            'note': tr("공통·프로젝트·플러그인 훅은 각 출처에서 별도로 상속될 수 있습니다")}


def _native_text(path, raw, data, event, groups):
    if path.suffix == '.json':
        data.setdefault('hooks', {})[event] = groups
        return json.dumps(data, ensure_ascii=False, indent=2).encode()
    try:
        import tomlkit
    except ImportError:
        raise profiles.Refused(tr("TOML 훅 편집에는 tomlkit 설치가 필요합니다")) from None
    import tomllib
    import codex_hooks
    text = raw.decode()
    doc = tomlkit.parse(text)
    # Preserve original TOML table objects (and attached comments) whenever
    # their semantic contents are unchanged, including FarShell-owned groups.
    old = doc.get('hooks', {}).get(event, [])
    available = list(old)
    new = tomlkit.aot()
    for group in groups:
        match = next((i for i, item in enumerate(available) if item.unwrap() == group), None)
        if match is not None:
            new.append(available.pop(match))
        else:
            new.append(tomlkit.item(group))
    doc['hooks'][event] = new
    result = tomlkit.dumps(doc)
    expected = json.loads(json.dumps(data))
    expected.setdefault('hooks', {})[event] = groups
    if tomllib.loads(result) != expected:
        raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    for marker in (codex_hooks.BEGIN, codex_hooks.STATE_BOUNDARY, codex_hooks.END):
        if text.count(marker) != result.count(marker):
            raise profiles.Refused(tr("훅 설정 형식이 올바르지 않습니다"))
    return result.encode()


def preview(profile_id, revision, event=None, command=None, matcher='*', timeout=10, remove_id=None, hook_id=None, enabled=None):
    import copy
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    profiles.require_no_pending_jobs(profile_id)
    vp, vr, vault = _vault(profile_id)
    if remove_id is not None or hook_id is not None:
        if remove_id is not None and hook_id is not None or any(v is not None for v in (event, command)):
            raise profiles.Refused(tr("훅 이벤트 또는 명령이 올바르지 않습니다"))
        identity = remove_id if remove_id is not None else hook_id
        profiles.identifier(identity)
        row = next((r for r in inventory(profile_id)['hooks'] if r['id'] == identity), None)
        if row is None or not row['writable']:
            raise profiles.Refused(tr("이 훅은 제거할 수 없습니다"))
        if hook_id is not None and type(enabled) is not bool or remove_id is not None and enabled is not None:
            raise profiles.Refused(tr("훅 이벤트 또는 명령이 올바르지 않습니다"))
        _, path, raw, data = _read(profile_id, hook_file=row['source'] == 'hooks.json')
        event = row['event']
    else:
        if enabled is not None:
            raise profiles.Refused(tr("훅 이벤트 또는 명령이 올바르지 않습니다"))
        import claude_hooks
        import codex_hooks
        supported = (claude_hooks if profile['tool'] == 'claude' else codex_hooks).EVENTS
        if event not in supported or not isinstance(command, str) or not 1 <= len(command) <= 8192 or any(ord(c) < 32 for c in command):
            raise profiles.Refused(tr("훅 이벤트 또는 명령이 올바르지 않습니다"))
        if 'runtime-' in command or not isinstance(matcher, str) or not 1 <= len(matcher) <= 256 or any(ord(c) < 32 for c in matcher) or type(timeout) is not int or not 1 <= timeout <= 600:
            raise profiles.Refused(tr("훅 경로·matcher·timeout을 확인하세요"))
        _, path, raw, data = _read(profile_id, hook_file=True)
    base, disabled = _event(data, vault, path.name, event)
    base = copy.deepcopy(base)
    if hook_id is not None:
        position = f"{row['group']}:{row['index']}"
        disabled.discard(position) if enabled else disabled.add(position)
    elif remove_id is not None:
        gi, hi = row['group'], row['index']
        del base[gi]['hooks'][hi]
        disabled = {f'{a}:{b - (1 if a == gi and b > hi else 0)}' for value in disabled for a, b in [map(int, value.split(':'))] if (a, b) != (gi, hi)}
        # Retain the now-empty group to avoid renumbering other stored groups.
    else:
        base.append({'matcher': matcher, 'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]})
    rendered = _render(base, disabled)
    key = path.name + ':' + event
    if disabled:
        vault[key] = {'base': base, 'disabled': sorted(disabled), 'active_hash': _hash(rendered)}
    else:
        vault.pop(key, None)
    writes = {path.name: _native_text(path, raw, data, event, rendered), vp.name: json.dumps(vault, ensure_ascii=False, indent=2).encode()}
    before = {path.name: profiles.digest(raw) if path.exists() else None, vp.name: profiles.digest(vr) if vr is not None else None}
    plan = {'profile_id': profile_id, 'revision': revision, 'before': before,
            'writes': {name: value.decode() for name, value in writes.items()}}
    return {**mcp_plans.issue('profile-hook', plan), 'event': event, 'removing': remove_id is not None, 'enabled': enabled,
            'warning': tr("훅은 이 계정의 권한으로 명령을 실행합니다")}


def apply(token):
    from contextlib import ExitStack
    from agent_profile_migration import _write_bytes
    plan = mcp_plans.take(token, 'profile-hook')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    pid = plan['profile_id']
    profile = profiles.get(pid)
    profiles.require_revision(profile, plan['revision'])
    profiles.require_no_pending_jobs(pid)
    root = profiles.root(pid)
    files = []
    # One lock order for native + vault files. The HTTP operation lock also
    # serializes hooks against migration/provider/plugin operations.
    with ExitStack() as stack:
        for name in sorted(plan['writes']):
            stack.enter_context(config_io.locked(fsguard.profile_file(root, name)))
        for name, expected in plan['before'].items():
            path = fsguard.profile_file(root, name)
            actual = profiles.digest(fsguard.read_profile_bytes(root, name)) if path.exists() else None
            if actual != expected:
                raise profiles.Refused(tr("훅 설정이 바뀌었습니다. 다시 검토하세요"))
        jid = profiles.job_begin(pid, 'hook-edit')
        try:
            for name, text in plan['writes'].items():
                path = fsguard.profile_file(root, name)
                if path.exists():
                    backup = fsguard.private_profile_directory(profiles.directory() / 'backups' / jid)
                    _write_bytes(backup / (profiles.digest(name.encode()) + '.bak'), fsguard.read_profile_bytes(root, name))
                raw = text.encode()
                files.append({'path': name, 'before_sha256': plan['before'][name], 'sha256': profiles.digest(raw)})
                profiles.job_finish(jid, 'running', {'files': files})
                _write_bytes(path, raw)
            return profiles.complete_job(pid, plan['revision'], jid, {'status': 'ok', 'job_id': jid, 'files': files, 'restart_required': True})
        except BaseException:
            if files:
                profiles.bump(pid, plan['revision'], 'partial')
            profiles.job_finish(jid, 'partial' if files else 'failed', {'files': files, 'recovery_required': bool(files)})
            raise


def recover(profile_id, revision):
    """Explicit recovery: re-parse native configuration, never replay a job."""
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    from agent_profile_runtime import validate_native_files, environment
    validate_native_files(profile_id)
    environment(profile_id)
    with profiles.database() as db:
        pending = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE profile_id=? AND state IN ('running','partial','partial-rollback')", (profile_id,))]
    for job in pending:
        if job['kind'].startswith('plugin-'):
            metadata = json.loads(job['result'])
            plugin = metadata.get('plugin')
            if not plugin:
                raise profiles.Refused(tr("중단된 플러그인 작업은 네이티브 상태를 먼저 복구해야 합니다"))
            from agent_profile_plugins import _installed
            if _installed(profile, plugin) is not None:
                _, _, _, config = _read(profile_id)
                enabled = config.get('enabledPlugins', {}).get(plugin) if profile['tool'] == 'claude' else config.get('plugins', {}).get(plugin, {}).get('enabled')
                if enabled is not False:
                    raise profiles.Refused(tr("중단된 플러그인을 먼저 끈 뒤 복구를 확인하세요"))
    with profiles.database() as db:
        db.execute("UPDATE jobs SET state='interrupted' WHERE profile_id=? AND state IN ('running','partial','partial-rollback')", (profile_id,))
    profiles.bump(profile_id, revision)
    return {"status": "ok", "note": tr("설정을 다시 읽었습니다. 중단된 작업은 자동 재실행하지 않습니다")}
