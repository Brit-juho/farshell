"""이어서 시작할 때 직전 Claude 권한 모드(bypass 등)를 되살린다.

기록: 훅 payload의 permission_mode를 대화(native id)별로 저장한다.
적용: resume 명령에 `--permission-mode`를 붙이되, 고정 허용 목록 안의 값만. default·미기록·모르는 값은 아무것도 안 붙인다.
"""
import pytest
from starlette.testclient import TestClient

NATIVE = "6f1c1a2e-aaaa-4bbb-8ccc-0123456789ab"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    from session_catalog import db, permission_modes
    permission_modes.forget_cache()
    db.snapshot()
    yield permission_modes
    permission_modes.forget_cache()


def _mode(catalog_mod, native=NATIVE):
    from session_catalog.db import database
    with database() as db:
        return catalog_mod.lookup(db, native)


def test_known_modes_are_stored_and_replayed(catalog):
    assert catalog.record(NATIVE, "bypassPermissions") is True
    assert _mode(catalog) == "bypassPermissions"


def test_latest_mode_wins_and_same_mode_is_not_rewritten(catalog):
    assert catalog.record(NATIVE, "plan") is True
    assert catalog.record(NATIVE, "plan") is False          # 훅은 도구 호출마다 온다 — 같은 값은 쓰지 않는다
    assert catalog.record(NATIVE, "acceptEdits") is True    # Shift+Tab으로 바꾼 모드를 따라간다
    assert _mode(catalog) == "acceptEdits"


def test_default_is_stored_but_adds_nothing(catalog):
    catalog.record(NATIVE, "bypassPermissions")
    catalog.record(NATIVE, "default")                       # 사용자가 bypass를 껐다
    assert _mode(catalog) is None


@pytest.mark.parametrize("mode", ["", None, "auto", "dontAsk", "BYPASSPERMISSIONS", "bypassPermissions --x", 7, ["plan"]])
def test_unknown_modes_are_dropped(catalog, mode):
    assert catalog.record(NATIVE, mode) is False
    assert _mode(catalog) is None


@pytest.mark.parametrize("native", ["", None, "../x", "a b", "x" * 161, "a;rm -rf", 5])
def test_bad_native_ids_are_dropped(catalog, native):
    assert catalog.record(native, "bypassPermissions") is False
    assert _mode(catalog, native) is None


def test_lookup_before_any_record_is_none(catalog):
    assert _mode(catalog) is None                           # 표가 아직 없어도 오류가 아니다


def test_resume_argv_adds_flag_only_for_replayed_claude_modes():
    from session_catalog.lifecycle import resume_argv
    base = resume_argv("claude", "/bin/claude", "/root", NATIVE, None)
    assert base[-2:] == ["--resume", NATIVE] and "--permission-mode" not in base
    for mode in ("acceptEdits", "plan", "bypassPermissions"):
        argv = resume_argv("claude", "/bin/claude", "/root", NATIVE, mode)
        assert argv[-4:] == ["--resume", NATIVE, "--permission-mode", mode]
    for mode in ("default", "auto", "x; reboot", None):
        assert "--permission-mode" not in resume_argv("claude", "/bin/claude", "/root", NATIVE, mode)
    # codex는 플래그 체계가 달라 이번 범위 밖 — 모드가 와도 붙이지 않는다.
    codex = resume_argv("codex", "/bin/codex", "/root", NATIVE, "bypassPermissions")
    assert codex[-2:] == ["resume", NATIVE] and "--permission-mode" not in codex


def test_resume_argv_never_lets_a_mode_become_shell_text():
    import shlex
    from session_catalog.lifecycle import resume_argv
    argv = resume_argv("claude", "/bin/claude", "/root", NATIVE, "bypassPermissions")
    assert shlex.split(shlex.join(argv)) == argv


# ── /api/agent/event → 기록 ─────────────────────────────────────────────────

@pytest.fixture
def client(catalog):
    import main
    with TestClient(main.app) as c:
        yield c


def _event(client, **over):
    body = {"event": "pre", "agent": "claude",
            "payload": {"session_id": NATIVE, "tool_name": "Bash", "permission_mode": "bypassPermissions"}}
    for k, v in over.items():
        if k == "payload":
            body["payload"].update(v)
        else:
            body[k] = v
    return client.post("/api/agent/event", json=body)


def test_event_records_mode_for_claude(client, catalog):
    assert _event(client).status_code == 200
    assert _mode(catalog) == "bypassPermissions"


def test_event_from_codex_or_subagent_is_not_recorded(client, catalog):
    assert _event(client, agent="codex").status_code == 200
    assert _mode(catalog) is None
    assert _event(client, payload={"agent_id": "sub-1"}).status_code == 200
    assert _mode(catalog) is None


def test_event_without_mode_or_with_bad_mode_keeps_working(client, catalog):
    assert _event(client, payload={"permission_mode": None}).status_code == 200
    assert _event(client, payload={"permission_mode": "launch-missiles"}).status_code == 200
    assert _mode(catalog) is None
