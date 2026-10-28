"""Reviewed, non-destructive configuration and exact-conversation import.

No clauth executable, credentials, refresh chains or status feed is required.
Source locations are discovered on the owner host; clients send opaque IDs only.
"""
from __future__ import annotations

import json
import itertools
import os
import re
import secrets
import shlex
import sys
import time
import tomllib
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import mcp_adapters
import mcp_plans
import vt_paths
from i18n import tr

MAX_FILES = 2000
MAX_BYTES = 64 * 1024 * 1024
MAX_FIRST_RECORD = 1024 * 1024
# Retirement needs a verified native resume only for the newest conversations of a
# source; older owned ones must be imported with a matching source hash.
RETIRE_VERIFY_RECENT = 5
# Conversations are streamed (hashed and copied in chunks), never loaded whole, so their
# limit is a disk budget rather than the 64 MiB memory bound used for configuration files.
MAX_CONVERSATION_BYTES = 2 * 1024 * 1024 * 1024
_SID = re.compile(r"[a-fA-F0-9-]{32,36}")
_SECRET = re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)")


def sources():
    home = mcp_adapters._home()
    result = {}
    for tool in ("claude", "codex"):
        folder = home / (".claude" if tool == "claude" else ".codex")
        if folder.is_dir() and not folder.is_symlink():
            key = profiles.digest(str(folder).encode())[:32]
            result[key] = {"id": key, "tool": tool, "name": tool, "kind": "native", "root": folder}
    base = home / ".clauth" / "profiles"
    if base.is_dir() and not base.is_symlink():
        folders = list(itertools.islice(base.iterdir(), profiles.MAX_PROFILES + 1))
        if len(folders) > profiles.MAX_PROFILES:
            raise profiles.Refused(tr("이전 원본 프로필 수가 한도를 넘었습니다"))
        for folder in sorted(folders):
            if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", folder.name):
                continue
            tool = "codex" if (folder / "auth.json").exists() or (folder / "codex-home").is_dir() else "claude"
            key = profiles.digest(str(folder).encode())[:32]
            result[key] = {"id": key, "tool": tool, "name": folder.name, "kind": "clauth", "root": folder}
    return result


def source_list():
    return {"sources": [{k: row[k] for k in ("id", "tool", "name", "kind")} for row in sources().values()]}


def _source(source_id):
    row = sources().get(profiles.identifier(source_id))
    if not row:
        raise profiles.Refused(tr("이전 원본을 찾지 못했습니다"))
    return row


def _json(root, relative):
    raw = fsguard.read_profile_bytes(root, relative)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    return value


