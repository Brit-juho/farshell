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


def _manifest(files, tool):
    name = '.claude-plugin/plugin.json' if tool == 'claude' else '.codex-plugin/plugin.json'
    if name not in files and tool == 'codex':
        name = 'plugin.json'
    if name not in files:
        raise profiles.Refused(tr("플러그인 manifest를 찾지 못했습니다"))
    value = json.loads(files[name][0])
    if not isinstance(value, dict):
        raise profiles.Refused(tr("플러그인 이름이 다르거나 별도 검토가 필요한 의존성이 있습니다"))
    return value


def _dependencies(value, market):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32 or len(value) != len(set(map(str, value))):
        raise profiles.Refused(tr("의존성은 같은 마켓플레이스의 고정 플러그인 목록이어야 합니다"))
    for name in value:
        match = _SELECTOR.fullmatch(name) if isinstance(name, str) else None
        if match is None or match[2] != market:
            raise profiles.Refused(tr("의존성은 같은 마켓플레이스의 고정 플러그인 목록이어야 합니다"))
    return value


def _source_packages(source, tool, selector):
    name, market = selector.split('@')
    catalog_file = fsguard.profile_file(source, '.claude-plugin/marketplace.json')
    entries = None
    if catalog_file.exists():
        catalog = json.loads(fsguard.read_profile_bytes(source, '.claude-plugin/marketplace.json'))
        if not isinstance(catalog, dict) or catalog.get('name') != market or not isinstance(catalog.get('plugins'), list) or len(catalog['plugins']) > 128:
            raise profiles.Refused(tr("플러그인 마켓플레이스 형식이 올바르지 않습니다"))
        entries = {}
        for entry in catalog['plugins']:
            if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', entry['name']) or entry['name'] in entries:
                raise profiles.Refused(tr("플러그인 마켓플레이스 형식이 올바르지 않습니다"))
            entries[entry['name']] = entry
    result, visiting, done = [], set(), set()
    def visit(current):
        if current in done:
            return
        if current in visiting or len(visiting) >= 16 or len(done) + len(visiting) >= 32:
            raise profiles.Refused(tr("플러그인 의존성이 순환하거나 한도를 넘었습니다"))
        visiting.add(current)
        entry = entries.get(current) if entries is not None else {'source': '.'} if current == name else None
        if entry is None:
            raise profiles.Refused(tr("고정 보관본에 필요한 플러그인 의존성이 없습니다"))
        relative = entry.get('source')
        from pathlib import PurePosixPath
        if not isinstance(relative, str) or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts or '\\' in relative or ':' in relative:
            raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
        package = source if str(PurePosixPath(relative)) == '.' else fsguard.profile_file(source, str(PurePosixPath(relative)))
        if not package.is_dir():
            raise profiles.Refused(tr("고정 보관본에 필요한 플러그인 의존성이 없습니다"))
        if not package.resolve().is_relative_to(source.resolve()):
            raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
        files = _snapshot(package)
        manifest = _manifest(files, tool)
        if manifest.get('name') != current:
            raise profiles.Refused(tr("플러그인 이름이 다르거나 별도 검토가 필요한 의존성이 있습니다"))
        dependencies = list(dict.fromkeys(_dependencies(entry.get('dependencies'), market) + _dependencies(manifest.get('dependencies'), market)))
        for dependency in dependencies:
            visit(dependency.split('@')[0])
        result.append({'selector': current + '@' + market, 'files': files, 'dependencies': dependencies})
        visiting.remove(current)
        done.add(current)
    visit(name)
    return result


