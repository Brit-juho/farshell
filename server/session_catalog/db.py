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

SCHEMA_VERSION = 3
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
    3: (
        # 저장소 = CLI를 켠 곳(docs/ref/session-ui.md 「Projects」). registered: 처음 CLI 대화가 시작된 시각(NULL이면 스캔·터미널이
        # 만든 내부 행 — 목록의 저장소가 아니다). removed: 설정에서 제거한 시각(그 뒤 그 자리에서 CLI를 켜면 다시 NULL).
        # last_activity: 이 저장소 대화의 마지막 활동. hidden은 0 보임 · 1 사람이 숨김 · 2 오래돼 자동 숨김(새 활동이면 풀린다).
        "ALTER TABLE projects ADD COLUMN registered REAL",
        "ALTER TABLE projects ADD COLUMN removed REAL",
        "ALTER TABLE projects ADD COLUMN last_activity REAL",
        # 대화를 저장소에 놓은 결과(session_catalog/placement.py): NULL 아직 · 'placed' · 'skip:<이유>'. 색인이 「아직」만 빨리 찾는다.
        "ALTER TABLE conversations ADD COLUMN placement TEXT",
        "CREATE INDEX conversations_workspace ON conversations(workspace_id,updated)",
        "CREATE INDEX conversations_placement ON conversations(placement,updated)",
        # 보관함은 대화 단위가 된다(저장소 2차) — 이미 보관한 세션에 이어진 대화를 보관으로 옮긴다. 그 대화가 보관 안 한 다른 세션에도
        # 이어져 있으면(같은 대화를 다른 세션에서 이어 씀) 옮기지 않는다.
        "UPDATE conversations SET archived=(SELECT MAX(s.archived) FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
        " JOIN sessions s ON s.id=r.session_id WHERE e.conversation_id=conversations.id)"
        " WHERE archived IS NULL AND EXISTS(SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
        " JOIN sessions s ON s.id=r.session_id WHERE e.conversation_id=conversations.id AND s.archived IS NOT NULL)"
        " AND NOT EXISTS(SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
        " JOIN sessions s ON s.id=r.session_id WHERE e.conversation_id=conversations.id AND s.archived IS NULL)",
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


CURSOR = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?):([a-f0-9]{32})")


def touch_activity(db, sid, ts):
    """세션의 마지막 활동 시각을 앞으로만 옮긴다(목록 정렬·자동 보관의 기준)."""
    if ts:
        db.execute("UPDATE sessions SET last_activity=? WHERE id=? AND (last_activity IS NULL OR last_activity<?)", (ts, sid, ts))


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# 세션에 이어 붙일 대화: 이 세션의 실행에 묶였고, 식별자가 검증됐고, 에이전트 내부 자식이 아닌 것 중 최신.
_RESUME_PICK = """SELECT c.id FROM conversations c JOIN conversation_executions e ON e.conversation_id=c.id
  JOIN runtimes r ON r.id=e.runtime_id WHERE r.session_id={sid} AND c.deleted IS NULL
  AND c.parent_native_id IS NULL AND json_extract(c.detail,'$.native_identity_verified')
  ORDER BY c.updated DESC,c.id LIMIT 1"""


def resume_target(db, sid):
    row = db.execute(_RESUME_PICK.format(sid="?"), (sid,)).fetchone()
    return row[0] if row else None


