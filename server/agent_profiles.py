"""Host-local profile registry. Native CLIs own credentials; metadata never does.

Profiles are logical launch targets, not authentication or OS isolation boundaries.
All mutations are reached through the elevated human router or the local CLI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import fsguard
import vt_paths
from i18n import tr

class Refused(ValueError):
    """A deliberate, localized message safe to return to the profile owner."""


MAX_PROFILES = 64
ID = re.compile(r"[a-f0-9]{32}")


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refused(tr("프로필 ID가 올바르지 않습니다"))
    return value


def directory() -> Path:
    return fsguard.private_profile_directory(vt_paths.state_dir() / "agent-profiles")


@contextmanager
def database():
    folder = directory()
    path = folder / "registry.sqlite3"
    if path.exists() or path.is_symlink():
        fsguard.profile_file(folder, "registry.sqlite3")
    # Never open/close an existing registry here: closing any descriptor drops every POSIX lock this
    # process holds on the file, including SQLite's locks of other live connections (session_catalog.db.private_file).
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600))
    except FileExistsError:
        pass
    if os.lstat(path).st_mode & 0o777 != 0o600:
        os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4, 5):
            raise Refused(tr("프로필 저장소 버전을 지원하지 않습니다"))
        db.executescript("""
          CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, tool TEXT NOT NULL, label TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, tool TEXT NOT NULL,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            revision INTEGER NOT NULL, created_at REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'ready');
          CREATE TABLE IF NOT EXISTS sessions (
            name TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id),
            created TEXT NOT NULL, profile_revision INTEGER NOT NULL,
            conversation TEXT, started_at REAL NOT NULL, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id),
            kind TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
            result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id),
            source_id TEXT NOT NULL, kind TEXT NOT NULL, manifest TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS refresh_tests (
            profile_id TEXT PRIMARY KEY REFERENCES profiles(id), subject TEXT NOT NULL,
            initial_fingerprint TEXT NOT NULL, mode TEXT NOT NULL,
            started_at REAL NOT NULL, completed_at REAL);
        """)
        if 'purpose' not in {r[1] for r in db.execute('PRAGMA table_info(profiles)')}:
            db.execute("ALTER TABLE profiles ADD COLUMN purpose TEXT NOT NULL DEFAULT 'work'")
        if 'process_identity' not in {r[1] for r in db.execute('PRAGMA table_info(sessions)')}:
            db.execute('ALTER TABLE sessions ADD COLUMN process_identity TEXT')
        columns = {r[1] for r in db.execute('PRAGMA table_info(profiles)')}
        if 'metadata_revision' not in columns:
            db.execute("ALTER TABLE profiles ADD COLUMN metadata_revision INTEGER NOT NULL DEFAULT 1")
        if 'archived_at' not in columns:
            db.execute("ALTER TABLE profiles ADD COLUMN archived_at REAL")
        session_columns = {r[1] for r in db.execute('PRAGMA table_info(sessions)')}
        for column in ('launch_auth_fingerprint', 'launch_subject'):
            if column not in session_columns:
                db.execute(f'ALTER TABLE sessions ADD COLUMN {column} TEXT')
        db.execute("PRAGMA user_version=5")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _public(row):
    return {key: row[key] for key in ("id", "name", "tool", "account_id", "revision", "created_at", "state", "purpose", "metadata_revision", "archived_at")}


def get(profile_id):
    with database() as db:
        row = db.execute("SELECT * FROM profiles WHERE id=?", (identifier(profile_id),)).fetchone()
        if row is None:
            raise Refused(tr("프로필을 찾지 못했습니다"))
        return _public(row)


def root(profile_id):
    profile = get(profile_id)
    return fsguard.private_profile_directory(directory() / profile["id"] / profile["tool"], create=False)


def context(profile_id, project=None):
    profile = get(profile_id)
    require_active(profile)
    home = vt_paths.user_home().resolve()
    return {"tool": profile["tool"], "root": str(root(profile_id)), "home": str(home),
            "project": project, "worktree": None, "session": None,
            "managed_profile": profile["id"], "profile_revision": profile["revision"]}


def listing():
    candidate = vt_paths.state_dir() / 'agent-profiles'
    if not candidate.exists() and not candidate.is_symlink():
        return {'profiles': [], 'archived_profiles': [], 'accounts': [], 'jobs': [], 'sessions': []}
    with database() as db:
        rows = [_public(row) for row in db.execute("SELECT * FROM profiles ORDER BY created_at,id")]
        accounts = [dict(row) | {"verification_state": "unverified"} for row in db.execute("SELECT * FROM accounts ORDER BY id")]
        jobs = [dict(row) for row in db.execute("SELECT id,profile_id,kind,state,created_at FROM jobs ORDER BY created_at DESC LIMIT 64")]
        sessions = [dict(row) for row in db.execute("SELECT name,profile_id,profile_revision,conversation,state FROM sessions ORDER BY started_at DESC LIMIT 64")]
    return {"profiles": [r for r in rows if r["archived_at"] is None],
            "archived_profiles": [r for r in rows if r["archived_at"] is not None],
            "accounts": accounts, "jobs": jobs, "sessions": sessions}


def create(name, tool, account_id=None, account_label=None, purpose="work"):
    if tool not in ("claude", "codex") or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise Refused(tr("프로필 이름 또는 도구가 올바르지 않습니다"))
    if account_label is not None and (not isinstance(account_label, str) or not 1 <= len(account_label.strip()) <= 80 or any(ord(c) < 32 for c in account_label)):
        raise Refused(tr("계정 이름이 올바르지 않습니다"))
    if purpose not in ("work", "test"):
        raise Refused(tr("프로필 용도가 올바르지 않습니다"))
    pid = secrets.token_hex(16)
    with database() as db:
        if db.execute("SELECT count(*) FROM profiles").fetchone()[0] >= MAX_PROFILES:
            raise Refused(tr("프로필 수가 한도를 넘었습니다"))
        if account_id:
            account = db.execute("SELECT * FROM accounts WHERE id=?", (identifier(account_id),)).fetchone()
            if not account or account["tool"] != tool:
                raise Refused(tr("계정과 도구가 일치하지 않습니다"))
        else:
            account_id = secrets.token_hex(16)
            db.execute("INSERT INTO accounts VALUES (?,?,?)", (account_id, tool, (account_label or name).strip()))
        base = directory()
        staging = fsguard.private_profile_directory(base / ('.new-' + pid))
        folder = fsguard.private_profile_directory(staging / tool)
        # Files are seeded only with FarShell lifecycle hooks. Native trust is
        # still decided by the CLI, never pre-approved by a database copy.
        import claude_hooks
        import codex_hooks
        import config_io
        if tool == "claude":
            data, _ = claude_hooks.apply_install({})
            config_io.atomic_write(folder / "settings.json", json.dumps(data, ensure_ascii=False, indent=2))
        else:
            text, _ = codex_hooks.apply_install("")
            config_io.atomic_write(folder / "config.toml", text)
        destination = fsguard.profile_file(base, pid)
        if destination.exists():
            raise Refused(tr("프로필 저장 경로가 이미 있습니다"))
        os.rename(staging, destination)
        db.execute("INSERT INTO profiles (id,name,tool,account_id,revision,created_at,state,purpose) VALUES (?,?,?,?,?,?,?,?)", (pid, name.strip(), tool, account_id, 1, time.time(), "ready", purpose))
    return get(pid)


def require_active(profile):
    if profile.get('archived_at') is not None:
        raise Refused(tr("보관된 프로필은 복원한 뒤 사용하세요"))


def require_revision(profile, revision):
    require_active(profile)
    if type(revision) is not int or revision != profile["revision"]:
        raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))


def bump(profile_id, revision, state="ready"):
    with database() as db:
        changed = db.execute("UPDATE profiles SET revision=revision+1,state=? WHERE id=? AND revision=?", (state, identifier(profile_id), revision))
        if changed.rowcount != 1:
            raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))


def job_begin(profile_id, kind):
    get(profile_id)
    jid = secrets.token_hex(16)
    with database() as db:
        pending = db.execute("SELECT id FROM jobs WHERE profile_id=? AND state='running'", (profile_id,)).fetchone()
        if pending:
            raise Refused(tr("이 프로필의 작업이 이미 실행 중입니다"))
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)", (jid, profile_id, kind, "running", time.time(), "{}"))
    return jid


def job_finish(jid, state, result):
    with database() as db:
        db.execute("UPDATE jobs SET state=?,result=? WHERE id=?", (state, json.dumps(result, ensure_ascii=False), identifier(jid)))


def job_status(jid):
    with database() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier(jid),)).fetchone()
        if row is None:
            raise Refused(tr("작업을 찾지 못했습니다"))
        return dict(row) | {"result": json.loads(row["result"])}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def complete_job(profile_id, revision, job_id, result, *, bump_revision=True, receipt=None, job_state="done"):
    """Commit a mutation's registry revision, receipt and job in one transaction."""
    value = dict(result)
    rid = secrets.token_hex(16) if receipt else None
    if rid:
        value['receipt'] = rid
    with database() as db:
        if bump_revision:
            cursor = db.execute("UPDATE profiles SET revision=revision+1,state='ready' WHERE id=? AND revision=?", (identifier(profile_id), revision))
            valid = cursor.rowcount == 1
        else:
            row = db.execute('SELECT revision FROM profiles WHERE id=?', (identifier(profile_id),)).fetchone()
            valid = row is not None and row['revision'] == revision
        if not valid:
            raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
        if receipt:
            source_id, kind, manifest, verified = receipt
            db.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?,?)', (rid, profile_id, source_id, kind, json.dumps(manifest), int(verified), time.time()))
        cursor = db.execute("UPDATE jobs SET state=?,result=? WHERE id=? AND profile_id=?", (job_state, json.dumps(value), identifier(job_id), profile_id))
        if cursor.rowcount != 1:
            raise Refused(tr("작업을 찾지 못했습니다"))
    return value


