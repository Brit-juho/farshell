"""grant 의 `approved_via`(manual | window | system | migrated) — 엔진이 쓰는 모든 grant 에 `approved_by` 옆에 기록하고,
옛 레코드(필드 없음)는 `migrated` 로 읽는다. window 로 승인된 grant 는 새 window 를 열거나 늘리는 근거가 될 수 없다.
"""

from __future__ import annotations

import json
import time

import pytest

from authz import approvals, defaults, engine, migrate, msg_svc, store
from authz import principals as P
from authz import scopes as S
from test_authz_migrate import NOW, TMUX, sd  # noqa: F401

HUMAN = P.human("dev1")


def test_via_of_reads_missing_or_unknown_as_migrated():
    assert S.via_of({}) == "migrated"
    assert S.via_of({"approved_via": "bogus"}) == "migrated"
    assert S.via_of(None) == "migrated"
    for v in S.APPROVED_VIA:
        assert S.via_of({"approved_via": v}) == v
    assert S.APPROVED_VIA == ("manual", "window", "system", "migrated")


def test_every_writer_stamps_approved_via():
    legacy = {"id": "g1", "agent": {"sid": "$1", "created": 1, "name": "a"}, "target": {"sid": "$2", "created": 2},
              "read": True, "write": False, "granted": 1.0}
    assert approvals.engine_grant(legacy, approved_by="human:x")["approved_via"] == "manual"          # 사람 승인
    assert approvals.engine_grant(legacy, approved_by="x", approved_via="window")["approved_via"] == "window"
    assert approvals.engine_grant(legacy, approved_by="x", approved_via="nonsense")["approved_via"] == "migrated"
    assert migrate.translate_agent_grant(legacy)["approved_via"] == "migrated"
    assert {g["approved_via"] for g in migrate.translate_peer_grant({"id": "p", "level": "control", "agentMessages": True})} == {"migrated"}
    d = migrate.translate_delegation({"id": "d1", "state": "running", "parent": {"key": "$1:1"}, "child": {"key": "$2:2"}})
    assert d and {g["approved_via"] for g in d} == {"system"}
    assert defaults.rules_for(HUMAN) and all(r["approved_via"] == "system" for r in defaults.rules_for(HUMAN))
    assert all(r["approved_via"] == "system" for r in defaults.rules_for(P.agent("$1:1")))


def test_revoking_write_keeps_the_original_via():
    docs = {"grants": {"items": []}, "approvals": {"items": []}}
    legacy = {"id": "g1", "agent": {"sid": "$1", "created": 1, "name": "a"}, "target": {"sid": "$2", "created": 2},
              "read": True, "write": True, "granted": 1.0}
    docs["grants"]["items"].append(approvals.engine_grant(legacy, approved_by="human:x", approved_via="window"))
    approvals.revoke(docs, "g1", "write", 5.0)
    g = docs["grants"]["items"][0]
    assert (g["approved_by"], g["approved_via"]) == ("human:x", "window")


def test_msg_grant_is_manual_and_listed(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    a, b = "$1:1", "$2:2"
    rid = msg_svc.request_access(a, "a", b, "b", {a, b})[0]["id"]
    out = msg_svc.decide(rid, "allow", None, HUMAN, {a, b})
    assert out["grant"]["approvedVia"] == "manual" and out["grant"]["approvedBy"] == HUMAN.id
    rec = store._read("grants")[0]["items"][0]
    assert rec["approved_via"] == "manual"
    assert msg_svc.list_grants({a, b})[0]["approvedVia"] == "manual"
    del rec["approved_via"]                                                  # 필드가 없는 옛 레코드
    assert msg_svc.grant_view(rec)["approvedVia"] == "migrated"
    assert approvals.grant_listing(approvals.engine_grant(
        {"id": "g", "agent": {"sid": "$1", "created": 1}, "target": {"sid": "$2", "created": 2}, "read": True},
        approved_by="human:x"))["approvedVia"] == "manual"


def test_sync_keeps_a_manual_via_and_does_not_flag_old_records(sd):
    migrate.apply(now=NOW, tmux=TMUX)
    gp = store.doc_path("grants")
    data = json.loads(gp.read_text())
    mig = [g for g in data["items"] if g.get("origin", "").startswith("legacy:agent_grants:")]
    assert mig and all(g["approved_via"] == "migrated" for g in mig)
    mig[0]["approved_via"], mig[0]["approved_by"] = "manual", "human:dev:me"
    gp.write_text(json.dumps(data))
    r = migrate.apply(now=NOW, tmux=TMUX)                                     # 다시 이전해도 사람이 승인한 출처는 유지
    assert r["grants"]["updated"] == 0
    kept = [g for g in store._read("grants")[0]["items"] if g["id"] == mig[0]["id"]][0]
    assert (kept["approved_by"], kept["approved_via"]) == ("human:dev:me", "manual")


def test_records_written_before_the_field_existed_verify_clean_and_stay_byte_equal(sd):
    migrate.apply(now=NOW, tmux=TMUX)
    gp = store.doc_path("grants")
    data = json.loads(gp.read_text())
    for g in data["items"]:
        g.pop("approved_via", None)                                           # 이 필드가 생기기 전에 쓴 저장소
    gp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    before = gp.read_bytes()
    v = migrate.verify_store(now=NOW, tmux=TMUX)
    assert v["clean"], v["diff"]                                              # 필드가 없다는 것만으로 차이가 되지 않는다
    r = migrate.apply(now=NOW, tmux=TMUX)
    assert r["grants"]["updated"] == 0 and gp.read_bytes() == before          # 다시 이전해도 옛 레코드를 고쳐 쓰지 않는다
    assert all(S.via_of(g) == "migrated" for g in store._read("grants")[0]["items"])


# --- window 가드 ---------------------------------------------------------------------------------


def _w(now: float, **kw) -> dict:
    return {"subject": "agent:local/*", "resources": ["session:local/*"], "covers": ["session.read"],
            "max_ttl_per_grant": 600, "expires": now + 600, **kw}


@pytest.mark.parametrize("via,blocked", [("window", True), ("manual", False), ("system", False), ("migrated", False),
                                         (None, False), ("bogus", False)])
def test_window_grant_cannot_open_or_extend_a_window(via, blocked):
    now = time.time()
    g = {"id": "g", "approved_by": "x"}
    if via is not None:
        g["approved_via"] = via
    errs = engine.validate_window(_w(now), elevated=True, now=now, basis=[g])
    assert ("window_derived_grant" in errs) is blocked
    assert (errs == []) is (not blocked)
    assert engine.window_basis_errors([g]) == (["window_derived_grant"] if blocked else [])


def test_window_guard_applies_even_when_other_grants_are_fine_and_defaults_to_no_basis():
    now = time.time()
    ok = {"approved_via": "manual"}
    bad = {"approved_via": "window"}
    assert engine.validate_window(_w(now), elevated=True, now=now) == []
    assert engine.validate_window(_w(now), elevated=True, now=now, basis=[ok, bad]) == ["window_derived_grant"]
    # 다른 검증은 그대로 함께 보고한다
    errs = engine.validate_window(_w(now), elevated=False, now=now, basis=[bad])
    assert errs == ["window_derived_grant", "elevated_required"]
