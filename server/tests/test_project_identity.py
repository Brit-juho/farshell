"""저장소의 정체는 실경로 하나다 — 부팅마다 바뀌는 디스크 장치 번호가 열쇠에 끼면 같은 저장소가 둘로 갈라진다(2026-11 실측: 28개 경로가
68행). 열쇠에서 그 값을 빼고, DB가 같은 경로의 둘째 행을 거부하며, 옛 중복 행은 v4 마이그레이션이 한 번 합친다."""
import sqlite3
import subprocess
import types

import pytest


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db, inventory
    db.snapshot()
    monkeypatch.setattr(inventory, "_place_cache", {})
    monkeypatch.setattr(inventory, "_pending", {})
    monkeypatch.setattr(inventory, "SETTLE", 0.0)
    monkeypatch.setattr(inventory, "_agent_cli", lambda name: None)
    return home


def _git_repo(path):
    path.mkdir(parents=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(path), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, capture_output=True, env=env)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"],
                   cwd=path, check=True, capture_output=True, env=env)
    return path


def _item(name, cwd):
    return {"physical_key": "$1:1", "tmux_id": "$1", "name": name, "origin": "local", "cwd": str(cwd), "panes": []}


def _reconcile(item):
    from session_catalog import inventory
    inventory.reconcile({"complete": True, "epoch": "e", "sessions": [item]})


def _counts():
    from session_catalog.db import database
    with database() as con:
        return (con.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0])


def test_the_locator_has_no_device_number_or_inode(catalog):
    from session_catalog import inventory
    repo = _git_repo(catalog / "proj")
    placement = inventory.inspect_workspace(str(repo))
    assert placement["locator"] == f"git:{inventory.canonical_path(repo / '.git')}"
    assert placement["incarnation"] == str(repo.stat().st_ino)
    plain = catalog / "plain"
    plain.mkdir()
    assert inventory.inspect_workspace(str(plain))["locator"] == f"directory:{inventory.canonical_path(plain)}"


def test_a_changed_device_number_does_not_make_a_second_project(catalog, monkeypatch):
    """재부팅 뒤 장치 번호가 바뀌어도 같은 폴더는 같은 저장소·같은 작업 폴더 행이다."""
    import pathlib
    from session_catalog import inventory
    repo = _git_repo(catalog / "proj")
    _reconcile(_item("w", repo))
    first = _counts()
    assert first == (1, 1)

    real_stat = pathlib.Path.stat

    def other_device(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        fields = {n: getattr(st, n) for n in dir(st) if n.startswith("st_")}
        fields["st_dev"] = st.st_dev + 4
        return types.SimpleNamespace(**fields)

    monkeypatch.setattr(pathlib.Path, "stat", other_device)
    inventory._place_cache.clear()
    _reconcile(_item("w", repo))
    assert _counts() == first


def test_the_database_refuses_a_second_row_for_the_same_path(catalog):
    from session_catalog.db import database, host_id
    repo = _git_repo(catalog / "proj")
    _reconcile(_item("w", repo))
    with pytest.raises(Exception) as err:
        with database(write=True) as con:
            con.execute("INSERT INTO projects(id,host,locator,name,path,kind) SELECT 'x'||id,host,'git:other',name,path,kind FROM projects")
    assert "UNIQUE" in str(err.value) or "store_unavailable" in str(err.value)


def test_a_folder_that_becomes_a_repository_keeps_its_project_row(catalog):
    from session_catalog import inventory
    folder = catalog / "newproj"
    folder.mkdir()
    _reconcile(_item("w", folder))
    from session_catalog.db import database
    with database() as con:
        before = con.execute("SELECT id FROM projects WHERE name='newproj'").fetchone()[0]
    _git_repo_in_place(folder)
    inventory._place_cache.clear()
    _reconcile(_item("w", folder))
    with database() as con:
        rows = con.execute("SELECT id,kind,locator FROM projects WHERE name='newproj'").fetchall()
        wk = con.execute("SELECT kind FROM workspaces WHERE path=?", (str(inventory.canonical_path(folder)),)).fetchall()
    assert [r["id"] for r in rows] == [before] and rows[0]["kind"] == "git" and rows[0]["locator"].startswith("git:")
    assert [w["kind"] for w in wk] == ["checkout"]


def _git_repo_in_place(path):
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(path), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, capture_output=True, env=env)


def test_the_lowercase_spelling_of_a_folder_is_the_same_project(catalog):
    """대소문자를 구별하지 않는 파일 시스템(맥)에서 `/GitHub`와 `/gitHub`는 한 저장소다."""
    import sys
    if sys.platform != "darwin":
        pytest.skip("macOS 파일 시스템 동작")
    from session_catalog import inventory
    repo = _git_repo(catalog / "MixedCase")
    lowered = str(repo).replace("MixedCase", "mixedcase")
    a = inventory.inspect_workspace(str(repo))
    b = inventory.inspect_workspace(lowered)
    assert a["locator"] == b["locator"] and a["project_path"] == b["project_path"]


