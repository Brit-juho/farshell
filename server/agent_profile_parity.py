"""Private, read-only migration comparisons; return codes, never secret values."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import agent_profiles as profiles
import agent_profile_migration as migration
import fsguard
import mcp_adapters


_HELPERS = {'apiKeyHelper', 'proxyAuthHelper', 'awsCredentialExport', 'awsAuthRefresh', 'gcpAuthRefresh', 'otelHeadersHelper'}


def contains(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        index = 0
        for item in expected:
            while index < len(actual) and not contains(item, actual[index]):
                index += 1
            if index == len(actual):
                return False
            index += 1
        return True
    return type(expected) is type(actual) and expected == actual


def _read(root, filename, fmt='json'):
    path = fsguard.profile_file(root, filename)
    if not path.exists():
        return {}
    raw = fsguard.read_profile_bytes(root, filename)
    data = json.loads(raw) if fmt == 'json' else tomllib.loads(raw.decode())
    if not isinstance(data, dict):
        raise ValueError('invalid-configuration')
    return data


def _plugins_match(source, base, settings, profile_id):
    from agent_profile_plugins import _snapshot, _fingerprint, _installed
    profile = profiles.get(profile_id)
    if profile['tool'] == 'claude':
        expected = settings.get('enabledPlugins', {})
        target = _read(profiles.root(profile_id), 'settings.json').get('enabledPlugins', {})
        installed = _read(base, 'plugins/installed_plugins.json').get('plugins', {})
        if not isinstance(expected, dict) or not isinstance(target, dict) or not isinstance(installed, dict):
            return False
        for selector, state in expected.items():
            if type(state) is not bool or target.get(selector) is not state:
                return False
            rows = installed.get(selector, [])
            if not isinstance(rows, list):
                return False
            rows = [r for r in rows if isinstance(r, dict) and r.get('scope') == 'user']
            if not rows:
                return False
            value = rows[-1].get('installPath')
            if not isinstance(value, str):
                return False
            original = Path(value)
            if original.is_symlink() or not original.resolve().is_relative_to((base / 'plugins').resolve()):
                return False
            target_root = _installed(profile, selector)
            if target_root is None or _fingerprint(_snapshot(original)) != _fingerprint(_snapshot(target_root)):
                return False
        return True
    # The existing native inventory adapter reads the tool's installation
    # registry; do not infer installation from config.toml references alone.
    import agent_config_context
    import mcp_plugins
    with agent_config_context.use({'tool': 'codex', 'root': str(base), 'home': str(mcp_adapters._home())}):
        result = mcp_plugins.scan_codex_plugins(refresh=True)
    if result['errors']:
        return False
    desired = settings.get('plugins', {})
    target = _read(profiles.root(profile_id), 'config.toml', 'toml').get('plugins', {})
    for selector, state in desired.items():
        expected = next((r for r in result['plugins'] if r['name'] == selector), None)
        if expected is None or not expected['installed'] or not isinstance(state, dict) or not contains(state, target.get(selector)):
            return False
        version = expected.get('version')
        parts = selector.split('@')
        if len(parts) != 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p) for p in parts) or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}', version):
            return False
        original = fsguard.profile_file(base, 'plugins/cache/' + parts[1] + '/' + parts[0] + '/' + version)
        target_root = _installed(profile, selector, verify_source=True)
        if target_root is None or _fingerprint(_snapshot(original)) != _fingerprint(_snapshot(target_root)):
            return False
    return True


def compare(source, profile_id, manifest):
    from agent_profile_secret import read as read_private
    from agent_profile_runtime import environment
    home = mcp_adapters._home()
    base = source['root'] if source['kind'] == 'native' else home / ('.claude' if source['tool'] == 'claude' else '.codex')
    root = profiles.root(profile_id)
    settings = _read(base, 'settings.json' if source['tool'] == 'claude' else 'config.toml', 'json' if source['tool'] == 'claude' else 'toml')
    own = _read(source['root'], 'config.toml', 'toml') if source['kind'] == 'clauth' else {}
    private = read_private(root)
    effective = environment(profile_id)
    source_env = settings.get('env', {})
    if not isinstance(source_env, dict) or not isinstance(own.get('env', {}), dict):
        raise ValueError('invalid-environment')
    source_env = {**source_env, **own.get('env', {})}
    resolved = set()
    if private is not None:
        try:
            expected_raw, _ = migration._private_configuration(own, root)
            expected_private = json.loads(expected_raw)
            from usage.api_profiles import credential
            routing = ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')
            same_binding = credential(expected_private) == credential(private) and expected_private['env'].get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com') == private['env'].get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com') and all(expected_private['env'].get(k) == private['env'].get(k) for k in routing)
            if same_binding and contains(expected_private, private):
                resolved.add('api-key-login-required')
        except ValueError:
            pass
    if all(effective.get(k) == v for k, v in own.get('env', {}).items()):
        resolved.add('profile-environment-requires-setup')
    if 'plugins-reinstall-required' in manifest.get('warnings', []):
        from agent_profile_share import plugins_shared
        if plugins_shared(profile_id) or _plugins_match(source, base, settings, profile_id):
            resolved.add('plugins-reinstall-required')
    # Retired decision: copying credentials is no longer a rule, so these never block.
    reasons = [w for w in manifest.get('warnings', []) if w not in resolved and w not in migration.RETIRED_WARNINGS]
    writes, _, _, modes = migration._configuration(source, migration.recorded_skill_names(source, manifest),
                                                   root if manifest.get('private_configuration') else None)
    migration._preserve_private_helper(writes, root, manifest.get('private_configuration'))
    for name, expected in writes.items():
        path = fsguard.profile_file(root, name)
        if not path.exists():
            reasons.append('configuration-coverage-incomplete')
            continue
        raw = fsguard.read_profile_bytes(root, name)
        if name.endswith('.json') and name in ('settings.json', '.claude.json', '.farshell-provider.json'):
            matches = contains(json.loads(expected), json.loads(raw))
        elif name == 'config.toml':
            matches = contains(tomllib.loads(expected.decode()), tomllib.loads(raw.decode()))
        else:
            matches = raw == expected and bool(path.stat().st_mode & 0o111) == (modes.get(name, 0o600) == 0o700)
        if not matches:
            reasons.append('configuration-coverage-incomplete')
    return {'blockers': sorted(set(reasons)), 'resolved_warnings': sorted(resolved & set(manifest.get('warnings', [])) - migration.RETIRED_WARNINGS)}
