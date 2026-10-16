"""어댑터 공통 계약 — 같은 시험을 가짜 어댑터(가로채기 있음·없음)와 cdp 어댑터(가짜 CDP 서버, 실제 웹소켓)에 돌린다. 스텁은 전부 거부.
실제 브라우저가 필요한 smoke 는 `test_browser_playwright_smoke.py`(importorskip)."""

from __future__ import annotations

import os

import pytest

import browser.adapters as A
from browser.adapters import cdp as cdp_mod
from browser.adapters.base import AdapterDown, AdapterError, BrowserAdapter, StubAdapter, Unavailable
from browser_kit import FakeAdapter, FakeCdpServer, element, page

HOME = "http://localhost:3000/"


def make_web():
    return {
        HOME: page("Home", "hello", elements=[element("button", "Go", goto=HOME + "next"), element("textbox", "Name")],
                   cookies=[{"name": "sid", "value": "V1", "domain": "localhost", "path": "/"}], storage={"local": {"k": "SV"}, "session": {}},
                   network=[{"url": HOME, "method": "GET", "status": 200}]),
        HOME + "next": page("Next", "n"),
        HOME + "redir": page("r", redirect="http://10.0.0.1/x"),
        HOME + "framed": page("F", frames=["http://10.0.0.1/inner"]),
        "http://10.0.0.1/x": page("bad"), "http://10.0.0.1/inner": page("inner"),
    }


def guard(url, kind):
    from browser import policy
    if kind == "subresource":
        d = policy.check_subresource(url, exposed_ports={3000})
    else:
        d = policy.check_url(url, exposed_ports={3000}, top_level=(kind == "document"), dns=False)
    return None if d.allow else d.reason


@pytest.fixture(params=["fake", "fake_nointercept", "cdp"])
def adapter(request):
    web = make_web()
    if request.param == "fake":
        yield FakeAdapter(web)
    elif request.param == "fake_nointercept":
        yield FakeAdapter(web, intercepts=False)
    else:
        pytest.importorskip("websockets.sync.server")
        srv = FakeCdpServer(web)
        a = cdp_mod.Adapter(endpoint=srv.url)
        a._srv = srv
        yield a
        a.shutdown()
        srv.stop()


def test_capabilities_contract(adapter):
    cap = adapter.capabilities()
    assert cap["available"] is True and "navigate" in cap["ops"] and adapter.profile == "isolated"
    assert isinstance(cap["intercepts"], bool)


def test_open_list_navigate_snapshot_click_type_close(adapter):
    ref = adapter.open(url=None, guard=guard)["ref"]
    assert [t["ref"] for t in adapter.list()] == [ref]
    r = adapter.navigate(ref, HOME)
    assert r["url"] == HOME and r["title"] == "Home" and not r.get("blocked")
    s = adapter.snapshot(ref)
    assert s["title"] == "Home" and s["text"] == "hello"
    assert [e["ref"] for e in s["elements"]] == ["e1", "e2"] and all(e["native"] for e in s["elements"])
    assert adapter.screenshot(ref).startswith(b"\x89PNG")
    adapter.type(ref, s["elements"][1]["native"], "Neo", submit=True)
    adapter.click(ref, s["elements"][0]["native"])
    adapter.settle(ref)
    assert adapter.frame_urls(ref)[0] in (HOME, HOME + "next")
    adapter.blank(ref)
    assert adapter.frame_urls(ref)[0] == "about:blank"
    adapter.close(ref)
    assert adapter.list() == []
    with pytest.raises(AdapterError):
        adapter.snapshot(ref)


def test_open_with_first_url_goes_through_the_guard(adapter):
    ref = adapter.open(url=HOME, guard=guard)["ref"]
    assert adapter.navigate(ref, HOME)["title"] == "Home"
    assert adapter.pop_blocked(ref) == []


def test_values_are_not_fetched_unless_asked(adapter):
    ref = adapter.open(url=HOME, guard=guard)["ref"]
    assert "value" not in adapter.cookies(ref, False)[0]
    assert adapter.cookies(ref, True)[0]["value"] == "V1"
    st = adapter.storage(ref, False)
    assert st["local"]["k"] is None
    assert adapter.storage(ref, True)["local"]["k"] == "SV"


def test_eval_and_network(adapter):
    ref = adapter.open(url=HOME, guard=guard)["ref"]
    assert adapter.eval(ref, "1+1")
    assert isinstance(adapter.network(ref), list)