def _safe_config(value):
    """Strip credential-bearing keys, preserve native env references only."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _SECRET.search(key) or key in ("apiKeyHelper", "proxyAuthHelper", "awsCredentialExport", "awsAuthRefresh", "gcpAuthRefresh", "otelHeadersHelper", "headers", "http_headers"):
                continue
            if key == "env" and isinstance(item, dict):
                out[key] = {k: v for k, v in item.items() if isinstance(v, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", v)}
            else:
                out[key] = _safe_config(item)
        return out
    if isinstance(value, list):
        return [_safe_config(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise profiles.Refused(tr("원본 설정에 지원하지 않는 값이 있습니다"))


def _toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (int, float):
        import math
        if not math.isfinite(value):
            raise profiles.Refused(tr("설정 숫자가 올바르지 않습니다"))
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    raise profiles.Refused(tr("설정 값의 형식이 올바르지 않습니다"))


def _toml(data):
    text = "\n".join(json.dumps(k) + " = " + _toml_value(v) for k, v in data.items()) + "\n"
    if tomllib.loads(text) != data:
        raise profiles.Refused(tr("TOML 변환 결과가 원본과 다릅니다"))
    return text


def _private_configuration(own, root):
    """Match clauth base/model/env precedence without copying OAuth material."""
    from urllib.parse import urlsplit
    key = own.get('api_key')
    if key is not None and (not isinstance(key, str) or not key or len(key) > 32768 or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in key)):
        raise profiles.Refused(tr("API 키 형식이 올바르지 않습니다"))
    env = {}
    endpoint = own.get('base_url')
    if endpoint is not None:
        env['ANTHROPIC_BASE_URL'] = endpoint
    models = own.get('models', {})
    if not isinstance(models, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    for name in ('opus', 'sonnet', 'haiku', 'fable', 'subagent'):
        if models.get(name) is not None:
            env['CLAUDE_CODE_SUBAGENT_MODEL' if name == 'subagent' else 'ANTHROPIC_DEFAULT_' + name.upper() + '_MODEL'] = models[name]
    custom = own.get('env', {})
    if not isinstance(custom, dict):
        raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
    env.update(custom)
    if not key and not env.get('ANTHROPIC_BASE_URL') and not any(env.get(k) for k in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY')):
        raise profiles.Refused(tr("원본에 복제할 API 인증 설정이 없습니다"))
    for name in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'):
        value = env.get(name)
        if value is not None and (not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value)):
            raise profiles.Refused(tr("API 키 형식이 올바르지 않습니다"))
    protected = {'HOME', 'PATH', 'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'BASH_ENV', 'ENV', 'SHELLOPTS', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'CLAUDE_CODE_OAUTH_TOKEN', 'TMPDIR'}
    for name, value in env.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            or name in protected or name.startswith(('VT_', 'FSH_', 'LD_', 'DYLD_'))
            or not isinstance(value, str) or len(value) > 32768 or '\0' in value):
            raise profiles.Refused(tr("프로필 환경변수에 격리 경계를 바꾸는 값이 있습니다"))
    if 'ANTHROPIC_BASE_URL' in env:
        parsed = urlsplit(env['ANTHROPIC_BASE_URL'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise profiles.Refused(tr("API endpoint 형식이 올바르지 않습니다"))
    private = json.dumps({'version': 1, 'api_key': key, 'env': env}, ensure_ascii=False).encode()
    helper = shlex.join([sys.executable, str(Path(__file__).with_name('agent_profile_secret.py')), str(root)]) if key else None
    return private, helper


def _configuration(source, skill_names=None, private_root=None):
    """Build a fresh home from selected tooling, never copy a login or trust."""
    import claude_hooks
    import codex_hooks
    home = mcp_adapters._home()
    base = source["root"] if source["kind"] == "native" else home / (".claude" if source["tool"] == "claude" else ".codex")
    writes, stamps, warnings, modes = {}, {}, [], {}
    def read(root, relative, fmt):
        path = fsguard.profile_file(root, relative)
        if not path.exists():
            stamps[str(path)] = None
            return {}
        raw = fsguard.read_profile_bytes(root, relative)
        stamps[str(path)] = profiles.digest(raw)
        value = json.loads(raw) if fmt == "json" else tomllib.loads(raw.decode())
        if not isinstance(value, dict):
            raise profiles.Refused(tr("원본 설정의 형식이 올바르지 않습니다"))
        return value
    if source["tool"] == "claude":
        settings = read(base, "settings.json", "json") if base.is_dir() else {}
        config = {k: settings[k] for k in ("model", "hooks", "language", "outputStyle") if k in settings}
        if source["kind"] == "clauth":
            own = read(source["root"], "config.toml", "toml")
            if private_root is not None:
                private, helper = _private_configuration(own, private_root)
                writes[".farshell-provider.json"] = private
            if private_root is None and (own.get("api_key") or own.get("base_url")):
                warnings.append("api-key-login-required")
            # No clauth routing or API helper is retained in the new home.
            model = (own.get("models") or {}).get("default") if isinstance(own.get("models"), dict) else own.get("model")
            if isinstance(model, str):
                config["model"] = model
            if private_root is None and own.get("env"):
                warnings.append("profile-environment-requires-setup")
        config = _safe_config(config)
        if private_root is not None and helper:
            config["apiKeyHelper"] = helper
        if "runtime-" in json.dumps(config):
            raise profiles.Refused(tr("훅에 임시 runtime 경로가 있습니다. 원본 경로를 먼저 수정하세요"))
        config, _ = claude_hooks.apply_install(config)
        writes["settings.json"] = json.dumps(config, ensure_ascii=False, indent=2).encode()
        native = read(home, ".claude.json", "json")
        # Workspace trust and account/onboarding state are deliberately absent.
        mcp = _safe_config(native.get("mcpServers", {}))
        writes[".claude.json"] = json.dumps({"mcpServers": mcp}, ensure_ascii=False, indent=2).encode()
        if settings.get("enabledPlugins"):
            warnings.append("plugins-reinstall-required")
    else:
        settings = read(base, "config.toml", "toml") if base.is_dir() else {}
        config = _safe_config({k: settings[k] for k in ("model", "mcp_servers", "skills") if k in settings})
        if settings.get("model_provider", "openai") != "openai":
            warnings.append("custom-provider-setup-required")
        text, _ = codex_hooks.apply_install(_toml(config))
        writes["config.toml"] = text.encode()
        if settings.get("plugins"):
            warnings.append("plugins-reinstall-required")
    if any(k in json.dumps(settings).lower() for k in ("api_key", "token", "password", "headers", '"env"')):
        warnings.append("credentials-not-copied")
    # Direct tooling packages are plain files. Links/large trees must be
    # reviewed separately instead of following them into an arbitrary home.
    skill_base = base / "skills"
    if skill_names is not None and (not isinstance(skill_names, list) or len(skill_names) > 500
                                   or any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", n) for n in skill_names)
                                   or len(set(skill_names)) != len(skill_names)):
        raise profiles.Refused(tr("가져올 스킬 목록이 올바르지 않습니다"))
    if skill_base.is_dir():
        packages = list(itertools.islice(skill_base.iterdir(), 501))
        if len(packages) > 500:
            raise profiles.Refused(tr("이전할 스킬 수가 한도를 넘었습니다"))
        available = {p.name for p in packages}
        if skill_names is not None and set(skill_names) - available:
            raise profiles.Refused(tr("선택한 스킬 원본이 없습니다"))
        for package in sorted(packages):
            if skill_names is not None and package.name not in skill_names:
                continue
            if not package.is_dir() or not (package / "SKILL.md").is_file():
                continue
            origin = fsguard.skill_import_source(package, home)
            # The resolved link destination is part of the reviewed source
            # fingerprint. Retargeting it between preview/apply is a conflict.
            stamps[str(package)] = str(origin)
            for path in fsguard.profile_tree(origin):
                parts = path.relative_to(origin).parts
                if '.git' in parts:
                    continue
                if path.is_dir() and not path.is_symlink():
                    continue
                if path.name in fsguard.DENY_NAMES or path.name.startswith('.env'):
                    raise profiles.Refused(tr("스킬 패키지에 자격증명 파일이 있습니다. 가져오지 않습니다"))
                relative = str(Path('skills') / package.name / path.relative_to(origin))
                raw = fsguard.read_profile_bytes(origin, str(path.relative_to(origin)))
                mode = 0o700 if path.stat().st_mode & 0o111 else 0o600
                stamps[str(path)] = profiles.digest(raw) + ':' + str(mode)
                writes[relative] = raw
                modes[relative] = mode
                if len(writes) > MAX_FILES or sum(map(len, writes.values())) > MAX_BYTES:
                    raise profiles.Refused(tr("이전할 파일 수 또는 크기가 한도를 넘었습니다"))
    return writes, stamps, sorted(set(warnings)), modes


def recorded_skill_names(source, manifest):
    """The skill selection a config import used, so later checks recompute the same files.

    Receipts written before the selection was recorded and imported without full coverage
    used the importable set (packages with links cannot be imported at all).
    """
    if manifest.get('complete_skill_coverage'):
        return None
    if 'skill_names' in manifest:
        return manifest['skill_names']
    return skill_report(source['id'])['importable']


def skill_report(source_id):
    """Split source skills into importable names and ones whose tree holds links.

    Packages that contain links (for example bundles installed by another tool)
    cannot be copied; they are listed so the person reinstalls them per profile.
    """
    source = _source(source_id)
    home = mcp_adapters._home()
    base = source["root"] if source["kind"] == "native" else home / (".claude" if source["tool"] == "claude" else ".codex")
    skill_base = base / "skills"
    ready, blocked = [], []
    if skill_base.is_dir():
        for package in sorted(itertools.islice(skill_base.iterdir(), 501)):
            if not package.is_dir() or not (package / "SKILL.md").is_file():
                continue
            try:
                for _ in fsguard.profile_tree(fsguard.skill_import_source(package, home)):
                    pass
                ready.append(package.name)
            except fsguard.FsDenied:
                blocked.append(package.name)
    return {"importable": ready, "blocked": blocked}


def _preserve_private_helper(writes, target, include_private):
    if include_private or 'settings.json' not in writes:
        return
    from agent_profile_secret import read
    private = read(target)
    if private is not None:
        _, helper = _private_configuration(private, target)
        if helper:
            settings = json.loads(writes['settings.json'])
            settings['apiKeyHelper'] = helper
            writes['settings.json'] = json.dumps(settings, ensure_ascii=False, indent=2).encode()


def config_preview(profile_id, revision, source_id, skill_names=None, include_private=False):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    source = _source(source_id)
    if source["tool"] != profile["tool"]:
        raise profiles.Refused(tr("원본과 대상 CLI가 다릅니다"))
    if type(include_private) is not bool or include_private and (profile['purpose'] != 'test' or source['kind'] != 'clauth' or source['tool'] != 'claude'):
        raise profiles.Refused(tr("비밀값 복제는 Claude 테스트 프로필에서만 가능합니다"))
    if include_private:
        from agent_profile_runtime import require_inactive
        profiles.require_no_pending_jobs(profile_id)
        require_inactive(profile_id)
    target = profiles.root(profile_id)
    writes, stamps, warnings, modes = _configuration(source, skill_names, target if include_private else None)
    _preserve_private_helper(writes, target, include_private)
    targets = {}
    for rel in writes:
        path = fsguard.profile_file(target, rel)
        targets[rel] = profiles.digest(fsguard.read_profile_bytes(target, rel)) if path.exists() else None
    plan = {"profile_id": profile_id, "revision": revision, "source_id": source_id, "stamps": stamps,
            "targets": targets, "write_hashes": {k: profiles.digest(v) for k, v in writes.items()}, "warnings": warnings, "skill_names": skill_names, "include_private": include_private}
    return {**mcp_plans.issue("profile-config-import", plan), "files": [{"path": k, "bytes": len(v), "sha256": profiles.digest(v)} for k, v in writes.items()],
            "warnings": warnings, "credentials_copied": False, "private_api_configuration_copied": include_private, "restart_required": True}


def config_apply(token):
    plan = mcp_plans.take(token, "profile-config-import")
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    pid = plan["profile_id"]
    profile = profiles.get(pid)
    profiles.require_revision(profile, plan["revision"])
    source = _source(plan["source_id"])
    if plan.get('include_private'):
        from agent_profile_runtime import require_inactive
        profiles.require_no_pending_jobs(pid)
        require_inactive(pid)
    writes, stamps, warnings, modes = _configuration(source, plan.get('skill_names'), profiles.root(pid) if plan.get('include_private') else None)
    _preserve_private_helper(writes, profiles.root(pid), plan.get('include_private'))
    if stamps != plan["stamps"] or {k: profiles.digest(v) for k, v in writes.items()} != plan["write_hashes"]:
        raise profiles.Refused(tr("이전 원본이 바뀌었습니다. 다시 검토하세요"))
    target = profiles.root(pid)
    jid = profiles.job_begin(pid, "config-import")
    results = []
    try:
        # All targets are checked before any mutation, and again under each
        # native file lock. Cross-file writes are explicitly not atomic.
        for rel, expected in plan["targets"].items():
            path = fsguard.profile_file(target, rel)
            actual = profiles.digest(fsguard.read_profile_bytes(target, rel)) if path.exists() else None
            if actual != expected:
                raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
        for rel, raw in writes.items():
            path = fsguard.profile_file(target, rel)
            fsguard.private_profile_directory(path.parent)
            with config_io.locked(path):
                actual = profiles.digest(fsguard.read_profile_bytes(target, rel)) if path.exists() else None
                if actual != plan["targets"][rel]:
                    raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
                # Persistent original for conflict-aware manual recovery.
                if path.exists():
                    backup = fsguard.private_profile_directory(profiles.directory() / "backups" / jid)
                    saved = backup / (profiles.digest(rel.encode()) + ".bak")
                    _write_bytes(saved, fsguard.read_profile_bytes(target, rel))
                entry = {"path": rel, "sha256": profiles.digest(raw), "before_sha256": plan['targets'][rel],
                         "before_mode": 0o700 if path.exists() and path.stat().st_mode & 0o111 else 0o600, "state": "writing"}
                results.append(entry)
                # Write-ahead evidence survives a crash between rename and DB
                # completion. Recovery compares before/after rather than guessing.
                profiles.job_finish(jid, 'running', {'files': results})
                _write_bytes(path, raw, modes.get(rel, 0o600))
                entry['state'] = 'done'
                profiles.job_finish(jid, 'running', {'files': results})
        manifest = {"files": results, "warnings": warnings,
                    "complete_skill_coverage": plan.get('skill_names') is None,
                    "skill_names": plan.get('skill_names'),
                    "private_configuration": bool(plan.get('include_private')),
                    "source_fingerprint": profiles.digest(json.dumps(stamps, sort_keys=True).encode())}
        result = {"status": "ok", "files": results, "warnings": warnings, "credentials_copied": False,
                  "private_api_configuration_copied": bool(plan.get('include_private'))}
        return profiles.complete_job(pid, profile['revision'], jid, result, receipt=(source['id'], 'config', manifest, True))
    except BaseException:
        if results:
            profiles.bump(pid, profile["revision"], "partial")
        profiles.job_finish(jid, "partial" if results else "failed", {"files": results, "recovery_required": bool(results)})
        raise


def _write_bytes(path, raw, mode=0o600):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _receipt(pid, source_id, kind, manifest, verified=False):
    rid = secrets.token_hex(16)
    with profiles.database() as db:
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?)", (rid, pid, source_id, kind, json.dumps(manifest), int(verified), time.time()))
    return rid


def conversations(source_id):
    source = _source(source_id)
    home = mcp_adapters._home()
    roots = [source["root"]]
    if source["kind"] == "clauth":
        roots += [source["root"] / "codex-home", home / (".claude" if source["tool"] == "claude" else ".codex")]
    result = {}
    scanned = 0
    owners = {}
    if source['kind'] == 'clauth':
        owner_root = home / '.clauth'
        if fsguard.profile_file(owner_root, 'session_profiles.json').exists():
            owners = _json(owner_root, 'session_profiles.json').get('sessions', {})
            if not isinstance(owners, dict):
                raise profiles.Refused(tr("대화 소유 기록의 형식이 올바르지 않습니다"))
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        bases = [root / 'projects'] if source['tool'] == 'claude' else [root / 'sessions', root / 'archived_sessions']
        paths = (path for base in bases if base.is_dir() for path in fsguard.profile_tree(base) if path.suffix == '.jsonl')
        for path in paths:
            scanned += 1
            if scanned > MAX_FILES:
                raise profiles.Refused(tr("대화가 너무 많습니다. 원본 범위를 좁히세요"))
            rel = str(path.relative_to(root))
            path = fsguard.profile_file(root, rel)
            # Listing never loads whole transcripts (a shared ~/.claude holds
            # gigabytes): only the first record and the size are read here.
            head, size = fsguard.read_profile_head(root, rel, MAX_FIRST_RECORD)
            try:
                first = json.loads(head.splitlines()[0])
            except (ValueError, IndexError):
                continue
            sid = path.stem if source["tool"] == "claude" else (first.get("payload") or {}).get("id") if isinstance(first, dict) else None
            if not isinstance(sid, str) or not _SID.fullmatch(sid):
                continue
            ownership = 'native' if source['kind'] == 'native' else 'profile'
            if source['kind'] == 'clauth' and root == home / ('.claude' if source['tool'] == 'claude' else '.codex'):
                owner = owners.get(sid)
                if isinstance(owner, dict) and isinstance(owner.get('known'), str):
                    if owner['known'] != source['name']:
                        continue
                    ownership = 'known'
                else:
                    ownership = 'contested' if owner == 'contested' else 'unknown'
            key = profiles.digest(str(path).encode())[:32]
            result[key] = {"id": key, "ownership": ownership, "conversation": sid, "relative": rel, "root": root,
                           "bytes": size, "mtime": path.stat().st_mtime, "tool": source["tool"],
                           "rollout_id": path.stem[-36:] if source['tool'] == 'codex' else sid,
                           "history_base": (first.get('payload') or {}).get('history_base') if source['tool'] == 'codex' else None}
    return result


def conversation_list(source_id):
    return {"conversations": [{k: row[k] for k in ("id", "conversation", "bytes", "tool", "ownership")} for row in conversations(source_id).values()]}


def _conversation_bundle(source_id, row, rows):
    files = {}
    visited = set()
    total = 0
    def add(root, relative):
        nonlocal total
        if relative in files:
            if files[relative]['root'] != str(root):
                raise profiles.Refused(tr("대화 부속 파일의 경로가 충돌합니다"))
            return
        sha, size, _ = fsguard.hash_profile_file(root, relative, MAX_CONVERSATION_BYTES - total)
        total += size
        if len(files) >= MAX_FILES:
            raise profiles.Refused(tr("대화 파일의 전체 크기가 한도를 넘었습니다"))
        files[relative] = {'root': str(root), 'relative': relative, 'bytes': size, 'sha256': sha}
    def collect(item, depth=0):
        identity = (str(item['root']), item['relative'])
        if identity in visited or depth > 32:
            raise profiles.Refused(tr("대화 상속 기록이 순환하거나 너무 깊습니다"))
        if item['ownership'] in ('unknown', 'contested'):
            raise profiles.Refused(tr("대화 소유 프로필이 확인되지 않았습니다. 원본 소유 기록을 확인하세요"))
        visited.add(identity)
        add(item['root'], item['relative'])
        if item['tool'] == 'codex' and item.get('history_base') is not None:
            base = item['history_base']
            if not isinstance(base, dict) or not isinstance(base.get('thread_id'), str) or not _SID.fullmatch(base['thread_id']) or type(base.get('end_byte_offset')) is not int or base['end_byte_offset'] <= 0 or type(base.get('end_ordinal_exclusive')) is not int or base['end_ordinal_exclusive'] < 0:
                raise profiles.Refused(tr("대화 상속 기록 형식이 올바르지 않습니다"))
            parents = [r for r in rows.values() if r['root'] == item['root'] and r.get('rollout_id') == base['thread_id']]
            if len(parents) != 1 or parents[0]['bytes'] < base['end_byte_offset']:
                raise profiles.Refused(tr("대화가 참조하는 이전 기록을 찾지 못했습니다"))
            collect(parents[0], depth + 1)
        elif item['tool'] == 'claude':
            relative = Path(item['relative'])
            for companion in (relative.with_suffix(''), Path('file-history') / item['conversation']):
                folder = fsguard.profile_file(item['root'], str(companion))
                if folder.exists():
                    for path in fsguard.profile_tree(folder):
                        add(item['root'], str(path.relative_to(item['root'])))
    collect(row)
    return files


def conversation_preview(profile_id, revision, source_id, conversation_id):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    source = _source(source_id)
    rows = conversations(source_id)
    row = rows.get(profiles.identifier(conversation_id))
    if not row or source['tool'] != profile['tool']:
        raise profiles.Refused(tr("이전할 대화를 찾지 못했습니다"))
    bundle = _conversation_bundle(source_id, row, rows)
    target_root = profiles.root(profile_id)
    if fsguard.profile_file(target_root, row['relative']).exists():
        raise profiles.Refused(tr("대상에 대화가 이미 있습니다. 덮어쓰지 않습니다"))
    targets = {}
    for relative, item in bundle.items():
        target = fsguard.profile_file(target_root, relative)
        existing = fsguard.hash_profile_file(target_root, relative, MAX_CONVERSATION_BYTES)[0] if target.exists() else None
        if existing is not None and existing != item['sha256']:
            raise profiles.Refused(tr("대화 부속 파일의 경로가 충돌합니다"))
        targets[relative] = existing
    plan = {'profile_id': profile_id, 'revision': revision, 'source_id': source_id, 'conversation_id': conversation_id,
            'bundle': bundle, 'targets': targets, 'sha256': bundle[row['relative']]['sha256'], 'relative': row['relative']}
    return {**mcp_plans.issue('profile-conversation', plan), 'conversation': row['conversation'],
            'bytes': sum(i['bytes'] for i in bundle.values()), 'files': len(bundle), 'sha256': bundle[row['relative']]['sha256'], 'source_preserved': True,
            'warning': tr("대상 계정으로 기존 대화를 보낼 수 있습니다. 조직과 과금 계정을 확인하세요")}


def conversation_apply(token):
    plan = mcp_plans.take(token, 'profile-conversation')
    if not plan:
        raise profiles.Refused(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    profile = profiles.get(plan['profile_id'])
    profiles.require_revision(profile, plan['revision'])
    rows = conversations(plan['source_id'])
    row = rows.get(plan['conversation_id'])
    if not row or _conversation_bundle(plan['source_id'], row, rows) != plan['bundle']:
        raise profiles.Refused(tr("대화 원본이 바뀌었습니다. 다시 검토하세요"))
    root = profiles.root(profile['id'])
    jid = profiles.job_begin(profile['id'], 'conversation-import')
    written = []
    try:
        # Validate the whole destination before starting a multi-file copy.
        for relative, expected in plan['targets'].items():
            target = fsguard.profile_file(root, relative)
            actual = fsguard.hash_profile_file(root, relative, MAX_CONVERSATION_BYTES)[0] if target.exists() else None
            if actual != expected:
                raise profiles.Refused(tr("대상 설정이 바뀌었습니다. 다시 검토하세요"))
        for relative, item in plan['bundle'].items():
            if plan['targets'][relative] is not None:
                continue
            target = fsguard.profile_file(root, relative)
            fsguard.private_profile_directory(target.parent)
            with config_io.locked(target):
                if target.exists():
                    raise profiles.Refused(tr("대화 부속 파일의 경로가 충돌합니다"))
                written.append({'path': relative, 'sha256': item['sha256'], 'before_sha256': None})
                profiles.job_finish(jid, 'running', {'files': written})
                try:
                    fsguard.copy_profile_file(Path(item['root']), relative, target, item['sha256'], MAX_CONVERSATION_BYTES)
                except fsguard.FsChanged:
                    raise profiles.Refused(tr("대화 원본이 바뀌었습니다. 다시 검토하세요"))
        native_read = False
        if row['tool'] == 'codex':
            from agent_profile_runtime import codex_read
            native_read = codex_read(profile['id'], row['conversation'])
        manifest = {'conversation': row['conversation'], 'relative': row['relative'], 'bytes': plan['bundle'][row['relative']]['bytes'], 'sha256': plan['sha256'],
                    'bundle': [{k: item[k] for k in ('relative', 'bytes', 'sha256')} for item in plan['bundle'].values()],
                    'bundle_complete': True, 'native_read_verified': native_read}
        result = {'status': 'copied', 'conversation': row['conversation'], 'files': len(plan['bundle']),
                  'native_read_verified': native_read, 'resume_verified': False, 'source_preserved': True,
                  'file_results': written}
        return profiles.complete_job(profile['id'], profile['revision'], jid, result, bump_revision=False,
                                     receipt=(plan['source_id'], 'conversation', manifest, False))
    except BaseException:
        if written:
            profiles.bump(profile['id'], profile['revision'], 'partial')
        profiles.job_finish(jid, 'partial' if written else 'failed', {'files': written, 'recovery_required': bool(written)})
        raise


def require_conversation(profile_id, sid):
    with profiles.database() as db:
        rows = db.execute("SELECT manifest,verified FROM receipts WHERE profile_id=? AND kind='conversation'", (profile_id,)).fetchall()
    for row in rows:
        data = json.loads(row["manifest"])
        if data["conversation"] != sid:
            continue
        target_root = profiles.root(profile_id)
        for item in data.get('bundle', []):
            if item['relative'] == data['relative']:
                continue
            full, size, head = fsguard.hash_profile_file(target_root, item['relative'], MAX_CONVERSATION_BYTES, item['bytes'])
            appended = row['verified'] and item['relative'].endswith('.jsonl') and size >= item['bytes'] and head == item['sha256']
            if full != item['sha256'] and not appended:
                raise profiles.Refused(tr("검토한 대화 부속 파일이 바뀌었습니다"))
        size = data.get('bytes')
        full, length, head = fsguard.hash_profile_file(target_root, data["relative"], MAX_CONVERSATION_BYTES, size if type(size) is int else None)
        if full == data['sha256']:
            return
        # A verified native resume may append turns. Preserve the reviewed
        # prefix; replacement/truncation never becomes an accepted resume.
        if row['verified'] and type(size) is int and length >= size and head == data['sha256']:
            return
    raise profiles.Refused(tr("검토한 대화 이관 기록이 없습니다"))


def mark_resumed(profile_id, sid):
    with profiles.database() as db:
        for row in db.execute("SELECT id,manifest FROM receipts WHERE profile_id=? AND kind='conversation'", (profile_id,)).fetchall():
            if json.loads(row["manifest"])["conversation"] == sid:
                db.execute("UPDATE receipts SET verified=1 WHERE id=?", (row["id"],))


def _legacy_runtime_state():
    import hook_identity
    from agent_config_context import process_args
    table = hook_identity._ps_table()
    if table is None:
        return 'unknown'
    ambiguous = False
    legacy = (mcp_adapters._home() / '.clauth').resolve()
    for pid, (_, _, _, comm) in table.items():
        if not (Path(comm).name.startswith('claude') or Path(comm).name == 'codex'):
            continue
        try:
            _, env = process_args(pid)
            value = env.get('CLAUDE_CONFIG_DIR') or env.get('CODEX_HOME')
            if value and Path(value).expanduser().resolve().is_relative_to(legacy):
                return 'present'
        except (OSError, ValueError):
            ambiguous = True
    return 'unknown' if ambiguous else 'absent'


def _source_has_api_quota(source):
    """clauth 0.17.0 providers/mod.rs routes only these public service hosts.

    An arbitrary proxy has no generic quota API in either implementation. Never
    invent a percentage or send its key to one of these unrelated services.
    """
    from urllib.parse import urlsplit
    own = tomllib.loads(fsguard.read_profile_bytes(source['root'], 'config.toml').decode())
    custom = own.get('env', {})
    if not isinstance(custom, dict):
        return True
    endpoints = [own.get('base_url'), custom.get('ANTHROPIC_BASE_URL')]
    known = {'api.deepseek.com', 'api.z.ai', 'open.bigmodel.cn', 'openrouter.ai',
             'api.minimax.io', 'api.minimaxi.com', 'token-plan.ap-southeast-1.maas.aliyuncs.com',
             'token-plan.cn-beijing.maas.aliyuncs.com', 'coding-intl.dashscope.aliyuncs.com', 'coding.dashscope.aliyuncs.com'}
    try:
        from usage.api_profiles import ALIBABA
        hosts = [urlsplit(endpoint).hostname for endpoint in endpoints if isinstance(endpoint, str)]
        if hosts and hosts[-1] in ALIBABA:
            return False
        return any(host in known for host in hosts) or own.get('provider') is not None
    except ValueError:
        return True


def retirement_report():
    """Read current independent evidence; never infer readiness from import alone."""
    from agent_profile_runtime import authentication_fingerprint, session_status
    from agent_profile_secret import read as read_private
    source_rows = [s for s in sources().values() if s['kind'] == 'clauth']
    with profiles.database() as db:
        receipts = [dict(r) | {'manifest': json.loads(r['manifest'])} for r in db.execute('SELECT * FROM receipts ORDER BY created_at')]
        refresh = {r['profile_id']: dict(r) for r in db.execute('SELECT * FROM refresh_tests')}
        has_observations = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
        observations = {r['profile_id']: dict(r) for r in db.execute('SELECT * FROM observations')} if has_observations else {}
        sessions = [dict(r) for r in db.execute('SELECT * FROM sessions')]
        has_api = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_quota_observations'").fetchone()
        api_observations = {r['profile_id']: dict(r) for r in db.execute('SELECT * FROM api_quota_observations')} if has_api else {}
    checked_sessions = [session_status(s['name']) for s in sessions]
    pinned = {s['profile_id'] for s in checked_sessions if s['state'] == 'running' and s['applied_revision'] == s['desired_revision']}
    active = [p for pid in pinned if (p := profiles.get(pid))['archived_at'] is None]
    concurrent_profiles = set()
    for index, first in enumerate(active):
        for second in active[index + 1:]:
            if first['account_id'] != second['account_id']:
                continue
            a = observations.get(first['id'], {}).get('subject')
            b = observations.get(second['id'], {}).get('subject')
            fresh = all(time.time() - observations.get(p['id'], {}).get('last_at', 0) <= 1800 and observations.get(p['id'], {}).get('last_fingerprint') == authentication_fingerprint(p['id']) for p in (first, second))
            if fresh and a and a == b:
                concurrent_profiles.update((first["id"], second["id"]))
            elif read_private(profiles.root(first['id'])) and read_private(profiles.root(second['id'])):
                from usage.api_profiles import credential
                if not credential(read_private(profiles.root(first['id']))) or not credential(read_private(profiles.root(second['id']))):
                    continue
                fingerprint = authentication_fingerprint(first['id'])
                if fingerprint and fingerprint == authentication_fingerprint(second['id']):
                    concurrent_profiles.update((first['id'], second['id']))
    legacy = _legacy_runtime_state()
    items = []
    for source in source_rows:
        imports = [r for r in receipts if r['source_id'] == source['id']]
        configs = [r for r in imports if r['kind'] == 'config' and r['verified']]
        blockers = []
        if not concurrent_profiles:
            blockers.append('same-account-concurrency-not-certified')
        if legacy != 'absent':
            blockers.append('legacy-runtime-present' if legacy == 'present' else 'legacy-runtime-check-unavailable')
        candidates = []
        resolved_by_profile = {}
        for config in configs:
            pid = config['profile_id']
            reasons = list(config['manifest'].get('warnings', []))
            try:
                from agent_profile_parity import compare
                comparison = compare(source, pid, config['manifest'])
                reasons = comparison['blockers']
                resolved_by_profile[pid] = comparison['resolved_warnings']
            except (ValueError, OSError, fsguard.FsDenied):
                reasons.append('configuration-verification-unavailable')
            if pid not in concurrent_profiles:
                reasons.append('same-account-concurrency-not-certified')
            if not config['manifest'].get('complete_skill_coverage'):
                reasons.append('skill-coverage-incomplete')
            profile = profiles.get(pid)
            if profile['archived_at'] is not None:
                reasons.append('profile-archived')
            if profile['state'] != 'ready':
                reasons.append('profile-recovery-required')
            try:
                profiles.require_no_pending_jobs(pid)
            except profiles.Refused:
                reasons.append('profile-recovery-required')
            root = profiles.root(pid)
            try:
                _, stamps, _, _ = _configuration(source, recorded_skill_names(source, config['manifest']),
                                                 root if config['manifest'].get('private_configuration') else None)
                current = profiles.digest(json.dumps(stamps, sort_keys=True).encode())
                if current != config['manifest'].get('source_fingerprint'):
                    reasons.append('source-changed-after-import')
            except (ValueError, OSError, fsguard.FsDenied):
                reasons.append('source-verification-unavailable')
            fingerprint = authentication_fingerprint(pid)
            probes = [r for r in receipts if r['profile_id'] == pid and r['kind'] == 'native-probe' and r['verified']
                      and fingerprint and r['manifest'].get('auth_fingerprint') == fingerprint]
            if not probes:
                reasons.append('native-inference-not-certified')
            from agent_profile_identity import requires_identity, matches
            observation = observations.get(pid, {})
            try:
                if requires_identity(source['id']) and not matches(source['id'], observation, fingerprint):
                    reasons.append('source-account-not-certified')
            except (ValueError, OSError, fsguard.FsDenied):
                reasons.append('source-account-not-certified')
            from usage.api_profiles import declares_api_credentials
            private = read_private(root)
            if private is None or not declares_api_credentials(private):
                test = refresh.get(pid, {})
                if not observation.get('subject') or observation.get('last_fingerprint') != fingerprint or time.time() - observation.get('last_at', 0) > 1800:
                    reasons.append('native-usage-not-certified')
                if not observation.get('rotation_after_expiry') and not test.get('completed_at'):
                    reasons.append('native-refresh-not-certified')
            else:
                if _source_has_api_quota(source):
                    observation = api_observations.get(pid, {})
                    private_fingerprint = profiles.digest(json.dumps(read_private(root), sort_keys=True).encode())
                    if observation.get('auth_fingerprint') != private_fingerprint or time.time() - observation.get('checked_at', 0) > 1800:
                        reasons.append('api-provider-usage-not-certified')
            if pid not in pinned:
                reasons.append('live-profile-session-not-certified')
            candidates.append((pid, sorted(set(reasons))))
        if not candidates:
            blockers.append('configuration-not-migrated')
        elif all(reasons for _, reasons in candidates):
            blockers.extend(min((reasons for _, reasons in candidates), key=len))
        rows = sorted(conversations(source['id']).values(), key=lambda r: r['mtime'], reverse=True)
        owned = [r for r in rows if r['ownership'] not in ('unknown', 'contested')]
        unowned = len(rows) - len(owned)
        for position, row in enumerate(owned):
            row_receipts = [r for r in imports if r['kind'] == 'conversation' and r['manifest'].get('conversation') == row['conversation']
                            and r['manifest'].get('bundle_complete')]
            matches = []
            if row_receipts:
                # Hash only conversations that have a receipt, streaming.
                try:
                    current = fsguard.hash_profile_file(row['root'], row['relative'], MAX_BYTES)[0]
                except fsguard.FsDenied:
                    current = None
                matches = [r for r in row_receipts if r['manifest'].get('sha256') == current]
            if position < RETIRE_VERIFY_RECENT:
                if not any(r['verified'] for r in matches):
                    blockers.append('conversation-resume-not-verified')
            elif not matches:
                blockers.append('conversation-not-imported')
        limitations = ['conversation-ownership-unresolved'] if unowned else []
        try:
            from usage.api_profiles import ALIBABA
            own = tomllib.loads(fsguard.read_profile_bytes(source['root'], 'config.toml').decode())
            from urllib.parse import urlsplit
            env = own.get('env', {})
            endpoint = (env.get('ANTHROPIC_BASE_URL') if isinstance(env, dict) else None) or own.get('base_url')
            if isinstance(endpoint, str) and urlsplit(endpoint).hostname in ALIBABA:
                limitations.append('alibaba-quota-unsupported')
        except (ValueError, OSError, fsguard.FsDenied):
            pass
        items.append({'source_id': source['id'], 'name': source['name'], 'tool': source['tool'], 'blockers': sorted(set(blockers)),
                      'accepted_limitations': limitations,
                      'candidates': [{'profile_id': pid, 'blockers': reasons, 'resolved_warnings': resolved_by_profile.get(pid, [])} for pid, reasons in candidates]})
    return {'ready_to_retire': bool(items) and not any(i['blockers'] for i in items), 'profiles': items,
            'source_preserved': True, 'clauth_stopped': False, 'legacy_runtime_state': legacy, 'concurrency_certified': bool(concurrent_profiles),
            'note': tr("실제 로그인·갱신·사용량·대화 재개 검증 전에는 clauth를 중지하지 않습니다")}


def rollback(profile_id, revision, job_id, *, preview=False):
    """Restore only files still equal to this import's writes, never replay it."""
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    job = profiles.job_status(job_id)
    if job['profile_id'] != profile_id or job['kind'] not in ('config-import', 'conversation-import', 'provider-edit', 'hook-edit') or job['state'] not in ('partial', 'done', 'partial-rollback'):
        raise profiles.Refused(tr("되돌릴 수 있는 구성 가져오기 작업이 아닙니다"))
    files = job['result'].get('file_results', job['result'].get('files', []))
    if not isinstance(files, list) or not files:
        raise profiles.Refused(tr("되돌릴 파일 기록이 없습니다"))
    if any(r.get('path') == '.farshell-provider.json' for r in files):
        from agent_profile_runtime import require_inactive
        require_inactive(profile_id)
    root = profiles.root(profile_id)
    backup = profiles.directory() / 'backups' / job_id
    restores = []
    for row in files:
        rel = row['path']
        path = fsguard.profile_file(root, rel)
        actual = fsguard.hash_profile_file(root, rel, MAX_CONVERSATION_BYTES)[0] if path.exists() else None
        if actual == row.get('before_sha256'):
            continue
        if actual != row['sha256']:
            raise profiles.Refused(tr("가져온 뒤 수정된 파일이 있어 되돌리기를 중단했습니다"))
        before = row.get('before_sha256')
        saved = fsguard.read_profile_bytes(backup, profiles.digest(rel.encode()) + '.bak') if before else None
        if saved is not None and profiles.digest(saved) != before:
            raise profiles.Refused(tr("복구 보관본이 바뀌었습니다"))
        restores.append((path, actual, saved, row.get('before_mode', 0o600)))
    if preview:
        return {'profile_id': profile_id, 'job_id': job_id, 'files': [r['path'] for r in files], 'source_preserved': True}
    done = []
    try:
        for path, expected, saved, mode in reversed(restores):
            with config_io.locked(path):
                if (fsguard.hash_profile_file(root, str(path.relative_to(root)), MAX_CONVERSATION_BYTES)[0] if path.exists() else None) != expected:
                    raise profiles.Refused(tr("가져온 뒤 수정된 파일이 있어 되돌리기를 중단했습니다"))
                if saved is None:
                    path.unlink()
                else:
                    _write_bytes(path, saved, 0o700 if mode == 0o700 else 0o600)
                done.append(str(path.relative_to(root)))
        return profiles.complete_job(profile_id, revision, job_id, {'status': 'ok', 'restored_files': done, 'restart_required': True}, job_state='rolled-back')
    except BaseException:
        if done:
            profiles.bump(profile_id, revision, 'partial')
            profiles.job_finish(job_id, 'partial-rollback', {'restored_files': done, 'file_results': files, 'recovery_required': True})
        raise
