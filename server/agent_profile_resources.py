"""Managed Claude resources can be disabled without touching shared tooling."""
from __future__ import annotations

import json
import os
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import mcp_adapters
from i18n import tr


def tree_digest(folder):
    import hashlib
    digest = hashlib.sha256()
    count = size = 0
    for path in sorted(folder.rglob('*')):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = str(path.relative_to(folder))
        raw = fsguard.read_profile_bytes(folder, relative)
        count += 1
        size += len(raw)
        if count > 500 or size > 32 * 1024 * 1024:
            raise profiles.Refused(tr("스킬 파일 수 또는 크기가 한도를 넘었습니다"))
        digest.update(relative.encode())
        digest.update(b'\0')
        digest.update(profiles.digest(raw).encode())
        digest.update(str(path.stat().st_mode & 0o777).encode())
    return digest.hexdigest()


def _disabled(root):
    path = fsguard.profile_file(root, '.farshell-disabled-mcp.json')
    if not path.exists():
        return {}
    data = json.loads(fsguard.read_profile_bytes(root, path.name))
    if not isinstance(data, dict):
        raise profiles.Refused(tr("비활성 MCP 저장소가 올바르지 않습니다"))
    return data


def decorate(rows, ctx):
    if ctx['tool'] != 'claude':
        return
    root = Path(ctx['root'])
    pid = ctx['managed_profile']
    disabled = _disabled(root)
    native = root / '.claude.json'
    active_names = {row['name'] for row in rows if row['kind'] == 'mcp' and Path(row['source']) == native}
    if active_names.intersection(disabled):
        raise profiles.Refused(tr("MCP 반영이 중단되어 원본과 비활성 보관본이 겹칩니다. 복구가 필요합니다"))
    for name in disabled:
        if not isinstance(name, str):
            raise profiles.Refused(tr("비활성 MCP 이름이 올바르지 않습니다"))
        rows.append({'kind': 'mcp', 'native_id': name, 'name': name, 'tool': 'claude', 'scope': 'global',
                     'source': str(native), 'write_source': str(native), 'enabled': False, 'writable': True,
                     'profile': str(root), 'project': None, 'context': ctx,
                     'revision': mcp_adapters.file_revision(native), 'write_revision': mcp_adapters.file_revision(native)})
    hidden = fsguard.profile_file(root, '.farshell-disabled-skills')
    if hidden.is_dir():
        for folder in sorted(hidden.iterdir())[:500]:
            path = folder / 'SKILL.md'
            if path.is_file() and not folder.is_symlink():
                original = root / 'skills' / folder.name / 'SKILL.md'
                rows.append({'kind': 'skill', 'native_id': folder.name, 'name': folder.name, 'tool': 'claude', 'scope': 'global',
                             'source': str(original), 'write_source': str(path), 'enabled': False, 'writable': True,
                             'profile': str(root), 'project': None, 'context': ctx,
                             'revision': mcp_adapters.file_revision(original), 'write_revision': mcp_adapters.file_revision(path)})
    for row in rows:
        source = Path(row['source'])
        if row['tool'] != 'claude' or row.get('scope') != 'global':
            continue
        if row['kind'] == 'mcp' and source == native:
            row.update(writable=True, editable=row.get('editable', True), managed_profile=pid,
                       managed_kind='mcp', managed_digest=profiles.digest(json.dumps(disabled, sort_keys=True).encode()))
            row['id'] = profiles.digest(json.dumps([pid, 'mcp', row['name']]).encode())[:32]
            if row['name'] not in disabled:
                row['enabled'] = True
        elif row['kind'] == 'skill' and source.parent.parent == root / 'skills' and not row.get('linked'):
            artifact = Path(row['write_source']).parent
            row.update(writable=True, managed_profile=pid, managed_kind='skill', managed_digest=tree_digest(artifact))
            row['id'] = profiles.digest(json.dumps([pid, 'skill', source.parent.name]).encode())[:32]
            if row.get('enabled') is None:
                row['enabled'] = True


