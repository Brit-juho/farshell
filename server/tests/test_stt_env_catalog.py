"""STT 환경 감지(env)와 모델 카탈로그(catalog) 검증."""
import io

import pytest

import stt_engines
from stt_engines import catalog, env
from stt_engines.env import HostEnv


@pytest.fixture(autouse=True)
def _reset_env():
    env.override(None)
    yield
    env.override(None)


def _patch(monkeypatch, system, machine, mac_ver="", proc_version=None, meminfo=None):
    monkeypatch.setattr(env.platform, "system", lambda: system)
    monkeypatch.setattr(env.platform, "machine", lambda: machine)
    monkeypatch.setattr(env.platform, "mac_ver", lambda: (mac_ver, ("", "", ""), machine))
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/proc/version":
            if proc_version is None:
                raise FileNotFoundError(path)
            return io.StringIO(proc_version)
        if path == "/proc/meminfo":
            if meminfo is None:
                raise FileNotFoundError(path)
            return io.StringIO(meminfo)
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", fake_open)

    class _R:
        stdout = str(16 * 1024 ** 3) + "\n"
    monkeypatch.setattr(env.subprocess, "run", lambda *a, **k: _R())


def test_detect_macos_arm64(monkeypatch):
    _patch(monkeypatch, "Darwin", "arm64", "26.0.1")
    e = env.detect(refresh=True)
    assert (e.os, e.arch, e.key) == ("macos", "arm64", "macos-arm64")
    assert e.macos_version == (26, 0) and not e.wsl2 and e.ram_mb == 16384
    assert e.label == "macOS 26.0 (Apple Silicon)"


def test_detect_macos_x86(monkeypatch):
    _patch(monkeypatch, "Darwin", "x86_64", "14.5")
    e = env.detect(refresh=True)
    assert e.key == "macos-x86_64" and e.macos_version == (14, 5)
    assert e.label == "macOS 14.5 (Intel)"


def test_detect_linux_x86(monkeypatch):
    _patch(monkeypatch, "Linux", "x86_64", proc_version="Linux version 6.5 (gcc)",
           meminfo="MemTotal:       8388608 kB\nMemFree: 1 kB\n")
    e = env.detect(refresh=True)
    assert e.key == "linux-x86_64" and not e.wsl2 and e.macos_version is None
    assert e.ram_mb == 8192 and e.label == "Linux x86_64"


def test_detect_linux_arm64_aarch64(monkeypatch):
    _patch(monkeypatch, "Linux", "aarch64", proc_version="Linux version 6.5")
    e = env.detect(refresh=True)
    assert e.key == "linux-arm64" and not e.wsl2


def test_detect_wsl2(monkeypatch):
    _patch(monkeypatch, "Linux", "AMD64", proc_version="Linux version 5.15.90.1-microsoft-standard-WSL2")
    e = env.detect(refresh=True)
    assert e.os == "linux" and e.arch == "x86_64" and e.wsl2
    assert e.label == "WSL2 x86_64"
    assert e.to_dict()["wsl2"] is True


def test_detect_other(monkeypatch):
    _patch(monkeypatch, "Windows", "AMD64")
    e = env.detect(refresh=True)
    assert e.os == "other" and e.key == "other-x86_64" and e.ram_mb is None
    assert e.label == "other x86_64" and not e.wsl2 and e.macos_version is None


def test_detect_unknown_arch(monkeypatch):
    _patch(monkeypatch, "FreeBSD", "")
    assert env.detect(refresh=True).arch == "unknown"


def test_detect_caches_and_refresh(monkeypatch):
    _patch(monkeypatch, "Darwin", "arm64", "26.0")
    first = env.detect(refresh=True)
    _patch(monkeypatch, "Linux", "x86_64", proc_version="x")
    assert env.detect() is first
    assert env.detect(refresh=True).os == "linux"


def test_override(monkeypatch):
    forced = HostEnv(os="linux", arch="arm64", macos_version=None)
    env.override(forced)
    assert env.detect() is forced
    _patch(monkeypatch, "Darwin", "arm64", "26.0")
    env.override(None)
    assert env.detect().os == "macos"


def test_ram_failure_is_none(monkeypatch):
    _patch(monkeypatch, "Linux", "x86_64", proc_version="x", meminfo=None)
    assert env.detect(refresh=True).ram_mb is None


def test_to_dict_fields():
    d = HostEnv(os="macos", arch="arm64", macos_version=(26, 1), ram_mb=8).to_dict()
    assert d == {"os": "macos", "arch": "arm64", "key": "macos-arm64", "label": "macOS 26.1 (Apple Silicon)",
                 "macos_version": [26, 1], "wsl2": False, "ram_mb": 8}
    assert HostEnv(os="linux", arch="x86_64", macos_version=None).to_dict()["macos_version"] is None


# ── 카탈로그 ───────────────────────────────────────────────────────
ENVS = [
    HostEnv("macos", "arm64", (26, 0)),
    HostEnv("macos", "x86_64", (14, 0)),
    HostEnv("linux", "x86_64", None),
    HostEnv("linux", "arm64", None),
    HostEnv("linux", "x86_64", None, wsl2=True),
    HostEnv("other", "x86_64", None),
]


def test_ids_unique():
    ids = [m.id for m in catalog.CATALOG]
    assert len(ids) == len(set(ids))


def test_engines_registered():
    for m in catalog.CATALOG:
        assert m.engine in stt_engines._ENGINE_CLASSES, m.id


def test_get():
    assert catalog.get("qwen3-asr-0.6b-mlx").engine == "mlx_qwen3"
    assert catalog.get("nope") is None


def test_recommended_values():
    assert catalog.recommended_id(ENVS[0]) == "qwen3-asr-0.6b-mlx"
    for e in ENVS[1:]:
        assert catalog.recommended_id(e) == "qwen3-asr-0.6b-gguf"


@pytest.mark.parametrize("e", ENVS, ids=lambda e: e.key + ("-wsl2" if e.wsl2 else ""))
def test_recommended_engine_supported(e):
    """권장 모델의 엔진은 해당 환경에서 supported 여야 한다(없는 환경은 llama.cpp 배포본이 없음)."""
    spec = catalog.get(catalog.recommended_id(e))
    ok, _ = stt_engines.get_engine(spec.engine).supported(e)
    if e.os in ("macos", "linux"):
        assert ok


def test_static_tags_never_recommended_and_in_set():
    allowed = {"고정확", "경량", "실험적"}
    for m in catalog.CATALOG:
        assert isinstance(m.static_tags, tuple)
        assert set(m.static_tags) <= allowed, m.id
        assert "권장" not in m.static_tags


def test_spec_basic_fields_and_to_dict():
    for m in catalog.CATALOG:
        assert m.label and m.tagline and isinstance(m.size_mb, int)
        d = m.to_dict()
        assert d["id"] == m.id and isinstance(d["static_tags"], list) and isinstance(d["notes"], list)
        if m.engine != "apple_speech":
            assert m.repo, m.id
        if m.engine == "llamacpp_qwen3":
            assert len(m.files) == 2 and any(f.startswith("mmproj") for f in m.files)
