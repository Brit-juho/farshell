"""`VT_AUTHZ_ENFORCE=share`: 에이전트 세션 공유의 판정·저장·감사를 엔진이 쥔다.

기존 `test_agent_share.py` 는 무수정으로 플래그 꺼짐/켜짐 둘 다 통과한다(`VT_AUTHZ_ENFORCE=share pytest …`).
여기는 켜졌을 때만 성립하는 성질을 못 박는다:

  - 엔진이 권한자다: grant 없는 에이전트는 어떤 거짓 힌트로도 읽기·쓰기를 못 한다(적대 시험).
  - 옛 저장소와 엔진 저장소는 매 연산 뒤 같다(dual-write) — `migrate.verify_store()` 가 "차이 없음".
  - 플래그를 끄면 같은 상태로 이어진다(롤백), 다시 켜면 그 사이 옛 코드의 변경을 채택한다.
  - 거절 쿨다운·대기 한도·분당 한도·공유 상한(ceiling)·철회 즉시 반영.
  - 감사는 hash chain 에 있고 내용이 없다, 변경은 감사 실패 시 fail closed.
  - H1(기계 토큰 우회)은 여기서 안 막힌다 — 3e. 그 사실을 `authz_char/test_char_agent_share_enforced.py` 가 못 박는다.

실제 tmux(전용 소켓)를 쓴다 — `test_agent_share.py` 와 같은 이유.
"""

from __future__ import annotations

import json
import time

import pytest

import agent_share
import tmux_runner
from authz import approvals, audit, flags, limits, migrate, shares, store
from authz import principals as P
from authz import scopes as S
from authz.errors import ShareError

# 같은 격리 tmux·로그인 픽스처와 도우미를 쓴다.
from test_agent_share import MACHINE, aread, app_auth, approve, asend, env, setup_pair, share, web  # noqa: F401


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    monkeypatch.setattr(agent_share, "_read_seen", {})


def docs():
    return {n: store._read(n)[0] for n in ("grants", "approvals", "shares", "meta")}


def old_state():
    data, corrupt = agent_share._load()
    assert not corrupt
    return data


def _norm(state: dict) -> dict:
    return {"grants": sorted((g["id"], str(g["agent"]["sid"]), str(g["agent"]["created"]), str(g["target"]["sid"]),
                              str(g["target"]["created"]), bool(g["read"]), bool(g["write"])) for g in state["grants"]),
            "requests": sorted((r["id"], r["kind"], r["state"], str(r["agent"]["sid"]), str(r["target"]["sid"]))
                               for r in state["requests"])}


def assert_parity(note: str = "") -> None:
    """옛 저장소 == 엔진 저장소 (투영 비교) 이고 `fsh migrate-auth --verify` 의 저장소 비교가 차이 없다."""
    eng = approvals.legacy_state(docs())
    assert _norm(eng) == _norm(old_state()), f"dual-write 불일치 {note}"
    v = migrate.verify_store()
    assert v["clean"], f"verify 차이 {note}: {v['diff']}"


def audit_rows(prefix="share."):
    return audit.read_recent(1000, prefix)


# --- 플래그 --------------------------------------------------------------------------------


def test_flag_defaults_off_and_parses_tokens(monkeypatch):
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    assert flags.tokens() == frozenset() and not flags.enforced("share") and not agent_share.enforced()
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", " Share , deleg,,bogus ")
    assert flags.enforced("share") and flags.enforced("deleg") and not flags.enforced("msg")
    assert flags.unknown() == ["bogus"]


def test_flag_off_touches_no_authz_file(web, monkeypatch):
    """꺼져 있으면 엔진 저장소·authz 감사에 아무것도 쓰지 않는다(출하해도 아무것도 안 바뀐다)."""
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    import vt_paths
    assert not (vt_paths.state_dir() / "authz").exists() and not (vt_paths.state_dir() / "audit").exists()


# --- 엔진이 권한자 + dual-write -----------------------------------------------------------------


