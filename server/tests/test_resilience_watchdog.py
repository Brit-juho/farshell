"""서버 워치독·헬스·경로 차단 응답."""
import json
import time

import pytest
from starlette.testclient import TestClient

import server_watchdog as wd
from resilience import breaker, fsops, pools


@pytest.fixture
def run_dir(monkeypatch, tmp_path):
    d = tmp_path / "run"
    (d / "vt-pids").mkdir(parents=True)
    d.chmod(0o700)
    monkeypatch.setattr(wd, "_run_dir", lambda: d)
    return d


def test_classify_ready_responses():
    assert wd._classify(200, b'{"ok":true,"status":"degraded","problems":["pool:fs:degraded"]}', "tcp")[0] == "ok"
    st, data, why = wd._classify(503, json.dumps({"ok": False, "problems": ["pool:default:unresponsive"]}).encode(), "socket")
    assert st == "unready" and "pool:default:unresponsive" in why
    assert wd._classify(401, b"{}", "tcp")[0] == "ok"           # 자격 문제는 재시작 사유가 아니다
    assert wd._classify(502, b"", "tcp")[0] == "unready"


def test_restart_budget_stops_restart_loops(run_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(wd.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(wd, "RESTART_MAX", 2)
    assert wd.restart("t1") and wd.restart("t2")
    assert wd.restart("t3") is False                 # 시간당 상한 — 원인이 그대로면 재시작해도 또 막힌다
    assert len(calls) == 2
    assert calls[0][0][-1] == "restart"


def test_collect_incident_writes_bundle(run_dir):
    (run_dir / "vt-server.log").write_text("line1\nline2\n")
    d = wd.collect_incident("test reason", {"problems": ["pool:default:unresponsive"]})
    assert (d / "reason.txt").read_text().splitlines()[1] == "test reason"
    assert json.loads((d / "ready.json").read_text())["problems"]
    assert "line2" in (d / "server-log-tail.txt").read_text()


def test_summary_reports_stopped_without_pid(run_dir):
    out = wd.summary()
    assert out.splitlines()[0] == "READY stopped"
    assert "WATCHDOG off" in out


def test_health_endpoints():
    import main
    with TestClient(main.app) as c:
        live = c.get("/api/health/live")
        assert live.status_code == 200 and set(live.json()) == {"ok", "pid", "uptime"}
        ready = c.get("/api/health/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["ok"] is True and {p["name"] for p in body["pools"]} >= {"default", "fs", "git", "proc"}


def test_blocked_path_returns_503_fs_unavailable(tmp_path, monkeypatch):
    import main
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    (tmp_path / "d").mkdir()
    breaker._reset_for_tests()
    breaker.trip(str(tmp_path / "d"), "test")
    try:
        with TestClient(main.app) as c:
            r = c.get("/api/fs/tree", params={"path": str(tmp_path / "d")})
            assert r.status_code == 503 and r.json()["error"] == "fs_unavailable"
            assert int(r.headers["Retry-After"]) > 0
            # 막히지 않은 형제 경로는 그대로 열린다
            (tmp_path / "e").mkdir()
            assert c.get("/api/fs/tree", params={"path": str(tmp_path / "e")}).status_code == 200
    finally:
        breaker._reset_for_tests()


def test_fs_timeout_trips_breaker(tmp_path):
    import asyncio
    import threading
    gate = threading.Event()
    breaker._reset_for_tests()

    async def scenario():
        with pytest.raises(pools.OpTimeout):
            await fsops.call(gate.wait, path=str(tmp_path / "x"), op="t", timeout=0.2)
        with pytest.raises(breaker.PathBlocked):
            await fsops.call(lambda: 1, path=str(tmp_path / "x" / "y"), op="t")

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
        breaker._reset_for_tests()