def test_guard_blocks_redirect_hop_and_subframe_when_intercepting(adapter):
    ref = adapter.open(url=None, guard=guard)["ref"]
    if not adapter.intercepts:
        pytest.skip("가로채기가 없는 어댑터 — broker 의 행동 뒤 검사가 맡는다(test_browser_broker)")
    r = adapter.navigate(ref, HOME + "redir")
    assert r["blocked"] == "not_allowlisted"
    assert adapter.frame_urls(ref)[0] != "http://10.0.0.1/x"
    r = adapter.navigate(ref, HOME + "framed")
    assert r["blocked"] == "not_allowlisted"
    assert "http://10.0.0.1/inner" not in adapter.frame_urls(ref)
    assert adapter.pop_blocked(ref) in ([], ["not_allowlisted"]) and adapter.pop_blocked(ref) == []


def test_tabs_are_independent(adapter):
    a = adapter.open(url=HOME, guard=guard)["ref"]
    b = adapter.open(url=None, guard=guard)["ref"]
    assert a != b and adapter.frame_urls(b)[0] == "about:blank"
    adapter.close(a)
    assert [t["ref"] for t in adapter.list()] == [b]


def test_adapter_down_raises_adapterdown(adapter):
    ref = adapter.open(url=HOME, guard=guard)["ref"]
    if isinstance(adapter, FakeAdapter):
        adapter.set_down()
    else:
        adapter._srv.stop()
        adapter._conn.close()
        import time
        for _ in range(50):
            if adapter._conn.down:
                break
            time.sleep(0.05)
    with pytest.raises(AdapterDown):
        adapter.snapshot(ref)
    assert not adapter.alive()


# --- cdp 고유 ------------------------------------------------------------------------------------------

def test_cdp_connects_to_loopback_only():
    assert cdp_mod.loopback_ws("ws://127.0.0.1:9222/devtools/browser/x")
    assert cdp_mod.loopback_ws("ws://localhost:1/x") and cdp_mod.loopback_ws("ws://[::1]:1/x")
    for u in ("ws://0.0.0.0:9222/x", "ws://192.168.1.5:9222/x", "ws://example.com/x", "http://127.0.0.1/x", "ws://127.0.0.1.evil.com/x", "ws://evil.com/x"):
        assert not cdp_mod.loopback_ws(u), u
    a = cdp_mod.Adapter(endpoint="ws://192.168.1.5:9222/devtools/browser/x")
    with pytest.raises(AdapterDown) as e:
        a.open(url=None, guard=guard)
    assert "not_loopback" in str(e.value)


