"""직접 입력 모델 삭제 범위 · 고아 프로세스 정리가 남의 프로세스를 건드리지 않는지."""
import os
import subprocess

import pytest

import stt_service
from stt_engines import llamacpp_qwen3 as lc


def test_delete_custom_not_managed_by_app_is_refused(monkeypatch, tmp_path):
    import vt_paths
    monkeypatch.setattr(vt_paths, "state_dir", lambda: tmp_path)
    monkeypatch.delenv("VT_STT_MODEL", raising=False)
    called = []

    class Eng:
        id = "mlx_qwen3"
        label = "x"
        def supported(self, env): return True, ""
        def delete_model(self, spec): called.append(spec.id); return 1

    monkeypatch.setattr("stt_engines.get_engine", lambda eid: Eng())
    with pytest.raises(stt_service.SetupError):
        stt_service.delete_download("custom:mlx_qwen3:someone/unrelated-llm")
    assert called == []                                   # 무관한 리포의 캐시를 지우지 않았다


def test_delete_custom_recorded_by_app_is_allowed(monkeypatch, tmp_path):
    import vt_paths
    monkeypatch.setattr(vt_paths, "state_dir", lambda: tmp_path)
    monkeypatch.delenv("VT_STT_MODEL", raising=False)
    mid = "custom:mlx_qwen3:me/asr"
    stt_service._record_custom(mid)
    called = []

    class Eng:
        id = "mlx_qwen3"
        label = "x"
        def supported(self, env): return True, ""
        def delete_model(self, spec): called.append(spec.id); return 5

    monkeypatch.setattr("stt_engines.get_engine", lambda eid: Eng())
    assert stt_service.delete_download(mid) == 5 and called == [mid]


def _engine_with_pidfile(monkeypatch, tmp_path, pid_text):
    monkeypatch.setattr(lc, "_pid_path", lambda: tmp_path / "p.pid")
    (tmp_path / "p.pid").write_text(pid_text)
    return lc.LlamaCppQwen3Engine()


def _fake_ps(monkeypatch, command):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=command, stderr=""))


def test_orphan_not_killed_when_command_is_a_different_binary(monkeypatch, tmp_path):
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    eng = _engine_with_pidfile(monkeypatch, tmp_path, "4242\n/our/engines/llama-server")
    _fake_ps(monkeypatch, "/usr/local/bin/llama-server -m other.gguf --api-key secret")   # 사용자가 따로 띄운 것
    eng._kill_orphan()
    assert killed == []


def test_orphan_killed_when_command_matches_recorded_binary(monkeypatch, tmp_path):
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    eng = _engine_with_pidfile(monkeypatch, tmp_path, "4242\n/our/engines/llama-server")
    _fake_ps(monkeypatch, "/our/engines/llama-server -m a.gguf --api-key k")
    eng._kill_orphan()
    assert killed == [4242]
