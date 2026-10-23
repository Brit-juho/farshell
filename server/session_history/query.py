"""Human-only archive queries. No operation in this module starts an agent."""
from __future__ import annotations

import json
import sqlite3
import time
from session_catalog.db import database, identifier, page, changed, Refused, setting, set_setting


def listing(*, search='', provider='', limit=50, after=None, archived=False):
    page(limit)
    if after is not None:
        if not isinstance(after, str) or len(after) != 65 or after[32] != ':':
            raise Refused('invalid_cursor', 400)
        identifier(after[:32]); identifier(after[33:])
    if not isinstance(search, str) or len(search) > 256 or provider not in ('', 'claude', 'codex'):
        raise Refused('invalid_query', 400)
    pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    with database() as db:
        from .search import matches
        matched = matches(db, search) if search else None
        if matched is not None:
            db.execute('CREATE TEMP TABLE search_hits(origin TEXT,id TEXT,PRIMARY KEY(origin,id)) WITHOUT ROWID')
            db.executemany('INSERT OR IGNORE INTO search_hits VALUES(?,?)', matched)
        deadline = time.monotonic() + 1
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        local_sql = """SELECT c.*, (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id) AS message_count,
          (SELECT MAX(created) FROM manifests WHERE conversation_id=c.id) AS saved_at,
          EXISTS(SELECT 1 FROM source_files WHERE conversation_id=c.id AND state='source_missing') AS source_missing
          FROM conversations c WHERE c.deleted IS NULL AND (c.archived IS NOT NULL)=?
          AND (?='' OR c.provider=?) AND (? IS NULL OR c.host||':'||c.id>?) AND (?='' OR c.title LIKE ? ESCAPE '\\'
          OR c.native_id LIKE ? ESCAPE '\\' OR c.cwd LIKE ? ESCAPE '\\'
          OR EXISTS(SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.body LIKE ? ESCAPE '\\'))
          ORDER BY c.host,c.id LIMIT ?"""
        if matched is not None:
            start = local_sql.index('OR EXISTS(SELECT 1 FROM messages')
            end = local_sql.index('\n          ORDER BY',start)
            local_sql = local_sql[:start] + 'OR EXISTS(SELECT 1 FROM search_hits h WHERE h.origin=c.host AND h.id=c.id AND ? IS NOT NULL))' + local_sql[end:]
        rows = db.execute(local_sql, (int(archived), provider, provider, after, after, search, pattern, pattern, pattern, pattern, limit+1)).fetchall()
        local = [{**dict(r), 'detail': json.loads(r['detail']), 'key': r['host'] + ':' + r['id'], 'replica': False} for r in rows]
        replica_sql = """SELECT r.origin,r.id,r.payload FROM replicas r WHERE r.kind='conversation'
          AND json_extract(r.payload,'$.deleted') IS NULL
          AND (json_extract(r.payload,'$.archived') IS NOT NULL)=?
          AND (?='' OR json_extract(r.payload,'$.provider')=?) AND (? IS NULL OR r.origin||':'||r.id>?)
          AND (?='' OR json_extract(r.payload,'$.title') LIKE ? ESCAPE '\\'
          OR json_extract(r.payload,'$.native_id') LIKE ? ESCAPE '\\'
          OR json_extract(r.payload,'$.cwd') LIKE ? ESCAPE '\\'
          OR EXISTS(SELECT 1 FROM replicas m WHERE m.origin=r.origin AND m.kind='message'
          AND json_extract(m.payload,'$.conversation_id')=r.id AND json_extract(m.payload,'$.body') LIKE ? ESCAPE '\\'))
          ORDER BY r.origin,r.id LIMIT ?"""
        if matched is not None:
            start=replica_sql.index('OR EXISTS(SELECT 1 FROM replicas m')
            end=replica_sql.index('\n          ORDER BY',start)
            replica_sql=replica_sql[:start]+'OR EXISTS(SELECT 1 FROM search_hits h WHERE h.origin=r.origin AND h.id=r.id AND ? IS NOT NULL))'+replica_sql[end:]
        replicated=db.execute(replica_sql,
          (int(archived),provider,provider,after,after,search,pattern,pattern,pattern,pattern,limit+1)).fetchall()
        central = [{**json.loads(r['payload']), 'key': r['origin'] + ':' + r['id'], 'replica': True,
                    'origin': r['origin'], 'saved_at': None, 'source_missing': False} for r in replicated]
        merged = sorted(local + central, key=lambda r:r['key'])
        return {'items': merged[:limit], 'next': merged[limit-1]['key'] if len(merged)>limit else None}