def require_no_pending_jobs(profile_id):
    with database() as db:
        pending = db.execute("SELECT 1 FROM jobs WHERE profile_id=? AND state IN ('running','partial','partial-rollback') LIMIT 1", (identifier(profile_id),)).fetchone()
    if pending:
        raise Refused(tr("프로필의 중단되거나 실행 중인 작업을 먼저 확인하세요"))


def adopt(profile_id, revision):
    """Keep the native root and grant; retire test-only expiry controls."""
    profile = get(profile_id)
    require_revision(profile, revision)
    require_no_pending_jobs(profile_id)
    if profile['purpose'] != 'test' or profile['state'] != 'ready':
        raise Refused(tr("준비된 테스트 프로필만 사용 프로필로 전환할 수 있습니다"))
    with database() as db:
        cursor = db.execute("UPDATE profiles SET purpose='work',revision=revision+1 WHERE id=? AND revision=? AND purpose='test' AND state='ready'", (profile_id, revision))
        if cursor.rowcount != 1:
            raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
    return get(profile_id)


def metadata_change(profile_id, revision, metadata_revision, operation, name=None):
    profile = get(profile_id)
    if type(revision) is not int or revision != profile['revision'] or type(metadata_revision) is not int or metadata_revision != profile['metadata_revision']:
        raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
    if operation not in ('rename', 'archive', 'restore'):
        raise Refused(tr("지원하지 않는 관리 작업입니다"))
    if operation != 'restore':
        require_active(profile)
    if operation == 'rename':
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise Refused(tr("프로필 이름 또는 도구가 올바르지 않습니다"))
    else:
        require_no_pending_jobs(profile_id)
        from agent_profile_runtime import require_inactive, validate_native_files
        require_inactive(profile_id)
        if profile['state'] != 'ready' or (operation == 'restore' and profile['archived_at'] is None):
            raise Refused(tr("프로필 복구를 먼저 완료하세요"))
        if operation == 'restore':
            validate_native_files(profile_id)
    with database() as db:
        column, value = ('name', name.strip()) if operation == 'rename' else ('archived_at', time.time() if operation == 'archive' else None)
        # Column comes only from the fixed operation mapping above.
        cursor = db.execute(f"UPDATE profiles SET {column}=?,metadata_revision=metadata_revision+1 WHERE id=? AND revision=? AND metadata_revision=?", (value, profile_id, revision, metadata_revision))
        if cursor.rowcount != 1:
            raise Refused(tr("프로필이 바뀌었습니다. 다시 검토하세요"))
    return get(profile_id)
