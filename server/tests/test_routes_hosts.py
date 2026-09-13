"""N7/N39 2단계 — `/api/hosts` 집계 + `/api/peer/sessions`.

실제 tmux/네트워크는 monkeypatch로 대체한다 — 여기서 보는 건 집계 규칙이다:
로컬이 항상 첫 항목인가 · 꺼진 호스트가 목록 전체를 실패시키지 않는가 ·
원격 상태가 host 차원으로 기록돼 로컬과 안 섞이는가(A1의 실제 이득).
"""

from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "ws.json"))
    import agent_status
    import host_store
    importlib.reload(host_store)
    agent_status._state.clear()
    import routes.hosts as hosts_mod
    importlib.reload(hosts_mod)
    import main
    importlib.reload(main)

    # 로컬 tmux는 항상 이 두 세션인 것으로 고정 — 실제 tmux 상태에 의존하지 않는다.
    monkeypatch.setattr(hosts_mod, "_local_entry", lambda: {
        "id": agent_status.LOCAL_HOST, "label": "내 맥", "url": "", "version": "",
        "online": True, "latencyMs": 0, "lastSeen": 0,
        "sessions": [{"name": "dev", "windows": 1, "attached": 0,
                      "command": "zsh", "cwd": "/x", "status": "idle"}],
    })
    with TestClient(main.app) as c:
        yield c, host_store, hosts_mod, agent_status


def _add_peer(hs, pid="gpu-box"):
    hs.add_peer(pid, "http://127.0.0.1:9", "sekret", label="작업실")
    return pid


# --- 집계 ---------------------------------------------------------------------


def test_hosts_lists_local_first_with_no_peers(env):
    client, _, _, a = env
    body = client.get("/api/hosts").json()
    assert [h["id"] for h in body["hosts"]] == [a.LOCAL_HOST]
    assert body["hosts"][0]["online"] is True


def test_hosts_includes_registered_peer(env, monkeypatch):
    client, hs, mod, _ = env
    _add_peer(hs)
    monkeypatch.setattr(mod, "_fetch_remote", lambda peer: {
        "online": True, "latencyMs": 12,
        "sessions": [{"name": "train", "status": "working", "windows": 1,
                      "attached": 0, "command": "python", "cwd": "/r"}],
    })
    body = client.get("/api/hosts", params={"fresh": True}).json()
    ids = [h["id"] for h in body["hosts"]]
    assert ids == ["local", "gpu-box"]
    remote = body["hosts"][1]
    assert remote["label"] == "작업실"
    assert [s["name"] for s in remote["sessions"]] == ["train"]


def test_offline_peer_does_not_break_the_list(env, monkeypatch):
    """호스트 하나가 꺼져 있다고 목록 전체가 실패하면 안 된다 — 상태로 표현한다."""
    client, hs, mod, _ = env
    _add_peer(hs)
    monkeypatch.setattr(mod, "_fetch_remote", lambda peer: {
        "online": False, "reason": "연결 실패", "sessions": [],
    })
    body = client.get("/api/hosts", params={"fresh": True}).json()
    assert body["hosts"][0]["online"] is True          # 로컬은 멀쩡하다
    assert body["hosts"][1]["online"] is False
    assert body["hosts"][1]["reason"] == "연결 실패"
    assert body["hosts"][1]["sessions"] == []


def test_peer_secret_never_reaches_the_response(env, monkeypatch):
    client, hs, mod, _ = env
    _add_peer(hs)
    monkeypatch.setattr(mod, "_fetch_remote",
                        lambda peer: {"online": True, "latencyMs": 1, "sessions": []})
    raw = client.get("/api/hosts", params={"fresh": True}).text
    assert "sekret" not in raw


# --- 캐시 ---------------------------------------------------------------------


def test_remote_result_is_cached_between_calls(env, monkeypatch):
    """레일이 2초마다 새로고침해도 원격 왕복은 30초에 한 번이어야 한다."""
    client, hs, mod, _ = env
    _add_peer(hs)
    calls = []
    monkeypatch.setattr(mod, "_fetch_remote", lambda peer: calls.append(1) or {
        "online": True, "latencyMs": 1, "sessions": []})
    client.get("/api/hosts", params={"fresh": True})
    client.get("/api/hosts")
    client.get("/api/hosts")
    assert len(calls) == 1


def test_fresh_bypasses_the_cache(env, monkeypatch):
    client, hs, mod, _ = env
    _add_peer(hs)
    calls = []
    monkeypatch.setattr(mod, "_fetch_remote", lambda peer: calls.append(1) or {
        "online": True, "latencyMs": 1, "sessions": []})
    client.get("/api/hosts", params={"fresh": True})
    client.get("/api/hosts", params={"fresh": True})
    assert len(calls) == 2


# --- 원격 상태가 host 차원으로 기록되는가 (A1의 실제 이득) -------------------------


