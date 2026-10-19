"""브라우저 broker 의 HTTP 면 — 마스터 스위치(꺼짐 = 404), 호출자 분류(에이전트·터미널·사람·relay), 끝에서 끝까지의 pair 시나리오, MCP 도구, `fsh browser` CLI."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import browser.adapters as A
import mcp_farshell as m
from authz import browser_cli, inbox, leases
from authz import principals as P
from browser import broker, config, policy, tabs
from browser_kit import FakeAdapter, element, grant, page, seed
from test_browser_broker import AL, BO, CLI, H, HOME, web

PRINCIPALS = {"alpha": AL, "bravo": BO, "cli": CLI}


class _Inject:
    """소켓 게이트를 흉내 낸다 — `x-as` 헤더가 가리키는 주체를 `fsh_principal` 로 심고, `x-relay` 가 있으면 서버만 심을 수 있는 `vt_relay` 를 심는다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            who = h.get("x-as", "")
            if who in PRINCIPALS:
                scope["state"] = {"fsh_principal": PRINCIPALS[who]}
                scope["fsh_sock"] = True
            if h.get("x-relay"):
                scope["vt_relay"] = {"peer": h["x-relay"]}
        await self.app(scope, receive, send)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser")
    broker.reset_for_tests()
    tabs.drop_lease()
    fake = FakeAdapter(web())
    A.register("fake", lambda: fake)
    config.set_enabled("fake", True, consent=True)
    monkeypatch.setattr(broker, "exposed_ports", lambda: {3000})
    monkeypatch.setattr(policy, "resolve_host", lambda h: ["93.184.216.34"])
    from routes.browser import router
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(leases, "identify", lambda request: H if request.headers.get("x-as") == "human" else None)

    class E:
        pass
    e = E()
    e.fake, e.client = fake, TestClient(_Inject(app))
    yield e
    broker.shutdown()
    A.unregister("fake")
    broker.reset_for_tests()


def call(e, who, method, path, body=None, **hdr):
    return e.client.request(method, path, json=body if body is not None else {}, headers={"x-as": who, **hdr})


ALL_ROUTES = [
    ("GET", "/api/browser/adapters"), ("POST", "/api/browser/adapters/fake/enable"), ("POST", "/api/browser/tabs"), ("GET", "/api/browser/tabs"),
    *[("POST", f"/api/browser/tabs/bt-x/{a}") for a in ("navigate", "snapshot", "screenshot", "click", "type", "eval", "cookies", "storage", "network", "handoff")],
    ("DELETE", "/api/browser/tabs/bt-x"),
]


# --- 마스터 스위치 ----------------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["", "share,deleg,msg,peer,legacy,lease,pair", "Browsers", "browse"])
def test_flag_off_every_route_is_a_plain_404(env, monkeypatch, flag):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
    seed(grant(AL.id, ["browser", "browser.js", "browser.sensitive"], sconds={"browser.sensitive": {"ops": ["cookies"]}}))
    for method, path in ALL_ROUTES:
        for who in ("alpha", "cli", "human", ""):
            r = call(env, who, method, path)
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (method, path, who, r.status_code)
    assert not tabs.path().exists() and not config.path().parent.joinpath("tabs.lock").exists()      # 꺼진 서버는 아무 파일도 만들지 않는다


