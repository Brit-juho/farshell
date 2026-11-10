"""server/hook_health.py — 에이전트 훅 건강 점검(읽기 전용).

실제 ~/.claude·~/.codex 는 건드리지 않는다(tmp 로 격리). 서버는 설정 파일을 쓰지 않는다는 계약도 여기서 고정한다.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

import pytest

import claude_hooks
import codex_hooks
import hook_health as hh


@pytest.fixture
def env(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("VT_CODEX_HOME", str(codex))
    monkeypatch.delenv("VT_HOOK_HEALTH", raising=False)
    monkeypatch.delenv("VT_HOOK_HEALTH_PUSH", raising=False)
    monkeypatch.setenv("VT_CONFIG", str(tmp_path / "no-such-vt.env"))
    assert claude_hooks._cmd_install() == 0 and codex_hooks._cmd_install() == 0
    hh.reset_for_tests()
    events = []

    async def fake_broadcast(msg):
        events.append(msg)

    import routes.system as rs
    monkeypatch.setattr(rs, "broadcast_workspace_event", fake_broadcast)
    yield {"claude": claude / "settings.json", "codex": codex / "config.toml", "events": events, "tmp": tmp_path}
    hh.reset_for_tests()


def run(coro):
    return asyncio.run(coro)


def _edit_claude(path, fn):
    data = json.loads(path.read_text())
    fn(data["hooks"])
    path.write_text(json.dumps(data))


def test_ok_baseline_is_silent(env):
    r = run(hh.refresh())
    assert {c: h["state"] for c, h in r["health"].items()} == {"claude": "ok", "codex": "ok"}
    assert env["events"] == [], "처음 계산은 기준선이라 알리지 않는다"


def test_removed_hook_notifies_once_and_clears_after_repair(env):
    async def scenario():
        await hh.refresh()
        _edit_claude(env["claude"], lambda h: (h.pop("PostToolUseFailure"), h.pop("Stop")))
        r1 = await hh.refresh()
        r2 = await hh.refresh()          # 같은 이상이 계속 — 다시 알리지 않는다
        r3 = await hh.refresh(force=True)
        return r1, r2, r3

    r1, _r2, _r3 = run(scenario())
    h = r1["health"]["claude"]
    assert h["state"] == "missing" and sorted(h["missing"]) == ["PostToolUseFailure", "Stop"]
    assert len(env["events"]) == 1
    ev = env["events"][0]
    assert ev["type"] == "hooks_health" and ev["ok"] is False
    assert ev["tools"]["claude"] == {"state": "missing", "events": ["PostToolUseFailure", "Stop"]}
    assert ev["tools"]["codex"]["state"] == "ok"
    blob = json.dumps(ev)
    assert str(env["tmp"]) not in blob and "agent_hook" not in blob, "경로·설정 값을 싣지 않는다"

    assert claude_hooks._cmd_install() == 0     # 사용자가 fsh hooks install
    run(hh.refresh())
    assert len(env["events"]) == 2 and env["events"][1]["ok"] is True
    assert env["events"][1]["tools"]["claude"] == {"state": "ok", "events": []}


def test_runtime_path_is_mismatch_and_flagged_transient(env):
    def bend(hooks):
        for groups in hooks.values():
            for g in groups:
                for hk in g["hooks"]:
                    hk["command"] = hk["command"].replace(str(claude_hooks.REPO_ROOT), "/tmp/runtime-abc/gstack")
    _edit_claude(env["claude"], bend)
    r = run(hh.refresh())
    h = r["health"]["claude"]
    assert h["state"] == "mismatch" and h["transient"] is True and h["mismatch"]
    body = run(hh.status_payload())
    assert body["health"]["claude"]["state"] == "mismatch" and body["checkedAt"]


def test_codex_removed_block_is_missing(env):
    run(hh.refresh())
    env["codex"].write_text("")
    r = run(hh.refresh())
    assert r["health"]["codex"]["state"] == "missing" and len(env["events"]) == 1
    assert env["events"][0]["tools"]["codex"]["state"] == "missing"


def test_absent_cli_is_not_a_problem(env, tmp_path):
    import shutil
    shutil.rmtree(env["codex"].parent)
    r = run(hh.refresh())
    assert r["health"]["codex"]["state"] == "absent" and r["health"]["claude"]["state"] == "ok"
    assert not hh.is_bad(r["health"]) and env["events"] == []


def test_broken_json_is_unreadable_and_old_shape_is_kept(env):
    run(hh.refresh())
    env["claude"].write_text("{ not json")
    r = run(hh.refresh())
    assert r["health"]["claude"]["state"] == "unreadable"
    body = run(hh.status_payload())
    assert body["ok"] is False and body["error"] == "unreadable" and body["events"] == {}
    assert len(env["events"]) == 1 and env["events"][0]["tools"]["claude"] == {"state": "unreadable", "events": []}


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 거부를 못 만든다")
def test_permission_denied_is_unreadable(env):
    env["codex"].chmod(0)
    try:
        r = run(hh.refresh())
    finally:
        env["codex"].chmod(0o600)
    assert r["health"]["codex"]["state"] == "unreadable"


def test_missing_settings_file_with_existing_dir_is_missing(env):
    env["claude"].unlink()
    r = run(hh.refresh())
    assert r["health"]["claude"]["state"] == "missing" and len(r["health"]["claude"]["missing"]) == len(claude_hooks.EVENTS)


def test_file_changing_during_the_read_is_picked_up_next_time(env, monkeypatch):
    """읽는 도중 파일이 바뀌어도(stamp 는 읽기 전에 잡는다) 다음 점검이 새 내용으로 다시 계산한다."""
    run(hh.refresh())
    real = claude_hooks.load_settings
    state = {"n": 0}

    def racy(path):
        data = real(path)
        if state["n"] == 0:
            state["n"] += 1
            _edit_claude(env["claude"], lambda h: h.pop("SessionEnd"))     # 읽은 직후 사용자가 지운다
        return data

    monkeypatch.setattr(claude_hooks, "load_settings", racy)
    _edit_claude(env["claude"], lambda h: None)        # stamp 만 바꿔 다시 읽게 한다
    first = run(hh.refresh())
    assert first["health"]["claude"]["state"] == "ok"          # 읽은 시점의 내용
    assert not hh._fresh(hh.cached()), "읽은 뒤 파일이 바뀌었으니 캐시는 신선하지 않다"
    second = run(hh.refresh())
    assert second["health"]["claude"]["missing"] == ["SessionEnd"]
    assert len(env["events"]) == 1


def test_cache_is_reused_while_files_are_unchanged(env, monkeypatch):
    run(hh.refresh())
    calls = []
    real = hh.compute
    monkeypatch.setattr(hh, "compute", lambda: (calls.append(1), real())[1])
    run(hh.status_payload())
    run(hh.status_payload())
    assert calls == [], "파일이 같으면 다시 읽지 않는다"
    env["claude"].write_text(env["claude"].read_text() + "\n")
    run(hh.status_payload())
    assert calls == [1]


def test_disabled_switch(env, monkeypatch):
    monkeypatch.setenv("VT_HOOK_HEALTH", "0")
    assert hh.enabled() is False
    assert run(asyncio.wait_for(hh.run_loop(), 2)) is None, "꺼져 있으면 루프가 바로 끝난다"
    env["claude"].write_text("{}")
    body = run(hh.status_payload())
    assert body["monitor"] is False and body["tools"]["claude"]["ok"] is False   # 요청마다 읽는 예전 동작
    assert env["events"] == []


def test_slow_read_does_not_stall_the_loop_or_raise_an_alarm(env, monkeypatch):
    run(hh.refresh())
    monkeypatch.setattr(hh, "DEADLINE_SEC", 0.3)
    monkeypatch.setattr(claude_hooks, "load_settings", lambda p: time.sleep(1.5) or {})
    env["claude"].write_text(env["claude"].read_text() + "\n")      # stamp 변화 → 다시 읽는다

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        t = asyncio.create_task(ticker())
        start = time.monotonic()
        r = await hh.refresh()
        took = time.monotonic() - start
        t.cancel()
        return r, took, ticks

    r, took, ticks = run(scenario())
    assert took < 1.0 and ticks > 5, "이벤트 루프가 계속 돈다"
    assert r.get("stale") is True and r["health"]["claude"]["state"] == "ok", "멈춤은 훅 고장이 아니다"
    assert env["events"] == []
    time.sleep(1.4)    # 버려진 스레드가 끝나길 기다린다


def test_slow_read_with_no_previous_result_is_unknown(env, monkeypatch):
    monkeypatch.setattr(hh, "DEADLINE_SEC", 0.2)
    monkeypatch.setattr(claude_hooks, "load_settings", lambda p: time.sleep(0.8) or {})
    body = run(hh.status_payload())
    assert body["stale"] is True and body["error"] == "unknown" and env["events"] == []
    time.sleep(0.7)


def test_loop_notices_a_changed_file_immediately(env, monkeypatch):
    monkeypatch.setattr(hh, "STAMP_POLL_SEC", 0.05)

    async def scenario():
        task = asyncio.create_task(hh.run_loop())
        await asyncio.sleep(0.4)             # 기동 시 1회
        assert env["events"] == []
        _edit_claude(env["claude"], lambda h: h.pop("PreToolUse"))
        for _ in range(60):
            if env["events"]:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert len(env["events"]) == 1 and env["events"][0]["tools"]["claude"]["events"] == ["PreToolUse"]


def test_push_is_off_by_default_and_fires_once_on_transition(env, monkeypatch):
    import push
    sent = []
    monkeypatch.setattr(push, "send", lambda *a, **k: sent.append(a) or {"ok": True})

    async def scenario():
        await hh.refresh()
        _edit_claude(env["claude"], lambda h: h.pop("Stop"))
        await hh.refresh()
        assert sent == [], "기본 꺼짐"
        monkeypatch.setenv("VT_HOOK_HEALTH_PUSH", "1")
        _edit_claude(env["claude"], lambda h: h.pop("SessionEnd"))     # 이상 → 다른 이상: 푸시 없음(ok→이상이 아니다)
        await hh.refresh()
        assert sent == []
        claude_hooks._cmd_install()
        await hh.refresh()                                              # 복구
        _edit_claude(env["claude"], lambda h: h.pop("Stop"))           # ok → 이상
        await hh.refresh()

    run(scenario())
    assert len(sent) == 1


def test_route_keeps_old_shape_and_adds_health(env):
    from starlette.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        body = c.get("/api/hooks/status").json()
    assert body["ok"] is True and set(body["tools"]) == {"claude", "codex"}
    assert set(body["events"]) == set(claude_hooks.EVENTS)
    assert body["health"]["claude"]["state"] == "ok" and isinstance(body["checkedAt"], float) and body["monitor"] is True


def test_server_never_writes_agent_settings():
    """하드 계약: 점검기는 읽기 전용이다 — 설정 파일을 쓰는 함수를 부르지 않는다."""
    src = Path(hh.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for banned in ("write_settings", "write_text", "write_hooks_json", "atomic_write", "apply_install", "apply_uninstall",
                   "_cmd_install", "open(", "shutil", "os.replace", "os.rename", "unlink"):
        assert banned not in code, banned