def test_full_lifecycle_is_engine_driven_and_dual_written(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    assert_parity("share")
    d = docs()
    assert d["shares"]["items"][env.sess("target").key] == shares.entry(env.sess("target").key, "shell", "readwrite")
    assert d["meta"]["items"]["share_adopted"] is True

    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 202
    rid = r.json()["requestId"]
    assert "grantId" not in r.json()
    assert [a["id"] for a in docs()["approvals"]["items"]] == [f"ap-{rid}"]
    assert_parity("pending")

    res = approve(human, rid)
    assert res.status_code == 200
    gid = res.json()["grant"]["id"]
    assert res.json()["grantId"] == f"ag-{gid}"                       # 응답이 grantId 를 얻는다
    g = docs()["grants"]["items"]
    assert [x["id"] for x in g] == [f"ag-{gid}"] and g[0]["scopes"] == ["session.view", "session.read"]
    assert g[0]["approved_by"].startswith("human:")                  # 승인자는 결정한 사람 주체
    assert docs()["approvals"]["items"] == []
    assert_parity("approved")

    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 200 and r.json()["grantId"] == f"ag-{gid}" and "ready-marker" in r.json()["text"]

    wid = asend(agent, env, "claude-a", "shell", "echo hi").json()["requestId"]
    assert approve(human, wid, "readwrite").status_code == 200
    assert "session.input" in docs()["grants"]["items"][0]["scopes"]
    assert_parity("write approved")
    s = asend(agent, env, "claude-a", "shell", "echo typed-1")
    assert s.status_code == 200 and s.json()["grantId"] == f"ag-{gid}"
    env.wait("target", "echo typed-1")

    rv = human.delete(f"/api/agent/share/grants/{gid}?scope=write")
    assert rv.status_code == 200 and rv.json()["grantId"] == f"ag-{gid}"
    assert "session.input" not in docs()["grants"]["items"][0]["scopes"]
    assert_parity("write revoked")
    assert asend(agent, env, "claude-a", "shell", "x").status_code == 202

    assert human.delete(f"/api/agent/share/grants/ag-{gid}").status_code == 200   # 엔진 id 로도 철회
    assert docs()["grants"]["items"] == []
    assert_parity("revoked")

    assert human.delete("/api/agent/share/sessions/target").status_code == 200
    assert docs()["shares"]["items"] == {} and env.sess("target").share is None
    assert_parity("unshared")


def test_old_store_is_projection_of_engine_even_after_purge(web):
    """세션이 죽으면 엔진 정리가 옛 파일에도 반영된다(옛 코드가 되살리지 않는다)."""
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert len(old_state()["grants"]) == 1
    env.kill("claude-a")
    assert human.get("/api/agent/share/grants").json()["grants"] == []
    assert old_state()["grants"] == [] and docs()["grants"]["items"] == []
    assert_parity("purged")


def test_translate_roundtrip_is_lossless():
    legacy_g = {"id": "abc123", "agent": {"sid": "$5", "created": 1700, "name": "claude-a"},
                "target": {"sid": "$7", "created": 1800}, "read": True, "write": True, "granted": 11.5, "updated": 12.5}
    rec = approvals.engine_grant(legacy_g, approved_by="human:dev:x")
    assert approvals.grant_to_legacy(rec) == legacy_g
    legacy_r = {"id": "r1", "agent": {"sid": "$5", "created": 1700, "name": "claude-a"}, "target": {"sid": "$7", "created": 1800},
                "kind": "write", "state": "denied", "created": 1.0, "expires": 9e9, "decided": time.time()}
    rec = approvals.engine_request(legacy_r, time.time())
    assert approvals.request_to_legacy(rec) == legacy_r


# --- 롤백 -------------------------------------------------------------------------------------


def test_rollback_flag_off_keeps_working_and_reenable_adopts_old_changes(web, monkeypatch):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    wid = asend(agent, env, "claude-a", "shell", "x").json()["requestId"]
    approve(human, wid, "readwrite")
    gid = human.get("/api/agent/share/grants").json()["grants"][0]["id"]
    assert asend(agent, env, "claude-a", "shell", "echo on-1").status_code == 200
    before = human.get("/api/agent/share/grants").json()

    monkeypatch.delenv("VT_AUTHZ_ENFORCE")                           # 롤백: 옛 코드가 같은 상태를 이어받는다
    after_off = human.get("/api/agent/share/grants").json()
    assert after_off == before                                       # 같은 grant 목록(동등 parity)
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    assert asend(agent, env, "claude-a", "shell", "echo off-1").status_code == 200
    assert human.delete(f"/api/agent/share/grants/{gid}?scope=write").status_code == 200   # 옛 경로에서 철회
    assert asend(agent, env, "claude-a", "shell", "echo off-2").status_code == 202

    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")                  # 다시 켜면 옛 코드의 변경을 채택
    assert asend(agent, env, "claude-a", "shell", "echo on-2").status_code == 202    # 쓰기 철회가 살아 있다
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    assert "session.input" not in docs()["grants"]["items"][0]["scopes"]
    assert_parity("re-enabled")


def test_rollback_after_every_op_leaves_old_store_equal(web, monkeypatch):
    """연산마다 끄고 옛 코드가 읽는 값(grants/requests)이 엔진과 같다 — 롤백이 어느 시점에서도 안전하다."""
    human, agent, env = setup_pair(web, mode="readwrite")
    seq = []
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    seq.append(human.get("/api/agent/share/requests").json())
    approve(human, rid)
    seq.append(human.get("/api/agent/share/grants").json())
    rid2 = asend(agent, env, "claude-a", "shell", "x").json()["requestId"]
    seq.append(human.get("/api/agent/share/requests").json())
    human.post(f"/api/agent/share/requests/{rid2}/decision", json={"decision": "deny"})
    seq.append(human.get("/api/agent/share/requests").json())
    live_state = agent_share.list_state(agent_share.snapshot())
    monkeypatch.delenv("VT_AUTHZ_ENFORCE")
    assert agent_share.list_state(agent_share.snapshot()) == live_state
    assert asend(agent, env, "claude-a", "shell", "y").status_code == 403           # 거절 쿨다운이 옛 코드에도 이어진다
    assert seq[0]["requests"][0]["id"] == rid


def test_first_enable_adopts_shares_and_grants_made_while_off(web, monkeypatch):
    human, agent, env = web
    monkeypatch.delenv("VT_AUTHZ_ENFORCE")
    env.mk("target")
    env.mk("claude-a", "cat")
    assert share(human, "target", "shell").status_code == 200
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert not (store.root() / "grants.json").exists()
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    assert aread(agent, env, "claude-a", "shell").status_code == 200       # 켜는 순간 옛 grant·공유가 이어진다
    assert env.sess("target").share == {"nick": "shell", "mode": "read"}
    assert [g["origin"].startswith("legacy:agent_grants:") for g in docs()["grants"]["items"]] == [True]
    assert_parity("adopted")


# --- 적대 시험: 거짓 힌트로는 못 연다 ----------------------------------------------------------------


LIES = {"X-Fsh-Grant": "ag-forged", "X-Fsh-Level": "readwrite", "X-Fsh-Agent": "claude-b", "X-Fsh-Role": "human",
        "X-Fsh-Approved": "1", "X-Peer-Obo": "agent:local/$9:1"}


def test_ungranted_agent_cannot_read_or_send_even_with_lying_hints(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    env.mk("claude-b", "cat")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])      # A 만 읽기 grant
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    body = {"nickname": "shell", "lines": 5, "grant": "ag-forged", "agent": "claude-a", "from": "claude-a",
            "level": "readwrite", "approved": True, "sid": env.row("claude-a").sid}
    # B 는 grant 가 없다: 본문·헤더로 A 라고 우겨도 판정 주체는 pane 확인뿐
    r = agent.post("/api/agent/shared/read", json=body, headers={**env.ident("claude-b"), **LIES})
    assert r.status_code == 202 and r.json()["state"] == "pending" and "text" not in r.json()
    r = agent.post("/api/agent/shared/send", json={**body, "text": "echo pwned-b"}, headers={**env.ident("claude-b"), **LIES})
    assert r.status_code == 202 and "pwned" not in env.screen("target")
    # B 가 A 의 pane 을 주장하되 pid 는 자기 것: 부모 체인이 안 닿아 거부
    lie = env.ident("claude-b", pane=env.row("claude-a").pane_id)
    for path in ("read", "send"):
        r = agent.post(f"/api/agent/shared/{path}", json={**body, "text": "echo pwned-b2"}, headers={**lie, **LIES})
        assert r.status_code == 403 and r.json()["error"] == "identity_unverified"
    # 기계 토큰만(pane 헤더 없음)
    r = agent.post("/api/agent/shared/read", json=body, headers=LIES)
    assert r.status_code == 403 and r.json()["error"] == "identity_unverified"
    # A 의 읽기 grant 는 쓰기를 열어 주지 않는다(헤더로 level 을 올려도)
    r = agent.post("/api/agent/shared/send", json={**body, "text": "echo pwned-a"}, headers={**env.ident("claude-a"), **LIES})
    assert r.status_code == 202 and r.json()["kind"] == "write"
    assert "pwned" not in env.screen("target")
    # 엔진 저장소에는 B 의 grant 가 없다
    assert {g["subject_session"] for g in docs()["grants"]["items"]} == {env.sess("claude-a").key}