def test_flag_off_whoami_and_mcp_do_not_mention_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert "features" not in inbox.whoami(P.agent("$1:1"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser")
    assert inbox.whoami(P.agent("$1:1"))["features"] == {"browser": True}


# --- 호출자 --------------------------------------------------------------------------------------------

def test_unidentified_caller_is_refused(env):
    r = call(env, "", "GET", "/api/browser/tabs")
    assert r.status_code == 403 and r.json()["error"] == "identity_unverified"


def test_svc_and_hook_principals_are_refused(env):
    for p in (P.svc("tui", 501, 1), P.Principal(P.AGENT, "agent:local/$9:9", attrs={"key": "$9:9", "via": "hook"})):
        PRINCIPALS["svc"] = p
        r = call(env, "svc", "GET", "/api/browser/tabs")
        assert r.status_code == 403 and r.json()["error"] == "request_not_allowed"


def test_relay_caller_is_a_peer_and_needs_its_own_grant(env):
    r = call(env, "", "POST", "/api/browser/tabs", {"adapter": "fake"}, **{"x-relay": "pr1"})
    assert r.status_code == 403 and r.json()["error"] == "forbidden"                       # 기본 권한이 없다
    seed(grant("peer:pr1", ["browser"]))
    r = call(env, "", "POST", "/api/browser/tabs", {"adapter": "fake", "url": HOME}, **{"x-relay": "pr1"})
    assert r.status_code == 201
    tid = r.json()["tab"]["id"]
    r = call(env, "", "POST", f"/api/browser/tabs/{tid}/eval", {"script": "1"}, **{"x-relay": "pr1"})
    assert r.status_code == 403 and r.json()["error"] == "relay_forbidden"
    assert call(env, "", "POST", "/api/browser/adapters/fake/enable", {}, **{"x-relay": "pr1"}).status_code == 403


# --- 어댑터 켜기 ----------------------------------------------------------------------------------------

def test_agent_cannot_enable_but_cli_and_human_can(env):
    assert call(env, "alpha", "POST", "/api/browser/adapters/fake/enable").status_code == 403
    r = call(env, "cli", "POST", "/api/browser/adapters/fake/enable", {"enabled": False})
    assert r.status_code == 200 and not next(a for a in r.json()["adapters"] if a["name"] == "fake")["enabled"]
    r = call(env, "human", "POST", "/api/browser/adapters/fake/enable", {"enabled": True})
    assert r.status_code == 200 and next(a for a in r.json()["adapters"] if a["name"] == "fake")["enabled"]


def test_adapters_listing_shape_and_stub_reasons(env):
    rows = {a["name"]: a for a in call(env, "cli", "GET", "/api/browser/adapters").json()["adapters"]}
    assert set(rows) >= {"playwright", "cdp", "chrome-devtools-mcp", "gstack", "browserskill", "agent-browser", "fake"}
    for n in ("chrome-devtools-mcp", "gstack", "browserskill", "agent-browser"):
        assert rows[n]["available"] is False and rows[n]["reason"] == "not_implemented" and rows[n]["enabled"] is False and rows[n]["ops"] == []
    assert rows["fake"]["enabled"] is True and rows["fake"]["profile"] == "isolated"


# --- 종료 조건: 에이전트 pair 가 격리 탭으로 노출 포트를 조작하고, 민감한 것은 승인 없이는 막힌다 -----------------

def test_agent_pair_drives_an_isolated_tab_on_an_exposed_port_and_sensitive_things_stay_blocked(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser,pair")
    seed(grant(AL.id, ["browser"], pair_id="pr-pair000001"), grant(BO.id, ["browser"], pair_id="pr-pair000001"))
    # 1. alpha 가 격리 탭을 열어 노출 포트(localhost:3000)를 본다
    r = call(env, "alpha", "POST", "/api/browser/tabs", {"adapter": "fake", "url": HOME})
    assert r.status_code == 201, r.text
    tab = r.json()["tab"]
    tid = tab["id"]
    assert tab["adapter"] == "fake" and tab["pair"] == "pr-pair000001" and tab["navigate"]["title"] == "Home"
    # 2. 같은 pair 의 bravo 가 같은 탭을 조작한다
    snap = call(env, "bravo", "POST", f"/api/browser/tabs/{tid}/snapshot").json()
    assert snap["title"] == "Home" and snap["elements"][0]["ref"] == "e1"
    assert call(env, "bravo", "POST", f"/api/browser/tabs/{tid}/click", {"ref": "e1"}).status_code == 200
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/snapshot").json()["title"] == "Next"
    # 3. 노출하지 않은 포트·사설망·file: 은 막힌다
    for u in ("http://localhost:3001/", "http://10.0.0.7/", "file:///etc/passwd", "http://169.254.169.254/latest"):
        r = call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/navigate", {"url": u})
        assert r.status_code == 403 and r.json()["error"] == "url_blocked", u
    # 4. 승인 없이 막히는 것: eval · 쿠키 값 · 저장소 · 네트워크 · attach · 사용자 프로필
    for path, body in (("eval", {"script": "document.cookie"}), ("cookies", {"values": True}), ("cookies", {}), ("storage", {}), ("network", {})):
        r = call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/{path}", body)
        assert r.status_code == 403 and r.json()["error"] == "forbidden" and r.json()["need"]["scopes"][0].startswith("browser."), (path, r.text)
    r = call(env, "alpha", "POST", "/api/browser/tabs", {"attach": "someone-elses-tab"})
    assert r.status_code == 403 and r.json()["need"]["ops"] == ["attach"]
    r = call(env, "alpha", "POST", "/api/browser/tabs", {"profile": "user"})
    assert r.status_code == 501 and r.json()["error"] == "unsupported_profile"
    assert env.fake.calls.count(("eval", "f1")) == 0 and not any(c[0] in ("cookies", "storage", "network") for c in env.fake.calls)
    # 5. 사람이 승인하면(grant) 그 범위만 열린다 — 값은 values 조건이 따로 있어야 한다
    seed(grant(AL.id, ["browser.js"]), grant(AL.id, ["browser.sensitive"], sconds={"browser.sensitive": {"ops": ["cookies"]}}))
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/eval", {"script": "1"}).json() == {"result": "ok", "truncated": False}
    call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/navigate", {"url": HOME})
    c = call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/cookies", {}).json()
    assert c["cookies"][0]["name"] == "sid" and "SECRETCOOKIE" not in json.dumps(c)
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/cookies", {"values": True}).status_code == 403
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/network", {}).status_code == 403
    # 6. 닫기
    assert call(env, "bravo", "DELETE", f"/api/browser/tabs/{tid}").status_code == 200
    assert tabs.get(tid) is None


def test_third_agent_and_foreign_tab_are_refused_over_http(env):
    seed(grant(AL.id, ["browser"]), grant(BO.id, ["browser"]))
    tid = call(env, "alpha", "POST", "/api/browser/tabs", {"adapter": "fake"}).json()["tab"]["id"]
    r = call(env, "bravo", "POST", f"/api/browser/tabs/{tid}/snapshot")
    assert r.status_code == 403 and r.json()["why"] == "not_owner"
    assert call(env, "bravo", "GET", "/api/browser/tabs").json() == {"tabs": []}
    assert [t["id"] for t in call(env, "cli", "GET", "/api/browser/tabs").json()["tabs"]] == [tid]
    assert call(env, "cli", "DELETE", f"/api/browser/tabs/{tid}").status_code == 200


def test_http_error_shapes(env):
    seed(grant(AL.id, ["browser"]))
    tid = call(env, "alpha", "POST", "/api/browser/tabs", {"adapter": "fake"}).json()["tab"]["id"]
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/navigate", {}).status_code == 400
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/navigate", {"url": 5}).status_code == 400
    assert call(env, "alpha", "POST", "/api/browser/tabs/bt-none/snapshot").status_code == 404
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/download").status_code == 404        # 다운로드 조작은 없다
    r = env.client.post("/api/browser/tabs", content=b"not json", headers={"x-as": "alpha"})
    assert r.status_code == 201                          # 본문을 못 읽으면 빈 본문으로 본다(어댑터 선택은 켜진 첫 것)
    shot = call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/screenshot").json()
    assert shot["bytes"] > 0 and shot["png"]


def test_adapter_down_over_http_is_503_then_tab_orphaned(env):
    seed(grant(AL.id, ["browser"]))
    tid = call(env, "alpha", "POST", "/api/browser/tabs", {"adapter": "fake"}).json()["tab"]["id"]
    env.fake.set_down()
    assert call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/snapshot").status_code == 503
    r = call(env, "alpha", "POST", f"/api/browser/tabs/{tid}/snapshot")
    assert r.status_code == 503 and r.json()["error"] == "tab_orphaned"
    t = call(env, "alpha", "GET", "/api/browser/tabs").json()["tabs"][0]
    assert t["state"] == "orphaned" and t["owner"] == AL.id


# --- MCP -----------------------------------------------------------------------------------------------

@pytest.fixture
def mcp(monkeypatch):
    seen, st = [], {"reply": (200, {}), "who": {"scopes": ["browser", "session.read"], "features": {"browser": True}}}

    def fake(method, path, body=None, headers=None, timeout=None):
        seen.append((method, path, body))
        if path == "/api/authz/whoami":
            return (200, st["who"]) if st["who"] is not None else (403, {})
        if path == "/api/delegations/role":
            return 200, {"is_child": False, "can_spawn": False}
        r = st["reply"]
        return r(method, path) if callable(r) else r
    monkeypatch.setattr(m, "_api", fake)
    return seen, st


BROWSER = {"browser_list_adapters", "browser_open", "browser_navigate", "browser_snapshot", "browser_screenshot", "browser_click", "browser_type",
           "browser_eval", "browser_close", "browser_tabs", "browser_handoff"}


def test_mcp_tool_names_and_exposure(mcp):
    seen, st = mcp
    assert BROWSER == m.BROWSER_TOOL_NAMES
    names = lambda: {t["name"] for t in m.tools_for_caller()}      # noqa: E731
    n = names()
    assert (BROWSER - {"browser_eval"}) <= n and "browser_eval" not in n          # browser.js 가 있을 때만 eval 이 보인다
    st["who"] = {"scopes": ["browser", "browser.js"], "features": {"browser": True}}
    assert BROWSER <= names()
    st["who"] = {"scopes": ["session.read"], "features": {"browser": True}}        # 권한 없음
    assert not (BROWSER & names())
    st["who"] = {"scopes": ["browser", "browser.js"]}                              # 서버가 browser 를 켜지 않았다
    assert not (BROWSER & names())
    st["who"] = None
    assert not (BROWSER & names())


def test_mcp_tools_send_the_right_requests(mcp):
    seen, st = mcp
    st["reply"] = (201, {"tab": {"id": "bt-1", "adapter": "fake", "navigate": {"url": HOME, "title": "Home"}}})
    text, err = m.tool_browser_open({"url": HOME})
    assert not err and "bt-1" in text and seen[-1] == ("POST", "/api/browser/tabs", {"url": HOME})
    st["reply"] = (200, {"url": HOME, "title": "Home"})
    assert not m.tool_browser_navigate({"id": "bt-1", "url": HOME})[1] and seen[-1] == ("POST", "/api/browser/tabs/bt-1/navigate", {"url": HOME})
    st["reply"] = (200, {"url": HOME, "title": "Home", "text": "hello", "elements": [{"ref": "e1", "role": "button", "name": "Go"}]})
    text, err = m.tool_browser_snapshot({"id": "bt-1"})
    assert not err and "e1 [button] Go" in text and "hello" in text
    st["reply"] = (200, {"ok": True})
    assert not m.tool_browser_click({"id": "bt-1", "ref": "e1"})[1] and seen[-1] == ("POST", "/api/browser/tabs/bt-1/click", {"ref": "e1"})
    assert not m.tool_browser_type({"id": "bt-1", "ref": "e2", "text": "x", "submit": True})[1]
    assert seen[-1][2] == {"ref": "e2", "text": "x", "submit": True}
    st["reply"] = (200, {"result": {"a": 1}, "truncated": False})
    assert m.tool_browser_eval({"id": "bt-1", "script": "1"}) == ('{"a": 1}', False)
    st["reply"] = (200, {"png": "AAAA", "bytes": 3})
    content, err = m.tool_browser_screenshot({"id": "bt-1"})
    assert not err and content[0] == {"type": "image", "data": "AAAA", "mimeType": "image/png"}
    st["reply"] = (200, {"ok": True})
    assert not m.tool_browser_close({"id": "bt-1"})[1] and seen[-1][:2] == ("DELETE", "/api/browser/tabs/bt-1")
    assert not m.tool_browser_handoff({"id": "bt-1"})[1] and seen[-1][:2] == ("POST", "/api/browser/tabs/bt-1/handoff")
    st["reply"] = (200, {"tabs": [{"id": "bt-1", "state": "open", "adapter": "fake", "humanTaken": False}]})
    assert "bt-1 [open]" in m.tool_browser_tabs({})[0]
    st["reply"] = (200, {"adapters": [{"name": "fake", "enabled": True}, {"name": "gstack", "available": False, "reason": "not_implemented"}]})
    text, _ = m.tool_browser_list_adapters({})
    assert "fake: 켜짐" in text and "사용 불가(not_implemented)" in text


def test_mcp_failures_are_explained(mcp):
    seen, st = mcp
    st["reply"] = (403, {"error": "forbidden", "need": {"resource": "browser:local/tab/bt-1", "scopes": ["browser.js"]}})
    text, err = m.tool_browser_eval({"id": "bt-1", "script": "1"})
    assert err and "browser.js" in text and "request_access" in text
    st["reply"] = (403, {"error": "url_blocked", "urlReason": "not_allowlisted"})
    assert "not_allowlisted" in m.tool_browser_navigate({"id": "x", "url": "http://a"})[0]
    st["reply"] = (503, {"error": "tab_orphaned"})
    assert "orphaned" in m.tool_browser_snapshot({"id": "x"})[0]


def test_mcp_screenshot_goes_out_as_image_content(mcp, monkeypatch):
    seen, st = mcp
    st["reply"] = (200, {"png": "AAAA", "bytes": 3})
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "browser_screenshot", "arguments": {"id": "bt-1"}}})
    assert r["result"]["content"][0]["type"] == "image" and r["result"]["isError"] is False
    r = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "browser_close", "arguments": {"id": "bt-1"}}})
    assert r["result"]["content"][0]["type"] == "text"


