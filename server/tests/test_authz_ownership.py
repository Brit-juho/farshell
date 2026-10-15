"""소유 무결성 — 위임의 부모-자식 간선은 만든 뒤 바뀌지 않고, 제어는 그 위임의 엄격한 부모만 한다.

  - 장부 쓰기는 `parent.key`·`child.key`(채워진 뒤)·`id` 를 바꾸는 순간 감사하고 거부한다.
  - `deleg.control`(보내기·닫기)은 그 위임의 부모만: 자기 자신(자식)·형제·다른 부모의 자식·상위 조상은 안 된다.
  - 소유 키는 `session_created` 를 품는다 — 같은 이름으로 다시 만든 세션이나 이어받은 대화(새 세션)는 다른 키라 소유를 만들거나 가져가지 못한다.
  - 승인 비트 = 직계 한 단계: 깊이 제한이 풀려도 조부모는 손자를 제어하지 못한다.

꺼짐/켜짐 둘 다에서 같은 결과여야 한다.
"""

from __future__ import annotations

import json

import pytest

import delegation
import queue_store
import vt_paths
from authz import audit

from test_delegation import _caller, tm  # noqa: F401  (격리 가짜 tmux 픽스처)


@pytest.fixture(params=["off", "deleg"], autouse=True)
def mode(request, monkeypatch):
    if request.param == "deleg":
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    else:
        monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    return request.param


def spawn(tm, parent="main", title="kid", **kw):
    return delegation.spawn(_caller(tm, parent), task="x", title=title, **kw)["delegation"]


def code(fn):
    with pytest.raises(delegation.DelegationError) as e:
        fn()
    return e.value.code


def audit_text(mode: str) -> str:
    p = audit.path() if mode == "deleg" else vt_paths.state_dir() / "agent_access.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def edit(fn):
    with delegation._locked():
        data = delegation._read()
        fn(data)
        delegation._write(data)


def ledger() -> list[dict]:
    return json.loads(delegation._path().read_text(encoding="utf-8"))["items"]


# --- 간선 불변 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["parent_key", "child_key", "id"])
def test_edge_is_immutable_after_spawn(tm, mode, field):
    did = spawn(tm)
    before = delegation._path().read_bytes()

    def tamper(d):
        it = d["items"][0]
        if field == "parent_key":
            it["parent"]["key"] = "$99:9"
        elif field == "child_key":
            it["child"]["key"] = "$99:9"
        else:
            it["id"] = "forged0000"
    assert code(lambda: edit(tamper)) == "edge_immutable"
    assert delegation._path().read_bytes() == before           # 아무것도 쓰이지 않았다
    assert "edge_immutable" in audit_text(mode)                # 감사됐다
    assert delegation.get(did)["parent"]["key"] != "$99:9"


def test_audit_of_a_refused_edge_change_has_no_content(tm, mode):
    spawn(tm)
    with pytest.raises(delegation.DelegationError):
        edit(lambda d: d["items"][0]["parent"].update(key="$99:9"))
    text = audit_text(mode)
    assert "edge_immutable" in text and "$99:9" not in text


def test_spawn_may_fill_the_empty_child_key_once_and_new_items_and_deletions_are_fine(tm):
    spawn(tm)                                                  # 예약(child.key 빈 값) → 확정이 통과한다
    assert ledger()[0]["child"]["key"]
    spawn(tm, title="kid2")                                    # 새 항목 추가
    edit(lambda d: d["items"].pop())                           # 항목 삭제(정리)
    assert len(ledger()) == 1
    edit(lambda d: d["items"][0].update(state="closed"))       # 상태 변경은 자유 — 간선만 잠긴다
    assert ledger()[0]["state"] == "closed"


def test_other_ledger_fields_may_change(tm):
    spawn(tm)
    edit(lambda d: d["items"][0].update(summary="x", outcome="succeeded"))
    assert ledger()[0]["summary"] == "x"


# --- deleg.control = 엄격한 부모만 ----------------------------------------------------------------


def two_families(tm):
    a = spawn(tm, "main", "kid-a")
    a2 = spawn(tm, "main", "kid-b")                            # 같은 부모의 형제
    b = spawn(tm, "other", "kid-c")                            # 다른 부모의 자식
    return a, a2, b


@pytest.mark.parametrize("op", ["send", "close"])
def test_only_the_strict_parent_controls(tm, op):
    a, a2, b = two_families(tm)

    def call(caller: str, did: str):
        k = _caller(tm, caller).key
        return (lambda: delegation.send_to_child(k, did, "더")) if op == "send" else (lambda: delegation.close(did, caller_key=k))

    assert code(call("kid-a", a)) == "not_found"               # 자기 자신(자식)
    assert code(call("kid-b", a)) == "not_found"               # 형제
    assert code(call("kid-c", a)) == "not_found"               # 다른 부모의 자식
    assert code(call("other", a)) == "not_found"               # 다른 부모
    assert code(call("stranger", a)) == "not_found"            # 무관한 세션
    call("main", a)()                                          # 부모는 된다
    assert delegation.get(a)["state"] == ("running" if op == "send" else "closed")
    assert delegation.get(a2)["state"] == "running" and delegation.get(b)["state"] == "running"


