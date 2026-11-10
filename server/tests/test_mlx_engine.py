"""MLX 엔진 — 가짜 mlx_qwen3_asr 모듈 주입으로 load/transcribe/unload 검증."""
import hashlib
import importlib.machinery
import os
import sys
import types

import pytest

from stt_engines import catalog, hfcache, pins
from stt_engines import mlx_qwen3 as mod
from stt_engines.base import NotReady
from stt_engines.env import HostEnv
from stt_engines.mlx_qwen3 import MlxQwen3Engine

SPEC = catalog.get("qwen3-asr-0.6b-mlx")


_FILES = {"model.safetensors": b"w", "config.json": b"{}"}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    h = tmp_path / "hub"
    monkeypatch.setattr(hfcache, "cache_dir", lambda: h)
    # 카탈로그 모델은 고정(pins) 표의 파일 목록·크기·해시와 맞아야 쓴다. 검증 로직은 그대로 두고
    # 표에 가짜 모델의 항목(아래 _snapshot 이 만드는 파일)을 주입한다 — 실제 해시 검증을 통과해야 한다.
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setitem(pins.PINS, SPEC.repo, pins.Pin(
        SPEC.repo, "f" * 40,
        {n: (len(d), hashlib.sha256(d).hexdigest()) for n, d in _FILES.items()}))
    return h


@pytest.fixture
def fake_pkg(monkeypatch):
    calls = []
    seen_files = []

    def transcribe(path, model=None, language=None):
        calls.append({"path": path, "model": model, "language": language})
        with open(path, "rb") as f:
            seen_files.append(f.read())
        return types.SimpleNamespace(text="  안녕하세요 \n")
    m = types.ModuleType("mlx_qwen3_asr")
    m.transcribe = transcribe
    m.__spec__ = importlib.machinery.ModuleSpec("mlx_qwen3_asr", None)
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", m)
    return types.SimpleNamespace(calls=calls, files=seen_files)


@pytest.fixture
def eng():
    e = MlxQwen3Engine()
    yield e
    e.unload()


def _snapshot(hub, config=True):
    snap = hfcache.repo_dir(SPEC.repo, hub) / "snapshots" / "rev"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(_FILES["model.safetensors"])
    if config:
        (snap / "config.json").write_bytes(_FILES["config.json"])
    return snap


@pytest.mark.parametrize("env,ok", [
    (HostEnv("macos", "arm64", (26, 0)), True),
    (HostEnv("macos", "arm64", (13, 0)), True),
    (HostEnv("macos", "x86_64", (14, 0)), False),
    (HostEnv("linux", "arm64", None), False),
    (HostEnv("linux", "x86_64", None, wsl2=True), False),
    (HostEnv("other", "arm64", None), False),
])
def test_supported(eng, env, ok):
    r, why = eng.supported(env)
    assert r is ok and (why == "") == ok


def test_requirements_follow_find_spec(eng, monkeypatch):
    he = HostEnv("macos", "arm64", (26, 0))
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda n: None)
    r = eng.requirements(he)[0]
    assert (r.id, r.kind, r.satisfied, r.can_install) == ("mlx-qwen3-asr", "pip", False, False)
    assert "pip install mlx-qwen3-asr" in r.hint and sys.executable in r.hint
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda n: object())
    r = eng.requirements(he)[0]
    assert r.satisfied and r.hint == ""


def test_install_requirement_not_automatic(eng):
    with pytest.raises(NotReady):
        eng.install_requirement("mlx-qwen3-asr", lambda *_: None)


def test_load_without_package(eng, hub, monkeypatch):
    _snapshot(hub)
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", None)      # import 불가 → find_spec ValueError 대신 차단
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda n: None)
    with pytest.raises(NotReady, match="pip install|설치"):
        eng.load(SPEC)
    assert eng.loaded() is None


def test_load_no_snapshot(eng, hub, fake_pkg):
    with pytest.raises(NotReady, match="받아 주세요"):
        eng.load(SPEC)
    assert not eng.model_present(SPEC) and eng.loaded() is None


def test_load_incomplete_snapshot_without_config(eng, hub, fake_pkg):
    _snapshot(hub, config=False)
    with pytest.raises(NotReady, match="받아 주세요"):
        eng.load(SPEC)
    assert not eng.model_present(SPEC)


def test_load_ok(eng, hub, fake_pkg):
    snap = _snapshot(hub)
    assert eng.model_present(SPEC)
    eng.load(SPEC)
    assert eng.loaded() == SPEC.id and eng._model_path == str(snap)


def test_load_idempotent(eng, hub, fake_pkg):
    _snapshot(hub)
    eng.load(SPEC)
    fn = eng._transcribe_fn
    eng.load(SPEC)
    assert eng._transcribe_fn is fn


def test_transcribe_args_and_text(eng, hub, fake_pkg):
    snap = _snapshot(hub)
    eng.load(SPEC)
    assert eng.transcribe(b"RIFFdata", "ko") == "안녕하세요"
    c = fake_pkg.calls[0]
    assert c["model"] == str(snap) and c["language"] == "Korean"
    assert fake_pkg.files[0] == b"RIFFdata"


@pytest.mark.parametrize("lang,want", [("ko", "Korean"), ("KO", "Korean"), ("en", "English"), ("ja", "Japanese"),
                                       ("zh", "Chinese"), (None, None), ("", None), ("xx", None)])
def test_language_mapping(eng, hub, fake_pkg, lang, want):
    _snapshot(hub)
    eng.load(SPEC)
    eng.transcribe(b"x", lang)
    assert fake_pkg.calls[-1]["language"] == want


def test_temp_wav_deleted(eng, hub, fake_pkg):
    _snapshot(hub)
    eng.load(SPEC)
    eng.transcribe(b"x", "ko")
    eng.transcribe(b"y", None)
    paths = [c["path"] for c in fake_pkg.calls]
    assert len(set(paths)) == 2
    assert all(p.endswith(".wav") and not os.path.exists(p) for p in paths)


def test_temp_wav_deleted_on_error(eng, hub, monkeypatch, fake_pkg):
    _snapshot(hub)
    eng.load(SPEC)
    seen = []

    def boom(path, **k):
        seen.append(path)
        raise RuntimeError("decode failed")
    eng._transcribe_fn = boom
    with pytest.raises(RuntimeError):
        eng.transcribe(b"x", "ko")
    assert seen and not os.path.exists(seen[0])


def test_transcribe_not_loaded(eng):
    with pytest.raises(NotReady):
        eng.transcribe(b"x", "ko")


def test_unload(eng, hub, fake_pkg):
    _snapshot(hub)
    eng.load(SPEC)
    eng.unload()
    assert eng.loaded() is None
    with pytest.raises(NotReady):
        eng.transcribe(b"x", "ko")
    eng.unload()                                  # 두 번 불러도 안전


def test_delete_model_unloads_and_removes(eng, hub, fake_pkg):
    snap = _snapshot(hub)
    eng.load(SPEC)
    freed = eng.delete_model(SPEC)
    assert eng.loaded() is None and not snap.exists() and freed >= 0
    assert not eng.model_present(SPEC)


def test_rss_mb_nonnegative(eng):
    assert eng.rss_mb() >= 0
