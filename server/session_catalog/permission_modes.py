"""Claude's last observed policy, scoped by verified native profile and conversation.

Unscoped rows remain readable for old callers, but are never sufficient for resume.
Unsupported newer values invalidate scoped records instead of reviving an older bypass.
"""
from __future__ import annotations

import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

from .db import database

logger = logging.getLogger(__name__)
NATIVE_ID = re.compile(r'[A-Za-z0-9_-]{1,160}')
KNOWN = frozenset({'default', 'acceptEdits', 'plan', 'bypassPermissions'})
REPLAYED = KNOWN
_lock = threading.Lock()
_last: dict[tuple, tuple[str, int]] = {}


def record(native_id, mode, *, root=None, observed_ns=None) -> bool:
    """Remember accepted hook policy. Old hooks without a timestamp use arrival order."""
    if not isinstance(native_id, str) or not NATIVE_ID.fullmatch(native_id) or mode is None:
        return False
    if root is None and (not isinstance(mode, str) or mode not in KNOWN):
        return False
    if root is not None:
        if not isinstance(root, str) or not Path(root).is_absolute():
            return False
        try:
            root = str(Path(root).resolve())
        except (OSError, ValueError):
            return False
    if observed_ns is not None and (type(observed_ns) is not int or not 0 < observed_ns < 2**63):
        return False
    stored = mode if isinstance(mode, str) and mode in KNOWN else 'unsupported'
    stamp = observed_ns if observed_ns is not None else time.time_ns()
    key = (root, native_id)
    with _lock:
        cached = _last.get(key)
        if cached and (stamp <= cached[1] or (observed_ns is None and cached[0] == stored)):
            return False
        try:
            with database(write=True) as db:
                if root is None:
                    db.execute('''CREATE TABLE IF NOT EXISTS native_permission_modes(
                        native_id TEXT PRIMARY KEY,mode TEXT NOT NULL,updated REAL NOT NULL)''')
                    db.execute('''INSERT INTO native_permission_modes VALUES(?,?,?)
                        ON CONFLICT(native_id) DO UPDATE SET mode=excluded.mode,updated=excluded.updated''',
                               (native_id, stored, time.time()))
                else:
                    db.execute('''CREATE TABLE IF NOT EXISTS scoped_permission_modes(
                        root TEXT NOT NULL,native_id TEXT NOT NULL,mode TEXT NOT NULL,updated INTEGER NOT NULL,
                        PRIMARY KEY(root,native_id))''')
                    written = db.execute('''INSERT INTO scoped_permission_modes VALUES(?,?,?,?)
                        ON CONFLICT(root,native_id) DO UPDATE SET mode=excluded.mode,updated=excluded.updated
                        WHERE excluded.updated > scoped_permission_modes.updated''', (root, native_id, stored, stamp))
                    if not written.rowcount:
                        return False
        except Exception as exc:  # bookkeeping must never fail a hook
            logger.debug('permission mode not stored: %s', type(exc).__name__)
            return False
        _last[key] = (stored, stamp)
    return True


def lookup(db, native_id, *, root=None) -> str | None:
    if not isinstance(native_id, str) or not NATIVE_ID.fullmatch(native_id):
        return None
    try:
        if root is None:
            row = db.execute('SELECT mode FROM native_permission_modes WHERE native_id=?', (native_id,)).fetchone()
        else:
            row = db.execute('SELECT mode FROM scoped_permission_modes WHERE root=? AND native_id=?',
                             (str(Path(root).resolve()), native_id)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] in REPLAYED else None


def forget_cache() -> None:
    with _lock:
        _last.clear()


def invalidate(native_id, *, observed_ns=None) -> None:
    """Unknown profile on a newer accepted hook invalidates earlier scoped policies."""
    if not isinstance(native_id, str) or not NATIVE_ID.fullmatch(native_id):
        return
    stamp = observed_ns if type(observed_ns) is int and 0 < observed_ns < 2**63 else time.time_ns()
    with _lock:
        for key in list(_last):
            if key[1] == native_id and _last[key][1] <= stamp:
                del _last[key]
        try:
            with database(write=True) as db:
                db.execute("UPDATE scoped_permission_modes SET mode='unsupported',updated=? WHERE native_id=? AND updated<=?",
                           (stamp, native_id, stamp))
        except sqlite3.OperationalError:
            pass