def detail(cid, *, after=0, limit=100):
    identifier(cid)
    page(limit)
    if type(after) is not int or after < 0:
        raise Refused('invalid_cursor', 400)
    with database() as db:
        row = db.execute('SELECT * FROM conversations WHERE id=? AND deleted IS NULL', (cid,)).fetchone()
        if not row:
            raise Refused('conversation_not_found', 404)
        messages = db.execute('SELECT rowid AS cursor,* FROM messages WHERE conversation_id=? AND rowid>? ORDER BY rowid LIMIT ?', (cid, after, limit+1)).fetchall()
        # Bound the response by bytes as well as row count; never silently truncate a block.
        batch, size = [], 0
        for message in messages[:limit]:
            value = dict(message)
            weight = len(value['body'].encode())
            if batch and size + weight > 1024*1024:
                break
            size += weight
            batch.append(value)
        manifests = [dict(m) for m in db.execute('SELECT id,file_id,generation,length,digest,created,detail FROM manifests WHERE conversation_id=? ORDER BY created DESC LIMIT 200', (cid,))]
        for manifest in manifests:
            info = json.loads(manifest.pop('detail'))
            manifest.update({k: info.get(k) for k in ('state', 'compressed', 'native_identity_verified', 'dependency_complete', 'relative_path')})
        children = [dict(r) for r in db.execute('SELECT id,provider,native_id,title,state FROM conversations WHERE host=? AND provider=? AND scope=? AND parent_native_id=? AND deleted IS NULL LIMIT 200',
                                               (row['host'], row['provider'], row['scope'], row['native_id']))]
        native = [{**dict(r), 'detail':json.loads(r['detail'])} for r in db.execute('SELECT * FROM native_children WHERE conversation_id=? ORDER BY observed DESC LIMIT 200',(cid,))]
        return {'conversation': {**dict(row), 'detail': json.loads(row['detail'])}, 'messages': batch,
                'next': batch[-1]['cursor'] if len(messages)>len(batch) and batch else None,
                'manifests': manifests, 'children': children, 'native_children':native,
                'capabilities': {'read': True, 'restore': bool(manifests), 'resume': False,
                                 'resume_reason': 'native_resume_not_verified'}}


def manifest(mid, origin=None):
    identifier(mid)
    if origin is not None:
        identifier(origin)
    with database() as db:
        if origin:
            row = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='manifest' AND id=?", (origin, mid)).fetchone()
            if not row:
                raise Refused('manifest_not_found',404)
            value = json.loads(row[0])
            tombstone = db.execute("SELECT 1 FROM deletion_tombstones WHERE kind='replica' AND resource=?", (origin + ':' + value['conversation_id'],)).fetchone()
            if tombstone:
                raise Refused('manifest_not_found',404)
            return value
        row = db.execute('SELECT m.* FROM manifests m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND c.deleted IS NULL', (mid,)).fetchone()
        if not row:
            raise Refused('manifest_not_found', 404)
        return dict(row)


def mutate(cid, *, archived=None, deleted=False,delete_scope="local"):
    identifier(cid)
    if delete_scope not in ("local","replicas"):
        raise Refused("invalid_delete_scope",400)
    if archived is not None and not isinstance(archived, bool) or not isinstance(deleted, bool):
        raise Refused('invalid_archive', 400)
    with database(write=True) as db:
        row = db.execute('SELECT * FROM conversations WHERE id=? AND deleted IS NULL', (cid,)).fetchone()
        if not row:
            raise Refused('conversation_not_found', 404)
        if db.execute('SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id WHERE e.conversation_id=? AND r.ended IS NULL', (cid,)).fetchone():
            raise Refused('runtime_must_stop')
        if deleted:
            db.execute('UPDATE conversations SET deleted=?,updated=? WHERE id=?', (time.time(),time.time(), cid))
            db.execute('INSERT OR IGNORE INTO deletion_tombstones VALUES(?,?,?)', ('conversation', cid, time.time()))
            if delete_scope=='replicas':
                changed(db,'history_delete',cid)
                set_setting(db,'deletion_delivery:'+cid,{r['peer']:{'state':'pending'} for r in db.execute("SELECT peer FROM replication_grants WHERE direction='send' AND enabled=1")})
        elif archived is not None:
            db.execute('UPDATE conversations SET archived=?,updated=? WHERE id=?', (time.time() if archived else None,time.time(), cid))
        changed(db, 'conversation', cid)
        return {'id': cid, 'deleted': deleted, 'archived': archived,'delete_scope':delete_scope,'replica_delivery':setting(db,'deletion_delivery:'+cid,{})}


