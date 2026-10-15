"""거부 기록(`audit/denials.jsonl`) — 주 체인과 분리, 분당 60줄 + 누락 줄, 1MB x 3 회전, 0600, 내용 없음, 쓰기 실패는 요청을 막지 않는다."""

from __future__ import annotations

import json
import os
import stat

import pytest

from authz import audit, denylog


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    denylog.reset()
    yield


def deny(i=0, **over):
    return {"principal": f"agent:local/s{i}", "action": "share.send", "resource": "session:local/t", "scopes": ["session.input"],
            "decision": "deny", "result": "denied", "reason": "ceiling", **over}


def lines(p=None):
    p = p or denylog.path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_denials_leave_the_main_chain_alone_and_allows_stay():
    audit.append(deny())
    audit.append({"principal": "agent:local/s1", "action": "share.read", "resource": "session:local/t", "scopes": ["session.read"],
                  "decision": "allow", "result": "ok"})
    chain = lines(audit.path())
    assert [r["action"] for r in chain] == ["share.read"] and chain[0]["seq"] == 1
    d = lines()
    assert len(d) == 1 and d[0]["source"] == "denials" and "seq" not in d[0] and "prev_sha" not in d[0]
    ok, broken, n = audit.verify()
    assert ok and n == 1


@pytest.mark.parametrize("action", ["share.decide", "msg.decide", "peer.decide", "share.revoke", "msg.revoke", "authz.decide",
                                    "authz.revoke", "deleg.grant", "authz.panic"])
def test_mutations_with_a_deny_result_stay_in_the_chain(action):
    """사람이 요청을 거절한 결정은 거부 판정이 아니라 변경 이력이다."""
    audit.append({"principal": "human:dev:1", "action": action, "resource": "session:local/t", "decision": "deny", "result": "denied"})
    assert [r["action"] for r in lines(audit.path())] == [action] and lines() == []


def test_file_is_0600_in_a_0700_directory():
    denylog.record(deny())
    p = denylog.path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_sixty_per_minute_then_a_dropped_count_line():
    t0 = 6000.0
    wrote = [denylog.record(deny(i), now=t0 + i * 0.1) for i in range(75)]
    assert wrote.count(True) == 60 and wrote.count(False) == 15
    assert len(lines()) == 60
    assert denylog.record(deny(99), now=t0 + 61)                      # 다음 분: 누락 줄이 먼저, 그다음 이 줄
    rows = lines()
    assert len(rows) == 62
    assert rows[60]["action"] == "denylog.dropped" and rows[60]["dropped"] == 15 and rows[61]["principal"] == "agent:local/s99"
    assert denylog.STATE.total_dropped == 15


def test_a_quiet_minute_writes_no_dropped_line():
    for i in range(3):
        denylog.record(deny(i), now=100.0 + i)
    denylog.record(deny(), now=500.0)
    assert not [r for r in lines() if r["action"] == "denylog.dropped"]


def test_rotation_keeps_three_files_of_at_most_one_megabyte(monkeypatch):
    monkeypatch.setattr(denylog, "MAX_BYTES", 4000)
    big = "x" * 150
    t = 10_000.0
    for i in range(140):                                              # 분당 상한에 걸리지 않게 분을 넘기며 쓴다
        denylog.record(deny(i, reason=big), now=t + i * 61)
    p = denylog.path()
    names = sorted(x.name for x in p.parent.glob("denials.jsonl*"))
    assert names == ["denials.jsonl", "denials.jsonl.1", "denials.jsonl.2"]
    for x in p.parent.glob("denials.jsonl*"):
        assert x.stat().st_size <= 4000
    newest = [r["principal"] for r in denylog.read_recent(5)]
    assert newest[0] == "agent:local/s139"                            # 최근 것이 먼저, 가장 오래된 세대는 지워졌다
    assert "agent:local/s0" not in {r["principal"] for r in denylog.read_recent(1000)}


def test_no_content_ever_reaches_the_file():
    denylog.record(deny(text="TOPSECRET", content="TOPSECRET", screen="TOPSECRET", output="TOPSECRET",
                        detail={"text": "TOPSECRET", "chars": 9, "url": "https://x.example/p?token=TOPSECRET#frag"}))
    raw = denylog.path().read_text()
    assert "TOPSECRET" not in raw and "token=" not in raw
    assert json.loads(raw)["detail"]["chars"] == 9


def test_a_write_failure_never_raises_or_blocks(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(denylog, "_write_line", boom)
    assert denylog.record(deny()) is False
    assert denylog.STATE.failed == 1
    r = audit.append(deny())                                          # 감사 호출자는 예외를 받지 않는다
    assert r["source"] == "denials"
    assert denylog.STATE.failed == 2


def test_chain_write_failure_still_fails_closed_for_mutations(monkeypatch):
    """대비: 변경 기록은 여전히 못 쓰면 예외다(호출자가 503 audit_unavailable 로 중단한다)."""
    monkeypatch.setattr(audit, "path", lambda: __import__("pathlib").Path("/proc/nope/authz.jsonl"))
    with pytest.raises(OSError):
        audit.append({"principal": "human:dev:1", "action": "share.decide", "decision": "allow", "result": "allowed"})


def test_query_merges_both_sources_newest_first():
    audit.append({"principal": "human:dev:1", "action": "share.share", "resource": "session:local/a", "decision": "allow",
                  "result": "ok"}, now=100.0)
    denylog.record(deny(1), now=200.0)
    audit.append({"principal": "human:dev:1", "action": "share.unshare", "resource": "session:local/a", "decision": "allow",
                  "result": "ok"}, now=300.0)
    rows = audit.query(limit=10)
    assert [(r["source"], r["action"]) for r in rows] == [("chain", "share.unshare"), ("denials", "share.send"), ("chain", "share.share")]
    assert [r["source"] for r in audit.query(since=150.0)] == ["chain", "denials"]
    assert audit.read_merged(10, "share.")[0]["action"] == "share.unshare"
