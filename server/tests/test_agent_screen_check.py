"""만료 직전 화면 확인 — 사람이 답을 안 하고 있을 뿐인 질문이 TTL로 유휴가 되지 않게 한다.

2026-10-15 실서버(Final 세션): AskUserQuestion을 띄워 둔 채 17분이 지나자 waiting → working(2분) →
idle(15분) → 항목 삭제 순으로 만료돼, 화면은 질문인데 상태는 유휴였다.
"""

import pytest

import agent_prompt_detect as D
import agent_status as A

CHOICE_SCREEN = """\
설계안 아래 내용으로 진행할까요?
────────────────────────────────────────
←  ☐ MCP  ☐ 설계 승인  ✔ Submit  →

MCP 방식과 범위는 위 설계안대로 할까요?

❯ 1. 로컬 stdio (추천)
  2. 원격 HTTP MCP로
  3. Type something.
────────────────────────────────────────
  4. Chat about this

Enter to select · Tab/Arrow keys to navigate · Esc to cancel
"""

QUOTED_SCREEN = ("이 문구가 떠 있으면 질문입니다: Enter to select · Tab/Arrow keys to navigate\n"
                 + "\n".join(f"대화 {i}" for i in range(40))
                 + "\n❯ \n  Sonnet 5.5 | farshell\n")

BUSY_SCREEN = "일하는 중\n✻ Thinking… (12s · esc to interrupt)\n❯ \n"


@pytest.fixture(autouse=True)
def _reset():
    A._state.clear()
    A._recover_at.clear()
    yield
    A._state.clear()
    A.set_screen_probe(None)


def _waiting_entry(session="Final"):
    A.on_event("pre", {"session_id": "s1", "cwd": "/r", "tool_name": "AskUserQuestion"},
               session=session, agent="claude")
    ent = A._state["s1"]
    assert ent["status"] == A.WAITING and ent["_kind_src"] == "hook"
    return ent


def _expire(ent, seconds):
    ent["updated_at"] -= seconds
    ent["since"] -= seconds


# ── 화면 판정 ──────────────────────────────────────────────────────────────
def test_screen_state_sees_the_choice_ui():
    r = D.screen_state("claude", CHOICE_SCREEN)
    assert r["prompt"] is True and r["kind"] == "choice" and r["busy"] is False


def test_screen_state_ignores_a_quote_far_above_the_bottom():
    assert D.screen_state("claude", QUOTED_SCREEN)["prompt"] is False


def test_screen_state_busy_marker_after_prompt_means_the_prompt_is_gone():
    r = D.screen_state("claude", CHOICE_SCREEN + BUSY_SCREEN)
    assert r["prompt"] is False and r["busy"] is True


def test_screen_state_unknown_agent_is_never_a_prompt():
    assert D.screen_state("nope-cli", CHOICE_SCREEN) == {"prompt": False, "kind": None, "busy": False}


# ── waiting 만료 ───────────────────────────────────────────────────────────
def test_hook_waiting_survives_the_ttl_while_the_screen_still_asks():
    ent = _waiting_entry()
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    _expire(ent, 600)
    A.sweep()
    assert ent["status"] == A.WAITING
    A.sweep()                                   # 다음 확인 전까지는 다시 읽지 않는다(updated_at이 갱신됐다)
    assert ent["status"] == A.WAITING


def test_hook_waiting_drops_to_working_when_the_screen_no_longer_asks():
    ent = _waiting_entry()
    A.set_screen_probe(lambda n, a: {"prompt": False, "kind": None, "busy": False})
    _expire(ent, 600)
    A.sweep()
    assert ent["status"] == A.WORKING


def test_waiting_still_expires_when_the_screen_cannot_be_read():
    """확인할 수 없으면 예전 TTL 동작이다(일시적 읽기 실패가 영구 대기로 굳지 않게)."""
    ent = _waiting_entry()
    A.set_screen_probe(lambda n, a: None)
    _expire(ent, 600)
    A.sweep()
    assert ent["status"] == A.WORKING


