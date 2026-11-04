"""/voice/input 확인 단계와 /voice/inject — 실제 tmux 없이 대상·입력을 대체한다."""
import asyncio
import json
import threading

import pytest

import tmux_target
import voice_confirm as vc
import voice_handler
import voice_targets
from routes import voice

TARGET = {"key": "k" * 32, "name": "work", "tmux_id": "$1", "pane": "%5", "generation": "99", "boot": "b"}
TIMING = {"total_ms": 1}


class Req:
    def __init__(self, body=b"audio", headers=None, query=None, cookies=None, payload=None):
        self._body, self._payload = body, payload
        self.headers, self.query_params, self.cookies = headers or {}, query or {}, cookies or {}

    async def body(self):
        return self._body

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Env:
    def __init__(self):
        self.sent, self.valid, self.mode, self.kind, self.send_ok = [], True, "shell", "shell", True


@pytest.fixture
def env(monkeypatch):
    e = Env()
    voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
    vc.reset_for_tests()
    voice_targets._targets.clear()

    async def stt(audio, input_format="webm", language=None):
        return e.text, dict(TIMING)
    e.text = "ls -la"
    monkeypatch.setattr(voice_handler, "transcribe_timed", stt)
    monkeypatch.setattr(voice_targets, "valid", lambda t: e.valid)
    monkeypatch.setattr(vc, "read_mode", lambda: e.mode)
    monkeypatch.setattr(vc, "pane_kind", lambda pane: e.kind)

    def send(pane, text, *a, **k):
        e.sent.append((pane, text))
        return e.send_ok
    monkeypatch.setattr(tmux_target, "send_to_tmux", send)
    yield e
    vc.reset_for_tests(); voice_targets._targets.clear()


def issue_target(owner_cookie="a"):
    owner = voice_targets.principal(Req(cookies={}, headers={"authorization": "Bearer " + owner_cookie}))
    return owner, voice_targets.issue(owner, TARGET)["token"]


def post_input(env_, owner_cookie="a", confirm=True, vid="rec1"):
    _, tok = issue_target(owner_cookie)
    h = {"x-voice-target": tok, "x-voice-id": vid, "authorization": "Bearer " + owner_cookie}
    if confirm:
        h["x-voice-confirm"] = "1"
    return asyncio.run(voice.voice_input(Req(headers=h, query={"session_id": "s"})))


def inject(payload, cookie="a"):
    return asyncio.run(voice.voice_inject(Req(payload=payload, headers={"authorization": "Bearer " + cookie})))


def body(r):
    return json.loads(r.body)


# ── /voice/input ────────────────────────────────────────────────────

def test_shell_pane_requires_confirm_and_does_not_type(env):
    r = post_input(env)
    assert r["injected"] is False and r["pending_confirm"] is True and r["text"] == "ls -la"
    assert r["confirm_reason"] == "shell" and r["confirm_token"] and r["confirm_ttl_sec"] == 300
    assert r["target_name"] == "work" and env.sent == []


def test_agent_pane_injects_directly(env):
    env.kind = "agent"
    r = post_input(env)
    assert r["injected"] is True and "pending_confirm" not in r
    assert env.sent == [("%5", "ls -la")]


def test_unknown_pane_leans_to_confirm(env):
    env.kind = "unknown"
    r = post_input(env)
    assert r["pending_confirm"] is True and r["confirm_reason"] == "unknown" and env.sent == []


def test_mode_off_injects_everything(env):
    env.mode = "off"; env.text = "sudo rm -rf /"
    r = post_input(env)
    assert r["injected"] is True and env.sent == [("%5", "sudo rm -rf /")]


def test_mode_on_confirms_even_agent(env):
    env.mode, env.kind = "on", "agent"
    r = post_input(env)
    assert r["pending_confirm"] is True and r["confirm_reason"] == "always" and env.sent == []


def test_dangerous_confirms_even_on_agent_pane(env):
    env.kind = "agent"; env.text = "git push --force origin main"
    r = post_input(env)
    assert r["pending_confirm"] is True and r["confirm_reason"] == "dangerous" and env.sent == []


def test_client_without_confirm_support_keeps_old_behavior_but_blocks_dangerous(env):
    r = post_input(env, confirm=False)                       # 데몬·옛 클라이언트: 셸이어도 바로 입력
    assert r["injected"] is True and env.sent == [("%5", "ls -la")]
    env.sent.clear(); env.text = "sudo reboot"
    r = post_input(env, confirm=False, vid="rec2")
    assert r["injected"] is False and "pending_confirm" not in r and r["error"] and env.sent == []


def test_resend_of_same_voice_id_returns_same_pending(env):
    _, tok = issue_target()
    h = {"x-voice-target": tok, "x-voice-id": "rec1", "authorization": "Bearer a", "x-voice-confirm": "1"}
    a = asyncio.run(voice.voice_input(Req(headers=h, query={"session_id": "s"})))
    b = asyncio.run(voice.voice_input(Req(headers=h, query={"session_id": "s"})))
    assert b["duplicate"] is True and b["confirm_token"] == a["confirm_token"] and len(vc._pending) == 1