# --- CLI -----------------------------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch):
    seen, st = [], {"reply": (200, {})}

    def fake(method, path, body=None, timeout=30.0):
        seen.append((method, path, body))
        r = st["reply"]
        return r(method, path) if callable(r) else r
    monkeypatch.setattr(browser_cli, "_call", fake)
    return seen, st


def test_cli_adapters_tabs_close(cli, capsys):
    seen, st = cli
    st["reply"] = (200, {"adapters": [{"name": "playwright", "profile": "isolated", "enabled": True, "available": True},
                                       {"name": "gstack", "profile": "isolated", "enabled": False, "available": False, "reason": "not_implemented"}]})
    assert browser_cli.main(["adapters"]) == 0 and seen[-1][:2] == ("GET", "/api/browser/adapters")
    out = capsys.readouterr().out
    assert "playwright" in out and "켜짐" in out and "not_implemented" in out
    st["reply"] = (200, {"tabs": [{"id": "bt-1", "state": "open", "adapter": "cdp", "ownerName": "alpha", "lastUsed": 0}]})
    assert browser_cli.main(["tabs"]) == 0 and "bt-1" in capsys.readouterr().out
    st["reply"] = (200, {"ok": True})
    assert browser_cli.main(["close", "bt-1"]) == 0 and seen[-1][:2] == ("DELETE", "/api/browser/tabs/bt-1")