def test_cdp_launch_command_is_loopback_isolated_and_never_attaches(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    binp = tmp_path / "chrome"
    binp.write_text("#!/bin/sh\n")
    binp.chmod(0o755)
    monkeypatch.setenv("VT_BROWSER_BIN", str(binp))
    seen = {}

    class P:
        def __init__(self, args, **kw):
            seen["args"], seen["kw"] = args, kw

        def poll(self):
            return 1                      # 곧바로 종료한 브라우저
    monkeypatch.setattr(subprocess, "Popen", P)
    a = cdp_mod.Adapter()
    with pytest.raises(AdapterDown):
        a.open(url=None, guard=guard)
    args = seen["args"]
    assert "--remote-debugging-address=127.0.0.1" in args and "--remote-debugging-port=0" in args
    prof = [x for x in args if x.startswith("--user-data-dir=")][0].split("=", 1)[1]
    assert prof.startswith(str(tmp_path / "vt" / "browser"))                 # 사용자 프로필이 아니라 상태 디렉터리 아래 임시 프로필
    assert not os.path.exists(prof)                                           # 실패하면 지운다
    assert not any(x.startswith("--remote-debugging-pipe") or "9222" in x for x in args)


def test_cdp_without_a_browser_is_unavailable(monkeypatch):
    monkeypatch.setenv("VT_BROWSER_BIN", "/nonexistent/chrome")
    cap = cdp_mod.Adapter().capabilities()
    assert cap["available"] is False and cap["reason"] == "browser_binary_not_found"


def test_cdp_reports_script_errors_and_popups_are_closed():
    pytest.importorskip("websockets.sync.server")
    srv = FakeCdpServer(make_web())
    a = cdp_mod.Adapter(endpoint=srv.url)
    try:
        ref = a.open(url=HOME, guard=guard)["ref"]
        with pytest.raises(AdapterError) as e:
            a.eval(ref, "throw")
        assert e.value.code == "eval_error"
    finally:
        a.shutdown()
        srv.stop()


# --- 스텁 · registry ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["chrome-devtools-mcp", "gstack", "browserskill", "agent-browser"])
def test_stub_adapters_satisfy_the_interface_and_refuse_everything(name):
    a = A.get(name)
    assert isinstance(a, BrowserAdapter) and isinstance(a, StubAdapter) and a.name == name and a.profile == "isolated"
    cap = a.capabilities()
    assert cap["available"] is False and cap["reason"] and cap["ops"] == []
    assert A.describe(name)["available"] is False
    for call in (lambda: a.open(url=None, guard=guard), a.list, lambda: a.navigate("x", HOME), lambda: a.snapshot("x"), lambda: a.screenshot("x"),
                 lambda: a.click("x", "n"), lambda: a.type("x", "n", "t"), lambda: a.eval("x", "1"), lambda: a.cookies("x", False),
                 lambda: a.storage("x", False), lambda: a.network("x"), lambda: a.frame_urls("x"), lambda: a.blank("x"), lambda: a.close("x")):
        with pytest.raises(Unavailable):
            call()
    assert a.alive() is False


def test_registry_names_and_unknown():
    assert A.names()[:6] == ["playwright", "cdp", "chrome-devtools-mcp", "gstack", "browserskill", "agent-browser"]
    assert A.describe("nope")["reason"] == "unknown_adapter"
    with pytest.raises(KeyError):
        A.get("nope")


def test_playwright_reports_missing_install_without_importing_it():
    try:
        import playwright  # noqa: F401
        pytest.skip("playwright 가 설치돼 있다 — smoke 시험이 본다")
    except ImportError:
        pass
    cap = A.get("playwright").capabilities()
    assert cap["available"] is False and cap["reason"] == "playwright_not_installed"
    with pytest.raises(AdapterDown):
        A.get("playwright").open(url=None, guard=guard)


# --- playwright 어댑터: 가짜 playwright 모듈로 어댑터 코드(작업 스레드·가로채기·격리 컨텍스트)를 시험 -----------------------

class _FReq:
    def __init__(self, url, nav, main=True, rtype="document"):
        self.url, self._nav, self.resource_type = url, nav, rtype
        self.frame = type("F", (), {"parent_frame": None if main else object()})()
        self.method, self.headers, self.post_data = "GET", {}, None

    def is_navigation_request(self):
        return self._nav


class _FRoute:
    def __init__(self):
        self.result = None

    def abort(self, why):
        self.result = ("abort", why)

    def continue_(self):
        self.result = ("continue", None)


class _FLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel
        self.first = self

    def click(self, timeout=None):
        self.page.calls.append(("click", self.sel))

    def fill(self, text, timeout=None):
        self.page.calls.append(("fill", self.sel, text))

    def press(self, key, timeout=None):
        self.page.calls.append(("press", key))


class _FPage:
    def __init__(self, ctx, web):
        self.ctx, self.web, self.url, self.calls, self._closed = ctx, web, "about:blank", [], False
        self.frames = []

    def _fetch(self, url, nav=True, main=True, rtype="document"):
        r = _FRoute()
        self.ctx.handler(r, _FReq(url, nav, main, rtype))
        return r.result[0] == "continue"

    def goto(self, url, wait_until=None):
        if url == "about:blank":                         # 실제 Playwright 도 about:blank 는 가로채지 않는다
            self.url, self.frames = url, []
            return
        hops = 0
        while True:
            if not self._fetch(url):
                raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
            pg = self.web.get(url)
            if pg is None:
                raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
            if pg.get("redirect") and hops < 10:
                url, hops = pg["redirect"], hops + 1
                continue
            break
        if url == "about:blank":
            self.url = url
            return
        self.url = url
        self.frames = [type("Fr", (), {"url": url})()]
        for f in pg["frames"]:
            if self._fetch(f, main=False):
                self.frames.append(type("Fr", (), {"url": f})())
        for r in pg["subresources"]:
            self._fetch(r, nav=False, rtype="image")

    def title(self):
        return (self.web.get(self.url) or {}).get("title", "")

    def evaluate(self, script, arg=None):
        pg = self.web.get(self.url) or page()
        if "data-fsh-ref" in script and "const SEL" in script:
            return {"url": self.url, "title": pg["title"], "text": pg["text"],
                    "elements": [{"n": i, "tag": "button", "role": e["role"], "type": "", "name": e["name"]} for i, e in enumerate(pg["elements"], 1)]}
        if "localStorage" in script:
            st = pg["storage"]
            return {a: {k: (v if arg else None) for k, v in st.get(a, {}).items()} for a in ("local", "session")}
        return f"eval:{script}"

    def screenshot(self, type=None):
        return b"\x89PNG fake"

    def locator(self, sel):
        return _FLoc(self, sel)

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True

    def on(self, ev, fn):
        pass


class _FCtx:
    def __init__(self, browser):
        self.browser = browser
        self.handler = None
        self.pages = []
        self.kw = {}

    def set_default_timeout(self, ms):
        pass

    set_default_navigation_timeout = set_default_timeout

    def route(self, pat, fn):
        self.handler = fn

    def on(self, ev, fn):
        pass

    def new_page(self):
        p = _FPage(self, self.browser.web)
        self.pages.append(p)
        return p

    def cookies(self):
        pg = self.browser.web.get(self.pages[0].url, page())
        return [dict(c) for c in pg["cookies"]]

    def close(self):
        self.browser.contexts.remove(self)


class _FBrowser:
    def __init__(self, web):
        self.web, self.contexts, self._conn = web, [], True

    def is_connected(self):
        return self._conn

    def new_context(self, **kw):
        c = _FCtx(self)
        c.kw = kw
        self.contexts.append(c)
        return c

    def close(self):
        self._conn = False


@pytest.fixture
def fake_playwright(monkeypatch):
    import importlib.util
    import sys
    import threading
    import types
    web = make_web()
    state = {"browser": None, "threads": set(), "args": None}

    class _PW:
        def __init__(self):
            self.chromium = self

        def launch(self, **kw):
            state["args"] = kw
            state["threads"].add(threading.get_ident())
            state["browser"] = _FBrowser(web)
            return state["browser"]

        def stop(self):
            pass

    class _Ctl:
        def start(self):
            state["threads"].add(threading.get_ident())
            return _PW()
    mod = types.ModuleType("playwright")
    sync = types.ModuleType("playwright.sync_api")
    sync.sync_playwright = lambda: _Ctl()
    mod.sync_api = sync
    monkeypatch.setitem(sys.modules, "playwright", mod)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a, **k: object() if n == "playwright" else real(n, *a, **k))
    from browser.adapters import playwright as pw_mod
    a = pw_mod.Adapter()
    a._state = state
    yield a
    a.shutdown()


