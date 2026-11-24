"""Remove what other Macs replicated to this one — `fsh archive-replicas`.

Replication copies a Mac's conversations to another one: the readable messages and conversation records go into the
`replicas` table, the raw transcript chunks into `chunks/`, and the search index covers them too. To keep each Mac with only
its own conversations, turn replication off (settings › 대화 보관, it needs the person's password) and then run this.

    python -m session_history.replica_purge status
    python -m session_history.replica_purge preview [--origin ID]
    python -m session_history.replica_purge apply [--origin ID] [--grace SECONDS]
    python -m session_history.replica_purge vacuum

What `apply` does, in order, and what it never touches:
  1. refuses while this Mac still receives replication (an enabled `receive` grant) — it would only fetch everything again;
  2. writes a consistent copy of the catalog database first (`backup.snapshot`, the daily backup that is kept);
  3. works out which chunk files only the removed copies use — a chunk that a local conversation (or another origin that
     stays) also uses is kept, so nothing of this Mac's own conversations can lose a byte;
  4. deletes the replica rows in small batches, and the checkpoints that replication kept for them;
  5. moves those chunk files into a trash folder, waits `--grace` seconds (a capture that was in the middle of committing a
     manifest that uses one of them gets it back), puts back any chunk a local manifest now uses and deletes the rest;
  6. removes the search index, which is only a projection: the server rebuilds it from this Mac's own messages.
Never touched: this Mac's conversations, messages, manifests and chunks, the originals of Claude/Codex, the replication
grants themselves (a person's decision) and the other Mac's data.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from session_catalog.db import database, directory, host_id, Refused
from store_lock import exclusive, LockTimeout
from . import archive

BATCH = 2000
TRASH = '.purge-trash'
DEFAULT_GRACE = 60


def _digests(detail):
    return {part['digest'] for part in json.loads(detail)['chunks']}


def _replica_digests(kind, payload):
    """Chunks a replicated record points at: a `manifest` row is the manifest itself, a `pending` row wraps one."""
    record = json.loads(payload)
    manifest = record['manifest'] if kind == 'pending' else record
    return _digests(manifest['detail'])


def _on_disk_size(digest):
    total = 0
    for path in (archive.chunk_path(digest), archive.chunk_packed_path(digest)):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _kept_digests(db, removed_origins):
    """Every digest something that stays still uses: local manifests and replicas of origins that are not removed."""
    keep = set()
    for (detail,) in db.execute('SELECT detail FROM manifests'):
        keep |= _digests(detail)
    for origin, kind, payload in db.execute("SELECT origin,kind,payload FROM replicas WHERE kind IN ('manifest','pending')"):
        if origin not in removed_origins:
            keep |= _replica_digests(kind, payload)
    return keep


def plan(origin=None):
    """What would be removed. Read only."""
    with database() as db:
        me = host_id(db)
        origins = {}
        for o, kind, n, size in db.execute(
                'SELECT origin,kind,COUNT(*),SUM(LENGTH(payload)) FROM replicas GROUP BY origin,kind'):
            if o != me and (origin is None or o == origin):
                origins.setdefault(o, {})[kind] = {'rows': n, 'bytes': size or 0}
        removed = set(origins)
        receiving = [r['peer'] for r in db.execute(
            "SELECT peer FROM replication_grants WHERE direction='receive' AND enabled=1")]
        remote = set()
        for o, kind, payload in db.execute("SELECT origin,kind,payload FROM replicas WHERE kind IN ('manifest','pending')"):
            if o in removed:
                remote |= _replica_digests(kind, payload)
        keep = _kept_digests(db, removed)
    only = remote - keep
    on_disk = [d for d in only if archive.chunk_path(d).exists() or archive.chunk_packed_path(d).exists()]
    return {'host': me, 'origins': origins, 'receiving': receiving, 'chunks_only_remote': len(on_disk),
            'chunk_bytes': sum(_on_disk_size(d) for d in on_disk), 'chunks_shared_kept': len(remote & keep),
            '_remove': sorted(on_disk), '_origins': sorted(removed)}


def _delete_rows(origins, progress=None):
    deleted = 0
    for origin in origins:
        while True:
            with database(write=True) as db:
                cursor = db.execute('DELETE FROM replicas WHERE rowid IN (SELECT rowid FROM replicas WHERE origin=? LIMIT ?)',
                                    (origin, BATCH))
                count = cursor.rowcount
            deleted += count
            if progress:
                progress(deleted)
            if count < BATCH:
                break
    return deleted


def _delete_checkpoints(origins):
    from session_catalog.db import setting
    peers = []
    with database(write=True) as db:
        for (peer,) in db.execute('SELECT peer FROM replication_grants').fetchall():
            if setting(db, 'replication-origin:' + peer) in origins:
                peers.append(peer)
        for peer in peers:
            db.execute('DELETE FROM replica_checkpoints WHERE peer=?', (peer,))
    return peers


def _move_chunks(digests, trash):
    trash.mkdir(mode=0o700, exist_ok=True)
    moved = []
    for digest in digests:
        for path in (archive.chunk_path(digest), archive.chunk_packed_path(digest)):
            try:
                os.replace(path, trash / path.name)
                moved.append(path.name)
            except FileNotFoundError:
                continue
    return moved


def _finish_chunks(moved, trash):
    """After the grace period: put back what a local manifest uses now, delete the rest."""
    with database() as db:
        keep = _kept_digests(db, set())
    restored = deleted = freed = 0
    for name in moved:
        digest = name[:-len(archive.PACKED_SUFFIX)] if name.endswith(archive.PACKED_SUFFIX) else name
        source = trash / name
        if digest in keep:
            target = archive.chunk_path(digest).with_name(name)
            if target.exists():                  # written again in the meantime: the trash copy is redundant
                freed += source.stat().st_size
                source.unlink()
            else:
                os.replace(source, target)
            restored += 1
        else:
            try:
                freed += source.stat().st_size
                source.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
    try:
        trash.rmdir()
    except OSError:
        pass
    return {'restored': restored, 'deleted': deleted, 'freed_bytes': freed}


def remove_search_index():
    """The index is a projection of messages and replicas; deleting it makes the server rebuild it from what remains."""
    path = directory() / 'search.sqlite3'
    size = path.stat().st_size if path.is_file() else 0
    for suffix in ('', '-journal', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return size


def apply(origin=None, *, grace=DEFAULT_GRACE, progress=None):
    found = plan(origin)
    if found['receiving']:
        raise Refused('replication_still_on', 409)
    if not found['_origins']:
        return {'rows_deleted': 0, 'chunks': {'deleted': 0, 'restored': 0, 'freed_bytes': 0}, 'search_bytes': 0,
                'checkpoints': [], 'backup': None, 'nothing_to_do': True}
    folder = archive.private_dir(directory() / 'chunks')
    with exclusive(folder / '.purge.lock', deadline=0.5):
        from . import backup
        snapshot = backup.snapshot()                       # a consistent copy of the database as it is right now
        rows = _delete_rows(found['_origins'], progress)
        peers = _delete_checkpoints(set(found['_origins']))
        trash = folder / TRASH
        moved = _move_chunks(found['_remove'], trash)
        if grace:
            time.sleep(grace)
        chunks = _finish_chunks(moved, trash)
        search_bytes = remove_search_index()
    return {'rows_deleted': rows, 'chunks': chunks, 'search_bytes': search_bytes, 'checkpoints': peers,
            'backup': snapshot.name, 'nothing_to_do': False}


def vacuum(*, timeout=120):
    """Give the freed pages back to the file system (the database file never shrinks by itself). It needs the database to
    itself for about a minute; other requests wait. Run it when nobody is using FarShell."""
    path = directory() / 'catalog.sqlite3'
    before = path.stat().st_size
    con = sqlite3.connect(path, timeout=timeout)
    try:
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        con.execute('VACUUM')
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        con.close()
    return {'before': before, 'after': path.stat().st_size}


# ── 명령줄 ───────────────────────────────────────────────────────────────────────────────────────────────────

def _mb(n):
    return f'{n / 1024 ** 2:,.0f}MB'


def _show(found):
    print('다른 맥에서 복제로 받은 사본:')
    if not found['origins']:
        print('  없습니다. 이 맥에는 이 맥의 대화만 있습니다.')
    for origin, kinds in found['origins'].items():
        rows = sum(v['rows'] for v in kinds.values())
        size = sum(v['bytes'] for v in kinds.values())
        detail = ', '.join(f"{k} {v['rows']:,}개" for k, v in sorted(kinds.items()))
        print(f"  출처 {origin[:8]}…: 기록 {rows:,}건 (글 {_mb(size)}) — {detail}")
    if found['origins']:
        print(f"  보관용 조각: 받은 사본에만 쓰이는 조각 {found['chunks_only_remote']:,}개, 디스크 {_mb(found['chunk_bytes'])} (지움 대상)")
        print(f"  이 맥 자기 대화와 같은 내용이라 남기는 조각 {found['chunks_shared_kept']:,}개")
        print('  검색 목차: 지운 뒤 서버가 자기 대화만으로 다시 만듭니다(그동안 검색은 느린 방식으로 동작)')
    if found['receiving']:
        print(f"  ⚠ 아직 복제를 받는 설정이 켜져 있습니다({', '.join(found['receiving'])}). 설정 › 대화 보관에서 먼저 끄세요. 켜져 있는 동안은 지우지 않습니다.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog='fsh archive-replicas', description='다른 맥에서 복제로 받은 사본을 정리(이 맥에는 이 맥의 대화만)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('status', 'preview', 'apply', 'vacuum'):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
        if name in ('preview', 'apply'):
            p.add_argument('--origin', default=None, help='이 출처의 사본만(기본: 다른 맥 전부)')
        if name == 'apply':
            p.add_argument('--grace', type=int, default=DEFAULT_GRACE, help='조각을 휴지통에 둔 채 다시 확인하며 기다리는 초(기본 60)')
    args = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        if args.cmd in ('status', 'preview'):
            found = plan(getattr(args, 'origin', None))
            public = {k: v for k, v in found.items() if not k.startswith('_')}
            if args.json:
                print(json.dumps(public))
            else:
                _show(found)
                if args.cmd == 'preview' and found['origins'] and not found['receiving']:
                    print('실제로 지우려면: fsh archive-replicas apply')
            return 0
        if args.cmd == 'apply':
            out = apply(args.origin, grace=args.grace,
                        progress=None if args.json else (lambda n: print(f'  받은 기록 {n:,}건 삭제', end='\r', flush=True)))
            if args.json:
                print(json.dumps(out))
                return 0
            if out['nothing_to_do']:
                print('지울 복제 사본이 없습니다.')
                return 0
            c = out['chunks']
            print(f"\n받은 기록 {out['rows_deleted']:,}건, 보관용 조각 {c['deleted']:,}개({_mb(c['freed_bytes'])}) 삭제, 검색 목차 {_mb(out['search_bytes'])} 제거")
            if c['restored']:
                print(f"  유예 중 이 맥 자기 대화가 쓰게 된 조각 {c['restored']}개는 되돌려 놓았습니다")
            print(f"  지우기 전 DB 백업: backups/{out['backup']}")
            print('  목록 DB 파일 크기까지 줄이려면: fsh archive-replicas vacuum (잠시 다른 요청이 기다립니다)')
            return 0
        out = vacuum()
        print(json.dumps(out) if args.json else f"목록 DB {_mb(out['before'])} → {_mb(out['after'])}")
        return 0
    except Refused as error:
        why = {'replication_still_on': '이 맥이 아직 복제를 받는 설정이 켜져 있습니다. 설정 › 대화 보관에서 먼저 끄세요.'}.get(error.code, error.code)
        print(f'멈췄습니다: {why}', file=sys.stderr)
        return 3
    except LockTimeout:
        print('다른 정리가 이미 실행 중입니다.', file=sys.stderr)
        return 3
    except sqlite3.OperationalError as error:
        print(f'DB가 사용 중이라 못 했습니다({error}). 잠시 뒤 다시 하세요.', file=sys.stderr)
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
