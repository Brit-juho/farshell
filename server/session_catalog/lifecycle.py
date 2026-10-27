"""Recorded intent before tmux effects; a retry never blindly spawns twice."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import time

import zstandard

import fsguard
import store_lock
import tmux_runner
from .db import database, directory, identifier, uid, encode, changed, host_id, resume_target, Refused
from . import permission_modes
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


def resume_argv(provider, binary, root, native, mode=None):
    """The tmux command for a resume. `mode` is replayed only from the fixed allow-list, never as given."""
    env_key = 'CLAUDE_CONFIG_DIR' if provider == 'claude' else 'CODEX_HOME'
    argv = ['env', '-u', 'ANTHROPIC_API_KEY', '-u', 'OPENAI_API_KEY', '-u', 'CLAUDE_CODE_OAUTH_TOKEN',
            f'{env_key}={root}', binary]
    argv += ['--resume', native] if provider == 'claude' else ['resume', native]
    if provider == 'claude' and mode in permission_modes.REPLAYED:
        argv += ['--permission-mode', mode]
    return argv


def _launch_resume(name, cwd, argv):
    """Keep the interactive shell alive after the CLI exits; never retry input."""
    command = shlex.join(argv)
    # PTY control bytes are interpreted before shell quoting (e.g. Ctrl-C).
    if any(ord(char) < 32 or ord(char) == 127 for char in command):
        return False
    rc, output, _ = tmux_runner.run(
        ['new-session', '-d', '-P', '-F', '#{pane_id}', '-s', name, '-c', str(cwd)], timeout=5)
    if rc:
        return False
    pane = output.decode('utf-8', errors='replace').strip()
    if not re.fullmatch(r'%[0-9]+', pane):
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
        if not any(_exists(root, r['relative_path'], source['provider'],conversation['native_id']) for r in files):
            raise Refused('native_source_missing')
        binary = shutil.which(conversation['provider'])
        if not binary:
            raise Refused('cli_unavailable')
        native = conversation['native_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', native):
            raise Refused('native_identity_unverified')
        # Same permission mode as the previous run (Claude only); a missing record or `default` adds nothing.
        mode = None
        if source['provider'] == 'claude':
            with database() as db:
                mode = permission_modes.lookup(db, native)
        argv = resume_argv(source['provider'], binary, root, native, mode)
        placement = inventory.inspect_workspace(str(cwd))
        now, name = time.time(), 'fsh-' + opid
        with database(write=True) as db:
            if sid:
                row = db.execute('SELECT * FROM sessions WHERE id=? AND deleted IS NULL AND archived IS NULL', (sid,)).fetchone()
                if not row or db.execute('SELECT 1 FROM runtimes WHERE session_id=? AND ended IS NULL', (sid,)).fetchone():
                    raise Refused('runtime_must_stop')
            else:
                sid = uid()
                wid = inventory.workspace(db, placement)
                db.execute('INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated) VALUES(?,?,?,?,?,?,?)',
                           (sid, host_id(db), wid, conversation['title'] or native, 'local', now, now))
            result = {'session_id': sid, 'conversation_id': cid, 'name': name, 'native_id': native}
            if mode:
                result['permission_mode'] = mode
            prior = _begin(db, opid, 'resume', sid, {'conversation_id': cid, 'session_id': sid}, result)
            if prior:
                return {'operation_id': opid, 'state': prior['state'], **json.loads(prior['result'])}
        if not _launch_resume(name, cwd, argv):
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
            db.execute('INSERT INTO runtimes VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (rid, sid, snapshot['epoch'], item['physical_key'], name, 'running', now, None, time.time(), encode(item)))
            for pane in item['panes']:
                db.execute('INSERT INTO conversation_executions VALUES(?,?,?,0)', (cid, rid, pane['pane_id']))
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


def create(opid,name,cwd,cols=80,rows=24,*,request_spec=None):
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
            db.execute('INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated) VALUES(?,?,?,?,?,?,?)',
                       (sid,host_id(db),wid,name,'local',now,now))
            result={'session_id':sid,'name':name,'host':host_id(db)}
            _begin(db,opid,'create',sid,payload,result)
        rc,_,_=tmux_runner.run(['new-session','-d','-s',name,'-x',str(cols),'-y',str(rows),'-c',cwd],timeout=5)
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
            db.execute('INSERT INTO runtimes VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (rid,sid,observed['epoch'],item['physical_key'],name,'running',now,None,time.time(),encode(item)))
            for pane in item['panes']:
                db.execute('INSERT INTO pane_executions VALUES(?,?,?,?)',
                           (uid(),rid,pane['pane_id']+':'+str(pane['pid']),encode(pane)))
            changed(db,'session',sid)
        result.update(runtime_id=rid,tmux_id=item['tmux_id'])
        _finish(opid,'running',result)
        return result
