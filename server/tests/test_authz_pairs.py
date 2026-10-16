"""pair 본체 — 코드·합류·승인·역할 프리셋·handoff·끝내기·관계. 마스터 스위치(`VT_AUTHZ_ENFORCE=pair`)를 켠 채 돈다.
시계는 `pairs.now` 를 바꾼 가짜 시계, 세션은 가짜 pane 목록이다(진짜 tmux 를 쓰지 않는다)."""

from __future__ import annotations

import os
import re

import pytest

from authz import engine, leases, pairs, saga, store
from authz import principals as P
from authz.errors import ShareError
from share_identity import PaneRow

A = P.agent("$1:101", name="alpha")
B = P.agent("$2:102", name="bravo")
C = P.agent("$3:103", name="charlie")
H = P.human("dev1")
HE = P.human("dev1", elevated=True)
ROWS = [PaneRow(f"%{i}", 100 + i, f"${i}", 100 + i, n, True, "claude", "", "") for i, n in ((1, "alpha"), (2, "bravo"), (3, "charlie"))]


class Clock:
    def __init__(self):
        import time
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair")
    monkeypatch.delenv("VT_PAIR_IDLE_MIN", raising=False)
    root = (tmp_path / "roots").resolve()
    (root / "proj" / "src").mkdir(parents=True)
    (root / "proj" / "docs").mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    clock = Clock()
    alive = {"v": {"$1:101", "$2:102", "$3:103"}}
    monkeypatch.setattr(pairs, "now", clock)
    monkeypatch.setattr(pairs, "_pane_rows", lambda: ROWS)
    monkeypatch.setattr(pairs, "_alive_keys", lambda: alive["v"])
    monkeypatch.setattr(pairs, "_activity", lambda: None)
    monkeypatch.setattr(leases, "alive_keys", lambda fresh=False: alive["v"])
    monkeypatch.setattr(leases, "_alive_cache", {"t": 0.0, "v": None})
    pairs.reset_rate()
    leases.reset_rate()
    saga.FAULT.clear()
    import agent_msg
    monkeypatch.setattr(agent_msg, "_rate", {})

    class E:
        pass
    e = E()
    e.root, e.clock, e.alive, e.tmp = root, clock, alive, tmp_path
    e.proj = str(root / "proj")
    return e


def manage(p, gid=None):
    """에이전트에게 `pair.manage` 를 준다(사람이 승인함에서 승인한 것과 같은 레코드)."""
    with store.txn() as d:
        d["grants"]["items"].append({"id": gid or f"g-pm-{p.attrs['name']}", "subject": p.id, "resource": "pair:*", "scopes": ["pair.manage"],
                                     "schema": 2, "state": "active", "approved_by": "human:test", "approved_via": "manual",
                                     "expires": None, "conds": {}})


def code_of(fn, *a, **k):
    with pytest.raises(ShareError) as e:
        fn(*a, **k)
    return e.value.code, e.value.status, e.value


def bundle(env, **over):
    b = {"sessions": ["bravo"], "dirs": [env.proj], "role": "driver"}
    b.update(over)
    return b


def make_active(env, creator=A, joiner=B, ticks=(), **over):
    """A(driver)가 만들고 B(navigator)가 합류해 사람이 승인한 pair → pair 보기."""
    if creator.kind == P.AGENT:
        manage(creator)
    v, _ = pairs.create(creator, bundle(env, **over))
    c = pairs.issue_code(creator, v["id"])
    pairs.join(joiner, c["code"])
    res = pairs.decide(pairs.request_id_of(v["id"]), "allow", HE if ticks else H, elevated=list(ticks), alive=None)
    assert res["state"] == "allowed", res
    return v["id"]


def doc():
    d, bad = store._read("pairs")
    assert not bad
    return d["items"]


def get(pid):
    return next(x for x in doc() if x["id"] == pid)


def grants_of(pid):
    d, _ = store._read("grants")
    return [g for g in d["items"] if g.get("pair_id") == pid]


# --- 코드 ---------------------------------------------------------------------------------------


