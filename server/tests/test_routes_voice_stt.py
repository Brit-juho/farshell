"""routes/voice.py 의 /voice/stt/* — 가짜 엔진으로 REST 계약(상태코드·필드)과 「몰래 받지 않음」을 검증한다."""
import asyncio
import json

import pytest
from fastapi.responses import JSONResponse

import stt_engines
import stt_service
import vt_paths
import workspace
from routes import voice
from stt_engines import env as env_mod
from stt_engines.setup_jobs import SetupManager
from stt_fakes import LINUX_X86, MAC_ARM, make_engines


class _Req:
    def __init__(self, body=None, raw=None):
        self._body, self._raw = body, raw

    async def json(self):
        if self._raw is not None:
            raise ValueError("bad json")
        return self._body


@pytest.fixture(autouse=True)
def fx(monkeypatch, tmp_path):
    calls = []
    engines = make_engines(calls)
    monkeypatch.setattr(stt_engines, "_instances", dict(engines))
    env_mod.override(MAC_ARM)
    monkeypatch.delenv("VT_STT_MODEL", raising=False)
    monkeypatch.setattr(workspace, "WS_PATH", tmp_path / "ws.json")
    monkeypatch.setattr(vt_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(stt_service, "_setup", SetupManager())
    monkeypatch.setattr(stt_service, "_waiting", 0)
    monkeypatch.setattr(stt_service, "_last_error", "")
    monkeypatch.setattr(stt_service, "_last_timing", None)
    # 받기를 동기로 돌려 결과를 바로 본다(라우트는 start_setup(model, step)만 부른다)
    real = stt_service.start_setup
    monkeypatch.setattr(stt_service, "start_setup", lambda m, s, blocking=False: real(m, s, blocking=True))
    yield type("F", (), {"calls": calls, "engines": engines, "tmp": tmp_path, "real_setup": real})
    env_mod.override(None)


def run(c):
    return asyncio.run(c)


def downloads(calls):
    return [c for c in calls if c[1] in ("download_model", "install_requirement")]


def body(r):
    return json.loads(r.body)


# ── catalog / status ───────────────────────────────────────────────

def test_catalog_route():
    d = run(voice.stt_catalog())
    assert d["recommended"] == "qwen3-asr-0.6b-mlx" and d["selected"] == "qwen3-asr-0.6b-mlx"
    assert d["models"][0]["id"] == d["recommended"] and d["unsupported"] == [] and d["setup"] is None
    assert {"env", "selected_valid", "legacy_value", "engines", "models", "unsupported"} <= set(d)
    env_mod.override(LINUX_X86)
    d = run(voice.stt_catalog())
    assert d["recommended"] == "qwen3-asr-0.6b-gguf" and {m["engine"] for m in d["unsupported"]} == {"mlx_qwen3", "apple_speech"}


def test_catalog_route_custom_query(fx):
    cid = "custom:llamacpp_qwen3:me/my-model"
    assert not any(m["custom"] for m in run(voice.stt_catalog())["models"])
    d = run(voice.stt_catalog(custom=cid))
    assert [m["id"] for m in d["models"] if m["custom"]] == [cid]
    assert not any(m["custom"] for m in run(voice.stt_catalog(custom="custom:mlx_qwen3:../x/y"))["models"])
    assert not any(m["custom"] for m in run(voice.stt_catalog(custom="https://evil/x"))["models"])


def test_catalog_route_legacy_setting(fx):
    (fx.tmp / "ws.json").write_text(json.dumps({"settings": {"voice.sttModel": "small"}}))
    d = run(voice.stt_catalog())
    assert d["selected"] is None and d["selected_valid"] is False and d["legacy_value"] == "small"


def test_status_route(fx):
    st = run(voice.stt_status())
    assert st["available"] is True and st["loaded"] is False and st["engine"] is None
    assert st["model"] == "qwen3-asr-0.6b-mlx" and st["setup"] is None and st["last_error"] is None and st["timing"] is None
    assert downloads(fx.calls) == [] and fx.calls == []                  # 상태 조회는 엔진을 건드리지 않는다


# ── setup ──────────────────────────────────────────────────────────

def test_setup_route_runs_download(fx):
    r = run(voice.stt_setup(_Req({"model": "qwen3-asr-0.6b-mlx", "step": "model"})))
    assert r["state"] == "done" and r["steps"][0]["id"] == "model"
    assert downloads(fx.calls) == [("mlx_qwen3", "download_model", "qwen3-asr-0.6b-mlx")]
    assert run(voice.stt_status())["setup"]["state"] == "done"


@pytest.mark.parametrize("req", [
    _Req(raw=True), _Req(["x"]), _Req(None),
    _Req({"model": "whisper-base", "step": "model"}), _Req({"model": "qwen3-asr-0.6b-mlx", "step": "x"}),
    _Req({"model": "qwen3-asr-0.6b-mlx"}), _Req({}), _Req({"model": "../x", "step": "model"}),
    _Req({"model": "custom:mlx_qwen3:/etc/passwd", "step": "model"}),
])
def test_setup_route_400(fx, req):
    r = run(voice.stt_setup(req))
    assert isinstance(r, JSONResponse) and r.status_code == 400 and body(r)["error"]
    assert downloads(fx.calls) == []


def test_setup_route_unsupported_engine_400(fx):
    env_mod.override(LINUX_X86)
    r = run(voice.stt_setup(_Req({"model": "qwen3-asr-0.6b-mlx", "step": "model"})))
    assert r.status_code == 400 and "linux-x86_64" in body(r)["error"] and downloads(fx.calls) == []


def test_setup_route_pip_requirement_400_with_hint(fx):
    from stt_engines.base import Requirement
    fx.engines["mlx_qwen3"].reqs = [Requirement("pkg", "패키지", "pip", False, "pip install mlx-qwen3-asr")]
    r = run(voice.stt_setup(_Req({"model": "qwen3-asr-0.6b-mlx", "step": "req:pkg"})))
    assert r.status_code == 400 and "pip install mlx-qwen3-asr" in body(r)["error"] and downloads(fx.calls) == []


def test_setup_route_409_when_busy(fx, monkeypatch):
    import threading
    gate = threading.Event()
    e = fx.engines["mlx_qwen3"]
    orig = e.download_model
    e.download_model = lambda spec, p: (gate.wait(5), orig(spec, p))
    monkeypatch.setattr(stt_service, "start_setup", fx.real_setup)       # 실제(백그라운드)로
    try:
        first = run(voice.stt_setup(_Req({"model": "qwen3-asr-0.6b-mlx", "step": "model"})))
        assert first["state"] == "running"
        r = run(voice.stt_setup(_Req({"model": "qwen3-asr-1.7b-mlx", "step": "model"})))
        assert isinstance(r, JSONResponse) and r.status_code == 409 and body(r)["error"]
    finally:
        gate.set()


# ── preload / unload ───────────────────────────────────────────────

def test_preload_without_model_returns_error_and_never_downloads(fx):
    r = run(voice.stt_preload())
    assert r["loaded"] is False and r["engine"] is None and "받지 않았습니다" in r["error"]
    assert downloads(fx.calls) == []
    assert run(voice.stt_status())["last_error"] == r["error"]


def test_preload_and_unload(fx):
    fx.engines["mlx_qwen3"].present.add("qwen3-asr-0.6b-mlx")
    r = run(voice.stt_preload())
    assert r == {"loaded": True, "engine": "mlx_qwen3"}
    st = run(voice.stt_status())
    assert st["loaded"] and st["loaded_model"] == "qwen3-asr-0.6b-mlx" and st["engine"] == "mlx_qwen3"
    assert run(voice.stt_unload()) == {"unloaded": True, "loaded": False}
    assert run(voice.stt_unload()) == {"unloaded": False, "loaded": False}


# ── model 목록·삭제 ────────────────────────────────────────────────

def test_model_list_and_delete(fx, monkeypatch):
    monkeypatch.setattr(stt_service, "_model_bytes", lambda spec, eng: 2048)
    fx.engines["mlx_qwen3"].present.add("qwen3-asr-0.6b-mlx")
    d = run(voice.stt_model_list())
    assert d["models"] == [{"model_id": "qwen3-asr-0.6b-mlx", "label": "Qwen3-ASR 0.6B (MLX)", "engine": "mlx_qwen3",
                            "size_bytes": 2048, "selected": True}] and d["binaries"] == []
    run(voice.stt_preload())
    r = run(voice.stt_model_delete(_Req({"model_id": "qwen3-asr-0.6b-mlx"})))
    assert r == {"deleted": True, "freed_bytes": 123}
    assert ("mlx_qwen3", "unload") in fx.calls and not stt_service.loaded()
    assert run(voice.stt_model_list())["models"] == []


def test_delete_binary(fx):
    import stt_engines.llama_release as lr
    root = lr.install_root()
    root.mkdir(parents=True)
    (root / "x").write_bytes(b"y" * 5)
    assert run(voice.stt_model_list())["binaries"][0]["id"] == "llama-server"
    assert run(voice.stt_model_delete(_Req({"binary": "llama-server"}))) == {"deleted": True, "freed_bytes": 5}
    assert not root.exists()


@pytest.mark.parametrize("req", [
    _Req(raw=True), _Req({}), _Req({"model_id": "a", "binary": "llama-server"}),
    _Req({"binary": "rm -rf"}), _Req({"model_id": "nope"}), _Req({"model_id": "custom:mlx_qwen3:../a/b"}),
    _Req(None), _Req([]),
])
def test_delete_route_400(req):
    r = run(voice.stt_model_delete(req))
    assert isinstance(r, JSONResponse) and r.status_code == 400 and body(r)["error"]
