"""세션 소유자 · SessionEnd 반납 · waiting 유지 · 변경 알림.

실서버에서 한 tmux 세션에 엔트리 셋(지금 세션 working + `/clear` 전 세션 done 둘)이
붙어 세션이 `done`으로 보였고, 질문 대기 중 서브에이전트의 post 하나로 waiting이 풀렸다.
"""

import time

import pytest

import agent_status as A


@pytest.fixture(autouse=True)
def _reset():
    A._state.clear()
    events = []
    A.subscribe(rec := lambda ent, reason: events.append((reason, ent)))
    yield events
    A.unsubscribe(rec)
    A._state.clear()


def _ev(event, sid, session="dev", **payload):
    return A.on_event(event, {"session_id": sid, "cwd": "/r", **payload}, session=session, agent="claude")


# ── 소유자 ─────────────────────────────────────────────────────────────────
def test_clear_retires_previous_session_so_old_done_cannot_win():
    _ev("pre", "old", tool_name="Bash")
    _ev("stop", "old")
    _ev("session_start", "new", source="clear")
    _ev("pre", "new", tool_name="Bash")
    assert A.status_for_session("dev") == A.WORKING
    assert A.known_status_for_session("dev") == A.WORKING
    assert A.get_state("old") == {}, "은퇴한 엔트리는 지운다"


def test_late_event_from_retired_session_is_ignored():
    _ev("pre", "old", tool_name="Bash")
    _ev("session_start", "new", source="clear")
    assert _ev("stop", "old") is None
    assert A.status_for_session("dev") == A.IDLE
    assert A.get_state("old") == {}


def test_resume_revives_a_retired_conversation():
    _ev("pre", "a", tool_name="Bash")
    _ev("session_start", "b", source="clear")
    _ev("session_start", "a", source="resume")
    assert _ev("pre", "a", tool_name="Read")["status"] == A.WORKING
    assert A.get_state("b") == {}


def test_non_owner_events_do_not_touch_the_session():
    """같은 pane의 TMUX_PANE을 물려받은 `claude -p` 같은 자식의 훅."""
    _ev("pre", "main", tool_name="Bash")
    assert _ev("pre", "child", tool_name="Read") is None
    assert _ev("stop", "child") is None
    assert A.status_for_session("dev") == A.WORKING
    assert A.get_state("child") == {}


def test_startup_from_child_while_owner_busy_does_not_take_over():
    _ev("pre", "main", tool_name="Bash")
    assert _ev("session_start", "child", source="startup") is None
    assert A.status_for_session("dev") == A.WORKING
    assert _ev("stop", "main")["status"] == A.DONE


def test_startup_after_owner_idle_takes_over():
    _ev("session_start", "a", source="startup")
    _ev("stop", "a")
    assert _ev("session_start", "b", source="startup")["status"] == A.IDLE
    assert A.get_state("a") == {}


def test_owner_is_per_session():
    _ev("pre", "a", session="one", tool_name="Bash")
    _ev("pre", "b", session="two", tool_name="Bash")
    _ev("stop", "a", session="one")
    assert A.status_for_session("one") == A.DONE
    assert A.status_for_session("two") == A.WORKING


def test_screen_placeholder_is_dropped_when_a_hook_claims_the_session():
    A.on_waiting("pane:dev", True, question="q?", session="dev")
    assert A.status_for_session("dev") == A.WAITING
    _ev("pre", "s", tool_name="Bash")
    assert A.get_state("pane:dev") == {}
    assert A.status_for_session("dev") == A.WORKING


def test_visible_state_hides_non_owner_entries():
    _ev("pre", "s", tool_name="Bash")
    A.report("pane:other", "done", session="dev")   # 자기보고 엔트리 — 소유자가 있으면 가린다
    vis = A.visible_state()
    assert "s" in vis and "pane:other" not in vis


def test_unattributed_events_still_update_their_entry():
    A.on_event("pre", {"session_id": "x", "tool_name": "Bash"})
    assert A.get_status("x") == A.WORKING


# ── SessionEnd = 반납 ─────────────────────────────────────────────────────
def test_session_end_releases_instead_of_done(_reset):
    _ev("pre", "s", tool_name="Bash")
    ent = _ev("session_end", "s", reason="clear")
    assert ent["released"] is True and ent["status"] == A.IDLE
    assert A.status_for_session("dev") == A.IDLE
    assert A.known_status_for_session("dev") is None
    assert any(r == "release" and e["session_id"] == "s" and e["tmux_session"] == "dev"
               for r, e in _reset)


