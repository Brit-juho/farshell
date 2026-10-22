"""Digest-reviewed native installation from a private local package snapshot."""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import agent_profiles as profiles
import fsguard
import mcp_plans
import vt_paths
from i18n import tr

_SELECTOR = re.compile(r'([A-Za-z0-9_.-]{1,100})@([A-Za-z0-9_.-]{1,100})')


def _snapshot(root):
    files = {}
    size = count = 0
    for path in fsguard.profile_tree(root):
        rel = path.relative_to(root)
        if '.git' in rel.parts:
            continue
        count += 1
        if count > 2000:
            raise profiles.Refused(tr("플러그인 파일 수 또는 크기가 한도를 넘었습니다"))
        if path.is_dir() and not path.is_symlink():
            continue
        if path.name.lower() in fsguard.DENY_NAMES or path.name.startswith('.env'):
            raise profiles.Refused(tr("플러그인에 자격증명 파일이 있습니다"))
        raw = fsguard.read_profile_bytes(root, str(rel), 32 * 1024 * 1024 - size)
        size += len(raw)
        files[str(rel)] = (raw, 0o700 if path.stat().st_mode & 0o111 else 0o600)
    return files


def _fingerprint(files):
    return profiles.digest(json.dumps({k: [profiles.digest(v[0]), v[1]] for k, v in files.items()}, sort_keys=True).encode())


def preview(profile_id, revision, action, plugin, path=None):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    match = _SELECTOR.fullmatch(plugin) if isinstance(plugin, str) else None
    if not match or action not in ('install', 'update', 'remove'):
        raise profiles.Refused(tr("플러그인 작업 또는 이름이 올바르지 않습니다"))
    name, market = match.groups()
    plan = {'profile_id': profile_id, 'revision': revision, 'action': action, 'plugin': plugin}
    detail = {'plugin': plugin, 'action': action}
    if action != 'remove':
        if profile['tool'] == 'codex' and action == 'update':
            raise profiles.Refused(tr("이 CLI의 개별 플러그인 업데이트는 지원하지 않습니다"))
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise profiles.Refused(tr("검토할 로컬 플러그인 폴더의 절대경로가 필요합니다"))
        source = Path(path)
        home = vt_paths.user_home().resolve()
        if source.is_symlink() or not source.resolve().is_relative_to(home) or not source.is_dir():
            raise profiles.Refused(tr("플러그인 원본은 이 호스트 홈 안의 실제 폴더여야 합니다"))
        # Credential stores are not plugin sources. Native plugin caches are
        # permitted only below their plugins subtree, never the store root.
        parts = source.resolve().relative_to(home).parts
        if any(p in ('.ssh', '.aws', '.gnupg', '.clauth', '.vt', '.config') for p in parts):
            raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
        for native in ('.claude', '.codex'):
            if native in parts:
                index = parts.index(native)
                if len(parts) <= index + 2 or parts[index + 1] != 'plugins':
                    raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
        files = _snapshot(source)
        manifest_name = '.claude-plugin/plugin.json' if profile['tool'] == 'claude' else '.codex-plugin/plugin.json'
        if manifest_name not in files and profile['tool'] == 'codex':
            manifest_name = 'plugin.json'
        if manifest_name not in files:
            raise profiles.Refused(tr("플러그인 manifest를 찾지 못했습니다"))
        manifest = json.loads(files[manifest_name][0])
        if not isinstance(manifest, dict) or manifest.get('name') != name or manifest.get('dependencies'):
            raise profiles.Refused(tr("플러그인 이름이 다르거나 별도 검토가 필요한 의존성이 있습니다"))
        stage_id = secrets.token_hex(16)
        stage = fsguard.private_profile_directory(profiles.directory() / 'plugin-staging' / stage_id)
        from agent_profile_migration import _write_bytes
        package = fsguard.private_profile_directory(stage / 'package')
        for rel, (raw, mode) in files.items():
            dest = fsguard.profile_file(package, rel)
            fsguard.private_profile_directory(dest.parent)
            _write_bytes(dest, raw, mode)
        catalog = {'name': market, 'owner': {'name': 'FarShell'}, 'plugins': [{'name': name, 'source': './package'}]}
        catalog_path = fsguard.profile_file(stage, '.claude-plugin/marketplace.json')
        fsguard.private_profile_directory(catalog_path.parent)
        _write_bytes(catalog_path, json.dumps(catalog).encode())
        # Both CLIs support this marketplace layout; no remote URL, mutable
        # branch, install command or dependency source is passed to the CLI.
        plan.update(stage_id=stage_id, digest=_fingerprint(_snapshot(stage)), package_digest=_fingerprint(files))
        detail.update(staged_path=str(stage), package_sha256=plan['digest'], files=len(files))
    return {**mcp_plans.issue('profile-plugin', plan), **detail,
            'warning': tr("설치 코드를 포함한 고정 보관본을 검토하세요. 플러그인은 프로필 권한으로 실행됩니다")}


