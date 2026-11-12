"""카탈로그 v2 동작(세션 로직 점검 단계 2): 목록 정렬·검색, 보이는 이름(별칭), 자동 보관, 종료로 표시, 자동 별칭."""

import time

import pytest


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db
    db.snapshot()
    return db


def _item(key, name, cwd=""):
    return {"physical_key": key, "tmux_id": key.split(":")[0], "name": name, "origin": "local", "cwd": cwd, "panes": []}


def _reconcile(*items, epoch="e"):
    from session_catalog import inventory
    inventory.reconcile({"complete": True, "epoch": epoch, "sessions": list(items)})


def _sessions(db, **kw):
    return {r["name"]: r for r in db.snapshot(**kw)["sessions"]}


def test_activity_order_cursor_and_search(catalog):
    db = catalog
    for i in range(5):
        _reconcile(_item(f"${i}:{i}", f"s{i}"))
    _reconcile()   # 모두 종료
    from session_catalog.db import database
    with database(write=True) as con:
        for i in range(5):
            con.execute("UPDATE sessions SET last_activity=? WHERE title=?", (1000 + i, f"s{i}"))
    first = db.snapshot(order="activity", limit=2, meta=False)
    assert [r["name"] for r in first["sessions"]] == ["s4", "s3"]
    assert "projects" not in first
    second = db.snapshot(order="activity", limit=2, cursor=first["next"], meta=False)
    assert [r["name"] for r in second["sessions"]] == ["s2", "s1"]
    third = db.snapshot(order="activity", limit=2, cursor=second["next"], meta=False)
    assert [r["name"] for r in third["sessions"]] == ["s0"] and third["next"] is None
    assert [r["name"] for r in db.snapshot(order="activity", q="s3")["sessions"]] == ["s3"]
    assert db.snapshot(order="activity", q="100%")["sessions"] == []   # % 는 글자 그대로


@pytest.mark.parametrize("kw", [{"order": "x"}, {"order": "activity", "cursor": "bad"}, {"order": "id", "cursor": "1:" + "a" * 32},
                                {"project": "nope"}, {"q": "x" * 201}])
def test_bad_list_arguments_are_refused(catalog, kw):
    with pytest.raises(catalog.Refused):
        catalog.snapshot(**kw)


def test_last_user_text_only_for_local(catalog):
    from session_catalog.db import database
    _reconcile(_item("$1:1", "w"))
    with database(write=True) as con:
        con.execute("INSERT INTO sources(id,provider,root,scope,enabled,created) VALUES('s','claude','/x','l',1,1)")
        con.execute("INSERT INTO conversations(id,host,provider,scope,native_id,title,created,updated) VALUES(?,'h','claude','l','n','제목',1,1)", ("c" * 32,))
        rid = con.execute("SELECT id FROM runtimes").fetchone()[0]
        con.execute("INSERT INTO conversation_executions VALUES(?,?,'%1',1)", ("c" * 32, rid))
        con.execute("INSERT INTO conversation_local(conversation_id,last_user_text,user_message_count,updated) VALUES(?,'고쳐줘',3,1)", ("c" * 32,))
    row = _sessions(catalog)["w"]
    assert (row["provider"], row["conversation_title"], row["conversation_count"], row["last_user_text"]) == ("claude", "제목", 1, "고쳐줘")
    assert "last_user_text" not in _sessions(catalog, local=False)["w"]


def test_alias_follows_tmux_rename_until_a_person_names_it(catalog):
    db = catalog
    _reconcile(_item("$1:1", "app"))
    sid = db.snapshot()["sessions"][0]["id"]
    _reconcile(_item("$1:1", "app-renamed"))
    row = db.snapshot()["sessions"][0]
    assert (row["title"], row["name_source"]) == ("app-renamed", "auto")
    db.edit_session(sid, row["revision"], title="결제 버그")
    _reconcile(_item("$1:1", "again"))
    row = db.snapshot()["sessions"][0]
    assert (row["title"], row["name_source"]) == ("결제 버그", "human")
    db.edit_session(sid, row["revision"], auto_name=True)
    row = db.snapshot()["sessions"][0]
    assert (row["title"], row["name_source"]) == ("again", "auto")


