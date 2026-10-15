"""`VT_AUTHZ_ENFORCE=peer`(3d): 이 서버에서 다른 맥을 조작하는 길(`/api/hosts/*`, `/ws/hosts/*`, `/ws/remote/*`)의 호출자 권한.

로그인한 사람은 `host:<id>` 의 모든 scope 를, `local`(cli·svc)은 목록·ping 만, 에이전트와 명시한 기계 토큰은 아무것도 못 가진다.
인증 수단이 없는 서버의 무인증 호출은 서버가 원래 열어 둔 것이라 그대로 허용한다. 기록 전체와의 비교는 `test_char_peer_enforced.py`.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")
    from authz import host_gate
    host_gate._denied_at.clear()          # 거부 감사의 60초 중복 억제는 프로세스 전역이다 — 시험끼리 섞이지 않게


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)




class _Conn:
    def __init__(self, principal=None):
        self.scope = {"fsh_sock": True} if principal is not None else {}
        self.state = type("S", (), {"fsh_principal": principal})()
        self.cookies, self.query_params, self.headers = {}, {}, {}


def test_host_scopes_by_caller_kind(world, monkeypatch):
    from authz import host_gate
    from authz import principals as P
    from authz import scopes as S
    callers = {
        "human": P.human("dev1"), "human_elev": P.human("dev1", elevated=True),
        "cli": P.cli(501, 4242), "svc": P.svc("tui", 501, 4243),
        "agent": P.agent("$1:1001"), "machine": P.legacy_token(), "open": P.open_server(),
    }
    asks = {
        "list": ("session.view", {}), "ping": ("ping", {}), "search": ("session.read", {}),
        "create": ("session.manage", {"op": "create"}), "kill": ("session.manage", {"op": "kill"}),
        "relay": ("relay", {"group": S.ANY_GROUP}), "relay_queue": ("relay", {"group": "queue"}),
        "relay_ws": ("relay", {"group": "ws.term"}),
    }
    want = {
        "human": set(asks), "human_elev": set(asks), "open": set(asks),
        "cli": {"list", "ping"}, "svc": {"list", "ping"}, "agent": set(), "machine": set(),
    }
    for name, p in callers.items():
        got = {k for k, (sc, ctx) in asks.items() if host_gate.decide(_Conn(p), "m5", sc, ctx).allow}
        assert got == want[name], (name, got ^ want[name])


def test_without_the_flag_nothing_about_defaults_changes(world, monkeypatch):
    from authz import defaults
    from authz import principals as P
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    ids = [r["id"] for r in defaults.rules_for(P.legacy_token())]
    assert ids == ["default:legacy_token"]
    assert not any("relay" in r["scopes"] for r in defaults.rules_for(P.human("d")))
    assert defaults.rules_for(P.cli(1, 2))[0]["id"] == "default:cli_deleg_close" and len(defaults.rules_for(P.cli(1, 2))) == 1
    assert defaults.rules_for(P.svc("tui", 1, 2)) == []


def test_the_machine_token_keeps_every_local_resource_but_other_hosts(world):
    from authz import engine, store
    from authz import principals as P
    snap = store.snapshot()
    m = P.legacy_token()
    for res, sc, ctx in (("session:local/x", "session.input", {"mode": "raw"}), ("host:local", "api.access", {}),
                         ("fs:local/tmp", "fs.write", {"op": "write"}), ("msg:local/x", "msg.send", {}),
                         ("deleg:local/x", "deleg.control", {"role": "parent"})):
        assert engine.decide(m, sc, res, ctx, snap).allow, (res, sc)
    assert not engine.decide(m, "session.manage", "host:m5", {"op": "kill"}, snap).allow


def test_the_host_routes_deny_the_machine_token_and_allow_a_human(world):
    world.set_mode("password")
    for who, status in (("machine", 403), ("session", 200)):
        h = world.headers_for(who, "POST", "/api/hosts/m5/ping", b"")
        r = client().post("/api/hosts/m5/ping", headers=h)
        assert r.status_code == status, (who, r.status_code, r.text)
    h = world.headers_for("machine", "POST", "/api/hosts/m5/ping", b"")
    assert client().post("/api/hosts/m5/ping", headers=h).json()["error"] == "host_access_denied"


def test_host_denials_are_audited_without_content(world):
    from authz import audit
    world.set_mode("password")
    h = world.headers_for("machine", "POST", "/api/hosts/m5/sessions", b"")
    client().post("/api/hosts/m5/sessions", json={"name": "secret-name-xyz"}, headers=h)
    rows = audit.read_recent(10, "host.")
    assert rows and rows[0]["decision"] == "deny" and rows[0]["resource"] == "host:m5"
    assert "secret-name-xyz" not in json.dumps(rows)


def test_the_flag_off_keeps_the_old_open_host_routes(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    world.set_mode("password")
    h = world.headers_for("machine", "POST", "/api/hosts/m5/ping", b"")
    assert client().post("/api/hosts/m5/ping", headers=h).status_code == 200
