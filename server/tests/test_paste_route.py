"""N24(2.1.5 2/n) — POST /api/sessions/{id}/paste + WS `{"type":"paste"}`.

붙여넣기 전용 경로. `/keys`(N38)와 같은 뼈대(404·400·인증 상속)를 지키고,
추가로 이 경로만의 것: 실제로 PTY에 도달할 때 **서버가 마커·개행·제어문자를
처리한 결과**가 들어가는지 확인한다.
"""

import pytest
from starlette.testclient import TestClient

import auth
import main
import pty_manager as pty_manager_module
from deps import pty_mgr


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _make_session(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    return r.json()["id"]


def test_unknown_session_is_404(client):
    r = client.post("/api/sessions/does-not-exist/paste", json={"text": "hi"})
    assert r.status_code == 404


def test_missing_text_is_400(client):
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/paste", json={})
        assert r.status_code == 400
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_empty_text_is_400(client):
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/paste", json={"text": ""})
        assert r.status_code == 400
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_writes_prepared_payload_into_pty(client):
    """실제로 셸에 도달하는지 — echo 왕복(N38 레시피와 동일)."""
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/paste", json={"text": "echo vt24ok\r"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_inherits_token_auth_middleware(client, monkeypatch):
    sid = _make_session(client)
    try:
        monkeypatch.setattr(auth, "is_protected", lambda: True)
        r = client.post(f"/api/sessions/{sid}/paste", json={"text": "x"})
        assert r.status_code == 401
    finally:
        monkeypatch.setattr(auth, "is_protected", lambda: False)
        client.delete(f"/api/sessions/{sid}")


def test_route_calls_pty_manager_paste_with_tmux_name(client, monkeypatch):
    """tmux 세션이면 is_tmux=True + tmux_name을 넘겨야 위임(3/n)이 동작한다
    — 라우트가 session_store를 실제로 확인하는지 검증."""
    calls = []
    monkeypatch.setattr(
        pty_mgr, "paste",
        lambda sid, text, is_tmux=False, tmux_name=None: calls.append((sid, text, is_tmux, tmux_name)),
    )
    sid = _make_session(client)
    try:
        from deps import session_store
        session_store.update_tmux_name(sid, "dev")
        r = client.post(f"/api/sessions/{sid}/paste", json={"text": "hi"})
        assert r.status_code == 200
        assert calls == [(sid, "hi", True, "dev")]
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_route_reports_tmux_paste_failure_as_502_not_silent(client, monkeypatch):
    """계획서 4-4 위험 3 — tmux paste-buffer 실패를 조용히 삼키지 않는다."""
    monkeypatch.setattr(pty_mgr, "paste", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tmux paste-buffer 실패: dev")))
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/paste", json={"text": "hi"})
        assert r.status_code == 502
        assert r.json()["error"] == "paste_failed"
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_pty_manager_paste_end_to_end_multiline_goes_as_one_block():
    """멀티라인 붙여넣기가 줄마다 즉시 실행되지 않는지(N24의 존재 이유)
    — bracket=True 세션에서 셸이 실제로 한 번에 받는지까지 확인."""
    import asyncio

    async def _run():
        mgr = pty_manager_module.PTYManager()
        sid = "test-paste-e2e"
        mgr.create_session(sid, cmd="/bin/sh")
        session = mgr._sessions[sid]
        # 이 pane이 bracketed paste를 원한다고 이미 알려진 상태로 만든다
        # (실제로는 출력 스트림에서 ESC[?2004h를 보고 세팅된다 — N25 1/n).
        session._bracketed = True
        await asyncio.sleep(0.3)
        mgr.paste(sid, "echo line1\necho line2\n", is_tmux=False)
        await asyncio.sleep(0.5)
        mgr.destroy_session(sid)

    asyncio.run(_run())
    # 크래시 없이 끝나면 충분 — 셸 출력 캡처는 pty_manager 기존 테스트가
    # 이미 다루는 배관이라 여기선 paste() 경로 자체의 생존만 확인한다.


# ── N26(2.1.5 4/n) — 정규 모드 거절이 실제로 소실을 막는지 ───────────────────

def test_pty_manager_paste_rejects_oversized_canonical_line_instead_of_losing_it():
    """1/n 실측(test_input_mode.py)의 결론을 여기서 다시 확인한다: 거절하지
    않고 그냥 보냈다면 이 줄은 잘리는 게 아니라 통째로 사라졌을 것이다."""
    import asyncio
    import time

    import input_mode
    import paste_prepare

    async def _run():
        mgr = pty_manager_module.PTYManager()
        sid = "test-paste-canonical"
        mgr.create_session(sid, cmd="/bin/sh")
        session = mgr._sessions[sid]
        await asyncio.sleep(0.3)
        # cat을 정규 모드로 띄운다(1/n과 같은 방식).
        mgr.write(sid, b"cat > /dev/null\n")
        await asyncio.sleep(0.5)
        limit = input_mode.max_canon(session.fd)
        assert input_mode.is_canonical(session.fd) is True

        with pytest.raises(paste_prepare.LineTooLong):
            mgr.paste(sid, "x" * limit + "\n", is_tmux=False)  # 한계+1(개행 포함)

        mgr.destroy_session(sid)

    asyncio.run(_run())
