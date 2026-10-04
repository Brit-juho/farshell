"""리눅스 시스템 라이브러리 요구사항 — 최소 이미지에서 libgomp가 없어 엔진이 시작 중 종료되던 실측 문제."""
import subprocess

import pytest

from stt_engines import llama_release
from stt_engines import llamacpp_qwen3 as lc
from stt_engines.env import HostEnv

LINUX = HostEnv(os="linux", arch="arm64", macos_version=None)
MAC = HostEnv(os="macos", arch="arm64", macos_version=(26, 0))


def _fake_ldd(monkeypatch, stdout):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ldd" if name == "ldd" else None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=""))


def test_missing_libgomp_reported_from_ldd(monkeypatch, tmp_path):
    _fake_ldd(monkeypatch, "\tlibgomp.so.1 => not found\n\tlibc.so.6 => /lib/libc.so.6\n")
    assert lc._linux_missing_libs(tmp_path / "llama-server") == ["libgomp"]


def test_nothing_missing_when_ldd_clean(monkeypatch, tmp_path):
    _fake_ldd(monkeypatch, "\tlibc.so.6 => /lib/libc.so.6\n")
    assert lc._linux_missing_libs(tmp_path / "llama-server") == []


def test_without_binary_falls_back_to_find_library(monkeypatch):
    import ctypes.util
    monkeypatch.setattr(ctypes.util, "find_library", lambda n: None)
    assert lc._linux_missing_libs(None) == ["libgomp"]
    monkeypatch.setattr(ctypes.util, "find_library", lambda n: "libgomp.so.1")
    assert lc._linux_missing_libs(None) == []


def test_requirement_listed_on_linux_with_install_hint(monkeypatch):
    monkeypatch.setattr(llama_release, "find_binary", lambda: None)
    monkeypatch.setattr(lc, "_linux_missing_libs", lambda b: ["libgomp"])
    reqs = {r.id: r for r in lc.LlamaCppQwen3Engine().requirements(LINUX)}
    assert not reqs["linux-libs"].satisfied
    assert "apt install libgomp1" in reqs["linux-libs"].hint
    assert reqs["linux-libs"].can_install is False          # 시스템 패키지는 앱이 대신 설치하지 않는다


def test_requirement_absent_on_macos(monkeypatch):
    monkeypatch.setattr(llama_release, "find_binary", lambda: None)
    assert "linux-libs" not in {r.id for r in lc.LlamaCppQwen3Engine().requirements(MAC)}


def test_rss_reads_proc_without_ps(monkeypatch, tmp_path):
    """ps가 없는 최소 이미지(실측: python:slim)에서도 메모리 조회가 예외 없이 동작해야 한다."""
    from stt_engines import _proc
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/4242/status":
            import io
            return io.StringIO("Name:\tx\nVmRSS:\t  204800 kB\n")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ps")))
    assert _proc.rss_mb(4242) == 200


def test_rss_zero_when_nothing_available(monkeypatch):
    from stt_engines import _proc
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ps")))
    assert _proc.rss_mb(999999999) == 0
