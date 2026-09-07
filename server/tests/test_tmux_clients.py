"""C1·C2 — 멀티 클라이언트 화면 관리.

🔴 이 엔드포인트들은 **남의 화면을 끊을 수 있다.** 그래서 테스트의 무게가
기능보다 **경계**에 있다: 클라이언트가 tty를 직접 고르지 못하게 하고(요청자는
자기 web session id만 보낸다), 자기 자신은 못 끊고(복구 경로가 없다), 남길
화면을 특정 못 하면 아무것도 안 끊는다.
"""

import pytest
from starlette.testclient import TestClient

import main
import tmux_runner
from deps import pty_mgr
from pty_manager import PTYSession


@pytest.fixture
def env(monkeypatch):
    """가짜 tmux + 가짜 PTY 세션(tty 포함)."""
    calls = []

    def fake_run_text(args, timeout=None):
        if "list-clients" in args:
            return ("/dev/ttys001\tweb\t151\t42\t1788000000\n"
                    "/dev/ttys002\titerm\t80\t24\t1788000100")
        return ""

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (0, "", "")

    monkeypatch.setattr(tmux_runner, "run_text", fake_run_text)
    monkeypatch.setattr(tmux_runner, "run", fake_run)

    sess = PTYSession(session_id="web-1", pid=1, fd=-1, tty="/dev/ttys001")
    pty_mgr._sessions["web-1"] = sess
    yield calls
    pty_mgr._sessions.pop("web-1", None)


@pytest.fixture
def client(env):
    with TestClient(main.app) as c:
        yield c


def test_clients_marks_me(client):
    body = client.get("/api/tmux/clients?session=dev&me=web-1").json()
    assert body["me_tty"] == "/dev/ttys001"
    me = [c for c in body["clients"] if c["is_me"]]
    assert len(me) == 1 and me[0]["tty"] == "/dev/ttys001"
    assert me[0]["label"] == "이 화면"


def test_clients_without_me_marks_nothing(client):
    """`me`를 안 보내면 아무도 '나'가 아니다 — 추측하지 않는다."""
    body = client.get("/api/tmux/clients?session=dev").json()
    assert body["me_tty"] is None
    assert all(c["is_me"] is False for c in body["clients"])


def test_clients_rejects_bad_session_name(client):
    r = client.get("/api/tmux/clients?session=-t%20other")
    assert r.status_code == 400


def test_detach_other_client(client, env):
    r = client.post("/api/tmux/detach-client", json={"tty": "/dev/ttys002", "me": "web-1"})
    assert r.status_code == 200
    assert ["detach-client", "-t", "/dev/ttys002"] in env


def test_detach_self_is_refused(client, env):
    """지금 보고 있는 화면을 스스로 끊으면 복구 경로가 없다."""
    r = client.post("/api/tmux/detach-client", json={"tty": "/dev/ttys001", "me": "web-1"})
    assert r.status_code == 400
    assert r.json()["error"] == "cannot detach self"
    assert env == [], "tmux 명령이 아예 나가면 안 된다"


def test_solo_keeps_requester_and_detaches_others(client, env):
    r = client.post("/api/tmux/clients/solo", json={"session": "dev", "me": "web-1"})
    body = r.json()
    assert body["kept"] == "/dev/ttys001"
    assert body["detached"] == ["/dev/ttys002"]
    assert ["detach-client", "-t", "/dev/ttys002"] in env
    # 남은 화면 크기로 즉시 재동기화하지 않으면, 방금 사라진 작은 화면 크기가
    # 한동안 그대로 남는다.
    assert ["refresh-client", "-t", "/dev/ttys001"] in env


def test_solo_without_known_tty_detaches_nothing(client, env):
    """남길 화면을 특정 못 하면 **아무것도 끊지 않는다** — 전부 끊으면 되돌릴 수 없다."""
    r = client.post("/api/tmux/clients/solo", json={"session": "dev", "me": "unknown"})
    assert r.status_code == 400
    assert env == []


def test_solo_rejects_bad_session_name(client, env):
    r = client.post("/api/tmux/clients/solo", json={"session": "; rm -rf /", "me": "web-1"})
    assert r.status_code == 400
    assert env == []


def test_pty_session_records_slave_tty():
    """C1의 진짜 작업량 — 이 값이 없으면 '이게 나인가'를 판별할 수 없다."""
    import asyncio

    async def run():
        sid = "tty-probe"
        try:
            pty_mgr.create_session(sid, cmd="/bin/echo", cmd_args=["/bin/echo", "hi"], cols=80, rows=24)
            sess = pty_mgr.sessions[sid]
            assert sess.tty.startswith("/dev/"), f"슬레이브 tty가 기록돼야 한다: {sess.tty!r}"
        finally:
            pty_mgr.destroy_session(sid)

    asyncio.run(run())
