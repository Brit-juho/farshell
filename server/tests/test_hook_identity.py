"""hook_identity — 훅 프로세스가 그 pane의 대화형 에이전트인가 (docs/plan-2.4/60 P2).

프로세스 표 모양은 2026-10-13 실측을 따른다: pane 셸(zsh) 아래 claude가 포그라운드 그룹
리더(PGID == PID == TPGID)이고, MCP 서버는 claude의 그룹을 물려받으며, Bash 도구·훅은 tty
없는 별도 그룹(TPGID 0)으로 뜬다.
"""

import json

import pytest

import agent_status as A
import hook_identity as H
import pane_resolve
import tmux_runner
from tmux_runner import PaneInfo

PANE_PID = 100
# pid: (ppid, pgid, tpgid, comm)
BASE = {
    PANE_PID: (1, PANE_PID, 200, "-zsh"),
    200: (PANE_PID, 200, 200, "claude"),                  # 대화형 claude — 포그라운드 리더
    210: (200, 210, 0, "/bin/zsh"),                       # 훅을 돌리는 셸(별도 그룹, tty 없음)
    211: (210, 210, 0, "/bin/bash"),                      # agent_hook.sh
    300: (200, 200, 200, "node"),                         # MCP 서버 — claude의 그룹을 물려받음
    301: (300, 200, 200, "claude"),                       # MCP가 띄운 claude -p
    302: (301, 302, 0, "/bin/bash"),                      # 그 claude -p의 훅
    400: (200, 400, 0, "/bin/zsh"),                       # Bash 도구 셸
    401: (400, 400, 0, "claude"),                         # Bash 도구로 띄운 claude -p
    402: (401, 402, 0, "/bin/bash"),                      # 그 훅
    500: (1, 500, 0, "codex"),                            # 데몬(조상에 pane 없음)
    501: (500, 501, 0, "/bin/bash"),
}


def _c(hook_pid, table=BASE, session_id=None):
    return H.classify(hook_pid, "%1", session_id, table=table, pane_pid=PANE_PID)


def test_interactive_agent_in_the_pane_is_ok():
    assert _c(211) == (H.OK, 200)


def test_claude_p_started_by_an_mcp_server_is_a_child():
    assert _c(302) == (H.CHILD, 301)


def test_claude_p_started_from_the_bash_tool_is_a_child():
    assert _c(402) == (H.CHILD, 401)


def test_daemon_outside_the_pane_is_foreign():
    assert _c(501) == (H.FOREIGN, 500)


def test_shell_wrapper_leading_the_group_is_still_ok():
    t = dict(BASE)
    t[200] = (150, 150, 150, "claude")                     # 래퍼 셸 스크립트가 그룹 리더
    t[150] = (PANE_PID, 150, 150, "/bin/bash")
    assert _c(211, t) == (H.OK, 200)


def test_child_launched_directly_by_another_agent_is_a_child():
    t = dict(BASE)
    t[600] = (200, 200, 200, "claude")                     # claude가 직접 띄운 claude -p
    t[601] = (600, 601, 0, "/bin/bash")
    assert _c(601, t) == (H.CHILD, 600)


def test_background_group_is_a_child():
    t = dict(BASE)
    t[200] = (PANE_PID, 200, 999, "claude")                # 리더지만 포그라운드가 아니다(&)
    assert _c(211, t) == (H.CHILD, 200)


def test_claude_session_file_confirms_even_inside_a_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "301.json").write_text(
        json.dumps({"pid": 301, "sessionId": "abc", "kind": "interactive", "entrypoint": "cli"}))
    assert _c(302, session_id="abc") == (H.OK, 301)
    assert _c(302, session_id="other") == (H.CHILD, 301)