def test_session_end_from_non_owner_is_ignored():
    _ev("pre", "main", tool_name="Bash")
    assert _ev("session_end", "child") is None
    assert A.status_for_session("dev") == A.WORKING


# ── waiting 유지 ─────────────────────────────────────────────────────────
def _ask(sid="s"):
    _ev("pre", sid, tool_name="AskUserQuestion", tool_use_id="t1",
        tool_input={"questions": [{"question": "Pick", "options": [{"label": "red"}]}]})


def test_claude_ask_user_question_pre_raises_waiting():
    _ask()
    ent = A.get_state("s")
    assert ent["status"] == A.WAITING and ent["kind"] == A.KIND_CHOICE
    assert ent["question"] == "Pick" and ent["options"] == [{"key": "1", "label": "red"}]


def test_exit_plan_mode_pre_raises_plan_waiting():
    _ev("pre", "s", tool_name="ExitPlanMode", tool_use_id="p1")
    assert A.get_state("s")["kind"] == A.KIND_PLAN


def test_permission_after_pre_keeps_the_prompt_token():
    _ask()
    pid = A.get_state("s")["prompt_id"]
    _ev("permission", "s", tool_name="AskUserQuestion",
        tool_input={"questions": [{"question": "Pick", "options": [{"label": "red"}]}]})
    ent = A.get_state("s")
    assert ent["status"] == A.WAITING and ent["prompt_id"] == pid
    assert ent["_wait_tuid"] == "t1", "PermissionRequest는 직전 pre의 tool_use_id를 이어받는다"


def test_subagent_events_do_not_clear_waiting():
    _ask()
    _ev("pre", "s", tool_name="Read", tool_use_id="x1", agent_id="sub1")
    _ev("post", "s", tool_name="Read", tool_use_id="x1", agent_id="sub1")
    assert A.status_for_session("dev") == A.WAITING
    assert A.get_state("s")["question"] == "Pick"


def test_sibling_tool_does_not_clear_waiting():
    _ask()
    _ev("post", "s", tool_name="Read", tool_use_id="t2")
    _ev("pre", "s", tool_name="Grep", tool_use_id="t3")
    assert A.status_for_session("dev") == A.WAITING


def test_same_tool_post_clears_waiting():
    _ask()
    ent = _ev("post", "s", tool_name="AskUserQuestion", tool_use_id="t1")
    assert ent["status"] == A.WORKING and ent["question"] is None


def test_prompt_and_stop_clear_waiting():
    _ask()
    assert _ev("stop", "s")["status"] == A.DONE
    _ask()
    assert _ev("prompt", "s")["status"] == A.WORKING


def test_waiting_without_tool_ids_keeps_legacy_release():
    """옛 CLI(tool_use_id·agent_id 없음)는 예전처럼 메인의 다음 pre/post가 푼다."""
    _ev("permission", "s", tool_name="Bash")
    assert _ev("post", "s")["status"] == A.WORKING


def test_subagent_permission_waits_and_its_own_tool_releases():
    _ev("pre", "s", tool_name="Task", tool_use_id="m1")
    _ev("pre", "s", tool_name="Bash", tool_use_id="b1", agent_id="sub1")
    _ev("permission", "s", tool_name="Bash", agent_id="sub1")
    assert A.status_for_session("dev") == A.WAITING
    _ev("post", "s", tool_name="Read", tool_use_id="r9")            # 메인 쪽 이벤트
    assert A.status_for_session("dev") == A.WAITING
    _ev("post", "s", tool_name="Bash", tool_use_id="b1", agent_id="sub1")
    assert A.status_for_session("dev") == A.WORKING


def test_subagent_tools_do_not_revive_a_finished_session():
    _ev("pre", "s", tool_name="Bash", tool_use_id="m1")
    _ev("stop", "s")
    _ev("pre", "s", tool_name="Read", tool_use_id="x1", agent_id="bg")
    assert A.status_for_session("dev") == A.DONE