def test_revoked_grant_stops_reads_immediately_and_unshare_closes_everything(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    gid = human.get("/api/agent/share/grants").json()["grants"][0]["id"]
    for _ in range(3):
        assert aread(agent, env, "claude-a", "shell").status_code == 200
    assert human.delete(f"/api/agent/share/grants/{gid}").status_code == 200
    assert aread(agent, env, "claude-a", "shell").status_code == 202       # 다음 호출부터 즉시
    assert asend(agent, env, "claude-a", "shell", "z").status_code == 202


def test_lowering_share_mode_is_enforced_by_the_engine_ceiling(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    assert asend(agent, env, "claude-a", "shell", "echo one").status_code == 200
    share(human, "target", "shell", "read")
    assert docs()["shares"]["items"][env.sess("target").key]["level"] == "read"
    r = asend(agent, env, "claude-a", "shell", "echo two")
    assert r.status_code == 403 and r.json()["error"] == "read_only_share"
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    # 엔진 판정 자체도 같다: grant 에는 쓰기가 남아 있지만 상한이 막는다
    from authz import engine
    me = P.agent(env.sess("claude-a").key)
    dec = engine.decide(me, "session.input", f"session:local/{env.sess('target').key}",
                        {"share": env.sess("target").share, "share_known": True, "prefer_live": False, "mode": "line"},
                        store.Snapshot(grants=docs()["grants"]["items"], shares=docs()["shares"]["items"], now=time.time()))
    assert not dec.allow and dec.reason == "ceiling"


def test_share_policy_drift_resolves_to_the_lower_level(web):
    """tmux 미러를 손으로 고쳐도 진실(shares.json)을 넘지 못한다 — 낮은 쪽."""
    human, agent, env = setup_pair(web, mode="read")
    key = env.sess("target").key
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert tmux_runner.set_option(env.row("target").sid, tmux_runner.SHARE_MODE_OPTION, "readwrite")
    assert env.sess("target").share == {"nick": "shell", "mode": "read"}           # 올린 쪽은 무시
    r = asend(agent, env, "claude-a", "shell", "echo sneaky")
    assert r.status_code == 403 and r.json()["error"] == "read_only_share"
    assert docs()["shares"]["items"][key]["level"] == "read"
    # 반대로 저장소가 높고 tmux 가 낮으면 tmux(낮은 쪽)
    share(human, "target", "shell", "readwrite")
    assert tmux_runner.set_option(env.row("target").sid, tmux_runner.SHARE_MODE_OPTION, "read")
    assert env.sess("target").share == {"nick": "shell", "mode": "read"}
    # tmux 옵션이 지워지면 공유 아님, 닉네임을 바꿔도 저장소의 닉네임이 진실
    tmux_runner.run(["set-option", "-u", "-t", env.row("target").sid, tmux_runner.SHARE_NICK_OPTION])
    assert env.sess("target").share is None
    assert aread(agent, env, "claude-a", "shell").status_code == 404
    assert tmux_runner.set_option(env.row("target").sid, tmux_runner.SHARE_NICK_OPTION, "evil")
    assert tmux_runner.set_option(env.row("target").sid, tmux_runner.SHARE_MODE_OPTION, "readwrite")
    assert env.sess("target").share == {"nick": "shell", "mode": "readwrite"}
    assert aread(agent, env, "claude-a", "evil").status_code == 404


def test_tmux_only_share_after_snapshot_is_not_shared(web):
    """snapshot 이후 tmux 옵션만 심은 공유(저장소에 없음)는 공유가 아니다 — 낮은 쪽."""
    human, agent, env = setup_pair(web)
    env.mk("planted", "cat")
    sid = env.row("planted").sid
    assert tmux_runner.set_option(sid, tmux_runner.SHARE_NICK_OPTION, "planted")
    assert tmux_runner.set_option(sid, tmux_runner.SHARE_MODE_OPTION, "readwrite")
    assert env.sess("planted").share is None
    assert aread(agent, env, "claude-a", "planted").status_code == 404


def test_deny_cooldown_r46_and_per_kind_scope(web, monkeypatch):
    human, agent, env = setup_pair(web, mode="readwrite")
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    assert human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "deny"}).status_code == 200
    rec = docs()["approvals"]["items"][0]
    assert rec["state"] == "denied" and abs(rec["cooldown_until"] - rec["decided"] - limits.DENY_COOLDOWN) < 1e-6
    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 403 and r.json()["error"] == "request_denied" and 0 < r.json()["retryAfter"] <= 300
    assert aread(agent, env, "claude-a", "shell").json()["error"] == "request_denied"        # 반복해도 같다
    assert asend(agent, env, "claude-a", "shell", "x").status_code == 202                    # 다른 scope 집합은 별개
    assert_parity("denied")
    monkeypatch.setattr(agent_share, "DENY_COOLDOWN", 0)
    assert aread(agent, env, "claude-a", "shell").status_code == 202                         # 쿨다운 끝
    assert_parity("cooldown over")


