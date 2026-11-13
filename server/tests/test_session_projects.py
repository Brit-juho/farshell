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


def test_a_broken_detail_does_not_stall_the_batch(catalog):
    from session_catalog.db import database
    a = _repo(catalog / "a")
    bad = conv(cwd=a)
    good = conv(cwd=a)
    with database(write=True) as db:
        db.execute("UPDATE conversations SET detail='[1,2]' WHERE id=?", (bad,))
    place()
    assert row(good)["placement"] == "placed"
    assert row(bad)["placement"] is not None, "망가진 detail도 결정된다(빈 것으로 보고 실행 방식을 원본에서 찾는다)"


def test_a_symlinked_folder_pointing_outside_is_not_mapped_after_it_disappears(catalog, tmp_path):
    repo = _repo(catalog / "proj")
    conv(cwd=repo)
    place()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    gone = conv(cwd=repo / "link" / "missing")
    place()
    assert row(gone)["placement"] == "skip:cwd", "링크를 풀면 탐색 허용 폴더 밖"


def test_a_compressed_archive_gives_its_real_last_timestamp(catalog):
    import zstandard
    from session_catalog import placement
    from session_catalog.db import database, uid
    from session_history import archive
    body = b"".join(json.dumps({"timestamp": f"2026-03-{d:02d}T00:00:00Z", "pad": "x" * 2000}).encode() + b"\n" for d in range(1, 29))
    packed = zstandard.ZstdCompressor().compress(body)
    half = len(packed) // 2
    parts = [archive.put_chunk(packed[:half]), archive.put_chunk(packed[half:])]
    info = {"compressed": True, "chunks": [{"digest": d, "size": 1} for d in parts]}
    assert placement._last_timestamp(placement._tail(info)) == placement.first_timestamp(b'"timestamp":"2026-03-28T00:00:00Z"')


def test_a_missing_denied_folder_inside_a_repo_is_not_mapped(catalog):
    repo = _repo(catalog / "proj")
    conv(cwd=repo)
    place()
    gone = conv(cwd=repo / ".ssh" / "missing")
    place()
    assert row(gone)["placement"] == "skip:cwd", "없는 경로도 거부 경로 검사를 거친다"


def test_bad_time_values_in_detail_do_not_stall_the_batch(catalog):
    a = _repo(catalog / "a")
    bad = conv(cwd=a, detail={"launch": "cli", "started": 1, "last": "bad"})
    good = conv(cwd=a)
    place()
    assert row(bad)["placement"] == "placed" and row(good)["placement"] == "placed"


def test_concatenated_zstd_frames_are_read_to_the_end(catalog):
    import zstandard
    from session_catalog import placement
    from session_history import archive
    c = zstandard.ZstdCompressor()
    a = c.compress(b'{"timestamp":"2026-04-01T00:00:00Z"}\n')
    b = c.compress(b'{"timestamp":"2026-04-09T00:00:00Z"}\n')
    info = {"compressed": True, "chunks": [{"digest": archive.put_chunk(a + b), "size": 1}]}
    assert placement._last_timestamp(placement._tail(info)) == placement.first_timestamp(b'"timestamp":"2026-04-09T00:00:00Z"')


def test_huge_numbers_are_not_times():
    from session_catalog import placement
    assert placement._num(10 ** 309) is None and placement._num(float("nan")) is None and placement._num(True) is None
    assert placement._num(5) == 5.0


# --- P4: 저장소·대화 목록 API(projects_view) -------------------------------------------------------------

def _view(monkeypatch, running=()):
    from session_catalog import projects_view
    monkeypatch.setattr(projects_view, "_running_native_ids", lambda: set(running))
    return projects_view


def _native(cid):
    from session_catalog.db import database
    with database() as db:
        return db.execute("SELECT native_id FROM conversations WHERE id=?", (cid,)).fetchone()[0]


def test_project_list_shows_registered_projects_with_ended_counts(catalog, monkeypatch):
    a, b = _repo(catalog / "a"), _repo(catalog / "b")
    live = conv(cwd=a)
    conv(cwd=a)
    conv(cwd=b)
    place()
    view = _view(monkeypatch, running={_native(live)})
    got = {p["name"]: p for p in view.project_list()["projects"]}
    assert got["a"]["ended_count"] == 1, "돌고 있는 대화는 「종료 N」에 넣지 않는다"
    assert got["b"]["ended_count"] == 1 and got["a"]["kind"] == "git"
    view.update_project(got["b"]["id"], {"hidden": True})
    assert [p["name"] for p in view.project_list()["projects"]] == ["a"]
    assert {p["name"] for p in view.project_list(include_hidden=True)["projects"]} == {"a", "b"}
    view.update_project(got["a"]["id"], {"removed": True})
    assert view.project_list(include_hidden=True)["projects"][0]["name"] == "b", "제거한 저장소는 설정에도 없다"