# ── 변경 알림 ─────────────────────────────────────────────────────────────
def test_screen_waiting_change_is_emitted_once(_reset):
    _ev("pre", "s", tool_name="Bash")
    _reset.clear()
    A.on_waiting("s", True, question="Proceed?", options=[{"key": "1", "label": "Yes"}])
    A.on_waiting("s", True, question="Proceed?", options=[{"key": "1", "label": "Yes"}])
    assert [r for r, _ in _reset] == ["screen"], "같은 화면을 다시 그려도 한 번만"
    A.on_waiting("s", False)
    assert [r for r, _ in _reset] == ["screen", "screen"]
    assert _reset[-1][1]["status"] == A.WORKING


def test_interrupt_and_retire_are_emitted(_reset):
    _ev("pre", "s", tool_name="Bash")
    _reset.clear()
    A.finish_interrupted_for_session("dev")
    assert _reset[-1][0] == "interrupt" and _reset[-1][1]["status"] == A.DONE
    _ev("session_start", "n", source="clear")
    assert any(r == "release" and e["session_id"] == "s" and e["released"] for r, e in _reset)


def test_emitted_entries_are_public(_reset):
    _ask()
    A.finish_waiting_for_session("dev")
    assert all(not k.startswith("_") for _, e in _reset for k in e)


def test_ttl_transitions_are_emitted(_reset, monkeypatch):
    monkeypatch.setenv("VT_AGENT_TTL_WORKING", "10")
    _ev("pre", "s", tool_name="Bash")
    A._state["s"]["updated_at"] = time.time() - 60
    _reset.clear()
    A.sweep()
    assert _reset and _reset[0][0] == "ttl" and _reset[0][1]["status"] == A.IDLE


# ── P1: tmux 세션 대조 · PTY의 현재 세션 ──────────────────────────────────
def test_reconcile_follows_rename_and_moves_the_owner(_reset):
    _ev("pre", "s", session="old", tool_name="Bash")
    A.reconcile_sessions([("$1", "old")])          # 첫 대조 — tmux id를 새긴다
    _reset.clear()
    assert A.reconcile_sessions([("$1", "new")]) == 1
    assert A.status_for_session("new") == A.WORKING
    assert A.status_for_session("old") == A.IDLE
    assert _reset[-1][0] == "rename" and _reset[-1][1]["tmux_session"] == "new"
    assert _ev("stop", "s", session="new")["status"] == A.DONE, "새 이름에서도 소유자 그대로"


def test_reconcile_retires_entries_of_closed_sessions():
    _ev("stop", "s", session="dev")
    A.reconcile_sessions([("$1", "dev")])
    A.reconcile_sessions([("$2", "dev")])          # 닫히고 같은 이름으로 새로 생겼다
    assert A.get_state("s") == {}
    assert A.status_for_session("dev") == A.IDLE, "새 세션이 옛 상태를 물려받지 않는다"


def test_reconcile_retires_unseen_names_and_keeps_remote_hosts():
    _ev("stop", "s", session="gone")
    A.report("peer:x", "working", session="gone", host="mac2")
    A.reconcile_sessions([("$1", "dev")])
    assert A.get_state("s") == {}
    assert A.status_for_session("gone", host="mac2") == A.WORKING


def test_pty_moved_to_another_session_is_attributed_there(monkeypatch):
    """`detach-on-destroy off`·switch-client로 웹 PTY의 클라이언트가 옮겨 간 경우."""
    import deps
    from types import SimpleNamespace
    monkeypatch.setattr(deps.pty_mgr, "_sessions", {"w1": SimpleNamespace(tty="/dev/ttys009")})
    deps.session_store.add("w1", name="alpha")
    deps.session_store.update_tmux_name("w1", "alpha")
    removed = []
    monkeypatch.setattr(deps._prompt_detector, "remove", lambda sid: removed.append(sid))
    try:
        assert deps.tmux_for_pty("w1") == "alpha"
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "beta"})
        assert deps.tmux_for_pty("w1") == "beta" and removed == ["w1"]
        assert deps.session_store.get("w1").tmux_name == "alpha", "프런트가 보는 값은 그대로"
        _ev("pre", "s", session="beta", tool_name="Bash")
        deps._on_interrupt("w1")
        assert A.status_for_session("beta") == A.DONE
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "alpha"})
        assert deps.tmux_for_pty("w1") == "alpha" and removed == ["w1", "w1"]
    finally:
        deps.session_store.remove("w1")
        deps._pty_tmux_now.clear()


