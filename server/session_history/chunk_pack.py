"""Conversion of stored chunks between the raw and the packed (zstd) form — `fsh archive-compress`.

New chunks are packed by `archive.put_chunk`. This tool converts the chunks that were stored before that, and can
convert them back. Nothing here changes a chunk's name (the sha256 of its raw bytes), a manifest or the database.

Safety, in order, for every chunk that is converted:
  1. the raw file is read and its sha256 must equal its name;
  2. the packed copy is written atomically (fsync, rename) next to it;
  3. the packed copy is read back and unpacked, and its sha256 must equal the name;
  4. only after the folder has been synced is the raw file removed.
A run that stops anywhere (Ctrl-C, power loss, a full disk) leaves every chunk readable: at least one of the two
forms exists at every moment, and `read_chunk` prefers the raw one. It is safe to run while the server is running.

    python -m session_history.chunk_pack status
    python -m session_history.chunk_pack preview [--sample N]
    python -m session_history.chunk_pack apply [--limit-mb N]
    python -m session_history.chunk_pack expand [--limit-mb N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

from session_catalog.db import directory, HASH
from store_lock import exclusive, LockTimeout
from . import archive

MIN_FREE = 2 * 1024 ** 3        # the same floor the archive keeps free for itself (archive._quota)
BATCH = 200                     # chunks whose folder entries are synced together before their raw files go
LOW_DISK = 'low_disk'


def _folder():
    return archive.private_dir(directory() / 'chunks')


def _entries(folder=None):
    """(digest, packed?, path, size) for every chunk file; temp files (dot names) and anything else are skipped."""
    folder = folder or _folder()
    with os.scandir(folder) as it:
        for entry in it:
            name = entry.name
            packed = name.endswith(archive.PACKED_SUFFIX)
            digest = name[:-len(archive.PACKED_SUFFIX)] if packed else name
            if name.startswith('.') or not HASH.fullmatch(digest) or not entry.is_file(follow_symlinks=False):
                continue
            yield digest, packed, entry.path, entry.stat(follow_symlinks=False).st_size


def status():
    raw = packed = raw_bytes = packed_bytes = both = 0
    seen = {}
    for digest, is_packed, _path, size in _entries():
        seen.setdefault(digest, set()).add(is_packed)
        if is_packed:
            packed += 1
            packed_bytes += size
        else:
            raw += 1
            raw_bytes += size
    both = sum(1 for forms in seen.values() if len(forms) == 2)
    return {'raw_chunks': raw, 'raw_bytes': raw_bytes, 'packed_chunks': packed, 'packed_bytes': packed_bytes,
            'both_forms': both, 'free_bytes': shutil.disk_usage(directory()).free}


def preview(sample=None):
    """Read-only estimate: compress the raw chunks in memory (all of them, or a random sample) and extrapolate."""
    import random
    raws = [(d, p, s) for d, packed, p, s in _entries() if not packed]
    total_bytes = sum(s for _d, _p, s in raws)
    if sample and sample < len(raws):
        random.Random(1).shuffle(raws)
        raws = raws[:sample]
    before = after = skipped = unreadable = 0
    for digest, path, size in raws:
        try:
            with open(path, 'rb') as stream:
                data = stream.read()
        except OSError:
            unreadable += 1
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            unreadable += 1
            continue
        blob = archive.pack_bytes(data)
        before += len(data)
        if len(blob) < len(data):
            after += len(blob)
        else:
            after += len(data)
            skipped += 1
    ratio = after / before if before else 1.0
    return {'raw_total_bytes': total_bytes, 'estimated_after_bytes': int(total_bytes * ratio),
            'estimated_saved_bytes': int(total_bytes * (1 - ratio)), 'ratio': ratio,
            'chunks_measured': len(raws) - unreadable, 'sampled': bool(sample), 'incompressible': skipped,
            'unreadable': unreadable}


def _verify_packed(path, digest):
    """Read the packed file back from disk and check it unpacks to bytes whose sha256 is the chunk's name."""
    try:
        with open(path, 'rb') as stream:
            data = archive.unpack_bytes(stream.read(archive.CHUNK + archive.PACK_SLACK + 1))
    except (OSError, archive.Refused):
        return False
    return hashlib.sha256(data).hexdigest() == digest


