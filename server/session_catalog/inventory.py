"""Batch physical observation. An incomplete snapshot is never an exit event."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import fsguard
import git_safe
import tmux_runner
from .db import database, host_id, uid, encode, changed, Refused, touch_activity

logger = logging.getLogger(__name__)


def observe():
    """tmux 세션 목록 한 번. `list-sessions`와 `list-panes` 사이에 세션이 생기거나 사라지면 두 결과가
    어긋난다 — 한 번만 다시 읽는다(그래도 어긋나면 불완전으로 둔다)."""
    snapshot = _observe_once()
    if not snapshot["complete"] and snapshot.get("error") == "invalid_snapshot":
        snapshot = _observe_once()
    return snapshot


def _observe_once():
    rc, raw, err = tmux_runner.run(["list-sessions", "-F", "#{session_id}\t#{session_created}\t#{session_name}\t#{@fsh_origin}"], timeout=2)
    if rc:
        # 「서버 없음」을 tmux_runner.server_absent 한 곳에서 판정한다(위임 장부와 같은 기준). 재부팅 뒤 소켓 파일이
        # 없을 때도 여기서 확정해야, 직전 세션들이 몇 시간씩 「상태 모름」으로 남지 않고 곧바로 종료로 기록된다.
        if tmux_runner.server_absent(rc, err):
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
    panes = tmux_runner.run_text(["list-panes", "-a", "-F", "#{session_id}\t#{pane_id}\t#{pane_pid}\t#{window_id}\t#{window_active}#{pane_active}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_dead}\t#{pane_dead_status}"], timeout=2)
    if panes is None:
        return {"complete": False, "sessions": [], "error": "panes_unavailable"}
    by_session = {}
    for line in panes.splitlines():
        p = line.split("\t")
        if len(p) != 9:
            return {"complete": False, "sessions": [], "error": "invalid_snapshot"}
        dead = p[7] == "1"
        # 프로세스가 끝나고 남겨 둔 pane(`remain-on-exit`)은 정상 상태다. tmux 3.7c는 그 pid를 남기지만 비울 수도
        # 있다고 보고 받는다 — 예전에는 pid가 숫자가 아닌 한 줄이 스냅샷 전체를 불완전으로 만들어 모든 런타임이 「상태 모름」이 됐다.
        if not p[2].isdigit() and not dead:
            return {"complete": False, "sessions": [], "error": "invalid_snapshot"}
        pane = {"pane_id": p[1], "pid": int(p[2]) if p[2].isdigit() else None, "window": p[3],
                "active": p[4] == "11", "cwd": p[5], "command": p[6]}
        if dead:
            pane["dead"] = True
            pane["dead_status"] = int(p[8]) if p[8].lstrip("-").isdigit() else None
        by_session.setdefault(p[0], []).append(pane)
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
    """세션이 놓일 곳(배치). 처음 관측 때 정하고, cd로 다른 저장소에 가도 바꾸지 않는다 — 예외는 「저장소 밖」
    배치가 나중에 저장소 근거를 얻을 때 한 번 승격하는 것뿐이다(`_retry_placements`).

    Git이 「저장소가 아니다」라고 답할 때만 저장소 밖(directory)이다. 시간 초과·다른 오류는 None(다음 관측에서 다시) —
    예전에는 일시 실패도 저장소 밖으로 영구 분류했다."""
    try:
        path = fsguard.resolve_under_roots(cwd, git_meta=True)
        if not path.is_dir():
            return None
        result = subprocess.run(git_safe.argv("rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir", cwd=path),
                                capture_output=True, timeout=2, check=False, env=git_safe.env())
        if result.returncode != 0 and b"not a git repository" not in (result.stderr or b"").lower():
            return None
        roots = result.stdout.decode("utf-8", "strict").splitlines() if result.returncode == 0 else []
        if result.returncode == 0 and len(roots) != 2:
            return None
        if len(roots) == 2:
            path = fsguard.resolve_under_roots(roots[0], git_meta=True)
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


# 배치를 다시 본 (세션 정체성 키, cwd) — 같은 cwd를 5초마다 Git으로 다시 읽지 않는다. 위치가 바뀌면 다시 본다.
_placement_tried: dict[str, set[str]] = {}
_PLACEMENT_TRIES_PER_SESSION = 32


def _agent_cwd(name):
    try:
        import agent_status
        return agent_status.agent_cwd_for(name)
    except Exception:  # noqa: BLE001 — 에이전트 상태가 없으면 pane cwd만 본다
        return None


def _retry_placements(snapshot, known_rows):
    """이미 기록된 실행 중 세션의 배치를 다시 본다(L3).
    - 배치가 없는(None — 처음 관측 때 Git이 일시 실패) 세션: 활성 pane 또는 CLI가 알린 cwd로 다시 정한다.
    - 「저장소 밖」 배치: 그 cwd가 저장소 안이 되면 한 번 저장소로 승격한다. 저장소 배치는 다시 바꾸지 않는다.
    돌려주는 값: {세션 id: 새 배치}."""
    live = {item['physical_key']: item for item in snapshot['sessions']}
    for key in list(_placement_tried):
        if key not in live:
            _placement_tried.pop(key, None)
    out = {}
    for row in known_rows:
        item = live.get(row['physical_key'])
        if not item or (row['workspace_id'] and row['project_kind'] != 'directory'):
            continue
        tried = _placement_tried.setdefault(row['physical_key'], set())
        for cwd in dict.fromkeys(c for c in (item.get('cwd'), _agent_cwd(item.get('name'))) if c):
            if cwd in tried or len(tried) >= _PLACEMENT_TRIES_PER_SESSION:
                continue
            placement = inspect_workspace(cwd)
            if placement is None:
                continue          # 일시 실패 — 이 cwd는 다음 관측에서 다시 본다
            tried.add(cwd)
            if not row['workspace_id'] or placement['project_kind'] == 'git':
                out[row['session_id']] = (row['workspace_id'], placement)
                break
    return out


def _reconcile(snapshot=None):
    snapshot = observe() if snapshot is None else snapshot
    now = time.time()
    with database() as check:
        known = {(r['epoch'], r['physical_key']) for r in check.execute('SELECT epoch,physical_key FROM runtimes')}
        unplaced = []
        if snapshot.get('complete'):
            unplaced = [dict(r) for r in check.execute(
                "SELECT r.physical_key, s.id AS session_id, s.workspace_id, p.kind AS project_kind FROM runtimes r"
                " JOIN sessions s ON s.id=r.session_id LEFT JOIN workspaces w ON w.id=s.workspace_id"
                " LEFT JOIN projects p ON p.id=w.project_id"
                " WHERE r.ended IS NULL AND r.epoch=? AND (s.workspace_id IS NULL OR p.kind='directory')",
                (snapshot.get('epoch'),))]
    placements = {item['physical_key']: inspect_workspace(item['cwd']) for item in snapshot['sessions']
                  if (snapshot.get('epoch'), item['physical_key']) not in known}
    replaced = _retry_placements(snapshot, unplaced) if unplaced else {}
    with database(write=True) as db:
        if not snapshot["complete"]:
            db.execute("UPDATE runtimes SET state='unknown' WHERE ended IS NULL")
            return snapshot
        host = host_id(db)
        epoch = snapshot["epoch"]
        for sid, (old, placement) in replaced.items():
            wid = workspace(db, placement)
            # 그 사이 다른 쪽이 이미 바꿨으면(old와 다르면) 건드리지 않는다.
            cur = db.execute("UPDATE sessions SET workspace_id=?,updated=?,revision=revision+1 WHERE id=? AND workspace_id IS ?",
                             (wid, now, sid, old))
            if cur.rowcount:
                changed(db, "session", sid)
        live = {(epoch, item["physical_key"]) for item in snapshot["sessions"]}
        for row in db.execute("SELECT * FROM runtimes WHERE ended IS NULL").fetchall():
            if (row["epoch"], row["physical_key"]) not in live:
                # 끝날 때의 브랜치(그 세션 작업 공간의 마지막으로 알던 브랜치)와 마지막 활동(마지막으로 살아 있던 관측 시각).
                branch = db.execute("SELECT w.branch FROM sessions s JOIN workspaces w ON w.id=s.workspace_id WHERE s.id=?",
                                    (row["session_id"],)).fetchone()
                db.execute("UPDATE runtimes SET state='stopped',ended=?,observed=?,branch_at_end=COALESCE(branch_at_end,?) WHERE id=?",
                           (now, now, branch["branch"] if branch else None, row["id"]))
                touch_activity(db, row["session_id"], row["observed"])
                changed(db, "runtime", row["id"])
        for item in snapshot["sessions"]:
            row = db.execute("SELECT * FROM runtimes WHERE epoch=? AND physical_key=?", (epoch, item["physical_key"])).fetchone()
            detail = encode(item)
            if not row:
                sid, rid = uid(), uid()
                wid = workspace(db, placements.get(item["physical_key"]))
                db.execute("INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated,last_activity) VALUES(?,?,?,?,?,?,?,?)",
                           (sid, host, wid, item["name"], item["origin"], now, now, now))
                db.execute("INSERT INTO runtimes(id,session_id,epoch,physical_key,name,state,started,ended,observed,detail) VALUES(?,?,?,?,?,?,?,NULL,?,?)", (rid, sid, epoch, item["physical_key"], item["name"], "running", now, now, detail))
                changed(db, "session", sid)
            else:
                if row["ended"] is not None:
                    other = db.execute("SELECT 1 FROM runtimes WHERE session_id=? AND ended IS NULL AND id!=?",
                                       (row["session_id"], row["id"])).fetchone()
                    if row["dismissed_at"] is not None and not other:
                        # 사람이 「종료로 표시」했지만 사실 살아 있던 실행이 다시 보인다 — 신원 재사용이 아니라 표시를 되돌린다.
                        db.execute("UPDATE runtimes SET state='running',ended=NULL,dismissed_at=NULL,observed=? WHERE id=?", (now, row["id"]))
                        changed(db, "runtime", row["id"])
                        row = db.execute("SELECT * FROM runtimes WHERE id=?", (row["id"],)).fetchone()
                    else:
                        # Identity reuse is a conflict, not resurrection of an old grant. 그 세션 하나만 건너뛴다 —
                        # 예외로 트랜잭션 전체를 되돌리면 같은 관측의 종료 기록과 이어지는 수집·보관·대화 연결이 모두 멈췄다.
                        logger.warning("catalog runtime identity conflict: runtime=%s", row["id"])
                        continue
                rid = row["id"]
                if row["name"] != item["name"]:
                    # tmux 이름이 바뀌었다(사람이 터미널에서 바꿈). 보이는 이름이 자동이면 따라간다 — 사람이 붙인 별칭은 그대로.
                    db.execute("UPDATE sessions SET title=?,updated=?,revision=revision+1 WHERE id=? AND name_source='auto' AND title!=?",
                               (item["name"], now, row["session_id"], item["name"]))
                if detail != row["detail"] or row["state"] != "running":
                    changed(db, "runtime", rid)
                # `observed` is a liveness stamp nobody compares; refresh it only with a real change or once a minute.
                # An unconditional UPDATE here committed (and checkpointed the WAL) on every 5s reconcile.
                if detail != row["detail"] or row["state"] != "running" or row["name"] != item["name"] or now - row["observed"] >= 60:
                    db.execute("UPDATE runtimes SET name=?,state='running',observed=?,detail=? WHERE id=?", (item["name"], now, detail, rid))
            for pane in item["panes"]:
                key = f"{pane['pane_id']}:{pane['pid'] if pane['pid'] is not None else 'dead'}"
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
    items = worktree.list_worktrees_or_empty()
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
