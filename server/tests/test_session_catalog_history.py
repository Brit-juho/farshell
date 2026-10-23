"""Archive loss/replay and physical identity regressions, using disposable state."""
import io
import json
import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def private_history(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(home / 'state'))
    monkeypatch.setenv('VT_BROWSE_ROOTS', str(home))
    from session_catalog import db
    db.snapshot()
    return home


def _source(home, provider='claude'):
    from session_history.archive import register_source
    from session_catalog.db import database
    root = home / ('.claude' if provider == 'claude' else '.codex')
    folder = root / ('projects/sample' if provider == 'claude' else 'sessions/2026/01')
    folder.mkdir(parents=True)
    registered = register_source(provider, str(root))
    with database() as db:
        source = dict(db.execute('SELECT * FROM sources WHERE id=?', (registered['id'],)).fetchone())
    return root, folder, source


def test_source_loss_keeps_raw_and_projection(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    raw = (json.dumps({'sessionId': 'parent-1', 'type': 'assistant', 'cwd': str(private_history),
                       'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hidden'},
                         {'type': 'text', 'text': '보존할 답변'}]}}, ensure_ascii=False) + '\n').encode()
    path = folder / 'parent-1.jsonl'
    path.write_bytes(raw)
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    before = query.detail(cid)
    assert [m['body'] for m in before['messages']] == ['보존할 답변']
    path.unlink()
    archive.scan_source(source)
    assert query.listing()['items'][0]['source_missing'] == 1
    assert query.detail(cid)['messages'] == before['messages']
    out = io.BytesIO()
    archive.restore_stream(query.manifest(before['manifests'][0]['id']), out)
    assert out.getvalue() == raw


