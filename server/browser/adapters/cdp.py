"""`cdp` 어댑터 — Chrome DevTools Protocol 로 **이 어댑터가 직접 띄운** 격리된 headless Chromium 계열 브라우저를 다룬다.

  - 프로필은 `~/.vt/browser/` 아래 새 임시 디렉터리(0700)이고 서버 종료 때 지운다 — 사용자의 Chrome 프로필·로그인 세션에는 닿지 않는다(`profile=isolated`).
    이미 떠 있는 브라우저에 붙는 길은 **없다**(격리를 증명할 수 없다). 환경변수 `VT_BROWSER_BIN` 으로 실행 파일을 고를 수 있다.
  - **CDP 포트는 loopback 한정**이다: `--remote-debugging-address=127.0.0.1`, 포트는 임시(`0`), 연결 주소도 loopback 인지 `loopback_ws()` 가 확인한다. 외부에 내보내는 라우트는 없다.
  - 탭마다 새 브라우저 컨텍스트(쿠키·storage 분리), 다운로드 거부, popup 은 닫는다. 요청은 `Fetch.requestPaused` 로 가로채 `guard` 에 묻는다
    (redirect hop·서브프레임 문서·하위 리소스 — N-27).
  - 요소는 `snapshot()` 이 붙인 `data-fsh-ref` 로만 가리킨다. 마우스·키보드는 CDP `Input` 도메인으로 보낸다.
WebSocket 클라이언트는 필수 의존성 `websockets`(sync) 를 쓰고, 연결 직전에 지연 import 한다. 브라우저 실행 파일이 없으면 `available=False`(`browser_binary_not_found`).
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from . import _js
from .base import AdapterDown, AdapterError, BrowserAdapter, Guard

logger = logging.getLogger(__name__)

CMD_TIMEOUT = 15.0
NAV_WAIT = 15.0
LAUNCH_WAIT = 15.0
NET_KEEP = 100
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def loopback_ws(url: str) -> bool:
    """CDP 웹소켓 주소가 loopback 인가 — 아니면 붙지 않는다."""
    try:
        u = urlsplit(url)
        return u.scheme in ("ws", "wss") and (u.hostname or "") in _LOOPBACK
    except ValueError:
        return False


def find_binary() -> Optional[str]:
    env = os.environ.get("VT_BROWSER_BIN", "").strip()
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    cands = []
    if sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "microsoft-edge"):
        w = shutil.which(n)
        if w:
            cands.append(w)
    return next((c for c in cands if os.path.isfile(c) and os.access(c, os.X_OK)), None)


class _Tab:
    def __init__(self, ctx_id: str, target_id: str, session: str, guard: Guard):
        self.ctx_id, self.target_id, self.session, self.guard = ctx_id, target_id, session, guard
        self.main_frame = ""
        self.blocked: list = []          # 막힌 문서·서브프레임 로드의 사유
        self.sub_blocked = 0             # 막힌 하위 리소스 수
        self.net: list = []
        self.pending_req: dict = {}
        self.loaded = threading.Event()


class _Conn:
    """CDP 웹소켓 하나 — 읽기 스레드가 응답과 이벤트를 나눈다. 연결이 끊기면 대기 중인 호출은 모두 AdapterDown."""

    def __init__(self, ws, on_event):
        self.ws, self.on_event = ws, on_event
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self.down: Optional[str] = None
        self._t = threading.Thread(target=self._read, name="fsh-browser-cdp-reader", daemon=True)
        self._t.start()

    def _fail_all(self, why: str) -> None:
        with self._lock:
            self.down = self.down or why
            pend, self._pending = list(self._pending.values()), {}
        for f in pend:
            if not f.done():
                f.set_exception(AdapterDown(why))

    def _read(self) -> None:
        try:
            for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg:
                    with self._lock:
                        f = self._pending.pop(msg["id"], None)
                    if f is not None and not f.done():
                        if "error" in msg:
                            f.set_exception(AdapterError("cdp_error", str(msg["error"].get("message", ""))[:200]))
                        else:
                            f.set_result(msg.get("result") or {})
                elif "method" in msg:
                    try:
                        self.on_event(msg["method"], msg.get("params") or {}, msg.get("sessionId"))
                    except Exception as e:  # noqa: BLE001 — 이벤트 처리 실패가 읽기 스레드를 죽이면 안 된다
                        logger.debug(f"cdp 이벤트 처리 실패 {msg.get('method')}: {e}")
        except Exception as e:  # noqa: BLE001
            self._fail_all(f"connection_closed:{type(e).__name__}")
            return
        self._fail_all("connection_closed")

    def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None, wait: bool = True, timeout: float = CMD_TIMEOUT):
        if self.down:
            raise AdapterDown(self.down)
        i = next(self._ids)
        msg = {"id": i, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut: Future = Future()
        with self._lock:
            self._pending[i] = fut
        try:
            self.ws.send(json.dumps(msg))
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._pending.pop(i, None)
            self._fail_all(f"send_failed:{type(e).__name__}")
            raise AdapterDown("send_failed") from e
        if not wait:
            with self._lock:
                self._pending.pop(i, None)
            return None
        try:
            return fut.result(timeout)
        except TimeoutError as e:
            with self._lock:
                self._pending.pop(i, None)
            raise AdapterError("timeout", f"{method} timed out") from e

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class Adapter(BrowserAdapter):
    name = "cdp"
    profile = "isolated"
    intercepts = True
    description = "isolated headless Chromium via the DevTools protocol (loopback only)"

    def __init__(self, *, endpoint: Optional[str] = None) -> None:
        """`endpoint`: 시험용 — 이미 떠 있는 가짜 CDP 서버의 loopback ws 주소. 실제 사용에서는 항상 None(직접 띄운다)."""
        self._endpoint = endpoint
        self._conn: Optional[_Conn] = None
        self._proc: Optional[subprocess.Popen] = None
        self._profile: Optional[Path] = None
        self._tabs: dict[str, _Tab] = {}
        self._by_session: dict[str, _Tab] = {}
        self._contexts: set = set()
        self._lock = threading.RLock()
        self._dead: Optional[str] = None           # 연결이 끊긴 이유 — 새로 `open` 해서 다시 띄우기 전까지 «내려감»

    # --- 능력 ---------------------------------------------------------------------------------
    def capabilities(self) -> dict:
        if self._endpoint is None and find_binary() is None:
            return {"available": False, "reason": "browser_binary_not_found", "ops": [], "intercepts": True}
        try:
            import websockets.sync.client  # noqa: F401
        except ImportError:
            return {"available": False, "reason": "websockets_not_installed", "ops": [], "intercepts": True}
        return {"available": True, "reason": None, "ops": ["open", "list", "navigate", "snapshot", "screenshot", "click", "type", "eval",
                                                              "cookies", "storage", "network", "close"], "intercepts": True}

    def alive(self) -> bool:
        c = self._conn
        if c is None:
            return self._dead is None
        return c.down is None and (self._proc is None or self._proc.poll() is None)

    # --- 연결 ---------------------------------------------------------------------------------
    def _launch(self) -> str:
        binp = find_binary()
        if binp is None:
            raise AdapterDown("browser_binary_not_found")
        import vt_paths
        base = vt_paths.state_dir() / "browser"
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        self._profile = Path(tempfile.mkdtemp(prefix="cdp-", dir=str(base)))
        os.chmod(self._profile, 0o700)
        args = [binp, "--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self._profile}", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--disable-background-networking", "--disable-sync", "--password-store=basic", "--use-mock-keychain", "about:blank"]
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise AdapterDown(f"launch_failed:{type(e).__name__}") from e
        port_file = self._profile / "DevToolsActivePort"
        end = time.monotonic() + LAUNCH_WAIT
        while time.monotonic() < end:
            if self._proc.poll() is not None:
                raise AdapterDown("browser_exited")
            try:
                lines = port_file.read_text().splitlines()
                if len(lines) >= 2 and lines[0].isdigit():
                    return f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
            except OSError:
                pass
            time.sleep(0.1)
        raise AdapterDown("launch_timeout")

    def _connect(self) -> _Conn:
        if self._conn is not None:
            if self._conn.down or (self._proc is not None and self._proc.poll() is not None):
                why = self._conn.down or "browser_exited"
                self._teardown()
                self._dead = why
                raise AdapterDown(why)
            return self._conn
        self._dead = None
        try:
            url = self._endpoint or self._launch()
        except AdapterDown:
            self._teardown()                  # 실패한 실행이 임시 프로필·프로세스를 남기지 않는다
            raise
        if not loopback_ws(url):
            self._teardown()
            raise AdapterDown("cdp_endpoint_not_loopback")
        try:
            from websockets.sync.client import connect
            kw = {"max_size": 64 * 1024 * 1024, "open_timeout": 10, "ping_interval": None}
            try:
                ws = connect(url, proxy=None, **kw)
            except TypeError:                         # 옛 websockets 는 proxy 인자가 없다
                ws = connect(url, **kw)
        except Exception as e:  # noqa: BLE001
            self._teardown()
            raise AdapterDown(f"connect_failed:{type(e).__name__}") from e
        self._conn = _Conn(ws, self._on_event)
        try:
            self._conn.send("Target.setDiscoverTargets", {"discover": True})
        except AdapterError as e:
            self._teardown()
            raise AdapterDown("handshake_failed") from e
        return self._conn

    def _teardown(self) -> None:
        c, self._conn = self._conn, None
        if c is not None:
            c.close()
        p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None
        self._tabs.clear()
        self._by_session.clear()
        self._contexts.clear()

    # --- 이벤트 -------------------------------------------------------------------------------
    def _on_event(self, method: str, p: dict, session: Optional[str]) -> None:
        if method == "Target.targetCreated":
            info = p.get("targetInfo") or {}
            if info.get("type") == "page" and info.get("openerId"):            # popup — 닫는다
                c = self._conn
                if c is not None:
                    c.send("Target.closeTarget", {"targetId": info.get("targetId")}, wait=False)
            return
        tab = self._by_session.get(session or "")
        if tab is None:
            return
        c = self._conn
        if method == "Fetch.requestPaused" and c is not None:
            req = p.get("request") or {}
            rtype = p.get("resourceType") or ""
            if rtype == "Document":
                kind = "document" if p.get("frameId") == tab.main_frame else "subframe"
            else:
                kind = "subresource"
            try:
                why = tab.guard(str(req.get("url") or ""), kind)
            except Exception as e:  # noqa: BLE001 — 판정 오류 = 닫힘
                why = f"guard_error:{type(e).__name__}"
            if why:
                if kind == "subresource":
                    tab.sub_blocked += 1
                else:
                    tab.blocked.append(why)
                    del tab.blocked[:-20]
                c.send("Fetch.failRequest", {"requestId": p["requestId"], "errorReason": "BlockedByClient"}, session=session, wait=False)
            else:
                c.send("Fetch.continueRequest", {"requestId": p["requestId"]}, session=session, wait=False)
        elif method in ("Page.loadEventFired", "Page.domContentEventFired"):
            tab.loaded.set()
        elif method == "Network.requestWillBeSent":
            r = p.get("request") or {}
            tab.pending_req[p.get("requestId")] = {"method": r.get("method"), "url": r.get("url"), "type": p.get("type"),
                                                    "requestHeaders": r.get("headers") or {}, **({"postData": r["postData"]} if r.get("postData") else {})}
            if len(tab.pending_req) > 400:
                tab.pending_req.pop(next(iter(tab.pending_req)))
        elif method == "Network.responseReceived":
            row = tab.pending_req.pop(p.get("requestId"), None)
            if row is not None:
                resp = p.get("response") or {}
                row.update(status=resp.get("status"), responseHeaders=resp.get("headers") or {})
                tab.net.append(row)
                del tab.net[:-NET_KEEP]

    # --- 보조 ---------------------------------------------------------------------------------
    def _tab(self, ref: str) -> _Tab:
        t = self._tabs.get(ref)
        if t is None:
            raise AdapterError("tab_gone", "tab is gone")
        return t

    def _eval(self, t: _Tab, expr: str, *, await_promise: bool = False):
        r = self._connect().send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": await_promise,
                                                       "timeout": 10000}, session=t.session)
        if r.get("exceptionDetails"):
            raise AdapterError("eval_error", str((r["exceptionDetails"].get("exception") or {}).get("description") or r["exceptionDetails"].get("text") or "")[:300])
        return (r.get("result") or {}).get("value")

    # --- 탭 -----------------------------------------------------------------------------------
    def open(self, *, url: Optional[str], guard: Guard) -> dict:
        with self._lock:
            c = self._connect()
            ctx = c.send("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
            self._contexts.add(ctx)
            try:
                c.send("Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": ctx})
            except AdapterError:
                pass
            tid = c.send("Target.createTarget", {"url": "about:blank", "browserContextId": ctx})["targetId"]
            sess = c.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            tab = _Tab(ctx, tid, sess, guard)
            self._tabs[tid] = tab
            self._by_session[sess] = tab
            for m in ("Page.enable", "Network.enable"):
                c.send(m, session=sess)
            c.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, session=sess)
            tree = c.send("Page.getFrameTree", session=sess)
            tab.main_frame = ((tree.get("frameTree") or {}).get("frame") or {}).get("id", "")
        if url and url != "about:blank":
            self.navigate(tid, url)
        return {"ref": tid}

    def list(self) -> list:
        c = self._connect()
        infos = c.send("Target.getTargets").get("targetInfos") or []
        return [{"ref": i["targetId"], "url": i.get("url", "")} for i in infos if i.get("targetId") in self._tabs]

    def navigate(self, ref: str, url: str) -> dict:
        t = self._tab(ref)
        c = self._connect()
        t.blocked.clear()
        t.loaded.clear()
        r = c.send("Page.navigate", {"url": url}, session=t.session)
        if r.get("errorText"):
            if t.blocked:
                return {"url": self._eval(t, "location.href") or "", "title": "", "blocked": t.blocked[0]}
            raise AdapterError("navigate_failed", str(r["errorText"])[:100])
        t.loaded.wait(NAV_WAIT)
        return {"url": self._eval(t, "location.href") or "", "title": self._eval(t, "document.title") or "", "blocked": t.blocked[0] if t.blocked else None}

    def snapshot(self, ref: str) -> dict:
        t = self._tab(ref)
        raw = self._eval(t, _js.SNAPSHOT) or {}
        els = [{"ref": f"e{e['n']}", "role": e.get("role"), "tag": e.get("tag"), "type": e.get("type"), "name": e.get("name"),
                "native": _js.native(e["n"])} for e in raw.get("elements", [])]
        return {"url": raw.get("url"), "title": raw.get("title"), "text": raw.get("text", ""), "elements": els}

    def screenshot(self, ref: str) -> bytes:
        t = self._tab(ref)
        r = self._connect().send("Page.captureScreenshot", {"format": "png"}, session=t.session)
        return base64.b64decode(r.get("data") or "")

    def click(self, ref: str, native) -> None:
        t = self._tab(ref)
        box = self._eval(t, f"({_js.BOX})({json.dumps(str(native))})")
        if not box:
            raise AdapterError("element_gone", "element not found — take a new snapshot")
        c = self._connect()
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            c.send("Input.dispatchMouseEvent", {"type": typ, "x": box["x"], "y": box["y"], "button": "left", "buttons": 1 if typ == "mousePressed" else 0,
                                                "clickCount": 1}, session=t.session)

    def type(self, ref: str, native, text: str, submit: bool = False) -> None:
        t = self._tab(ref)
        if not self._eval(t, f"({_js.FOCUS})({json.dumps(str(native))})"):
            raise AdapterError("element_gone", "element not found — take a new snapshot")
        c = self._connect()
        c.send("Input.insertText", {"text": text}, session=t.session)
        if submit:
            for typ in ("keyDown", "keyUp"):
                c.send("Input.dispatchKeyEvent", {"type": typ, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"}, session=t.session)

    def eval(self, ref: str, script: str):
        return self._eval(self._tab(ref), script, await_promise=True)

    def cookies(self, ref: str, values: bool) -> list:
        t = self._tab(ref)
        rows = self._connect().send("Storage.getCookies", {"browserContextId": t.ctx_id}).get("cookies") or []
        return rows if values else [{k: v for k, v in r.items() if k != "value"} for r in rows]

    def storage(self, ref: str, values: bool) -> dict:
        return self._eval(self._tab(ref), f"({_js.STORAGE})({json.dumps(bool(values))})") or {}

    def network(self, ref: str) -> list:
        return [dict(r) for r in self._tab(ref).net]

    def frame_urls(self, ref: str) -> list:
        t = self._tab(ref)
        tree = self._connect().send("Page.getFrameTree", session=t.session).get("frameTree") or {}
        out: list = []

        def walk(n: dict) -> None:
            out.append((n.get("frame") or {}).get("url", ""))
            for ch in n.get("childFrames") or []:
                walk(ch)
        walk(tree)
        return out

    def settle(self, ref: str) -> None:
        time.sleep(0.4)

    def pop_blocked(self, ref: str) -> list:
        t = self._tab(ref)
        out, t.blocked = list(t.blocked), []
        return out

    def blank(self, ref: str) -> None:
        t = self._tab(ref)
        self._connect().send("Page.navigate", {"url": "about:blank"}, session=t.session)

    def close(self, ref: str) -> None:
        with self._lock:
            t = self._tabs.pop(ref, None)
            if t is None:
                return
            self._by_session.pop(t.session, None)
            c = self._conn
            if c is None or c.down:
                return
            for m, p in (("Target.closeTarget", {"targetId": t.target_id}), ("Target.disposeBrowserContext", {"browserContextId": t.ctx_id})):
                try:
                    c.send(m, p)
                except AdapterError:
                    pass
            self._contexts.discard(t.ctx_id)

    def shutdown(self) -> None:
        with self._lock:
            self._teardown()
