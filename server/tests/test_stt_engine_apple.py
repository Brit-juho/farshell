"""Apple SpeechAnalyzer 엔진 — 단위(Swift 없이) + 통합(실제 macOS 26+·swiftc 필요, 없으면 skip)."""
import glob
import hashlib
import platform
import shutil
import subprocess

import pytest

from stt_engines import apple_speech as mod
from stt_engines.apple_speech import AppleSpeechEngine
from stt_engines.base import NotReady
from stt_engines.catalog import get
from stt_engines.env import HostEnv


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.vt_paths, "state_dir", lambda: tmp_path)
    e = AppleSpeechEngine()
    yield e
    e.unload()


def _env(os_="macos", arch="arm64", ver=(26, 0)):
    return HostEnv(os=os_, arch=arch, macos_version=ver)


@pytest.mark.parametrize("env,ok", [
    (_env(), True),
    (_env(ver=(27, 0)), True),
    (_env(ver=(15, 5)), False),
    (_env(arch="x86_64"), False),
    (_env(os_="linux", ver=None), False),
    (_env(os_="other", arch="x86_64", ver=None), False),
])
def test_supported(eng, env, ok):
    got, why = eng.supported(env)
    assert got is ok
    assert (why == "") == ok
    if not ok:
        assert "macOS 26" in why


def test_helper_name_uses_source_hash(eng, tmp_path):
    h = hashlib.sha256(mod.SRC.read_bytes()).hexdigest()[:8]
    assert mod.helper_path() == tmp_path / "engines" / "apple" / f"stt_apple-{h}"


def test_requirements_states(eng, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    sw, helper = eng.requirements(_env())
    assert (sw.kind, sw.satisfied, sw.hint) == ("system", False, "xcode-select --install")
    assert (helper.kind, helper.satisfied, helper.can_install) == ("binary", False, False)
    monkeypatch.setattr(mod.shutil, "which", lambda n: "/usr/bin/swiftc")
    sw, helper = eng.requirements(_env())
    assert sw.satisfied and helper.can_install and not helper.satisfied
    p = mod.helper_path()
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    assert eng.requirements(_env())[1].satisfied


def test_load_does_not_build_silently(eng, monkeypatch):
    called = []
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: called.append(a))
    with pytest.raises(NotReady, match="설정에서 먼저 준비"):
        eng.load(get("apple-speech-ko"))
    assert called == [] and eng.loaded() is None
    with pytest.raises(NotReady):
        eng.transcribe(b"x", "ko")
    assert eng.model_present(None) is False


def test_install_requires_swiftc_and_reports_build_error(eng, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    with pytest.raises(NotReady, match="xcode-select"):
        eng.install_requirement("apple-helper", lambda d, t: None)
    monkeypatch.setattr(mod.shutil, "which", lambda n: "/usr/bin/swiftc")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", "warn\nerror: boom\n"))
    with pytest.raises(NotReady, match="error: boom"):
        eng.install_requirement("apple-helper", lambda d, t: None)
    assert not mod.helper_path().exists()


def test_unknown_requirement_rejected(eng):
    with pytest.raises(NotReady):
        eng.install_requirement("nope", lambda d, t: None)


def test_delete_model_is_noop(eng):
    assert eng.delete_model(get("apple-speech-ko")) == 0


@pytest.mark.parametrize("bad", ["/tmp/a\nb.wav", "/tmp/a\rb.wav"])
def test_path_newline_rejected(bad):
    with pytest.raises(ValueError):
        mod.check_path_line(bad)
    assert mod.check_path_line("/tmp/ok.wav") == "/tmp/ok.wav"


def test_rss_zero_without_process(eng):
    assert eng.rss_mb() == 0


# ── 통합 ────────────────────────────────────────────────────────────
def _mac26():
    try:
        return platform.system() == "Darwin" and int(platform.mac_ver()[0].split(".")[0]) >= 26
    except ValueError:
        return False


@pytest.mark.skipif(not _mac26() or shutil.which("swiftc") is None, reason="macOS 26+ 및 swiftc 필요")
def test_real_build_load_transcribe_unload(eng):
    spec = get("apple-speech-ko")
    eng.install_requirement("apple-helper", lambda d, t: None)
    assert mod.helper_path().is_file()
    if not eng.model_present(spec):
        pytest.skip("ko-KR 자산 미설치")
    eng.load(spec)
    assert eng.loaded() == "apple-speech-ko"
    pid = eng._proc.pid
    wavs = sorted(glob.glob("/tmp/stt-bench/samples/*.wav"))[:3]
    if not wavs:
        pytest.skip("샘플 wav 없음")
    for w in wavs:
        assert eng.transcribe(open(w, "rb").read(), "ko")
    assert eng.rss_mb() > 0
    eng.unload()
    assert eng.loaded() is None
    assert subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0