def test_append_partial_utf8_and_replay(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    line = (json.dumps({'sessionId': 'a', 'type': 'user', 'message': {'role': 'user', 'content': '한글'}}, ensure_ascii=False)+'\n').encode()
    path = folder / 'a.jsonl'
    path.write_bytes(line[:-3])
    archive.scan_source(source)
    path.write_bytes(line)
    archive.scan_source(source)
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    assert len(query.detail(cid)['messages']) == 1
    assert query.detail(cid)['messages'][0]['body'] == '한글'


def test_rewrite_retains_old_raw(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    path = folder / 'a.jsonl'
    old = b'{"sessionId":"a","type":"user","message":{"role":"user","content":"first"}}\n'
    path.write_bytes(old)
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    first = query.detail(cid)['manifests'][0]['id']
    path.write_bytes(old.replace(b'first', b'other'))
    archive.scan_source(source)
    out = io.BytesIO()
    archive.restore_stream(query.manifest(first), out)
    assert out.getvalue() == old
    assert len(query.detail(cid)['manifests']) == 2


def test_tombstone_prevents_reingest(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    path = folder / 'a.jsonl'
    path.write_text('{"sessionId":"a"}\n')
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    query.mutate(cid, deleted=True)
    path.write_text('{"sessionId":"a"}\n{"type":"user","message":{"content":"new"}}\n')
    archive.scan_source(source)
    assert query.listing()['items'] == []


def test_symlink_source_and_custom_state_remain_private(private_history):
    import fsguard
    root, folder, source = _source(private_history)
    secret = private_history / 'outside.jsonl'
    secret.write_text('private')
    (folder / 'bad.jsonl').symlink_to(secret)
    from session_history.archive import source_files
    assert source_files(source)[1] == []
    from session_catalog.db import directory
    assert fsguard.is_managed_profile_path(directory() / 'catalog.sqlite3')


def test_runtime_generations_do_not_reuse_names(private_history):
    from session_catalog import inventory, db
    def observed(epoch, physical):
        return {'complete': True, 'epoch': epoch, 'sessions': [{'physical_key': physical, 'tmux_id': '$1',
                'name': 'same', 'origin': 'external', 'cwd': '/not-available', 'panes': []}]}
    inventory.reconcile(observed('epoch1', '$1:1'))
    first = db.snapshot()['sessions'][0]
    inventory.reconcile({'complete': False, 'sessions': []})
    assert db.snapshot()['sessions'][0]['execution_state'] == 'unknown'
    inventory.reconcile(observed('epoch2', '$1:1'))
    rows = db.snapshot()['sessions']
    assert len(rows) == 2
    assert next(r for r in rows if r['id'] == first['id'])['execution_state'] == 'stopped'
    assert len({r['id'] for r in rows}) == 2


def test_live_archive_and_stale_revision_rejected(private_history):
    from session_catalog import inventory, db
    inventory.reconcile({'complete': True, 'epoch': 'e', 'sessions': [{'physical_key': '$1:1', 'name': 'n',
                         'origin': 'external', 'cwd': '', 'panes': []}]})
    sid = db.snapshot()['sessions'][0]['id']
    with pytest.raises(db.Refused, match='runtime_must_stop'):
        db.edit_session(sid, 1, archived=True)
    db.edit_session(sid, 1, title='kept')
    with pytest.raises(db.Refused, match='revision_conflict'):
        db.edit_session(sid, 1, title='lost')


def test_future_schema_and_corruption_not_replaced(private_history):
    from session_catalog import db
    path = db.directory() / 'catalog.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=999')
    with pytest.raises(db.Refused, match='unsupported_store_version'):
        db.snapshot()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 999


def test_codex_native_child_and_reasoning_projection(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history, 'codex')
    records = [
        {'type': 'session_meta', 'payload': {'id': 'child', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}}}},
        {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': [{'text': 'never visible'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'result'}]}},
    ]
    (folder / 'child.jsonl').write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    archive.scan_source(source)
    record = query.listing()['items'][0]
    assert record['parent_native_id'] == 'parent'
    assert [m['body'] for m in query.detail(record['id'])['messages']] == ['result']


def test_compressed_codex_archive_is_searchable(private_history):
    import zstandard
    from session_history import archive, query
    root, folder, source = _source(private_history, 'codex')
    raw = (json.dumps({'type': 'session_meta', 'payload': {'id': 'compressed'}})+'\n'+
           json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                      'content': [{'type': 'output_text', 'text': 'compressed answer'}]}})+'\n').encode()
    (folder / 'compressed.jsonl.zst').write_bytes(zstandard.ZstdCompressor().compress(raw))
    assert archive.scan_source(source)['errors'] == 0
    records = query.listing(search='compressed answer')['items']
    assert len(records) == 1
    assert query.detail(records[0]['id'])['messages'][0]['body'] == 'compressed answer'