# ── v4 마이그레이션: 옛 중복 행 합치기 ────────────────────────────────────────────

def _v3_store(path):
    """v3 모양 저장소에 같은 경로가 장치 번호만 다른 세 행으로 갈라진 상태를 만든다."""
    from session_catalog import db
    con = sqlite3.connect(path)
    con.executescript("BEGIN;" + db.DDL + "PRAGMA user_version=1; COMMIT;")
    con.execute("BEGIN")
    for v in (2, 3):
        for statement in db.MIGRATIONS[v]:
            con.execute(statement)
    con.execute("PRAGMA user_version=3")
    con.commit()
    return con


def test_v4_merges_rows_that_differ_only_by_device_number(tmp_path):
    from session_catalog import db
    path = tmp_path / "catalog.sqlite3"
    con = _v3_store(path)
    for i, dev in enumerate((229, 230, 233)):
        con.execute("INSERT INTO projects(id,host,locator,name,path,kind,registered,last_activity) VALUES(?,?,?,?,?,?,?,?)",
                    (f"p{i}", "h", f"git:/nonexistent/repo/.git:{dev}:55", "repo", "/nonexistent/repo", "git",
                     100.0 if i == 1 else None, float(i)))
        con.execute("INSERT INTO workspaces(id,project_id,path,kind,incarnation) VALUES(?,?,?,?,?)",
                    (f"w{i}", f"p{i}", "/nonexistent/repo", "checkout", f"{dev}:77"))
        con.execute("INSERT INTO sessions(id,host,workspace_id,title,origin,created,updated) VALUES(?,?,?,?,?,?,?)",
                    (f"s{i}", "h", f"w{i}", f"s{i}", "local", 1.0, 1.0))
    con.execute("INSERT INTO projects(id,host,locator,name,path,kind) VALUES('other','h','git:/nonexistent/o/.git:229:1','o','/nonexistent/o','git')")
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("BEGIN")
    db.migrate(con, 3)
    con.commit()
    projects = con.execute("SELECT id,locator,registered,removed,last_activity FROM projects ORDER BY path").fetchall()
    assert [(p[1]) for p in projects] == ["git:/nonexistent/o/.git", "git:/nonexistent/repo/.git"]
    merged = next(p for p in projects if p[1].endswith("repo/.git"))
    assert merged[0] == "p1", "등록된 행이 살아남는다"
    assert merged[2] == 100.0 and merged[4] == 2.0
    works = con.execute("SELECT id,project_id,incarnation FROM workspaces WHERE project_id='p1'").fetchall()
    assert len(works) == 1 and works[0][2] == "77"
    assert {r[0] for r in con.execute("SELECT workspace_id FROM sessions")} == {works[0][0]}, "세션 참조가 옮겨진다"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO projects(id,host,locator,name,path,kind) VALUES('dup','h','git:z','repo','/nonexistent/repo','git')")


def test_v4_keeps_a_removed_project_removed_and_a_live_one_alive(tmp_path):
    from session_catalog import db
    con = _v3_store(tmp_path / "catalog.sqlite3")
    con.execute("INSERT INTO projects(id,host,locator,name,path,kind,registered,removed) VALUES"
                "('a','h','git:/nx/a/.git:1:2','a','/nx/a','git',10.0,20.0),('b','h','git:/nx/a/.git:3:2','a','/nx/a','git',10.0,20.0),"
                "('c','h','git:/nx/c/.git:1:2','c','/nx/c','git',10.0,20.0),('d','h','git:/nx/c/.git:3:2','c','/nx/c','git',15.0,NULL)")
    con.commit()
    con.execute("BEGIN")
    db.migrate(con, 3)
    con.commit()
    rows = {r[0]: r for r in con.execute("SELECT path,registered,removed FROM projects")}
    assert rows["/nx/a"][2] == 20.0, "둘 다 제거된 저장소는 제거된 채"
    assert rows["/nx/c"][2] is None and rows["/nx/c"][1] == 10.0, "한 행이라도 살아 있으면 살아 있고 등록은 가장 이른 시각"


def test_v4_on_an_empty_or_clean_store_changes_nothing(tmp_path):
    from session_catalog import db
    con = _v3_store(tmp_path / "catalog.sqlite3")
    con.execute("INSERT INTO projects(id,host,locator,name,path,kind) VALUES('a','h','git:/nx/a/.git','a','/nx/a','git')")
    con.commit()
    con.execute("BEGIN")
    db.migrate(con, 3)
    con.commit()
    assert con.execute("SELECT id,locator,revision FROM projects").fetchall() == [("a", "git:/nx/a/.git", 1)]