def test_claude_p_session_file_does_not_confirm(tmp_path, monkeypatch):
    """`claude -p`도 kind=interactive로 쓰지만 entrypoint가 sdk-cli다(2026-10-13 실측)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "401.json").write_text(
        json.dumps({"pid": 401, "sessionId": "p1", "kind": "interactive", "entrypoint": "sdk-cli"}))
    assert _c(402, session_id="p1") == (H.CHILD, 401)


def test_broken_session_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "200.json").write_text("{nope")
    assert _c(211, session_id="x") == (H.OK, 200)


@pytest.mark.parametrize("hook_pid", [None, "211", True, 0, 1, 99999])
def test_unknown_when_it_cannot_tell(hook_pid):
    assert _c(hook_pid)[0] == H.UNKNOWN


def test_unknown_when_ps_fails(monkeypatch):
    monkeypatch.setattr(H, "_ps_table", lambda: None)
    assert H.classify(211, "%1", pane_pid=PANE_PID) == (H.UNKNOWN, None)


def test_parses_real_ps_output_shape(monkeypatch):
    out = ("  100     1   100   200 -zsh\n  200   100   200   200 claude\n"
           "  210   200   210     0 /bin/zsh\n  211   210   210     0 /bin/bash\n")

    class R:
        returncode = 0
        stdout = out
    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: R())
    assert H.classify(211, "%1", pane_pid=PANE_PID) == (H.OK, 200)


# ── agent_status의 verified 규칙 ────────────────────────────────────────────
def _ev(event, sid, verified=None, **payload):
    return A.on_event(event, {"session_id": sid, **payload}, session="dev", agent="claude",
                      verified=verified)


@pytest.fixture(autouse=True)
def _clear():
    A._state.clear()
    yield
    A._state.clear()


def test_verified_agent_takes_over_even_from_a_busy_owner():
    """비정상 종료로 반납 없이 남은 working 소유자를 새로 띄운 claude가 넘겨받는다."""
    _ev("pre", "crashed", tool_name="Bash")
    assert _ev("session_start", "fresh", verified=True, source="startup")["status"] == A.IDLE
    assert A.get_state("crashed") == {}


def test_verified_agent_with_a_new_session_id_takes_over_without_session_start():
    _ev("pre", "a", tool_name="Bash")
    assert _ev("pre", "b", verified=True, tool_name="Read")["status"] == A.WORKING
    assert A.get_state("a") == {}


def test_child_never_takes_over_even_when_owner_is_idle():
    _ev("session_start", "main", verified=True, source="startup")
    assert _ev("session_start", "kid", verified=False, source="startup") is None
    assert _ev("stop", "kid", verified=False) is None
    assert A.status_for_session("dev") == A.IDLE


def test_child_without_an_owner_still_counts():
    """래퍼로 띄운 진짜 에이전트를 자식으로 오판해도 소유자가 없는 세션에서는 동작한다."""
    assert _ev("pre", "only", verified=False, tool_name="Bash")["status"] == A.WORKING


def test_late_event_is_dropped_by_timestamp():
    A.on_event("stop", {"session_id": "s"}, session="dev", ts=2000)
    assert A.on_event("pre", {"session_id": "s", "tool_name": "Bash"}, session="dev", ts=1000) is None
    assert A.status_for_session("dev") == A.DONE
    assert A.on_event("pre", {"session_id": "s", "tool_name": "Bash"}, session="dev", ts=3000)["status"] == A.WORKING


# ── pane_resolve ───────────────────────────────────────────────────────────
@pytest.fixture
def panes(monkeypatch):
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", "fsh")
    monkeypatch.delenv("VT_TMUX_SOCKET", raising=False)
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: [
        PaneInfo(session="one", command="zsh", pid=1, path="/repo", pane_id="%1"),
        PaneInfo(session="two", command="codex", pid=2, path="/repo", pane_id="%2"),
    ])


def test_claude_without_pane_is_not_attributed_by_cwd(panes, monkeypatch):
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: [
        PaneInfo(session="one", command="zsh", pid=1, path="/solo", pane_id="%1")])
    assert pane_resolve.resolve(None, None, "/solo", cwd_fallback=False) == (None, "no-pane")
    assert pane_resolve.resolve(None, None, "/solo") == ("one", "cwd"), "자기보고는 예전 규칙"


def test_codex_cwd_prefers_the_pane_running_codex(panes):
    assert pane_resolve.resolve("%1", "/tmp/tmux-501/fsh,1,0", "/repo", agent="codex") == ("two", "cwd")


def test_verified_codex_pane_is_trusted(panes):
    assert pane_resolve.resolve("%1", "/tmp/tmux-501/fsh,1,0", "/repo", agent="codex",
                                pane_verified=True) == ("one", "pane")


# ── Codex 「Action Required」 제목 ───────────────────────────────────────────
def _codex(sid="c"):
    return A.on_event("pre", {"session_id": sid, "tool_name": "Bash"}, session="dev", agent="codex")


def test_codex_action_required_title_raises_and_clears_waiting():
    _codex()
    A.apply_titles({"dev": "[ ! ] Action Required | repo | Working"})
    ent = A.get_state("c")
    assert ent["status"] == A.WAITING and ent["kind"] == A.KIND_QUESTION
    A.apply_titles({"dev": "[ . ] Action Required | repo"})          # 깜빡임 — 그대로
    assert A.get_state("c")["status"] == A.WAITING
    A.apply_titles({"dev": "⠦ repo"})            # 작업 중 제목에는 스피너가 붙는다(0.160.1 실측)
    assert A.get_state("c")["status"] == A.WORKING


def test_title_does_not_clear_hook_waiting_or_touch_other_agents():
    _codex()
    A.on_event("permission", {"session_id": "c", "tool_name": "Bash"}, session="dev", agent="codex")
    A.apply_titles({"dev": "repo"})
    assert A.get_state("c")["status"] == A.WAITING, "훅이 만든 대기는 제목으로 풀지 않는다"
    A.on_event("pre", {"session_id": "k", "tool_name": "Bash"}, session="other", agent="claude")
    A.apply_titles({"other": "[ ! ] Action Required"})
    assert A.get_state("k")["status"] == A.WORKING


def test_title_releases_hook_waiting_only_after_it_was_seen():
    _codex()
    A.on_event("permission", {"session_id": "c", "tool_name": "Bash"}, session="dev", agent="codex")
    A.apply_titles({"dev": "[ ! ] Action Required | repo"})
    A.apply_titles({"dev": "⠦ repo"})                                 # 승인 → 작업 이어짐
    assert A.get_state("c")["status"] == A.WORKING
    A.on_event("permission", {"session_id": "c", "tool_name": "Bash"}, session="dev", agent="codex")
    A.apply_titles({"dev": "[ . ] Action Required | repo"})
    A.apply_titles({"dev": "repo"})                                   # 거절·ESC — 훅 없이 턴 끝
    ent = A.get_state("c")
    assert ent["status"] == A.DONE and ent["question"] is None
