"""stt_service — 선택 해석·카탈로그·직렬화 실행·준비(받기)·관리. 가짜 엔진으로 검증한다."""
import asyncio
import json
import threading
import time

import pytest

import stt_engines
import stt_service
import vt_paths
import workspace
from stt_engines import catalog, env as env_mod
from stt_engines.base import NotReady
from stt_engines.setup_jobs import SetupBusy, SetupManager
from stt_fakes import LINUX_X86, MAC_ARM, MAC_X86, make_engines


@pytest.fixture(autouse=True)
def fx(monkeypatch, tmp_path):
    calls = []
    engines = make_engines(calls)
    monkeypatch.setattr(stt_engines, "_instances", dict(engines))
    env_mod.override(MAC_ARM)
    monkeypatch.delenv("VT_STT_MODEL", raising=False)
    monkeypatch.delenv("VT_STT_IDLE_SEC", raising=False)
    monkeypatch.setattr(workspace, "WS_PATH", tmp_path / "ws.json")
    monkeypatch.setattr(vt_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(stt_service, "_setup", SetupManager())
    monkeypatch.setattr(stt_service, "_waiting", 0)
    monkeypatch.setattr(stt_service, "_last_error", "")
    monkeypatch.setattr(stt_service, "_last_timing", None)
    monkeypatch.setattr(stt_service, "_last_use", 0.0)
    yield type("F", (), {"calls": calls, "engines": engines, "tmp": tmp_path})
    env_mod.override(None)


def set_ws(tmp, value):
    (tmp / "ws.json").write_text(json.dumps({"settings": {"voice.sttModel": value}}), encoding="utf-8")


def downloads(calls):
    return [c for c in calls if c[1] in ("download_model", "install_requirement")]


# ── selection ──────────────────────────────────────────────────────

def test_selection_default_is_recommended_per_env():
    s = stt_service.selection()
    assert (s.model_id, s.is_default, s.valid, s.legacy) == ("qwen3-asr-0.6b-mlx", True, True, None)
    env_mod.override(LINUX_X86)
    assert stt_service.selection().model_id == "qwen3-asr-0.6b-gguf"


def test_selection_env_var_wins_over_workspace(fx, monkeypatch):
    set_ws(fx.tmp, "qwen3-asr-1.7b-mlx")
    assert stt_service.selection().model_id == "qwen3-asr-1.7b-mlx"
    monkeypatch.setenv("VT_STT_MODEL", "qwen3-asr-0.6b-gguf")
    s = stt_service.selection()
    assert s.model_id == "qwen3-asr-0.6b-gguf" and not s.is_default and s.valid


@pytest.mark.parametrize("old", ["base", "small", "Systran/faster-whisper-small"])
def test_selection_legacy_values_invalid(fx, old):
    set_ws(fx.tmp, old)
    s = stt_service.selection()
    assert (s.valid, s.legacy, s.model_id, s.spec) == (False, old, None, None)


def test_selection_model_of_unusable_engine_is_invalid(fx):
    set_ws(fx.tmp, "qwen3-asr-0.6b-mlx")
    env_mod.override(LINUX_X86)
    s = stt_service.selection()
    assert not s.valid and s.legacy == "qwen3-asr-0.6b-mlx"


@pytest.mark.parametrize("cid,ok", [
    ("custom:mlx_qwen3:owner/repo", True),
    ("custom:llamacpp_qwen3:Org-1/Model_x.v2", True),
    ("custom:mlx_qwen3:/etc/passwd", False),
    ("custom:mlx_qwen3:https://huggingface.co/a/b", False),
    ("custom:mlx_qwen3:../x/y", False),
    ("custom:mlx_qwen3:a/..", False),
    ("custom:mlx_qwen3:a/b/c", False),
    ("custom:mlx_qwen3:onlyrepo", False),
    ("custom:whisper:a/b", False),
    ("custom:apple_speech:a/b", False),
    ("custom:mlx_qwen3:a/" + "x" * 200, False),
    ("custom:mlx_qwen3:" + "x" * 200 + "/b", False),
    ("", False),
])
def test_parse_custom(cid, ok):
    spec = stt_service.parse_custom(cid)
    if ok:
        assert spec.custom and spec.id == cid and spec.engine == cid.split(":")[1]
        assert spec.repo == cid.split(":", 2)[2]
    else:
        assert spec is None


def test_selection_custom_valid(fx):
    set_ws(fx.tmp, "custom:llamacpp_qwen3:me/my-model")
    s = stt_service.selection()
    assert s.valid and s.spec.custom and s.model_id == "custom:llamacpp_qwen3:me/my-model"


def test_selection_custom_mlx_invalid_on_linux(fx):
    set_ws(fx.tmp, "custom:mlx_qwen3:me/m")
    env_mod.override(LINUX_X86)
    assert not stt_service.selection().valid


# ── catalog_payload ────────────────────────────────────────────────

@pytest.mark.parametrize("env,rec,usable,not_usable", [
    (MAC_ARM, "qwen3-asr-0.6b-mlx", {"mlx_qwen3", "llamacpp_qwen3", "apple_speech"}, set()),
    (LINUX_X86, "qwen3-asr-0.6b-gguf", {"llamacpp_qwen3"}, {"mlx_qwen3", "apple_speech"}),
    (MAC_X86, "qwen3-asr-0.6b-gguf", {"llamacpp_qwen3"}, {"mlx_qwen3", "apple_speech"}),
])
def test_catalog_one_recommended_and_unsupported(env, rec, usable, not_usable):
    env_mod.override(env)
    d = stt_service.catalog_payload()
    assert d["recommended"] == rec and d["env"]["key"] == env.key
    allm = d["models"] + d["unsupported"]
    assert [m["id"] for m in allm if "권장" in m["tags"]] == [rec]      # 권장은 정확히 1개
    assert d["models"][0]["id"] == rec
    assert {m["engine"] for m in d["models"]} == usable
    for m in d["models"]:
        assert m["supported"] and m["unsupported_reason"] == ""
    for m in d["unsupported"]:
        assert not m["supported"] and m["unsupported_reason"] and m["engine"] in not_usable
        assert "권장" not in m["tags"] and m["ready"] is False and m["requirements"] == []
    assert {e["id"]: e["usable"] for e in d["engines"]} == {
        "mlx_qwen3": "mlx_qwen3" in usable, "llamacpp_qwen3": True, "apple_speech": "apple_speech" in usable}
    assert all(e["reason"] for e in d["engines"] if not e["usable"])
    assert "static_tags" not in d["models"][0]


def test_catalog_old_macos_apple_unsupported_with_reason():
    env_mod.override(type(MAC_ARM)(os="macos", arch="arm64", macos_version=(15, 0)))
    d = stt_service.catalog_payload()
    ap = [m for m in d["unsupported"] if m["engine"] == "apple_speech"]
    assert ap and "26" in ap[0]["unsupported_reason"]


def test_catalog_selected_and_legacy(fx):
    d = stt_service.catalog_payload()
    assert d["selected"] == "qwen3-asr-0.6b-mlx" and d["selected_valid"] and d["legacy_value"] is None
    assert [m["id"] for m in d["models"] if m["selected"]] == ["qwen3-asr-0.6b-mlx"]
    set_ws(fx.tmp, "base")
    d = stt_service.catalog_payload()
    assert d["selected"] is None and d["selected_valid"] is False and d["legacy_value"] == "base"
    assert not any(m["selected"] for m in d["models"])


def test_catalog_requirements_present_ready(fx):
    from stt_engines.base import Requirement
    mlx = fx.engines["mlx_qwen3"]
    mlx.reqs = [Requirement("pkg", "패키지", "pip", False, "pip install x")]
    d = stt_service.catalog_payload()
    m = next(x for x in d["models"] if x["id"] == "qwen3-asr-0.6b-mlx")
    assert m["requirements"][0]["satisfied"] is False and not m["model_present"] and not m["ready"]
    mlx.present.add("qwen3-asr-0.6b-mlx")                     # 모델만 있고 패키지 없음 → ready 아님
    m = next(x for x in stt_service.catalog_payload()["models"] if x["id"] == "qwen3-asr-0.6b-mlx")
    assert m["model_present"] and not m["ready"]
    mlx.reqs = [Requirement("pkg", "패키지", "pip", True)]
    m = next(x for x in stt_service.catalog_payload()["models"] if x["id"] == "qwen3-asr-0.6b-mlx")
    assert m["ready"] is True and m["loaded"] is False
    mlx._loaded = "qwen3-asr-0.6b-mlx"
    m = next(x for x in stt_service.catalog_payload()["models"] if x["id"] == "qwen3-asr-0.6b-mlx")
    assert m["loaded"] is True


def test_catalog_custom_arg_appended(fx):
    cid = "custom:llamacpp_qwen3:me/my-model"
    d = stt_service.catalog_payload(cid)
    extra = [m for m in d["models"] if m["id"] == cid]
    assert len(extra) == 1 and extra[0]["custom"] and extra[0]["supported"] and extra[0]["tags"] == []
    fx.engines["llamacpp_qwen3"].present.add(cid)
    assert next(m for m in stt_service.catalog_payload(cid)["models"] if m["id"] == cid)["model_present"]
    assert not any(m["custom"] for m in stt_service.catalog_payload()["models"])
    assert not any(m["custom"] for m in stt_service.catalog_payload("custom:mlx_qwen3:../a/b")["models"])


def test_catalog_selected_custom_shown_without_arg(fx):
    cid = "custom:llamacpp_qwen3:me/my-model"
    set_ws(fx.tmp, cid)
    d = stt_service.catalog_payload()
    m = [x for x in d["models"] if x["id"] == cid]
    assert len(m) == 1 and m[0]["selected"]
    assert len([x for x in stt_service.catalog_payload(cid)["models"] if x["id"] == cid]) == 1   # 중복 없음


def test_catalog_custom_unsupported_engine_goes_nowhere():
    env_mod.override(LINUX_X86)
    d = stt_service.catalog_payload("custom:mlx_qwen3:me/m")
    cid = "custom:mlx_qwen3:me/m"
    # 서비스는 지원 안 되는 엔진의 custom도 supported=False 로 목록에 붙인다(숨기지 않고 사유를 보여준다)
    hit = [m for m in d["models"] + d["unsupported"] if m["id"] == cid]
    assert all(not m["supported"] and m["unsupported_reason"] for m in hit)


# ── transcribe_wav ─────────────────────────────────────────────────

def ready(fx, mid="qwen3-asr-0.6b-mlx", eng="mlx_qwen3"):
    fx.engines[eng].present.add(mid)
    return fx.engines[eng]


def test_transcribe_basic_and_timing_and_no_download(fx):
    e = ready(fx)
    text, t = stt_service.transcribe_wav(b"wav", "ko")
    assert text == "안녕하세요" and set(t) == {"wait_ms", "load_ms", "stt_ms"}
    assert stt_service.status_payload()["timing"] == {"last": t} and stt_service.last_error() == ""
    assert stt_service.loaded() and stt_service.loaded_model_id() == "qwen3-asr-0.6b-mlx"
    assert stt_service.loaded_engine_id() == "mlx_qwen3" and stt_service.rss_mb() == 77
    assert [c[1] for c in fx.calls].count("load") == 1
    stt_service.transcribe_wav(b"wav", None)
    assert [c[1] for c in fx.calls].count("load") == 1          # 이미 올라가 있으면 다시 안 올림
    assert downloads(fx.calls) == []


def test_transcribe_not_ready_propagates_and_never_downloads(fx):
    with pytest.raises(NotReady) as ei:
        stt_service.transcribe_wav(b"wav", None)
    assert "받지 않았습니다" in str(ei.value)
    assert stt_service.last_error() == str(ei.value)
    assert downloads(fx.calls) == []
    assert stt_service._waiting == 0                              # 실패해도 줄 카운터 복구
    assert stt_service.status_payload()["last_error"] == str(ei.value)
    ready(fx)
    stt_service.transcribe_wav(b"wav", None)
    assert stt_service.last_error() == ""


def test_transcribe_legacy_selection_not_ready(fx):
    set_ws(fx.tmp, "base")
    with pytest.raises(NotReady, match="base"):
        stt_service.transcribe_wav(b"wav", None)
    assert fx.calls == []


def test_transcribe_serialized_across_threads(fx):
    e = ready(fx)
    e.delay = 0.05
    errs, outs = [], []

    def run():
        try:
            outs.append(stt_service.transcribe_wav(b"w", None)[0])
        except Exception as ex:
            errs.append(ex)
    ts = [threading.Thread(target=run) for _ in range(stt_service.MAX_WAITING)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(outs) == stt_service.MAX_WAITING
    assert e.max_active == 1


def test_transcribe_busy_over_max_waiting(fx):
    import threading as th
    e = ready(fx)
    e.gate = th.Event()
    n = stt_service.MAX_WAITING
    results = []

    def run():
        try:
            results.append(stt_service.transcribe_wav(b"w", None)[0])
        except Exception as ex:
            results.append(ex)
    ts = [th.Thread(target=run) for _ in range(n)]
    [t.start() for t in ts]
    deadline = time.time() + 3
    while stt_service._waiting < n and time.time() < deadline:
        time.sleep(0.01)
    with pytest.raises(stt_service.Busy):
        stt_service.transcribe_wav(b"w", None)
    e.gate.set()
    [t.join() for t in ts]
    assert all(r == "안녕하세요" for r in results) and stt_service._waiting == 0
    assert stt_service.transcribe_wav(b"w", None)[0] == "안녕하세요"   # 풀린 뒤엔 다시 받는다


def test_engine_switch_unloads_previous(fx):
    a = ready(fx)
    b = ready(fx, "qwen3-asr-0.6b-gguf", "llamacpp_qwen3")
    stt_service.transcribe_wav(b"w", None)
    assert a.loaded() and not b.loaded()
    set_ws(fx.tmp, "qwen3-asr-0.6b-gguf")
    stt_service.transcribe_wav(b"w", None)
    assert b.loaded() == "qwen3-asr-0.6b-gguf" and a.loaded() is None
    assert ("mlx_qwen3", "unload") in fx.calls
    assert stt_service.loaded_engine_id() == "llamacpp_qwen3"


def test_model_switch_same_engine_reloads(fx):
    e = ready(fx)
    e.present.add("qwen3-asr-1.7b-mlx")
    stt_service.transcribe_wav(b"w", None)
    set_ws(fx.tmp, "qwen3-asr-1.7b-mlx")
    stt_service.transcribe_wav(b"w", None)
    assert e.loaded() == "qwen3-asr-1.7b-mlx"
    assert [c for c in fx.calls if c[1] == "load"] == [("mlx_qwen3", "load", "qwen3-asr-0.6b-mlx"),
                                                        ("mlx_qwen3", "load", "qwen3-asr-1.7b-mlx")]


# ── preload / unload / available ───────────────────────────────────

def test_preload_unload_flow(fx):
    assert stt_service.preload() is None and "받지 않았습니다" in stt_service.last_error()
    assert not stt_service.loaded() and downloads(fx.calls) == []
    ready(fx)
    assert stt_service.preload() == "mlx_qwen3" and stt_service.last_error() == ""
    assert stt_service.loaded()
    assert stt_service.unload() is True and not stt_service.loaded()
    assert stt_service.unload() is False
    assert stt_service.loaded_model_id() is None and stt_service.loaded_engine_id() is None
    assert stt_service.rss_mb() == 0


def test_preload_unexpected_error_recorded(fx):
    def boom(spec):
        raise OSError("디스크")
    fx.engines["mlx_qwen3"].load = boom
    assert stt_service.preload() is None and "OSError" in stt_service.last_error()


def test_available_per_env(fx):
    assert stt_service.available()
    for e in fx.engines.values():
        e.keys = ("none-0",)
    assert not stt_service.available()
    assert stt_service.status_payload()["available"] is False


def test_status_payload_shape(fx):
    ready(fx)
    stt_service.preload()
    stt_service.transcribe_wav(b"w", None)
    st = stt_service.status_payload()
    assert st["available"] and st["loaded"] and st["engine"] == "mlx_qwen3"
    assert st["model"] == st["loaded_model"] == "qwen3-asr-0.6b-mlx" and st["rss_mb"] == 77
    assert st["timing"]["last"]["stt_ms"] >= 0 and st["setup"] is None and st["last_error"] is None


# ── start_setup ────────────────────────────────────────────────────

def test_setup_rejects_bad_input(fx):
    with pytest.raises(stt_service.SetupError):
        stt_service.start_setup("whisper-base", "model", blocking=True)
    with pytest.raises(stt_service.SetupError):
        stt_service.start_setup("custom:mlx_qwen3:../a/b", "model", blocking=True)
    with pytest.raises(stt_service.SetupError, match="step"):
        stt_service.start_setup("qwen3-asr-0.6b-mlx", "bogus", blocking=True)
    with pytest.raises(stt_service.SetupError, match="없는 항목"):
        stt_service.start_setup("qwen3-asr-0.6b-mlx", "req:nope", blocking=True)
    env_mod.override(LINUX_X86)
    with pytest.raises(stt_service.SetupError, match="linux-x86_64"):
        stt_service.start_setup("qwen3-asr-0.6b-mlx", "model", blocking=True)
    assert downloads(fx.calls) == []


def test_setup_model_download_runs_as_job(fx):
    job = stt_service.start_setup("qwen3-asr-0.6b-mlx", "model", blocking=True)
    assert job["state"] == "done" and job["model"] == "qwen3-asr-0.6b-mlx" and job["error"] is None
    s = job["steps"][0]
    assert s["id"] == "model" and s["state"] == "done" and s["done"] == 3 and s["total"] == 6
    assert downloads(fx.calls) == [("mlx_qwen3", "download_model", "qwen3-asr-0.6b-mlx")]
    assert stt_service.status_payload()["setup"]["state"] == "done"
    assert stt_service.catalog_payload()["setup"]["state"] == "done"
    assert not (fx.tmp / "state").exists()                       # 카탈로그 모델은 매니페스트 불필요


def test_setup_model_already_present_is_instant_done(fx):
    ready(fx)
    job = stt_service.start_setup("qwen3-asr-0.6b-mlx", "model", blocking=True)
    assert job["state"] == "done" and job["steps"][0]["state"] == "done"
    assert downloads(fx.calls) == []


def test_setup_model_failure_reflected(fx):
    fx.engines["mlx_qwen3"].download_error = "네트워크 끊김"
    job = stt_service.start_setup("qwen3-asr-0.6b-mlx", "model", blocking=True)
    assert job["state"] == "error" and "네트워크 끊김" in job["error"] and job["steps"][0]["state"] == "error"
    assert stt_service.status_payload()["setup"]["error"] == job["error"]
    fx.engines["mlx_qwen3"].download_error = None
    ok = stt_service.start_setup("qwen3-asr-0.6b-mlx", "model", blocking=True)     # 실패 뒤 재시도 가능
    assert ok["state"] == "done"


def test_setup_requirements(fx):
    from stt_engines.base import Requirement
    fx.engines["llamacpp_qwen3"].reqs = [
        Requirement("llama-server", "llama.cpp", "binary", False, "받기", can_install=True, size_mb=50),
        Requirement("pkg", "패키지", "pip", False, "pip install foo", can_install=False),
        Requirement("sys", "시스템", "system", True),
    ]
    mid = "qwen3-asr-0.6b-gguf"
    job = stt_service.start_setup(mid, "req:llama-server", blocking=True)
    assert job["state"] == "done" and job["steps"][0]["id"] == "req:llama-server"
    assert ("llamacpp_qwen3", "install_requirement", "llama-server") in fx.calls
    with pytest.raises(stt_service.SetupError, match="pip install foo"):
        stt_service.start_setup(mid, "req:pkg", blocking=True)
    n = len(downloads(fx.calls))
    job = stt_service.start_setup(mid, "req:sys", blocking=True)
    assert job["state"] == "done" and len(downloads(fx.calls)) == n


def test_setup_busy_when_running(fx):
    gate = threading.Event()
    e = fx.engines["mlx_qwen3"]
    orig = e.download_model

    def slow(spec, progress):
        progress(1, 10)
        gate.wait(5)
        orig(spec, progress)
    e.download_model = slow
    job = stt_service.start_setup("qwen3-asr-0.6b-mlx", "model")           # 백그라운드
    assert job["state"] == "running"
    try:
        with pytest.raises(SetupBusy):
            stt_service.start_setup("qwen3-asr-1.7b-mlx", "model", blocking=True)
        deadline = time.time() + 3
        while stt_service.status_payload()["setup"]["steps"][0]["done"] < 1 and time.time() < deadline:
            time.sleep(0.01)
        st = stt_service.status_payload()["setup"]
        assert st["state"] == "running" and st["steps"][0]["done"] == 1 and st["steps"][0]["total"] == 10
    finally:
        gate.set()
    deadline = time.time() + 3
    while stt_service.status_payload()["setup"]["state"] == "running" and time.time() < deadline:
        time.sleep(0.01)
    assert stt_service.status_payload()["setup"]["state"] == "done"


def test_setup_custom_records_manifest(fx):
    cid = "custom:llamacpp_qwen3:me/my-model"
    job = stt_service.start_setup(cid, "model", blocking=True)
    assert job["state"] == "done"
    manifest = fx.tmp / "state" / "engines" / "custom_models.json"
    assert json.loads(manifest.read_text())["models"] == [cid]
    stt_service.start_setup(cid, "model", blocking=True)                   # 이미 있음 → 중복 기록 없음
    fx.engines["llamacpp_qwen3"].present.discard(cid)
    stt_service.start_setup(cid, "model", blocking=True)
    assert json.loads(manifest.read_text())["models"] == [cid]


def test_setup_custom_failure_not_recorded(fx):
    cid = "custom:llamacpp_qwen3:me/my-model"
    fx.engines["llamacpp_qwen3"].download_error = "x"
    assert stt_service.start_setup(cid, "model", blocking=True)["state"] == "error"
    assert not (fx.tmp / "state" / "engines" / "custom_models.json").exists()


# ── downloads / delete ─────────────────────────────────────────────

@pytest.fixture
def sizes(monkeypatch):
    monkeypatch.setattr(stt_service, "_model_bytes", lambda spec, eng: 1000 if spec.id != "apple-speech-ko" else 0)


def test_downloads_payload(fx, sizes, monkeypatch):
    ready(fx)
    fx.engines["llamacpp_qwen3"].present.add("qwen3-asr-1.7b-gguf")
    fx.engines["apple_speech"].present.add("apple-speech-ko")           # 크기 0 → 제외
    d = stt_service.downloads_payload()
    by = {m["model_id"]: m for m in d["models"]}
    assert set(by) == {"qwen3-asr-0.6b-mlx", "qwen3-asr-1.7b-gguf"}
    assert by["qwen3-asr-0.6b-mlx"]["selected"] is True and by["qwen3-asr-1.7b-gguf"]["selected"] is False
    assert by["qwen3-asr-0.6b-mlx"]["size_bytes"] == 1000 and by["qwen3-asr-0.6b-mlx"]["engine"] == "mlx_qwen3"
    assert d["binaries"] == []


def test_downloads_unsupported_engine_hidden(fx, sizes):
    ready(fx)
    env_mod.override(LINUX_X86)
    assert stt_service.downloads_payload()["models"] == []


def test_downloads_include_recorded_custom(fx, sizes):
    cid = "custom:llamacpp_qwen3:me/my-model"
    stt_service.start_setup(cid, "model", blocking=True)
    assert [m["model_id"] for m in stt_service.downloads_payload()["models"]] == [cid]


def test_downloads_binary_listed(fx, sizes):
    import stt_engines.llama_release as lr
    root = lr.install_root()
    root.mkdir(parents=True)
    (root / "llama-server").write_bytes(b"x" * 10)
    b = stt_service.downloads_payload()["binaries"]
    assert len(b) == 1 and b[0]["id"] == "llama-server" and b[0]["size_bytes"] == 10 and b[0]["path"] == str(root)


def test_delete_model_even_if_selected_unloads_engine(fx):
    e = ready(fx)
    stt_service.preload()
    assert e.loaded()
    assert stt_service.delete_download(model_id="qwen3-asr-0.6b-mlx") == 123      # 선택·로드 중이어도 허용
    assert ("mlx_qwen3", "unload") in fx.calls and not e.loaded()
    assert ("mlx_qwen3", "delete_model", "qwen3-asr-0.6b-mlx") in fx.calls
    assert not stt_service.loaded()


def test_delete_binary_unloads_llama_and_removes(fx):
    import stt_engines.llama_release as lr
    root = lr.install_root()
    root.mkdir(parents=True)
    (root / "llama-server").write_bytes(b"x" * 10)
    ready(fx, "qwen3-asr-0.6b-gguf", "llamacpp_qwen3")
    set_ws(fx.tmp, "qwen3-asr-0.6b-gguf")
    stt_service.preload()
    assert stt_service.delete_download(binary="llama-server") == 10
    assert ("llamacpp_qwen3", "unload") in fx.calls and not root.exists()
    assert stt_service.delete_download(binary="llama-server") == 0


@pytest.mark.parametrize("kw", [
    {}, {"model_id": "a", "binary": "llama-server"}, {"binary": "other"},
    {"model_id": "whisper-base"}, {"model_id": "custom:mlx_qwen3:../a/b"},
])
def test_delete_bad_args(fx, kw):
    with pytest.raises(stt_service.SetupError):
        stt_service.delete_download(**kw)


# ── idle_monitor ───────────────────────────────────────────────────

def _run_idle(monkeypatch, secs):
    real_sleep = asyncio.sleep

    async def fast(_):
        await real_sleep(0.01)
    monkeypatch.setattr(asyncio, "sleep", fast)

    async def go():
        t = asyncio.create_task(stt_service.idle_monitor())
        await real_sleep(secs)
        done = t.done()
        t.cancel()
        return done
    return asyncio.run(go())


def test_idle_monitor_unloads_after_idle(fx, monkeypatch):
    e = ready(fx)
    monkeypatch.setenv("VT_STT_IDLE_SEC", "0.05")
    stt_service.preload()
    assert e.loaded()
    _run_idle(monkeypatch, 0.4)
    assert not e.loaded()


def test_idle_monitor_keeps_recently_used(fx, monkeypatch):
    e = ready(fx)
    monkeypatch.setenv("VT_STT_IDLE_SEC", "100")
    stt_service.preload()
    _run_idle(monkeypatch, 0.2)
    assert e.loaded()


def test_idle_monitor_zero_exits_immediately(fx, monkeypatch):
    ready(fx)
    monkeypatch.setenv("VT_STT_IDLE_SEC", "0")
    stt_service.preload()
    assert _run_idle(monkeypatch, 0.1) is True


def test_idle_monitor_bad_value_defaults_600(fx, monkeypatch):
    e = ready(fx)
    monkeypatch.setenv("VT_STT_IDLE_SEC", "abc")
    stt_service.preload()
    assert _run_idle(monkeypatch, 0.1) is False and e.loaded()
