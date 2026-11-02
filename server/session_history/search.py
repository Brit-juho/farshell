"""Disposable FTS projection. Raw and normalized records remain authoritative."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from session_catalog.db import database, directory, private_file, host_id


# The index stores no copy of the bodies (`content=''`): searches only need which conversation matched,
# kept in `owners`, and every hit is re-read and permission-checked in the catalog anyway. The old layout
# kept a full second copy of every message (~0.4 GB) and is rebuilt once from scratch.
SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS bodies USING fts5(body,content='',tokenize='trigram')",
    'CREATE TABLE IF NOT EXISTS owners(rowid INTEGER PRIMARY KEY,origin TEXT NOT NULL,conversation TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS progress(kind TEXT PRIMARY KEY,position INTEGER NOT NULL)',
)


def _outdated(path):
    """True for an index in the old self-contained layout (bodies without owners)."""
    if not path.is_file() or path.is_symlink():
        return False
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=.2) as index:
        names = {r[0] for r in index.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return 'bodies' in names and 'owners' not in names


def _refresh(limit=1000):
    path = directory() / 'search.sqlite3'
    private_file(path)
    with database() as source, sqlite3.connect(path, timeout=.2) as index:
        for statement in SCHEMA:
            index.execute(statement)
        origin = host_id(source)
        for kind, sql in (
            ('local', 'SELECT rowid,id,conversation_id,body FROM messages WHERE rowid>? ORDER BY rowid LIMIT ?'),
            ('replica', "SELECT rowid,origin,payload FROM replicas WHERE kind='message' AND rowid>? ORDER BY rowid LIMIT ?"),
        ):
            row = index.execute('SELECT position FROM progress WHERE kind=?', (kind,)).fetchone()
            position = row[0] if row else 0
            rows = source.execute(sql, (position, limit)).fetchall()
            for record in rows:
                if kind == 'local':
                    owner, cid, body = origin, record['conversation_id'], record['body']
                else:
                    message = json.loads(record['payload'])
                    owner, cid, body = record['origin'], message['conversation_id'], message['body']
                rowid = index.execute('INSERT INTO owners(origin,conversation) VALUES(?,?)', (owner, cid)).lastrowid
                index.execute('INSERT INTO bodies(rowid,body) VALUES(?,?)', (rowid, body))
            if rows:
                position = rows[-1]['rowid']
            if rows or row is None:
                index.execute('INSERT INTO progress VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET position=excluded.position', (kind,position))


def matches(source, search):
    """Use FTS only when caught up to this catalog snapshot; otherwise use LIKE.

    The caller still checks deletion/visibility in the authoritative database.
    Bound result count so broad queries cannot allocate an unbounded ID set.
    """
    if len(search) < 3 or '\x00' in search:
        return None
    path = directory() / 'search.sqlite3'
    if not path.is_file() or path.is_symlink():
        return None
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=.1) as index:
            progress = dict(index.execute('SELECT kind,position FROM progress'))
            local = source.execute('SELECT COALESCE(MAX(rowid),0) FROM messages').fetchone()[0]
            remote = source.execute("SELECT COALESCE(MAX(rowid),0) FROM replicas WHERE kind='message'").fetchone()[0]
            if progress.get('local',0) < local or progress.get('replica',0) < remote:
                return None
            query = '"' + search.replace('"','""') + '"'
            rows = index.execute('SELECT DISTINCT o.origin,o.conversation FROM bodies b JOIN owners o ON o.rowid=b.rowid WHERE bodies MATCH ? LIMIT 10001', (query,)).fetchall()
            return rows if len(rows) <= 10000 else None
    except sqlite3.Error:
        # Index loss/corruption does not make the archive unavailable.
        return None


def refresh(limit=1000):
    from session_catalog.db import uid,Refused
    import os
    path=directory()/'search.sqlite3'
    try:
        if _outdated(path):
            # Disposable: searches fall back to SQL LIKE (matches() returns None) until it has caught up.
            path.unlink()
            for suffix in ('-journal','-wal','-shm'):
                Path(str(path)+suffix).unlink(missing_ok=True)
        _refresh(limit)
    except (sqlite3.DatabaseError,Refused) as error:
        cause=error.__cause__ if isinstance(error,Refused) else error
        if getattr(cause,'sqlite_errorcode',None) not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):
            raise
        # Only the disposable index is replaced; catalog/messages/raw bytes are untouched.
        invalid=path.with_name('.invalid-search-'+uid())
        if path.exists() and not path.is_symlink():
            os.replace(path,invalid)
        try:
            _refresh(limit)
        finally:
            invalid.unlink(missing_ok=True)
