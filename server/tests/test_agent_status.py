"""agent_status.on_event 의 cwd 전달 회귀.

그리드 뷰가 "어느 tmux 세션이 지금 작업 중인지"를 판정하려면 훅 이벤트를
cwd로 매칭해야 한다(서버에 hook session_id ↔ tmux 세션명 매핑이 따로 없음).
pre/post에서 state에 cwd가 쌓이는지, stop에서 state를 지우기 직전에 cwd만은
돌려주는지가 이 매칭의 전제 조건이다.
"""

import pytest

import agent_status


@pytest.fixture(autouse=True)
def _reset_state():
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def test_pre_stores_cwd():
    state = agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    assert state["cwd"] == "/repo/a"


def test_post_keeps_cwd_from_pre():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    state = agent_status.on_event("post", {"session_id": "s1"})
    assert state["cwd"] == "/repo/a"
    assert state["tool"] is None


def test_stop_keeps_cwd_and_entry_survives():
    """A1에서 계약이 바뀌었다 — stop이 엔트리를 지우지 않는다.

    이전에는 `_state.pop`이라 "done이라는 상태"가 서버에 남지 않았고, 그래서
    새로고침하면 done 배지가 사라졌다. 이제 엔트리를 유지하고 status만
    done으로 바꾼다(A5 복원의 전제). cwd는 여전히 그대로 실려 나온다.
    """
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    result = agent_status.on_event("stop", {"session_id": "s1"})
    assert result["cwd"] == "/repo/a"
    assert result["status"] == "done"
    assert agent_status.get_state("s1")["status"] == "done"


def test_stop_without_prior_pre_still_records_done():
    """도구를 하나도 안 쓴 응답(claude -p "ok" 같은)도 완료로 남아야 한다."""
    result = agent_status.on_event("stop", {"session_id": "never-seen"})
    assert result["status"] == "done"
    assert result["cwd"] is None


def test_stop_payload_cwd_wins_over_stale_state_cwd():
    """stop 페이로드에 cwd가 직접 오면(정상 케이스) 그걸 우선한다."""
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/old"})
    result = agent_status.on_event("stop", {"session_id": "s1", "cwd": "/repo/new"})
    assert result["cwd"] == "/repo/new"


def test_all_active_includes_cwd():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    active = agent_status.all_active()
    assert len(active) == 1
    assert active[0]["cwd"] == "/repo/a"
    assert active[0]["session_id"] == "s1"


# --- 호스트 차원 (N7/N39, 2026-09-13) -------------------------------------------
#
# 멀티호스트 2단계의 선행 조건. 맥 두 대에 똑같이 `dev` 세션이 있는 건 기본 이름이라
# 거의 확실히 일어나는 상황이고, 그때 원격 상태가 로컬 조회에 섞이면
#   - 로컬 `dev` 탭에 남의 승인 대기 배지가 뜨고
#   - **큐가 로컬 pane을 원격 상태 때문에 차단**한다(더 나쁜 쪽)
# 이 절은 그 두 가지가 구조적으로 불가능함을 고정한다.


def test_same_tmux_name_on_two_hosts_does_not_collide():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s-local", a.WORKING, session="dev")
    a.report("s-remote", a.WAITING, session="dev", host="gpu-box")

    assert a.status_for_session("dev") == a.WORKING            # 로컬 조회
    assert a.status_for_session("dev", host="gpu-box") == a.WAITING


def test_remote_waiting_does_not_block_local_queue():
    """큐 5번 관문이 보는 값 — 원격이 waiting이어도 로컬 투입은 막히면 안 된다."""
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s-local", a.IDLE, session="dev")
    a.report("s-remote", a.WAITING, session="dev", host="gpu-box")
    assert a.status_for_session("dev") != a.WAITING


def test_same_cwd_on_two_hosts_does_not_collide():
    """두 맥의 홈 경로는 사용자 이름이 같으면 문자열까지 똑같다."""
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    cwd = "/Users/neo/proj"
    a.report("s-local", a.WORKING, cwd=cwd)
    a.report("s-remote", a.WAITING, cwd=cwd, host="gpu-box")
    assert a.status_for_cwd(cwd) == a.WORKING
    assert a.status_for_cwd(cwd, host="gpu-box") == a.WAITING


def test_identical_sid_on_two_hosts_does_not_overwrite():
    """transcript_path는 두 맥에서 문자열까지 같을 수 있다 — 덮어쓰면 안 된다."""
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    sid = "/Users/neo/.claude/projects/x/session.jsonl"
    a.report(sid, a.WORKING)
    a.report(sid, a.DONE, host="gpu-box")
    assert a.get_status(sid) == a.WORKING
    assert a.get_status(sid, host="gpu-box") == a.DONE


def test_entries_carry_host_and_session_id():
    """_state의 키는 불투명하다 — 엔트리 필드가 유일한 식별 경로여야 한다."""
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s1", a.WORKING, host="gpu-box")
    ent = a.get_state("s1", host="gpu-box")
    assert ent["host"] == "gpu-box"
    assert ent["session_id"] == "s1"


def test_local_entries_default_to_local_host():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s1", a.WORKING)
    assert a.get_state("s1")["host"] == a.LOCAL_HOST


def test_ack_is_host_scoped():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s1", a.DONE)
    a.report("s1", a.DONE, host="gpu-box")
    a.ack("s1")
    assert a.get_status("s1") == a.IDLE
    assert a.get_status("s1", host="gpu-box") == a.DONE  # 원격은 그대로


def test_on_waiting_is_host_scoped():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    a.report("s1", a.WORKING)
    a.report("s1", a.WORKING, host="gpu-box")
    a.on_waiting("s1", True, host="gpu-box")
    assert a.get_status("s1") == a.WORKING
    assert a.get_status("s1", host="gpu-box") == a.WAITING


def test_on_event_is_host_scoped():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    payload = {"session_id": "s1", "cwd": "/x", "tool_name": "Bash"}
    a.on_event("pre", payload)
    a.on_event("pre", payload, host="gpu-box")
    a.on_event("stop", payload, host="gpu-box")
    assert a.get_status("s1") == a.WORKING       # 로컬은 아직 working
    assert a.get_status("s1", host="gpu-box") == a.DONE


def test_all_active_reports_host_per_entry():
    a = agent_status  # 모듈 직접 사용 — _reset_state가 autouse로 초기화한다
    payload = {"session_id": "s1", "cwd": "/x", "tool_name": "Bash"}
    a.on_event("pre", payload)
    a.on_event("pre", {**payload, "session_id": "s2"}, host="gpu-box")
    rows = {r["session_id"]: r["host"] for r in a.all_active()}
    assert rows == {"s1": a.LOCAL_HOST, "s2": "gpu-box"}