def _marketplace_peers(profile, market):
    if profile['tool'] == 'claude':
        root = profiles.root(profile['id'])
        path = fsguard.profile_file(root, 'plugins/installed_plugins.json')
        data = json.loads(fsguard.read_profile_bytes(root, 'plugins/installed_plugins.json')) if path.exists() else {}
        plugins = data.get('plugins', {})
        if not isinstance(plugins, dict):
            raise profiles.Refused(tr("플러그인 마켓플레이스 형식이 올바르지 않습니다"))
        names = [name for name, rows in plugins.items() if isinstance(rows, list) and any(isinstance(r, dict) and r.get('scope') == 'user' for r in rows)]
    else:
        from agent_profile_runtime import run
        code, raw = run(profile['id'], ['plugin', 'list', '--json'], timeout=45)
        data = json.loads(raw) if code == 0 else {}
        if not isinstance(data, dict) or not isinstance(data.get('installed'), list):
            raise profiles.Refused(tr("네이티브 플러그인 목록을 확인하지 못했습니다"))
        names = [r.get('pluginId') for r in data['installed'] if isinstance(r, dict) and r.get('installed') is not False]
    result = [name for name in names if isinstance(name, str) and _SELECTOR.fullmatch(name) and name.split('@')[1] == market]
    if len(result) > 32:
        raise profiles.Refused(tr("플러그인 의존성이 순환하거나 한도를 넘었습니다"))
    return sorted(set(result))


def _save_package(stage, relative, files):
    from agent_profile_migration import _write_bytes
    folder = fsguard.private_profile_directory(stage / relative)
    for name, (raw, mode) in files.items():
        path = fsguard.profile_file(folder, name)
        fsguard.private_profile_directory(path.parent)
        _write_bytes(path, raw, mode)


def _catalog(stage, market, packages):
    from agent_profile_migration import _write_bytes
    catalog = {'name': market, 'owner': {'name': 'FarShell'}, 'plugins': [{'name': p['selector'].split('@')[0], 'source': './' + p['relative']} for p in packages]}
    path = fsguard.profile_file(stage, '.claude-plugin/marketplace.json')
    fsguard.private_profile_directory(path.parent)
    _write_bytes(path, json.dumps(catalog).encode())


def _enabled(profile, selector):
    import agent_profile_config
    _, _, _, data = agent_profile_config._read(profile['id'])
    if profile['tool'] == 'claude':
        value = data.get('enabledPlugins', {}).get(selector, True)
    else:
        value = data.get('plugins', {}).get(selector, {}).get('enabled', True)
    if type(value) is not bool:
        raise profiles.Refused(tr("플러그인 마켓플레이스 형식이 올바르지 않습니다"))
    return value


def _before_files(profile):
    root = profiles.root(profile['id'])
    names = ('settings.json', 'plugins/installed_plugins.json', 'plugins/known_marketplaces.json') if profile['tool'] == 'claude' else ('config.toml',)
    result = {}
    for name in names:
        path = fsguard.profile_file(root, name)
        result[name] = profiles.digest(fsguard.read_profile_bytes(root, name)) if path.exists() else None
    return result