def snapshot(*, limit=50, after=None, archived=False, order="id", cursor=None, q=None, project=None, meta=True, local=True):
    """세션 목록 한 쪽.

    - `order="id"`(옛 방식): id 순, `after` 커서. 지금 화면이 쓴다 — 하위 호환으로 남긴다.
    - `order="activity"`: 마지막 활동 최신순, `cursor`(`<시각>:<id>`) 키셋. 200개 상한 밖의 오래된 세션도 쪽을 넘겨 닿는다.
    - `q`: 이름·대화 제목·브랜치·마지막 지시 글자 검색. `project`: 저장소(project id), `_none`이면 저장소 밖.
    - `meta=False`: projects·workspaces·relations를 싣지 않는다(목록만 자주 읽을 때).
    - 행마다 이름(`name`, 마지막 tmux 이름), 보이는 이름(`title`, 별칭)과 출처, 대화(CLI 종류·제목·수), 끝날 때 브랜치.
      마지막 지시(`last_user_text`)는 `local`일 때만(이 맥의 사람 화면 — 이 라우트는 중계되지 않는다).
    """
    if order not in ("id", "activity"):
        raise Refused("invalid_order", 400)
    page(limit, after if order == "id" else None)
    if cursor is not None and (order != "activity" or not isinstance(cursor, str) or not CURSOR.fullmatch(cursor)):
        raise Refused("invalid_cursor", 400)
    if q is not None and (not isinstance(q, str) or len(q) > 200):
        raise Refused("invalid_query", 400)
    if project is not None and project != "_none" and not ID.fullmatch(str(project)):
        raise Refused("invalid_project", 400)
    import repo_store
    hidden = repo_store.hidden_set() if meta else None
    with database() as db:
        host_row = db.execute("SELECT value FROM meta WHERE key='host'").fetchone()
    if not host_row:
        with database(write=True) as db:   # 빈 새 저장소 — 첫 쓰기 연결이 host를 만든다
            pass
    with database() as db:
        host = host_id(db)
        where, args = ["s.deleted IS NULL", "(s.archived IS NOT NULL)=?"], [int(archived)]
        if project == "_none":
            where.append("(w.project_id IS NULL OR EXISTS(SELECT 1 FROM projects p WHERE p.id=w.project_id AND p.kind='directory'))")
        elif project:
            where.append("w.project_id=?"); args.append(project)
        if q and q.strip():
            like = _like(q.strip())
            # w.path: 작업 트리 폴더(저장소 루트·워크트리 경로) — 「어느 폴더에서 한 세션」으로도 찾는다.
            fields = ["s.title", "r.name", "lc.title", "r.branch_at_end", "w.branch", "w.path"] + (["cl.last_user_text"] if local else [])
            where.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")"); args += [like] * len(fields)
        if order == "id":
            where.append("(? IS NULL OR s.id>?)"); args += [after, after]
            order_sql = "s.id"
        else:
            if cursor:
                ts, cid = CURSOR.fullmatch(cursor).groups()
                where.append("(COALESCE(s.last_activity,0)<? OR (COALESCE(s.last_activity,0)=? AND s.id<?))"); args += [float(ts), float(ts), cid]
            order_sql = "COALESCE(s.last_activity,0) DESC,s.id DESC"
        rows = db.execute("""SELECT s.*,r.id AS runtime_id,r.name AS tmux_name,r.name AS name,r.state AS execution_state,
          r.epoch,r.physical_key,r.detail AS runtime_detail,r.ended,r.observed,r.branch_at_end,r.dismissed_at,
          CASE WHEN (r.ended IS NOT NULL OR r.dismissed_at IS NOT NULL) AND s.archived IS NULL THEN (""" + _RESUME_PICK.format(sid="s.id") + """) END AS resume_conversation_id,
          lc.id AS conversation_id,lc.title AS conversation_title,lc.provider AS provider,cl.last_user_text,
          (SELECT COUNT(DISTINCT e.conversation_id) FROM conversation_executions e JOIN runtimes rr ON rr.id=e.runtime_id
           WHERE rr.session_id=s.id) AS conversation_count
          FROM sessions s LEFT JOIN runtimes r ON r.id=(SELECT id FROM runtimes WHERE session_id=s.id
          ORDER BY started DESC,id DESC LIMIT 1)
          LEFT JOIN conversations lc ON lc.id=(SELECT c.id FROM conversations c JOIN conversation_executions e ON e.conversation_id=c.id
           JOIN runtimes rr ON rr.id=e.runtime_id WHERE rr.session_id=s.id AND c.deleted IS NULL AND c.parent_native_id IS NULL
           ORDER BY c.updated DESC,c.id LIMIT 1)
          LEFT JOIN conversation_local cl ON cl.conversation_id=lc.id
          LEFT JOIN workspaces w ON w.id=s.workspace_id
          WHERE """ + " AND ".join(where) + " ORDER BY " + order_sql + " LIMIT ?", (*args, limit + 1)).fetchall()
        sessions = []
        for r in rows[:limit]:
            item = {**dict(r), "runtime_detail": json.loads(r["runtime_detail"] or "{}"),
                    "execution_state": "dismissed" if r["dismissed_at"] is not None else (r["execution_state"] or "never_started")}
            if not local:
                item.pop("last_user_text", None)
            sessions.append(item)
        if len(rows) > limit:
            last = rows[limit - 1]
            following = last["id"] if order == "id" else f"{last['last_activity'] or 0}:{last['id']}"
        else:
            following = None
        out = {"host": host, "revision": db.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0],
               "sessions": sessions, "auto_archive_days": auto_archive_days(db), "auto_name": auto_name_enabled(db),
               "resume_ends_session": resume_ends_session(db),
               "archived_count": db.execute("SELECT COUNT(*) FROM sessions WHERE deleted IS NULL AND archived IS NOT NULL").fetchone()[0],
               "next": following}
        if meta:
            out["projects"] = [{**dict(r), "hidden": repo_store.is_hidden({"path": r['path']}, hidden)} for r in db.execute("SELECT * FROM projects ORDER BY name,id")]
            out["workspaces"] = [dict(r) for r in db.execute("SELECT * FROM workspaces ORDER BY path,id")]
            out["relations"] = [dict(r) for r in db.execute("SELECT * FROM relations WHERE kind='delegation' LIMIT 5000")]
            # 저장소별 세션 수(이 목록과 같은 보관 여부) — 보관함의 저장소 칩. `_none`은 저장소 밖.
            out["project_counts"] = {(r[0] or "_none"): r[1] for r in db.execute(
                "SELECT CASE WHEN p.kind='git' THEN p.id END AS pid, COUNT(*) FROM sessions s LEFT JOIN workspaces w ON w.id=s.workspace_id"
                " LEFT JOIN projects p ON p.id=w.project_id WHERE s.deleted IS NULL AND (s.archived IS NOT NULL)=? GROUP BY pid",
                (int(archived),))}
        return out


