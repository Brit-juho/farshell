"""Private, versioned catalog; never repair an unreadable database by replacing it."""
from __future__ import annotations

import json
import fcntl
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import vt_paths

SCHEMA_VERSION = 1
ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")


class Refused(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refused("invalid_id", 400)
    return value


def uid():
    return secrets.token_hex(16)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def private_dir(path: Path):
    # Parent state directory is user-configured; descendants may never be links.
    if path.is_symlink():
        raise Refused("unsafe_store", 503)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Refused("unsafe_store", 503)
    os.chmod(path, 0o700)
    return path


def directory():
    return private_dir(vt_paths.state_dir().resolve() / "session-catalog")


def private_file(path: Path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
            raise Refused("unsafe_store", 503)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE projects(id TEXT PRIMARY KEY,host TEXT NOT NULL,locator TEXT NOT NULL,name TEXT NOT NULL,
 path TEXT NOT NULL,kind TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(host,locator));
CREATE TABLE workspaces(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),path TEXT NOT NULL,
 kind TEXT NOT NULL,branch TEXT,available INTEGER NOT NULL DEFAULT 1,incarnation TEXT NOT NULL,
 UNIQUE(project_id,path,incarnation));
CREATE TABLE sessions(id TEXT PRIMARY KEY,host TEXT NOT NULL,workspace_id TEXT REFERENCES workspaces(id),
 title TEXT NOT NULL,origin TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
 archived REAL,deleted REAL,revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE runtimes(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),epoch TEXT NOT NULL,
 physical_key TEXT NOT NULL,name TEXT NOT NULL,state TEXT NOT NULL,started REAL NOT NULL,ended REAL,
 observed REAL NOT NULL,detail TEXT NOT NULL,UNIQUE(epoch,physical_key));
CREATE UNIQUE INDEX active_runtime ON runtimes(session_id) WHERE ended IS NULL;
CREATE TABLE pane_executions(id TEXT PRIMARY KEY,runtime_id TEXT NOT NULL REFERENCES runtimes(id),
 physical_key TEXT NOT NULL,detail TEXT NOT NULL,UNIQUE(runtime_id,physical_key));
CREATE TABLE conversations(id TEXT PRIMARY KEY,host TEXT NOT NULL,provider TEXT NOT NULL,scope TEXT NOT NULL,
 native_id TEXT NOT NULL,workspace_id TEXT REFERENCES workspaces(id),title TEXT NOT NULL DEFAULT '',
 cwd TEXT NOT NULL DEFAULT '',parent_native_id TEXT,created REAL NOT NULL,updated REAL NOT NULL,
 archived REAL,deleted REAL,state TEXT NOT NULL DEFAULT 'collecting',detail TEXT NOT NULL DEFAULT '{}',
 UNIQUE(host,provider,scope,native_id));
CREATE INDEX conversations_updated ON conversations(updated,id);
CREATE TABLE conversation_executions(conversation_id TEXT REFERENCES conversations(id),
 runtime_id TEXT REFERENCES runtimes(id),pane_id TEXT NOT NULL,verified INTEGER NOT NULL,
 PRIMARY KEY(conversation_id,runtime_id,pane_id));
CREATE TABLE native_children(id TEXT PRIMARY KEY,conversation_id TEXT REFERENCES conversations(id),
 execution TEXT NOT NULL,native_id TEXT NOT NULL,detail TEXT NOT NULL,observed REAL NOT NULL,
 UNIQUE(conversation_id,execution,native_id));
CREATE TABLE relations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,parent_id TEXT NOT NULL,child_id TEXT NOT NULL,
 source_id TEXT NOT NULL,detail TEXT NOT NULL,UNIQUE(kind,source_id),CHECK(parent_id!=child_id));
CREATE TABLE sources(id TEXT PRIMARY KEY,provider TEXT NOT NULL,root TEXT NOT NULL,scope TEXT NOT NULL,
 enabled INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL,UNIQUE(provider,root));