def test_remote_status_is_recorded_under_peer_host(env, monkeypatch):
    """원격 `dev`가 waiting이어도 로컬 `dev` 조회는 영향을 받지 않아야 한다 —
    이게 안 되면 큐가 로컬 pane을 남의 상태 때문에 차단한다."""
    client, hs, mod, a = env
    _add_peer(hs)
    import peer_client
    monkeypatch.setattr(peer_client, "_call_sync", lambda *args, **kw: {
        "sessions": [{"name": "dev", "status": "waiting"}],
    })
    client.get("/api/hosts", params={"fresh": True})
    assert a.status_for_session("dev", host="gpu-box") == a.WAITING
    assert a.status_for_session("dev") == a.IDLE      # 로컬은 그대로


def test_unknown_remote_status_is_ignored(env, monkeypatch):
    """상대가 보낸 status 문자열을 그대로 믿지 않는다 — 모르는 값은 버린다."""
    client, hs, mod, a = env
    _add_peer(hs)
    import peer_client
    monkeypatch.setattr(peer_client, "_call_sync", lambda *args, **kw: {
        "sessions": [{"name": "dev", "status": "pwned"}],
    })
    client.get("/api/hosts", params={"fresh": True})
    assert a.get_state("peer:dev", host="gpu-box") == {}


# --- self / ping --------------------------------------------------------------


def test_self_returns_id_and_label(env):
    client, _, _, _ = env
    body = client.get("/api/hosts/self").json()
    assert body["id"] and body["label"]


def test_ping_unknown_host_is_404(env):
    client, _, _, _ = env
    assert client.post("/api/hosts/nope/ping").status_code == 404


def test_ping_failure_is_200_with_offline_state(env, monkeypatch):
    """연결 실패는 서버 오류가 아니라 상태다 — 500이면 프런트가 에러 토스트를 띄운다."""
    client, hs, mod, _ = env
    _add_peer(hs)
    import peer_client
    monkeypatch.setattr(peer_client, "ping_sync", lambda peer: (_ for _ in ()).throw(
        peer_client.PeerError("연결 실패: refused")))
    r = client.post("/api/hosts/gpu-box/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "online": False, "reason": "연결 실패: refused"}


# --- 2.1.3: 원격 「연결된 화면」·검색 중계 ------------------------------------------


def test_host_clients_proxies_and_passes_query_outside_the_signature(env, monkeypatch):
    """쿼리는 **서명 대상이 아니다**(상대는 path만 서명 검증한다) — 그래서 path에
    이어 붙이면 서명이 깨진다. `_call_sync`가 쿼리를 따로 받는 이유이고, 이
    테스트가 그 계약을 고정한다."""
    client, hs, mod, _ = env
    _add_peer(hs)
    seen = {}

    def fake_call(peer, method, path, body=None, timeout=None, query=None):
        seen.update(peer=peer["id"], method=method, path=path, query=query)
        return {"session": "dev", "clients": [], "me_tty": "/dev/ttys002"}

    monkeypatch.setattr(mod.peer_client, "_call_sync", fake_call)
    r = client.get("/api/hosts/gpu-box/clients?session=dev&screen=abc")
    assert r.status_code == 200
    assert seen["path"] == "/api/peer/clients"   # 쿼리가 섞이지 않았다
    assert seen["query"] == {"session": "dev", "screen": "abc"}


def test_host_clients_passes_through_the_remote_403(env, monkeypatch):
    """상대가 view만 줬으면 403이 그대로 와야 한다 — 502로 뭉개면 화면이
    「등급을 올리세요」를 말할 수 없다."""
    client, hs, mod, _ = env
    _add_peer(hs)

    def boom(*a, **k):
        raise mod.peer_client.PeerError("이 호스트는 읽기 전용(view)입니다", 403)

    monkeypatch.setattr(mod.peer_client, "call", boom)
    r = client.post("/api/hosts/gpu-box/clients/solo", json={"session": "dev", "screen": "abc"})
    assert r.status_code == 403
    assert "읽기 전용" in r.json()["reason"]


def test_host_clients_unknown_host_is_404(env):
    client, _, _, _ = env
    assert client.get("/api/hosts/nope/clients?session=dev").status_code == 404


def test_host_search_labels_rows_from_our_own_registry(env, monkeypatch):
    """호스트 라벨은 **이쪽 레지스트리**의 이름을 쓴다 — 원격이 자기를 뭐라고
    부르든 화면에 그 이름이 뜨면 안 된다."""
    client, hs, mod, _ = env
    _add_peer(hs)
    monkeypatch.setattr(
        mod.peer_client, "_call_sync",
        lambda peer, method, path, body=None, timeout=None, query=None: {
            "results": [{"session_name": "train", "line": "boom", "line_no": 1,
                         "source": "log", "context_before": [], "context_after": [],
                         "host_label": "내가 지은 이름"}],
            "truncated": False,
        })
    row = client.get("/api/hosts/gpu-box/search?q=boom").json()["results"][0]
    assert row["host"] == "gpu-box"
    assert row["host_label"] == "작업실"