def preview(profile_id, revision, action, plugin=None, path=None, download_id=None, job_id=None):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    from agent_profile_runtime import require_inactive
    if profile['tool'] == 'codex':
        try:
            import tomlkit
        except ImportError:
            raise profiles.Refused(tr("TOML 훅 편집에는 tomlkit 설치가 필요합니다")) from None
    require_inactive(profile_id)
    if action == 'restore':
        return _restore_preview(profile, job_id)
    profiles.require_no_pending_jobs(profile_id)
    match = _SELECTOR.fullmatch(plugin) if isinstance(plugin, str) else None
    if not match or action not in ('install', 'update', 'remove') or job_id is not None:
        raise profiles.Refused(tr("플러그인 작업 또는 이름이 올바르지 않습니다"))
    name, market = match.groups()
    source_packages = []
    if action != 'remove':
        if download_id is not None:
            if path is not None:
                raise profiles.Refused(tr("플러그인 작업 또는 이름이 올바르지 않습니다"))
            from agent_profile_download import resolve
            source = resolve(profile_id, download_id)
        else:
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise profiles.Refused(tr("검토할 로컬 플러그인 폴더의 절대경로가 필요합니다"))
            source = Path(path)
            home = vt_paths.user_home().resolve()
            if source.is_symlink() or not source.resolve().is_relative_to(home) or not source.is_dir():
                raise profiles.Refused(tr("플러그인 원본은 이 호스트 홈 안의 실제 폴더여야 합니다"))
            parts = source.resolve().relative_to(home).parts
            if any(p in ('.ssh', '.aws', '.gnupg', '.clauth', '.vt', '.config') for p in parts):
                raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
            for native in ('.claude', '.codex'):
                if native in parts:
                    index = parts.index(native)
                    if len(parts) <= index + 2 or parts[index + 1] != 'plugins':
                        raise profiles.Refused(tr("플러그인 원본 경로에 자격증명 저장소가 있습니다"))
        source_packages = _source_packages(source, profile['tool'], plugin)
    if action != 'remove':
        peers = _marketplace_peers(profile, market)
        selected_names = {p['selector'] for p in source_packages}
        for selector in peers:
            if selector not in selected_names:
                installed = _installed(profile, selector, verify_source=True)
                if installed is None:
                    raise profiles.Refused(tr("복구 대상 플러그인이 바뀌었습니다"))
                source_packages.append({'selector': selector, 'files': _snapshot(installed), 'dependencies': []})
    stage_id = secrets.token_hex(16)
    stage = fsguard.private_profile_directory(profiles.directory() / 'plugin-staging' / stage_id)
    packages, previous, preserving = [], {}, set()
    def preserve(selector, depth=0):
        if selector in preserving:
            raise profiles.Refused(tr("플러그인 의존성이 순환하거나 한도를 넘었습니다"))
        if selector in previous:
            return
        preserving.add(selector)
        if depth >= 16 or len(previous) >= 32:
            raise profiles.Refused(tr("플러그인 의존성이 순환하거나 한도를 넘었습니다"))
        installed = _installed(profile, selector)
        if installed is None:
            raise profiles.Refused(tr("기존 플러그인 복구 보관본을 만들지 못했습니다"))
        files = _snapshot(installed)
        manifest = _manifest(files, profile['tool'])
        relative = 'packages/' + selector.split('@')[0]
        previous[selector] = {'selector': selector, 'relative': relative, 'digest': _fingerprint(files), 'enabled': _enabled(profile, selector)}
        _save_package(stage / 'restore', relative, files)
        for dep in _dependencies(manifest.get('dependencies'), market):
            preserve(dep, depth + 1)
        preserving.remove(selector)
    for item in source_packages or [{'selector': plugin, 'files': None, 'dependencies': []}]:
        selector = item['selector']
        installed = _installed(profile, selector)
        before = _fingerprint(_snapshot(installed)) if installed is not None else None
        desired = _fingerprint(item['files']) if item['files'] is not None else None
        is_root = selector == plugin
        if not is_root and before is not None and before != desired:
            raise profiles.Refused(tr("기존 의존성의 내용이 다릅니다. 별도 업데이트를 검토하세요"))
        if is_root and action == 'install' and before is not None and before != desired:
            raise profiles.Refused(tr("이미 설치된 플러그인은 업데이트로 검토하세요"))
        if is_root and action in ('update', 'remove') and before is None:
            raise profiles.Refused(tr("업데이트하거나 제거할 플러그인이 없습니다"))
        relative = 'package' if len(source_packages) == 1 else 'packages/' + selector.split('@')[0]
        if item['files'] is not None:
            _save_package(stage, relative, item['files'])
        operation = 'reuse' if before is not None and before == desired else action if is_root else 'install'
        if before is not None:
            preserve(selector)
        packages.append({'selector': selector, 'relative': relative, 'package_digest': desired, 'before_digest': before,
                         'before_enabled': _enabled(profile, selector) if before is not None else None,
                         'operation': operation, 'dependencies': item['dependencies']})
    if source_packages:
        _catalog(stage, market, packages)
    if previous:
        _catalog(stage / 'restore', market, list(previous.values()))
    plan = {'profile_id': profile_id, 'revision': revision, 'action': action, 'plugin': plugin,
            'stage_id': stage_id, 'packages': packages, 'previous': list(previous.values()),
            'digest': _fingerprint(_snapshot(stage)), 'before_files': _before_files(profile)}
    return {**mcp_plans.issue('profile-plugin', plan), 'plugin': plugin, 'action': action,
            'staged_path': str(stage), 'package_sha256': plan['digest'],
            'packages': [{'plugin': p['selector'], 'operation': p['operation'], 'sha256': p['package_digest']} for p in packages],
            'reinstall': profile['tool'] == 'codex' and action == 'update',
            'warning': tr("설치 코드를 포함한 고정 보관본을 검토하세요. 플러그인은 프로필 권한으로 실행됩니다")}