def test_auto_archive_uses_last_activity_and_cleans_orphans(catalog):
    db = catalog
    from session_catalog.db import database
    _reconcile(_item("$1:1", "old"), _item("$2:2", "live"))
    _reconcile(_item("$2:2", "live"))
    now = time.time()
    with database(write=True) as con:
        con.execute("UPDATE sessions SET last_activity=? WHERE title='old'", (now - 40 * 86400,))
        con.execute("UPDATE sessions SET last_activity=? WHERE title='live'", (now - 40 * 86400,))
        con.execute("INSERT INTO sessions(id,host,title,origin,created,updated) VALUES(?,'h','orphan','local',?,?)", ("f" * 32, now - 2 * 86400, now - 2 * 86400))
    assert db.auto_archive(now) == 2   # old(종료 + 40일) + orphan(실행 기록 없음 + 하루 넘음). live는 실행 중
    archived = {r["title"] for r in db.snapshot(archived=True)["sessions"]}
    assert archived == {"old", "orphan"}


def test_dismiss_marks_an_unknown_run_ended_and_a_return_revives_it(catalog):
    db = catalog
    from session_catalog.db import database
    _reconcile(_item("$1:1", "w"))
    from session_catalog import inventory
    inventory.reconcile({"complete": False, "sessions": []})   # 상태 모름
    row = db.snapshot()["sessions"][0]
    with pytest.raises(db.Refused, match="runtime_state_known"):
        db.dismiss_runtime(row["id"], row["revision"])          # 아직 10분 안 됨
    with database(write=True) as con:
        con.execute("UPDATE runtimes SET observed=?", (time.time() - 3600,))
    db.dismiss_runtime(row["id"], row["revision"])
    assert db.snapshot()["sessions"][0]["execution_state"] == "dismissed"
    _reconcile(_item("$1:1", "w"))                               # 사실 살아 있었다 → 되살아난다(충돌 아님)
    row = db.snapshot()["sessions"][0]
    assert row["execution_state"] == "running" and row["dismissed_at"] is None


def test_cli_start_names_an_auto_session_after_its_repository(catalog, tmp_path, monkeypatch):
    import subprocess
    import agent_status
    from session_history import worker
    repo = tmp_path / "home" / "farshell"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _reconcile(_item("$1:1", "home"))
    entry = {"session_id": "n", "tmux_session": "home", "agent": "claude", "_tmux_uid": "$1", "cwd": str(repo),
             "_session_start_at": 5.0, "updated_at": time.time()}
    monkeypatch.setattr(agent_status, "visible_state", lambda: {"x": entry})
    monkeypatch.setattr(worker, "_NAMED", {})
    worker.Collector.track_sessions()
    row = catalog.snapshot()["sessions"][0]
    assert (row["title"], row["name"]) == ("farshell", "home"), "보이는 이름만 바뀌고 tmux 이름은 그대로"
    assert row["last_activity"] >= entry["updated_at"] - 1
    # 사람이 이름을 붙이면 다음 CLI 시작에도 건드리지 않는다.
    catalog.edit_session(row["id"], row["revision"], title="내 이름")
    entry["_session_start_at"] = 6.0
    worker.Collector.track_sessions()
    assert catalog.snapshot()["sessions"][0]["title"] == "내 이름"


def test_cli_start_rename_can_be_turned_off(catalog, tmp_path, monkeypatch):
    import agent_status
    from session_history import worker
    catalog.configure({"auto_name": False})
    _reconcile(_item("$1:1", "home"))
    monkeypatch.setattr(agent_status, "visible_state", lambda: {"x": {"session_id": "n", "tmux_session": "home", "agent": "claude",
                                                                      "_tmux_uid": "$1", "cwd": str(tmp_path), "_session_start_at": 1.0}})
    monkeypatch.setattr(worker, "_NAMED", {})
    worker.Collector.track_sessions()
    assert catalog.snapshot()["sessions"][0]["title"] == "home"
    assert catalog.snapshot()["auto_name"] is False


