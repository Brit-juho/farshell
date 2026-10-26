"""Explicit peer-to-peer encrypted replication; replicas never become local runtimes."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.exceptions import CryptoError

import host_store
import httpx
import peer_client
from session_catalog.db import (database, directory, private_file, uid, host_id, identifier, encode,
                                HASH, Refused, setting, set_setting, changed)
from . import archive

PATH = '/api/peer/session-history/receive'
MAX_WIRE = 8 * 1024 * 1024


def _key():
    path = directory() / 'replication.key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        with archive.fsguard.open_subagent_file(path) as stream:
            raw = stream.read(33)
        if len(raw) != 32:
            raise Refused('replication_key_unavailable', 503)
        return PrivateKey(raw)
    with os.fdopen(fd, 'wb') as stream:
        key = PrivateKey.generate()
        stream.write(bytes(key))
        stream.flush()
        os.fsync(stream.fileno())
    return key


def public_identity():
    public = base64.b64encode(bytes(_key().public_key)).decode()
    with database(write=True) as db:
        origin = host_id(db)
    return {'origin': origin, 'public_key': public,
            'fingerprint': hashlib.sha256(base64.b64decode(public)).hexdigest(), 'version': 1}


def _public(value):
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32:
            raise ValueError
        return PublicKey(raw)
    except (ValueError, TypeError):
        raise Refused('invalid_replication_key', 400) from None


def configure(body):
    if set(body) - {'peer', 'direction', 'origin', 'public_key', 'enabled', 'revision','sources'}:
        raise Refused('invalid_request', 400)
    peer, direction = body.get('peer'), body.get('direction')
    if not isinstance(peer, str) or host_store.normalize_id(peer) != peer or direction not in ('send', 'receive'):
        raise Refused('invalid_peer', 400)
    if type(body.get('enabled')) is not bool or type(body.get('revision')) is not int:
        raise Refused('invalid_revision', 400)
    origin = identifier(body.get('origin'))
    _public(body.get('public_key'))
    public = body['public_key']
    sources=body.get('sources',['*'])
    if (not isinstance(sources,list) or not 1<=len(sources)<=200
            or any(not isinstance(x,str) for x in sources) or len(set(sources))!=len(sources)
            or ('*' in sources and sources!=['*'])):
        raise Refused('invalid_source',400)
    if sources!=['*']:
        for source in sources:
            identifier(source)
    if not (host_store.find_peer(peer) if direction == 'send' else host_store.find_grant(peer)):
        raise Refused('peer_not_paired')
    with database(write=True) as db:
        if origin == host_id(db):
            raise Refused('replication_loop')
        prior = db.execute('SELECT * FROM replication_grants WHERE peer=?', (peer,)).fetchone()
        revision = prior['revision'] if prior else 0
        if revision != body['revision']:
            raise Refused('revision_conflict')
        # Changed keys require a fresh human decision and a full checkpoint reset.
        if prior and (prior['public_key'] != public or prior['direction'] != direction or setting(db,'replication-origin:'+peer) != origin or setting(db,'replication-sources:'+peer,['*']) != sources):
            db.execute('DELETE FROM replica_checkpoints WHERE peer=?', (peer,))
        db.execute('INSERT INTO replication_grants VALUES(?,?,?,?,?,?) ON CONFLICT(peer) DO UPDATE SET direction=excluded.direction,public_key=excluded.public_key,enabled=excluded.enabled,revision=excluded.revision',
                   (peer, direction, public, int(body['enabled']), revision+1, time.time()))
        set_setting(db, 'replication-origin:' + peer, origin)
        set_setting(db, 'replication-sources:' + peer, sources)
        if direction == 'send' and body['enabled']:
            set_setting(db, 'hub', peer)
        changed(db, 'replication', peer)
    return {'peer': peer, 'revision': revision+1, 'enabled': body['enabled']}


def _grant(peer, direction):
    with database() as db:
        row = db.execute('SELECT * FROM replication_grants WHERE peer=? AND direction=? AND enabled=1', (peer, direction)).fetchone()
        if not row:
            raise Refused('replication_not_allowed', 403)
        return {**dict(row), 'origin': setting(db, 'replication-origin:' + peer),'sources':setting(db,'replication-sources:'+peer,['*'])}


def seal(payload, public):
    raw = encode(payload).encode()
    if len(raw) > 6*1024*1024:
        raise Refused('replication_packet_too_large', 413)
    return {'version': 1, 'ciphertext': base64.b64encode(SealedBox(_public(public)).encrypt(raw)).decode()}


def receive(peer, envelope):
    grant = _grant(peer, 'receive')
    if not isinstance(envelope, dict) or set(envelope) != {'version', 'ciphertext'} or envelope['version'] != 1:
        raise Refused('invalid_replication_packet', 400)
    try:
        wire = envelope['ciphertext']
        if not isinstance(wire, str) or len(wire) > MAX_WIRE:
            raise ValueError
        encrypted=base64.b64decode(wire,validate=True)
        raw=_decrypt(encrypted)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('origin') != grant['origin']:
            raise ValueError
    except (ValueError, TypeError, CryptoError, RecursionError):
        raise Refused('invalid_replication_packet', 400) from None
    kind = data.get('kind')
    if kind in ('conversation','delete'):
        return _receive_metadata(peer,grant,data)
    if kind == 'chunk':
        digest = data.get('digest')
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise Refused('invalid_digest', 400)
        pending = data.get('manifest_id')
        identifier(pending)
        with database() as db:
            row = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='pending' AND id=?", (data['origin'], pending)).fetchone()
        if not row:
            raise Refused('manifest_required', 403)
        required = json.loads(row['payload'])['manifest']['detail']
        if digest not in {p['digest'] for p in json.loads(required)['chunks']}:
            raise Refused('chunk_not_in_manifest', 403)
        try:
            block = base64.b64decode(data.get('data', ''), validate=True)
        except (ValueError, TypeError):
            raise Refused('invalid_chunk', 400) from None
        if not archive.chunk_path(digest).exists():
            archive._quota(replica=True,additional=len(block))
        if hashlib.sha256(block).hexdigest() != digest or archive.put_chunk(block) != digest:
            raise Refused('chunk_corrupt', 400)
        return {'digest': digest, 'saved': True}
    if kind not in ('offer', 'projection', 'commit'):
        raise Refused('unsupported_replication_packet', 400)
    record = data.get('manifest')
    conversation = data.get('conversation')
    if not isinstance(record, dict) or not isinstance(conversation, dict):
        raise Refused('invalid_manifest', 400)
    identifier(record.get('id'))
    identifier(conversation.get('id'))
    identifier(conversation.get('scope'))
    if '*' not in grant['sources'] and conversation['scope'] not in grant['sources']:
        raise Refused('replication_source_not_allowed',403)
    if (conversation.get('provider') not in ('claude','codex')
            or not isinstance(conversation.get('title'),str) or len(conversation['title']) > 128
            or not isinstance(conversation.get('cwd'),str) or len(conversation['cwd']) > 16384
            or not isinstance(conversation.get('native_id'),str) or len(conversation['native_id']) > 160
            or not isinstance(record.get('digest'),str) or not HASH.fullmatch(record['digest'])):
        raise Refused('invalid_manifest',400)
    for value in (record.get('created'),conversation.get('created'),conversation.get('updated')):
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
            raise Refused('invalid_manifest',400)
    if record.get('conversation_id') != conversation['id'] or conversation.get('host') != data['origin']:
        raise Refused('origin_mismatch', 403)
    try:
        detail = json.loads(record['detail'])
        parts = detail['chunks']
        if not isinstance(parts, list) or len(parts) > 100000 or type(record['length']) is not int or record['length'] < 0:
            raise ValueError
        for part in parts:
            if not HASH.fullmatch(part['digest']) or type(part['size']) is not int or not 0 < part['size'] <= archive.CHUNK:
                raise ValueError
        if sum(p['size'] for p in parts) != record['length']:
            raise ValueError
    except (KeyError, ValueError, TypeError, RecursionError):
        raise Refused('invalid_manifest', 400) from None
    payload = {'manifest': record, 'conversation': conversation}
    if kind == 'offer':
        with database(write=True) as db:
            if db.execute("SELECT 1 FROM deletion_tombstones WHERE kind='replica' AND resource=?",(data['origin']+':'+conversation['id'],)).fetchone():
                return {'manifest_id':record['id'],'deleted':True}
            count = db.execute("SELECT COUNT(*) FROM replicas WHERE origin=? AND kind='pending'", (data['origin'],)).fetchone()[0]
            old = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='pending' AND id=?", (data['origin'], record['id'])).fetchone()
            if count >= 128 and not old:
                raise Refused('too_many_pending_manifests', 429)
            if old and json.loads(old['payload'])['manifest'] != record:
                raise Refused('manifest_conflict')
            committed=db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='manifest' AND id=?",(data['origin'],record['id'])).fetchone()
            if committed and committed['payload']!=encode(record):
                raise Refused('manifest_conflict')
            db.execute('INSERT OR IGNORE INTO replicas VALUES(?,?,?,?,?)', (data['origin'], record['id'], 'pending', encode(payload), time.time()))
        missing = []
        for part in parts:
            try:
                archive.read_chunk(part['digest'])
            except (OSError, Refused):
                missing.append(part['digest'])
        return {'missing': missing}
    with database() as db:
        pending = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='pending' AND id=?",
                             (data['origin'], record['id'])).fetchone()
        committed = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='manifest' AND id=?",
                               (data['origin'], record['id'])).fetchone()
    if pending and json.loads(pending['payload'])['manifest'] != record:
        raise Refused('manifest_conflict')
    if committed and committed['payload'] != encode(record):
        raise Refused('manifest_conflict')
    if not pending and not committed:
        raise Refused('manifest_required', 403)
    # Validate all referenced bytes before acknowledging a committed manifest.
    class Sink:
        def write(self, data):
            pass
    archive.restore_stream(record, Sink())
    messages = data.get('messages', [])
    if not isinstance(messages, list) or len(messages) > 2000:
        raise Refused('invalid_projection', 400)
    with database(write=True) as db:
        current = db.execute('SELECT revision,enabled FROM replication_grants WHERE peer=?', (peer,)).fetchone()
        if not current or not current['enabled'] or current['revision'] != grant['revision']:
            raise Refused('replication_revoked', 403)
        if db.execute("SELECT 1 FROM deletion_tombstones WHERE kind='replica' AND resource=?", (data['origin'] + ':' + conversation['id'],)).fetchone():
            raise Refused('replica_deleted', 410)
        values = [] if kind == 'projection' else [('manifest', record['id'], record), ('conversation', conversation['id'], conversation)]
        for kind_, id_, value in values:
            if kind_ == 'conversation':
                previous = db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='conversation' AND id=?",
                                      (data['origin'], id_)).fetchone()
                if previous and json.loads(previous['payload']).get('updated', 0) > value.get('updated', 0):
                    continue
            db.execute('INSERT INTO replicas VALUES(?,?,?,?,?) ON CONFLICT(origin,kind,id) DO UPDATE SET payload=excluded.payload,received=excluded.received',
                       (data['origin'], id_, kind_, encode(value), time.time()))
        for message in messages:
            if (not isinstance(message, dict) or message.get('conversation_id') != conversation['id']
                    or message.get('role') not in ('user', 'assistant', 'tool_call', 'tool_result')
                    or not isinstance(message.get('body'), str) or len(message['body'].encode()) > 2*1024*1024):
                raise Refused('invalid_projection', 400)
            identifier(message.get('id'))
            if (type(message.get('position')) is not int or message['position'] < 0
                    or type(message.get('created')) not in (int,float)
                    or not math.isfinite(message['created']) or message['created'] < 0
                    or not isinstance(message.get('generation'),str) or len(message['generation']) > 64):
                raise Refused('invalid_projection',400)
            prior=db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='message' AND id=?",(data['origin'],message['id'])).fetchone()
            if prior and prior['payload']!=encode(message):
                raise Refused('projection_conflict')
            db.execute('INSERT OR IGNORE INTO replicas VALUES(?,?,?,?,?)', (data['origin'], message['id'], 'message', encode(message), time.time()))
        if kind == 'commit':
            db.execute("DELETE FROM replicas WHERE origin=? AND kind='pending' AND id=?", (data['origin'], record['id']))
    return {'manifest_id': record['id'], 'digest': record['digest'], 'verified': True}


def _send(peer_id, payload):
    grant = _grant(peer_id, 'send')
    peer = host_store.find_peer(peer_id)
    if not peer:
        raise Refused('peer_not_paired')
    body = seal(payload, grant['public_key'])
    data = json.dumps(body, ensure_ascii=False).encode()
    _budget(peer_id,len(data))
    headers = peer_client._signed_headers(peer, 'POST', PATH, hashlib.sha256(data).hexdigest())
    headers['Content-Type'] = 'application/json'
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = client.post(peer['url'].rstrip('/') + PATH, content=data, headers=headers)
        if response.status_code != 200 or len(response.content) > 1024*1024:
            raise Refused('replication_failed', 503)
        return response.json()


def push_once(peer,*,max_chunks=None,should_stop=lambda:False):
    grant=_grant(peer,'send')
    if should_stop():
        return False
    with database() as db:
        checkpoint=db.execute('SELECT seq FROM replica_checkpoints WHERE peer=?',(peer,)).fetchone()
        seq=checkpoint['seq'] if checkpoint else 0
        event=db.execute("SELECT * FROM change_log WHERE kind IN ('manifest','conversation','history_delete') AND seq>? ORDER BY seq LIMIT 1",(seq,)).fetchone()
        if not event:
            return False
        manifest=None;messages=[]
        if event['kind']=='manifest':
            manifest=dict(db.execute('SELECT * FROM manifests WHERE id=?',(event['resource'],)).fetchone())
            row=db.execute('SELECT * FROM conversations WHERE id=?',(manifest['conversation_id'],)).fetchone()
        else:
            row=db.execute('SELECT * FROM conversations WHERE id=?',(event['resource'],)).fetchone()
        conv=dict(row) if row else None
        origin=host_id(db)
        if manifest and conv and conv['deleted'] is None:
            messages=[dict(r) for r in db.execute('SELECT * FROM messages WHERE conversation_id=? AND created=?',(conv['id'],manifest['created']))]
    if not conv or ('*' not in grant['sources'] and conv['scope'] not in grant['sources']):
        _checkpoint(peer,event['seq']);return True
    if event['kind']=='history_delete':
        ack=_send(peer,{'version':1,'origin':origin,'kind':'delete','id':conv['id'],'scope':conv['scope']})
        if ack.get('id')!=conv['id'] or not ack.get('deleted'):
            raise Refused('invalid_replication_ack',503)
        with database(write=True) as db:
            delivery=setting(db,'deletion_delivery:'+conv['id'],{})
            delivery[peer]={'state':'confirmed','updated':time.time()}
            set_setting(db,'deletion_delivery:'+conv['id'],delivery)
        _checkpoint(peer,event['seq']);return True
    if conv['deleted'] is not None:
        _checkpoint(peer,event['seq']);return True
    if event['kind']=='conversation':
        ack=_send(peer,{'version':1,'origin':origin,'kind':'conversation','conversation':conv})
        if ack.get('id')!=conv['id'] or not ack.get('saved'):
            raise Refused('invalid_replication_ack',503)
        _checkpoint(peer,event['seq']);return True
    common={'version':1,'origin':origin,'manifest':manifest,'conversation':conv}
    offer=_send(peer,{**common,'kind':'offer'})
    if offer.get('deleted') and offer.get('manifest_id')==manifest['id']:
        _checkpoint(peer,event['seq']);return True
    parts=json.loads(manifest['detail'])['chunks']
    allowed={part['digest'] for part in parts}
    missing=offer.get('missing')
    if not isinstance(missing,list) or any(not isinstance(x,str) for x in missing) or not set(missing)<=allowed:
        raise Refused('invalid_replication_ack',503)
    for index,digest in enumerate(missing):
        if should_stop() or (max_chunks is not None and index>=max_chunks):
            return True
        block=archive.read_chunk(digest)
        ack=_send(peer,{'version':1,'origin':origin,'kind':'chunk','digest':digest,'manifest_id':manifest['id'],
                        'data':base64.b64encode(block).decode()})
        if ack.get('digest')!=digest or not ack.get('saved'):
            raise Refused('invalid_replication_ack',503)
    batch=[];size=len(encode(common).encode())+128
    def project(batch):
        ack=_send(peer,{**common,'kind':'projection','messages':batch})
        if not ack.get('verified') or ack.get('manifest_id')!=manifest['id']:
            raise Refused('invalid_replication_ack',503)
    for message in messages:
        if should_stop():
            return True
        length=len(encode(message).encode())+1
        if batch and (size+length>5*1024*1024 or len(batch)>=1000):
            project(batch);batch=[];size=len(encode(common).encode())+128
        batch.append(message);size+=length
    if batch:
        project(batch)
    if should_stop():
        return True
    ack=_send(peer,{**common,'kind':'commit','messages':[]})
    if not ack.get('verified') or ack.get('digest')!=manifest['digest'] or ack.get('manifest_id')!=manifest['id']:
        raise Refused('invalid_replication_ack',503)
    _checkpoint(peer,event['seq'])
    return True


def _decrypt(encrypted):
    try:
        return SealedBox(_key()).decrypt(encrypted)
    except CryptoError:
        from session_catalog.db import private_dir
        folder=private_dir(directory()/'replication-keys')
        for path in sorted(folder.iterdir())[:16]:
            if path.is_symlink() or not path.is_file():
                continue
            with archive.fsguard.open_subagent_file(path) as stream:
                raw=stream.read(33)
            if len(raw)!=32:
                continue
            try:
                return SealedBox(PrivateKey(raw)).decrypt(encrypted)
            except CryptoError:
                continue
        raise


def rotate_key():
    import store_lock
    from session_catalog.db import private_dir
    with store_lock.exclusive(directory()/'replication-key.lock'):
        current=_key()
        folder=private_dir(directory()/'replication-keys')
        if len(list(folder.iterdir()))>=16:
            raise Refused('key_rotation_limit')
        name=hashlib.sha256(bytes(current.public_key)).hexdigest()
        backup=folder/name
        if not backup.exists():
            fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:
                out.write(bytes(current));out.flush();os.fsync(out.fileno())
        temp=directory()/('.key-'+uid())
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:
                out.write(bytes(PrivateKey.generate()));out.flush();os.fsync(out.fileno())
            os.replace(temp,directory()/'replication.key')
            fd=os.open(directory(),os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            temp.unlink(missing_ok=True)
    return {**public_identity(),'previous_keys_retained':True}


_BUDGET={}


def _budget(peer,size):
    import threading
    lock=getattr(_budget,'lock',None)
    if lock is None:
        lock=_budget.lock=threading.Lock()
    with lock:
        now=time.monotonic()
        for key,until in list(_BUDGET.items()):
            if until<now-60:
                _BUDGET.pop(key,None)
        previous=max(now,_BUDGET.get(peer,now))
        _BUDGET[peer]=previous+size/(5*1024*1024)
        delay=previous-now
    if delay>0:
        time.sleep(delay)


def _checkpoint(peer,seq):
    with database(write=True) as db:
        db.execute('INSERT INTO replica_checkpoints VALUES(?,?,?) ON CONFLICT(peer) DO UPDATE SET seq=MAX(seq,excluded.seq),updated=excluded.updated',(peer,seq,time.time()))


def _receive_metadata(peer,grant,data):
    origin=data['origin']
    if data['kind']=='delete':
        cid=identifier(data.get('id'));scope=identifier(data.get('scope'))
    else:
        conversation=data.get('conversation')
        if not isinstance(conversation,dict) or conversation.get('host')!=origin:
            raise Refused('invalid_projection',400)
        cid=identifier(conversation.get('id'));scope=identifier(conversation.get('scope'))
        if (conversation.get('provider') not in ('claude','codex') or conversation.get('deleted') is not None
                or not isinstance(conversation.get('title'),str) or len(conversation['title'])>128
                or not isinstance(conversation.get('cwd'),str) or len(conversation['cwd'])>16384
                or type(conversation.get('updated')) not in (int,float) or not math.isfinite(conversation['updated'])):
            raise Refused('invalid_projection',400)
    if '*' not in grant['sources'] and scope not in grant['sources']:
        raise Refused('replication_source_not_allowed',403)
    resource=origin+':'+cid
    with database(write=True) as db:
        active=db.execute('SELECT enabled,revision FROM replication_grants WHERE peer=?',(peer,)).fetchone()
        if not active or not active['enabled'] or active['revision']!=grant['revision']:
            raise Refused('replication_revoked',403)
        if data['kind']=='delete':
            db.execute("INSERT OR IGNORE INTO deletion_tombstones VALUES('replica',?,?)",(resource,time.time()))
            db.execute("DELETE FROM replicas WHERE origin=? AND ((kind='conversation' AND id=?) OR json_extract(payload,'$.conversation_id')=? OR json_extract(payload,'$.conversation.id')=?)",(origin,cid,cid,cid))
            return {'id':cid,'deleted':True}
        if db.execute("SELECT 1 FROM deletion_tombstones WHERE kind='replica' AND resource=?",(resource,)).fetchone():
            return {'id':cid,'saved':True,'deleted':True}
        prior=db.execute("SELECT payload FROM replicas WHERE origin=? AND kind='conversation' AND id=?",(origin,cid)).fetchone()
        if not prior or json.loads(prior['payload']).get('updated',0)<=conversation['updated']:
            db.execute("INSERT INTO replicas VALUES(?,?,?,?,?) ON CONFLICT(origin,kind,id) DO UPDATE SET payload=excluded.payload,received=excluded.received",(origin,cid,'conversation',encode(conversation),time.time()))
    return {'id':cid,'saved':True}
