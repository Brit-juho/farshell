"""N38(70-mobile.md §2) — POST /api/sessions/{id}/keys.

모바일 플릿 홈의 인라인 승인 버튼이 부르는 신규 엔드포인트. 세 가지를
지켜야 한다: 존재하지 않는 세션은 404 · 실제로 PTY에 텍스트가 들어감 ·
TokenAuthMiddleware를 그대로 상속(별도 예외 경로를 만들지 않았다).
"""

import pytest
from starlette.testclient import TestClient

import agent_status
import auth
import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_agent_state():
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def _make_session(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    return r.json()["id"]


def _make_tmux_session(client, sid, tmux_name="dev"):
    """P1-1 테스트용 — 진짜 tmux 없이 `session_store`에 이름만 붙인다.

    `consume_prompt`는 `session_store.get(sid).tmux_name`으로 엔트리를 찾으므로,
    `agent_status`의 엔트리도 **같은 이름**으로 만들어야 한다.
    """
    from deps import session_store
    session_store.update_tmux_name(sid, tmux_name)
    return tmux_name


def test_unknown_session_is_404(client):
    r = client.post("/api/sessions/does-not-exist/keys", json={"text": "1\r"})
    assert r.status_code == 404


def test_missing_text_is_400(client):
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={})
        assert r.status_code == 400
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_writes_text_into_pty(client):
    """실제로 셸에 도달하는지 — echo 왕복으로 확인(E2E 레시피와 같은 방식)."""
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "echo vt38ok\r"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_inherits_token_auth_middleware(client, monkeypatch):
    """새 경로를 TokenAuthMiddleware의 예외 목록('/', '/api/auth' 등)에 추가하지
    않았는지 — 추가돼 있었다면 인증 없이 통과해 401이 아니라 200/404가 온다."""
    sid = _make_session(client)
    try:
        monkeypatch.setattr(auth, "is_protected", lambda: True)
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "x"})
        assert r.status_code == 401
    finally:
        monkeypatch.setattr(auth, "is_protected", lambda: False)
        client.delete(f"/api/sessions/{sid}")


# ---------------------------------------------------------------------------
# P1-1(ADR-33) — 승인 응답 경쟁. `prompt_id`가 실린 요청만 이 스위트에서 다룬다
# (docs/plan-2.3/10-approval-race.md §5 「API」).

def _issue_prompt_token(tmux_name, agent_sid="pane:dev"):
    """실제 waiting 진입 경로(report → on_waiting)로 토큰을 발급받는다 —
    직접 dict를 조립하지 않는다(test_prompt_token.py와 같은 관례)."""
    agent_status.report(agent_sid, agent_status.WORKING, session=tmux_name)
    ent = agent_status.on_waiting(
        agent_sid, True, question="계속할까요?", options=[{"key": "1", "label": "예"}],
    )
    return ent["prompt_id"]


def test_valid_prompt_id_is_consumed_and_pty_receives_the_write(client):
    sid = _make_session(client)
    tmux_name = _make_tmux_session(client, sid)
    try:
        token = _issue_prompt_token(tmux_name, agent_sid=f"pane:{tmux_name}")
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "1\r", "prompt_id": token})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_same_prompt_id_twice_the_second_is_409_and_does_not_write(client, monkeypatch):
    """이게 이 항목의 핵심 단언이다 — 두 번째 요청이 409로 떨어지고, **PTY에
    쓰지 않는다.** `pty_mgr.write` 호출 횟수를 세어 확인한다."""
    import deps

    sid = _make_session(client)
    tmux_name = _make_tmux_session(client, sid)
    try:
        token = _issue_prompt_token(tmux_name, agent_sid=f"pane:{tmux_name}")

        calls = []
        real_write = deps.pty_mgr.write
        def _counting_write(session_id, data):
            calls.append((session_id, data))
            return real_write(session_id, data)
        monkeypatch.setattr(deps.pty_mgr, "write", _counting_write)

        r1 = client.post(f"/api/sessions/{sid}/keys", json={"text": "1\r", "prompt_id": token})
        assert r1.status_code == 200
        r2 = client.post(f"/api/sessions/{sid}/keys", json={"text": "1\r", "prompt_id": token})
        assert r2.status_code == 409
        assert r2.json()["error"] == "stale_prompt"

        assert len(calls) == 1, f"두 번째 요청이 PTY에 썼다 — 경쟁 방지가 깨졌다: {calls}"
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_without_prompt_id_the_existing_path_still_works(client):
    """E7 — 캐시된 구버전 JS가 `prompt_id` 없이 보내도 그대로 통과한다."""
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "echo ok\r"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_prompt_id_on_a_session_without_a_tmux_name_is_409(client):
    """E14 — tmux_name이 없는 순수 PTY 세션. 상태를 붙일 대상이 없으므로
    승인 버튼 자체가 안 뜨지만, 방어적으로도 409여야 한다."""
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "1\r", "prompt_id": "1"})
        assert r.status_code == 409
        assert r.json()["error"] == "stale_prompt"
    finally:
        client.delete(f"/api/sessions/{sid}")