def test_cli_enable_asks_consent_first_time(cli, capsys, monkeypatch):
    seen, st = cli
    replies = iter([(409, {"error": "consent_required", "consentText": "처음 켭니다"}), (200, {"adapters": []})])
    st["reply"] = lambda *_a: next(replies)
    monkeypatch.setattr(browser_cli, "_ask", lambda text: text == "처음 켭니다")
    assert browser_cli.main(["enable", "playwright"]) == 0
    assert seen[0][2] == {"enabled": True, "consent": False} and seen[1][2] == {"enabled": True, "consent": True}
    seen.clear()
    replies = iter([(409, {"error": "consent_required", "consentText": "x"})])
    monkeypatch.setattr(browser_cli, "_ask", lambda text: False)
    assert browser_cli.main(["enable", "playwright"]) == 1 and len(seen) == 1
    seen.clear()
    st["reply"] = (200, {"adapters": []})
    assert browser_cli.main(["enable", "cdp", "--yes"]) == 0 and seen[0][2] == {"enabled": True, "consent": True}
    assert browser_cli.main(["enable", "cdp", "--off"]) == 0 and seen[-1][2]["enabled"] is False


def test_cli_explains_a_server_with_the_feature_off(cli, capsys):
    seen, st = cli
    st["reply"] = (404, {"detail": "Not Found"})
    assert browser_cli.main(["adapters"]) == 1
    assert "browser" in capsys.readouterr().err


