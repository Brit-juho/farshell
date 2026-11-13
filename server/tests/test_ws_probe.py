"""생존 탐침(`resilience/wsutil.py` Prober) — 화면 복귀 뒤 "열린 것처럼 보이는 죽은 소켓"을 브라우저가 재는 왕복.

클라이언트 `{"type":"probe","id":n}` → 서버 즉시 `{"type":"probe_ack","id":n}`. 지키는 것:
- 다섯 소켓(`/ws/{id}` `/ws-agent` `/ws-workspace` `/ws-fs-watch` `/ws-notify`)이 같은 id로 답한다.
- 잘못된 id(정수 아님·bool·0·범위 밖)는 무시, 소켓당 초당 4회를 넘으면 무시.
- PTY 소켓은 탐침도 살아 있다는 증거로 보아 45초 하트비트 시계를 새로 한다(소스 배선 확인 — 아래 시험 설명 참고).
- 탐침이 다른 메시지 처리(예: fs-watch의 watch)를 깨지 않는다.
"""

import json
import pytest
from starlette.testclient import TestClient

import deps
import main
from resilience import wsutil


# ── Prober 단위 ───────────────────────────────────────────────────────────

def _prober(now):
    return wsutil.Prober(clock=lambda: now[0])


def test_prober_acks_valid_id():
    p = _prober([0.0])
    assert json.loads(p.ack_for({"type": "probe", "id": 7})) == {"type": "probe_ack", "id": 7}
    assert json.loads(p.ack_for({"type": "probe", "id": wsutil.PROBE_ID_MAX}))["id"] == wsutil.PROBE_ID_MAX


@pytest.mark.parametrize("bad", [None, "7", 7.0, True, False, 0, -1, wsutil.PROBE_ID_MAX + 1, [1], {"a": 1}])
def test_prober_ignores_bad_ids(bad):
    assert _prober([0.0]).ack_for({"type": "probe", "id": bad}) is None


def test_prober_ignores_missing_id_and_other_types():
    p = _prober([0.0])
    assert p.ack_for({"type": "probe"}) is None
    assert p.ack_for({"type": "pong", "id": 1}) is None
    assert p.ack_for("probe") is None
    assert p.ack_for(None) is None
    assert p.ack_for([{"type": "probe", "id": 1}]) is None


def test_prober_rate_limit_four_per_second_then_recovers():
    now = [100.0]
    p = _prober(now)
    answered = [p.ack_for({"type": "probe", "id": i}) for i in range(1, 8)]
    assert [a is not None for a in answered] == [True] * 4 + [False] * 3
    now[0] += 0.99
    assert p.ack_for({"type": "probe", "id": 9}) is None        # 첫 탐침으로부터 1초 전
    now[0] += 0.02
    assert p.ack_for({"type": "probe", "id": 10}) is not None   # 창이 지나 다시 답한다


def test_prober_rejected_probes_do_not_extend_the_window():
    now = [0.0]
    p = _prober(now)
    for i in range(1, 5):
        assert p.ack_for({"type": "probe", "id": i})
    for _ in range(100):                                          # 막힌 탐침이 창을 밀지 않는다
        now[0] += 0.009
        p.ack_for({"type": "probe", "id": 99})
    now[0] = 1.0
    assert p.ack_for({"type": "probe", "id": 5})


# ── 소켓 5종 ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    deps.ws_total_count = 0
    deps.ws_count_per_session.clear()
    with TestClient(main.app) as c:
        yield c