def test_empty_text_does_not_issue_token(env):
    env.text = ""
    r = post_input(env)
    assert r["injected"] is False and "pending_confirm" not in r and vc._pending == {}


# ── /voice/inject ───────────────────────────────────────────────────

def pending(env_, **kw):
    r = post_input(env_, **kw)
    return r["confirm_token"], {"token": r["confirm_token"], "text": "ls -la", "voice_id": "rec1"}


def test_inject_types_once_and_second_call_is_duplicate(env):
    tok, p = pending(env)
    r = inject({**p, "text": "  echo 고친 텍스트\x1b[31m\x07  "})
    assert r["injected"] is True
    assert env.sent == [("%5", "echo 고친 텍스트[31m")]            # 앞뒤 공백·ESC·BEL 제거
    again = inject(p)
    assert again == {"injected": True, "duplicate": True} and len(env.sent) == 1


def test_inject_other_principal_rejected(env):
    tok, p = pending(env)
    r = inject(p, cookie="mallory")
    assert r.status_code == 409 and body(r)["code"] == "expired" and env.sent == []
    assert inject(p)["injected"] is True                          # 원래 주체는 그대로 쓸 수 있다


def test_inject_unknown_and_expired_token(env, monkeypatch):
    r = inject({"token": "nope", "text": "x", "voice_id": ""})
    assert r.status_code == 409 and body(r)["code"] == "expired"
    tok, p = pending(env)
    vc._pending[tok]["expires"] = 0
    r = inject(p)
    assert r.status_code == 409 and body(r)["code"] == "expired" and env.sent == []


def test_inject_voice_id_must_match(env):
    tok, p = pending(env)
    for bad in ("other", "a b", "한글", "x" * 80):
        r = inject({**p, "voice_id": bad})
        assert r.status_code == 409 and body(r)["code"] == "voice_id"
    assert env.sent == []


@pytest.mark.parametrize("payload", [None, [], "x", {"token": 1, "text": "a"}, {"token": "t", "text": 5},
                                      {"token": "t", "text": "a", "voice_id": 3}, {"text": "a"}, {"token": "t" * 200, "text": "a"}])
def test_inject_bad_requests(env, payload):
    r = inject(payload)
    assert r.status_code == 400 and env.sent == []


def test_inject_invalid_json(env):
    r = asyncio.run(voice.voice_inject(Req(payload=ValueError("bad"))))
    assert r.status_code == 400


def test_inject_empty_and_too_long(env):
    tok, p = pending(env)
    for text in ("", "   ", "\x00\x1b\x07"):
        r = inject({**p, "text": text})
        assert r.status_code == 400 and body(r)["code"] == "empty"
    r = inject({**p, "text": "a" * (vc.MAX_TEXT_CHARS + 1)})
    assert r.status_code == 413 and body(r)["code"] == "too_long"
    r = inject({**p, "text": "a" * (vc.MAX_TEXT_CHARS * 2 + 1)})
    assert r.status_code == 413
    assert env.sent == []
    assert inject(p)["injected"] is True                          # 검증 실패는 토큰을 소진하지 않는다


def test_inject_target_gone_never_goes_elsewhere(env):
    tok, p = pending(env)
    env.valid = False
    r = inject(p)
    assert r.status_code == 409 and body(r)["code"] == "target_gone" and env.sent == []
    env.valid = True                                             # 대상이 돌아와도 토큰은 이미 버려졌다
    assert inject(p).status_code == 409 and env.sent == []


def test_inject_send_failure_can_retry(env):
    tok, p = pending(env)
    env.send_ok = False
    r = inject(p)
    assert r.status_code == 502 and body(r)["code"] == "send_failed"
    env.send_ok = True
    assert inject(p)["injected"] is True and len(env.sent) == 2


def test_inject_concurrent_requests_type_once(env):
    tok, p = pending(env)
    gate = threading.Event()
    orig = tmux_target.send_to_tmux

    def slow(pane, text, *a, **k):
        gate.wait(2)
        return orig(pane, text)
    import tmux_target as tt
    tt.send_to_tmux = slow

    async def go():
        t1 = asyncio.create_task(voice.voice_inject(Req(payload=p, headers={"authorization": "Bearer a"})))
        await asyncio.sleep(0.05)
        r2 = await voice.voice_inject(Req(payload=p, headers={"authorization": "Bearer a"}))
        gate.set()
        return await t1, r2
    try:
        r1, r2 = asyncio.run(go())
    finally:
        tt.send_to_tmux = orig
    assert r1["injected"] is True
    assert r2.status_code == 429 and body(r2)["code"] == "busy"
    assert len(env.sent) == 1
