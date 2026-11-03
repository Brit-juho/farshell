"""Batch physical observation. An incomplete snapshot is never an exit event."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import fsguard
import git_safe
import tmux_runner
from .db import database, host_id, uid, encode, changed, Refused


def observe():
    rc, raw, err = tmux_runner.run(["list-sessions", "-F", "#{session_id}\t#{session_created}\t#{session_name}\t#{@fsh_origin}"], timeout=2)
    if rc:
        if rc == 1 and b"no server running on" in err:
            return {"complete": True, "epoch": None, "sessions": []}
        # A socket error or timeout cannot establish that a server is absent.
        return {"complete": False, "sessions": [], "error": "tmux_unavailable"}
    server = tmux_runner.run_text(["display-message", "-p", "#{pid}\t#{socket_path}"], timeout=2)
    if not server:
        return {"complete": False, "sessions": [], "error": "identity_unverified"}
    try:
        pid, socket = server.strip().split("\t")
        st = Path(socket).stat()
        birth = subprocess.run(["ps", "-p", str(int(pid)), "-o", "lstart="], capture_output=True, timeout=2, check=False)
        if birth.returncode or not birth.stdout.strip():
            raise ValueError
        epoch = hashlib.sha256(str((socket, st.st_dev, st.st_ino, pid, birth.stdout)).encode()).hexdigest()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"complete": False, "sessions": [], "error": "identity_unverified"}
    panes = tmux_runner.run_text(["list-panes", "-a", "-F", "#{session_id}\t#{pane_id}\t#{pane_pid}\t#{window_id}\t#{window_active}#{pane_active}\t#{pane_current_path}\t#{pane_current_command}"], timeout=2)
    if panes is None:
        return {"complete": False, "sessions": [], "error": "panes_unavailable"}
    by_session = {}
    for line in panes.splitlines():
        p = line.split("\t")
        if len(p) != 7 or not p[2].isdigit():
            return {"complete": False, "sessions": [], "error": "invalid_snapshot"}
        by_session.setdefault(p[0], []).append({"pane_id": p[1], "pid": int(p[2]), "window": p[3],
                                               "active": p[4] == "11", "cwd": p[5], "command": p[6]})
    sessions = []
    for line in raw.decode("utf-8", "strict").splitlines():
        p = line.split("\t")
        if len(p) != 4 or not p[1].isdigit() or not by_session.get(p[0]):
            return {"complete": False, "sessions": [], "error": "invalid_snapshot"}
        children = by_session[p[0]]
        active = next((x for x in children if x["active"]), children[0])
        sessions.append({"physical_key": f"{p[0]}:{p[1]}", "tmux_id": p[0], "name": p[2],
                         "origin": p[3] if p[3] in ("local", "peer") else "external",
                         "cwd": active["cwd"], "panes": children})
    return {"complete": True, "epoch": epoch, "sessions": sessions}


def inspect_workspace(cwd):
    """Only initial placement; cd never silently reparents a durable session."""
    try:
        path = fsguard.resolve_under_roots(cwd)
        if not path.is_dir():
            return None
        result = subprocess.run(git_safe.argv("rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir", cwd=path),
                                capture_output=True, timeout=2, check=False, env=git_safe.env())
        roots = result.stdout.decode("utf-8", "strict").splitlines() if result.returncode == 0 else []
        if len(roots) == 2:
            path = fsguard.resolve_under_roots(roots[0])
            common = Path(roots[1]).resolve(strict=True)
            marker = common.stat()
            locator = f"git:{common}:{marker.st_dev}:{marker.st_ino}"
            project_path = common.parent if common.name == ".git" else path
            kind = "checkout" if path == project_path else "worktree"
        else:
            marker = path.stat()
            locator = f"directory:{path}:{marker.st_dev}:{marker.st_ino}"
            project_path, kind = path, "directory"
        marker = path.stat()
        return {"locator": locator, "project_path": str(project_path), "project_kind": "git" if roots else "directory",
                "path": str(path), "kind": kind, "incarnation": f"{marker.st_dev}:{marker.st_ino}"}
    except (OSError, UnicodeError, ValueError, fsguard.FsDenied, subprocess.TimeoutExpired):
        return None


def workspace(db, placement):
    if not placement:
        return None
    host = host_id(db)
    row = db.execute("SELECT id FROM projects WHERE host=? AND locator=?", (host, placement['locator'])).fetchone()
    project = row[0] if row else uid()
    if not row:
        root = Path(placement['project_path'])
        db.execute("INSERT INTO projects(id,host,locator,name,path,kind) VALUES(?,?,?,?,?,?)",
                   (project, host, placement['locator'], root.name or str(root), str(root), placement['project_kind']))
    row = db.execute("SELECT id FROM workspaces WHERE project_id=? AND path=? AND incarnation=?",
                     (project, placement['path'], placement['incarnation'])).fetchone()
    if row:
        return row[0]
    wid = uid()
    db.execute("INSERT INTO workspaces(id,project_id,path,kind,incarnation) VALUES(?,?,?,?,?)",
               (wid, project, placement['path'], placement['kind'], placement['incarnation']))
    return wid


def _reconcile(snapshot=None):
    snapshot = observe() if snapshot is None else snapshot
    now = time.time()
    with database() as check:
        known = {(r['epoch'], r['physical_key']) for r in check.execute('SELECT epoch,physical_key FROM runtimes')}
    placements = {item['physical_key']: inspect_workspace(item['cwd']) for item in snapshot['sessions']
                  if (snapshot.get('epoch'), item['physical_key']) not in known}
    with database(write=True) as db:
        if not snapshot["complete"]:
            db.execute("UPDATE runtimes SET state='unknown' WHERE ended IS NULL")
            return snapshot
        host = host_id(db)
        epoch = snapshot["epoch"]
        live = {(epoch, item["physical_key"]) for item in snapshot["sessions"]}
        for row in db.execute("SELECT * FROM runtimes WHERE ended IS NULL").fetchall():
            if (row["epoch"], row["physical_key"]) not in live:
                db.execute("UPDATE runtimes SET state='stopped',ended=?,observed=? WHERE id=?", (now, now, row["id"]))
                changed(db, "runtime", row["id"])
        for item in snapshot["sessions"]:
            row = db.execute("SELECT * FROM runtimes WHERE epoch=? AND physical_key=?", (epoch, item["physical_key"])).fetchone()
            detail = encode(item)
            if not row:
                sid, rid = uid(), uid()
                wid = workspace(db, placements.get(item["physical_key"]))
                db.execute("INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated) VALUES(?,?,?,?,?,?,?)",
                           (sid, host, wid, item["name"], item["origin"], now, now))
                db.execute("INSERT INTO runtimes VALUES(?,?,?,?,?,?,?,NULL,?,?)", (rid, sid, epoch, item["physical_key"], item["name"], "running", now, now, detail))
                changed(db, "session", sid)
            else:
                if row["ended"] is not None:
                    # Identity reuse is a conflict, not resurrection of an old grant.
                    raise Refused("runtime_identity_conflict")
                rid = row["id"]
                if detail != row["detail"] or row["state"] != "running":
                    changed(db, "runtime", rid)
                # `observed` is a liveness stamp nobody compares; refresh it only with a real change or once a minute.
                # An unconditional UPDATE here committed (and checkpointed the WAL) on every 5s reconcile.
                if detail != row["detail"] or row["state"] != "running" or row["name"] != item["name"] or now - row["observed"] >= 60:
                    db.execute("UPDATE runtimes SET name=?,state='running',observed=?,detail=? WHERE id=?", (item["name"], now, detail, rid))
            for pane in item["panes"]:
                key = f"{pane['pane_id']}:{pane['pid']}"
                encoded = encode(pane)
                if row and db.execute("SELECT 1 FROM pane_executions WHERE runtime_id=? AND physical_key=? AND detail=?", (rid, key, encoded)).fetchone():
                    continue
                db.execute("INSERT INTO pane_executions VALUES(?,?,?,?) ON CONFLICT(runtime_id,physical_key) DO UPDATE SET detail=excluded.detail",
                           (uid(), rid, key, encoded))
    return snapshot


def resolve_runtime(sid):
    with database() as db:
        row = db.execute("SELECT * FROM runtimes WHERE session_id=? AND ended IS NULL", (sid,)).fetchone()
        if not row:
            raise Refused("runtime_not_running")
        value = dict(row)
    current = observe()
    if not current["complete"] or current["epoch"] != value["epoch"]:
        raise Refused("identity_unverified")
    matching = next((x for x in current["sessions"] if x["physical_key"] == value["physical_key"]), None)
    if not matching:
        raise Refused("runtime_changed")
    return value, matching


def reconcile(snapshot=None):
    import store_lock
    from .db import directory
    with store_lock.exclusive(directory() / 'lifecycle.lock'):
        return _reconcile(snapshot)


def discover_projects():
    """Retain discovered workspaces even when no terminal is running there."""
    import worktree
    items = worktree.list_worktrees()
    placements = []
    deadline = time.monotonic() + 5
    for item in items[:500]:
        if item.get('path'):
            placements.append((inspect_workspace(item['path']),item.get('branch')))
        if time.monotonic() >= deadline:
            break
    with database(write=True) as db:
        for placement, branch in placements:
            wid = workspace(db, placement)
            if wid:
                db.execute('UPDATE workspaces SET branch=?,available=1 WHERE id=?', (branch, wid))