def test_reconcile_follows_rename_of_an_entry_created_after_the_last_check():
    A.reconcile_sessions([("$1", "old")])
    _ev("pre", "s", session="old", tool_name="Bash")   # 아직 tmux id가 없다
    A.reconcile_sessions([("$1", "new")])
    assert A.status_for_session("new") == A.WORKING


def _pty_fixture(monkeypatch, destroyed):
    import deps
    from types import SimpleNamespace
    monkeypatch.setattr(deps.pty_mgr, "_sessions", {"w1": SimpleNamespace(tty="/dev/ttys009")})
    monkeypatch.setattr(deps.pty_mgr, "destroy_session", lambda sid: destroyed.append(sid))
    deps.session_store.add("w1", name="alpha")
    deps.session_store.update_tmux_name("w1", "alpha")
    return deps


def _cleanup(deps):
    deps.session_store.remove("w1")
    deps._pty_tmux_now.clear()
    deps._pty_tmux_uid.clear()


def test_pty_is_closed_when_its_original_session_ended(monkeypatch):
    """원래 세션이 종료돼 `detach-on-destroy off`가 클라이언트를 다른 세션으로 옮기면 웹 PTY를 닫아 4004로 끝나게 한다."""
    destroyed = []
    deps = _pty_fixture(monkeypatch, destroyed)
    try:
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "alpha"})   # 붙어 있음을 확인
        assert deps._pty_tmux_uid["w1"] == "$1"
        deps.apply_tmux_state([("$2", "beta")], {"/dev/ttys009": "beta"})                     # alpha 종료, beta로 이동
        assert destroyed == ["w1"]
        assert "w1" not in deps._pty_tmux_uid and "w1" not in deps._pty_tmux_now
    finally:
        _cleanup(deps)


def test_pty_is_not_closed_when_the_user_switched_clients_on_purpose(monkeypatch):
    destroyed = []
    deps = _pty_fixture(monkeypatch, destroyed)
    try:
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "alpha"})
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "beta"})    # alpha는 살아 있다
        assert destroyed == []
        assert deps.tmux_for_pty("w1") == "beta"
    finally:
        _cleanup(deps)


def test_pty_is_not_closed_by_a_rename(monkeypatch):
    destroyed = []
    deps = _pty_fixture(monkeypatch, destroyed)
    try:
        deps.apply_tmux_state([("$1", "alpha")], {"/dev/ttys009": "alpha"})
        # 훅이 API 응답보다 먼저 와서 info.tmux_name은 아직 옛 이름이다 — id가 그대로면 종료가 아니다.
        deps.apply_tmux_state([("$1", "renamed")], {"/dev/ttys009": "renamed"})
        assert destroyed == []
        deps.session_store.update_tmux_name("w1", "renamed")
        deps.apply_tmux_state([("$1", "renamed")], {"/dev/ttys009": "renamed"})
        assert destroyed == [] and deps._pty_tmux_uid["w1"] == "$1"
    finally:
        _cleanup(deps)


def test_pty_is_closed_when_a_new_session_reuses_the_dead_name(monkeypatch):
    destroyed = []
    deps = _pty_fixture(monkeypatch, destroyed)
    try:
        deps.apply_tmux_state([("$1", "alpha"), ("$2", "beta")], {"/dev/ttys009": "alpha"})
        # alpha($1)가 끝나고 같은 이름의 새 세션($3)이 생겼다. 클라이언트는 beta로 옮겨 갔다.
        deps.apply_tmux_state([("$2", "beta"), ("$3", "alpha")], {"/dev/ttys009": "beta"})
        assert destroyed == ["w1"], "이름이 아니라 id로 판정한다"
    finally:
        _cleanup(deps)


def test_pty_without_a_recorded_id_is_left_alone(monkeypatch):
    """원래 세션에 붙어 있음을 한 번도 못 봤으면(만들자마자 종료 등) 오판해 닫지 않는다."""
    destroyed = []
    deps = _pty_fixture(monkeypatch, destroyed)
    try:
        deps.apply_tmux_state([("$2", "beta")], {"/dev/ttys009": "beta"})
        assert destroyed == []
        assert deps.tmux_for_pty("w1") == "beta"
    finally:
        _cleanup(deps)