def test_a_ledger_entry_whose_parent_is_its_own_child_grants_nobody_control(tm):
    did = spawn(tm)
    kid = _caller(tm, "kid").key
    # 위조·손상 흉내: 부모 칸을 자식 키로 — 파일을 직접 고친다(간선 보호는 이 쓰기를 거치지 않는 경로)
    p = delegation._path()
    data = json.loads(p.read_text(encoding="utf-8"))
    data["items"][0]["parent"]["key"] = kid
    p.write_text(json.dumps(data), encoding="utf-8")
    assert code(lambda: delegation.send_to_child(kid, did, "x")) == "not_found"
    assert code(lambda: delegation.close(did, caller_key=kid)) == "not_found"


def test_child_cannot_act_as_parent_even_for_its_own_delegation(tm):
    did = spawn(tm)
    kid = _caller(tm, "kid").key
    assert code(lambda: delegation.send_to_child(kid, did, "x")) == "not_found"
    delegation.report(kid, "succeeded", "ok")                  # 자식의 몫(보고)은 된다


# --- 재개·포크·재생성은 소유를 만들지도 가져가지도 못한다 ------------------------------------------------------


def test_key_includes_session_created():
    assert delegation._key("$1", 100) != delegation._key("$1", 101)
    assert delegation._key("$1", 100) == "$1:100"


def test_recreated_session_with_the_same_name_is_a_different_owner(tm):
    did = spawn(tm)
    old_parent, old_kid = _caller(tm, "main").key, _caller(tm, "kid").key
    for name in ("main", "kid"):                               # 같은 이름으로 다시 만든 세션(= 새 생성 시각)
        del tm.sessions[name]
        tm.add(name)
    new_parent, new_kid = _caller(tm, "main").key, _caller(tm, "kid").key
    assert new_parent != old_parent and new_kid != old_kid

    assert code(lambda: delegation.send_to_child(new_parent, did, "x")) == "not_found"     # 부모 자리를 가져가지 못한다
    assert code(lambda: delegation.close(did, caller_key=new_parent)) == "not_found"
    assert code(lambda: delegation.report(new_kid, "succeeded", "x")) == "not_delegated"   # 자식 자리도
    assert delegation.role_of(new_kid)["is_child"] is False and delegation.role_of(new_parent)["can_spawn"]


def test_hook_events_of_a_recreated_child_do_not_reach_the_parent(tm):
    spawn(tm)
    queue_store.remove_pending_by_source(lambda s: True)
    del tm.sessions["kid"]
    tm.add("kid")                                              # 같은 이름의 새 세션(이어받은 대화/포크가 여기서 돈다고 하자)
    delegation.on_agent_event("kid", "done", {"last_assistant_message": "hi"})
    delegation.on_agent_event("kid", "waiting", {})
    assert [x for x in queue_store.list_items() if x.get("source")] == []


def test_sweep_does_not_hand_a_dead_childs_delegation_to_its_namesake(tm):
    did = spawn(tm)
    del tm.sessions["kid"]
    tm.add("kid")
    delegation.sweep()
    item = delegation.get(did)
    assert item["state"] == "closed" and item["close_reason"] == "child_gone"
    assert item["child"]["key"] != _caller(tm, "kid").key


def test_new_session_in_the_same_directory_gets_no_ownership(tm):
    """`/clear`·`--resume`·포크로 같은 일을 이어 가는 새 tmux 세션은 위임 장부에 없다 — 보고도 제어도 못 한다."""
    did = spawn(tm)
    resumed = _caller(tm, "kid-resumed")
    assert code(lambda: delegation.report(resumed.key, "succeeded", "x")) == "not_delegated"
    assert code(lambda: delegation.send_to_child(resumed.key, did, "x")) == "not_found"


# --- 승인 비트는 직계 한 단계 ------------------------------------------------------------------------------


def test_no_transitive_control_even_if_the_depth_limit_is_raised(tm, monkeypatch):
    """깊이 제한을 풀어(손자 위임을 허용) 조부모 → 부모 → 손자 사슬을 만들어도 제어는 직계 부모만 한다."""
    top = spawn(tm, "main", "mid")                                       # main → mid
    monkeypatch.setattr(delegation, "check_spawn", lambda data, parent_key, now: None)   # 깊이 제한이 풀렸다고 가정
    leaf = spawn(tm, "mid", "leaf")                                      # mid → leaf
    main, mid, leaf_k = (_caller(tm, n).key for n in ("main", "mid", "leaf"))
    queue_store.remove_pending_by_source(lambda s: True)

    assert code(lambda: delegation.send_to_child(main, leaf, "조부모가 손자에게")) == "not_found"
    assert code(lambda: delegation.close(leaf, caller_key=main)) == "not_found"
    assert code(lambda: delegation.send_to_child(leaf_k, leaf, "x")) == "not_found"
    delegation.send_to_child(mid, leaf, "직계 부모")                       # 직계는 된다
    assert delegation.get(leaf)["state"] == "running"

    # 손자의 보고는 직계 부모에게만 간다
    delegation.report(leaf_k, "succeeded", "끝")
    targets = {(x.get("target") or {}).get("session") for x in queue_store.list_items() if x.get("source", {}).get("kind") == "reported"}
    assert targets == {"mid"}

    # 중간(mid)은 자식이면서 부모 — 자기 부모(main)의 위임은 자식 몫(보고)만, 제어는 못 한다
    assert code(lambda: delegation.close(top, caller_key=mid)) == "not_found"
    delegation.close(leaf, caller_key=mid)
    assert delegation.get(leaf)["state"] == "closed" and delegation.get(top)["state"] == "running"


def test_the_depth_limit_itself_still_holds(tm):
    spawn(tm, "main", "mid")
    assert code(lambda: delegation.spawn(_caller(tm, "mid"), task="y")) == "depth_limit"
