"""pair — HTTP 끝에서 끝까지(가짜 tmux 세계): 에이전트는 unix socket 주체로, 사람은 서명 세션으로 부른다.

만들기 → 코드 → 합류 → (승인함에 `pair` 항목) → 사람의 결정(체크 + 재인증) → status → handoff → 끝내기. 마스터 스위치가 꺼져 있으면 409.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from authz import principals as P
from authz import store
from shared_testkit import _Sock  # noqa: F401  (pytest 가 이름으로 쓰는 fixture/헬퍼)




def key(s):
    return f"{s.sid}:{s.created}"


@pytest.fixture
def pw(world, monkeypatch, tmp_path):
    import main
    world.set_mode("password")
    root = (tmp_path / "roots").resolve()
    (root / "proj").mkdir(parents=True)
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair")

    class W:
        pass
    w = W()
    w.world, w.proj = world, str(root / "proj")
    w.A = P.agent(key(world.agent_a), name="agent-a")
    w.B = P.agent(key(world.target), name="target")
    w.C = P.agent(key(world.other), name="other")
    w.sock = lambda p: TestClient(_Sock(main.app, p), raise_server_exceptions=False)
    w.web = lambda elev=False: _Web(main.app, world, "session_elev" if elev else "session")
    with store.txn() as d:
        d["grants"]["items"].append({"id": "g-pm", "subject": w.A.id, "resource": "pair:*", "scopes": ["pair.manage"], "schema": 2,
                                     "state": "active", "approved_by": "human:test", "approved_via": "manual", "expires": None, "conds": {}})
    return w


class _Web:
    def __init__(self, app, world, who):
        self.c, self.world, self.who = TestClient(app, raise_server_exceptions=False), world, who

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        h = self.world.headers_for(self.who, method, path, data)
        if body is not None:
            h["Content-Type"] = "application/json"
        return self.c.request(method, path, content=data or None, headers=h)


def test_flag_off_is_409_for_everyone_who_reaches_the_route(pw, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    with pw.sock(pw.A) as a:
        for m, path in (("POST", "/api/pairs"), ("GET", "/api/pairs"), ("POST", "/api/pairs/join"), ("DELETE", "/api/pairs/pr-x"),
                        ("GET", "/api/pairs/pr-x/status")):
            r = a.request(m, path, json={})
            assert r.status_code == 409 and r.json()["error"] == "pair_disabled", (m, path)
    assert not store.doc_path("pairs").exists()
    assert pw.web().call("GET", "/api/authz/approvals").json()["approvals"] == []


def test_full_ceremony_over_http(pw):
    with pw.sock(pw.A) as a, pw.sock(pw.B) as b, pw.sock(pw.C) as c:
        r = a.post("/api/pairs", json={"sessions": ["target"], "dirs": [pw.proj], "leases": True, "elevated": ["raw_input"], "role": "driver"})
        assert r.status_code == 201, r.text
        pid = r.json()["pair"]["id"]
        assert a.post("/api/pairs", json={"sessions": ["target"], "dirs": [pw.proj], "leases": True, "elevated": ["raw_input"],
                                          "role": "driver"}).status_code == 200                      # 멱등
        assert b.post(f"/api/pairs/{pid}/code").status_code == 404                                    # 시작자가 아니다
        code = a.post(f"/api/pairs/{pid}/code").json()
        assert code["code"] and code["phrase"]
        assert c.post("/api/pairs/join", json={"code": "ZZZZ-ZZZZ"}).status_code == 404
        j = b.post("/api/pairs/join", json={"code": code["code"]})
        assert j.status_code == 200 and j.json()["pair"]["phrase"] == code["phrase"], j.text
        st = b.get(f"/api/pairs/{pid}/status").json()["pair"]                                         # 코드 없이 자기 신원으로
        assert st["state"] == "joined" and st["approval"]["state"] == "pending"
        assert c.get(f"/api/pairs/{pid}/status").status_code == 404
        # 승인함
        web = pw.web()
        items = [x for x in web.call("GET", "/api/authz/approvals").json()["approvals"] if x["kind"] == "pair"]
        assert len(items) == 1 and items[0]["pair"]["phrases"]["joiner"] == code["phrase"] and items[0]["pair"]["needsReauth"] is True
        rid = items[0]["id"]
        # 에이전트는 승인할 수 없다
        for who in (a, b):
            r = who.post(f"/api/authz/approvals/{rid}/decision", json={"decision": "allow"})
            assert r.status_code == 403
        # 체크 + 재인증 없이는 안 된다
        r = web.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "elevated": ["raw_input"]})
        assert r.status_code == 403 and r.json()["error"] == "elevation_required"
        r = web.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "choice": "once"})
        assert r.status_code == 400 and r.json()["error"] == "choice_unsupported"
        r = web.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "elevated": ["raw_input"], "phrase": "nope nope"})
        assert r.status_code == 403                                                                   # 재인증 검사가 문구 검사보다 먼저
        elev = pw.web(True)
        r = elev.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "elevated": ["raw_input"], "phrase": code["phrase"]})
        assert r.status_code == 200 and r.json()["state"] == "allowed" and r.json()["kind"] == "pair", r.text
        again = elev.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "elevated": ["raw_input"]})
        assert again.json()["replayed"] is True
        assert elev.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "deny"}).status_code == 409
        assert b.get(f"/api/pairs/{pid}/status").json()["pair"]["state"] == "active"
        assert [x["id"] for x in a.get("/api/pairs").json()["pairs"]] == [pid] and c.get("/api/pairs").json()["pairs"] == []
        # 권한 화면에 pair grant 가 보인다
        gs = [g for g in web.call("GET", "/api/authz/grants?kind=pair").json()["grants"]]
        assert gs and all(g["kind"] == "pair" for g in gs)
        # handoff
        assert b.post(f"/api/pairs/{pid}/handoff").status_code == 403
        assert a.post(f"/api/pairs/{pid}/handoff").json()["pair"]["state"] == "handoff_pending"
        assert a.post(f"/api/pairs/{pid}/handoff/accept").status_code == 403
        acc = b.post(f"/api/pairs/{pid}/handoff/accept")
        assert acc.status_code == 200 and acc.json()["pair"]["generation"] == 1 and acc.json()["pair"]["isDriver"] is True
        # 끝내기
        assert c.delete(f"/api/pairs/{pid}").status_code == 404
        end = a.delete(f"/api/pairs/{pid}")
        assert end.status_code == 200 and end.json()["pair"]["state"] == "ended" and end.json()["cleanup"]["done"] is True
        assert [g for g in store._read("grants")[0]["items"] if g.get("pair_id") == pid] == []
        assert b.get(f"/api/pairs/{pid}/status").json()["pair"]["state"] == "ended"


def test_human_revoke_over_http_and_panic(pw):
    with pw.sock(pw.A) as a, pw.sock(pw.B) as b:
        pid = a.post("/api/pairs", json={"sessions": ["target"]}).json()["pair"]["id"]
        code = a.post(f"/api/pairs/{pid}/code").json()["code"]
        b.post("/api/pairs/join", json={"code": code})
        web = pw.web()
        rid = [x for x in web.call("GET", "/api/authz/approvals").json()["approvals"] if x["kind"] == "pair"][0]["id"]
        assert web.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "later"}).json()["later"] is True
        assert web.call("POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow"}).status_code == 200
        r = web.call("DELETE", f"/api/pairs/{pid}")
        assert r.status_code == 200 and r.json()["pair"]["state"] == "revoked"
        pid2 = a.post("/api/pairs", json={"sessions": ["other"]}).json()["pair"]["id"]
        a.post(f"/api/pairs/{pid2}/code")
        p = pw.web(True).call("POST", "/api/authz/panic")
        assert p.status_code == 200 and p.json()["revoked"]["pair"] == 1
        assert a.get(f"/api/pairs/{pid2}/status").json()["pair"]["state"] == "revoked"