def _sync_folder(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock():
    return exclusive(_folder() / '.pack.lock', deadline=0.5)


def apply(*, limit_bytes=None, min_free=MIN_FREE, progress=None, should_stop=None):
    """Pack every raw chunk. Returns a summary; `stopped` says why it ended early ('limit', 'low_disk', 'interrupted')."""
    folder = _folder()
    out = {'converted': 0, 'bytes_before': 0, 'bytes_after': 0, 'kept_raw_incompressible': 0, 'dropped_duplicates': 0,
           'failed': [], 'stopped': None}
    work = [(d, p, s) for d, packed, p, s in _entries(folder) if not packed]
    done_raw = []                      # raw files whose packed copy is written and verified, waiting for the folder sync

    def flush():
        if not done_raw:
            return
        _sync_folder(folder)           # the packed copies are durable before any raw file is removed
        for path, size, packed_size in done_raw:
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            out['converted'] += 1
            out['bytes_before'] += size
            out['bytes_after'] += packed_size
        done_raw.clear()

    with _lock():
        for index, (digest, path, size) in enumerate(work):
            if should_stop and should_stop():
                out['stopped'] = 'interrupted'
                break
            if limit_bytes is not None and out['bytes_before'] + sum(x[1] for x in done_raw) >= limit_bytes:
                out['stopped'] = 'limit'
                break
            if shutil.disk_usage(folder).free < min_free:
                out['stopped'] = LOW_DISK
                break
            packed_path = archive.chunk_packed_path(digest)
            try:
                with open(path, 'rb') as stream:
                    data = stream.read(archive.CHUNK + 1)
            except FileNotFoundError:
                continue                       # converted or removed by someone else in the meantime
            if len(data) > archive.CHUNK or hashlib.sha256(data).hexdigest() != digest:
                out['failed'].append({'digest': digest, 'why': 'raw_mismatch'})   # never touch a chunk that is already wrong
                continue
            if os.path.exists(packed_path):     # an earlier run (or the server) already wrote it: verify, then drop the raw
                if _verify_packed(packed_path, digest):
                    done_raw.append((path, size, os.path.getsize(packed_path)))
                    out['dropped_duplicates'] += 1
                else:
                    out['failed'].append({'digest': digest, 'why': 'existing_packed_invalid'})
                continue
            blob = archive.pack_bytes(data)
            if len(blob) >= len(data):
                out['kept_raw_incompressible'] += 1
                continue
            archive.write_chunk_file(packed_path, blob)
            if not _verify_packed(packed_path, digest):
                try:
                    os.unlink(packed_path)
                finally:
                    out['failed'].append({'digest': digest, 'why': 'verify_failed'})
                continue
            done_raw.append((path, size, len(blob)))
            if len(done_raw) >= BATCH:
                flush()
                if progress:
                    progress(index + 1, len(work), out)
        flush()
    return out


def expand(*, limit_bytes=None, min_free=MIN_FREE, progress=None, should_stop=None):
    """The inverse of `apply`: every packed chunk back to its raw form (used to go back to an older FarShell)."""
    folder = _folder()
    out = {'converted': 0, 'bytes_before': 0, 'bytes_after': 0, 'dropped_duplicates': 0, 'failed': [], 'stopped': None}
    work = [(d, p, s) for d, packed, p, s in _entries(folder) if packed]
    done = []

    def flush():
        if not done:
            return
        _sync_folder(folder)
        for path, size, raw_size in done:
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            out['converted'] += 1
            out['bytes_before'] += size
            out['bytes_after'] += raw_size
        done.clear()

    with _lock():
        for index, (digest, path, size) in enumerate(work):
            if should_stop and should_stop():
                out['stopped'] = 'interrupted'
                break
            if limit_bytes is not None and out['bytes_after'] + sum(x[2] for x in done) >= limit_bytes:
                out['stopped'] = 'limit'
                break
            if shutil.disk_usage(folder).free < min_free:
                out['stopped'] = LOW_DISK
                break
            raw_path = archive.chunk_path(digest)
            try:
                with open(path, 'rb') as stream:
                    data = archive.unpack_bytes(stream.read(archive.CHUNK + archive.PACK_SLACK + 1))
            except FileNotFoundError:
                continue
            except (OSError, archive.Refused):
                out['failed'].append({'digest': digest, 'why': 'packed_invalid'})
                continue
            if hashlib.sha256(data).hexdigest() != digest:
                out['failed'].append({'digest': digest, 'why': 'packed_mismatch'})
                continue
            if not os.path.exists(raw_path):
                archive.write_chunk_file(raw_path, data)
                with open(raw_path, 'rb') as stream:
                    if hashlib.sha256(stream.read(archive.CHUNK + 1)).hexdigest() != digest:
                        os.unlink(raw_path)
                        out['failed'].append({'digest': digest, 'why': 'verify_failed'})
                        continue
            else:
                out['dropped_duplicates'] += 1
            done.append((path, size, len(data)))
            if len(done) >= BATCH:
                flush()
                if progress:
                    progress(index + 1, len(work), out)
        flush()
    return out


def _gb(n):
    return f'{n / 1024 ** 3:.2f}GB'


def _mb(n):
    return f'{n / 1024 ** 2:.0f}MB'


def _print_status(s):
    print(f"  원래 형식 조각: {s['raw_chunks']:,}개, {_gb(s['raw_bytes'])}")
    print(f"  압축 저장 조각: {s['packed_chunks']:,}개, {_gb(s['packed_bytes'])}")
    if s['both_forms']:
        print(f"  두 형식이 모두 있는 조각: {s['both_forms']}개 (변환 도중이었던 흔적 — apply를 다시 실행하면 정리됩니다)")
    print(f"  디스크 여유: {_gb(s['free_bytes'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog='fsh archive-compress', description='대화 사본(조각) 압축 저장으로 변환·되돌리기')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('status', 'preview', 'apply', 'expand'):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
        if name == 'preview':
            p.add_argument('--sample', type=int, default=None, help='N개만 표본으로 재 보기(빠름)')
        if name in ('apply', 'expand'):
            p.add_argument('--limit-mb', type=int, default=None, help='이만큼 처리하고 멈춤(나눠서 하기)')
    args = ap.parse_args(argv)
    try:
        os.nice(10)             # the terminal and the web UI come first
    except OSError:
        pass
    if args.cmd == 'status':
        s = status()
        print(json.dumps(s) if args.json else '조각 저장 현황:')
        if not args.json:
            _print_status(s)
        return 0
    if args.cmd == 'preview':
        s = status()
        r = preview(args.sample)
        if args.json:
            print(json.dumps({'status': s, 'preview': r}))
            return 0
        print('조각 저장 현황:')
        _print_status(s)
        print('압축하면(읽기만 하고 아무것도 바꾸지 않았습니다):')
        print(f"  원래 형식 조각 합계 {_gb(r['raw_total_bytes'])} → 약 {_gb(r['estimated_after_bytes'])} "
              f"(약 {_gb(r['estimated_saved_bytes'])} 절약, 남는 비율 {r['ratio']:.0%})")
        print(f"  측정한 조각 {r['chunks_measured']:,}개{' (표본)' if r['sampled'] else ''}, 압축해도 안 줄어 그대로 두는 조각 {r['incompressible']}개, 읽지 못한 조각 {r['unreadable']}개")
        return 0
    limit = args.limit_mb * 1024 ** 2 if args.limit_mb else None
    started = time.monotonic()

    def progress(done, total, out):
        print(f"  {done:,}/{total:,} 처리, 변환 {out['converted']:,}개, {_mb(out['bytes_before'])} → {_mb(out['bytes_after'])}", flush=True)

    try:
        out = (apply if args.cmd == 'apply' else expand)(limit_bytes=limit, progress=None if args.json else progress)
    except LockTimeout:
        print('다른 변환이 이미 실행 중입니다.', file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print('중단했습니다. 지금까지 변환한 조각은 정상이고 나머지는 원래대로입니다. 다시 실행하면 이어서 합니다.')
        return 130
    if args.json:
        print(json.dumps(out))
    else:
        verb = '압축 저장으로 변환' if args.cmd == 'apply' else '원래 형식으로 복원'
        print(f"{verb}: {out['converted']:,}개, {_mb(out['bytes_before'])} → {_mb(out['bytes_after'])} ({time.monotonic() - started:.0f}초)")
        if out.get('kept_raw_incompressible'):
            print(f"  압축해도 안 줄어 그대로 둔 조각 {out['kept_raw_incompressible']}개")
        if out['stopped']:
            why = {'limit': '지정한 양에 도달해서', 'low_disk': '디스크 여유가 2GB 미만이라', 'interrupted': '중단 요청으로'}[out['stopped']]
            print(f'  {why} 멈췄습니다. 다시 실행하면 이어서 합니다.')
        if out['failed']:
            print(f"  변환하지 못한 조각 {len(out['failed'])}개(원본은 그대로 있습니다): " + ', '.join(f"{f['digest'][:8]}…{f['why']}" for f in out['failed'][:5]))
    return 1 if out['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
