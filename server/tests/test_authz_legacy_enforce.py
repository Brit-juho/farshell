"""`VT_AUTHZ_ENFORCE=legacy` — 공용 기계 토큰 정리: 허용표 범위, `?token=`·쿠키 토큰 거절, 로그 가림, 비밀번호 없는 서버."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from authz import legacy_gate, legacy_use, sock
from authz.errors import ShareError

SERVER = Path(__file__).resolve().parents[1]
TOKEN = "tok-legacy-test"
BEARER = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "unit/1"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    legacy_use._pending.clear()
    legacy_use._pending_denied.clear()
    yield
    legacy_use._pending.clear()
    legacy_use._pending_denied.clear()


def _auth(monkeypatch, *, password="", token=TOKEN, key=""):
    import auth
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", password)
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", token)
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", key)
    return auth


def _on(monkeypatch, value="legacy"):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", value)


def _off(monkeypatch):
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)


def _gated_client():
    """토큰 미들웨어만 — 안쪽 앱은 닿았다는 표시만 한다(라우트 부작용 없이 허용표를 전부 시험한다)."""
    import main

    async def inner(scope, receive, send):
        r = JSONResponse({"reached": True, "state": scope.get("state", {}).get("auth_kind")})
        await r(scope, receive, send)

    return TestClient(main.TokenAuthMiddleware(inner), raise_server_exceptions=False)


# --- 허용표 ------------------------------------------------------------------------------

# sock.ROUTES 의 각 항목을 실제로 부르는 (메서드, 경로). 아래 test_table_covers_every_socket_route 가 표와 어긋나지 않게 지킨다.
SAMPLES = [
    ("POST", "/api/agent/event"), ("POST", "/api/agent/report"), ("POST", "/api/clipboard/push"),
    ("POST", "/voice/cancel"), ("POST", "/voice/input"), ("POST", "/voice/target"), ("POST", "/voice/bind"),
    ("POST", "/voice/stt/preload"),
    ("GET", "/api/capabilities"), ("GET", "/api/tmux/sessions"), ("DELETE", "/api/tmux/kill/work"),
    ("PATCH", "/api/sessions/s1"), ("POST", "/api/notify/client-event"), ("POST", "/api/agent/message"),
    ("GET", "/api/agent/messages"), ("GET", "/api/agent/directory"), ("GET", "/api/agent/shared"),
    ("POST", "/api/agent/shared/read"), ("POST", "/api/agent/shared/send"),
    ("GET", "/api/delegations"), ("GET", "/api/delegations/role"), ("POST", "/api/delegations"),
    ("POST", "/api/delegations/report"), ("POST", "/api/delegations/d1/send"), ("POST", "/api/delegations/d1/close"),
    ("POST", "/api/delegations/d1/close-by-cli"), ("POST", "/api/queue/run"), ("POST", "/api/ports/_proxy/sync"),
    ("POST", "/api/host-grants/pc/change"),
    ("GET", "/api/authz/whoami"), ("GET", "/api/authz/requests"), ("POST", "/api/authz/requests"),
    ("GET", "/api/authz/my-grants"), ("GET", "/api/authz/approvals"), ("GET", "/api/authz/grants"),
    ("GET", "/api/authz/audit"),
    ("GET", "/api/leases"), ("POST", "/api/leases"), ("POST", "/api/leases/check"), ("POST", "/api/leases/ls-1/renew"),
    ("DELETE", "/api/leases/ls-1"),
]

DENIED = [
    ("POST", "/api/sessions/pty-1/keys"), ("POST", "/api/queue"), ("DELETE", "/api/queue/abc"),
    ("GET", "/api/hosts"), ("POST", "/api/hosts/pc/sessions"), ("DELETE", "/api/hosts/pc"),
    ("POST", "/api/files/write"), ("POST", "/api/git/commit"), ("GET", "/api/files/read"),
    ("GET", "/api/sessions"), ("POST", "/api/authz/whoami"),
    ("GET", "/api/delegations/d1/extra"), ("DELETE", "/api/agent/message"), ("GET", "/api/queue/run"),
]


def test_table_covers_every_socket_route():
    for method, rx, _ in sock.ROUTES:
        assert any(m == method and re.fullmatch(rx, p) for m, p in SAMPLES), f"{method} {rx} 시험 경로가 없다"
    for method, path in SAMPLES:
        assert sock.route_allowed(method, path), f"{method} {path} 는 소켓 허용표에 없다"


@pytest.mark.parametrize("method,path", SAMPLES, ids=lambda x: str(x))
def test_every_socket_allowlisted_route_works_with_the_legacy_token(method, path, monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    r = _gated_client().request(method, path, headers=BEARER)
    assert r.status_code == 200 and r.json()["reached"] is True
    legacy_use.flush()
    assert legacy_use.count_24h() == 1 and legacy_use.denied_24h() == 0       # 허용된 사용은 센다


@pytest.mark.parametrize("method,path", DENIED, ids=lambda x: str(x))
def test_sensitive_routes_are_denied_with_a_pointer_to_the_socket(method, path, monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    r = _gated_client().request(method, path, headers=BEARER)
    assert r.status_code == 403 and r.json()["error"] == "legacy_token_scope"
    assert "unix socket" in r.json()["reason"] and "로그인" in r.json()["reason"]
    legacy_use.flush()
    assert legacy_use.denied_24h() == 1 and legacy_use.count_24h() == 0       # 거절은 따로 센다
    rows = legacy_use.denied_top_24h()
    assert rows[0]["path"] == path and rows[0]["ua"] == "unit/1"
    assert TOKEN not in legacy_use.denied_path().read_text()


def test_off_by_default_the_token_still_opens_everything(monkeypatch):
    _auth(monkeypatch)
    _off(monkeypatch)
    c = _gated_client()
    for method, path in DENIED:
        assert c.request(method, path, headers=BEARER).status_code == 200
    legacy_use.flush()
    assert legacy_use.denied_24h() == 0


def test_human_cookie_session_and_other_flags_are_unaffected(monkeypatch):
    auth = _auth(monkeypatch, password="pbkdf2$x", key="k" * 32)
    _on(monkeypatch, "share,deleg,msg,peer,legacy")
    cookie = auth.make_session("")
    c = _gated_client()
    for method, path in DENIED:
        r = c.request(method, path, cookies={"vt_session": cookie})
        assert r.status_code == 200 and r.json()["state"] == "session"
    legacy_use.flush()
    assert legacy_use.count_24h() == 0 and legacy_use.denied_24h() == 0


def test_rollback_flag_off_after_on(monkeypatch):
    _auth(monkeypatch)
    c = _gated_client()
    _on(monkeypatch)
    assert c.post("/api/queue", headers=BEARER).status_code == 403
    assert c.get("/api/capabilities?token=" + TOKEN).status_code == 400
    _off(monkeypatch)
    assert c.post("/api/queue", headers=BEARER).status_code == 200
    assert c.get("/api/capabilities?token=" + TOKEN).status_code == 200


# --- 전달 방식 -----------------------------------------------------------------------------


def test_query_token_is_not_honored(monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    c = _gated_client()
    for url in (f"/api/capabilities?token={TOKEN}", "/api/capabilities?token=wrong"):    # 유효 여부와 무관하게 같은 답
        r = c.get(url)
        assert r.status_code == 400 and r.json()["error"] == "token_query_unsupported"
        assert "fsh mobile" in r.json()["reason"] and TOKEN not in r.text
    assert c.get(f"/api/capabilities?token={TOKEN}", headers=BEARER).status_code == 400     # Bearer 와 함께여도 거절
    legacy_use.flush()
    assert legacy_use.count_24h() == 0


def test_machine_token_as_session_cookie_is_not_honored(monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    r = _gated_client().get("/api/capabilities", cookies={"vt_session": TOKEN})
    assert r.status_code == 401 and r.json()["error"] == "token_cookie_unsupported"
    assert TOKEN not in r.text


def test_old_transport_still_works_when_flag_is_off(monkeypatch):
    _auth(monkeypatch)
    _off(monkeypatch)
    c = _gated_client()
    assert c.get(f"/api/capabilities?token={TOKEN}").status_code == 200
    assert c.get("/api/capabilities", cookies={"vt_session": TOKEN}).status_code == 200


def test_exempt_paths_are_untouched(monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    c = _gated_client()
    assert c.get("/api/auth/status?token=x").status_code == 200       # 인증 예외 경로는 미들웨어 앞에서 빠진다
    assert c.get("/static/app.js").status_code == 200


def test_no_credentials_server_is_not_gated(monkeypatch):
    _auth(monkeypatch, token="")
    _on(monkeypatch)
    assert _gated_client().post("/api/queue").status_code == 200


# --- WS --------------------------------------------------------------------------------------


def _ws(cookie="", query="", bearer=""):
    return SimpleNamespace(
        cookies={"vt_session": cookie} if cookie else {}, query_params={"token": query} if query else {},
        headers={"authorization": f"Bearer {bearer}", "user-agent": "ws-unit"} if bearer else {"user-agent": "ws-unit"},
        scope={"path": "/ws/terminal"})


def test_ws_accepts_only_a_signed_session_cookie_when_flag_is_on(monkeypatch):
    from routes import pty
    auth = _auth(monkeypatch, password="pbkdf2$x", key="k" * 32)
    cookie = auth.make_session("")
    _on(monkeypatch)
    assert pty._ws_auth_token(_ws(cookie=cookie)) == cookie
    for ws in (_ws(query=TOKEN), _ws(bearer=TOKEN), _ws(cookie=TOKEN)):
        assert pty._ws_auth_token(ws) is None
    legacy_use.flush()
    assert legacy_use.denied_24h() == 3
    _off(monkeypatch)
    assert pty._ws_auth_token(_ws(query=TOKEN)) == TOKEN and pty._ws_auth_token(_ws(bearer=TOKEN)) == TOKEN


# --- 로그 가림 ---------------------------------------------------------------------------------


def _log_line(msg_args):
    import main
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', msg_args, None)
    main._RedactSecretsFilter().filter(rec)
    return rec.getMessage()


def test_access_log_redacts_token_like_queries(monkeypatch):
    _on(monkeypatch)
    line = _log_line(("1.2.3.4:5", "GET", "/api/x?access_token=AAA&path=/p&vt_token=BBB&client_secret=CCC&token=DDD", "1.1", 200))
    for leaked in ("AAA", "BBB", "CCC", "DDD"):
        assert leaked not in line
    assert "path=/p" in line


def test_access_log_default_redaction_is_unchanged_when_flag_is_off(monkeypatch):
    _off(monkeypatch)
    line = _log_line(("1.2.3.4:5", "GET", "/api/x?token=DDD&client_secret=AAA", "1.1", 200))
    assert "DDD" not in line and "client_secret=AAA" in line      # 옛 규칙 그대로 — 넓은 규칙은 켠 뒤에만


# --- H14: 비밀번호 없이 토큰만 있는 서버의 서명 쿠키 ------------------------------------------------


def _req(cookie, headers=None):
    return SimpleNamespace(cookies={"vt_session": cookie}, headers=headers or {}, query_params={})


def test_signed_cookie_is_not_human_on_a_token_only_server(monkeypatch):
    from starlette.exceptions import HTTPException
    auth = _auth(monkeypatch, key="k" * 32)
    cookie = auth.make_session("")
    _off(monkeypatch)
    assert auth.request_kind(_req(cookie)) == auth.AUTH_SESSION           # 꺼짐: 옛 동작(H14 그대로)
    auth.require_human_session(_req(cookie))
    _on(monkeypatch)
    assert auth.request_kind(_req(cookie)) == auth.AUTH_NONE
    with pytest.raises(HTTPException) as e:
        auth.require_human_session(_req(cookie))
    assert e.value.status_code == 403 and e.value.detail["error"] == "human_session_required"


def test_signed_cookie_is_human_when_a_password_exists(monkeypatch):
    auth = _auth(monkeypatch, password="pbkdf2$x", key="k" * 32)
    cookie = auth.make_session("")
    _on(monkeypatch, "share,deleg,msg,peer,legacy")
    assert auth.request_kind(_req(cookie)) == auth.AUTH_SESSION


def test_token_only_signed_cookie_principal_is_the_legacy_shim_not_human(monkeypatch):
    from authz import principals as P
    auth = _auth(monkeypatch, key="k" * 32)
    cookie = auth.make_session("")
    req = SimpleNamespace(cookies={"vt_session": cookie}, headers={}, query_params={})
    _on(monkeypatch)
    assert P.from_request(req).kind == P.LEGACY
    _off(monkeypatch)
    assert P.from_request(req).kind == P.HUMAN


# --- D8: 비밀번호 없는 서버의 승인 기능 -------------------------------------------------------------


def test_approval_gate_matrix(monkeypatch):
    _on(monkeypatch)
    _auth(monkeypatch, token=TOKEN)                         # 토큰만 -> 승인 불가
    assert legacy_gate.approval_unavailable()
    with pytest.raises(ShareError) as e:
        legacy_gate.require_approval_available()
    assert e.value.status == 403 and e.value.code == "approval_unavailable_no_password"
    assert "fsh password" in e.value.reason
    _auth(monkeypatch, password="pbkdf2$x", token=TOKEN)    # 비밀번호 있음 -> 정상
    legacy_gate.require_approval_available()
    _auth(monkeypatch, token="")                            # 둘 다 없음 -> 지금과 같다(잠그지 않는다)
    legacy_gate.require_approval_available()
    _auth(monkeypatch, token=TOKEN)
    _off(monkeypatch)                                       # 플래그 꺼짐 -> 지금과 같다
    legacy_gate.require_approval_available()


def test_start_notice_only_when_it_applies(monkeypatch):
    _on(monkeypatch)
    _auth(monkeypatch, token=TOKEN)
    assert "fsh password" in legacy_gate.notice_for_start()
    _auth(monkeypatch, password="pbkdf2$x", token=TOKEN)
    assert legacy_gate.notice_for_start() == ""
    _auth(monkeypatch, token="")
    assert legacy_gate.notice_for_start() == ""
    _auth(monkeypatch, token=TOKEN)
    _off(monkeypatch)
    assert legacy_gate.notice_for_start() == ""


def test_every_approval_creating_service_refuses_without_a_password(monkeypatch):
    import agent_share
    from authz import host_grant_svc, msg_svc
    _on(monkeypatch)
    _auth(monkeypatch, token=TOKEN)
    for call in (lambda: agent_share.request_access({}, SimpleNamespace(sid="s", created="1", name="a"), None, "read"),
                 lambda: msg_svc.request_access("a:1", "a", "b:1", "b", None)):
        with pytest.raises(ShareError) as e:
            call()
        assert e.value.code == "approval_unavailable_no_password" and e.value.status == 403
    import host_store
    monkeypatch.setattr(host_store, "find_grant", lambda pid: {"id": "pc", "label": "pc"})
    monkeypatch.setattr(host_store, "normalize_id", lambda x: x)
    from authz import principals as P
    with pytest.raises(ShareError) as e:
        host_grant_svc.change(P.cli(1, 2), "pc", "allow-control")
    assert e.value.code == "approval_unavailable_no_password"


# --- 진단(fsh doctor / status) ------------------------------------------------------------------


def test_status_report_and_cli_show_denials(monkeypatch):
    _auth(monkeypatch)
    _on(monkeypatch)
    c = _gated_client()
    c.get("/api/capabilities", headers=BEARER)
    c.post("/api/queue", headers=BEARER)
    c.post("/api/queue", headers=BEARER)
    legacy_use.flush()
    r = sock.status_report()
    assert r["legacy_token_24h"] == 1 and r["legacy_denied_24h"] == 2 and r["legacy_enforced"] is True
    env = {**os.environ, "PYTHONPATH": str(SERVER), "VT_AUTHZ_ENFORCE": "legacy", "VT_STATE_DIR": os.environ["VT_STATE_DIR"]}
    out = subprocess.run([sys.executable, "-m", "authz", "sock-status"], cwd=str(SERVER), env=env,
                         capture_output=True, text=True, timeout=30).stdout.strip()
    assert "|legacy24h=1|denied24h=2|legacy=1|nopw=" in out
    top = subprocess.run([sys.executable, "-m", "authz", "legacy-denied"], cwd=str(SERVER), env=env,
                         capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
    assert top == ["2|/api/queue|unit/1".replace("unit/1", "testclient")] or top[0].startswith("2|/api/queue|")


def test_status_report_without_flag(monkeypatch):
    _auth(monkeypatch)
    _off(monkeypatch)
    r = sock.status_report()
    assert r["legacy_enforced"] is False and r["legacy_denied_24h"] == 0 and r["approval_unavailable"] is False
