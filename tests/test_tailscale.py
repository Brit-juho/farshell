"""D9: tailscale.py 단위 테스트.

`tailscale status --json` 출력을 subprocess mock으로 대체해 파싱 로직만 검증.
실제 tailscale 바이너리 유무와 무관하게 CI에서 돌아가야 한다.
"""

import json
import subprocess
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

import tailscale


RUNNING_JSON = json.dumps({
    "BackendState": "Running",
    "Self": {
        "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"],
        "DNSName": "my-mac.tailnet-name.ts.net.",
    },
})

STOPPED_JSON = json.dumps({
    "BackendState": "Stopped",
    "Self": {"TailscaleIPs": [], "DNSName": ""},
})


def test_is_installed_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: None)
    assert tailscale.is_installed() is False


def test_is_installed_true_when_binary_present(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: "/usr/local/bin/tailscale")
    assert tailscale.is_installed() is True


def test_get_status_not_installed(monkeypatch):
    monkeypatch.setattr(tailscale, "is_installed", lambda: False)
    status = tailscale.get_status()
    assert status.installed is False
    assert status.running is False
    assert status.ipv4 is None


def test_get_status_running_parses_ip_and_hostname(monkeypatch):
    monkeypatch.setattr(tailscale, "is_installed", lambda: True)
    monkeypatch.setattr(
        tailscale.subprocess, "check_output",
        lambda *a, **k: RUNNING_JSON.encode(),
    )
    status = tailscale.get_status()
    assert status.installed is True
    assert status.running is True
    assert status.ipv4 == "100.101.102.103"
    assert status.hostname == "my-mac.tailnet-name.ts.net"


def test_get_status_stopped(monkeypatch):
    monkeypatch.setattr(tailscale, "is_installed", lambda: True)
    monkeypatch.setattr(
        tailscale.subprocess, "check_output",
        lambda *a, **k: STOPPED_JSON.encode(),
    )
    status = tailscale.get_status()
    assert status.running is False
    assert status.ipv4 is None


def test_get_status_handles_subprocess_error(monkeypatch):
    monkeypatch.setattr(tailscale, "is_installed", lambda: True)
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=2.0)
    monkeypatch.setattr(tailscale.subprocess, "check_output", _raise)
    status = tailscale.get_status()
    assert status.installed is True
    assert status.running is False


def test_get_ip_none_when_not_running(monkeypatch):
    monkeypatch.setattr(tailscale, "get_status", lambda: tailscale.TailscaleStatus(installed=True, running=False))
    assert tailscale.get_ip() is None


def test_get_ip_returns_ipv4_when_running(monkeypatch):
    monkeypatch.setattr(
        tailscale, "get_status",
        lambda: tailscale.TailscaleStatus(installed=True, running=True, ips=["100.1.2.3", "fd7a::1"]),
    )
    assert tailscale.get_ip() == "100.1.2.3"


def test_get_hostname_none_when_not_running(monkeypatch):
    monkeypatch.setattr(
        tailscale, "get_status",
        lambda: tailscale.TailscaleStatus(installed=True, running=False, hostname="foo.ts.net"),
    )
    assert tailscale.get_hostname() is None


def test_get_status_dict_shape(monkeypatch):
    monkeypatch.setattr(
        tailscale, "get_status",
        lambda: tailscale.TailscaleStatus(
            installed=True, running=True, backend_state="Running",
            ips=["100.1.2.3"], hostname="foo.ts.net",
        ),
    )
    d = tailscale.get_status_dict()
    assert d == {
        "installed": True,
        "running": True,
        "backend_state": "Running",
        "ip": "100.1.2.3",
        "hostname": "foo.ts.net",
    }
