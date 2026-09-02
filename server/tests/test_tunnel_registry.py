"""L5 회귀: 포트 대시보드의 "미리보기" 버튼이 죽은 터널을 살아있다고 속으면 안 된다."""

import os

import pytest

import tunnel_registry


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_registry, "PID_DIR", tmp_path)
    monkeypatch.setattr(tunnel_registry, "REGISTRY_PATH", tmp_path / "tunnels.tsv")
    yield


def _write_registry(rows):
    tunnel_registry.REGISTRY_PATH.write_text(
        "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8"
    )


def _write_pidfile(port, pid):
    (tunnel_registry.PID_DIR / f"tunnel-{port}.pid").write_text(str(pid))


def test_no_registry_file_returns_empty():
    assert tunnel_registry.exposed_ports() == {}


def test_alive_pid_is_included():
    _write_registry([("3000", "https://foo.trycloudflare.com", "my app")])
    _write_pidfile(3000, os.getpid())  # 자기 자신 — 확실히 살아있는 pid
    result = tunnel_registry.exposed_ports()
    assert result == {3000: {"url": "https://foo.trycloudflare.com", "label": "my app"}}


def test_dead_pid_is_excluded():
    """pid 파일이 있어도 프로세스가 죽었으면 살아있다고 속으면 안 된다."""
    _write_registry([("3000", "https://foo.trycloudflare.com", "")])
    _write_pidfile(3000, 999999)  # 존재하지 않을 가능성이 매우 높은 pid
    assert tunnel_registry.exposed_ports() == {}


def test_missing_pidfile_is_excluded():
    _write_registry([("3000", "https://foo.trycloudflare.com", "")])
    # pid 파일 자체가 없음 — expose 도중이거나 unexpose 이후 레지스트리만 안 지워진 상태
    assert tunnel_registry.exposed_ports() == {}


def test_no_label_falls_back_to_localhost_port():
    _write_registry([("4000", "https://bar.trycloudflare.com", "")])
    _write_pidfile(4000, os.getpid())
    assert tunnel_registry.exposed_ports()[4000]["label"] == "localhost:4000"


def test_malformed_lines_are_skipped():
    _write_registry([("not-a-port", "https://x", "y")])
    assert tunnel_registry.exposed_ports() == {}