def toggle(row, enabled):
    root = profiles.root(row['managed_profile'])
    if row['managed_kind'] == 'skill':
        name = Path(row['source']).parent.name
        src = fsguard.profile_file(root, ('skills/' if row['enabled'] else '.farshell-disabled-skills/') + name)
        dst = fsguard.profile_file(root, ('.farshell-disabled-skills/' if enabled is False else 'skills/') + name)
        if row['enabled'] == enabled:
            return {'status': 'ok', 'changed': False}
        with config_io.locked(root / '.skill-operation'):
            if tree_digest(src) != row['managed_digest'] or dst.exists():
                raise profiles.Refused(tr("스킬 원본 또는 대상이 바뀌었습니다"))
            fsguard.private_profile_directory(dst.parent)
            os.rename(src, dst)
        return {'status': 'ok', 'changed': True}
    path = fsguard.profile_file(root, '.claude.json')
    saved = fsguard.profile_file(root, '.farshell-disabled-mcp.json')
    with config_io.locked(path), config_io.locked(saved):
        disabled = _disabled(root)
        if profiles.digest(json.dumps(disabled, sort_keys=True).encode()) != row['managed_digest']:
            raise profiles.Refused(tr("비활성 MCP 저장소가 바뀌었습니다"))
        data = json.loads(fsguard.read_profile_bytes(root, path.name)) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get('mcpServers', {}), dict):
            raise profiles.Refused(tr("MCP 저장소가 올바르지 않습니다"))
        servers = data.setdefault('mcpServers', {})
        name = row['name']
        if enabled:
            if name in servers or name not in disabled:
                raise profiles.Refused(tr("MCP 원본 또는 대상이 바뀌었습니다"))
            servers[name] = disabled[name]
            config_io.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
            del disabled[name]
            config_io.atomic_write(saved, json.dumps(disabled, ensure_ascii=False, indent=2))
        else:
            if name not in servers or name in disabled:
                raise profiles.Refused(tr("MCP 원본 또는 대상이 바뀌었습니다"))
            disabled[name] = servers[name]
            # Write recovery copy first. A crash cannot lose the definition.
            config_io.atomic_write(saved, json.dumps(disabled, ensure_ascii=False, indent=2))
            del servers[name]
            config_io.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    return {'status': 'ok', 'changed': True}


def add_mcp(profile_id, revision, name, definition, *, preview=False, file_revision=None):
    """Add only absent native user entries; no shared configuration writes."""
    import re
    import tomllib
    import agent_profile_migration as migration
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name) or not isinstance(definition, dict) or mcp_adapters.validate_definition(profile['tool'], definition):
        raise profiles.Refused(tr("새 MCP 이름 또는 정의가 올바르지 않습니다"))
    if '\0' in json.dumps(definition, ensure_ascii=False).replace('\\u0000', '\0'):
        raise profiles.Refused(tr("새 MCP 이름 또는 정의가 올바르지 않습니다"))
    root = profiles.root(profile_id)
    filename = '.claude.json' if profile['tool'] == 'claude' else 'config.toml'
    path = fsguard.profile_file(root, filename)
    raw = fsguard.read_profile_bytes(root, filename) if path.exists() else b'{}' if profile['tool'] == 'claude' else b''
    value = json.loads(raw) if profile['tool'] == 'claude' else tomllib.loads(raw.decode())
    key = 'mcpServers' if profile['tool'] == 'claude' else 'mcp_servers'
    if not isinstance(value, dict) or not isinstance(value.get(key, {}), dict) or name in value.get(key, {}) or profile['tool'] == 'claude' and name in _disabled(root):
        raise profiles.Refused(tr("MCP 이름이 이미 있거나 원본 형식이 올바르지 않습니다"))
    expected = profiles.digest(raw)
    if file_revision is not None and file_revision != expected:
        raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
    if preview:
        return {'name': name, 'profile_id': profile_id, 'file_revision': expected,
                'definition_sha256': profiles.digest(json.dumps(definition, sort_keys=True).encode()),
                'warning': tr("MCP는 프로필 권한으로 실행하거나 외부 서비스에 연결합니다")}
    with config_io.locked(path):
        current = fsguard.read_profile_bytes(root, filename) if path.exists() else b'{}' if profile['tool'] == 'claude' else b''
        if profiles.digest(current) != expected:
            raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
        if profile['tool'] == 'claude':
            value.setdefault(key, {})[name] = definition
            text = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            # Append a single absent TOML table, preserving comments and all
            # unrelated settings rather than reserializing the native file.
            text = raw.decode() + '\n[mcp_servers.' + json.dumps(name) + ']\n' + migration._toml(definition)
            tomllib.loads(text)
        backup = fsguard.private_profile_directory(profiles.directory() / 'mcp-backups')
        migration._write_bytes(backup / (expected + '.bak'), raw)
        migration._write_bytes(path, text.encode())
    profiles.bump(profile_id, revision)
    return {'status': 'ok', 'restart_required': True}