def test_code_format_and_phrase(env):
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", c["code"]), c["code"]
    w = c["phrase"].split(" ")
    assert len(w) == 2 and all(x in pairs.WORDS for x in w)
    assert len(set(pairs.WORDS)) == len(pairs.WORDS) == 64
    assert c["ttl"] == 300 and c["expires"] == pytest.approx(env.clock() + 300)


@pytest.mark.parametrize("raw,ok", [("abcd-efgh", True), ("ABCD EFGH", True), ("abcdefgh", True), ("ABCD-EFG", False), ("ABCD-EFGU", False),
                                    ("", False), (None, False), ("ABCD-EFGH-J", False)])
def test_normalize_code(raw, ok):
    assert bool(pairs.normalize_code(raw)) is ok


def test_crockford_aliases_normalize():
    assert pairs.normalize_code("O0Il-1Lo0") == "00111100"


def test_code_ttl_single_use_and_burn(env):
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    env.clock.advance(299)
    j = pairs.join(B, c["code"].lower())                                  # 소문자·하이픈 허용
    assert j["state"] == "joined" and j["phrase"] == c["phrase"] and j["pendingApproval"] is True
    assert code_of(pairs.join, C, c["code"])[:2] == ("invalid_code", 404)   # 1회용
    # TTL
    v2, _ = pairs.create(A, bundle(env, sessions=["charlie"]))
    c2 = pairs.issue_code(A, v2["id"])
    env.clock.advance(301)
    assert code_of(pairs.join, C, c2["code"])[0] == "invalid_code"


def test_five_wrong_attempts_burn_the_code(env):
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    for _ in range(4):
        assert code_of(pairs.join, B, "ZZZZ-ZZZZ")[0] == "invalid_code"
    assert get(v["id"])["code"]["state"] == "pending"
    assert code_of(pairs.join, B, "ZZZZ-ZZZZ")[0] == "invalid_code"          # 5번째
    assert get(v["id"])["code"]["state"] == "burned" and get(v["id"])["code"]["value"] is None
    assert code_of(pairs.join, B, c["code"])[0] == "invalid_code"            # 맞는 코드도 이미 폐기
    c3 = pairs.issue_code(A, v["id"])                                        # 다시 발급은 된다
    assert c3["code"] != c["code"] and c3["reused"] is False
    assert pairs.join(B, c3["code"])["state"] == "joined"


def test_issue_rate_limit_ten_per_hour_with_fake_clock(env):
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    for i in range(10):
        pairs.issue_code(A, v["id"])
        env.clock.advance(301)                                               # 이전 코드가 만료돼야 새 코드가 만들어진다
    e = code_of(pairs.issue_code, A, v["id"])
    assert e[:2] == ("rate_limited", 429)
    env.clock.advance(3600)
    assert pairs.issue_code(A, v["id"])["reused"] is False


def test_idempotent_reuse_same_bundle_and_no_widening(env):
    manage(A)
    v1, fresh1 = pairs.create(A, bundle(env))
    v1b, fresh1b = pairs.create(A, bundle(env))
    assert fresh1 and not fresh1b and v1b["id"] == v1["id"]
    c1 = pairs.issue_code(A, v1["id"])
    c1b = pairs.issue_code(A, v1["id"])
    assert c1b["code"] == c1["code"] and c1b["reused"] is True and len(doc()) == 1


def test_reissue_with_different_bundle_invalidates_earlier_code(env):
    """재발급 폐기 — 넓은 묶음의 옛 코드가 좁은 묶음으로 다시 만든 뒤에도 TTL 까지 사는 틈을 막는다."""
    manage(A)
    wide, _ = pairs.create(A, bundle(env, dirs=[env.proj], elevated=["raw_input"]))
    cw = pairs.issue_code(A, wide["id"])
    narrow, fresh = pairs.create(A, bundle(env, dirs=[env.proj + "/src"]))
    assert fresh and narrow["id"] != wide["id"]
    assert get(wide["id"])["state"] == "expired" and get(wide["id"])["end_reason"] == "superseded"
    assert get(wide["id"])["code"]["value"] is None
    assert code_of(pairs.join, B, cw["code"])[0] == "invalid_code"            # 넓은 옛 코드는 더 못 쓴다
    cn = pairs.issue_code(A, narrow["id"])
    assert pairs.join(B, cn["code"])["id"] == narrow["id"]