def test_conversation_list_pages_by_activity_and_searches(catalog, monkeypatch):
    from session_catalog.db import database
    a = _repo(catalog / "a")
    t = time.time() - 100
    ids = [conv(cwd=a, detail={"launch": "cli", "started": t + i, "last": t + 10 + i}) for i in range(5)]
    place()
    with database(write=True) as db:
        db.execute("INSERT INTO conversation_local(conversation_id,last_user_text,user_message_count,updated) VALUES(?,?,?,?)",
                   (ids[1], "결제 버그 고쳐줘", 3, 0))
    view = _view(monkeypatch)
    pid = view.project_list()["projects"][0]["id"]
    first = view.conversation_list(project=pid, limit=2)
    assert [c["id"] for c in first["conversations"]] == [ids[4], ids[3]] and first["next"]
    second = view.conversation_list(project=pid, limit=2, cursor=first["next"])
    assert [c["id"] for c in second["conversations"]] == [ids[2], ids[1]]
    hit = view.conversation_list(q="결제")["conversations"]
    assert [c["id"] for c in hit] == [ids[1]] and hit[0]["user_message_count"] == 3
    assert view.conversation_list(q="100%")["conversations"] == [], "% 는 글자 그대로"


def test_archived_conversations_have_their_own_list_and_counts(catalog, monkeypatch):
    from session_catalog.db import database
    a = _repo(catalog / "a")
    keep, gone = conv(cwd=a), conv(cwd=a)
    place()
    with database(write=True) as db:
        db.execute("UPDATE conversations SET archived=? WHERE id=?", (time.time(), gone))
    view = _view(monkeypatch)
    assert [c["id"] for c in view.conversation_list()["conversations"]] == [keep]
    arch = view.conversation_list(archived=True)
    assert [c["id"] for c in arch["conversations"]] == [gone] and sum(arch["project_counts"].values()) == 1


def test_bad_arguments_are_refused(catalog, monkeypatch):
    view = _view(monkeypatch)
    from session_catalog.db import Refused
    for kw in ({"limit": 0}, {"limit": 101}, {"cursor": "bad"}, {"q": "x" * 201}, {"project": "../x"}):
        with pytest.raises(Refused):
            view.conversation_list(**kw)
    for value in ({}, {"hidden": "yes"}, {"removed": False}):
        with pytest.raises(Refused):
            view.update_project("p" * 8, value)


def test_conversation_resume_preview_names_the_folder(catalog, monkeypatch):
    a = _repo(catalog / "farshell")
    cid = conv(cwd=a)
    view = _view(monkeypatch)
    import session_names
    monkeypatch.setattr(session_names, "allocate", lambda base: base + "-2")
    got = view.conversation_resume_preview(cid)
    assert got["resumable"] and got["folder"] == "farshell" and got["name"] == "farshell-2"


def test_project_routes_are_human_only_and_registered():
    from authz import route_table
    paths = {(m, p) for m, p, *_ in route_table._ROWS}
    for row in (("GET", "/api/session-catalog/projects"), ("GET", "/api/session-catalog/conversations"),
                ("POST", "/api/session-catalog/projects/{project_id}"),
                ("GET", "/api/session-history/conversations/{conversation_id}/resume-preview")):
        assert row in paths


def test_running_conversations_are_excluded_in_sql_so_pages_stay_full(catalog, monkeypatch):
    from session_catalog.db import database
    a = _repo(catalog / "a")
    t = time.time() - 100
    live = [conv(cwd=a, detail={"launch": "cli", "started": t, "last": t + 50 + i}) for i in range(3)]
    ended = [conv(cwd=a, detail={"launch": "cli", "started": t, "last": t + i}) for i in range(2)]
    with database(write=True) as db:   # 같은 대화 id가 여러 계정(scope)에 — 파이썬에서 거르면 쪽이 짧아졌다
        for n, cid in enumerate(live):
            db.execute("UPDATE conversations SET native_id='same',scope=? WHERE id=?", (f"s{n}", cid))
    place()
    view = _view(monkeypatch, running={"same"})
    first = view.conversation_list(limit=1)
    second = view.conversation_list(limit=1, cursor=first["next"])
    assert [c["id"] for c in first["conversations"] + second["conversations"]] == [ended[1], ended[0]]


def test_resume_preview_is_not_resumable_for_a_missing_folder(catalog, monkeypatch):
    import shutil
    a = _repo(catalog / "gone")
    cid = conv(cwd=a)
    shutil.rmtree(a)
    assert _view(monkeypatch).conversation_resume_preview(cid)["resumable"] is False
