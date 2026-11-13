"""백그라운드 셸 목록(bg_shells.py) — 시작·목록 맞추기·끝남·명령줄 노출 범위."""
import pytest

import agent_status as A
import bg_shells as B


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    A._state.clear()
    A._owners.clear()
    A._retired.clear()
    monkeypatch.setattr(A, "_probe", lambda ent: None)
    yield
    A._state.clear()
    A._owners.clear()
    A._retired.clear()


def event(kind, child=None, ts=None, **extra):
    payload = {"session_id": "parent", **extra}
    if child:
        payload["agent_id"] = child
    return A.on_event(kind, payload, session="sample", agent="claude", ts=ts)


def bg(cmd, desc="", tuid="toolu_1"):
    return {"tool_name": "Bash", "tool_use_id": tuid,
            "tool_input": {"command": cmd, "description": desc, "run_in_background": True}}


def shell(i, cmd, desc=""):
    return {"id": i, "type": "shell", "status": "running", "command": cmd, "description": desc}


def items(ent):
    return {r["id"]: r for r in B.public(ent)["items"]}


def test_foreground_bash_and_other_tools_make_no_rows():
    ent = {}
    assert not B.started(ent, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert not B.started(ent, {"tool_name": "Read", "tool_input": {"run_in_background": True}})
    assert B.public(ent) is None, "셸을 쓴 적 없는 세션은 화면에 아무것도 안 나간다"
    assert not B.fold(ent, {"background_tasks": []})
    assert B.public(ent) is None


def test_start_is_pending_then_the_roster_takes_over_with_the_start_time():
    ent = {}
    assert B.started(ent, bg("npm run e2e", "e2e 전체"), now=100)
    row = items(ent)["pending-toolu_1"]
    assert row["state"] == "running" and row["pending"] is True and row["description"] == "e2e 전체"
    assert B.fold(ent, {"background_tasks": [shell("b1", "npm run e2e", "e2e 전체"),
                                             {"id": "s1", "type": "subagent", "status": "running"}]}, now=130)
    rows = items(ent)
    assert list(rows) == ["b1"], "확인 전 행은 같은 명령줄의 목록 항목으로 넘어간다(서브에이전트 항목은 셸이 아니다)"
    assert rows["b1"]["started_at"] == 100 and rows["b1"]["pending"] is False
    assert B.public(ent)["checked_at"] == 130


def test_listed_then_missing_is_done_and_kept_for_the_ttl():
    ent = {}
    B.fold(ent, {"background_tasks": [shell("b1", "tail -f log"), shell("b2", "sleep 99")]}, now=10)
    B.fold(ent, {"background_tasks": [shell("b2", "sleep 99")]}, now=20)
    rows = items(ent)
    assert rows["b1"]["state"] == "done" and rows["b1"]["ended_at"] == 20
    assert rows["b2"]["state"] == "running"
    assert not B.sweep(ent, now=20 + B.TTL)
    assert B.sweep(ent, now=21 + B.TTL)
    assert list(items(ent)) == ["b2"], "실행 중인 셸은 TTL로 지우지 않는다"


def test_pending_that_finished_before_the_roster_is_done():
    ent = {}
    B.started(ent, bg("echo hi"), now=5)
    B.fold(ent, {"background_tasks": []}, now=6)
    row = items(ent)["pending-toolu_1"]
    assert row["state"] == "done" and row["pending"] is False


def test_bad_or_incomplete_rosters_change_nothing():
    ent = {}
    B.started(ent, bg("x"), now=1)
    before = items(ent)
    assert not B.fold(ent, {"background_tasks": "nope"})
    assert not B.fold(ent, {"background_tasks": [], "background_tasks_truncated": True})
    assert not B.fold(ent, {"background_tasks": [], "background_tasks_complete": False})
    assert items(ent) == before
    B.fold(ent, {"background_tasks": [{"id": "../x", "type": "shell"}, shell("ok", "x")]}, now=2)
    assert "../x" not in items(ent) and "ok" in items(ent)


def test_limit_evicts_ended_rows_first_and_never_running_ones():
    ent = {}
    B.fold(ent, {"background_tasks": [shell(f"r{i}", f"c{i}") for i in range(B.LIMIT)]}, now=1)
    B.fold(ent, {"background_tasks": [shell(f"r{i}", f"c{i}") for i in range(1, B.LIMIT)]}, now=2)   # r0 끝남
    assert B.started(ent, bg("new", tuid="toolu_new"), now=3), "끝난 행을 비워 자리를 만든다"
    assert "r0" not in items(ent)
    assert not B.started(ent, bg("more", tuid="toolu_more"), now=4), "전부 실행 중이면 새 행을 넣지 않는다"


def test_text_is_clipped_to_one_line():
    ent = {}
    B.started(ent, bg("a\n" + "b" * 1000, "d\tx" + "y" * 500), now=1)
    row = items(ent)["pending-toolu_1"]
    assert "\n" not in row["command"] and len(row["command"]) == B.CMD_MAX
    assert row["description"].startswith("d x") and len(row["description"]) == B.DESC_MAX


def test_hook_flow_post_stop_and_subagent_stop_fold_into_the_parent():
    event("session_start", ts=10, source="startup")
    event("pre", ts=11, **bg("npm test", "단위 테스트"))
    event("post", ts=12, **bg("npm test", "단위 테스트"))
    pub = A.public_entry(A.get_state("parent"))
    assert [r["state"] for r in pub["shells"]["items"]] == ["running"]
    event("subagent_start", "child", ts=13)
    # 서브에이전트가 띄운 셸도 부모 세션 범위 — SubagentStop 목록으로 맞춘다.
    event("subagent_stop", "child", ts=14, background_tasks=[shell("b1", "npm test", "단위 테스트"), shell("b2", "make", "빌드")])
    pub = A.public_entry(A.get_state("parent"))
    assert [r["id"] for r in pub["shells"]["items"]] == ["b1", "b2"]
    event("stop", ts=15, background_tasks=[shell("b2", "make", "빌드")])
    pub = A.public_entry(A.get_state("parent"))
    assert {r["id"]: r["state"] for r in pub["shells"]["items"]} == {"b1": "done", "b2": "running"}
    # 새 대화(session_start)면 비운다.
    event("session_start", ts=16, source="clear")
    assert "shells" not in A.public_entry(A.get_state("parent"))


def test_commands_stay_on_this_mac():
    event("session_start", ts=10, source="startup")
    event("stop", ts=11, background_tasks=[shell("b1", "deploy --token secret", "배포")])
    pub = A.public_entry(A.get_state("parent"))
    assert pub["shells"]["items"][0]["command"] == "deploy --token secret"
    remote = A.remote_view(pub)
    assert remote["shells"]["items"][0] == {k: v for k, v in pub["shells"]["items"][0].items() if k != "command"}
    assert "secret" not in repr(remote)
    assert pub["shells"]["items"][0]["command"] == "deploy --token secret", "원본은 건드리지 않는다"
