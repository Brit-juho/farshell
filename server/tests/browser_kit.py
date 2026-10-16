"""브라우저 broker 시험 도구 — 가짜 어댑터(공통 계약 구현)와 환경 조립.

`FakeAdapter` 는 `web` 사전(URL → 페이지)으로 «웹»을 흉내 낸다: redirect 사슬, 서브프레임, 하위 리소스, 클릭이 일으키는 이동(즉시/지연), 쿠키·storage·network.
`intercepts=True` 면 실제 어댑터처럼 hop·프레임·하위 리소스마다 `guard` 를 부른다. `False` 면 부르지 않아 broker 의 «행동 뒤 재검사»(N-27)만 남는다.
"""

from __future__ import annotations

import itertools
import time
from typing import Optional

from browser.adapters.base import AdapterDown, AdapterError, BrowserAdapter


def page(title="", text="", *, elements=(), redirect=None, frames=(), subresources=(), cookies=(), storage=None, network=()):
    return {"title": title, "text": text, "elements": list(elements), "redirect": redirect, "frames": list(frames), "subresources": list(subresources),
            "cookies": list(cookies), "storage": storage or {"local": {}, "session": {}}, "network": list(network)}


def element(role="button", name="", *, goto=None, delayed=None, frame=None):
    return {"role": role, "name": name, "goto": goto, "delayed": delayed, "frame": frame}


class FakeAdapter(BrowserAdapter):
    name = "fake"
    profile = "isolated"
    description = "fake adapter for tests"

    def __init__(self, web=None, *, intercepts: bool = True, name: str = "fake"):
        self.name = name
        self.intercepts = intercepts
        self.web = dict(web or {})
        self.tabs: dict[str, dict] = {}
        self.calls: list = []
        self.down = False
        self.evals: dict[str, object] = {}
        self._ids = itertools.count(1)
        self.closed: list = []

    # --- 시험 조작 -----------------------------------------------------------------------------
    def set_down(self, v: bool = True) -> None:
        self.down = v

    def drop(self, ref: str) -> None:
        self.tabs.pop(ref, None)

    def _chk(self, op: str, ref: Optional[str] = None):
        self.calls.append((op, ref))
        if self.down:
            raise AdapterDown("fake_down")
        if ref is not None and ref not in self.tabs:
            raise AdapterError("tab_gone", "tab is gone")
        return self.tabs.get(ref)

    # --- 계약 ---------------------------------------------------------------------------------
    def capabilities(self) -> dict:
        if self.down:
            return {"available": False, "reason": "fake_down", "ops": [], "intercepts": self.intercepts}
        return {"available": True, "reason": None, "ops": ["open", "list", "navigate", "snapshot", "screenshot", "click", "type", "eval",
                                                              "cookies", "storage", "network", "close"], "intercepts": self.intercepts}

    def alive(self) -> bool:
        return not self.down

    def open(self, *, url, guard):
        self._chk("open")
        ref = f"f{next(self._ids)}"
        self.tabs[ref] = {"url": "about:blank", "frames": [], "guard": guard, "blocked": [], "sub_blocked": [], "typed": {}, "pending": None, "refs": {}, "net": []}
        if url and url != "about:blank":
            self.navigate(ref, url)
        return {"ref": ref}

    def list(self):
        self._chk("list")
        return [{"ref": r, "url": t["url"]} for r, t in self.tabs.items()]

    def _guard(self, t, url, kind):
        if not self.intercepts:
            return True
        why = t["guard"](url, kind)
        if why:
            t["sub_blocked" if kind == "subresource" else "blocked"].append(why)
            return False
        return True

    def _load(self, t, url):
        """redirect 사슬을 따라 hop 마다 guard 를 부르고, 끝 페이지의 서브프레임·하위 리소스까지 부른다."""
        seen = 0
        while True:
            if not self._guard(t, url, "document"):
                return False
            pg = self.web.get(url)
            if pg is None:
                raise AdapterError("navigate_failed", "ERR_NAME_NOT_RESOLVED")
            if pg.get("redirect") and seen < 10:
                url = pg["redirect"]
                seen += 1
                continue
            break
        t["url"] = url
        t["frames"] = []
        for f in pg["frames"]:
            if self._guard(t, f, "subframe"):
                t["frames"].append(f)
        for r in pg["subresources"]:
            self._guard(t, r, "subresource")
        return True

    def navigate(self, ref, url):
        t = self._chk("navigate", ref)
        t["blocked"].clear()
        ok = self._load(t, url)
        if not ok:
            return {"url": t["url"], "title": "", "blocked": t["blocked"][0]}
        pg = self.web[t["url"]]
        return {"url": t["url"], "title": pg["title"], "blocked": t["blocked"][0] if t["blocked"] else None}

    def snapshot(self, ref):
        t = self._chk("snapshot", ref)
        pg = self.web.get(t["url"]) or page()
        els = []
        t["refs"] = {}
        for i, e in enumerate(pg["elements"], 1):
            els.append({"ref": f"e{i}", "role": e["role"], "name": e["name"], "native": f"#n{i}"})
            t["refs"][f"#n{i}"] = e
        return {"url": t["url"], "title": pg["title"], "text": pg["text"], "elements": els}

    def screenshot(self, ref):
        self._chk("screenshot", ref)
        return b"\x89PNG fake"

    def click(self, ref, native):
        t = self._chk("click", ref)
        e = t["refs"].get(native)
        if e is None:
            raise AdapterError("element_gone", "gone")
        if e.get("delayed"):
            t["pending"] = e["delayed"]
        if e.get("goto"):
            t["blocked"].clear()
            self._load(t, e["goto"])
        if e.get("frame"):
            t["frames"].append(e["frame"])               # 클릭이 서브프레임을 붙인다(가로채기가 없으면 guard 를 지나지 않는다)
            self._guard(t, e["frame"], "subframe") if self.intercepts else None

    def type(self, ref, native, text, submit=False):
        t = self._chk("type", ref)
        t["typed"][native] = text

    def eval(self, ref, script):
        self._chk("eval", ref)
        return self.evals.get(script, "ok")

    def cookies(self, ref, values):
        t = self._chk("cookies", ref)
        rows = self.web.get(t["url"], page())["cookies"]
        return [dict(c) for c in rows] if values else [{k: v for k, v in c.items() if k != "value"} for c in rows]

    def storage(self, ref, values):
        t = self._chk("storage", ref)
        st = self.web.get(t["url"], page())["storage"]
        return {a: {k: (v if values else None) for k, v in st.get(a, {}).items()} for a in ("local", "session")}

    def network(self, ref):
        t = self._chk("network", ref)
        return [dict(x) for x in self.web.get(t["url"], page())["network"]]

    def frame_urls(self, ref):
        t = self._chk("frame_urls", ref)
        return [t["url"], *t["frames"]]

    def settle(self, ref):
        t = self._chk("settle", ref)
        if t["pending"]:
            nxt, t["pending"] = t["pending"], None
            self._load(t, nxt) if self.intercepts else t.update(url=nxt)

    def pop_blocked(self, ref):
        t = self._chk("pop_blocked", ref)
        out, t["blocked"] = list(t["blocked"]), []
        return out

    def blank(self, ref):
        t = self._chk("blank", ref)
        t["url"], t["frames"] = "about:blank", []

    def close(self, ref):
        self._chk("close")
        self.closed.append(ref)
        self.tabs.pop(ref, None)

    def shutdown(self):
        self.tabs.clear()


