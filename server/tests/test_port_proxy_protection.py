"""2.2 — 내부 프록시 리스너 보호 + 기동 시 복구.

프록시 리스너는 **VT 서버 자신의 프로세스**가 연 포트다. 죽이면 PID가 같은
서버가 통째로 죽는다. 목록에서 감추는 것만으로는 부족하고(엔드포인트가 포트
번호를 직접 받는다), kill 경로 자체가 막혀야 한다.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def scan_mod(monkeypatch):
    import portscan
    importlib.reload(portscan)
    monkeypatch.setattr(portscan, "_vt_port", lambda: 7777)
    return portscan


def _row(port, **kw):
    base = {"port": port, "pid": 100, "cmd": "python3", "user": "me",
            "families": ["IPv4"], "addrs": ["127.0.0.1"], "command": "/usr/bin/python3 x"}
    base.update(kw)
    return base


# --- 분류 ----------------------------------------------------------------------


def test_proxy_port_is_classified_protected(scan_mod):
    protected, reason = scan_mod._classify(_row(7801), "me", 7777, {7801})
    assert protected is True
    assert reason == scan_mod.PROTECT_PROXY


def test_ordinary_port_stays_killable(scan_mod):
    protected, _ = scan_mod._classify(_row(3000), "me", 7777, {7801})
    assert protected is False


def test_classify_without_proxy_set_is_backward_compatible(scan_mod):
    """기존 호출부(인자 3개)가 그대로 동작해야 한다."""
    assert scan_mod._classify(_row(3000), "me", 7777)[0] is False


def test_vt_port_still_wins(scan_mod):
    assert scan_mod._classify(_row(7777), "me", 7777, set())[1] == scan_mod.PROTECT_SELF


# --- kill 경로가 실제 방어선이다 -----------------------------------------------------


def test_kill_port_refuses_proxy_listener(scan_mod, monkeypatch):
    """목록을 안 거치고 포트 번호로 직접 호출해도 막혀야 한다."""
    monkeypatch.setattr(scan_mod, "_proxy_ports", lambda: {7801})
    called = []
    monkeypatch.setattr(scan_mod, "pid_for_port", lambda p: called.append(p) or 100)
    result = scan_mod.kill_port(7801)
    assert result["ok"] is False
    assert result["error"] == "protected"
    assert result["reason"] == scan_mod.PROTECT_PROXY
    assert called == [], "보호 판정 전에 조회까지 갔다 — 순서가 뒤집혔다"


def test_kill_port_still_allows_normal_port(scan_mod, monkeypatch):
    monkeypatch.setattr(scan_mod, "_proxy_ports", lambda: {7801})
    monkeypatch.setattr(scan_mod, "pid_for_port", lambda p: None)
    assert scan_mod.kill_port(3000)["error"] == "not_found"


# --- 프록시 모듈을 못 읽어도 스캔은 돌아야 한다 ---------------------------------------


def test_proxy_ports_failure_is_not_fatal(scan_mod, monkeypatch):
    """스캔 전체가 프록시 모듈 사정으로 죽으면 포트 대시보드가 통째로 먹통이 된다."""
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name == "port_proxy":
            raise RuntimeError("terrible")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert scan_mod._proxy_ports() == set()


# --- 기동 시 복구가 lifespan에 걸려 있는가 -------------------------------------------


def test_lifespan_restores_and_stops_listeners():
    """`fsh stop --keep-tunnel`이 터널을 살려두므로 재기동 때 같은 주소로 붙어야 한다."""
    import inspect
    import main
    src = inspect.getsource(main.lifespan)
    assert "port_proxy.sync()" in src, "기동 시 리스너 복구가 없다"
    assert "port_proxy.shutdown_all()" in src, "종료 시 리스너 정리가 없다"


def test_lifespan_restore_failure_does_not_block_boot():
    """터미널이 안 뜨는 것이 공유가 안 되는 것보다 훨씬 나쁘다."""
    import inspect
    import main
    src = inspect.getsource(main.lifespan)
    sync_at = src.index("port_proxy.sync()")
    guarded = src.rindex("try:", 0, sync_at)
    assert "except Exception" in src[guarded:], "복구 실패가 기동을 막는다"
