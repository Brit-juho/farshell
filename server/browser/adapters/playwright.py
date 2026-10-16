"""`playwright` 어댑터 — 격리된 headless Chromium. 선택 의존성(`playwright` 패키지 + `playwright install chromium`)이라 지연 import 한다.

  - 탭마다 **새 브라우저 컨텍스트**(쿠키·storage 가 탭끼리도, 사용자 프로필과도 섞이지 않는다). 다운로드·서비스 워커·권한 요청은 끈다. 새 창(popup)은 닫는다.
  - 모든 요청을 컨텍스트 라우트로 가로채 `guard` 에 묻는다(redirect hop·서브프레임 포함, N-27). 막힌 요청은 `blockedbyclient` 로 끊고 이유를 기록한다.
  - Playwright 의 sync API 는 만든 스레드에 묶이므로 **전용 작업 스레드 하나**에서 모든 호출을 실행한다(`_Worker`).
  - 요소는 `snapshot()` 이 붙인 `data-fsh-ref` 로만 가리킨다 — 에이전트가 selector 를 넘기지 않는다.
이 머신에 Playwright 가 없으면 `capabilities()` 가 `available=False`(`playwright_not_installed`)이고 서버·CI 는 그대로 돈다.
"""

from __future__ import annotations

import importlib.util
import itertools
import queue
import threading
from concurrent.futures import Future
from typing import Callable, Optional

from . import _js
from .base import AdapterDown, AdapterError, BrowserAdapter, Guard

OP_TIMEOUT = 20.0
NAV_TIMEOUT_MS = 15000
ACTION_TIMEOUT_MS = 5000
NET_KEEP = 100
_DOWN_MARKERS = ("has been closed", "Target closed", "Browser closed", "Connection closed", "browser has been closed", "Target page, context or browser")


class _Worker:
    """한 스레드에서 함수를 차례로 실행한다 — Playwright sync 객체를 만든 스레드에서만 쓰기 위해."""

    def __init__(self, name: str):
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._run, name=name, daemon=True)
        self._t.start()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

    def call(self, fn: Callable, timeout: float = OP_TIMEOUT):
        fut: Future = Future()
        self._q.put((fn, fut))
        try:
            return fut.result(timeout)
        except TimeoutError as e:
            raise AdapterError("timeout", "browser operation timed out") from e

    def stop(self) -> None:
        self._q.put(None)


def _is_down(e: BaseException) -> bool:
    s = str(e)
    return any(m in s for m in _DOWN_MARKERS)


class _Tab:
    def __init__(self, context, page, guard: Guard):
        self.context, self.page, self.guard = context, page, guard
        self.blocked: list = []          # 막힌 문서·서브프레임 로드의 사유
        self.sub_blocked = 0             # 막힌 하위 리소스 수(이동 실패로 보지 않는다)
        self.net: list = []


