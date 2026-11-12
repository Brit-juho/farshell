"""Recorded intent before tmux effects; a retry never blindly spawns twice."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from pathlib import Path

import zstandard

import fsguard
import store_lock
import tmux_runner
from .db import touch_activity, resume_ends_session, database, directory, identifier, uid, encode, changed, host_id, resume_target, Refused
from . import permission_modes, execution_policy
from . import inventory


def _begin(db, opid, kind, sid, payload, result):
    identifier(opid)
    fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
    row = db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone()
    if row:
        if (row['kind'], row['resource'], row['fingerprint']) != (kind, sid, fingerprint):
            raise Refused('idempotency_conflict')
        return dict(row)
    now = time.time()
    db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)', (opid, kind, sid, fingerprint, 'starting', encode(result), now, now))
    return None


def _finish(opid, state, result):
    with database(write=True) as db:
        db.execute('UPDATE operations SET state=?,result=?,updated=? WHERE id=?', (state, encode(result), time.time(), opid))
        changed(db, 'operation', opid)


def resume_argv(provider, binary, root, native, mode=None, *, policy=None, account_injected=False):
    """The tmux command for a resume. `mode` is replayed only from the fixed allow-list, never as given.

    `account_injected`: 그 대화가 기록된 계정을 세션 환경으로 이미 넣었다(session_profile — 장기 토큰·API 주소 계정의
    인증 변수 포함). 그때는 인증 변수를 지우지 않는다 — 예전에는 언제나 CLAUDE_CODE_OAUTH_TOKEN 등을 지워서 그런 계정의
    대화는 이어서 시작하면 인증에 실패했다."""
    env_key = 'CLAUDE_CONFIG_DIR' if provider == 'claude' else 'CODEX_HOME'
    unset = [] if account_injected else ['-u', 'ANTHROPIC_API_KEY', '-u', 'OPENAI_API_KEY', '-u', 'CLAUDE_CODE_OAUTH_TOKEN']
    argv = ['env', *unset, f'{env_key}={root}', binary]
    argv += ['--resume', native] if provider == 'claude' else ['resume', native]
    if provider == 'claude':
        if not isinstance(mode, str) or mode not in permission_modes.REPLAYED:
            raise Refused('execution_policy_unavailable')
        argv += ['--permission-mode', mode]
    elif provider == 'codex':
        if not isinstance(policy, dict):
            raise Refused('execution_policy_unavailable')
        argv += execution_policy.codex_args(policy)
    else:
        raise Refused('unsupported_provider')
    return argv


def _profile_for_root(root, provider):
    """대화를 수집한 원본 폴더가 FarShell에 등록된 계정 폴더(`<state>/agent-profiles/<id>/<tool>`)면 그 계정 id."""
    try:
        import agent_profiles
        base = agent_profiles.directory().resolve()
        path = Path(root).resolve()
    except (OSError, ValueError, RuntimeError, Exception):  # noqa: BLE001 — 레지스트리를 못 읽으면 계정 주입 없음
        return None
    if path.name != provider or path.parent.parent != base:
        return None
    return path.parent.name


def _spawn_shell_session(name, cwd, selection, path_env):
    """셸 하나로 세션을 만들고 그 pane id를 돌려준다. 계정을 고르면 새 세션을 계정으로 만들 때와 같은 주입을 쓴다."""
    if selection is not None:
        import session_profile
        rc, _err, info = session_profile.spawn(name, 200, 50, str(cwd), selection)
        if rc:
            return None
        if 'cleared_env' in (info or {}):
            # 어느 계정으로 만들었는지 레지스트리에도 남긴다(새 세션 만들기와 같다) — 없으면 이 세션에서 위임한
            # 자식이 `parent_profile_unverified`로 거절된다. 기록 실패가 실행을 막지는 않는다.
            try:
                session_profile.record(name, selection)
            except Exception:  # noqa: BLE001
                pass
    else:
        rc, _, _ = tmux_runner.run(['new-session', '-d', '-s', name, '-c', str(cwd)], timeout=5)
        if rc:
            return None
    pane = tmux_runner.run_text(['display-message', '-p', '-t', tmux_runner.pane_target(name), '#{pane_id}'], timeout=2)
    pane = (pane or '').strip()
    return pane if re.fullmatch(r'%[0-9]+', pane) else None


def _launch_exec(name, cwd, argv, path_env, selection):
    """CLI가 끝나면 세션도 끝나게 띄운다. 셸 세션을 만든 뒤 그 pane에 「실패하면 화면을 남김」을 걸고 CLI로 교체한다 —
    CLI가 곧바로 실패해도 오류 화면이 남아 사람이 읽을 수 있고, 정상 종료하면 pane과 세션이 닫힌다(자동 보관 대상)."""
    pane = _spawn_shell_session(name, cwd, selection, path_env)
    if not pane:
        return False
    rc, _, _ = tmux_runner.run(['set-option', '-p', '-t', pane, 'remain-on-exit', 'failed'], timeout=3)
    if rc:
        return False
    rc, _, _ = tmux_runner.run(['respawn-pane', '-k', '-t', pane, '-c', str(cwd), '--', *_with_path(argv, path_env)], timeout=5)
    return rc == 0


def _with_path(argv, path_env):
    """CLI에 줄 PATH를 명령 자체에 싣는다(`env PATH=… <CLI>`). tmux 3.7c는 new-session·respawn-pane의 `-e PATH=…`를
    무시하고 서버 전체 환경의 PATH를 쓴다(실측) — 그래서 환경 옵션으로는 넘길 수 없다."""
    if not path_env:
        return list(argv)
    if argv and argv[0] == 'env':
        # `env`의 `-u 이름` 옵션은 첫 `이름=값`보다 앞에 와야 한다 — 그 뒤에 오면 명령 이름으로 읽힌다.
        i = 1
        while i + 1 < len(argv) and argv[i] == '-u':
            i += 2
        return [*argv[:i], f'PATH={path_env}', *argv[i:]]
    return ['env', f'PATH={path_env}', *argv]


def _launch_in_shell(name, cwd, argv, path_env, selection):
    """설정 「이어서 시작한 세션은 CLI가 끝나면 함께 종료」를 끈 경우 — 셸에 명령을 쳐서 CLI가 끝나도 셸이 남는다."""
    command = shlex.join(_with_path(argv, path_env))
    if any(ord(char) < 32 or ord(char) == 127 for char in command):
        return False
    pane = _spawn_shell_session(name, cwd, selection, path_env)
    if not pane:
        return False
    rc, _, _ = tmux_runner.run(['send-keys', '-t', pane, '-l', '--', command], timeout=5)
    if rc:
        return False
    rc, _, _ = tmux_runner.run(['send-keys', '-t', pane, 'Enter'], timeout=5)
    return rc == 0


def resume(cid, opid, *, sid=None):
    identifier(cid)
    identifier(opid)
    if sid is not None:
        identifier(sid)
    with store_lock.exclusive(directory() / 'lifecycle.lock'):
        # Durable retries return their recorded result, including needs_review.
        with database() as db:
            retry = db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone()
            if retry:
                if retry['kind'] != 'resume' or json.loads(retry['result']).get('conversation_id') != cid:
                    raise Refused('idempotency_conflict')
                return {'operation_id': opid, 'state': retry['state'], **json.loads(retry['result'])}
            conversation = db.execute('SELECT * FROM conversations WHERE id=? AND deleted IS NULL', (cid,)).fetchone()
            if not conversation:
                raise Refused('conversation_not_found', 404)
            conversation = dict(conversation)
            source = db.execute('SELECT * FROM sources WHERE scope=? AND provider=? AND enabled=1', (conversation['scope'], conversation['provider'])).fetchone()
            if not source:
                raise Refused('source_unavailable')
            source = dict(source)
            if not json.loads(conversation['detail']).get('native_identity_verified'):
                raise Refused('native_identity_unverified')
            if conversation['parent_native_id']:
                raise Refused('native_child_resume_unsupported')
            if db.execute('SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id WHERE e.conversation_id=? AND r.ended IS NULL', (cid,)).fetchone():
                raise Refused('conversation_already_running')
        root = fsguard.history_source_root(source['root'])
        cwd = fsguard.resolve_under_roots(conversation['cwd'])
        if not cwd.is_dir():
            raise Refused('workspace_missing')
        # A stored reference alone does not prove that a native resume source exists.
        with database() as db:
            files = db.execute('SELECT relative_path FROM source_files WHERE conversation_id=?', (cid,)).fetchall()
        native_files = [r['relative_path'] for r in files
                        if _exists(root, r['relative_path'], source['provider'], conversation['native_id'])]
        if not native_files:
            raise Refused('native_source_missing')
        # CLI 실행 파일: 그 대화를 돌렸던 CLI에서 기록한 경로 → 그 PATH → 서버 PATH → 흔한 위치(cli_resolve).
        import cli_resolve
        with database() as db:
            seen = db.execute('SELECT r.cli_path,r.cli_env_path FROM runtimes r JOIN conversation_executions e ON e.runtime_id=r.id'
                              ' WHERE e.conversation_id=? AND (r.cli_path IS NOT NULL OR r.cli_env_path IS NOT NULL)'
                              ' ORDER BY r.started DESC LIMIT 1', (cid,)).fetchone()
            ends_with_cli = resume_ends_session(db)
        recorded_path, recorded_env = (seen['cli_path'], seen['cli_env_path']) if seen else (None, None)
        binary = cli_resolve.resolve(conversation['provider'], recorded_path, recorded_env)
        if not binary:
            raise Refused('cli_unavailable')
        path_env = cli_resolve.launch_path(binary, recorded_env)
        # 계정: 대화가 등록된 계정 폴더에서 기록됐으면 그 계정으로 띄운다(새 세션을 계정으로 만들 때와 같은 주입).
        selection = None
        profile_id = _profile_for_root(root, conversation['provider'])
        if profile_id:
            import session_profile
            try:
                selection = session_profile.select(profile_id)
            except session_profile.SlotRefused as error:
                raise Refused(error.code) from None
        native = conversation['native_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', native):
            raise Refused('native_identity_unverified')
        # Restore the recorded policy explicitly, including OFF; current config is not evidence.
        mode, policy, witnesses = None, None, []
        if source['provider'] == 'claude':
            with database() as db:
                mode = permission_modes.lookup(db, native, root=str(root))
        else:
            policy, witnesses = execution_policy.read_codex(root, native_files, native)
            execution_policy.require_cli(binary)
        argv = resume_argv(source['provider'], binary, root, native, mode, policy=policy, account_injected=selection is not None)
        placement = inventory.inspect_workspace(str(cwd))
        # 새 tmux 이름은 다른 생성 경로와 같은 규칙(대화를 시작한 폴더의 저장소 이름, 겹치면 -2·-3)이다.
        # 예전 `fsh-<32자리>`는 사람이 읽을 수 없었다. 이름은 열쇠가 아니다 — 이 실행은 런타임 신원으로 묶인다.
        import session_names
        now, name = time.time(), session_names.allocate(session_names.base_for(str(cwd)))
        with database(write=True) as db:
            current_source = db.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
            if (not current_source or not current_source['enabled']
                    or current_source['revision'] != source['revision'] or current_source['root'] != source['root']):
                raise Refused('source_unavailable')
            if sid:
                row = db.execute('SELECT * FROM sessions WHERE id=? AND deleted IS NULL AND archived IS NULL', (sid,)).fetchone()
                if not row or db.execute('SELECT 1 FROM runtimes WHERE session_id=? AND ended IS NULL', (sid,)).fetchone():
                    raise Refused('runtime_must_stop')
            else:
                sid = uid()
                wid = inventory.workspace(db, placement)
                db.execute('INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated,last_activity) VALUES(?,?,?,?,?,?,?,?)',
                           # 보이는 이름은 자동 이름 규칙(새 tmux 이름 = 저장소 이름)이다. 대화 제목·id를 세션 이름으로 쓰지 않는다.
                           (sid, host_id(db), wid, name, 'local', now, now, now))
            result = {'session_id': sid, 'conversation_id': cid, 'name': name, 'native_id': native}
            if mode:
                result['permission_mode'] = mode
            if policy:
                result['execution_policy'] = execution_policy.summary(policy)
            prior = _begin(db, opid, 'resume', sid, {'conversation_id': cid, 'session_id': sid}, result)
            if prior:
                return {'operation_id': opid, 'state': prior['state'], **json.loads(prior['result'])}
        try:
            execution_policy.recheck(witnesses)
            with database() as db:
                current_source = db.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
                if (not current_source or not current_source['enabled']
                        or current_source['revision'] != source['revision'] or current_source['root'] != source['root']):
                    raise Refused('source_unavailable')
                if source['provider'] == 'claude':
                    if permission_modes.lookup(db, native, root=str(root)) != mode:
                        raise Refused('execution_policy_changed')
        except Refused:
            _finish(opid, 'needs_review', result)
            raise
        launched = (_launch_exec if ends_with_cli else _launch_in_shell)(name, cwd, argv, path_env, selection)
        if launched:
            tmux_runner.set_origin(name, 'local')   # 예전에는 빠져서 이어서 시작한 세션이 「직접 만듦」으로 보였다
        if not launched:
            # A timeout can leave a created process: never retry this operation automatically.
            _finish(opid, 'needs_review', result)
            raise Refused('launch_needs_review', 503)
        snapshot = inventory.observe()
        item = next((r for r in snapshot['sessions'] if r['name'] == name), None)
        if not snapshot['complete'] or not item:
            _finish(opid, 'needs_review', result)
            raise Refused('launch_needs_review', 503)
        with database(write=True) as db:
            rid = uid()
            db.execute('INSERT INTO runtimes(id,session_id,epoch,physical_key,name,state,started,ended,observed,detail) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (rid, sid, snapshot['epoch'], item['physical_key'], name, 'running', now, None, time.time(), encode(item)))
            for pane in item['panes']:
                db.execute('INSERT INTO conversation_executions VALUES(?,?,?,0)', (cid, rid, pane['pane_id']))
            touch_activity(db, sid, now)
            changed(db, 'session', sid)
        result.update(runtime_id=rid, native_verified=False)
        _finish(opid, 'started_unverified', result)
        return {'operation_id': opid, 'state': 'started_unverified', **result}


def resume_session(sid, opid):
    """Restart a stopped session by resuming its latest verified conversation under the same session id."""
    identifier(sid)
    identifier(opid)
    with database() as db:
        retry = db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone()
        if retry:
            if retry['kind'] != 'resume' or retry['resource'] != sid:
                raise Refused('idempotency_conflict')
            cid = json.loads(retry['result']).get('conversation_id')
        else:
            row = db.execute('SELECT archived FROM sessions WHERE id=? AND deleted IS NULL', (sid,)).fetchone()
            if not row:
                raise Refused('session_not_found', 404)
            if row['archived'] is not None:
                raise Refused('session_archived')
            cid = resume_target(db, sid)
            if not cid:
                raise Refused('no_resumable_conversation')
    return resume(cid, opid, sid=sid)


def _exists(root, relative, provider,native_id=None):
    try:
        if fsguard.history_capture_kind(relative,provider)!='transcript':
            return False
        with fsguard.open_subagent_file(fsguard.history_source_path(root, relative, provider)) as stream:
            if native_id is None:
                return True
            if relative.endswith('.zst'):
                import zstandard
                with zstandard.ZstdDecompressor(max_window_size=64*1024**2).stream_reader(stream) as decoded:
                    probe=decoded.read(1024*1024)
            else:
                probe=stream.read(1024*1024)
            from session_history.adapters import identity
            found=identity(probe,provider,relative)
            return bool(found and found['native_id']==native_id)
    except (OSError, ValueError, zstandard.ZstdError, fsguard.FsDenied):
        return False


def stop(sid, opid, *, force=False):
    identifier(sid)
    identifier(opid)
    if force is not True:
        raise Refused('force_confirmation_required', 428)
    with store_lock.exclusive(directory() / 'lifecycle.lock'):
        with database() as db:
            old = db.execute('SELECT * FROM operations WHERE id=?', (opid,)).fetchone()
            if old:
                if old['kind'] != 'stop' or old['resource'] != sid:
                    raise Refused('idempotency_conflict')
                return {'operation_id': opid, 'state': old['state'], **json.loads(old['result'])}
        runtime, item = inventory.resolve_runtime(sid)
        result = {'session_id': sid, 'runtime_id': runtime['id']}
        with database(write=True) as db:
            _begin(db, opid, 'stop', sid, {'runtime_id': runtime['id']}, result)
        # Exact physical ID, never tmux's name-prefix fallback.
        rc, _, _ = tmux_runner.run(['kill-session', '-t', item['tmux_id']], timeout=5)
        if rc:
            _finish(opid, 'needs_review', result)
            raise Refused('stop_unconfirmed', 503)
        with database(write=True) as db:
            db.execute("UPDATE runtimes SET state='stopped',ended=?,observed=? WHERE id=?", (time.time(), time.time(), runtime['id']))
            changed(db, 'runtime', runtime['id'])
        _finish(opid, 'done', result)
        return {'operation_id': opid, 'state': 'done', **result}


def create(opid,name,cwd,cols=80,rows=24,*,request_spec=None,launcher=None):
    """Create a logical task before spawning; operation replay never spawns again."""
    import os,re
    identifier(opid)
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',name):
        raise Refused('invalid_title',400)
    if not isinstance(cwd,str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        raise Refused('workspace_missing',400)
    for dimension in (cols,rows):
        if type(dimension) is not int or not 1<=dimension<=1000:
            raise Refused('invalid_dimensions',400)
    payload={'request':request_spec if request_spec is not None else {'name':name},'cwd':cwd,'cols':cols,'rows':rows}
    fingerprint=hashlib.sha256(encode(payload).encode()).hexdigest()
    placement=inventory.inspect_workspace(cwd)
    with store_lock.exclusive(directory()/'lifecycle.lock'):
        with database(write=True) as db:
            prior=db.execute('SELECT * FROM operations WHERE id=?',(opid,)).fetchone()
            if prior:
                if prior['kind']!='create' or prior['fingerprint']!=fingerprint:
                    raise Refused('idempotency_conflict')
                if prior['state']!='running':
                    raise Refused('launch_needs_review',503)
                return json.loads(prior['result'])
            sid=uid();now=time.time();wid=inventory.workspace(db,placement)
            db.execute('INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated,last_activity) VALUES(?,?,?,?,?,?,?,?)',
                       (sid,host_id(db),wid,name,'local',now,now,now))
            result={'session_id':sid,'name':name,'host':host_id(db)}
            _begin(db,opid,'create',sid,payload,result)
        # `launcher`: a caller that must create the session itself (account injection). It returns (rc, error, info).
        rc=launcher(name,cols,rows,cwd)[0] if launcher else tmux_runner.run(['new-session','-d','-s',name,'-x',str(cols),'-y',str(rows),'-c',cwd],timeout=5)[0]
        if rc:
            _finish(opid,'needs_review',result)
            raise Refused('launch_needs_review',503)
        tmux_runner.set_origin(name,'local')
        observed=inventory.observe()
        item=next((r for r in observed['sessions'] if r['name']==name),None)
        if not observed['complete'] or not item:
            _finish(opid,'needs_review',result)
            raise Refused('launch_needs_review',503)
        with database(write=True) as db:
            rid=uid()
            db.execute('INSERT INTO runtimes(id,session_id,epoch,physical_key,name,state,started,ended,observed,detail) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (rid,sid,observed['epoch'],item['physical_key'],name,'running',now,None,time.time(),encode(item)))
            for pane in item['panes']:
                db.execute('INSERT INTO pane_executions VALUES(?,?,?,?)',
                           (uid(),rid,pane['pane_id']+':'+(str(pane['pid']) if pane['pid'] is not None else 'dead'),encode(pane)))
            changed(db,'session',sid)
        result.update(runtime_id=rid,tmux_id=item['tmux_id'])
        _finish(opid,'running',result)
        return result