@pytest.mark.parametrize('message_count,padding',[(1,0),(2501,0),(7,900000)])
def test_encrypted_replica_survives_origin_loss_and_lost_ack(private_history, monkeypatch,message_count,padding):
    from session_history import archive, query, replication
    from session_catalog.db import database, encode, set_setting
    root, folder, source = _source(private_history)
    content = 'replicated text' + 'x'*padding
    line = json.dumps({'sessionId':'a','type':'user','message':{'role':'user','content':content}})+'\n'
    (folder / 'a.jsonl').write_text(line*message_count)
    archive.scan_source(source)
    sender = Path(os.environ['VT_STATE_DIR'])
    sender_identity = replication.public_identity()
    receiver = private_history / 'receiver'
    monkeypatch.setenv('VT_STATE_DIR', str(receiver))
    query.status()
    receiver_identity = replication.public_identity()
    with database(write=True) as db:
        db.execute('INSERT INTO replication_grants VALUES(?,?,?,?,?,?)', ('peer-a','receive',sender_identity['public_key'],1,1,0))
        set_setting(db,'replication-origin:peer-a',sender_identity['origin'])
    monkeypatch.setenv('VT_STATE_DIR', str(sender))
    with database(write=True) as db:
        db.execute('INSERT INTO replication_grants VALUES(?,?,?,?,?,?)', ('peer-b','send',receiver_identity['public_key'],1,1,0))
        set_setting(db,'replication-origin:peer-b',receiver_identity['origin'])
    lost = [False]
    def send(peer,payload):
        wire=replication.seal(payload,receiver_identity['public_key'])
        assert 'replicated text' not in encode(wire)
        monkeypatch.setenv('VT_STATE_DIR',str(receiver))
        try:
            result=replication.receive('peer-a',wire)
            if payload['kind']=='commit' and not lost[0]:
                lost[0]=True
                raise ConnectionError('lost ACK')
            return result
        finally:
            monkeypatch.setenv('VT_STATE_DIR',str(sender))
    monkeypatch.setattr(replication,'_send',send)
    with pytest.raises(ConnectionError):
        replication.push_once('peer-b')
    with database() as db:
        assert db.execute('SELECT COUNT(*) FROM replica_checkpoints').fetchone()[0]==0
    replication.push_once('peer-b')
    monkeypatch.setenv('VT_STATE_DIR',str(receiver))
    items=query.listing(search='replicated text')['items']
    assert len(items)==1 and items[0]['replica'] is True
    detail=query.replica_detail(sender_identity['origin'],items[0]['id'])
    assert detail['messages'][0]['body']==content
    with database() as db:
        assert db.execute("SELECT COUNT(*) FROM replicas WHERE kind='message'").fetchone()[0]==message_count
    out=io.BytesIO()
    archive.restore_stream(query.manifest(detail['manifests'][0]['id'],sender_identity['origin']),out)
    assert b'replicated text' in out.getvalue()
    if message_count==1 and padding==0:
        cid=items[0]['id']
        monkeypatch.setenv('VT_STATE_DIR',str(sender))
        query.mutate(cid,archived=True)
        assert replication.push_once('peer-b')
        monkeypatch.setenv('VT_STATE_DIR',str(receiver))
        assert query.listing(archived=True)['items'][0]['archived']
        monkeypatch.setenv('VT_STATE_DIR',str(sender))
        query.mutate(cid,deleted=True,delete_scope='replicas')
        while replication.push_once('peer-b'):
            pass
        monkeypatch.setenv('VT_STATE_DIR',str(receiver))
        assert query.listing(archived=True)['items']==[]
        with database() as db:
            assert db.execute("SELECT 1 FROM deletion_tombstones WHERE kind='replica'").fetchone()
    with database(write=True) as db:
        db.execute("UPDATE replication_grants SET enabled=0 WHERE peer='peer-a'")
    with pytest.raises(replication.Refused,match='replication_not_allowed'):
        replication.receive('peer-a',{})


def test_native_file_restore_never_overwrites(private_history):
    from session_history import archive,query,restore
    root,folder,source=_source(private_history)
    path=folder/'a.jsonl'
    raw=b'{"sessionId":"a"}\n'
    path.write_bytes(raw)
    archive.scan_source(source)
    mid=query.detail(query.listing()['items'][0]['id'])['manifests'][0]['id']
    plan=restore.plan(mid)
    with pytest.raises(Exception,match='restore_target_exists'):
        restore.apply(plan)
    path.unlink()
    result=restore.apply(plan)
    assert result['state']=='files_restored' and result['native_resume_verified'] is False
    assert path.read_bytes()==raw


def test_consistent_backup_has_no_credentials(private_history):
    import tarfile
    from session_history import archive,backup
    root,folder,source=_source(private_history)
    (folder/'a.jsonl').write_text('{"sessionId":"a"}\n')
    (root/'auth.json').write_text('must not enter backup')
    archive.scan_source(source)
    package=backup.export_package()
    with tarfile.open(package) as tar:
        names=tar.getnames()
        assert 'catalog.sqlite3' in names
        assert any(n.startswith('chunks/') for n in names)
        assert not any('auth' in n or 'replication.key' in n for n in names)


def test_deleted_provisional_identity_cannot_resurrect(private_history):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    path = folder/'a.jsonl'
    path.write_bytes(b'{"sessionId":"a"')
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    query.mutate(cid, deleted=True)
    path.write_bytes(b'{"sessionId":"a"}\n')
    archive.scan_source(source)
    assert query.listing()['items'] == []