def test_other_creators_pairs_are_not_superseded(env):
    manage(A)
    manage(C)
    a, _ = pairs.create(A, bundle(env))
    pairs.issue_code(A, a["id"])
    pairs.create(C, bundle(env, sessions=["alpha"], dirs=[env.proj + "/src"]))
    assert get(a["id"])["state"] == "code_issued"


def test_status_after_lost_response_needs_no_reissue(env):
    """합류 응답을 잃어도 합류자는 자기 신원으로 status 를 읽으면 된다. 코드·베어러가 필요 없다."""
    pid = None
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    pairs.join(B, c["code"])                                                  # 응답을 잃었다고 치고 버린다
    s = pairs.status(B, v["id"])
    assert s["state"] == "joined" and s["approval"]["state"] == "pending" and s["role"] == "navigator" and s["phrase"] == c["phrase"]
    assert code_of(pairs.status, C, v["id"])[:2] == ("not_found", 404)         # 다른 에이전트는 못 본다
    pairs.decide(pairs.request_id_of(v["id"]), "allow", H, alive=None)
    s = pairs.status(B, v["id"])
    assert s["state"] == "active" and s["approval"]["state"] == "allowed"
    assert "code" not in s or s["code"]["state"] == "redeemed"


def test_wrong_caller_kinds(env):
    assert code_of(pairs.create, P.cli(os.getuid(), 5), bundle(env))[:2] == ("request_not_allowed", 403)
    assert code_of(pairs.create, A, bundle(env))[0] == "forbidden"            # pair.manage 없음
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    assert code_of(pairs.join, H, c["code"])[:2] == ("request_not_allowed", 403)      # 사람은 합류하지 않는다
    assert code_of(pairs.join, A, c["code"])[0] == "self_join"
    assert code_of(pairs.issue_code, B, v["id"])[0] == "not_found"
    assert code_of(pairs.create, P.legacy_token(), bundle(env))[0] == "request_not_allowed"


# --- 마스터 스위치 -------------------------------------------------------------------------------


