"""P3 회귀: 포트 대시보드가 잘못된 프로세스를 죽이거나 목록을 왜곡하지 않아야 한다.

실제로 물릴 수 있는 사고 3가지를 막는다:
  - VT 서버(자기 자신)나 cloudflared를 죽여 원격 접속이 통째로 끊기는 것
  - lsof의 IPv4/IPv6 중복 때문에 같은 프로세스가 두 번 보이는 것
  - 조회와 kill 사이에 PID가 재사용돼 엉뚱한 프로세스를 죽이는 것
"""

import os

import pytest

import portscan

# lsof -nP -iTCP -sTCP:LISTEN +c 0 의 실제 출력 형태 (macOS).
# ControlCenter/redis-server 는 +c 0 없이는 9자로 잘린다.
LSOF_SAMPLE = """COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
ControlCenter 856 neo 11u IPv4 0xb293 0t0 TCP *:5000 (LISTEN)
ControlCenter 856 neo 12u IPv6 0xb47b 0t0 TCP *:5000 (LISTEN)
redis-server 1828 neo 6u IPv4 0xe5e1 0t0 TCP 127.0.0.1:6379 (LISTEN)
redis-server 1828 neo 7u IPv6 0x962e 0t0 TCP [::1]:6379 (LISTEN)
python3.13 59851 neo 11u IPv4 0xd7ed 0t0 TCP *:7777 (LISTEN)
cloudflared 74342 neo 8u IPv4 0xaaaa 0t0 TCP 127.0.0.1:20241 (LISTEN)
node 13486 other 16u IPv6 0xd58b 0t0 TCP *:3434 (LISTEN)
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    portscan._cache = None
    yield
    portscan._cache = None


def test_ipv4_ipv6_duplicates_are_merged():
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    # 7개 줄이지만 (port,pid) 기준으로는 5개
    assert len(rows) == 5
    ctl = rows[(5000, 856)]
    assert sorted(ctl["families"]) == ["IPv4", "IPv6"]
    assert ctl["cmd"] == "ControlCenter"          # +c 0 이라 안 잘린다


def test_bracketed_ipv6_address_parses():
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    redis = rows[(6379, 1828)]
    assert "[::1]" in redis["addrs"] and "127.0.0.1" in redis["addrs"]


def test_vt_server_port_is_protected(monkeypatch):
    monkeypatch.setenv("VT_PORT", "7777")
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    row = rows[(7777, 59851)]
    protected, reason = portscan._classify(row, "neo", 7777)
    assert protected and "VT 서버" in reason


def test_cloudflared_is_protected():
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    row = rows[(20241, 74342)]
    protected, reason = portscan._classify(row, "neo", 7777)
    assert protected and "원격 접속" in reason


def test_other_user_process_is_protected():
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    row = rows[(3434, 13486)]                      # user=other
    protected, reason = portscan._classify(row, "neo", 7777)
    assert protected and "다른 사용자" in reason


def test_normal_process_is_killable():
    rows = portscan._parse_lsof(LSOF_SAMPLE)
    row = rows[(6379, 1828)]
    protected, _ = portscan._classify(row, "neo", 7777)
    assert not protected


def test_kill_refuses_vt_port(monkeypatch):
    monkeypatch.setenv("VT_PORT", "7777")
    r = portscan.kill_port(7777)
    assert r["ok"] is False and r["error"] == "protected"


def test_kill_refuses_when_pid_changed(monkeypatch):
    """조회 때 pid 111이었는데 kill 시점엔 222 → 중단해야 한다(PID 재사용)."""
    monkeypatch.setenv("VT_PORT", "7777")
    monkeypatch.setattr(portscan, "pid_for_port", lambda port: 222)
    r = portscan.kill_port(9999, expected_pid=111)
    assert r["ok"] is False and r["error"] == "pid_changed"


def test_kill_reports_not_found(monkeypatch):
    monkeypatch.setenv("VT_PORT", "7777")
    monkeypatch.setattr(portscan, "pid_for_port", lambda port: None)
    r = portscan.kill_port(9999)
    assert r["ok"] is False and r["error"] == "not_found"


def test_malformed_lsof_lines_are_skipped():
    junk = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\ngarbage\nfoo bar\n"
    assert portscan._parse_lsof(junk) == {}


def test_public_vs_loopback_flag(monkeypatch):
    """전 인터페이스(*)와 루프백은 위험도가 다르다 — UI가 구분할 수 있어야 한다."""
    monkeypatch.setattr(portscan, "_run", lambda *a, **k: LSOF_SAMPLE)
    monkeypatch.setattr(portscan, "_ps_info", lambda pids: {})
    monkeypatch.setenv("USER", "neo")
    monkeypatch.setenv("VT_PORT", "7777")
    by_port = {p["port"]: p for p in portscan.scan(use_cache=False)["ports"]}
    assert by_port[5000]["public"] is True         # *:5000
    assert by_port[6379]["public"] is False        # 127.0.0.1 / [::1]


def test_scan_uses_cache(monkeypatch):
    calls = []

    def fake_run(args, timeout):
        calls.append(args[0])
        return LSOF_SAMPLE

    monkeypatch.setattr(portscan, "_run", fake_run)
    monkeypatch.setattr(portscan, "_ps_info", lambda pids: {})
    portscan.scan(use_cache=False)
    n = len(calls)
    portscan.scan(use_cache=True)
    assert len(calls) == n                          # 캐시 히트 — 재호출 없음
    assert portscan.scan(use_cache=True)["cached"] is True