def test_cli_without_a_server(monkeypatch, capsys):
    def boom(*a, **k):
        raise browser_cli.Unreachable("no socket")
    monkeypatch.setattr(browser_cli, "_call", boom)
    assert browser_cli.main(["tabs"]) == 1
    assert "unix socket" in capsys.readouterr().err


# --- fsh doctor -----------------------------------------------------------------------------------


def _doctor(capsys) -> list:
    assert browser_cli.main(["doctor"]) == 0
    return capsys.readouterr().out.strip().split("|")


def test_doctor_line_reports_switch_adapters_tabs_and_ledger_errors(env, monkeypatch, capsys):
    """서버에 묻지 않고 설정·탭 장부만 읽는다 — `enabled|켠 어댑터|사용 가능|열린 탭|orphaned|설정 오류`."""
    en, on, av, opened, orph, err = _doctor(capsys)
    assert (en, opened, orph, err) == ("1", "0", "0", "0")
    assert int(on) >= 1 and int(av) >= int(on)                       # fake 어댑터가 켜져 있다
    tabs._write({"version": tabs.VERSION, "host": {"gen": 1}, "tabs": {
        "bt-a": {"state": tabs.OPEN}, "bt-b": {"state": tabs.ORPHANED}, "bt-c": {"state": tabs.RELEASED}}})
    assert _doctor(capsys)[3:] == ["1", "1", "0"]
    tabs.path().write_text("{broken", encoding="utf-8")                # 손상된 장부는 오류로 알린다(파일은 그대로)
    assert _doctor(capsys)[5] == "1" and tabs.path().read_text(encoding="utf-8") == "{broken"
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert _doctor(capsys)[0] == "0"