def grant(subject: str, scopes, *, resource="browser:local/*", sconds=None, conds=None, gid=None, **extra) -> dict:
    return {"id": gid or f"g-{subject.split('/')[-1]}-{'-'.join(scopes)}-{int(time.time() * 1000) % 100000}", "subject": subject, "resource": resource,
            "scopes": list(scopes), "sconds": sconds or {}, "schema": 2, "state": "active", "approved_by": "human:test", "approved_via": "manual",
            "expires": None, "conds": conds or {}, **extra}


def seed(*grants) -> None:
    from authz import store
    with store.txn() as d:
        d["grants"]["items"].extend(grants)


# --- 가짜 CDP 서버 (cdp 어댑터의 프로토콜 처리를 실제 웹소켓으로 시험) -----------------------------------------------

class FakeCdpServer:
    """loopback 웹소켓에서 cdp 어댑터가 쓰는 CDP 메서드만 구현한다. `web` 은 FakeAdapter 와 같은 사전.
    `Page.navigate` 는 실제 브라우저처럼 `Fetch.requestPaused`(문서·redirect hop·서브프레임·하위 리소스)를 보내고 클라이언트의 continue/fail 을 기다린다."""

    def __init__(self, web):
        import threading
        from websockets.sync.server import serve
        self.web = web
        self.targets: dict[str, dict] = {}
        self.sessions: dict[str, str] = {}
        self._n = itertools.count(1)
        self.log: list = []
        self.server = serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/browser/fake"

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(3)

    def kill_connections(self) -> None:
        for c in list(self.server.connections) if hasattr(self.server, "connections") else []:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def _handler(self, ws):
        import json
        send = lambda o: ws.send(json.dumps(o))  # noqa: E731
        try:
            for raw in ws:
                msg = json.loads(raw)
                self.log.append(msg["method"])
                sess = msg.get("sessionId")
                try:
                    res = self._call(ws, send, msg["method"], msg.get("params") or {}, sess)
                    send({"id": msg["id"], "result": res or {}})
                except KeyError as e:
                    send({"id": msg["id"], "error": {"message": f"no such target {e}"}})
        except Exception:  # noqa: BLE001
            return

    def _tgt(self, sess):
        return self.targets[self.sessions[sess]]

    def _paused(self, ws, send, sess, url, rtype, frame):
        import json
        rid = f"r{next(self._n)}"
        send({"method": "Fetch.requestPaused", "sessionId": sess, "params": {"requestId": rid, "request": {"url": url}, "resourceType": rtype, "frameId": frame}})
        while True:
            m = json.loads(ws.recv())
            if m["method"] in ("Fetch.continueRequest", "Fetch.failRequest") and m["params"]["requestId"] == rid:
                return m["method"] == "Fetch.continueRequest"
            # 그 사이 오는 다른 명령은 이 시험에서 없다

    def _call(self, ws, send, method, p, sess):
        if method == "Target.createBrowserContext":
            return {"browserContextId": f"ctx{next(self._n)}"}
        if method == "Target.createTarget":
            tid = f"T{next(self._n)}"
            self.targets[tid] = {"url": "about:blank", "ctx": p["browserContextId"], "frames": [], "title": ""}
            return {"targetId": tid}
        if method == "Target.attachToTarget":
            s = f"S{next(self._n)}"
            self.sessions[s] = p["targetId"]
            return {"sessionId": s}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": k, "url": v["url"], "type": "page"} for k, v in self.targets.items()]}
        if method == "Target.closeTarget":
            self.targets.pop(p["targetId"], None)
            return {"success": True}
        if method == "Page.getFrameTree":
            t = self._tgt(sess)
            return {"frameTree": {"frame": {"id": "MAIN", "url": t["url"]}, "childFrames": [{"frame": {"id": f"F{i}", "url": u}} for i, u in enumerate(t["frames"])]}}
        if method == "Page.navigate":
            t = self._tgt(sess)
            url = p["url"]
            if url == "about:blank":
                t.update(url=url, frames=[])
                return {"frameId": "MAIN"}
            hops = 0
            while True:
                if not self._paused(ws, send, sess, url, "Document", "MAIN"):
                    return {"frameId": "MAIN", "errorText": "net::ERR_BLOCKED_BY_CLIENT"}
                pg = self.web.get(url)
                if pg is None:
                    return {"frameId": "MAIN", "errorText": "net::ERR_NAME_NOT_RESOLVED"}
                if pg.get("redirect") and hops < 10:
                    url, hops = pg["redirect"], hops + 1
                    continue
                break
            t.update(url=url, frames=[], title=pg["title"])
            for f in pg["frames"]:
                if self._paused(ws, send, sess, f, "Document", "FRAME1"):
                    t["frames"].append(f)
            for r in pg["subresources"]:
                self._paused(ws, send, sess, r, "Image", "MAIN")
            send({"method": "Page.loadEventFired", "sessionId": sess, "params": {}})
            return {"frameId": "MAIN"}
        if method == "Runtime.evaluate":
            t = self._tgt(sess)
            ex = p["expression"]
            pg = self.web.get(t["url"]) or page()
            if "data-fsh-ref" in ex and "querySelectorAll" in ex and "const SEL" in ex:
                els = [{"n": i, "tag": "button", "role": e["role"], "type": "", "name": e["name"]} for i, e in enumerate(pg["elements"], 1)]
                v = {"url": t["url"], "title": pg["title"], "text": pg["text"], "elements": els}
            elif "getBoundingClientRect" in ex and "scrollIntoView" in ex:
                v = {"x": 10, "y": 20}
            elif ".focus()" in ex:
                v = True
            elif "localStorage" in ex:
                vals = "true" in ex.rsplit(")(", 1)[-1]
                st = pg["storage"]
                v = {a: {k: (x if vals else None) for k, x in st.get(a, {}).items()} for a in ("local", "session")}
            elif ex == "location.href":
                v = t["url"]
            elif ex == "document.title":
                v = pg["title"]
            elif ex == "throw":
                return {"exceptionDetails": {"text": "boom", "exception": {"description": "Error: boom"}}}
            else:
                v = f"eval:{ex}"
            return {"result": {"type": "object", "value": v}}
        if method == "Page.captureScreenshot":
            import base64
            return {"data": base64.b64encode(b"\x89PNG fake").decode()}
        if method == "Storage.getCookies":
            t = next(iter(self.targets.values()))
            return {"cookies": [dict(c) for c in self.web.get(t["url"], page())["cookies"]]}
        return {}