def edit_session(sid, revision, *, title=None, archived=None, auto_name=None):
    """보이는 이름(별칭)·보관을 바꾼다. 이름을 주면 사람이 붙인 이름(`human`)이 되어 자동 이름이 멈춘다.
    `auto_name=True`는 자동으로 되돌린다(보이는 이름 = 마지막 tmux 이름)."""
    identifier(sid)
    if auto_name is not None and auto_name is not True:
        raise Refused("invalid_auto_name", 400)
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
        name_source = row["name_source"]
        new_title = row["title"]
        if title is not None:
            new_title, name_source = title.strip(), "human"
        if auto_name:
            latest = db.execute("SELECT name FROM runtimes WHERE session_id=? ORDER BY started DESC,id DESC LIMIT 1", (sid,)).fetchone()
            new_title, name_source = (latest["name"] if latest else row["title"]), "auto"
        db.execute("UPDATE sessions SET title=?,name_source=?,archived=?,updated=?,revision=revision+1 WHERE id=?",
                   (new_title, name_source,
                    (time.time() if archived else None) if archived is not None else row["archived"], time.time(), sid))
        changed(db, "session", sid)
        return dict(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())


AUTO_ARCHIVE_DEFAULT_DAYS = 30
AUTO_ARCHIVE_MAX_DAYS = 3650


def auto_archive_days(db):
    value = setting(db, "auto_archive_days", AUTO_ARCHIVE_DEFAULT_DAYS)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= AUTO_ARCHIVE_MAX_DAYS else AUTO_ARCHIVE_DEFAULT_DAYS


def auto_name_enabled(db):
    """CLI를 켜면 자동 이름 세션의 보이는 이름을 그 저장소 이름으로 맞출지(이 맥 전체 설정, 기본 켬)."""
    return setting(db, "auto_name_on_cli", True) is not False


def resume_ends_session(db):
    """이어서 시작한 세션이 CLI가 끝나면 같이 끝날지(기본 켬). 끄면 예전처럼 셸을 띄우고 명령을 쳐 넣어, CLI가 끝나도
    셸이 남는다."""
    return setting(db, "resume_ends_session", True) is not False