def test_search_index_can_be_lost_and_rebuilt(private_history):
    from session_history import archive, query, search
    from session_catalog.db import directory
    root, folder, source = _source(private_history)
    (folder/'a.jsonl').write_text('{"sessionId":"a","type":"user","message":{"content":"한국어 검색 보존 코드 hello-world"}}\n')
    archive.scan_source(source)
    before = query.listing(search='검색 보존')['items']
    assert len(before) == 1
    search.refresh()
    assert query.listing(search='검색 보존')['items'][0]['id'] == before[0]['id']
    (directory()/'search.sqlite3').unlink()
    assert len(query.listing(search='hello-world')['items']) == 1
    search.refresh()
    assert len(query.listing(search='hello-world')['items']) == 1


def test_backup_restores_into_empty_state_with_capture_disabled(private_history, monkeypatch):
    from session_history import archive, backup, query
    root, folder, source = _source(private_history)
    raw = b'{"sessionId":"a","type":"user","message":{"content":"recovery proof"}}\n'
    (folder/'a.jsonl').write_bytes(raw)
    archive.scan_source(source)
    before = query.listing()['items'][0]
    package = backup.export_package()
    destination = private_history/'recovered'
    assert backup.restore_package(package, destination)['state'] == 'restored'
    monkeypatch.setenv('VT_STATE_DIR',str(destination))
    result = query.detail(before['id'])
    assert result['messages'][0]['body'] == 'recovery proof'
    out = io.BytesIO()
    archive.restore_stream(query.manifest(result['manifests'][0]['id']),out)
    assert out.getvalue() == raw
    assert query.status()['sources'][0]['enabled'] == 0
    with pytest.raises(Exception,match='restore_target_exists'):
        backup.restore_package(package,destination)


def test_http_archive_boundaries_and_actor_bound_plans(private_history,monkeypatch):
    import auth,time
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from routes import session_catalog as routes
    monkeypatch.setattr(auth,'VT_AUTH_SESSION_KEY','archive-test-signing-key')
    monkeypatch.setattr(auth,'VT_AUTH_PASSWORD_HASH','configured')
    monkeypatch.setattr(auth,'VT_AUTH_TOKEN','machine-only')
    root,folder,source=_source(private_history)
    app=FastAPI(); app.include_router(routes.router); app.include_router(routes.elevated_router)
    with TestClient(app) as client:
        assert client.get('/api/session-history').status_code==403
        assert client.get('/api/session-history',headers={'Authorization':'Bearer machine-only'}).status_code==403
        client.cookies.set('vt_session',auth.make_session())
        assert client.get('/api/session-history').status_code==200
        assert client.post('/api/session-history/preview/source',json={'provider':'claude','root':str(root)}).status_code==401
        client.cookies.set('vt_session',auth.make_session(elev_exp=int(time.time())+60))
        preview=client.post('/api/session-history/preview/source',json={'provider':'claude','root':str(root)})
        assert preview.status_code==200,preview.text
        token=preview.json()['token']
        assert client.post('/api/session-history/apply/source',json={'token':token}).status_code==200
        assert client.post('/api/session-history/apply/source',json={'token':token}).status_code==409
        assert client.post('/api/session-history/settings',json={'quota_bytes':True}).status_code==400
        assert client.post('/api/session-history/settings',json={'unknown':'field'}).status_code==400