def apply(token):
    plan = mcp_plans.take(token, 'profile-plugin')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    profile = profiles.get(plan['profile_id'])
    profiles.require_revision(profile, plan['revision'])
    from agent_profile_runtime import run
    jid = profiles.job_begin(profile['id'], 'plugin-' + plan['action'])
    changed = False
    journal = {'plugin': plan['plugin'], 'action': plan['action']}
    profiles.job_finish(jid, 'running', journal)
    try:
        if plan['action'] != 'remove':
            stage = profiles.directory() / 'plugin-staging' / profiles.identifier(plan['stage_id'])
            if _fingerprint(_snapshot(stage)) != plan['digest']:
                raise profiles.Refused(tr("검토한 플러그인 보관본이 바뀌었습니다"))
            changed = True
            code, _ = run(profile['id'], ['plugin', 'marketplace', 'add', str(stage)], timeout=60)
            if code:
                raise profiles.Refused(tr("네이티브 마켓플레이스 등록에 실패했습니다"))
        verbs = {'install': 'add', 'remove': 'remove'} if profile['tool'] == 'codex' else {'install': 'install', 'update': 'update', 'remove': 'uninstall'}
        changed = True
        code, _ = run(profile['id'], ['plugin', verbs[plan['action']], plan['plugin']], timeout=120)
        if code:
            raise profiles.Refused(tr("네이티브 플러그인 작업에 실패했습니다"))
        # Installation can activate executable hooks. Keep the new installation
        # disabled until a separate native-toggle review, as the design requires.
        if plan['action'] == 'install':
            if profile['tool'] == 'claude':
                code, _ = run(profile['id'], ['plugin', 'disable', plan['plugin']], timeout=30)
                if code:
                    raise profiles.Refused(tr("설치한 플러그인의 비활성 상태를 확인하지 못했습니다"))
            else:
                import agent_profile_migration
                import tomllib
                import config_io
                import mcp_write
                root = profiles.root(profile['id'])
                path = fsguard.profile_file(root, 'config.toml')
                with config_io.locked(path):
                    raw = fsguard.read_profile_bytes(root, 'config.toml')
                    config = tomllib.loads(raw.decode())
                    table = config.get('plugins', {}).get(plan['plugin'])
                    if table is None:
                        text = raw.decode() + '\n[plugins.' + json.dumps(plan['plugin']) + ']\nenabled = false\n'
                    else:
                        text, found = mcp_write.toml_set_enabled(raw.decode(), plan['plugin'], False, table='plugins')
                        if not found:
                            raise profiles.Refused(tr("설치한 플러그인의 비활성 상태를 확인하지 못했습니다"))
                    tomllib.loads(text)
                    agent_profile_migration._write_bytes(path, text.encode())
        installed = _installed(profile, plan['plugin'])
        if plan['action'] == 'remove':
            if installed is not None:
                raise profiles.Refused(tr("네이티브 플러그인 제거 결과를 확인하지 못했습니다"))
        elif installed is None or _fingerprint(_snapshot(installed)) != plan['package_digest']:
            raise profiles.Refused(tr("설치된 플러그인 내용이 검토한 보관본과 다릅니다"))
        result = {'status': 'ok', 'job_id': jid, 'restart_required': True, 'enable_review_required': plan['action'] == 'install'}
        return profiles.complete_job(profile['id'], profile['revision'], jid, result, receipt=('', 'plugin', {**journal, 'package_digest': plan.get('package_digest')}, True))
    except BaseException:
        if changed:
            profiles.bump(profile['id'], profile['revision'], 'partial')
        profiles.job_finish(jid, 'partial' if changed else 'failed', {**journal, 'recovery_required': changed})
        raise


def _installed(profile, selector):
    root = profiles.root(profile['id'])
    if profile['tool'] == 'claude':
        data = json.loads(fsguard.read_profile_bytes(root, 'plugins/installed_plugins.json'))
        rows = data.get('plugins', {}).get(selector, [])
        rows = [r for r in rows if isinstance(r, dict) and r.get('scope') == 'user']
        value = rows[-1].get('installPath') if rows else None
    else:
        from agent_profile_runtime import run
        code, raw = run(profile['id'], ['plugin', 'list', '--json'], timeout=45)
        if code:
            raise profiles.Refused(tr("네이티브 플러그인 목록을 확인하지 못했습니다"))
        data = json.loads(raw)
        rows = data.get('installed')
        if not isinstance(rows, list):
            raise profiles.Refused(tr("네이티브 플러그인 목록을 확인하지 못했습니다"))
        row = next((r for r in rows if isinstance(r, dict) and r.get('pluginId') == selector), {})
        value = row.get('source', {}).get('path')
    if value is None:
        return None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
    folder = Path(value)
    if not folder.resolve().is_relative_to(root.resolve()) and not folder.resolve().is_relative_to((profiles.directory() / 'plugin-staging').resolve()):
        raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
    return folder