def test_deny_decision_creates_no_grant_and_cannot_be_forced(web):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "deny"})
    assert docs()["grants"]["items"] == []
    assert human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "allow"}).status_code == 404   # 이미 처리됨
    assert aread(agent, env, "claude-a", "shell").status_code == 403


def test_limits_per_minute_and_pending_caps(web, monkeypatch):
    assert (agent_share.READ_PER_MIN, agent_share.SEND_PER_MIN) == (limits.LIMITS["session.read"][0],
                                                                    limits.LIMITS["session.input"][0]) == (30, 10)
    assert (agent_share.MAX_PENDING_PER_AGENT, agent_share.MAX_REQUESTS) == (limits.PENDING_PER_PRINCIPAL, limits.PENDING_TOTAL)
    assert agent_share.REQUEST_TTL == limits.PENDING_TTL and agent_share.DENY_COOLDOWN == limits.DENY_COOLDOWN
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    monkeypatch.setattr(agent_share, "SEND_PER_MIN", 2)
    monkeypatch.setattr(agent_share, "READ_PER_MIN", 3)
    assert [asend(agent, env, "claude-a", "shell", "y").status_code for _ in range(3)] == [200, 200, 429]
    assert [aread(agent, env, "claude-a", "shell").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert aread(agent, env, "claude-a", "shell").json()["error"] == "rate_limited"
    monkeypatch.setattr(agent_share, "MAX_PENDING_PER_AGENT", 1)
    env.mk("t2", "cat")
    share(human, "t2", "second")
    assert aread(agent, env, "claude-a", "second").status_code == 202          # 첫 보류
    env.mk("t3", "cat")
    share(human, "t3", "third")
    r = aread(agent, env, "claude-a", "third")
    assert r.status_code == 429 and r.json()["error"] == "too_many_requests"


def test_rate_limit_does_not_apply_before_a_grant_exists(web, monkeypatch):
    """보류 요청 경로는 속도 제한에 걸리지 않는다(옛 동작) — 엔진은 grant 가 있을 때만 한도를 센다."""
    human, agent, env = setup_pair(web)
    monkeypatch.setattr(agent_share, "READ_PER_MIN", 1)
    assert [aread(agent, env, "claude-a", "shell").status_code for _ in range(4)] == [202] * 4


def test_pending_requests_expire_after_24h_in_the_engine_store(web):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    items = store._read("approvals")[0]
    assert items["items"][0]["expires"] - items["items"][0]["created"] == limits.PENDING_TTL
    items["items"][0]["expires"] = time.time() - 1
    store.write_private(store.doc_path("approvals"), json.dumps(items).encode())
    assert human.get("/api/agent/share/requests").json()["requests"] == []
    assert docs()["approvals"]["items"] == [] and old_state()["requests"] == []
    assert aread(agent, env, "claude-a", "shell").json()["requestId"] != rid


# --- 정책: R4.3 / R4.4 / 자동 승인 window 없음 -----------------------------------------------------


def test_decision_authority_is_human_only_and_r43_needs_elevation():
    res = "session:local/$1:1"
    approvals.check_authority(P.human("d1"), ["session.view", "session.read", "session.input"], res)   # 일반 승인
    for bad in (P.legacy_token(), P.agent("$9:9"), P.unauthenticated(), P.cli(501, 1)):
        with pytest.raises(ShareError) as e:
            approvals.check_authority(bad, ["session.read"], res)
        assert e.value.code == "human_session_required" and e.value.status == 403
    raw = {"session.input": {"modes": ["raw"]}}
    with pytest.raises(ShareError) as e:                                     # raw 입력은 elevated 만
        approvals.check_authority(P.human("d1"), ["session.input"], res, raw)
    assert e.value.code == "elevation_required" and "session.input[raw]" in e.value.extra["scopes"]
    approvals.check_authority(P.human("d1", elevated=True), ["session.input"], res, raw)


def test_r44_escalation_is_detected_and_audited(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    dec = [r for r in audit_rows("share.decide")]
    esc = [r["detail"].get("escalation") for r in reversed(dec)]
    assert esc == [["new_grant"], ["scopes:session.input"]]            # 처음 = 새 grant, 쓰기 추가 = scope 확대
    # 좁히는 변경(쓰기 철회)은 확대가 아니다
    g = approvals.agent_grants(docs())[0]
    narrower = {**g, "scopes": ["session.view", "session.read"]}
    assert S.escalation(g, narrower) == [] and S.escalation(narrower, g) == ["scopes:session.input"]
    # 같은 scope 에서 조건 값을 늘리는 것(line -> line+raw)도 확대다
    wider = {**g, "sconds": {"session.input": {"modes": ["line", "raw"]}}}
    assert S.escalation(g, wider) == ["cond_widened:session.input.modes"] and S.escalation(wider, g) == []


def test_auto_approval_windows_are_absent_in_3a(web):
    """windows.json 에 window 가 있어도 3a 는 읽지 않는다 — 요청은 사람이 결정할 때까지 보류."""
    human, agent, env = setup_pair(web)
    with store.txn() as d:
        d["windows"]["items"].append({"id": "w1", "subject": "agent:local/*", "resources": ["session:local/*"],
                                      "covers": ["session.read"], "max_ttl_per_grant": 3600,
                                      "expires": time.time() + 3600, "state": "active"})
    assert approvals.auto_approve() is None
    assert aread(agent, env, "claude-a", "shell").status_code == 202
    assert docs()["grants"]["items"] == []


# --- 저장소 손상·장애 ---------------------------------------------------------------------------


def test_corrupt_engine_grants_fail_closed_and_refuse_writes(web):
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    p = store.doc_path("grants")
    good = p.read_text()
    p.write_text("{ not json")
    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 202                                           # F1: agent scope fail closed
    assert p.read_text() == "{ not json"                                  # 읽기·요청만으로는 원본을 안 덮는다
    assert aread(agent, env, "claude-a", "shell").status_code == 202
    bad = approve(human, r.json()["requestId"])                           # grant 문서를 다시 써야 하는 변경 = 거부
    assert bad.status_code == 503 and bad.json()["error"] == "store_unreadable"
    assert p.read_text() == "{ not json"                                  # 손상 파일은 지우지도 고치지도 않는다
    assert len(docs()["approvals"]["items"]) == 1                          # 요청은 그대로 남아 있다(승인 안 됨)
    for _ in range(2):
        assert approve(human, r.json()["requestId"]).status_code == 503
    backups = list(p.parent.glob("grants.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{ not json" and backups[0].stat().st_mode & 0o777 == 0o600
    p.write_text(good)                                                    # 사람이 파일을 되살리면 다시 쓸 수 있다
    assert approve(human, r.json()["requestId"]).status_code == 200
    assert aread(agent, env, "claude-a", "shell").status_code == 200


def test_audit_failure_blocks_changes_fail_closed_but_not_reads(web, monkeypatch):
    human, agent, env = setup_pair(web, mode="readwrite")
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    real = audit.append

    def boom(rec, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(audit, "append", boom)
    r = approve(human, rid)
    assert r.status_code == 503 and r.json()["error"] == "audit_unavailable"
    assert docs()["grants"]["items"] == [] and old_state()["grants"] == []        # 아무것도 저장되지 않았다
    assert len(docs()["approvals"]["items"]) == 1
    assert share(human, "target", "shell", "read").status_code == 503
    assert docs()["shares"]["items"][env.sess("target").key]["level"] == "readwrite"
    monkeypatch.setattr(audit, "append", real)
    assert approve(human, rid).status_code == 200
    monkeypatch.setattr(audit, "append", boom)
    assert aread(agent, env, "claude-a", "shell").status_code == 200              # 데이터 경로는 계속
    assert human.delete("/api/agent/share/grants/%s" % human.get("/api/agent/share/grants").json()["grants"][0]["id"]).status_code == 503
    assert len(docs()["grants"]["items"]) == 1


def test_engine_failure_after_enable_fails_closed(web, monkeypatch):
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    from authz import engine

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(engine, "decide", boom)
    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 503 and r.json()["error"] == "authz_unavailable" and "text" not in r.json()


def test_legacy_file_corrupt_does_not_wipe_engine_state_and_refuses_projection(web):
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    good = agent_share._path().read_text()
    agent_share._path().write_text("{ not json")
    assert aread(agent, env, "claude-a", "shell").status_code == 200       # 엔진 상태가 권한자
    assert agent_share._path().read_text() == "{ not json"                  # 읽기만으로는 원본을 안 덮는다
    env.mk("t2", "cat")
    share(human, "t2", "second")
    before = docs()["approvals"]["items"]
    r = aread(agent, env, "claude-a", "second")
    assert r.status_code == 503 and r.json()["error"] == "store_unreadable"  # 옛 파일에 투영할 수 없으니 변경 거부
    assert agent_share._path().read_text() == "{ not json"                  # 손상 파일은 그대로
    assert docs()["approvals"]["items"] == before                           # 엔진만 앞서가지도 않는다
    assert len(list(agent_share._path().parent.glob("agent_grants.json.corrupt-*"))) == 1
    agent_share._path().write_text(good)                                    # 사람이 되살리면 이어진다
    assert aread(agent, env, "claude-a", "second").status_code == 202
    assert_parity("recovered")


# --- 감사 -------------------------------------------------------------------------------------


def test_audit_is_chained_complete_and_never_has_content(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    secret = "echo top-secret-" + "payload-77"
    approve(human, asend(agent, env, "claude-a", "shell", secret).json()["requestId"], "readwrite")
    asend(agent, env, "claude-a", "shell", secret, enter=True)
    aread(agent, env, "claude-a", "shell", lines=5)
    asend(agent, env, "claude-a", "shell", "bad\nline")
    agent.get("/api/agent/shared", headers=env.ident("claude-a", pane="%999"))
    human.delete("/api/agent/share/sessions/target")
    p = audit.path()
    raw = p.read_text()
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert "top-secret" not in raw and "payload-77" not in raw and "ready-marker" not in raw
    ok, broken, n = audit.verify()
    assert ok and broken is None and n >= 6
    acts = {(r["action"], r["result"]) for r in audit_rows()}
    for want in (("share.share", "ok"), ("share.request", "pending"), ("share.decide", "allowed"), ("share.send", "ok"),
                 ("share.read", "ok"), ("share.list", "identity_unverified"), ("share.unshare", "ok")):
        assert want in acts, (want, acts)
    dec = next(r for r in audit_rows("share.decide"))
    assert dec["decision"] == "allow" and dec["grant_id"].startswith("ag-") and dec["approval_id"].startswith("ap-")
    assert dec["principal"].startswith("human:") and dec["resource"].startswith("session:local/")
    send = next(r for r in audit_rows("share.send") if r["result"] == "ok")
    assert send["sha8"] and send["sizes"]["chars"] == len(secret) and send["principal"].startswith("agent:local/")
    # UI 감사 조회는 옛 모양 그대로(authz 감사에서 읽는다)
    ents = human.get("/api/agent/share/audit?limit=50").json()["entries"]
    assert ents[0]["ts"] >= ents[-1]["ts"] and {"action", "result", "by"} <= set(ents[0])
    assert any(e["action"] == "decide" and e["by"] == "user" and e["level"] == "readwrite" for e in ents)
    # 옛 로그도 같이 쓴다(롤백 시 이어짐)
    assert (agent_share._audit_path()).is_file()


def test_successful_reads_are_sampled_once_per_ten_minutes(web):
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    for _ in range(5):
        assert aread(agent, env, "claude-a", "shell").status_code == 200
    assert len([r for r in audit_rows("share.read") if r["result"] == "ok"]) == 1       # R11.3
    legacy = [json.loads(x) for x in agent_share._audit_path().read_text().splitlines()]
    assert len([e for e in legacy if e["action"] == "read" and e["result"] == "ok"]) == 5      # 옛 로그는 전부 남는다