CREATE TABLE source_files(id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources(id),relative_path TEXT NOT NULL,
 conversation_id TEXT REFERENCES conversations(id),generation TEXT NOT NULL,offset INTEGER NOT NULL,
 parsed_offset INTEGER NOT NULL DEFAULT 0,fingerprint TEXT NOT NULL,state TEXT NOT NULL,seen REAL NOT NULL,
 UNIQUE(source_id,relative_path));
CREATE TABLE manifests(id TEXT PRIMARY KEY,conversation_id TEXT REFERENCES conversations(id),file_id TEXT NOT NULL,
 generation TEXT NOT NULL,length INTEGER NOT NULL,digest TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL);
CREATE INDEX manifests_file ON manifests(file_id,created,id);
CREATE TABLE messages(id TEXT PRIMARY KEY,conversation_id TEXT REFERENCES conversations(id),generation TEXT NOT NULL,
 position INTEGER NOT NULL,role TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL,
 UNIQUE(conversation_id,generation,position,role));
CREATE INDEX messages_conversation ON messages(conversation_id,created,position);
CREATE TABLE operations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,resource TEXT NOT NULL,fingerprint TEXT NOT NULL,
 state TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE change_log(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,resource TEXT NOT NULL,
 created REAL NOT NULL);
CREATE TABLE replica_checkpoints(peer TEXT PRIMARY KEY,seq INTEGER NOT NULL,updated REAL NOT NULL);
CREATE TABLE replication_grants(peer TEXT PRIMARY KEY,direction TEXT NOT NULL,public_key TEXT NOT NULL,
 enabled INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL);
CREATE TABLE replicas(origin TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
 received REAL NOT NULL,PRIMARY KEY(origin,kind,id));
CREATE TABLE deletion_tombstones(kind TEXT NOT NULL,resource TEXT NOT NULL,created REAL NOT NULL,
 PRIMARY KEY(kind,resource));