def test_flag_off_every_entry_answers_pair_disabled_without_side_effects(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share,deleg,msg,peer,legacy,lease")
    for fn, args in ((pairs.create, (H, bundle(env))), (pairs.issue_code, (A, "pr-x")), (pairs.join, (B, "ABCD-EFGH")),
                     (pairs.status, (A, "pr-x")), (pairs.list_for, (A,)), (pairs.handoff_offer, (A, "pr-x")),
                     (pairs.handoff_accept, (A, "pr-x")), (pairs.end, (A, "pr-x"))):
        kw = {"everyone": False} if fn is pairs.list_for else {}
        assert code_of(fn, *args, **kw)[:2] == ("pair_disabled", 409)
    assert not store.doc_path("pairs").exists() and pairs.pending_approvals() == []
    assert pairs.related_keys("$1:101", "$2:102") is False and pairs.same_pair_holders(A.id, B.id) is False


def test_remote_refs_are_501(env):
    manage(A)
    for b in (bundle(env, sessions=[{"host": "other-mac", "session": "x"}]), bundle(env, **{"with": {"host": "other-mac", "session": "x"}})):
        e = code_of(pairs.create, A, b)
        assert e[:2] == ("pair_remote_unsupported", 501)
    assert doc() == []
    v, _ = pairs.create(A, bundle(env, sessions=[{"host": "local", "session": "bravo"}, {"local": "$3:103"}]))   # 이 호스트는 허용
    assert {s["key"] for s in get(v["id"])["bundle"]["sessions"]} == {"$2:102", "$3:103"}


# --- 승인 ---------------------------------------------------------------------------------------


def joined(env, **over):
    manage(A)
    v, _ = pairs.create(A, bundle(env, **over))
    c = pairs.issue_code(A, v["id"])
    pairs.join(B, c["code"])
    return v["id"], c


def test_agent_can_never_approve(env):
    pid, _ = joined(env)
    rid = pairs.request_id_of(pid)
    for who in (A, B, C, P.cli(os.getuid(), 1), P.legacy_token(), P.peer("x"), None):
        e = code_of(pairs.decide, rid, "allow", who, alive=None) if who is not None else None
        if who is not None:
            assert e[:2] == ("human_session_required", 403), who.id
    assert get(pid)["state"] == "joined" and grants_of(pid) == []


def test_pending_item_carries_everything_one_screen_needs(env):
    pid, c = joined(env, elevated=["raw_input", "relay"], leases=True)
    [it] = pairs.pending_approvals()
    assert it["kind"] == "pair" and it["id"] == pairs.request_id_of(pid) and it["state"] == "pending"
    p = it["pair"]
    assert p["phrases"] == {"initiator": c["phrase"], "joiner": c["phrase"]}
    assert p["bundle"]["sessions"] == ["bravo"] and p["bundle"]["dirs"] == [env.proj] and p["bundle"]["leases"] is True and p["bundle"]["summary"]
    assert p["roles"]["initiator"]["role"] == "driver" and p["roles"]["joiner"]["role"] == "navigator"
    assert p["roles"]["initiator"]["name"] == "alpha" and p["roles"]["joiner"]["name"] == "bravo"
    assert set(p["presets"]) == {"driver", "navigator"} and p["presets"]["driver"]["preset"]["id"] == "drive"
    assert {e["id"] for e in p["elevatedItems"]} == {"raw_input", "relay"} and p["needsReauth"] is True
    assert all(e["reasons"] for e in p["elevatedItems"]) and it["elevated"]
    assert it["expiresIn"] > 0
    assert c["code"].replace("-", "") not in str(it)                         # 코드 값은 승인함에 없다


def test_approval_without_elevated_ticks_grants_only_the_preset(env):
    pid = make_active(env, elevated=["raw_input", "session_manage"])
    gs = grants_of(pid)
    scopes = {s for g in gs for s in g["scopes"]}
    assert "session.input" in scopes and "session.manage" not in scopes
    for g in gs:
        if "session.input" in g["scopes"]:
            assert g["sconds"]["session.input"]["modes"] == ["line"]          # 체크하지 않았으니 raw 없음
    assert get(pid)["approval"]["ticked"] == []


def test_ticked_elevated_items_need_reauth_and_only_requested_ones(env):
    pid, _ = joined(env, elevated=["raw_input", "relay"])
    rid = pairs.request_id_of(pid)
    e = code_of(pairs.decide, rid, "allow", H, elevated=["raw_input"], alive=None)              # 재인증 안 한 사람
    assert e[:2] == ("elevation_required", 403) and e[2].extra["items"] == ["raw_input"]
    assert get(pid)["state"] == "joined" and grants_of(pid) == []
    assert code_of(pairs.decide, rid, "allow", HE, elevated=["session_manage"], alive=None)[0] == "wider_than_requested"
    assert code_of(pairs.decide, rid, "allow", HE, elevated=["nope"], alive=None)[0] == "bad_elevated"
    assert code_of(pairs.decide, rid, "allow", HE, elevated="raw_input", alive=None)[0] == "bad_elevated"
    r = pairs.decide(rid, "allow", HE, elevated=["raw_input"], alive=None)
    assert r["state"] == "allowed"
    gs = grants_of(pid)
    raw_ok = [g for g in gs if "session.input" in g["scopes"]]
    assert raw_ok and all(g["sconds"]["session.input"]["modes"] == ["line", "raw"] for g in raw_ok)
    assert not any("relay" in g["scopes"] for g in gs)                         # 요청했지만 체크하지 않은 relay 는 주지 않는다
    assert get(pid)["approval"]["ticked"] == ["raw_input"]


def test_requested_but_unticked_approval_needs_no_reauth(env):
    pid, _ = joined(env, elevated=["raw_input"])
    assert pairs.decide(pairs.request_id_of(pid), "allow", H, elevated=[], alive=None)["state"] == "allowed"


def test_every_elevated_item_really_is_an_elevated_reason():
    for it in pairs.ELEVATED_ITEMS:
        assert pairs.elevated_reasons(it), it


def test_check_phrase_mismatch_handling(env):
    pid, c = joined(env)
    rid = pairs.request_id_of(pid)
    for left in (2, 1):
        e = code_of(pairs.decide, rid, "allow", H, phrase="wrong words", alive=None)
        assert e[:2] == ("phrase_mismatch", 400) and e[2].extra["remaining"] == left
    assert get(pid)["state"] == "joined" and grants_of(pid) == []
    e = code_of(pairs.decide, rid, "allow", H, phrase="wrong words", alive=None)      # 세 번째 — 잠금
    assert e[:2] == ("phrase_mismatch_locked", 409)
    assert get(pid)["state"] == "ended" and get(pid)["end_reason"] == "phrase_mismatch" and grants_of(pid) == []
    # 맞는 문구(대소문자·공백 무시)는 통과
    pid2, c2 = joined(env, sessions=["charlie"], dirs=[env.proj + "/src"])
    ok = pairs.decide(pairs.request_id_of(pid2), "allow", H, phrase="  " + c2["phrase"].upper() + " ", alive=None)
    assert ok["state"] == "allowed"


def test_first_writer_wins_and_replay(env):
    pid, _ = joined(env)
    rid = pairs.request_id_of(pid)
    a = pairs.decide(rid, "allow", H, alive=None)
    b = pairs.decide(rid, "allow", H, alive=None)
    assert b["replayed"] is True and b["state"] == "allowed"
    assert code_of(pairs.decide, rid, "deny", H, alive=None)[:2] == ("already_decided", 409)
    assert len(grants_of(pid)) == len({g["id"] for g in grants_of(pid)})


def test_deny_ends_the_pair_with_no_grants(env):
    pid, _ = joined(env)
    r = pairs.decide(pairs.request_id_of(pid), "deny", H, alive=None)
    assert r["state"] == "denied" and get(pid)["state"] == "ended" and get(pid)["end_reason"] == "denied" and grants_of(pid) == []
    s = pairs.status(B, pid)
    assert s["approval"]["state"] == "denied" and s["state"] == "ended"
    assert code_of(pairs.decide, pairs.request_id_of(pid), "allow", H, alive=None)[0] == "already_decided"


def test_member_gone_before_approval_closes_the_request(env):
    pid, _ = joined(env)
    e = code_of(pairs.decide, pairs.request_id_of(pid), "allow", H, alive={"$1:101"})
    assert e[0] == "member_gone" and get(pid)["state"] == "ended" and grants_of(pid) == []


# --- 역할 프리셋 · 상한 ---------------------------------------------------------------------------


def test_role_presets_match_the_documented_sets():
    nav, nav_cd = pairs.role_caps("navigator")
    drv, drv_cd = pairs.role_caps("driver")
    assert set(nav) == {"session.view", "session.read", "msg.send", "fs.read"}
    assert set(drv) == set(nav) | {"session.input", "fs.write", "deleg.spawn"}
    assert drv_cd["session.input"] == {"modes": ["line"]} and drv_cd["fs.write"] == {"ops": ["write"]} and "browser" not in drv_cd
    from authz import scopes as S
    assert S.caps_subset(S.effective(nav, nav_cd), S.effective(*S.preset("collab")))
    assert S.caps_subset(S.effective(drv, drv_cd), S.effective(*S.preset("drive")))
    assert not S.requires_elevated("agent", drv, "", drv_cd)               # 프리셋 자체는 elevated 가 아니다


def test_derived_grants_are_exactly_what_the_roles_allow(env):
    pid = make_active(env, leases=True)
    gs = {g["id"]: g for g in grants_of(pid)}
    raw = pid[3:]
    # A = driver (initiator): bravo 세션 view/read/input[line], 폴더 read+write, B 에게 msg, 위임 quota. B = navigator: 폴더 read, A 에게 msg.
    a_s = gs[f"pp-{raw}-i-s0"]
    assert a_s["resource"] == "session:local/$2:102" and set(a_s["scopes"]) == {"session.view", "session.read", "session.input"}
    assert a_s["pair_id"] == pid and a_s["approved_via"] == "manual" and a_s["expires"] == get(pid)["hard_expires"] and a_s["subject"] == A.id
    assert a_s["origin"] == f"pair:grant:{pid}" and a_s["subject_session"] == "$1:101" and a_s["resource_session"] == "$2:102"
    assert set(gs[f"pp-{raw}-i-d0"]["scopes"]) == {"fs.read", "fs.write"} and gs[f"pp-{raw}-i-d0"]["sconds"]["fs.write"] == {"ops": ["write"]}
    assert gs[f"pp-{raw}-i-q"]["resource"] == f"deleg-quota:{A.id}" and gs[f"pp-{raw}-i-q"]["conds"] == {"deleg_quota": True}
    assert gs[f"pp-{raw}-i-m"]["resource"] == "msg:local/$2:102" and gs[f"pp-{raw}-j-m"]["resource"] == "msg:local/$1:101"
    assert set(gs[f"pp-{raw}-j-d0"]["scopes"]) == {"fs.read"}
    assert f"pp-{raw}-j-s0" not in gs and f"pp-{raw}-j-q" not in gs                  # 자기 세션에는 따로 주지 않는다 · navigator 는 spawn 없음
    assert get(pid)["grants"] == sorted(gs)


def test_engine_decisions_follow_the_roles_and_pair_grants_skip_the_share_ceiling(env):
    pid = make_active(env)
    snap = store.snapshot()
    f = lambda p, sc, res, **ctx: engine.decide(p, sc, res, ctx, snap)
    bravo = "session:local/$2:102"
    assert f(A, "session.read", bravo).allow                                    # 공유되지 않은 세션이어도 pair 의 grant 가 상한
    assert f(A, "session.input", bravo, mode="line").allow
    assert not f(A, "session.input", bravo, mode="raw").allow                   # raw 는 체크하지 않았다
    assert not f(A, "session.manage", bravo, op="kill").allow
    assert f(A, "fs.write", f"fs:local{env.proj}/src/x.py", op="write").allow
    assert not f(A, "fs.write", f"fs:local{env.proj}/src/x.py", op="commit").allow
    assert f(B, "fs.read", f"fs:local{env.proj}/src/x.py").allow
    assert not f(B, "fs.write", f"fs:local{env.proj}/src/x.py", op="write").allow    # navigator 는 못 쓴다
    assert not f(B, "session.input", "session:local/$3:103", mode="line").allow            # 번들 밖 세션은 어느 역할도 못 만진다
    assert not f(C, "session.read", bravo).allow and not f(C, "fs.read", f"fs:local{env.proj}/src/x.py").allow
    assert f(A, "msg.send", "msg:local/$2:102").allow


def test_pair_grants_are_inert_when_the_master_switch_goes_off(env, monkeypatch):
    pid = make_active(env)
    assert engine.decide(A, "session.read", "session:local/$2:102", {}, store.snapshot()).allow
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert not engine.decide(A, "session.read", "session:local/$2:102", {}, store.snapshot()).allow


# --- pair.manage 를 얻는 길 ---------------------------------------------------------------------


def test_agent_gets_pair_manage_through_the_normal_request_and_approval_only_when_the_switch_is_on(env, monkeypatch):
    from authz import inbox
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    e = code_of(inbox.create_request, A, "pair:*", ["pair.manage"], alive=None)
    assert e[0] == "scope_not_requestable"                                              # 꺼져 있으면 예전과 똑같이 요청할 수 없다
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair")
    assert code_of(pairs.create, A, bundle(env))[0] == "forbidden"
    item, fresh = inbox.create_request(A, "pair:*", ["pair.manage"], reason="pairing", alive=None)
    assert fresh and item["state"] == "pending"
    inbox.decide_generic(item["id"], "allow", "session", None, None, H, None)
    v, _ = pairs.create(A, bundle(env))                                                  # 사람이 승인한 뒤에야 만든다
    assert v["state"] == "proposed"
    # 자동 승인 window 는 pair.manage 를 절대 덮지 못한다(기존 규칙 그대로)
    from authz import scopes as S
    assert "pair.manage" in S.NEVER_WINDOW


def test_unrequestable_admin_scopes_stay_unrequestable_with_the_switch_on(env):
    from authz import inbox
    assert code_of(inbox.create_request, A, "authz:*", ["authz.admin"], alive=None)[0] == "scope_not_requestable"
