"""세션 목록의 위치·브랜치는 터미널(셸)이 아니라 에이전트 CLI 기준이다.

Claude/Codex가 내부에서 별도 워크트리로 들어가도 pane의 cwd(셸)는 원래 저장소에 남는다.
훅이 알린 CLI의 cwd가 있으면 그걸로 Git을 읽고(`contextSource: "agent"`), 없거나 못 읽으면
pane cwd로 되돌아간다. `cwd`(pane)는 그대로 둔다 — 화면은 툴팁에 터미널 위치를 따로 보인다.
"""
import asyncio
import subprocess

import pytest

import agent_status
import routes.tmux as tmux_route
import session_context as context
import tmux_runner


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def trees(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    for c in (context._cache, context._workspace_cache, context._repo_cache):
        c.clear()
    agent_status._state.clear()
    main = tmp_path / "project"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "t@example.com")
    git(main, "config", "user.name", "t")
    (main / "f.txt").write_text("1\n")
    git(main, "add", ".")
    git(main, "commit", "-qm", "init")
    wt = tmp_path / "project-wt"
    git(main, "worktree", "add", "-q", "-b", "feat/task", str(wt))
    yield main.resolve(), wt.resolve()
    agent_status._state.clear()
    for c in (context._cache, context._workspace_cache, context._repo_cache):
        c.clear()


def _tmux(monkeypatch, pane_cwd):
    async def fake_run(args, timeout=None):
        if args[0] == "list-sessions":
            return "a\t1\t0\t\tlocal\n"
        return f"a\tclaude\t{pane_cwd}\t%1\t1\t1\n"
    monkeypatch.setattr(tmux_runner, "run_text_async", fake_run)


def _seed_agent(cwd):
    ent = agent_status._entry("s1", cwd)
    ent["tmux_session"] = "a"


def _row():
    return asyncio.run(tmux_route.collect_tmux_sessions())[0]


def test_agent_worktree_branch_wins_over_terminal(trees, monkeypatch):
    main, wt = trees
    _tmux(monkeypatch, main)
    _seed_agent(str(wt))
    row = _row()
    assert row["context"]["branch"] == "feat/task"
    assert row["context"]["worktree"]["kind"] == "linked"
    assert (row["contextSource"], row["contextCwd"]) == ("agent", str(wt))
    assert row["cwd"] == str(main), "pane cwd는 그대로 — 터미널 위치 툴팁의 근거"


def test_without_agent_the_terminal_cwd_is_used(trees, monkeypatch):
    main, wt = trees
    _tmux(monkeypatch, main)
    row = _row()
    assert row["context"]["branch"] == "main"
    assert (row["contextSource"], row["contextCwd"]) == ("terminal", str(main))


def test_same_cwd_is_reported_as_terminal(trees, monkeypatch):
    main, _ = trees
    _tmux(monkeypatch, main)
    _seed_agent(str(main))
    assert _row()["contextSource"] == "terminal"


def test_unreadable_agent_cwd_falls_back_to_terminal(trees, monkeypatch, tmp_path_factory):
    main, _ = trees
    _tmux(monkeypatch, main)
    outside = tmp_path_factory.mktemp("outside")          # VT_BROWSE_ROOTS 밖
    _seed_agent(str(outside))
    row = _row()
    assert row["context"]["branch"] == "main"
    assert (row["contextSource"], row["contextCwd"]) == ("terminal", str(main))


def test_missing_agent_cwd_falls_back_to_terminal(trees, monkeypatch):
    main, wt = trees
    _tmux(monkeypatch, main)
    _seed_agent(str(wt / "gone"))
    row = _row()
    assert (row["contextSource"], row["context"]["branch"]) == ("terminal", "main")


# --- 세션 로직 점검 단계 4: 자리(home)·위치 고정·화면 기준 접미사 -----------------------------------------

def _tmux_full(monkeypatch, pane_cwd, rows=(("a", "$1", "100"),)):
    async def fake_run(args, timeout=None):
        if args[0] == "list-sessions":
            return "".join(f"{n}\t1\t0\t\tlocal\t\t\t{sid}\t{created}\t\t\n" for n, sid, created in rows)
        return "".join(f"{n}\tclaude\t{pane_cwd}\t%{i}\t1\t1\n" for i, (n, _, _) in enumerate(rows))
    monkeypatch.setattr(tmux_runner, "run_text_async", fake_run)


def test_a_briefly_unreadable_agent_cwd_does_not_flip_to_the_terminal(trees, monkeypatch):
    main, wt = trees
    _tmux_full(monkeypatch, main)
    _seed_agent(str(wt))
    monkeypatch.setattr(tmux_route, "_LAST_CONTEXT", {})
    assert _row()["contextSource"] == "agent"
    real = context._context

    def busy(cwd):
        data = real(cwd)
        return {**data, "status": "unavailable", "reason": "busy"} if cwd == str(wt) else data
    monkeypatch.setattr(context, "_context", busy)
    row = _row()
    assert (row["contextSource"], row["context"]["branch"]) == ("agent", "feat/task"), "한 틱 바쁘다고 터미널 위치로 바뀌지 않는다"


def test_rows_carry_the_catalog_home_and_visible_name(trees, monkeypatch):
    main, _ = trees
    _tmux_full(monkeypatch, main)
    seen = []

    def fake_homes(keys):
        seen.append(keys)
        return {"$1:100": {"catalog_id": "c" * 32, "title": "결제 버그", "name_source": "human",
                           "home": {"project_id": "p", "workspace_id": "w", "root": str(main), "name": "project",
                                    "repository": True, "kind": "checkout", "path": str(main)}}}
    monkeypatch.setattr(tmux_route, "_catalog_homes", fake_homes)
    row = _row()
    assert seen == [["$1:100"]]
    assert row["home"]["name"] == "project" and row["catalog"] == {"catalog_id": "c" * 32, "title": "결제 버그", "name_source": "human"}


def test_rows_without_a_catalog_record_have_no_home(trees, monkeypatch):
    main, _ = trees
    _tmux_full(monkeypatch, main)
    monkeypatch.setattr(tmux_route, "_catalog_homes", lambda keys: {})
    row = _row()
    assert row["home"] is None and row["catalog"] is None and row["context"]["branch"] == "main"


def test_catalog_homes_reads_running_runtimes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    repo = home / "farshell"
    repo.mkdir()
    git(repo, "init", "-q")
    from session_catalog import db, inventory
    inventory.reconcile({"complete": True, "epoch": "e", "sessions": [
        {"physical_key": "$1:1", "tmux_id": "$1", "name": "farshell", "origin": "local", "cwd": str(repo), "panes": []}]})
    got = tmux_route._catalog_homes(["$1:1", "$9:9"])
    assert set(got) == {"$1:1"}
    assert got["$1:1"]["home"]["name"] == "farshell" and got["$1:1"]["home"]["repository"] is True
    assert got["$1:1"]["title"] == "farshell" and got["$1:1"]["name_source"] == "auto"
