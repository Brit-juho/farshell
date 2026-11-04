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
