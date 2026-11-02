"""Immutable raw chunks. Source disappearance never deletes a committed capture."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
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


def _quota(*,replica=False,additional=0):
    with database() as db:
        quota = setting(db, 'replica_quota_bytes' if replica else 'quota_bytes', (50 if replica else 20) * 1024**3)
        # A shared physical chunk counts against each role that references it.
        # Pending receiver manifests count too, so interrupted transfers stay bounded.
        local, remote = set(), set()
        for row in db.execute('SELECT detail FROM manifests'):
            local.update(part['digest'] for part in json.loads(row['detail'])['chunks'])
        for row in db.execute("SELECT kind,payload FROM replicas WHERE kind IN ('manifest','pending')"):
            payload = json.loads(row['payload'])
            manifest = payload['manifest'] if row['kind'] == 'pending' else payload
            remote.update(part['digest'] for part in json.loads(manifest['detail'])['chunks'])
    owned = remote if replica else local
    known = local | remote
    # Uncommitted/orphan bytes also consume quota; failed captures cannot evade it.
    total = sum(path.stat().st_size for path in (directory() / 'chunks').glob('*')
                if path.is_file() and not path.is_symlink() and (path.name in owned or path.name not in known))
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
        for index, (role, body) in enumerate(projected):
            if body:
                out.append((position * 1000000 + index, role, body))
    checkpoint.update(version=1, skipping_oversized=skipping, omitted_reasons=sorted(reasons),
                      incomplete_record=incomplete)
    return out, offset, bool(reasons or skipping or incomplete)


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
        for pos, role, body in messages:
            msgid = hashlib.sha256(f'{cid}:{fid}:{generation}:{pos}:{role}'.encode()).hexdigest()[:32]
            db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?)', (msgid, cid, generation, pos, role, body, now))
        prior_detail=json.loads(conversation['detail']) if conversation else {}
        verified=prior_detail.get('native_identity_verified',False) or (identity is not None and capture_kind=='transcript' and not changing)
        db.execute('UPDATE conversations SET updated=?,state=?,detail=? WHERE id=?', (now, manifest['state'], encode({**prior_detail,'native_identity_verified':verified}), cid))
        changed(db, 'manifest', mid)
    return True


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