class Adapter(BrowserAdapter):
    name = "playwright"
    profile = "isolated"
    intercepts = True
    description = "isolated headless Chromium via Playwright"

    def __init__(self) -> None:
        self._w: Optional[_Worker] = None
        self._pw = None
        self._browser = None
        self._tabs: dict[str, _Tab] = {}
        self._dead = False                 # 브라우저 연결이 끊긴 것을 본 뒤 — 새로 띄우기 전까지 «내려감»
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # --- 능력 ---------------------------------------------------------------------------------
    def capabilities(self) -> dict:
        if importlib.util.find_spec("playwright") is None:
            return {"available": False, "reason": "playwright_not_installed", "ops": [], "intercepts": True}
        return {"available": True, "reason": None, "ops": ["open", "list", "navigate", "snapshot", "screenshot", "click", "type", "eval",
                                                              "cookies", "storage", "network", "close"], "intercepts": True}

    def alive(self) -> bool:
        b = self._browser
        try:
            return not self._dead and (b is None or bool(b.is_connected()))
        except Exception:  # noqa: BLE001
            return False

    # --- 작업 스레드 ---------------------------------------------------------------------------
    def _worker(self) -> _Worker:
        with self._lock:
            if self._w is None:
                self._w = _Worker("fsh-browser-playwright")
            return self._w

    def _run(self, fn: Callable, timeout: float = OP_TIMEOUT):
        def wrapped():
            try:
                return fn()
            except AdapterError:
                raise
            except Exception as e:  # noqa: BLE001
                if _is_down(e):
                    raise AdapterDown(f"playwright: {type(e).__name__}") from e
                raise AdapterError("op_failed", f"{type(e).__name__}: {str(e)[:200]}") from e
        return self._worker().call(wrapped, timeout)

    def _ensure_browser(self):
        if self._browser is not None:
            if not self._browser.is_connected():
                self._lost()
                raise AdapterDown("browser_disconnected")
            return self._browser
        if importlib.util.find_spec("playwright") is None:
            raise AdapterDown("playwright_not_installed")
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._dead = False
            self._browser = self._pw.chromium.launch(headless=True, args=["--no-first-run", "--disable-extensions", "--disable-background-networking"])
        except Exception as e:  # noqa: BLE001
            self._pw = self._browser = None
            raise AdapterDown(f"launch_failed:{type(e).__name__}") from e
        return self._browser

    def _lost(self) -> None:
        self._browser = None
        self._pw = None
        self._tabs.clear()
        self._dead = True

    def _tab(self, ref: str) -> _Tab:
        t = self._tabs.get(ref)
        if t is None or t.page.is_closed():
            raise AdapterError("tab_gone", "tab is gone")
        return t

    # --- 탭 -----------------------------------------------------------------------------------
    def open(self, *, url: Optional[str], guard: Guard) -> dict:
        def work():
            b = self._ensure_browser()
            ctx = b.new_context(accept_downloads=False, service_workers="block", permissions=[], java_script_enabled=True)
            ctx.set_default_timeout(ACTION_TIMEOUT_MS)
            ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            page = ctx.new_page()
            ref = f"pw{next(self._ids)}"
            tab = _Tab(ctx, page, guard)
            self._tabs[ref] = tab

            def on_route(route, request):
                try:
                    if request.is_navigation_request():
                        kind = "document" if request.frame.parent_frame is None else "subframe"
                    else:
                        kind = "subresource"
                    why = tab.guard(request.url, kind)
                except Exception as e:  # noqa: BLE001 — 판정 오류 = 닫힘
                    why = f"guard_error:{type(e).__name__}"
                if why:
                    if kind == "subresource":
                        tab.sub_blocked += 1
                    else:
                        tab.blocked.append(why)
                        del tab.blocked[:-20]
                    route.abort("blockedbyclient")
                else:
                    route.continue_()

            ctx.route("**/*", on_route)
            ctx.on("page", lambda p: None if p is page else p.close())        # popup 은 닫는다

            def on_resp(resp):
                try:
                    req = resp.request
                    row = {"method": req.method, "url": req.url, "status": resp.status, "type": req.resource_type,
                           "requestHeaders": req.headers, "responseHeaders": resp.headers}
                    pd = req.post_data
                    if pd is not None:
                        row["postData"] = pd
                    tab.net.append(row)
                    del tab.net[:-NET_KEEP]
                except Exception:  # noqa: BLE001
                    pass
            page.on("response", on_resp)
            if url and url != "about:blank":
                self._goto(tab, url)
            return {"ref": ref}
        return self._run(work)

    def _goto(self, tab: _Tab, url: str) -> dict:
        tab.blocked.clear()
        try:
            tab.page.goto(url, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            if tab.blocked:                    # 막아서 생긴 오류는 정책 결과로 보인다
                return {"url": tab.page.url, "title": "", "blocked": tab.blocked[0]}
            if _is_down(e):
                raise
            raise AdapterError("navigate_failed", f"{type(e).__name__}") from e
        return {"url": tab.page.url, "title": tab.page.title(), "blocked": tab.blocked[0] if tab.blocked else None}

    def list(self) -> list:
        def work():
            if self._browser is not None and not self._browser.is_connected():
                self._lost()
                raise AdapterDown("browser_disconnected")
            return [{"ref": r, "url": t.page.url} for r, t in self._tabs.items() if not t.page.is_closed()]
        return self._run(work)

    def navigate(self, ref: str, url: str) -> dict:
        return self._run(lambda: self._goto(self._tab(ref), url))

    def snapshot(self, ref: str) -> dict:
        def work():
            t = self._tab(ref)
            raw = t.page.evaluate(_js.SNAPSHOT)
            els = [{"ref": f"e{e['n']}", "role": e.get("role"), "tag": e.get("tag"), "type": e.get("type"), "name": e.get("name"),
                    "native": _js.native(e["n"])} for e in raw.get("elements", [])]
            return {"url": raw.get("url"), "title": raw.get("title"), "text": raw.get("text", ""), "elements": els}
        return self._run(work)

    def screenshot(self, ref: str) -> bytes:
        return self._run(lambda: self._tab(ref).page.screenshot(type="png"))

    def click(self, ref: str, native) -> None:
        self._run(lambda: self._tab(ref).page.locator(str(native)).first.click(timeout=ACTION_TIMEOUT_MS))

    def type(self, ref: str, native, text: str, submit: bool = False) -> None:
        def work():
            loc = self._tab(ref).page.locator(str(native)).first
            loc.fill(text, timeout=ACTION_TIMEOUT_MS)
            if submit:
                loc.press("Enter", timeout=ACTION_TIMEOUT_MS)
        self._run(work)

    def eval(self, ref: str, script: str):
        return self._run(lambda: self._tab(ref).page.evaluate(script))

    def cookies(self, ref: str, values: bool) -> list:
        def work():
            rows = self._tab(ref).context.cookies()
            if values:
                return rows
            return [{k: v for k, v in c.items() if k != "value"} for c in rows]
        return self._run(work)

    def storage(self, ref: str, values: bool) -> dict:
        return self._run(lambda: self._tab(ref).page.evaluate(_js.STORAGE, bool(values)))

    def network(self, ref: str) -> list:
        return self._run(lambda: [dict(r) for r in self._tab(ref).net])

    def frame_urls(self, ref: str) -> list:
        return self._run(lambda: [f.url for f in self._tab(ref).page.frames])

    def settle(self, ref: str) -> None:
        def work():
            p = self._tab(ref).page
            try:
                p.wait_for_load_state("domcontentloaded", timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            p.wait_for_timeout(300)
        self._run(work)

    def pop_blocked(self, ref: str) -> list:
        def work():
            t = self._tab(ref)
            out, t.blocked = list(t.blocked), []
            return out
        return self._run(work)

    def blank(self, ref: str) -> None:
        self._run(lambda: self._tab(ref).page.goto("about:blank"))

    def close(self, ref: str) -> None:
        def work():
            t = self._tabs.pop(ref, None)
            if t is not None:
                try:
                    t.context.close()
                except Exception:  # noqa: BLE001
                    pass
        self._run(work)

    def shutdown(self) -> None:
        w = self._w
        if w is None:
            return

        def work():
            for t in list(self._tabs.values()):
                try:
                    t.context.close()
                except Exception:  # noqa: BLE001
                    pass
            self._tabs.clear()
            try:
                if self._browser is not None:
                    self._browser.close()
                if self._pw is not None:
                    self._pw.stop()
            finally:
                self._browser = self._pw = None
        try:
            w.call(work, 15.0)
        except Exception:  # noqa: BLE001
            pass
        w.stop()
        self._w = None