def configure(values):
    """레일 설정에서 바꾸는 서버 쪽 값. 자동 보관 일수(0이면 끔), CLI 시작 때 자동 이름, 이어서 시작한 세션의 끝."""
    if not values:
        raise Refused("invalid_request", 400)
    with database(write=True) as db:
        if "auto_archive_days" in values:
            days = values.get("auto_archive_days")
            if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= AUTO_ARCHIVE_MAX_DAYS:
                raise Refused("invalid_auto_archive_days", 400)
            set_setting(db, "auto_archive_days", days)
        if "auto_name" in values:
            if not isinstance(values["auto_name"], bool):
                raise Refused("invalid_auto_name", 400)
            set_setting(db, "auto_name_on_cli", values["auto_name"])
        if "resume_ends_session" in values:
            if not isinstance(values["resume_ends_session"], bool):
                raise Refused("invalid_resume_ends_session", 400)
            set_setting(db, "resume_ends_session", values["resume_ends_session"])
        return {"auto_archive_days": auto_archive_days(db), "auto_name": auto_name_enabled(db),
                "resume_ends_session": resume_ends_session(db)}


ORPHAN_ARCHIVE_SECONDS = 86400


def auto_archive(now=None):
    """마지막 활동 뒤 N일이 지난, 실행 중이 아닌 세션과 저장소에 놓인 끝난 대화를 보관한다. 삭제하지 않는다.

    - 기준은 마지막 활동(`last_activity`)이다. 「종료로 표시」한 실행은 끝난 것으로 본다.
    - 실행 기록이 없는 세션(만들다 실패해 남은 행)은 만든 지 하루가 지나면 보관한다 — 예전에는 영영 남았다.
    - 상태를 확인하지 못한(`unknown`, 아직 끝나지 않은) 실행이 있는 세션은 건드리지 않는다.
    """
    now = time.time() if now is None else now
    with database(write=True) as db:
        days = auto_archive_days(db)
        if not days:
            return 0
        cutoff = now - days * 86400
        rows = db.execute("""SELECT s.id FROM sessions s WHERE s.deleted IS NULL AND s.archived IS NULL
          AND NOT EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=s.id AND r.ended IS NULL)
          AND ((EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=s.id) AND COALESCE(s.last_activity,s.updated,s.created)<?)
            OR (NOT EXISTS(SELECT 1 FROM runtimes r WHERE r.session_id=s.id) AND s.created<?))""",
                          (cutoff, now - ORPHAN_ARCHIVE_SECONDS)).fetchall()
        for row in rows:
            db.execute("UPDATE sessions SET archived=?,updated=?,revision=revision+1 WHERE id=?", (now, now, row["id"]))
            changed(db, "session", row["id"])
        # 보관함은 대화 단위(저장소 2차) — 저장소의 「종료 N」에 있는 끝난 대화도 마지막 활동(원본 기준) 뒤 N일이 지나면 보관한다.
        # 지금 열린 실행에 이어진 대화는 건드리지 않는다. 지우지 않는다(보관함·대화 보관함에서 다시 볼 수 있다).
        conversations = db.execute("""SELECT c.id FROM conversations c WHERE c.placement='placed' AND c.deleted IS NULL
          AND c.archived IS NULL AND c.parent_native_id IS NULL
          AND COALESCE(CAST(json_extract(c.detail,'$.last') AS REAL),c.updated)<?
          AND NOT EXISTS(SELECT 1 FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id
                         WHERE e.conversation_id=c.id AND r.ended IS NULL)""", (cutoff,)).fetchall()
        for row in conversations:
            db.execute("UPDATE conversations SET archived=? WHERE id=?", (now, row["id"]))
            changed(db, "conversation", row["id"])
        return len(rows) + len(conversations)


