"""Native profile launch/login. No global environment or auth-store swapping."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
import tmux_runner
import vt_paths
from i18n import tr

logger = logging.getLogger(__name__)

RECONCILE_SECONDS = 5

_BASE_ENV = {"PATH", "HOME", "TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME", "SHELL", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "SSH_AUTH_SOCK"}


def environment(profile_id, project=None, *, record=False):
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    env = {k: v for k, v in os.environ.items() if k in _BASE_ENV}
    env["HOME"] = str(vt_paths.user_home())
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    env["CLAUDE_CONFIG_DIR" if profile["tool"] == "claude" else "CODEX_HOME"] = str(profiles.root(profile_id))
    env["FSH_PROFILE_ID"] = profile_id
    # Isolated servers must propagate their own hook destination, never discover
    # the user's live server through the ordinary ~/.vt.env fallback.
    for key in ("VT_CONFIG", "VT_STATE_DIR", "VT_RUN_DIR", "VT_TMUX_SOCKET", "VT_PORT", "VT_SERVER"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    import agent_config
    ctx = profiles.context(profile_id, project)
    env = (agent_config.prepare_launch_environment if record else agent_config.launch_environment)({**ctx, "env": env})
    forbidden = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_OAUTH_TOKEN"}
    if forbidden.intersection(env):
        raise profiles.Refused(tr("프로필 환경변수가 네이티브 로그인 계정을 우회합니다"))
    from agent_profile_secret import read
    private = read(profiles.root(profile_id))
    if private is not None:
        # Reuse import validation for manual edits as well. Do not relax the
        # ambient/overlay credential ban when a private profile exists.
        from agent_profile_migration import _private_configuration
        raw, _ = _private_configuration(private, profiles.root(profile_id))
        env.update(json.loads(raw)['env'])
    from agent_profile_token import environment_value
    token = environment_value(profile_id)
    if token:
        if private is not None:
            raise profiles.Refused(tr("API 인증과 장기 토큰이 함께 설정되어 있습니다. 하나만 남기세요"))
        # Only the profile's own stored mint reaches the CLI, never an ambient value.
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def binary(tool):
    if tool not in ("claude", "codex"):
        raise profiles.Refused(tr("지원하지 않는 CLI입니다"))
    path = shutil.which(tool)
    if not path:
        raise profiles.Refused(tr("CLI 실행 파일을 찾지 못했습니다"))
    # Native launchers may be version-named symlink targets. Keep argv[0]'s
    # CLI name so process attribution and the reviewed resume contract agree.
    return str(Path(path).absolute())


def validate_native_files(profile_id):
    profile = profiles.get(profile_id)
    root = profiles.root(profile_id)
    names = ('settings.json', '.claude.json', '.credentials.json') if profile['tool'] == 'claude' else ('config.toml', 'hooks.json', 'auth.json')
    for name in names:
        fsguard.profile_file(root, name)
    import agent_profile_config
    agent_profile_config._read(profile_id)
    agent_profile_config.inventory(profile_id)
    if profile['tool'] == 'claude':
        import agent_profile_resources
        disabled = agent_profile_resources._disabled(root)
        path = fsguard.profile_file(root, '.claude.json')
        data = json.loads(fsguard.read_profile_bytes(root, path.name)) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get('mcpServers', {}), dict) or set(data.get('mcpServers', {})).intersection(disabled):
            raise profiles.Refused(tr("MCP 반영이 중단되어 원본과 비활성 보관본이 겹칩니다. 복구가 필요합니다"))


def run(profile_id, args, *, timeout=30):
    """Bound native output on disk; never log its content or command errors."""
    profile = profiles.get(profile_id)
    validate_native_files(profile_id)
    with tempfile.TemporaryFile() as out:
        process = subprocess.Popen([binary(profile["tool"]), *args], env=environment(profile_id),
                                   cwd=profiles.root(profile_id), stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or os.fstat(out.fileno()).st_size > 1024 * 1024:
                    raise profiles.Refused(tr("CLI 작업이 시간 또는 출력 한도를 넘었습니다"))
                time.sleep(.05)
        finally:
            if process.poll() is None:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        out.seek(0)
        data = out.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise profiles.Refused(tr("CLI 출력이 너무 큽니다"))
        return process.returncode, data


def auth_status(profile_id):
    profile = profiles.get(profile_id)
    args = ["auth", "status", "--json"] if profile["tool"] == "claude" else ["login", "status"]
    code, body = run(profile_id, args, timeout=15)
    # Login status is local CLI evidence, not a provider-verified subject.
    logged_in = False
    if profile["tool"] == "claude":
        try:
            value = json.loads(body)
            logged_in = code == 0 and isinstance(value, dict) and value.get("loggedIn") is True
        except (ValueError, UnicodeError):
            pass
    else:
        # Codex writes login status to stderr in some versions; the successful
        # command's exit code is its documented authentication test.
        logged_in = code == 0
    command = [str(Path(__file__).parent.parent / "bin" / "fsh"), "profile", "login", profile_id]
    if vt_paths.is_isolated():
        # A command copied out of a disposable server must select its registry,
        # not silently look up the ID in the person's production ~/.vt.
        command = ['env', 'HOME=' + str(vt_paths.user_home()),
                   *[key + '=' + os.environ[key] for key in ('VT_CONFIG', 'VT_STATE_DIR', 'VT_RUN_DIR') if os.environ.get(key)], *command]
    result = {"profile_id": profile_id, "authenticated": logged_in,
              "identity_state": "unverified", "checked_at": time.time(),
              "login_command": shlex.join(command), "auth_mode": "login"}
    from agent_profile_token import configured as token_configured, status as token_status
    if token_configured(profile_id):
        # `auth status` only sees the injected variable: it proves a token is present,
        # not that the provider still accepts it (that takes a real request: probe).
        result.update(auth_mode="long-lived-token", token=token_status(profile_id),
                      login_command=shlex.join([*command[:-2], "token", "set", profile_id]))
    return result


def login(profile_id):
    """Interactive local CLI only; no login code/token is captured by the web API."""
    profile = profiles.get(profile_id)
    validate_native_files(profile_id)
    if not sys.stdin.isatty():
        raise profiles.Refused(tr("프로필 로그인은 대화형 터미널에서 실행하세요"))
    profiles.require_active(profile)
    from agent_profile_token import configured as token_configured
    if token_configured(profile_id):
        raise profiles.Refused(tr("장기 토큰 프로필입니다. 다시 등록하려면 fsh profile token set을 쓰세요"))
    profiles.require_no_pending_jobs(profile_id)
    require_inactive(profile_id)
    args = ["auth", "login"] if profile["tool"] == "claude" else ["login", "--device-auth"]
    jid = profiles.job_begin(profile_id, 'login')
    code = None
    with profiles.database() as db:
        db.execute('DELETE FROM refresh_tests WHERE profile_id=?', (profile_id,))
    import signal

    def hangup(*_):
        # A closed terminal window must still close this job, or the profile stays "running".
        raise SystemExit(129)
    previous = {sig: signal.signal(sig, hangup) for sig in (signal.SIGHUP, signal.SIGTERM)}
    try:
        code = subprocess.call([binary(profile["tool"]), *args], env=environment(profile_id), cwd=profiles.root(profile_id))
        return code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        profiles.job_finish(jid, 'done' if code == 0 else 'interrupted', {'exit_code': code})


def codex_read(profile_id, conversation):
    """Read metadata through native app-server without starting a model turn."""
    import selectors
    import signal
    if profiles.get(profile_id)["tool"] != "codex" or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", conversation):
        return False
    process = subprocess.Popen([binary("codex"), "app-server", "--listen", "stdio://"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=environment(profile_id), cwd=profiles.root(profile_id), start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""
    total = 0
    def send(data):
        process.stdin.write(json.dumps(data).encode() + b"\n")
        process.stdin.flush()
    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "farshell_profile_import", "version": "1"}}})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            if not selector.select(.2):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            total += len(chunk)
            buffer += chunk
            if total > 1024 * 1024:
                return False
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                data = json.loads(line)
                if data.get("id") == 1:
                    if "error" in data:
                        return False
                    send({"method": "initialized"})
                    send({"id": 2, "method": "thread/read", "params": {"threadId": conversation, "includeTurns": False}})
                elif data.get("id") == 2:
                    return (data.get("result") or {}).get("thread", {}).get("id") == conversation
        return False
    except (ValueError, OSError, TypeError):
        return False
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        process.stdin.close()
        process.stdout.close()


def _created(name):
    value = tmux_runner.run_text(["display-message", "-p", "-t", f"={name}:", "#{session_id}:#{session_created}"])
    if not value or not re.fullmatch(r"\$\d+:\d+", value.strip()):
        raise profiles.Refused(tr("새 세션의 신원을 확인하지 못했습니다"))
    return value.strip()


def launch(profile_id, revision, cwd, conversation=None):
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    profiles.require_no_pending_jobs(profile_id)
    if profile["state"] != "ready":
        raise profiles.Refused(tr("프로필 복구를 먼저 완료하세요"))
    directory = fsguard.resolve_under_roots(cwd, check_hidden=False)
    if not directory.is_dir():
        raise profiles.Refused(tr("작업 디렉터리를 찾지 못했습니다"))
    if not auth_status(profile_id)["authenticated"]:
        raise profiles.Refused(tr("프로필에 먼저 로그인하세요"))
    if conversation is not None:
        if not isinstance(conversation, str) or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", conversation):
            raise profiles.Refused(tr("대화 ID가 올바르지 않습니다"))
        from agent_profile_migration import require_conversation
        require_conversation(profile_id, conversation)
    launch_auth = authentication_fingerprint(profile_id)
    launch_subject = None
    with profiles.database() as db:
        fields = {r[1] for r in db.execute('PRAGMA table_info(observations)')}
        if 'last_fingerprint' in fields:
            observed = db.execute('SELECT * FROM observations WHERE profile_id=?', (profile_id,)).fetchone()
            if observed and observed['last_fingerprint'] == launch_auth and time.time() - observed['last_at'] <= 1800:
                launch_subject = observed['subject']
    env = environment(profile_id, str(directory), record=True)
    argv = [binary(profile["tool"])]
    if conversation:
        argv += ["--resume", conversation] if profile["tool"] == "claude" else ["resume", conversation]
    name = "profile-" + secrets.token_hex(8)
    folder = fsguard.private_profile_directory(profiles.directory() / "launch")
    payload = folder / (secrets.token_hex(16) + ".json")
    config_io.atomic_write(payload, json.dumps({"argv": argv, "env": env, "cwd": str(directory)}))
    os.chmod(payload, 0o600)
    runner = str(Path(__file__).with_name("agent_config_launch.py"))
    command = shlex.join([sys.executable, runner, str(payload)])
    started_at = time.time()
    try:
        # Profile selection is pinned in the child payload, never tmux -g env.
        code, _, _ = tmux_runner.run(["new-session", "-d", "-s", name, "-c", str(directory), command], timeout=5)
        if code:
            raise profiles.Refused(tr("프로필 세션을 시작하지 못했습니다"))
        created = _created(name)
        with profiles.database() as db:
            db.execute("INSERT INTO sessions (name,profile_id,created,profile_revision,conversation,started_at,state,launch_auth_fingerprint,launch_subject) VALUES (?,?,?,?,?,?,?,?,?)", (name, profile_id, created, revision, conversation, started_at, "starting", launch_auth, launch_subject))
        return {"session": name, "profile_id": profile_id, "state": "starting", "applied_revision": revision}
    except BaseException:
        payload.unlink(missing_ok=True)
        # A successful spawn whose registry write failed must not be claimed as
        # registered. Its tmux pane is preserved for the person to recover.
        raise


def session_status(name):
    if not isinstance(name, str) or not re.fullmatch(r"profile-[a-f0-9]{16}", name):
        raise profiles.Refused(tr("프로필 세션 이름이 올바르지 않습니다"))
    with profiles.database() as db:
        row = db.execute("SELECT * FROM sessions WHERE name=?", (name,)).fetchone()
        if row is None:
            raise profiles.Refused(tr("프로필 세션을 찾지 못했습니다"))
        saved = dict(row)
    state = "unknown"
    evidence = {"process_found": False, "root_matches": False, "event_verified": False, "event_after_launch": False}
    try:
        if _created(name) != saved["created"]:
            state = "mismatch"
        else:
            import agent_config_context
            import agent_config_restart
            ctx = agent_config_context.identify(name)
            evidence['process_found'] = True
            evidence['root_matches'] = ctx['root'] == str(profiles.root(saved['profile_id']))
            owner = agent_config_restart._owner(ctx)
            evidence['event_verified'] = owner.get('_session_start_verified') is True
            evidence['event_after_launch'] = owner.get('_session_start_at', 0) >= saved['started_at']
            if ctx["root"] != str(profiles.root(saved["profile_id"])) or (saved.get('process_identity') and ctx['identity'] != saved['process_identity']):
                state = "mismatch"
            elif owner.get("_session_start_verified") is True and owner.get("_session_start_at", 0) >= saved["started_at"]:
                if saved["conversation"] and (owner.get("session_id") != saved["conversation"] or owner.get("_session_start_source") != "resume"):
                    state = "mismatch"
                else:
                    state = "running"
                    with profiles.database() as db:
                        db.execute('UPDATE sessions SET process_identity=? WHERE name=? AND process_identity IS NULL', (ctx['identity'], name))
                    if saved["conversation"]:
                        from agent_profile_migration import mark_resumed
                        mark_resumed(saved["profile_id"], saved["conversation"])
    except (ValueError, OSError, fsguard.FsDenied):
        pass
    with profiles.database() as db:
        db.execute("UPDATE sessions SET state=? WHERE name=?", (state, name))
    return {"session": name, "profile_id": saved["profile_id"], "state": state,
            "applied_revision": saved["profile_revision"], "desired_revision": profiles.get(saved["profile_id"])["revision"],
            "conversation": saved["conversation"], "identity_state": "unverified", "evidence": evidence}


def reconcile_sessions(max_age=3600):
    """Check recently launched profile sessions from inside the server.

    SessionStart evidence lives in this process's memory, so a session launched
    from the owner's shell can only be verified here, not by that shell.
    """
    import sqlite3
    if not (vt_paths.state_dir() / "agent-profiles" / "registry.sqlite3").exists():
        return 0
    with profiles.database() as db:
        names = [r["name"] for r in db.execute("SELECT name FROM sessions WHERE state IN ('starting','unknown') AND started_at>?", (time.time() - max_age,))]
    for name in names:
        try:
            session_status(name)
        except (profiles.Refused, ValueError, OSError, sqlite3.Error):
            continue
    return len(names)


async def reconcile_loop():
    while True:
        await asyncio.sleep(RECONCILE_SECONDS)
        try:
            await asyncio.to_thread(reconcile_sessions)
        except Exception as exc:
            logger.warning("profile session reconcile failed: %s", type(exc).__name__)


def session_recorded(name):
    """The registry's last recorded state, without re-deriving it (that needs server memory)."""
    if not isinstance(name, str) or not re.fullmatch(r"profile-[a-f0-9]{16}", name):
        raise profiles.Refused(tr("프로필 세션 이름이 올바르지 않습니다"))
    with profiles.database() as db:
        row = db.execute("SELECT name,profile_id,profile_revision,conversation,started_at,state FROM sessions WHERE name=?", (name,)).fetchone()
    if row is None:
        raise profiles.Refused(tr("프로필 세션을 찾지 못했습니다"))
    return dict(row)


