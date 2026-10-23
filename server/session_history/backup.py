"""Consistent SQLite snapshots; archives never include credentials or transport keys."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
import time
from pathlib import Path

import fsguard
from session_catalog.db import database, directory, private_dir, private_file, uid, Refused, setting, set_setting
from .archive import chunk_path, read_chunk


def snapshot():
    folder = private_dir(directory() / 'backups')
    temp, dest = folder / ('.' + uid()), folder / (str(time.time_ns()) + '.sqlite3')
    private_file(temp)
    try:
        with database() as source, sqlite3.connect(temp) as target:
            source.backup(target, pages=128, sleep=.01)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise Refused('backup_invalid', 503)
        fd = os.open(temp, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, dest)
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        with database(write=True) as db:
            set_setting(db, 'last_backup', {'created': time.time(), 'file': dest.name})
        # Only our generated snapshots, never raw transcript chunks, are rotated.
        backups = sorted(p for p in folder.glob('*.sqlite3') if p.is_file() and not p.is_symlink())
        for path in backups[:-7]:
            path.unlink()
        return dest
    finally:
        temp.unlink(missing_ok=True)


def maybe_snapshot():
    with database() as db:
        previous = setting(db, 'last_backup', {})
    if time.time() - previous.get('created', 0) >= 86400:
        snapshot()


def export_package():
    snap = snapshot()
    target = private_dir(directory() / 'exports') / (uid() + '.tar')
    private_file(target)
    try:
        with sqlite3.connect(f'file:{snap}?mode=ro', uri=True) as db:
            hashes = set()
            details = [json.loads(row[0]) for row in db.execute('SELECT detail FROM manifests')]
            details += [json.loads(json.loads(row[0])['detail']) for row in db.execute("SELECT payload FROM replicas WHERE kind='manifest'")]
            for detail in details:
                hashes.update(part['digest'] for part in detail['chunks'])
        with tarfile.open(target, 'w') as package:
            package.add(snap, arcname='catalog.sqlite3', recursive=False)
            for digest in sorted(hashes):
                read_chunk(digest)
                package.add(chunk_path(digest), arcname='chunks/' + digest, recursive=False)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def restore_package(package_path, destination_state, *, max_bytes=50*1024**3):
    """Offline disaster recovery into an absent catalog directory only.

    No server may be using the destination. Sources and replication are disabled
    for explicit review; stored runtime observations never revive processes.
    """
    import shutil
    import stat
    from session_catalog.db import DDL, SCHEMA_VERSION, HASH
    destination = private_dir(Path(destination_state).resolve()) / 'session-catalog'
    if destination.exists() or destination.is_symlink():
        raise Refused('restore_target_exists')
    staging = private_dir(destination.parent / ('.history-restore-' + uid()))
    try:
        seen, total = set(), 0
        with tarfile.open(package_path, 'r:') as package:
            for number, member in enumerate(package):
                name = member.name
                valid = name == 'catalog.sqlite3' or (name.startswith('chunks/') and HASH.fullmatch(name[7:]))
                if (number >= 100001 or not valid or name in seen or not member.isfile()
                        or member.size < 0 or member.size > max_bytes):
                    raise Refused('backup_invalid',400)
                seen.add(name)
                total += member.size
                if total > max_bytes:
                    raise Refused('backup_too_large',413)
                target = staging / name
                private_dir(target.parent)
                stream = package.extractfile(member)
                if stream is None:
                    raise Refused('backup_invalid',400)
                digest = hashlib.sha256()
                with stream, target.open('xb') as out:
                    os.chmod(target,0o600)
                    remaining = member.size
                    while remaining:
                        block = stream.read(min(4*1024*1024,remaining))
                        if not block:
                            raise Refused('backup_invalid',400)
                        out.write(block); digest.update(block); remaining -= len(block)
                    out.flush(); os.fsync(out.fileno())
                if name.startswith('chunks/') and digest.hexdigest() != name[7:]:
                    raise Refused('backup_invalid',400)
        if 'catalog.sqlite3' not in seen:
            raise Refused('backup_invalid',400)
        expected = sqlite3.connect(':memory:')
        expected.executescript(DDL)
        schema = list(expected.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        expected.close()
        with sqlite3.connect(staging/'catalog.sqlite3') as restored:
            if (restored.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION
                    or restored.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                    or list(restored.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")) != schema
                    or restored.execute('PRAGMA foreign_key_check').fetchone()):
                raise Refused('backup_invalid',400)
            records = [json.loads(r[0]) for r in restored.execute('SELECT detail FROM manifests')]
            records += [json.loads(json.loads(r[0])['detail']) for r in restored.execute("SELECT payload FROM replicas WHERE kind='manifest'")]
            for manifest in records:
                for part in manifest['chunks']:
                    if 'chunks/'+part['digest'] not in seen or (staging/'chunks'/part['digest']).stat().st_size != part['size']:
                        raise Refused('backup_invalid',400)
            restored.execute('UPDATE sources SET enabled=0,revision=revision+1')
            restored.execute('UPDATE replication_grants SET enabled=0,revision=revision+1')
            restored.execute("UPDATE runtimes SET state='stopped',ended=COALESCE(ended,?),observed=0",(time.time(),))
            restored.execute("UPDATE operations SET state='needs_review' WHERE state='starting'")
        os.rename(staging,destination)
        return {'state':'restored','sources_enabled':False,'replication_enabled':False}
    except (KeyError,TypeError,ValueError,sqlite3.Error,tarfile.TarError) as error:
        raise Refused('backup_invalid',400) from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)
