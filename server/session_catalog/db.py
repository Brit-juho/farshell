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

SCHEMA_VERSION = 2
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
    """Create the file 0600 if missing; refuse anything but a private regular file of ours.

    An existing file is checked with lstat and never opened here. POSIX locks belong to the process,
    and closing *any* descriptor of a file drops all of them, so the old open/close on every call
    silently released SQLite's locks held by other live connections in this process (other threads,
    or a long-lived connection). A descriptor is opened only to create the file or to fix a wrong
    mode, both rare. A chmod is skipped when the mode is already right: each one posts an FSEvents
    change, and this runs for the database and each companion on every connection.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600))
        except FileExistsError:
            pass  # created concurrently; checked below like any existing file
        st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
        raise Refused("unsafe_store", 503)
    if stat.S_IMODE(st.st_mode) != 0o600:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            again = os.fstat(fd)
            if (again.st_dev, again.st_ino) != (st.st_dev, st.st_ino) or again.st_nlink != 1:
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
CREATE INDEX manifests_conversation ON manifests(conversation_id,created);
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

# 저장소 형식 올리기(마이그레이션). 새로 만드는 저장소도 「v1 DDL → 차례로 올리기」 같은 길을 밟는다 — 그래야 백업 복원이
# 비교하는 `sqlite_master`의 SQL 문장이 새 설치와 올린 저장소에서 글자까지 같다(ALTER로 더한 열은 CREATE TABLE 문장에
# 그대로 덧붙는다). 버전 N의 목록은 N-1에서 N으로 올리는 문장들이고, 한 트랜잭션 안에서 실행한다.
MIGRATIONS = {
    2: (
        # 목록 정렬·자동 보관의 기준 「마지막 활동」(종료 감지 시각이 아니라 작업 시각), 보이는 이름(별칭)의 출처.
        "ALTER TABLE sessions ADD COLUMN last_activity REAL",
        "ALTER TABLE sessions ADD COLUMN name_source TEXT NOT NULL DEFAULT 'auto'",
        # 끝날 때의 브랜치, 이어서 시작에 쓸 CLI 실행 파일과 그 PATH, 「종료로 표시」(상태를 읽지 못한 런타임을 사람이 정리).
        "ALTER TABLE runtimes ADD COLUMN branch_at_end TEXT",
        "ALTER TABLE runtimes ADD COLUMN cli_path TEXT",
        "ALTER TABLE runtimes ADD COLUMN cli_env_path TEXT",
        "ALTER TABLE runtimes ADD COLUMN dismissed_at REAL",
        "CREATE INDEX sessions_activity ON sessions(archived,deleted,last_activity,id)",
        "CREATE INDEX runtimes_session ON runtimes(session_id,started)",
        "CREATE INDEX conversation_executions_runtime ON conversation_executions(runtime_id)",
        # 이 맥에서만 쓰는 대화 요약(마지막 사용자 지시 등). 복제 대상이 아니다 — 복제는 정해진 표만 보낸다.
        "CREATE TABLE conversation_local(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),"
        " last_user_text TEXT NOT NULL DEFAULT '',user_message_count INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL DEFAULT 0)",
        # 기존 행 채우기. 마지막 활동 = 런타임의 종료·관측·시작 중 가장 늦은 시각, 없으면 세션 갱신·생성 시각.
        "UPDATE sessions SET last_activity=COALESCE((SELECT MAX(MAX(COALESCE(r.ended,0),r.observed,r.started))"
        " FROM runtimes r WHERE r.session_id=sessions.id),updated,created)",
        # 보이는 이름(title)과 출처. 예전에는 title이 생성 때 한 번 정해지고 tmux 이름 변경을 따라가지 않아, 종료된
        # 세션에 처음 이름(`server`, `fsh-<32자리>`)이 남았다. 그 옛 자동 이름 모양과 마지막 tmux 이름과 같은 값은
        # 자동(auto)으로, 그 밖에 마지막 tmux 이름과 다르고 연결된 대화의 제목·id도 아닌 값은 사람이 붙인 이름(human)으로 본다.
        "UPDATE sessions SET name_source='human' WHERE title!=COALESCE((SELECT r.name FROM runtimes r"
        " WHERE r.session_id=sessions.id ORDER BY r.started DESC LIMIT 1),title)"
        " AND NOT (title GLOB 'server' OR title GLOB 'server-[0-9]*' OR (title GLOB 'fsh-*' AND length(title)=36))"
        " AND NOT EXISTS(SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
        " JOIN conversations c ON c.id=e.conversation_id WHERE r.session_id=sessions.id"
        " AND (c.title=sessions.title OR c.native_id=sessions.title))",
        # 자동 이름은 마지막 tmux 이름을 보여 준다(사람이 터미널에서 바꾼 이름). 단 옛 `fsh-<32자리>`는 보이는 이름으로 쓰지 않는다.
        "UPDATE sessions SET title=(SELECT r.name FROM runtimes r WHERE r.session_id=sessions.id ORDER BY r.started DESC LIMIT 1)"
        " WHERE name_source='auto' AND EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=sessions.id)"
        " AND (SELECT r.name FROM runtimes r WHERE r.session_id=sessions.id ORDER BY r.started DESC LIMIT 1) NOT GLOB 'fsh-*'"
        " AND NOT EXISTS(SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
        " JOIN conversations c ON c.id=e.conversation_id WHERE r.session_id=sessions.id"
        " AND (c.title=sessions.title OR c.native_id=sessions.title))",
    ),
}


def migrate(db, from_version):
    """`from_version`에서 SCHEMA_VERSION까지 올린다. 호출부가 트랜잭션을 연다."""
    for version in range(from_version + 1, SCHEMA_VERSION + 1):
        for statement in MIGRATIONS.get(version, ()):
            db.execute(statement)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def expected_schema():
    """지금 버전 저장소의 `sqlite_master`(type,name,sql) — 백업 복원 검사가 비교한다."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.executescript(DDL)
        probe.execute("BEGIN")
        migrate(probe, 1)
        probe.commit()
        return list(probe.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
    finally:
        probe.close()


def _backup_before_migration(db, path: Path, version: int):
    """올리기 전 사본(`catalog.sqlite3.v<N>.bak`, 0600). SQLite backup API라 WAL까지 일관된 사본이다.
    이미 있으면 덮지 않는다(첫 사본이 원본에 가장 가깝다). 되돌리는 법은 docs/ref/dev.md."""
    copy = path.with_name(f"{path.name}.v{version}.bak")
    if copy.exists() or copy.is_symlink():
        return
    fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    target = sqlite3.connect(copy)
    try:
        db.backup(target)
    finally:
        target.close()
    os.chmod(copy, 0o600)


_anchor = None


def hold_wal():
    """Keep one idle connection open while the server runs.

    Every `database()` call opens and closes its own connection. When the last connection to a WAL
    database closes, SQLite checkpoints into the main file and deletes `-wal`, so the next call
    recreates it: a checkpoint into the whole catalog plus file churn many times a second. An idle
    connection that has read once holds the WAL open; checkpoints then happen only by the normal
    auto-checkpoint. It holds no transaction, so it never blocks writers or checkpoints. This relies on
    private_file never closing a descriptor of an existing store file (that would drop its locks).
    """
    global _anchor
    if _anchor is not None:
        return
    with database():
        pass  # creates or validates the store and puts it in WAL mode first
    anchor = sqlite3.connect(directory() / "catalog.sqlite3", timeout=2, check_same_thread=False)
    try:
        anchor.execute("SELECT 1 FROM meta LIMIT 1").fetchall()
    except sqlite3.Error:
        anchor.close()
        raise
    _anchor = anchor


def release_wal():
    global _anchor
    anchor, _anchor = _anchor, None
    if anchor is not None:
        anchor.close()


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
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise Refused("unrecognized_store", 503)
                db.executescript("BEGIN IMMEDIATE;" + DDL + "PRAGMA user_version=1; COMMIT;")
                version = 1
            if version < 1 or version > SCHEMA_VERSION:
                # 더 새 버전이 만든 저장소(되돌린 뒤 실행)는 건드리지 않는다.
                raise Refused("unsupported_store_version", 503)
            if version < SCHEMA_VERSION:
                if db.execute("SELECT 1 FROM sessions LIMIT 1").fetchone():
                    _backup_before_migration(db, path, version)
                db.execute("BEGIN IMMEDIATE")
                try:
                    migrate(db, version)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        # With the WAL kept open (hold_wal) it is reused instead of deleted, so a large batch would
        # otherwise leave it at its peak size; cut it back after each checkpoint that resets it.
        db.execute("PRAGMA journal_size_limit=33554432")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        if write:
            db.execute("INSERT OR IGNORE INTO meta VALUES('host',?)", (uid(),))
            # 대화 목록의 저장 시각(MAX(created))이 이 인덱스 없이는 대화마다 manifests를 훑어 수 초가 걸려
            # 목록 쿼리의 1초 제한에 걸린다. 이미 만들어진 저장소에도 첫 쓰기 연결에서 한 번 만든다.
            db.execute("CREATE INDEX IF NOT EXISTS manifests_conversation ON manifests(conversation_id,created)")
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
                "auto_archive_days": auto_archive_days(db),
                "archived_count": db.execute("SELECT COUNT(*) FROM sessions WHERE deleted IS NULL AND archived IS NOT NULL").fetchone()[0],
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


AUTO_ARCHIVE_DEFAULT_DAYS = 30
AUTO_ARCHIVE_MAX_DAYS = 3650


def auto_archive_days(db):
    value = setting(db, "auto_archive_days", AUTO_ARCHIVE_DEFAULT_DAYS)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= AUTO_ARCHIVE_MAX_DAYS else AUTO_ARCHIVE_DEFAULT_DAYS


def configure(values):
    """레일 설정에서 바꾸는 서버 쪽 값. 0이면 자동 보관을 끈다."""
    days = values.get("auto_archive_days")
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= AUTO_ARCHIVE_MAX_DAYS:
        raise Refused("invalid_auto_archive_days", 400)
    with database(write=True) as db:
        set_setting(db, "auto_archive_days", days)
        return {"auto_archive_days": days}


def auto_archive(now=None):
    """종료 후 N일이 지난 세션을 보관한다. 삭제하지 않는다.

    실행 중(ended IS NULL)이거나 상태를 확인하지 못한 세션, 실행 기록이 없는 세션은 건드리지 않는다.
    """
    now = time.time() if now is None else now
    with database(write=True) as db:
        days = auto_archive_days(db)
        if not days:
            return 0
        cutoff = now - days * 86400
        rows = db.execute("""SELECT s.id FROM sessions s WHERE s.deleted IS NULL AND s.archived IS NULL
          AND EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=s.id)
          AND NOT EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=s.id AND r.ended IS NULL)
          AND (SELECT MAX(r.ended) FROM runtimes r WHERE r.session_id=s.id)<?""", (cutoff,)).fetchall()
        for row in rows:
            db.execute("UPDATE sessions SET archived=?,updated=?,revision=revision+1 WHERE id=?", (now, now, row["id"]))
            changed(db, "session", row["id"])
        return len(rows)


def delete_session(sid, revision):
    """보관함의 세션 기록 한 줄만 지운다. 대화와 원본 사본은 남는다. 위임 관계가 있으면 막는다."""
    identifier(sid)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Refused("invalid_revision", 400)
    with database(write=True) as db:
        row = db.execute("SELECT * FROM sessions WHERE id=? AND deleted IS NULL", (sid,)).fetchone()
        if not row:
            raise Refused("session_not_found", 404)
        if row["revision"] != revision:
            raise Refused("revision_conflict")
        if row["archived"] is None:
            raise Refused("archive_first")
        if db.execute("SELECT 1 FROM runtimes WHERE session_id=? AND ended IS NULL", (sid,)).fetchone():
            raise Refused("runtime_must_stop")
        if db.execute("SELECT 1 FROM relations WHERE parent_id=? OR child_id=?", (sid, sid)).fetchone():
            raise Refused("session_has_relations")
        now = time.time()
        db.execute("UPDATE sessions SET deleted=?,updated=?,revision=revision+1 WHERE id=?", (now, now, sid))
        changed(db, "session", sid)
        return {"id": sid, "deleted": now}


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