def note_verified_restart(ctx):
    """Called only after the existing safe-resume engine verifies its new PID."""
    with profiles.database() as db:
        row = db.execute('SELECT profile_id FROM sessions WHERE name=?', (ctx['session'],)).fetchone()
    if not row:
        return
    if str(profiles.root(row['profile_id'])) != ctx['root']:
        return
    profile = profiles.get(row['profile_id'])
    with profiles.database() as db:
        db.execute('UPDATE sessions SET process_identity=?,profile_revision=?,state=? WHERE name=?', (ctx['identity'], profile['revision'], 'running', ctx['session']))




def probe(profile_id, revision):
    """A minimal test-only native turn; retain a receipt, never model output."""
    profile = profiles.get(profile_id)
    profiles.require_revision(profile, revision)
    if profile['tool'] != 'claude':
        raise profiles.Refused(tr("자동 호출 검증은 Claude 프로필에서만 가능합니다"))
    code, version = run(profile_id, ['--version'], timeout=15)
    if code or not re.fullmatch(rb'\d+\.\d+\.\d+[^\r\n]*\(Claude Code\)\s*', version):
        raise profiles.Refused(tr("실제 네이티브 CLI 버전을 확인하지 못했습니다"))
    jid = profiles.job_begin(profile_id, 'native-probe')
    try:
        code, raw = run(profile_id, ['-p', 'Reply with exactly FSH_PROFILE_OK. Do not use tools.',
                                    '--tools', '', '--max-turns', '1', '--output-format', 'json', '--no-session-persistence'], timeout=90)
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        ok = code == 0 and data.get('is_error') is False and isinstance(data.get('result'), str) and 'FSH_PROFILE_OK' in data['result']
        from agent_profile_migration import _receipt
        # Bind evidence to the exact native auth/provider state. Later edits or
        # re-login require a fresh probe, rather than inheriting certification.
        fingerprint = authentication_fingerprint(profile_id)
        rid = _receipt(profile_id, '', 'native-probe', {'authenticated': ok, 'auth_fingerprint': fingerprint,
                                                       'native_version': version.decode().strip(), 'tools_disabled': True}, verified=ok)
        result = {'status': 'ok' if ok else 'failed', 'receipt': rid, 'native_inference_verified': ok}
        profiles.job_finish(jid, 'done' if ok else 'failed', result)
        return result
    except BaseException:
        profiles.job_finish(jid, 'failed', {'native_inference_verified': False})
        raise


