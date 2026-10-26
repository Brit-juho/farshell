"""Disposable FTS projection. Raw and normalized records remain authoritative."""
from __future__ import annotations

import json
import sqlite3

from session_catalog.db import database, directory, private_file, host_id


def _refresh(limit=1000):
    path = directory() / 'search.sqlite3'
    private_file(path)
    with database() as source, sqlite3.connect(path, timeout=.2) as index:
        index.execute("CREATE VIRTUAL TABLE IF NOT EXISTS bodies USING fts5(origin UNINDEXED,conversation UNINDEXED,body,tokenize='trigram')")
        index.execute('CREATE TABLE IF NOT EXISTS progress(kind TEXT PRIMARY KEY,position INTEGER NOT NULL)')
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
                index.execute('INSERT INTO bodies(origin,conversation,body) VALUES(?,?,?)', (owner,cid,body))
            if rows:
                position = rows[-1]['rowid']
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
            rows = index.execute('SELECT DISTINCT origin,conversation FROM bodies WHERE bodies MATCH ? LIMIT 10001', (query,)).fetchall()
            return rows if len(rows) <= 10000 else None
    except sqlite3.Error:
        # Index loss/corruption does not make the archive unavailable.
        return None


def refresh(limit=1000):
    from session_catalog.db import uid,Refused
    import os
    path=directory()/'search.sqlite3'
    try:
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