def test_branch_at_end_is_recorded_when_a_run_stops(catalog):
    from session_catalog.db import database
    _reconcile(_item("$1:1", "w"))
    with database(write=True) as con:
        con.execute("INSERT INTO workspaces(id,project_id,path,kind,branch,incarnation) VALUES(?,NULL,'/r','checkout','feat/x','1:1')", ("9" * 32,))
        con.execute("UPDATE sessions SET workspace_id=?", ("9" * 32,))
    _reconcile()
    assert catalog.snapshot()["sessions"][0]["branch_at_end"] == "feat/x"


# --- 세션 로직 점검 단계 4: 배치 재시도·승격(L3) ---------------------------------------------------

def _git_repo(path):
    import subprocess
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _placement(db_module, name):
    from session_catalog.db import database
    with database() as con:
        return con.execute("SELECT w.kind, p.kind AS pk, p.name FROM sessions s LEFT JOIN workspaces w ON w.id=s.workspace_id"
                           " LEFT JOIN projects p ON p.id=w.project_id WHERE s.title=?", (name,)).fetchone()


def test_a_session_started_outside_a_repository_is_promoted_once(catalog, tmp_path, monkeypatch):
    from session_catalog import inventory
    monkeypatch.setattr(inventory, "_placement_tried", {})
    home = tmp_path / "home"
    repo = _git_repo(home / "farshell")
    other = _git_repo(home / "other")
    _reconcile(_item("$1:1", "w", str(home)))
    assert tuple(_placement(catalog, "w"))[:2] == ("directory", "directory")
    _reconcile(_item("$1:1", "w", str(repo)))                       # 저장소로 cd → 한 번 승격
    assert tuple(_placement(catalog, "w")) == ("checkout", "git", "farshell")
    _reconcile(_item("$1:1", "w", str(other)))                      # 다른 저장소로 가도 바뀌지 않는다
    assert tuple(_placement(catalog, "w")) == ("checkout", "git", "farshell")


def test_a_failed_first_placement_is_retried(catalog, tmp_path, monkeypatch):
    from session_catalog import inventory
    monkeypatch.setattr(inventory, "_placement_tried", {})
    repo = _git_repo(tmp_path / "home" / "farshell")
    real = inventory.inspect_workspace
    monkeypatch.setattr(inventory, "inspect_workspace", lambda cwd: None)    # Git 일시 실패
    _reconcile(_item("$1:1", "w", str(repo)))
    assert _placement(catalog, "w")["kind"] is None
    monkeypatch.setattr(inventory, "inspect_workspace", real)
    _reconcile(_item("$1:1", "w", str(repo)))
    assert tuple(_placement(catalog, "w")) == ("checkout", "git", "farshell")


def test_a_git_error_is_not_mistaken_for_outside_a_repository(catalog, tmp_path, monkeypatch):
    import subprocess
    from session_catalog import inventory
    repo = _git_repo(tmp_path / "home" / "farshell")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", b"fatal: unable to read index"))
    assert inventory.inspect_workspace(str(repo)) is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", b"fatal: not a git repository"))
    assert inventory.inspect_workspace(str(repo))["kind"] == "directory"


def test_a_folder_that_becomes_a_repository_in_place_is_promoted(catalog, tmp_path, monkeypatch):
    """빈 폴더에서 시작해 그 자리에서 `git init`을 한 세션 — 「저장소 밖」 결과는 1분 뒤 다시 본다."""
    import subprocess
    from session_catalog import inventory
    monkeypatch.setattr(inventory, "_placement_tried", {})
    folder = tmp_path / "home" / "newproj"
    folder.mkdir(parents=True)
    _reconcile(_item("$1:1", "w", str(tmp_path / "home")))
    _reconcile(_item("$1:1", "w", str(folder)))                 # 저장소 밖으로 확인
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    _reconcile(_item("$1:1", "w", str(folder)))
    assert _placement(catalog, "w")["pk"] == "directory", "1분 안에는 다시 보지 않는다"
    monkeypatch.setattr(inventory, "_NONREPO_RECHECK", 0.0)
    for tried in inventory._placement_tried.values():
        for cwd in tried:
            tried[cwd] = 0.0
    _reconcile(_item("$1:1", "w", str(folder)))
    assert tuple(_placement(catalog, "w")) == ("checkout", "git", "newproj")
