"""Last permission mode a Claude conversation ran in, so a resume can start in the same mode.

The mode comes from the `permission_mode` field of Claude Code's own hook payload and is stored per
native conversation id. It is a launch hint, not a grant: only the modes below are ever stored or
replayed, and a mode this table does not know is dropped instead of being passed to the CLI.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time

from .db import database

logger = logging.getLogger(__name__)

NATIVE_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
# `--permission-mode` choices that exist in every Claude Code release we support. Newer modes
# (auto, dontAsk, ...) are not replayed: an unknown choice makes the CLI exit at once.
KNOWN = frozenset({"default", "acceptEdits", "plan", "bypassPermissions"})
# `default` is the CLI's own default, so nothing is added to the command line for it.
REPLAYED = KNOWN - {"default"}

_lock = threading.Lock()
_last: dict[str, str] = {}   # native id -> mode already stored; hooks fire on every tool call


def record(native_id, mode) -> bool:
    """Remember `mode` for the conversation. Returns True only when a row was written."""
    if not isinstance(native_id, str) or not NATIVE_ID.fullmatch(native_id):
        return False
    if not isinstance(mode, str) or mode not in KNOWN:   # a hook body can carry any JSON type
        return False
    with _lock:
        if _last.get(native_id) == mode:
            return False
    try:
        with database(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS native_permission_modes(
                native_id TEXT PRIMARY KEY,mode TEXT NOT NULL,updated REAL NOT NULL)""")
            db.execute("""INSERT INTO native_permission_modes VALUES(?,?,?)
                ON CONFLICT(native_id) DO UPDATE SET mode=excluded.mode,updated=excluded.updated""",
                       (native_id, mode, time.time()))
    except Exception as exc:  # noqa: BLE001 — a hook must never fail because of this bookkeeping
        logger.debug("permission mode not stored: %s", type(exc).__name__)
        return False
    with _lock:
        _last[native_id] = mode
    return True


def lookup(db, native_id) -> str | None:
    """The mode to replay on resume, or None when nothing should be added to the command."""
    if not isinstance(native_id, str) or not NATIVE_ID.fullmatch(native_id):
        return None
    try:
        row = db.execute("SELECT mode FROM native_permission_modes WHERE native_id=?", (native_id,)).fetchone()
    except sqlite3.OperationalError:   # table is created by the first stored mode
        return None
    return row[0] if row and row[0] in REPLAYED else None


def forget_cache() -> None:
    """Tests switch state directories between cases; the in-memory shortcut must not outlive them."""
    with _lock:
        _last.clear()