def _disable(profile, selector):
    from agent_profile_runtime import run
    if _enabled(profile, selector) is False:
        return
    if profile['tool'] == 'claude':
        code, _ = run(profile['id'], ['plugin', 'disable', selector], timeout=30)
        if code:
            raise profiles.Refused(tr("설치한 플러그인의 비활성 상태를 확인하지 못했습니다"))
    else:
        import tomlkit
        import config_io
        from agent_profile_migration import _write_bytes
        root = profiles.root(profile['id'])
        path = fsguard.profile_file(root, 'config.toml')
        with config_io.locked(path):
            doc = tomlkit.parse(fsguard.read_profile_bytes(root, 'config.toml').decode())
            if 'plugins' not in doc:
                doc['plugins'] = tomlkit.table()
            if selector not in doc['plugins']:
                doc['plugins'][selector] = tomlkit.table()
            doc['plugins'][selector]['enabled'] = False
            _write_bytes(path, tomlkit.dumps(doc).encode())
    if _enabled(profile, selector) is not False:
        raise profiles.Refused(tr("설치한 플러그인의 비활성 상태를 확인하지 못했습니다"))


def _restore_preview(profile, job_id):
    job = profiles.job_status(job_id)
    if job['profile_id'] != profile['id'] or job['kind'] not in ('plugin-install', 'plugin-update', 'plugin-remove') or job['state'] not in ('done', 'partial', 'interrupted'):
        raise profiles.Refused(tr("플러그인 복구 보관본을 찾지 못했습니다"))
    saved = job['result'].get('recovery')
    if not isinstance(saved, dict):
        raise profiles.Refused(tr("플러그인 복구 보관본을 찾지 못했습니다"))
    stage = profiles.directory() / 'plugin-staging' / profiles.identifier(saved['stage_id'])
    if _fingerprint(_snapshot(stage)) != saved['digest']:
        raise profiles.Refused(tr("검토한 플러그인 보관본이 바뀌었습니다"))
    packages = []
    for item in reversed(saved['packages']):
        installed = _installed(profile, item['selector'])
        current = _fingerprint(_snapshot(installed)) if installed is not None else None
        if current not in (None, item['package_digest'], item['before_digest']):
            raise profiles.Refused(tr("복구 대상 플러그인이 바뀌었습니다"))
        old = next((p for p in saved['previous'] if p['selector'] == item['selector']), None)
        packages.append({'selector': item['selector'], 'relative': old['relative'] if old else '',
                         'package_digest': item['before_digest'], 'before_digest': current,
                         'before_enabled': _enabled(profile, item['selector']) if current is not None else None,
                         'operation': 'reuse' if item['operation'] == 'reuse' else 'replace' if old else 'remove', 'dependencies': []})
    plan = {'profile_id': profile['id'], 'revision': profile['revision'], 'action': 'restore', 'plugin': saved['plugin'],
            'stage_id': saved['stage_id'], 'digest': saved['digest'], 'packages': packages,
            'previous': saved['previous'], 'before_files': _before_files(profile), 'restore_job': job['id']}
    return {**mcp_plans.issue('profile-plugin', plan), 'plugin': saved['plugin'], 'action': 'restore',
            'staged_path': str(stage / 'restore'), 'package_sha256': saved['digest'],
            'warning': tr("복구한 플러그인은 끈 상태로 둡니다. 활성화는 별도로 검토하세요")}