def test_first_open_is_serialized_without_resetting_store(private_history):
    from concurrent.futures import ThreadPoolExecutor
    from session_catalog.db import database,directory,host_id
    # A separate empty state would normally be created concurrently by UI and worker.
    import shutil
    shutil.rmtree(directory())
    def open_once(_):
        with database(write=True) as db:
            return host_id(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        hosts=list(pool.map(open_once,range(12)))
    assert len(set(hosts))==1


def test_claude_companions_survive_source_loss(private_history):
    from session_history import archive,query
    from session_catalog.db import database
    root,folder,source=_source(private_history)
    (folder/'parent.jsonl').write_text('{"sessionId":"parent"}\n')
    child=folder/'parent/subagents'; child.mkdir(parents=True)
    (child/'agent-child.jsonl').write_text('{"sessionId":"parent","type":"assistant","message":{"content":"child answer"}}\n')
    (child/'agent-child.meta.json').write_text('{"agentType":"research"}')
    attachment=folder/'parent/tool-results'; attachment.mkdir()
    raw=b'preserved tool attachment'
    (attachment/'result.txt').write_bytes(raw)
    (root/'auth.json').write_text('must not collect')
    archive.scan_source(source)
    with database() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM source_files')]
        assert len(rows)==4
        metadata=next(r for r in rows if r['relative_path'].endswith('.meta.json'))
        assert db.execute('SELECT native_id,parent_native_id FROM conversations WHERE id=?',(metadata['conversation_id'],)).fetchone()[:]==('child','parent')
        mid=db.execute("SELECT id FROM manifests WHERE file_id=?",(next(r for r in rows if r['relative_path'].endswith('.txt'))['id'],)).fetchone()[0]
    import shutil
    shutil.rmtree(root)
    out=io.BytesIO(); archive.restore_stream(query.manifest(mid),out)
    assert out.getvalue()==raw


def test_create_records_intent_and_replay_never_spawns_twice(private_history,monkeypatch):
    from session_catalog import lifecycle,inventory
    from session_catalog.db import uid,database,Refused
    calls=[]
    def spawn(args,**kwargs):
        calls.append(args);return 0,b'',b''
    monkeypatch.setattr(lifecycle.tmux_runner,'run',spawn)
    monkeypatch.setattr(lifecycle.tmux_runner,'set_origin',lambda *a:True)
    item={'name':'sample','tmux_id':'$1','physical_key':'$1:100','cwd':str(private_history),'panes':[]}
    monkeypatch.setattr(inventory,'observe',lambda:{'complete':True,'epoch':'epoch-a','sessions':[item]})
    opid=uid()
    result=lifecycle.create(opid,'sample',str(private_history))
    assert result['runtime_id'] and result['session_id']
    assert lifecycle.create(opid,'sample',str(private_history))==result
    assert len(calls)==1
    with pytest.raises(Refused,match='idempotency_conflict'):
        lifecycle.create(opid,'different',str(private_history))
    with database() as db:
        assert db.execute('SELECT state FROM operations WHERE id=?',(opid,)).fetchone()[0]=='running'


def test_identity_v2_needs_review_and_never_survives_epoch_reuse(private_history,monkeypatch):
    from session_catalog import identity,inventory
    from session_catalog.db import database,host_id
    from authz import store
    a={'physical_key':'$1:10','name':'parent','tmux_id':'$1','cwd':str(private_history),'panes':[],'origin':'local'}
    b={**a,'physical_key':'$2:10','name':'child','tmux_id':'$2'}
    observed={'complete':True,'epoch':'epoch-1','sessions':[a,b]}
    monkeypatch.setattr(inventory,'observe',lambda:observed)
    inventory.reconcile(observed)
    with store.txn() as docs:
        docs['grants']['items'].append({'id':'test-old','schema':2,'subject':'agent:local/$1:10',
            'resource':'session:local/$2:10','scopes':['session.read'],'effect':'allow','state':'active'})
    identity.enable()
    assert store.snapshot().grants==[]
    preview=identity.preview()
    assert preview['records'][0]['id']=='test-old'
    identity.apply(preview,['test-old'])
    assert [g['id'] for g in store.snapshot().grants]==['test-old']
    observed['epoch']='epoch-2'
    assert store.snapshot().grants==[]
    from session_catalog.db import Refused
    with pytest.raises(Refused,match='identity_migration_changed'):
        identity.apply(preview,['test-old'])


def test_new_explicit_grant_is_bound_but_legacy_adoption_is_not(private_history,monkeypatch):
    from session_catalog import identity,inventory
    from authz import store
    item={'physical_key':'$1:10','name':'parent','tmux_id':'$1','cwd':str(private_history),'panes':[],'origin':'local'}
    observed={'complete':True,'epoch':'epoch-1','sessions':[item]}
    monkeypatch.setattr(inventory,'observe',lambda:observed)
    inventory.reconcile(observed); identity.enable()
    base={'schema':2,'subject':'agent:local/$1:10','resource':'fs:local/*','scopes':['fs.read'],'state':'active'}
    with store.txn() as docs:
        docs['grants']['items'] += [{**base,'id':'manual','approved_via':'manual'},
                                    {**base,'id':'adopted','approved_via':'migrated'}]
    assert [g['id'] for g in store.snapshot().grants]==['manual']
    with store.txn() as docs:
        assert next(g for g in docs['grants']['items'] if g['id']=='manual')['identity_version']==2
        assert 'identity_version' not in next(g for g in docs['grants']['items'] if g['id']=='adopted')


def test_corrupt_search_index_is_rebuilt_without_touching_archive(private_history):
    from session_history import archive,query,search
    from session_catalog.db import directory
    root,folder,source=_source(private_history)
    raw=b'{"sessionId":"a","type":"user","message":{"content":"index recovery proof"}}\n'
    (folder/'a.jsonl').write_bytes(raw);archive.scan_source(source)
    mid=query.detail(query.listing()['items'][0]['id'])['manifests'][0]['id']
    (directory()/'search.sqlite3').write_bytes(b'broken index')
    assert len(query.listing(search='recovery')['items'])==1
    search.refresh()
    assert len(query.listing(search='recovery')['items'])==1
    out=io.BytesIO();archive.restore_stream(query.manifest(mid),out)
    assert out.getvalue()==raw


def test_receiver_key_rotation_preserves_inflight_ciphertext(private_history):
    from session_history import replication
    import base64
    old=replication.public_identity()
    envelope=replication.seal({'version':1,'test':'inflight'},old['public_key'])
    rotated=replication.rotate_key()
    assert rotated['public_key']!=old['public_key'] and rotated['previous_keys_retained']
    assert json.loads(replication._decrypt(base64.b64decode(envelope['ciphertext'])))['test']=='inflight'
    current=replication.seal({'test':'new key'},rotated['public_key'])
    assert json.loads(replication._decrypt(base64.b64decode(current['ciphertext'])))['test']=='new key'


def test_receiver_enforces_approved_source_scope(private_history,monkeypatch):
    from session_history import archive,query,replication
    from session_catalog.db import database,Refused
    root,folder,source=_source(private_history)
    (folder/'a.jsonl').write_text('{"sessionId":"a"}\n');archive.scan_source(source)
    identity=replication.public_identity()
    with database() as db:
        conversation=dict(db.execute('SELECT * FROM conversations').fetchone())
        manifest=dict(db.execute('SELECT * FROM manifests').fetchone())
    grant={'origin':identity['origin'],'sources':['b'*32],'revision':1}
    monkeypatch.setattr(replication,'_grant',lambda *a:grant)
    payload={'version':1,'origin':identity['origin'],'kind':'offer','conversation':conversation,'manifest':manifest}
    with pytest.raises(Refused,match='replication_source_not_allowed'):
        replication.receive('peer',replication.seal(payload,identity['public_key']))
    grant['sources']=[source['scope']]
    assert replication.receive('peer',replication.seal(payload,identity['public_key']))['missing']==[]


def test_resume_source_requires_current_native_identity(private_history):
    from session_catalog.lifecycle import _exists
    root, folder, source = _source(private_history)
    path = folder / 'a.jsonl'
    relative = str(path.relative_to(root))
    path.write_text('{"sessionId":"original"}\n')
    assert _exists(str(root), relative, 'claude', 'original')
    path.write_text('{"sessionId":"replacement"}\n')
    assert not _exists(str(root), relative, 'claude', 'original')
    assert _exists(str(root), relative, 'claude', 'replacement')
    codex_root, codex_folder, _ = _source(private_history, 'codex')
    compressed = codex_folder / 'rollout-test.jsonl.zst'
    compressed.write_bytes(b'corrupt compressed transcript')
    assert not _exists(str(codex_root), str(compressed.relative_to(codex_root)), 'codex', 'original')


def test_concurrently_appended_source_is_partial_and_unverified(private_history, monkeypatch):
    from session_history import archive, query
    root, folder, source = _source(private_history)
    path = folder / 'a.jsonl'
    path.write_text('{"sessionId":"a","type":"user","message":{"content":"first"}}\n')
    project = archive._projection
    def append_during_projection(*args, **kwargs):
        result = project(*args, **kwargs)
        with path.open('a') as stream:
            stream.write('{"sessionId":"a"}\n')
        return result
    monkeypatch.setattr(archive, '_projection', append_during_projection)
    archive.scan_source(source)
    manifest = query.detail(query.listing()['items'][0]['id'])['manifests'][0]
    detail = json.loads(query.manifest(manifest['id'])['detail'])
    assert detail['state'] == 'partial'
    assert detail['native_identity_verified'] is False


def test_replica_chunks_do_not_consume_origin_quota(private_history):
    from session_history import archive
    from session_catalog.db import database, encode, set_setting, uid
    block = b'replica bytes'
    digest = archive.put_chunk(block)
    with database(write=True) as db:
        set_setting(db, 'quota_bytes', 1)
        set_setting(db, 'replica_quota_bytes', 1)
        db.execute('INSERT INTO replicas VALUES(?,?,?,?,?)', ('remote', uid(), 'manifest',
            encode({'detail': encode({'chunks': [{'digest': digest, 'size': len(block)}]})}), 1))
    archive._quota()
    from session_catalog.db import Refused
    with pytest.raises(Refused, match='archive_quota_reached'):
        archive._quota(replica=True)


def test_pair_and_lease_relationships_do_not_survive_epoch_reuse(private_history, monkeypatch):
    from session_catalog import identity, inventory
    from authz import pairs, leases
    item = {'physical_key': '$1:10', 'name': 'agent', 'tmux_id': '$1',
            'cwd': str(private_history), 'panes': [], 'origin': 'local'}
    observed = {'complete': True, 'epoch': 'first', 'sessions': [item]}
    monkeypatch.setattr(inventory, 'observe', lambda: observed)
    inventory.reconcile(observed)
    identity.enable()
    pair = {'members': [{'kind': 'agent', 'principal': 'agent:local/$1:10', 'ref': {'local': '$1:10'}}],
            'bound_sessions': ['$1:10']}
    lease = {'id': 'lease-test', 'holder_key': '$1:10', 'bound_sessions': ['$1:10']}
    assert not pairs._identity_live(pair)
    assert leases.active([lease], 1, {'$1:10'}, {}) == []
    identity.stamp_approved(pair)
    identity.stamp_approved(lease)
    assert pairs._identity_live(pair)
    assert len(leases.active([lease], 1, {'$1:10'}, {})) == 1
    observed['epoch'] = 'replacement'
    assert not pairs._identity_live(pair)
    assert leases.active([lease], 1, {'$1:10'}, {}) == []


def test_replica_child_navigation_is_origin_and_scope_bound(private_history):
    from session_history import query
    from session_catalog.db import database, encode, uid
    origin, other_origin, parent, child, unrelated = [uid() for _ in range(5)]
    scope = uid()
    base = {'host': origin, 'provider': 'claude', 'scope': scope, 'native_id': 'parent', 'deleted': None}
    with database(write=True) as db:
        for host, cid, record in (
            (origin, parent, base),
            (origin, child, {**base, 'native_id': 'child', 'parent_native_id': 'parent'}),
            (other_origin, unrelated, {**base, 'native_id': 'other-child', 'parent_native_id': 'parent'}),
            (origin, uid(), {**base, 'scope': uid(), 'native_id': 'wrong-scope', 'parent_native_id': 'parent'}),
        ):
            db.execute('INSERT INTO replicas VALUES(?,?,?,?,?)', (host, cid, 'conversation', encode({**record, 'id': cid}), 1))
    assert [row['id'] for row in query.replica_detail(origin, parent)['children']] == [child]


def test_durable_peer_attach_refuses_host_incarnation_before_pty(private_history, monkeypatch):
    from routes import peer
    from session_catalog.db import uid
    from types import SimpleNamespace
    class Socket:
        url = SimpleNamespace(path='/api/peer/ws-v2/test')
        headers = {}
        accepted = False
        closed = None
        async def accept(self): self.accepted = True
        async def close(self, **kwargs): self.closed = kwargs
    socket = Socket()
    monkeypatch.setattr(peer, '_guarded', lambda *args: {'id': 'peer-test', 'level': 'view'})
    import asyncio
    asyncio.run(peer.peer_ws(socket, 'same-name', uid(), uid(), uid()))
    assert socket.accepted and socket.closed['code'] == 4404


def test_remote_delegation_rejects_legacy_and_changed_sender_binding(private_history, monkeypatch):
    from session_catalog import identity
    from session_catalog.db import uid
    from authz import remote
    from authz.errors import ShareError
    import delegation
    identity.enable()
    key, origin = '$1:10', uid()
    grant = {'id': 'peer-test', 'obo': 'agent:peer-test/'+key}
    with pytest.raises(ShareError, match='실행 권한'):
        remote._deleg_binding(grant, {})
    binding = {'host':origin, 'epoch':'first', 'physical_key':key}
    body = {'identity_version':2, 'runtime_binding':binding, 'delegation':'test'}
    assert remote._deleg_binding(grant, body) == binding
    monkeypatch.setattr(delegation, 'list_all', lambda:[{'id':'test','parent':{'key':'peer-test/'+key},
        'identity_version':2,'runtime_bindings':{'peer-test/'+key:{**binding,'epoch':'previous'}}}])
    with pytest.raises(ShareError):
        remote._deleg_binding(grant, body, 'parent')


def test_small_daemon_capture_budget_preserves_complete_raw_and_projection(private_history):
    from session_history import archive, query, worker
    from session_catalog.db import database, Refused
    root, folder, source = _source(private_history)
    records = [{'sessionId':'budget-proof','type':'user','message':{'content':f'{i}:'+('한글'*300)}} for i in range(600)]
    raw = b''.join((json.dumps(row,ensure_ascii=False)+'\n').encode() for row in records)
    path = folder / 'budget-proof.jsonl';path.write_bytes(raw)
    offset = 0
    while offset < len(raw):
        assert archive.capture(source,str(path.relative_to(root)),max_bytes=worker.CAPTURE_BYTES)
        with database() as db:
            row = db.execute('SELECT offset FROM source_files WHERE source_id=?',(source['id'],)).fetchone()
        assert 0 < row['offset']-offset <= worker.CAPTURE_BYTES
        offset = row['offset']
    cid = query.listing()['items'][0]['id']
    with database() as db:
        messages = [row['body'] for row in db.execute('SELECT body FROM messages WHERE conversation_id=? ORDER BY position',(cid,))]
    assert messages == [row['message']['content'] for row in records]
    manifest = query.detail(cid)['manifests'][0]
    assert manifest['state'] == 'saved'
    output = io.BytesIO();archive.restore_stream(query.manifest(manifest['id']),output)
    assert output.getvalue() == raw
    for budget in (0,True,archive.PASS_BYTES+1):
        with pytest.raises(Refused,match='invalid_capture_budget'):
            archive.capture(source,str(path.relative_to(root)),max_bytes=budget)


def test_small_budget_compressed_projection_does_not_stall_on_large_valid_line(private_history):
    import zstandard
    from session_history import archive, query, worker
    root, folder, source = _source(private_history, 'codex')
    text = 'large valid record ' + 'x'*(worker.CAPTURE_BYTES+100)
    raw = (json.dumps({'type':'session_meta','payload':{'id':'large-compressed'}})+'\n'+
           json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant',
               'content':[{'type':'output_text','text':text}]}})+'\n').encode()
    path = folder/'large.jsonl.zst';path.write_bytes(zstandard.ZstdCompressor().compress(raw))
    archive.capture(source,str(path.relative_to(root)),max_bytes=worker.CAPTURE_BYTES)
    cid = query.listing()['items'][0]['id']
    detail = query.detail(cid)
    assert detail['messages'][0]['body'] == text
    from session_catalog.lifecycle import _exists
    assert _exists(str(root), str(path.relative_to(root)), 'codex', 'large-compressed')
    assert detail['manifests'][0]['state'] == 'saved'
    assert archive.capture(source,str(path.relative_to(root)),max_bytes=worker.CAPTURE_BYTES) is False
