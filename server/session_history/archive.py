"""Immutable raw chunks. Source disappearance never deletes a committed capture."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import threading
import time
from pathlib import Path

import fsguard
from session_catalog.db import (database, directory, private_dir, uid, encode, host_id, changed,
                                identifier, HASH, Refused, setting)
from . import adapters

CHUNK = 4 * 1024 * 1024
PASS_BYTES = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_FILES = 20000


def chunk_path(digest):
    if not isinstance(digest, str) or not HASH.fullmatch(digest):
        raise Refused('invalid_digest', 400)
    folder = private_dir(directory() / 'chunks')
    return folder / digest


def put_chunk(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= CHUNK:
        raise Refused('invalid_chunk', 400)
    digest = hashlib.sha256(data).hexdigest()
    dest = chunk_path(digest)
    if dest.exists() or dest.is_symlink():
        if read_chunk(digest) != data:
            raise Refused('chunk_corrupt', 503)
        return digest
    temp = dest.with_name('.' + uid())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, dest)
        _note_written(len(data))
        folder = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if temp.exists():
            temp.unlink()
    return digest


def read_chunk(digest):
    with fsguard.open_subagent_file(chunk_path(digest)) as stream:
        data = stream.read(CHUNK + 1)
    if len(data) > CHUNK or hashlib.sha256(data).hexdigest() != digest:
        raise Refused('chunk_corrupt', 503)
    return data


def source_files(source):
    root = fsguard.history_source_root(source['root'])
    prefixes = ['projects'] if source['provider'] == 'claude' else ['sessions', 'archived_sessions']
    found, truncated = [], False
    for prefix in prefixes:
        base = root / prefix
        if not base.exists():
            continue
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
            if len(Path(current).relative_to(root).parts) > 12:
                dirs[:] = []
                truncated = True
                continue
            for name in sorted(files):
                relative = str((Path(current) / name).relative_to(root))
                # Validate and descriptor-open later, not through os.walk's path alone.
                try:
                    fsguard.history_source_path(root, relative, source['provider'])
                except (OSError, fsguard.FsDenied):
                    continue
                found.append(relative)
                if len(found) >= MAX_FILES:
                    return root, found, True
    return root, found, truncated


def preview_source(provider, raw_root):
    if provider not in ('claude', 'codex'):
        raise Refused('unsupported_provider', 400)
    root, files, truncated = source_files({'root': raw_root, 'provider': provider})
    total = sum((root / name).stat().st_size for name in files)
    info = root.stat()
    return {'provider': provider, 'root': str(root), 'root_identity': [info.st_dev, info.st_ino], 'files': len(files), 'bytes': total, 'truncated': truncated}


def register_source(provider, root):
    preview = preview_source(provider, root)
    with database(write=True) as db:
        old = db.execute('SELECT * FROM sources WHERE provider=? AND root=?', (provider, preview['root'])).fetchone()
        sid = old['id'] if old else uid()
        if old:
            db.execute('UPDATE sources SET enabled=1,revision=revision+1 WHERE id=?', (sid,))
        else:
            db.execute('INSERT INTO sources(id,provider,root,scope,enabled,created) VALUES(?,?,?,?,1,?)',
                       (sid, provider, preview['root'], sid, time.time()))
        changed(db, 'source', sid)
    return {'id': sid, **preview}


def _count_usage():
    with database() as db:
        # A shared physical chunk counts against each role that references it.
        # Pending receiver manifests count too, so interrupted transfers stay bounded.
        local, remote = set(), set()
        for row in db.execute('SELECT detail FROM manifests'):
            local.update(part['digest'] for part in json.loads(row['detail'])['chunks'])
        for row in db.execute("SELECT kind,payload FROM replicas WHERE kind IN ('manifest','pending')"):
            payload = json.loads(row['payload'])
            manifest = payload['manifest'] if row['kind'] == 'pending' else payload
            remote.update(part['digest'] for part in json.loads(manifest['detail'])['chunks'])
    known = local | remote
    totals = {'local': 0, 'remote': 0}
    # Uncommitted/orphan bytes also consume quota; failed captures cannot evade it.
    for path in (directory() / 'chunks').glob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if path.name in local or path.name not in known:
            totals['local'] += size
        if path.name in remote or path.name not in known:
            totals['remote'] += size
    return totals


# The full count parses every manifest and stats every chunk (over a second on a large archive) and
# used to run for every changed file on every 2s collector tick, holding the GIL the terminal needs.
# It is now redone at most every QUOTA_RECOUNT seconds; bytes of chunks created in between are added
# to both roles, so the cached check can only overestimate usage, never let a write past the quota.
QUOTA_RECOUNT = 300
_usage = {'root': None, 'at': 0.0, 'local': 0, 'remote': 0, 'written': 0}
_usage_lock = threading.Lock()


def _note_written(size):
    with _usage_lock:
        _usage['written'] += size


def _quota(*,replica=False,additional=0):
    with database() as db:
        quota = setting(db, 'replica_quota_bytes' if replica else 'quota_bytes', (50 if replica else 20) * 1024**3)
    root = str(directory())
    with _usage_lock:
        if _usage['root'] != root or time.monotonic() - _usage['at'] >= QUOTA_RECOUNT:
            _usage.update(_count_usage(), root=root, at=time.monotonic(), written=0)
        total = _usage['remote' if replica else 'local'] + _usage['written']
    if total + additional >= quota or shutil.disk_usage(directory()).free - additional < 2 * 1024**3:
        raise Refused('archive_quota_reached', 507)


def _prefix(stream, count):
    digest, remaining = hashlib.sha256(), count
    stream.seek(0)
    while remaining:
        data = stream.read(min(CHUNK, remaining))
        if not data:
            break
        digest.update(data)
        remaining -= len(data)
    return digest, remaining


def _projection(stream, provider, begin, end, *, seek=True, checkpoint=None):
    if seek:
        stream.seek(begin)
    checkpoint = checkpoint if checkpoint is not None else {}
    reasons = set(checkpoint.get('omitted_reasons', []))
    skipping = checkpoint.get('skipping_oversized', False)
    out, offset, incomplete = [], begin, False
    while offset < end:
        line = stream.readline(min(MAX_LINE + 1, end - offset))
        if not line:
            break
        if skipping:
            offset += len(line)
            skipping = not line.endswith(b'\n')
            continue
        if len(line) > MAX_LINE:
            # Commit a bounded skip, including its continuation state. Never parse
            # an oversized record's tail as an independent JSON record after restart.
            reasons.add('oversized_record')
            offset += len(line)
            skipping = not line.endswith(b'\n')
            continue
        if not line.endswith(b'\n'):
            incomplete = True
            break
        position = offset
        offset += len(line)
        try:
            row = json.loads(line)
            projected = adapters.records(row, provider)
        except (ValueError, UnicodeError, RecursionError, TypeError):
            reasons.add('invalid_record')
            continue
        # Claude가 나중에 붙이는 제목 줄(summary/custom-title/ai-title)도 본다 — 앞 256줄만 보던 identity는 대화가
        # 길어진 뒤 생긴 제목을 놓쳤다. 저장하지 않는 임시 키(`_title`)로 넘기고 capture가 꺼낸다.
        if provider == 'claude' and isinstance(row, dict) and row.get('type') in ('summary', 'custom-title', 'ai-title'):
            found = row.get('customTitle', row.get('title', row.get('summary', '')))
            if isinstance(found, str) and found.strip():
                checkpoint['_title'] = found.strip()[:128]
        for index, (role, body) in enumerate(projected):
            if body:
                out.append((position * 1000000 + index, role, body))
    checkpoint.update(version=1, skipping_oversized=skipping, omitted_reasons=sorted(reasons),
                      incomplete_record=incomplete)
    return out, offset, bool(reasons or skipping or incomplete)


# Every capture of a growing transcript adds a manifest listing all of that file's chunks, so an active
# file gathered hundreds of near-identical restore points and the catalog grew quadratically. Per file
# we keep the newest KEEP_RECENT_MANIFESTS, the newest of each generation (the last state before the file
# was rewritten) and the newest of each local day, forever. Within a generation the chunk lists are
# prefixes of the newest one, so no chunk loses its last reference.
KEEP_RECENT_MANIFESTS = 20
PRUNE_BATCH = 500


def _retained(rows):
    """`rows` are (id, generation, created) of one file, newest first."""
    keep, generations, days = set(), set(), set()
    for index, (mid, generation, created) in enumerate(rows):
        day = time.strftime('%Y-%m-%d', time.localtime(created))
        if index < KEEP_RECENT_MANIFESTS or generation not in generations or day not in days:
            keep.add(mid)
        generations.add(generation)
        days.add(day)
    return keep


def _prunable(rows, key):
    """Group rows (sorted by key, newest first within a key) and return what retention drops."""
    drop, group, current = [], [], object()
    for row in rows + [None]:
        if row is None or key(row) != current:
            if group:
                keep = _retained([(r['id'], r['generation'], r['created']) for r in group])
                drop += [r for r in group if r['id'] not in keep]
            if row is None:
                break
            group, current = [], key(row)
        group.append(row)
    return drop


def prune_manifests():
    """Apply manifest retention to local captures and to manifests received from peers.

    A local manifest is dropped only after every send peer's checkpoint has passed it: messages
    replicate together with their manifest, so dropping an unsent one would lose them on the peer.
    Received manifests are only stored here; their origin keeps its own copies.
    """
    with database() as db:
        local = [dict(r) for r in db.execute('SELECT id,file_id,generation,created FROM manifests ORDER BY file_id,created DESC,id DESC')]
        senders = [r['peer'] for r in db.execute("SELECT peer FROM replication_grants WHERE direction='send'")]
        sent = {r['peer']: r['seq'] for r in db.execute('SELECT peer,seq FROM replica_checkpoints')}
        sent_upto = min((sent.get(peer, 0) for peer in senders), default=None)
        seqs = {}
        if sent_upto is not None:
            seqs = {r['resource']: r['seq'] for r in db.execute("SELECT resource,MAX(seq) AS seq FROM change_log WHERE kind='manifest' GROUP BY resource")}
        received = [dict(r) for r in db.execute(
            "SELECT origin,id,json_extract(payload,'$.file_id') AS file_id,json_extract(payload,'$.generation') AS generation,"
            "json_extract(payload,'$.created') AS created FROM replicas WHERE kind='manifest' ORDER BY origin,file_id,created DESC,id DESC")]
    drop_local = [r['id'] for r in _prunable(local, lambda r: r['file_id'])
                  if sent_upto is None or (r['id'] in seqs and seqs[r['id']] <= sent_upto)]
    drop_received = [(r['origin'], r['id']) for r in _prunable(
        [r for r in received if isinstance(r['created'], (int, float)) and isinstance(r['generation'], str)],
        lambda r: (r['origin'], r['file_id']))]
    # Small batches keep each write transaction short next to live capture and terminal traffic.
    # The newest manifest of a file is always retained, and a capture landing meanwhile only adds a newer
    # one, so nothing dropped here can be the manifest the next capture continues from.
    for start in range(0, len(drop_local), PRUNE_BATCH):
        with database(write=True) as db:
            db.executemany('DELETE FROM manifests WHERE id=?', [(mid,) for mid in drop_local[start:start + PRUNE_BATCH]])
    for start in range(0, len(drop_received), PRUNE_BATCH):
        with database(write=True) as db:
            db.executemany("DELETE FROM replicas WHERE origin=? AND kind='manifest' AND id=?", drop_received[start:start + PRUNE_BATCH])
    return {'local': len(drop_local), 'received': len(drop_received)}


def capture(source, relative, *, max_bytes=PASS_BYTES):
    """One bounded append pass; all raw writes precede database references.

    The interactive daemon uses a smaller budget than explicit/offline capture.
    JSON projection and commits must yield often enough to leave terminal I/O responsive.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= PASS_BYTES:
        raise Refused('invalid_capture_budget', 400)
    root = fsguard.history_source_root(source['root'])
    path = fsguard.history_source_path(root, relative, source['provider'])
    with database() as db:
        previous = db.execute('SELECT * FROM source_files WHERE source_id=? AND relative_path=?', (source['id'], relative)).fetchone()
        previous = dict(previous) if previous else None
        prior_manifest = db.execute('SELECT * FROM manifests WHERE file_id=? ORDER BY created DESC,id DESC LIMIT 1', (previous['id'],)).fetchone() if previous else None
        prior = json.loads(prior_manifest['detail']) if prior_manifest else None
    with fsguard.open_subagent_file(path) as file:
        st = os.fstat(file.fileno())
        fingerprint = encode([st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns])
        legacy_incomplete = (previous and prior and previous['state'] == 'partial'
                             and prior.get('projection', {}).get('version') != 1)
        if (previous and previous['fingerprint'] == fingerprint and previous['offset'] == st.st_size
                and previous['state'] != 'indexing' and not legacy_incomplete):
            # No write here: `seen` is only read by scan_source, which stamps it itself before calling capture.
            # A commit (fsync) per unchanged file every collector tick kept fseventsd/Spotlight saturated.
            return False
        _quota()
        begin = 0
        digest, chunks = hashlib.sha256(), []
        generation, parsed = uid(), 0
        projection = {}
        if previous and prior and previous['offset'] <= st.st_size:
            check, remaining = _prefix(file, previous['offset'])
            if not remaining and check.hexdigest() == prior_manifest['digest']:
                begin, parsed = previous['offset'], previous['parsed_offset']
                generation, digest, chunks = previous['generation'], check, list(prior['chunks'])
                if prior.get('projection', {}).get('version') == 1:
                    projection = dict(prior['projection'])
                elif previous['state'] in ('partial', 'indexing'):
                    # Re-read legacy incomplete projections once. Stable message
                    # positions/IDs make this safe even after earlier messages exist.
                    parsed = 0
        file.seek(0)
        probe = file.read(min(1024 * 1024, st.st_size))
        compressed = relative.endswith('.zst')
        if compressed:
            try:
                import zstandard
                with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(io.BytesIO(probe)) as z:
                    probe = z.read(1024 * 1024)
            except Exception:
                # Raw capture still proceeds. Native identity remains unverified.
                probe = b''
        capture_kind = fsguard.history_capture_kind(relative, source['provider'])
        identity = adapters.identity(probe, source['provider'], relative) if capture_kind == 'transcript' else None
        if capture_kind in ('child_metadata','tool_attachment'):
            parts=Path(relative).parts
            native=Path(relative).name.removeprefix('agent-').removesuffix('.meta.json') if capture_kind=='child_metadata' else parts[2]
            import re
            if re.fullmatch(r'[A-Za-z0-9_-]{1,160}',native):
                identity={'native_id':native,'cwd':'','title':'','parent_native_id':parts[2] if capture_kind=='child_metadata' else None}
        file.seek(begin)
        end = min(st.st_size, begin + max_bytes)
        while file.tell() < end:
            data = file.read(min(CHUNK, end - file.tell()))
            if not data:
                raise Refused('source_changed')
            if not chunk_path(hashlib.sha256(data).hexdigest()).exists():
                _quota(additional=len(data) + max(0, file.tell() - begin - len(data)))
            chunks.append({'digest': put_chunk(data), 'size': len(data)})
            digest.update(data)
        indexing = False
        projection_failed = False
        if capture_kind != 'transcript':
            messages, next_parsed, partial = [], end, False
        elif compressed:
            messages, next_parsed, partial = [], parsed, False
            if end == st.st_size:
                try:
                    import zstandard
                    file.seek(0)
                    with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(file, closefd=False) as decoded:
                        reader = io.BufferedReader(decoded)
                        remaining = parsed
                        while remaining:
                            data = reader.read(min(CHUNK, remaining))
                            if not data:
                                break
                            remaining -= len(data)
                        if remaining:
                            raise Refused('compressed_source_changed')
                        if parsed >= 1024**3:
                            partial = True
                            projection.setdefault('omitted_reasons', []).append('projection_limit')
                        else:
                            messages, next_parsed, partial = _projection(reader, source['provider'], parsed, parsed + max(max_bytes, MAX_LINE + 1), seek=False, checkpoint=projection)
                            indexing = bool(reader.peek(1))
                except Exception:
                    partial = True
                    projection_failed = True
                    projection.setdefault('omitted_reasons', []).append('decode_error')
        else:
            projection_end = min(end, parsed + max(max_bytes, MAX_LINE + 1))
            messages, next_parsed, partial = _projection(file, source['provider'], parsed, projection_end, checkpoint=projection)
            indexing = next_parsed < end and (projection_end < end or not projection.get('incomplete_record'))
        latest_title = projection.pop('_title', None)
        projection.update(version=1, omitted_reasons=sorted(set(projection.get('omitted_reasons', []))))
        projection['state'] = 'indexing' if indexing else ('partial' if partial else 'complete')
        after = os.fstat(file.fileno())
        if (st.st_dev, st.st_ino) != (after.st_dev, after.st_ino) or after.st_size < end:
            raise Refused('source_changed')
        changing=(st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns)
        if changing:
            partial=True;indexing=False
    now = time.time()
    manifest = {'version': 1, 'provider': source['provider'], 'scope': source['scope'], 'relative_path': relative,
                'chunks': chunks, 'compressed': compressed, 'length': end,
                'state': 'indexing' if indexing else ('partial' if end < st.st_size or changing or projection_failed or projection.get('incomplete_record') else 'saved'),
                'raw_complete': end == st.st_size and not changing, 'projection': projection,
                'native_identity_verified': identity is not None and capture_kind == 'transcript' and not changing, 'dependency_complete': False, 'capture_kind':capture_kind}
    with database(write=True) as db:
        current_source = db.execute('SELECT enabled,revision FROM sources WHERE id=?', (source['id'],)).fetchone()
        if not current_source or not current_source['enabled'] or current_source['revision'] != source['revision']:
            raise Refused('source_revoked')
        if previous and previous['conversation_id']:
            deleted = db.execute('SELECT deleted FROM conversations WHERE id=?',
                                 (previous['conversation_id'],)).fetchone()
            if deleted and deleted['deleted'] is not None:
                return False
        host = host_id(db)
        native = identity['native_id'] if identity else 'unparsed-' + hashlib.sha256(relative.encode()).hexdigest()[:32]
        conversation = db.execute('SELECT * FROM conversations WHERE host=? AND provider=? AND scope=? AND native_id=?',
                                  (host, source['provider'], source['scope'], native)).fetchone()
        if conversation is None and identity and previous and previous['conversation_id']:
            provisional = db.execute("SELECT * FROM conversations WHERE id=? AND native_id LIKE 'unparsed-%' AND deleted IS NULL",
                                     (previous['conversation_id'],)).fetchone()
            if provisional:
                db.execute('UPDATE conversations SET native_id=?,title=?,cwd=?,parent_native_id=? WHERE id=?',
                           (native, identity['title'], identity['cwd'], identity['parent_native_id'], provisional['id']))
                conversation = db.execute('SELECT * FROM conversations WHERE id=?', (provisional['id'],)).fetchone()
        if conversation and conversation['deleted']:
            return False
        cid = conversation['id'] if conversation else uid()
        if not conversation:
            db.execute('INSERT INTO conversations(id,host,provider,scope,native_id,title,cwd,parent_native_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (cid, host, source['provider'], source['scope'], native, (identity or {}).get('title', ''),
                        (identity or {}).get('cwd', ''), (identity or {}).get('parent_native_id'), now, now))
        if previous:
            current = db.execute('SELECT generation,offset,parsed_offset FROM source_files WHERE id=?', (previous['id'],)).fetchone()
            if not current or tuple(current) != (previous['generation'], previous['offset'], previous['parsed_offset']):
                raise Refused('capture_conflict')
            fid = previous['id']
            db.execute('UPDATE source_files SET conversation_id=?,generation=?,offset=?,parsed_offset=?,fingerprint=?,state=?,seen=? WHERE id=?',
                       (cid, generation, end, next_parsed, fingerprint, manifest['state'], now, fid))
        else:
            fid = uid()
            db.execute('INSERT INTO source_files VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (fid, source['id'], relative, cid, generation, end, next_parsed, fingerprint, manifest['state'], now))
        mid = uid()
        db.execute('INSERT INTO manifests VALUES(?,?,?,?,?,?,?,?)', (mid, cid, fid, generation, end, digest.hexdigest(), encode(manifest), now))
        fresh = []
        for pos, role, body in messages:
            msgid = hashlib.sha256(f'{cid}:{fid}:{generation}:{pos}:{role}'.encode()).hexdigest()[:32]
            if db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?)', (msgid, cid, generation, pos, role, body, now)).rowcount:
                fresh.append((pos, role, body))
        # 처음 들어온 메시지만 센다 — 옛 형식을 다시 읽는 경로(같은 메시지 id)에서 지시 수가 두 번 늘지 않게.
        _refresh_conversation_summary(db, cid, source['provider'], fresh, latest_title, now)
        prior_detail=json.loads(conversation['detail']) if conversation else {}
        verified=prior_detail.get('native_identity_verified',False) or (identity is not None and capture_kind=='transcript' and not changing)
        detail_next={**prior_detail,'native_identity_verified':verified}
        # 저장소(session_catalog/placement.py)가 읽는 사실: 실행 방식, 실제 시작(원본 첫 timestamp), 마지막 활동(원본 파일 수정 시각).
        # 기록 저장소의 created·updated는 수집 시각이라 옛 파일을 처음 읽은 날이 「활동」이 돼 버린다.
        if identity and identity.get('launch') and not prior_detail.get('launch'):
            detail_next['launch']=identity['launch']
        if 'started' not in prior_detail and capture_kind=='transcript':
            from session_catalog.placement import first_timestamp
            started=first_timestamp(probe)
            if started:
                detail_next['started']=started
        from session_catalog.placement import _num
        last_activity=max(_num(prior_detail.get('last')) or 0,after.st_mtime)
        detail_next['last']=last_activity
        if conversation and conversation['workspace_id']:
            from session_catalog.placement import touch_project
            touch_project(db,conversation['workspace_id'],last_activity,now)
        db.execute('UPDATE conversations SET updated=?,state=?,detail=? WHERE id=?', (now, manifest['state'], encode(detail_next), cid))
        changed(db, 'manifest', mid)
    return True