def apply(token):
    plan = mcp_plans.take(token, 'profile-plugin')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    profile = profiles.get(plan['profile_id'])
    profiles.require_revision(profile, plan['revision'])
    from agent_profile_runtime import run, require_inactive
    require_inactive(profile['id'])
    with profiles.database() as db:
        pending = [dict(r) for r in db.execute("SELECT id,kind,state,result FROM jobs WHERE profile_id=? AND state IN ('running','partial','partial-rollback')", (profile['id'],))]
    related = [j['id'] for j in pending if j['kind'] == 'plugin-restore' and j['state'] == 'partial' and json.loads(j['result']).get('restored_job') == plan.get('restore_job')]
    if any(j['id'] != plan.get('restore_job') and j['id'] not in related for j in pending):
        raise profiles.Refused(tr("프로필의 중단되거나 실행 중인 작업을 먼저 확인하세요"))
    stage = profiles.directory() / 'plugin-staging' / profiles.identifier(plan['stage_id'])
    if _fingerprint(_snapshot(stage)) != plan['digest'] or _before_files(profile) != plan['before_files']:
        raise profiles.Refused(tr("검토한 플러그인 보관본이 바뀌었습니다"))
    for item in plan['packages']:
        installed = _installed(profile, item['selector'])
        actual = _fingerprint(_snapshot(installed)) if installed is not None else None
        if actual != item['before_digest'] or actual is not None and _enabled(profile, item['selector']) != item['before_enabled']:
            raise profiles.Refused(tr("복구 대상 플러그인이 바뀌었습니다"))
    jid = profiles.job_begin(profile['id'], 'plugin-' + plan['action'])
    changed = False
    recovery = {k: plan[k] for k in ('stage_id', 'digest', 'packages', 'previous', 'plugin')}
    journal = {'plugin': plan['plugin'], 'action': plan['action'], 'recovery': recovery, 'progress': []}
    if plan.get('restore_job'):
        journal['restored_job'] = plan['restore_job']
        journal['previous_attempts'] = related
    profiles.job_finish(jid, 'running', journal)
    try:
        if any(p['operation'] in ('install', 'update', 'replace') for p in plan['packages']):
            changed = True
            source = stage / 'restore' if plan['action'] == 'restore' else stage
            if profile['tool'] == 'codex' and any(p['before_digest'] is not None for p in plan['packages']):
                market = plan['plugin'].split('@')[1]
                code, _ = run(profile['id'], ['plugin', 'marketplace', 'remove', market], timeout=60)
                if code:
                    raise profiles.Refused(tr("네이티브 마켓플레이스 등록에 실패했습니다"))
            code, _ = run(profile['id'], ['plugin', 'marketplace', 'add', str(source)], timeout=60)
            if code:
                raise profiles.Refused(tr("네이티브 마켓플레이스 등록에 실패했습니다"))
        for item in plan['packages']:
            selector, operation = item['selector'], item['operation']
            if operation == 'reuse':
                continue
            changed = True
            journal['progress'].append({'plugin': selector, 'operation': operation, 'state': 'writing'})
            profiles.job_finish(jid, 'running', journal)
            remove = operation == 'remove' or operation in ('update', 'replace') and (profile['tool'] == 'codex' or plan['action'] == 'restore')
            if remove and item['before_digest'] is not None:
                verb = 'remove' if profile['tool'] == 'codex' else 'uninstall'
                code, _ = run(profile['id'], ['plugin', verb, selector], timeout=120)
                if code:
                    raise profiles.Refused(tr("네이티브 플러그인 작업에 실패했습니다"))
            if operation != 'remove':
                verb = 'update' if operation == 'update' and profile['tool'] == 'claude' else 'add' if profile['tool'] == 'codex' else 'install'
                code, _ = run(profile['id'], ['plugin', verb, selector], timeout=120)
                if code:
                    raise profiles.Refused(tr("네이티브 플러그인 작업에 실패했습니다"))
                _disable(profile, selector)
            installed = _installed(profile, selector)
            actual = _fingerprint(_snapshot(installed)) if installed is not None else None
            if actual != item['package_digest']:
                raise profiles.Refused(tr("설치된 플러그인 내용이 검토한 보관본과 다릅니다"))
            journal['progress'][-1]['state'] = 'done'
            profiles.job_finish(jid, 'running', journal)
        for item in plan['packages']:
            installed = _installed(profile, item['selector'], verify_source=True)
            if (_fingerprint(_snapshot(installed)) if installed is not None else None) != item['package_digest']:
                raise profiles.Refused(tr("설치된 플러그인 내용이 검토한 보관본과 다릅니다"))
            if installed is not None and _enabled(profile, item['selector']) != (item['before_enabled'] if item['operation'] == 'reuse' else False):
                raise profiles.Refused(tr("설치한 플러그인의 비활성 상태를 확인하지 못했습니다"))
        result = {**journal, 'status': 'ok', 'job_id': jid, 'restart_required': True,
                  'enable_review_required': any(p['operation'] not in ('reuse', 'remove') for p in plan['packages'])}
        if plan.get('restore_job'):
            # Mark the old partial intent resolved in the same DB transaction
            # as the new revision and successful recovery job.
            return _complete_restore(profile, jid, plan['restore_job'], result)
        return profiles.complete_job(profile['id'], profile['revision'], jid, result, receipt=('', 'plugin', {'plugin': plan['plugin'], 'packages': [{k: p[k] for k in ('selector', 'package_digest', 'dependencies')} for p in plan['packages']]}, True))
    except BaseException:
        if changed:
            profiles.bump(profile['id'], profile['revision'], 'partial')
        profiles.job_finish(jid, 'partial' if changed else 'failed', {**journal, 'recovery_required': changed})
        raise


