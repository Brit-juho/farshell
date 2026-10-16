"""다른 Mac 이 relay 로 브라우저 broker 에 닿는 길 — `browser` relay 그룹은 «명시해야만» 열린다.

기본 그룹 목록(26개)·`control` 등급·`*`·사람 기본에 들지 않고, 옛 등급(권한 엔진 꺼짐)만으로는 절대 열리지 않는다.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import relay_policy
from authz import scopes as S


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def _relay(world, who, method, path):
    world.set_mode("password")
    p = "/api/peer/relay" + path
    h = world.headers_for(who, method, p, b"")
    r = client().request(method, p, headers=h)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else None


def test_browser_group_is_outside_the_default_groups_and_everything_that_expands_them():
    assert "browser" not in S.ALL_RELAY_GROUPS and len(S.ALL_RELAY_GROUPS) == 26
    assert "browser" in S.OPTIN_RELAY_GROUPS
    star = S.effective(["relay"], {"relay": {"groups": ["*"]}})["relay"]["groups"]
    assert "browser" not in star and "browser" not in S.effective(["*"])["relay"]["groups"]
    named = S.effective(["relay"], {"relay": {"groups": ["browser"]}})["relay"]["groups"]
    assert named == frozenset({"browser"})
    assert "browser" in S.effective(["relay"], {"relay": {"groups": ["*"]}}, deny=True)["relay"]["groups"]       # 금지 규칙은 넓게 막는다
    assert "browser" in S.effective(["relay"], None, deny=True)["relay"]["groups"]
    for name in S.PRESET_ORDER:
        sc, cd = S.preset(name)
        assert "browser" not in (cd.get("relay") or {}).get("groups", [])
    from authz import defaults
    assert "browser" not in defaults.PEER_CONTROL_SCONDS["relay"]["groups"]
    assert "browser" not in defaults.peer_preset("control")[1]["relay"]["groups"]


def test_policy_helpers():
    assert not relay_policy.http_allowed("GET", "/api/browser/tabs")                  # 기본 허용 목록은 그대로다
    assert relay_policy.optin_group("GET", "/api/browser/tabs") == "browser"
    assert relay_policy.optin_group("POST", "/api/browser/tabs/bt-1/navigate") == "browser"
    assert relay_policy.optin_group("GET", "/api/browser/adapters") == "browser"
    for bad in ("/api/browser/../authz/grants", "/api/browser//tabs", "/api/browser/tabs/..", "/api/browserx", "/api/browser", "/api/browser/other"):
        assert relay_policy.optin_group("GET", bad) is None, bad
    assert S.relay_group("GET", "/api/browser/tabs") == "browser" and S.relay_group("GET", "/api/workspace") == "workspace"
    for rx, _m in relay_policy._HTTP_RULES:                                            # 기본 규칙 표에는 브라우저가 없다
        assert not rx.fullmatch("/api/browser/tabs")


@pytest.mark.parametrize("flag", ["peer", "peer,browser"])
def test_control_level_peer_cannot_relay_to_the_browser(world, monkeypatch, flag):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
    for method, path in (("GET", "/api/browser/tabs"), ("POST", "/api/browser/tabs"), ("GET", "/api/browser/adapters")):
        st, j = _relay(world, "peer_control", method, path)
        assert (st, j["error"]) == (403, "relay_forbidden"), (flag, method, path, st, j)


def test_legacy_level_alone_never_opens_it(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser")                                  # peer 강제 꺼짐 — 옛 등급 경로
    st, j = _relay(world, "peer_control", "GET", "/api/browser/tabs")
    assert (st, j["error"]) == (403, "relay_forbidden")


def test_a_grant_naming_the_browser_group_opens_the_relay_gate(world, monkeypatch):
    from authz import store
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer,browser")
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "g-br", "subject": "peer:pc", "resource": "relay:local/browser", "scopes": ["relay"],
                                        "sconds": {"relay": {"groups": ["browser"]}}, "state": "active", "schema": 2, "approved_by": "human:t",
                                        "approved_via": "manual", "expires": None, "conds": {}})
    st, j = _relay(world, "peer_control", "GET", "/api/browser/tabs")
    assert st == 200 and j == {"relayDispatched": True}                                # 관문은 통과 — 안쪽 라우트가 자기 grant 로 다시 판정한다
    st, j = _relay(world, "peer_control", "GET", "/api/workspace")
    assert st == 200