def status():
    with database(write=True) as db:
        sources = [dict(r) for r in db.execute('SELECT * FROM sources ORDER BY created')]
        return {'sources': sources, 'quota_bytes': setting(db, 'quota_bytes', 20*1024**3),
                'hub': setting(db, 'hub'), 'replica_quota_bytes':setting(db,'replica_quota_bytes',50*1024**3),
                'replication_worker':setting(db,'replication_worker',{'state':'not_started'}), 'worker': setting(db, 'worker', {'state': 'not_started'}),
                'conversations': db.execute('SELECT COUNT(*) FROM conversations WHERE deleted IS NULL').fetchone()[0],
                'messages': db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],
                'saved_at': db.execute('SELECT MAX(created) FROM manifests').fetchone()[0],
                'replication': [{**dict(r), 'origin':setting(db,'replication-origin:'+r['peer']),'sources':setting(db,'replication-sources:'+r['peer'],['*'])} for r in db.execute('SELECT peer,direction,enabled,revision,public_key FROM replication_grants')],
                'checkpoints': [dict(r) for r in db.execute('SELECT * FROM replica_checkpoints')]}


def configure(body):
    if set(body) - {'quota_bytes','replica_quota_bytes', 'source_id', 'enabled', 'revision'}:
        raise Refused('unknown_field', 400)
    with database(write=True) as db:
        for quota_key in ('quota_bytes','replica_quota_bytes'):
            if quota_key not in body:
                continue
            value = body[quota_key]
            if type(value) is not int or not 1024**3 <= value <= 1024**4:
                raise Refused('invalid_quota', 400)
            set_setting(db, quota_key, value)
        if 'source_id' in body:
            identifier(body['source_id'])
            if type(body.get('enabled')) is not bool or type(body.get('revision')) is not int:
                raise Refused('invalid_source', 400)
            result = db.execute('UPDATE sources SET enabled=?,revision=revision+1 WHERE id=? AND revision=?',
                                (body['enabled'], body['source_id'], body['revision']))
            if result.rowcount != 1:
                raise Refused('revision_conflict')
        changed(db, 'settings', 'history')
    return status()


def replica_detail(origin, cid, *, after=0, limit=100):
    identifier(origin); identifier(cid); page(limit)
    if type(after) is not int or after < 0:
        raise Refused('invalid_cursor',400)
    with database() as db:
        row=db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='conversation' AND id=?",(origin,cid)).fetchone()
        if not row or json.loads(row[0]).get('deleted'):
            raise Refused('conversation_not_found',404)
        messages=db.execute("SELECT rowid,payload FROM replicas WHERE origin=? AND kind='message' AND json_extract(payload,'$.conversation_id')=? AND rowid>? ORDER BY rowid LIMIT ?",(origin,cid,after,limit+1)).fetchall()
        manifests=[json.loads(r[0]) for r in db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='manifest' AND json_extract(payload,'$.conversation_id')=? ORDER BY received DESC LIMIT 200",(origin,cid))]
        batch, size = [], 0
        for record in messages[:limit]:
            value = {**json.loads(record['payload']),'cursor':record['rowid']}
            weight = len(value['body'].encode())
            if batch and size + weight > 1024*1024:
                break
            size += weight
            batch.append(value)
        conversation = json.loads(row[0])
        children = [json.loads(child[0]) for child in db.execute(
            """SELECT payload FROM replicas WHERE origin=? AND kind='conversation'
            AND json_extract(payload,'$.provider')=? AND json_extract(payload,'$.scope')=?
            AND json_extract(payload,'$.parent_native_id')=? AND json_extract(payload,'$.deleted') IS NULL
            ORDER BY id LIMIT 200""",
            (origin, conversation['provider'], conversation['scope'], conversation['native_id']))]
        return {'conversation':conversation, 'messages':batch,
                'next':batch[-1]['cursor'] if len(messages)>len(batch) and batch else None,'manifests':manifests,'children':children,
                'capabilities':{'read':True,'restore':False,'resume':False,'resume_reason':'replica_read_only'}}
