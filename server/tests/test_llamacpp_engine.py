"""llama.cpp 엔진 — 가짜 llama-server(실제 서브프로세스)로 수명주기·보안·파싱 검증."""
import base64
import json
import os
import signal
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request

import pytest

import vt_paths
from stt_engines import catalog, hfcache
from stt_engines import llamacpp_qwen3 as mod
from stt_engines.base import NotReady
from stt_engines.llamacpp_qwen3 import LlamaCppQwen3Engine, _pick_gguf, parse_asr_text

SPEC = catalog.get("qwen3-asr-0.6b-gguf")
KEY_RESPONSE = "language Korean<asr_text>안녕하세요"

FAKE_SERVER = r'''#!__PY__
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

argv = sys.argv[1:]
def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None
port, key, host = int(opt("--port")), opt("--api-key"), opt("--host")
here = os.path.dirname(os.path.abspath(__file__))
mode = os.environ.get("FAKE_MODE", "")
if mode == "die":
    print("fake: model load failed: boom", file=sys.stderr, flush=True)
    sys.exit(3)
reply = os.environ.get("FAKE_REPLY", "language Korean<asr_text>안녕하세요")
with open(os.path.join(here, "args.json"), "w") as f:
    json.dump({"argv": argv, "m": opt("-m"), "mmproj": opt("--mmproj"), "host": host, "pid": os.getpid()}, f)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health":
            if mode == "nohealth":
                return self._send(503, {"status": "loading"})
            return self._send(200, {"status": "ok"})
        self._send(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n)
        if self.headers.get("Authorization") != "Bearer " + key:
            return self._send(401, {"error": "unauthorized"})
        with open(os.path.join(here, "last_request.json"), "wb") as f:
            f.write(body)
        self._send(200, {"choices": [{"message": {"content": reply}}]})

HTTPServer((host or "127.0.0.1", port), H).serve_forever()
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(vt_paths, "state_dir", lambda: state)
    hub = tmp_path / "hub"
    monkeypatch.setattr(hfcache, "cache_dir", lambda: hub)
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    exe = fake_dir / "llama-server"
    exe.write_text(FAKE_SERVER.replace("__PY__", sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("VT_LLAMA_SERVER", str(exe))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_REPLY", raising=False)
    # 서버 기동 대기를 짧게
    monkeypatch.setattr(mod, "_START_TIMEOUT", 15)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: time.sleep(0.05)))
    return types.SimpleNamespace(state=state, hub=hub, fake_dir=fake_dir, exe=exe, tmp=tmp_path)


def _put_model(hub, spec=SPEC, names=None):
    snap = hfcache.repo_dir(spec.repo, hub) / "snapshots" / "rev1"
    snap.mkdir(parents=True, exist_ok=True)
    for n in names or spec.files:
        (snap / n).write_bytes(b"GGUF" + n.encode())
    return snap


@pytest.fixture
def eng():
    e = LlamaCppQwen3Engine()
    yield e
    e.unload()


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        # 좀비는 죽은 것으로 본다
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(st) and not st.startswith("Z")
    except OSError:
        return True


def _wait_dead(pid, t=5):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _args(env):
    return json.loads((env.fake_dir / "args.json").read_text())


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("네트워크 호출 금지")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom, raising=False)


# ── 순수 함수 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("language Korean<asr_text>안녕하세요", "안녕하세요"),
    ("  language English<asr_text>  hello world \n", "hello world"),
    ("그냥 본문", "그냥 본문"),
    ("", ""),
    (None, ""),
    ("language None<asr_text>", ""),
    ("a<asr_text>b<asr_text>c", "b<asr_text>c"),
])
def test_parse_asr_text(raw, want):
    assert parse_asr_text(raw) == want


def test_pick_gguf_prefers_q8():
    names = ["m-Q4_K.gguf", "m-Q8_0.gguf", "mmproj-F16.gguf", "mmproj-Q8_0.gguf", "README.md"]
    assert _pick_gguf(names) == ["m-Q8_0.gguf", "mmproj-Q8_0.gguf"]


def test_pick_gguf_fallback_first():
    assert _pick_gguf(["a.gguf", "b.gguf", "mmproj-x.gguf"]) == ["a.gguf", "mmproj-x.gguf"]


@pytest.mark.parametrize("names", [["a.gguf"], ["mmproj-a.gguf"], ["README.md"], []])
def test_pick_gguf_missing_parts(names):
    with pytest.raises(NotReady, match="mmproj"):
        _pick_gguf(names)


# ── NotReady (몰래 받지 않는다) ────────────────────────────────────
def test_load_no_binary(env, eng, monkeypatch):
    _put_model(env.hub)
    monkeypatch.setenv("VT_LLAMA_SERVER", str(env.tmp / "ghost"))
    _no_network(monkeypatch)
    with pytest.raises(NotReady, match="설정"):
        eng.load(SPEC)
    assert eng.loaded() is None and not (env.state / "engines" / "llama-server.pid").exists()


def test_load_no_model(env, eng, monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(NotReady, match="설정"):
        eng.load(SPEC)
    assert eng.loaded() is None and not (env.fake_dir / "args.json").exists()


def test_load_partial_model(env, eng, monkeypatch):
    _put_model(env.hub, names=[SPEC.files[0]])          # mmproj 없음
    _no_network(monkeypatch)
    with pytest.raises(NotReady, match="설정"):
        eng.load(SPEC)
    assert not eng.model_present(SPEC)


def test_transcribe_without_load(eng):
    with pytest.raises(NotReady):
        eng.transcribe(b"RIFF", "ko")


def test_model_present(env, eng):
    assert not eng.model_present(SPEC)
    _put_model(env.hub)
    assert eng.model_present(SPEC)


# ── 수명주기 ───────────────────────────────────────────────────────
def test_load_transcribe_unload(env, eng):
    snap = _put_model(env.hub)
    eng.load(SPEC)
    assert eng.loaded() == SPEC.id
    pid_file = env.state / "engines" / "llama-server.pid"
    pid = eng._proc.pid
    # pid 파일은 「pid\n실행 파일 경로」 — 고아 정리 때 우리 프로세스인지 대조한다
    assert pid_file.read_text().splitlines()[0] == str(pid)
    assert len(pid_file.read_text().splitlines()) == 2

    a = _args(env)
    assert a["m"] == str(snap / SPEC.files[0]) and a["mmproj"] == str(snap / SPEC.files[1])
    assert a["host"] == "127.0.0.1"
    for flag in ("-ngl", "--no-webui", "--api-key"):
        assert flag in a["argv"]

    wav = b"RIFF0000WAVEfake"
    assert eng.transcribe(wav, "ko") == "안녕하세요"
    sent = json.loads((env.fake_dir / "last_request.json").read_text())
    part = sent["messages"][0]["content"][0]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"]) == wav

    eng.unload()
    assert eng.loaded() is None
    assert not pid_file.exists()
    assert _wait_dead(pid)


def test_transcribe_without_tag(env, eng, monkeypatch):
    _put_model(env.hub)
    monkeypatch.setenv("FAKE_REPLY", "  태그 없는 응답 ")
    eng.load(SPEC)
    assert eng.transcribe(b"x", None) == "태그 없는 응답"


def test_load_idempotent_same_model(env, eng):
    _put_model(env.hub)
    eng.load(SPEC)
    p = eng._proc
    eng.load(SPEC)
    assert eng._proc is p


def test_load_other_model_restarts(env, eng):
    other = catalog.get("qwen3-asr-1.7b-gguf")
    _put_model(env.hub)
    _put_model(env.hub, other)
    eng.load(SPEC)
    old = eng._proc.pid
    eng.load(other)
    assert eng.loaded() == other.id and eng._proc.pid != old
    assert _wait_dead(old)


def test_binds_loopback_and_requires_key(env, eng):
    _put_model(env.hub)
    eng.load(SPEC)
    assert eng._base.startswith("http://127.0.0.1:")
    body = json.dumps({"messages": []}).encode()
    # 키 없음 → 401
    req = urllib.request.Request(eng._base + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401
    # 틀린 키 → 401
    req = urllib.request.Request(eng._base + "/v1/chat/completions", data=body,
                                 headers={"Authorization": "Bearer wrong"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401
    # 인자의 키는 충분히 길고 실행마다 다름
    key = _args(env)["argv"][_args(env)["argv"].index("--api-key") + 1]
    assert len(key) >= 24 and key == eng._key
    eng.unload()
    _put_model(env.hub)
    eng.load(SPEC)
    assert eng._key != key


def test_transcribe_surfaces_http_error(env, eng):
    _put_model(env.hub)
    eng.load(SPEC)
    eng._key = "wrong"
    with pytest.raises(RuntimeError, match="401"):
        eng.transcribe(b"x", "ko")
    assert eng.loaded() == SPEC.id                      # 인증 오류는 프로세스를 내리지 않는다


def test_child_dies_then_transcribe_notready(env, eng):
    _put_model(env.hub)
    eng.load(SPEC)
    proc = eng._proc
    proc.kill()
    proc.wait()
    assert eng.loaded() is None
    with pytest.raises(NotReady):
        eng.transcribe(b"x", "ko")


def test_child_dies_connection_refused_path(env, eng):
    """loaded() 검사 이후에 죽은 경우(경쟁)에도 NotReady로 정리된다."""
    _put_model(env.hub)
    eng.load(SPEC)
    proc = eng._proc
    proc.kill()
    proc.wait()
    eng._model_id = SPEC.id            # loaded()가 아직 살아 있다고 믿는 상황을 흉내
    real = eng.loaded
    calls = {"n": 0}

    def fake_loaded():
        calls["n"] += 1
        return SPEC.id if calls["n"] == 1 else real()
    eng.loaded = fake_loaded
    with pytest.raises(NotReady, match="종료"):
        eng.transcribe(b"x", "ko")
    assert eng._proc is None


def test_load_child_dies_immediately(env, eng, monkeypatch):
    _put_model(env.hub)
    monkeypatch.setenv("FAKE_MODE", "die")
    with pytest.raises(NotReady, match="boom"):
        eng.load(SPEC)
    assert eng.loaded() is None
    assert not (env.state / "engines" / "llama-server.pid").exists()


def test_load_health_timeout_kills_child(env, eng, monkeypatch):
    _put_model(env.hub)
    monkeypatch.setenv("FAKE_MODE", "nohealth")
    monkeypatch.setattr(mod, "_START_TIMEOUT", 1)
    with pytest.raises(NotReady, match="초과"):
        eng.load(SPEC)
    assert eng.loaded() is None
    pid = _args(env)["pid"]
    assert _wait_dead(pid)


def test_rss_mb(process_table, env, eng):
    assert eng.rss_mb() == 0
    _put_model(env.hub)
    eng.load(SPEC)
    assert eng.rss_mb() >= 1


# ── 고아 프로세스 ──────────────────────────────────────────────────
def _pid_file(env):
    p = env.state / "engines" / "llama-server.pid"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_kill_orphan_terminates_our_server(process_table, env, eng):
    port = mod._free_port()
    proc = subprocess.Popen([str(env.exe), "--port", str(port), "--api-key", "k", "--host", "127.0.0.1"],
                            start_new_session=True)
    try:
        _pid_file(env).write_text(str(proc.pid))
        # 기동 직후엔 ps에 인자가 보이도록 잠시 대기
        time.sleep(0.3)
        eng._kill_orphan()
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert not _pid_file(env).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_orphan_leaves_unrelated_process(process_table, env, eng):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _pid_file(env).write_text(str(other.pid))
        eng._kill_orphan()
        time.sleep(0.2)
        assert other.poll() is None
        assert not _pid_file(env).exists()
    finally:
        other.kill()
        other.wait()


def test_kill_orphan_bad_pid_file(env, eng):
    _pid_file(env).write_text("not-a-pid")
    eng._kill_orphan()                       # 예외 없이 지나감
    eng2 = LlamaCppQwen3Engine()
    (env.state / "engines" / "llama-server.pid").unlink(missing_ok=True)
    eng2._kill_orphan()


def test_load_cleans_orphan_first(process_table, env, eng):
    _put_model(env.hub)
    port = mod._free_port()
    orphan = subprocess.Popen([str(env.exe), "--port", str(port), "--api-key", "k", "--host", "127.0.0.1"],
                              start_new_session=True)
    try:
        _pid_file(env).write_text(str(orphan.pid))
        time.sleep(0.3)
        eng.load(SPEC)
        assert _wait_dead(orphan.pid, 2), "load()가 고아 llama-server를 정리하지 않았다"
        assert eng.loaded() == SPEC.id
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


# ── 모델 삭제 ──────────────────────────────────────────────────────
def test_delete_model_only_named_files(env, eng):
    snap = _put_model(env.hub, names=list(SPEC.files) + ["unrelated.bin"])
    eng.delete_model(SPEC)
    for n in SPEC.files:
        assert not (snap / n).exists()
    assert (snap / "unrelated.bin").exists()
    assert not eng.model_present(SPEC)


def test_delete_model_unloads_first(env, eng):
    _put_model(env.hub)
    eng.load(SPEC)
    pid = eng._proc.pid
    eng.delete_model(SPEC)
    assert eng.loaded() is None and _wait_dead(pid)


# ── 환경/요구사항 ──────────────────────────────────────────────────
def test_requirements_follow_binary(env, eng, monkeypatch):
    from stt_engines.env import HostEnv
    he = HostEnv("linux", "x86_64", None)
    r = eng.requirements(he)[0]
    assert r.id == "llama-server" and r.satisfied and r.can_install and r.kind == "binary"
    monkeypatch.setenv("VT_LLAMA_SERVER", str(env.tmp / "ghost"))
    r = eng.requirements(he)[0]
    assert not r.satisfied and r.hint
    r = eng.requirements(HostEnv("other", "x86_64", None))[0]
    assert not r.can_install and r.size_mb is None


def test_supported(env, eng, monkeypatch):
    from stt_engines.env import HostEnv
    assert eng.supported(HostEnv("linux", "arm64", None)) == (True, "")
    monkeypatch.setenv("VT_LLAMA_SERVER", str(env.tmp / "ghost"))
    monkeypatch.setenv("PATH", str(env.tmp))
    ok, why = eng.supported(HostEnv("other", "x86_64", None))
    assert not ok and "VT_LLAMA_SERVER" in why


def test_install_requirement_unknown_id(eng):
    with pytest.raises(NotReady):
        eng.install_requirement("nope", lambda *_: None)


def test_install_requirement_wraps_release_error(eng, monkeypatch):
    from stt_engines import llama_release

    def boom(env, progress):
        raise llama_release.ReleaseError("해시 불일치")
    monkeypatch.setattr(llama_release, "download_and_install", boom)
    with pytest.raises(NotReady, match="해시 불일치"):
        eng.install_requirement("llama-server", lambda *_: None)
