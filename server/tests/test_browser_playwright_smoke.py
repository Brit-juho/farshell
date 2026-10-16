"""실제 Playwright 로 격리 탭을 열어 FarShell 이 «노출한» 포트를 조작하는 smoke. Playwright 패키지(또는 Chromium)가 없으면 통째로 skip 한다 — CI 에서 깨지지 않는다.

이 시험은 사용자의 Chrome·프로필·실제 ~/.vt 에 닿지 않는다: 임시 상태 디렉터리, 임시 HTTP 서버(loopback 임시 포트), Playwright 가 직접 띄운 격리 Chromium.
"""

from __future__ import annotations

import http.server
import threading

import pytest

pytest.importorskip("playwright.sync_api")

import browser.adapters as A  # noqa: E402
from authz import principals as P  # noqa: E402
from authz.errors import ShareError  # noqa: E402
from browser import broker, config, policy, tabs  # noqa: E402
from browser_kit import grant, seed  # noqa: E402

AL = P.agent("$1:101", name="alpha")


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port + 1}/secret")
            self.end_headers()
            return
        body = (b"<html><head><title>FarShell Smoke</title></head><body><h1>hi</h1>"
                b"<input placeholder='name'><button onclick=\"document.title='clicked'\">Go</button></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "sid=SMOKESECRET; Path=/")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port
    monkeypatch.setattr(broker, "exposed_ports", lambda: {port})
    broker.reset_for_tests()
    tabs.drop_lease()
    cap = A.describe("playwright")
    if not cap["available"]:
        pytest.skip(f"playwright 사용 불가: {cap['reason']}")
    config.set_enabled("playwright", True, consent=True)
    seed(grant(AL.id, ["browser"]))
    yield port
    broker.shutdown()
    srv.shutdown()


def test_isolated_playwright_tab_drives_an_exposed_port(smoke):
    port = smoke
    try:
        tab = broker.open_tab(AL, {"adapter": "playwright", "url": f"http://localhost:{port}/"})
    except ShareError as e:
        if e.code in ("adapter_down", "adapter_unavailable"):
            pytest.skip(f"브라우저를 띄우지 못했다: {e.extra.get('adapterReason') or e.code}")
        raise
    tid = tab["id"]
    snap = broker.op(AL, tid, "snapshot")
    assert snap["title"] == "FarShell Smoke" and any(e["role"] == "button" for e in snap["elements"])
    btn = next(e["ref"] for e in snap["elements"] if e["role"] == "button")
    broker.op(AL, tid, "click", {"ref": btn})
    assert broker.op(AL, tid, "snapshot")["title"] == "clicked"
    # 노출하지 않은 포트로는 못 간다(redirect 로도)
    with pytest.raises(ShareError) as e:
        broker.op(AL, tid, "navigate", {"url": f"http://localhost:{port + 1}/"})
    assert e.value.code == "url_blocked"
    with pytest.raises(ShareError) as e:
        broker.op(AL, tid, "navigate", {"url": f"http://localhost:{port}/redir"})
    assert e.value.code == "url_blocked"
    # 승인 없이 막히는 것
    for name in ("eval", "cookies", "network"):
        with pytest.raises(ShareError) as e:
            broker.op(AL, tid, name, {"script": "document.cookie"})
        assert e.value.status == 403
    broker.close_tab(AL, tid)
    assert tabs.get(tid) is None


def test_policy_blocks_in_the_real_network_layer(smoke):
    port = smoke
    seed(grant(AL.id, ["browser.js"]))
    try:
        tid = broker.open_tab(AL, {"adapter": "playwright", "url": f"http://127.0.0.1:{port}/"})["id"]
    except ShareError as e:
        pytest.skip(f"브라우저를 띄우지 못했다: {e.code}")
    # 페이지 스크립트가 만든 이동도 가로채기·행동 뒤 재검사로 막힌다
    with pytest.raises(ShareError) as e:
        broker.op(AL, tid, "eval", {"script": "location.href = 'http://10.255.255.1/'"})
    assert e.value.code == "url_blocked"
    assert policy.check_url("http://10.255.255.1/", exposed_ports={port}, dns=False).allow is False