def test_expired_wait_comes_back_when_the_working_check_sees_the_question():
    ent = _waiting_entry()
    A.set_screen_probe(lambda n, a: None)
    _expire(ent, 600)
    A.sweep()
    assert ent["status"] == A.WORKING
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    _expire(ent, 2000)
    A.sweep()
    assert ent["status"] == A.WAITING and ent["kind"] == "choice"


def test_waiting_has_a_hard_cap():
    ent = _waiting_entry()
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    _expire(ent, A.WAITING_HARD_CAP + 10)
    A.sweep()
    assert ent["status"] == A.WORKING


def test_probe_errors_fall_back_to_the_old_ttl_behavior():
    ent = _waiting_entry()
    ent["_kind_src"] = "screen"

    def boom(n, a):
        raise RuntimeError("tmux gone")
    A.set_screen_probe(boom)
    _expire(ent, 600)
    A.sweep()
    assert ent["status"] == A.WORKING


# ── working 만료 ───────────────────────────────────────────────────────────
def _working_entry():
    A.on_event("prompt", {"session_id": "s1", "cwd": "/r"}, session="Final", agent="claude")
    ent = A._state["s1"]
    assert ent["status"] == A.WORKING
    return ent


def test_working_is_not_expired_while_the_screen_shows_work():
    ent = _working_entry()
    A.set_screen_probe(lambda n, a: {"prompt": False, "kind": None, "busy": True})
    _expire(ent, 2000)
    A.sweep()
    assert ent["status"] == A.WORKING


def test_working_becomes_waiting_when_the_screen_shows_a_question():
    ent = _working_entry()
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    _expire(ent, 2000)
    A.sweep()
    assert ent["status"] == A.WAITING and ent["kind"] == "choice"


def test_working_still_expires_to_idle_when_the_screen_is_quiet():
    ent = _working_entry()
    A.set_screen_probe(lambda n, a: {"prompt": False, "kind": None, "busy": False})
    _expire(ent, 2000)
    A.sweep()
    assert ent["status"] == A.IDLE


# ── 서버가 모르는 세션 복구 ────────────────────────────────────────────────
def test_recover_revives_a_session_the_server_forgot():
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    assert A.recover_from_screens({"Final": "claude"}) == 1
    ent = next(e for e in A._state.values() if e.get("tmux_session") == "Final")
    assert ent["status"] == A.WAITING and ent["kind"] == "choice" and ent["agent"] == "claude"


def test_recover_skips_known_sessions_and_non_hook_agents():
    A.on_event("prompt", {"session_id": "s1", "cwd": "/r"}, session="Final", agent="claude")
    calls = []
    A.set_screen_probe(lambda n, a: calls.append(n) or {"prompt": True, "kind": "choice", "busy": False})
    assert A.recover_from_screens({"Final": "claude", "aider-1": "aider", "plain": None}) == 0
    assert calls == []


def test_recover_is_rate_limited_per_session():
    calls = []
    A.set_screen_probe(lambda n, a: calls.append(n) or {"prompt": False, "kind": None, "busy": False})
    A.recover_from_screens({"Final": "claude"}, now=1000.0)
    A.recover_from_screens({"Final": "claude"}, now=1010.0)
    assert len(calls) == 1
    A.recover_from_screens({"Final": "claude"}, now=1000.0 + A.RECOVER_INTERVAL + 1)
    assert len(calls) == 2


def test_a_later_hook_takes_over_a_recovered_placeholder():
    A.set_screen_probe(lambda n, a: {"prompt": True, "kind": "choice", "busy": False})
    A.recover_from_screens({"Final": "claude"})
    A.on_event("pre", {"session_id": "real", "cwd": "/r", "tool_name": "Bash"}, session="Final", agent="claude")
    assert A.status_for_session("Final") == A.WORKING