"""


@contextmanager
def database(*, write=False):
    root = directory()
    path = root / "catalog.sqlite3"
    private_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists() or companion.is_symlink():
            private_file(companion)
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        lock_path = root / "migration.lock"
        private_file(lock_path)
        with open(lock_path, "rb") as migration_lock:
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(migration_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Refused("store_unavailable", 503)
                    time.sleep(.01)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise Refused("unsupported_store_version", 503)
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise Refused("unrecognized_store", 503)
                db.executescript("BEGIN IMMEDIATE;" + DDL + f"PRAGMA user_version={SCHEMA_VERSION}; COMMIT;")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        if write:
            db.execute("INSERT OR IGNORE INTO meta VALUES('host',?)", (uid(),))
        yield db
        db.commit()
    except (sqlite3.Error, OSError) as exc:
        db.rollback()
        raise Refused("store_unavailable", 503) from exc
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def host_id(db):
    row = db.execute("SELECT value FROM meta WHERE key='host'").fetchone()
    if not row:
        raise Refused("store_uninitialized", 503)
    return row[0]


def changed(db, kind, resource):
    db.execute("INSERT INTO change_log(kind,resource,created) VALUES(?,?,?)", (kind, resource, time.time()))


def setting(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(db, key, value):
    db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))


def page(limit, after=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise Refused("invalid_limit", 400)
    if after is not None:
        identifier(after)
    return limit


# 세션에 이어 붙일 대화: 이 세션의 실행에 묶였고, 식별자가 검증됐고, 에이전트 내부 자식이 아닌 것 중 최신.
_RESUME_PICK = """SELECT c.id FROM conversations c JOIN conversation_executions e ON e.conversation_id=c.id
  JOIN runtimes r ON r.id=e.runtime_id WHERE r.session_id={sid} AND c.deleted IS NULL
  AND c.parent_native_id IS NULL AND json_extract(c.detail,'$.native_identity_verified')
  ORDER BY c.updated DESC,c.id LIMIT 1"""


def resume_target(db, sid):
    row = db.execute(_RESUME_PICK.format(sid="?"), (sid,)).fetchone()
    return row[0] if row else None


def snapshot(*, limit=50, after=None, archived=False):
    page(limit, after)
    import repo_store
    hidden = repo_store.hidden_set()
    with database(write=True) as db:
        host = host_id(db)
        rows = db.execute("""SELECT s.*,r.id AS runtime_id,r.name AS tmux_name,r.state AS execution_state,
          r.epoch,r.physical_key,r.detail AS runtime_detail,r.ended,r.observed,
          CASE WHEN r.ended IS NOT NULL AND s.archived IS NULL THEN (""" + _RESUME_PICK.format(sid="s.id") + """) END AS resume_conversation_id
          FROM sessions s LEFT JOIN runtimes r ON r.id=(SELECT id FROM runtimes WHERE session_id=s.id
          ORDER BY started DESC,id DESC LIMIT 1) WHERE s.deleted IS NULL AND (s.archived IS NOT NULL)=?
          AND (? IS NULL OR s.id>?) ORDER BY s.id LIMIT ?""", (int(archived), after, after, limit + 1)).fetchall()
        return {"host": host, "revision": db.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0],
                "projects": [{**dict(r), "hidden": repo_store.is_hidden({"path":r['path']},hidden)} for r in db.execute("SELECT * FROM projects ORDER BY name,id")],
                "workspaces": [dict(r) for r in db.execute("SELECT * FROM workspaces ORDER BY path,id")],
                "sessions": [{**dict(r), "runtime_detail": json.loads(r["runtime_detail"] or "{}"),
                              "execution_state": r["execution_state"] or "never_started"} for r in rows[:limit]],
                "relations": [dict(r) for r in db.execute("SELECT * FROM relations WHERE kind='delegation' LIMIT 5000")],
                "next": rows[limit-1]["id"] if len(rows)>limit else None}


def edit_session(sid, revision, *, title=None, archived=None):
    identifier(sid)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Refused("invalid_revision", 400)
    if title is not None and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 128
                              or any(ord(c) < 32 for c in title)):
        raise Refused("invalid_title", 400)
    if archived is not None and not isinstance(archived, bool):
        raise Refused("invalid_archive", 400)
    with database(write=True) as db:
        row = db.execute("SELECT * FROM sessions WHERE id=? AND deleted IS NULL", (sid,)).fetchone()
        if not row:
            raise Refused("session_not_found", 404)
        if row["revision"] != revision:
            raise Refused("revision_conflict")
        if archived and db.execute("SELECT 1 FROM runtimes WHERE session_id=? AND ended IS NULL", (sid,)).fetchone():
            raise Refused("runtime_must_stop")
        db.execute("UPDATE sessions SET title=?,archived=?,updated=?,revision=revision+1 WHERE id=?",
                   (title.strip() if title is not None else row["title"],
                    (time.time() if archived else None) if archived is not None else row["archived"], time.time(), sid))
        changed(db, "session", sid)
        return dict(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())


def session_detail(sid):
    identifier(sid)
    with database() as db:
        row = db.execute('SELECT * FROM sessions WHERE id=? AND deleted IS NULL', (sid,)).fetchone()
        if not row:
            raise Refused('session_not_found',404)
        runtimes = [dict(r) for r in db.execute('SELECT * FROM runtimes WHERE session_id=? ORDER BY started DESC LIMIT 50',(sid,))]
        if row['archived'] is not None:
            reason = 'archived'
        elif any(r['ended'] is None for r in runtimes):
            reason = 'running'
        else:
            reason = None if resume_target(db, sid) else 'no_conversation'
        return {'session':dict(row), 'runtimes':runtimes,
                'resumable':{'ok':reason is None,'reason':reason},
                'conversations':[dict(r) for r in db.execute('SELECT DISTINCT c.id,c.title,c.native_id,c.provider,c.updated FROM conversations c JOIN conversation_executions e ON e.conversation_id=c.id JOIN runtimes r ON r.id=e.runtime_id WHERE r.session_id=? AND c.deleted IS NULL ORDER BY c.updated DESC',(sid,))],
                'relations':[dict(r) for r in db.execute('SELECT * FROM relations WHERE parent_id=? OR child_id=?',(sid,sid))]}