def _json_or_none(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _until(ws, pred, limit=50):
    """pred(msg)가 참인 JSON 텍스트 메시지가 올 때까지 읽는다(바이너리·ping·스냅샷은 건너뜀)."""
    for _ in range(limit):
        m = ws.receive()
        if m.get("type") == "websocket.close":
            raise AssertionError(f"소켓이 닫혔다: {m}")
        text = m.get("text")
        if text is None:
            continue
        msg = _json_or_none(text)
        if isinstance(msg, dict) and pred(msg):
            return msg
    raise AssertionError("기대한 메시지가 오지 않았다")


def _ack(ws, pid):
    ws.send_text(json.dumps({"type": "probe", "id": pid}))
    return _until(ws, lambda m: m.get("type") == "probe_ack")


@pytest.mark.parametrize("path", ["/ws-agent", "/ws-workspace", "/ws-fs-watch", "/ws-notify"])
def test_probe_ack_on_each_socket(client, path):
    with client.websocket_connect(path) as ws:
        assert _ack(ws, 41) == {"type": "probe_ack", "id": 41}
        assert _ack(ws, 42) == {"type": "probe_ack", "id": 42}


def test_probe_ack_on_pty_socket(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    sid = r.json()["id"]
    try:
        with client.websocket_connect(f"/ws/{sid}") as ws:
            assert _ack(ws, 5) == {"type": "probe_ack", "id": 5}
    finally:
        client.delete(f"/api/sessions/{sid}")


@pytest.mark.parametrize("path", ["/ws-agent", "/ws-workspace", "/ws-fs-watch", "/ws-notify"])
def test_bad_ids_get_no_ack_and_socket_stays_usable(client, path):
    with client.websocket_connect(path) as ws:
        for bad in ("7", 0, -5, True, 2**31, None):
            ws.send_text(json.dumps({"type": "probe", "id": bad}))
        # 무시됐다 — 바로 뒤의 유효 탐침에만 답이 오고, 그 id는 마지막에 보낸 것이다.
        assert _ack(ws, 77)["id"] == 77


def test_rate_limit_over_a_real_socket(client):
    with client.websocket_connect("/ws-fs-watch") as ws:
        for i in range(1, 9):
            ws.send_text(json.dumps({"type": "probe", "id": i}))
        acks = []
        # 확인용 watch를 끼워 서버가 앞선 메시지를 모두 처리했음을 알고 거기까지 읽는다.
        ws.send_json({"type": "watch", "subs": []})
        msg = _until(ws, lambda m: m["type"] in ("probe_ack", "watching"))
        while msg["type"] == "probe_ack":
            acks.append(msg["id"])
            msg = _until(ws, lambda m: m["type"] in ("probe_ack", "watching"))
        assert acks == [1, 2, 3, 4]


def test_probe_does_not_break_fs_watch_protocol(client):
    with client.websocket_connect("/ws-fs-watch") as ws:
        _ack(ws, 1)
        ws.send_json({"type": "watch", "subs": []})
        assert _until(ws, lambda m: m["type"] == "watching") == {"type": "watching", "rejected": []}


def test_pty_probe_refreshes_heartbeat_clock():
    """PTY 소켓은 탐침도 살아 있다는 증거로 보아 하트비트 시계(last_pong)를 새로 한다.

    실제 소켓으로 "탐침을 보내는 동안 안 닫힌다"를 재는 시험은 이 Mac에서 시험 프로세스가 1초 넘게
    멎는 일이 있어(다른 시험 뒤 sleep 0.15초가 0.3초로 늘고 가끔 1.5초 넘게 멎음) 하트비트 기한과 겨뤄 흔들렸다.
    그래서 시계를 다루는 두 줄의 배선을 소스에서 확인한다 — 하트비트가 이 변수로 닫는지, 탐침 분기가 갱신하는지.
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "pty.py").read_text(encoding="utf-8")
    assert "loop.time() - last_pong > WS_HEARTBEAT_TIMEOUT" in src
    assert re.search(r'msg_type == "probe":\s*(?:#[^\n]*\n\s*)*last_pong = loop\.time\(\)', src), \
        "probe 분기가 last_pong을 갱신해야 한다"
    # pong 분기와 같은 방식이다
    assert re.search(r'msg_type == "pong":\s*last_pong = loop\.time\(\)', src)
