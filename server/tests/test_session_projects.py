"""저장소 = CLI를 켠 곳(session_catalog/placement.py) — 대화를 저장소에 놓기, 거르기, 지운 워크트리, 자동 숨김, 제거 뒤 재등록."""
import json
import subprocess
import time

import pytest


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db, placement
    db.snapshot()
    # pytest 임시 폴더는 /var/folders 아래라 실제 규칙(임시 폴더 거르기)에 걸린다 — 시험에서는 /tmp만 임시로 본다.
    monkeypatch.setattr(placement, "_TEMP", ("/tmp", "/private/tmp"))
    return home


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True,
                   env={"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(path), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    return path


_n = [0]


def conv(provider="claude", launch="cli", cwd="", created=None, updated=None, parent=None, detail=None):
    from session_catalog.db import database, host_id, uid
    now = time.time()
    _n[0] += 1
    cid = uid()
    d = {} if detail is not None else {"launch": launch}
    d.update(detail or {})
    with database(write=True) as db:
        db.execute("INSERT INTO conversations(id,host,provider,scope,native_id,cwd,parent_native_id,created,updated,detail)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cid, host_id(db), provider, "s", f"n{_n[0]}", str(cwd), parent, created or now, updated or created or now, json.dumps(d)))
    return cid


def row(cid):
    from session_catalog.db import database
    with database() as db:
        r = db.execute("SELECT c.workspace_id,c.placement,p.name,p.path,p.kind,p.registered,p.removed,p.last_activity,p.hidden"
                       " FROM conversations c LEFT JOIN workspaces w ON w.id=c.workspace_id LEFT JOIN projects p ON p.id=w.project_id"
                       " WHERE c.id=?", (cid,)).fetchone()
    return dict(r)


def place():
    from session_catalog import placement
    return placement.place_conversations()


def test_terminal_cli_conversations_make_projects_and_merge_into_the_git_top(catalog):
    repo = _repo(catalog / "proj")
    (repo / "frontend").mkdir()
    folder = catalog / "notes"
    folder.mkdir()
    a = conv(cwd=repo / "frontend")
    b = conv(provider="codex", launch="codex-tui", cwd=repo)
    c = conv(cwd=folder)
    assert place() == 3
    ra, rb, rc = row(a), row(b), row(c)
    assert ra["name"] == rb["name"] == "proj" and ra["kind"] == "git" and ra["placement"] == "placed"
    assert ra["registered"] is not None and ra["hidden"] == 0
    assert rc["name"] == "notes" and rc["kind"] == "directory"


@pytest.mark.parametrize("provider,launch", [("claude", "sdk-cli"), ("claude", "claude-desktop"), ("codex", "codex_exec"),
                                              ("codex", "Codex Desktop"), ("claude", "")])
def test_automation_and_desktop_apps_do_not_make_projects(catalog, provider, launch):
    repo = _repo(catalog / "proj")
    cid = conv(provider=provider, launch=launch, cwd=repo)
    place()
    r = row(cid)
    assert r["workspace_id"] is None and r["placement"] == "skip:launch"


def test_temp_and_missing_cwds_and_children_are_skipped(catalog):
    t = conv(cwd="/private/tmp/x")
    e = conv(cwd="")
    child = conv(cwd=_repo(catalog / "p"), parent="n1")
    place()
    assert row(t)["placement"] == "skip:cwd" and row(e)["placement"] == "skip:cwd"
    assert row(child)["placement"] == "skip:child" and row(child)["workspace_id"] is None, "하위 대화는 놓지 않는다"


def test_a_linked_worktree_goes_to_its_main_repository(catalog):
    repo = _repo(catalog / "proj")
    wt = catalog / "proj-wt"
    _git(repo, "worktree", "add", "-q", "-b", "fix", str(wt))
    cid = conv(cwd=wt)
    place()
    r = row(cid)
    assert r["name"] == "proj" and r["path"] == str(repo.resolve())


def test_a_deleted_worktree_folder_finds_its_repository_through_the_recorded_workspace(catalog):
    repo = _repo(catalog / "proj")
    wt = catalog / "proj-wt"
    _git(repo, "worktree", "add", "-q", "-b", "fix", str(wt))
    first = conv(cwd=wt / "src")
    (wt / "src").mkdir()
    place()                                  # 옛 작업 트리가 기록에 남는다
    _git(repo, "worktree", "remove", "--force", str(wt))
    later = conv(cwd=wt / "src")
    lost = conv(cwd=catalog / "never-existed")
    place()
    assert row(first)["name"] == "proj"
    assert row(later)["name"] == "proj", "지운 워크트리 아래 폴더 → 기록의 옛 작업 트리 → 본 저장소"
    assert row(lost)["placement"] == "skip:gone"


def test_old_projects_are_auto_hidden_until_new_activity_but_a_person_hide_stays(catalog):
    old = time.time() - 120 * 86400
    a = _repo(catalog / "a")
    first = conv(cwd=a, created=old, updated=old)
    place()
    assert row(first)["hidden"] == 2
    fresh = conv(cwd=a)
    place()
    assert row(fresh)["hidden"] == 0, "새 활동이면 자동 숨김이 풀린다"
    from session_catalog.db import database
    with database(write=True) as db:
        db.execute("UPDATE projects SET hidden=1")
    conv(cwd=a)
    place()
    assert row(fresh)["hidden"] == 1, "사람이 숨긴 것은 새 활동에도 그대로"


def test_a_removed_project_comes_back_only_for_a_conversation_started_after_removal(catalog):
    a = _repo(catalog / "a")
    conv(cwd=a, created=100, updated=100)
    place()
    from session_catalog.db import database
    removed_at = time.time() - 10
    with database(write=True) as db:
        db.execute("UPDATE projects SET removed=?", (removed_at,))
    before = conv(cwd=a, created=removed_at - 5)
    place()
    assert row(before)["removed"] == removed_at, "제거 전에 시작한 대화는 되살리지 않는다"
    after = conv(cwd=a, created=removed_at + 5)
    place()
    assert row(after)["removed"] is None


def test_launch_is_read_from_the_archived_head_when_the_record_lacks_it(catalog):
    from session_catalog.db import database, uid
    from session_history import archive
    a = _repo(catalog / "a")
    head = (json.dumps({"type": "user", "sessionId": "x", "cwd": str(a), "entrypoint": "cli"}) + "\n").encode()
    digest = archive.put_chunk(head)
    cid = conv(cwd=a, detail={})
    with database(write=True) as db:
        db.execute("INSERT INTO manifests VALUES(?,?,?,?,?,?,?,?)",
                   (uid(), cid, "f", "g", len(head), "d", json.dumps({"chunks": [{"digest": digest, "size": len(head)}],
                                                                      "relative_path": "projects/x/x.jsonl"}), time.time()))
    place()
    r = row(cid)
    assert r["name"] == "a" and r["placement"] == "placed"


def test_backfill_uses_the_transcript_times_not_the_capture_times(catalog):
    """기록 저장소의 created·updated는 수집 시각이다 — 옛 대화는 원본 줄의 timestamp로 등록·마지막 활동을 정한다."""
    from datetime import datetime
    from session_catalog.db import database, uid
    from session_history import archive
    a = _repo(catalog / "a")
    lines = [{"type": "user", "sessionId": "x", "cwd": str(a), "entrypoint": "cli", "timestamp": "2026-01-02T03:04:05.000Z"},
             {"type": "assistant", "timestamp": "2026-01-03T00:00:00Z"}]
    head = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    digest = archive.put_chunk(head)
    cid = conv(cwd=a, detail={}, created=time.time(), updated=time.time())   # 수집은 방금
    with database(write=True) as db:
        db.execute("INSERT INTO manifests VALUES(?,?,?,?,?,?,?,?)",
                   (uid(), cid, "f", "g", len(head), "d", json.dumps({"chunks": [{"digest": digest, "size": len(head)}],
                                                                      "relative_path": "projects/x/x.jsonl"}), time.time()))
    place()
    r = row(cid)
    assert r["registered"] == datetime.fromisoformat("2026-01-02T03:04:05+00:00").timestamp()
    assert r["last_activity"] == datetime.fromisoformat("2026-01-03T00:00:00+00:00").timestamp()
    assert r["hidden"] == 2, "실제 마지막 활동이 90일 넘었다 — 자동 숨김"


def test_a_failing_git_check_is_retried_then_given_up(catalog, monkeypatch):
    from session_catalog import inventory
    a = _repo(catalog / "a")
    monkeypatch.setattr(inventory, "inspect_workspace", lambda cwd: None)
    cid = conv(cwd=a)
    place()
    assert row(cid)["placement"] is None
    place()
    assert row(cid)["placement"] == "skip:git"


def test_new_captures_record_how_the_cli_was_started():
    from session_history import adapters
    claude = adapters.identity((json.dumps({"sessionId": "s1", "cwd": "/x", "entrypoint": "cli"}) + "\n").encode().decode(), "claude", "p/s1.jsonl")
    codex = adapters.identity(json.dumps({"type": "session_meta", "payload": {"id": "c1", "cwd": "/x", "originator": "codex_exec"}}) + "\n",
                              "codex", "2026/a.jsonl")
    assert claude["launch"] == "cli" and codex["launch"] == "codex_exec"


def test_a_missing_path_that_escapes_the_roots_is_not_mapped(catalog):
    repo = _repo(catalog / "proj")
    conv(cwd=repo)
    place()
    sneaky = conv(cwd=f"{repo}/../../outside/missing")
    place()
    assert row(sneaky)["placement"] == "skip:cwd", "정규화하면 탐색 허용 폴더 밖 — 문자열 앞부분만으로 옛 작업 트리에 붙이지 않는다"


def test_placement_only_patches_its_own_detail_keys(catalog):
    """배치가 detail 전체를 덮으면 그 사이 수집이 고친 칸(검증 여부 등)이 되돌아간다 — 바꿀 칸만 고친다."""
    from session_catalog import placement, inventory
    from session_catalog.db import database
    a = _repo(catalog / "a")
    cid = conv(cwd=a)
    real = inventory.inspect_workspace

    def racing(cwd):
        with database(write=True) as db:   # git 확인 중에 수집이 detail을 고친다
            db.execute("UPDATE conversations SET detail=json_set(detail,'$.native_identity_verified',json('true')) WHERE id=?", (cid,))
        return real(cwd)
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(inventory, "inspect_workspace", racing)
    try:
        placement.place_conversations()
    finally:
        mp.undo()
    with database() as db:
        d = json.loads(db.execute("SELECT detail FROM conversations WHERE id=?", (cid,)).fetchone()[0])
    assert d["native_identity_verified"] is True and row(cid)["placement"] == "placed"


def test_new_activity_on_a_placed_conversation_lifts_the_project(catalog):
    from session_catalog import placement
    from session_catalog.db import database
    old = time.time() - 200 * 86400
    a = _repo(catalog / "a")
    cid = conv(cwd=a, detail={"launch": "cli", "started": old, "last": old})
    place()
    assert row(cid)["hidden"] == 2
    with database(write=True) as db:
        wid = db.execute("SELECT workspace_id FROM conversations WHERE id=?", (cid,)).fetchone()[0]
        placement.touch_project(db, wid, time.time(), time.time())
    assert row(cid)["hidden"] == 0 and row(cid)["last_activity"] > old, "이어서 쓴 대화가 저장소를 다시 올린다"


def test_first_timestamp_reads_the_transcript_not_the_capture_time():
    from session_catalog import placement
    assert placement.first_timestamp(b'{"type":"x"}\n{"timestamp":"2026-02-03T04:05:06Z"}\n') == 1770091506.0
    assert placement.first_timestamp(b"no time") is None