def record_cli(physical_key, path, env_path):
    """그 tmux 세션에서 지금 도는 실행(런타임)에 CLI 실행 파일 경로와 PATH를 적는다(이어서 시작이 쓴다)."""
    if not isinstance(physical_key, str) or not physical_key:
        return False
    with database(write=True) as db:
        # 값이 같아도 맞는 행이 있으면 성공이다(SQLite는 값이 그대로여도 맞은 행을 센다). 0이면 아직 런타임 행이 없다.
        cur = db.execute("UPDATE runtimes SET cli_path=?,cli_env_path=? WHERE physical_key=? AND ended IS NULL",
                         (path, env_path, physical_key))
        return cur.rowcount > 0


def homes(physical_keys):
    """실행 중인 tmux 세션(정체성 키) → 카탈로그가 아는 그 세션의 자리와 보이는 이름. 세션 목록(`/api/tmux/sessions`)이
    붙인다. 자리(home)는 지금 위치의 저장소다 — CLI가 돌면 CLI 위치, 아니면 셸 위치를 같은 자리 10초 뒤 따른다(inventory.py).
    아직 기록되지 않은 세션(방금 만듦)은 빠진다 — 호출자는 그때 지금 위치(context)로 그린다."""
    keys = [k for k in dict.fromkeys(physical_keys) if isinstance(k, str) and k]
    if not keys:
        return {}
    out = {}
    with database() as db:
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            rows = db.execute(
                "SELECT r.physical_key, s.id AS sid, s.title, s.name_source, s.workspace_id, w.path AS wpath, w.kind AS wkind,"
                " p.id AS pid, p.name AS pname, p.path AS ppath, p.kind AS pkind, p.registered AS preg, p.removed AS prem,"
                " p.hidden AS phid FROM runtimes r JOIN sessions s ON s.id=r.session_id"
                " LEFT JOIN workspaces w ON w.id=s.workspace_id LEFT JOIN projects p ON p.id=w.project_id"
                f" WHERE r.ended IS NULL AND s.deleted IS NULL AND r.physical_key IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for r in rows:
                home = None
                if r["workspace_id"] and r["pid"]:
                    home = {"project_id": r["pid"], "workspace_id": r["workspace_id"], "root": r["ppath"], "name": r["pname"],
                            "repository": r["pkind"] == "git", "kind": r["wkind"], "path": r["wpath"],
                            # 목록의 저장소인가(CLI를 켠 곳으로 등록됨·제거 안 됨)와 숨김 — 아니면 화면이 「저장소 밖」 폴더 가지로 그린다.
                            "registered": r["preg"] is not None and r["prem"] is None, "hidden": bool(r["phid"])}
                out[r["physical_key"]] = {"catalog_id": r["sid"], "title": r["title"], "name_source": r["name_source"], "home": home}
    return out


UNKNOWN_DISMISS_SECONDS = 600


def dismiss_runtime(sid, revision):
    """상태를 10분 넘게 읽지 못한 실행을 사람이 「종료로 표시」한다. tmux는 건드리지 않는다.
    끝난 것으로 기록하되(`ended`, `dismissed_at`), 그 실행이 다시 관측되면 reconcile이 되살린다(충돌 아님)."""
    identifier(sid)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Refused("invalid_revision", 400)
    now = time.time()
    with database(write=True) as db:
        row = db.execute("SELECT * FROM sessions WHERE id=? AND deleted IS NULL", (sid,)).fetchone()
        if not row:
            raise Refused("session_not_found", 404)
        if row["revision"] != revision:
            raise Refused("revision_conflict")
        runtime = db.execute("SELECT * FROM runtimes WHERE session_id=? AND ended IS NULL", (sid,)).fetchone()
        if not runtime:
            raise Refused("runtime_not_running")
        if runtime["state"] != "unknown" or now - runtime["observed"] < UNKNOWN_DISMISS_SECONDS:
            raise Refused("runtime_state_known")
        db.execute("UPDATE runtimes SET state='stopped',ended=?,dismissed_at=? WHERE id=?", (now, now, runtime["id"]))
        db.execute("UPDATE sessions SET updated=?,revision=revision+1 WHERE id=?", (now, sid))
        touch_activity(db, sid, runtime["observed"])
        changed(db, "runtime", runtime["id"])
        return {"id": sid, "dismissed": now}


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