# 사람이 친 지시가 아닌 사용자 줄(시스템이 끼워 넣은 글, FarShell이 대신 친 위임·에이전트 메시지 머리말).
from human_prompt import human_prompt  # noqa: E402 — 사람 지시 판정은 실행 중 세션(agent_status)과 같은 규칙


def _refresh_conversation_summary(db, cid, provider, messages, latest_title, now):
    """이번에 읽은 메시지로 대화 제목과 이 맥 전용 요약(마지막 지시·지시 수)을 갱신한다.

    - 제목: Claude가 붙인 제목 줄이 새로 보이면 바꾼다. Codex는 제목 줄이 없어 제목이 비었을 때만 첫 지시로 정한다.
    - 요약(`conversation_local`)은 복제·원격 응답에 실리지 않는다(사용자 원문은 이 맥에서만, docs/ref/agents.md).
    """
    prompts = [p for p in (human_prompt(body) for _, role, body in messages if role == 'user') if p]
    if latest_title:
        db.execute("UPDATE conversations SET title=? WHERE id=? AND title!=?", (latest_title, cid, latest_title))
    elif provider == 'codex' and prompts:
        db.execute("UPDATE conversations SET title=? WHERE id=? AND title=''", (prompts[0][:60], cid))
    if prompts:
        db.execute("INSERT INTO conversation_local(conversation_id,last_user_text,user_message_count,updated) VALUES(?,?,?,?)"
                   " ON CONFLICT(conversation_id) DO UPDATE SET last_user_text=excluded.last_user_text,"
                   " user_message_count=conversation_local.user_message_count+excluded.user_message_count,updated=excluded.updated",
                   (cid, prompts[-1], len(prompts), now))


def scan_source(source):
    now = time.time()
    root, files, truncated = source_files(source)
    with database(write=True) as db:
        db.executemany('UPDATE source_files SET seen=? WHERE source_id=? AND relative_path=?',
                       [(now, source['id'], name) for name in files])
    updated, errors = 0, 0
    for relative in files:
        try:
            updated += bool(capture(source, relative))
        except (OSError, ValueError, Refused, fsguard.FsDenied):
            errors += 1
    if not truncated:
        with database(write=True) as db:
            db.execute("UPDATE source_files SET state='source_missing' WHERE source_id=? AND seen<?", (source['id'], now))
    return {'updated': updated, 'errors': errors, 'truncated': truncated, 'files': len(files)}


def restore_stream(manifest, out):
    info = json.loads(manifest['detail'])
    digest, count = hashlib.sha256(), 0
    for part in info['chunks']:
        data = read_chunk(part['digest'])
        if len(data) != part['size']:
            raise Refused('chunk_corrupt', 503)
        digest.update(data)
        count += len(data)
        out.write(data)
    if count != manifest['length'] or digest.hexdigest() != manifest['digest']:
        raise Refused('manifest_corrupt', 503)