def _complete_restore(profile, job_id, restored_job_id, result):
    with profiles.database() as db:
        cursor = db.execute("UPDATE profiles SET revision=revision+1,state='ready' WHERE id=? AND revision=?", (profile['id'], profile['revision']))
        if cursor.rowcount != 1:
            raise profiles.Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
        cursor = db.execute("UPDATE jobs SET state='rolled-back' WHERE id=? AND profile_id=? AND state IN ('done','partial','interrupted')", (restored_job_id, profile['id']))
        if cursor.rowcount != 1:
            raise profiles.Refused(tr("플러그인 복구 보관본을 찾지 못했습니다"))
        for prior in result.get('previous_attempts', []):
            db.execute("UPDATE jobs SET state='rolled-back' WHERE id=? AND profile_id=? AND state='partial'", (prior, profile['id']))
        db.execute("UPDATE jobs SET state='done',result=? WHERE id=?", (json.dumps(result), job_id))
    return result


def _installed(profile, selector, *, verify_source=False):
    root = profiles.root(profile['id'])
    if profile['tool'] == 'claude':
        registry_path = fsguard.profile_file(root, 'plugins/installed_plugins.json')
        if not registry_path.exists():
            return None
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
        row = next((r for r in rows if isinstance(r, dict) and r.get('pluginId') == selector and r.get('installed') is not False), {})
        if not row:
            return None
        source_value = row.get('source', {}).get('path')
        version = row.get('version')
        match = _SELECTOR.fullmatch(selector)
        if match is None or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}', version) or not isinstance(source_value, str):
            raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
        source_folder = Path(source_value)
        if not source_folder.is_absolute() or not source_folder.resolve().is_relative_to(root.resolve()) and not source_folder.resolve().is_relative_to((profiles.directory() / 'plugin-staging').resolve()):
            raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
        cache = fsguard.profile_file(root, 'plugins/cache/' + match[2] + '/' + match[1] + '/' + version)
        if not cache.is_dir():
            raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
        if verify_source and _fingerprint(_snapshot(source_folder)) != _fingerprint(_snapshot(cache)):
            raise profiles.Refused(tr("설치된 플러그인 내용이 검토한 보관본과 다릅니다"))
        value = str(cache)
    if value is None:
        return None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
    folder = Path(value)
    if not folder.resolve().is_relative_to(root.resolve()) and not folder.resolve().is_relative_to((profiles.directory() / 'plugin-staging').resolve()):
        raise profiles.Refused(tr("네이티브 플러그인 설치 경로가 올바르지 않습니다"))
    return folder