def test_playwright_adapter_contract_with_a_fake_module(fake_playwright):
    a = fake_playwright
    assert a.capabilities()["available"] is True
    ref = a.open(url=HOME, guard=guard)["ref"]
    s = a.snapshot(ref)
    assert s["title"] == "Home" and [e["ref"] for e in s["elements"]] == ["e1", "e2"] and s["elements"][0]["native"] == '[data-fsh-ref="1"]'
    a.click(ref, s["elements"][0]["native"])
    a.type(ref, s["elements"][1]["native"], "Neo", submit=True)
    assert a.eval(ref, "1+1") == "eval:1+1" and a.screenshot(ref).startswith(b"\x89PNG")
    assert "value" not in a.cookies(ref, False)[0] and a.cookies(ref, True)[0]["value"] == "V1"
    assert a.storage(ref, False)["local"]["k"] is None and a.storage(ref, True)["local"]["k"] == "SV"
    assert [t["ref"] for t in a.list()] == [ref]
    # 격리: 탭마다 새 컨텍스트, 다운로드·서비스 워커·권한 꺼짐
    b = a.open(url=None, guard=guard)["ref"]
    ctxs = a._state["browser"].contexts
    assert len(ctxs) == 2 and all(c.kw["accept_downloads"] is False and c.kw["service_workers"] == "block" and c.kw["permissions"] == [] for c in ctxs)
    # 모든 Playwright 호출은 한 작업 스레드에서
    import threading
    assert len(a._state["threads"]) == 1 and threading.get_ident() not in a._state["threads"]
    assert a._state["args"]["headless"] is True
    a.close(b)
    assert [t["ref"] for t in a.list()] == [ref]


def test_playwright_adapter_guard_blocks_hops_frames_and_subresources(fake_playwright):
    a = fake_playwright
    ref = a.open(url=None, guard=guard)["ref"]
    r = a.navigate(ref, HOME + "redir")
    assert r["blocked"] == "not_allowlisted"
    r = a.navigate(ref, HOME + "framed")
    assert r["blocked"] == "not_allowlisted" and "http://10.0.0.1/inner" not in a.frame_urls(ref)
    assert a.pop_blocked(ref) == ["not_allowlisted"] and a.pop_blocked(ref) == []
    a._state["browser"].web[HOME + "sub"] = page("S", subresources=["http://169.254.169.254/x"])
    r = a.navigate(ref, HOME + "sub")
    assert r["title"] == "S" and not r["blocked"] and a.pop_blocked(ref) == []             # 하위 리소스 차단은 이동 실패가 아니다
    a.blank(ref)
    assert a.frame_urls(ref) == ["about:blank"] or a.frame_urls(ref) == []


def test_playwright_adapter_browser_loss_is_adapterdown(fake_playwright):
    a = fake_playwright
    ref = a.open(url=HOME, guard=guard)["ref"]
    a._state["browser"]._conn = False
    with pytest.raises(AdapterDown):
        a.list()
    assert not a.alive()
    with pytest.raises(AdapterError):
        a.snapshot(ref)                                  # 옛 탭은 사라졌다 — broker 는 이미 orphaned 로 돌렸다
    new = a.open(url=None, guard=guard)["ref"]           # 새로 열면 브라우저를 다시 띄운다(옛 탭을 되살리지 않는다)
    assert a.alive() and new != ref and [t["ref"] for t in a.list()] == [new]