def authentication_fingerprint(profile_id):
    from agent_profile_secret import read, FILENAME
    root = profiles.root(profile_id)
    from agent_profile_token import FILENAME as TOKEN_FILE, configured as token_configured
    if token_configured(profile_id):
        return profiles.digest(fsguard.read_profile_bytes(root, TOKEN_FILE))
    private = read(root)
    if private is not None:
        from usage.api_profiles import credential, declares_api_credentials
        binding = profiles.digest(fsguard.read_profile_bytes(root, FILENAME))
        if declares_api_credentials(private):
            return binding if credential(private) is not None else None
        from usage.profiles import _read_credentials
        auth = _read_credentials(profiles.get(profile_id))
        return profiles.digest(json.dumps([binding, auth['fingerprint']]).encode()) if auth else None
    from usage.profiles import _read_credentials
    auth = _read_credentials(profiles.get(profile_id))
    return auth['fingerprint'] if auth else None


def require_inactive(profile_id):
    """Authentication edits must not change a running CLI's helper mid-turn."""
    import hook_identity
    from agent_config_context import process_args
    root = profiles.root(profile_id)
    table = hook_identity._ps_table()
    if table is None:
        raise profiles.Refused(tr("프로필의 실행 상태를 확인하지 못했습니다"))
    unknown = False
    for pid, (_, _, _, comm) in table.items():
        if not (Path(comm).name.startswith('claude') or Path(comm).name == 'codex'):
            continue
        try:
            _, env = process_args(pid)
        except ProcessLookupError:
            continue
        except (OSError, ValueError):
            unknown = True
            continue
        value = env.get('CLAUDE_CONFIG_DIR') or env.get('CODEX_HOME')
        if value and Path(value).expanduser().resolve() == root:
            raise profiles.Refused(tr("이 프로필의 CLI를 정상 종료한 뒤 인증 설정을 변경하세요"))
    if unknown:
        raise profiles.Refused(tr("프로필의 실행 상태를 확인하지 못했습니다"))
