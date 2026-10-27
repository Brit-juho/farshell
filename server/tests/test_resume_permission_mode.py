"""이어서 시작할 때 직전 Claude 권한 모드(bypass 등)를 되살린다.

기록: 훅 payload의 permission_mode를 대화(native id)별로 저장한다.
적용: resume 명령에 `--permission-mode`를 붙이되, 고정 허용 목록 안의 값만. default도 명시하며 미기록·모르는 값은 재개를 거부한다.
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


def test_default_is_stored_and_replayed_explicitly(catalog):
    catalog.record(NATIVE, "bypassPermissions")
    catalog.record(NATIVE, "default")                       # 사용자가 bypass를 껐다
    assert _mode(catalog) == "default"


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


def test_resume_argv_restores_every_known_claude_mode_and_refuses_unknown():
    from session_catalog.lifecycle import resume_argv
    from session_catalog.db import Refused
    for mode in ("default", "acceptEdits", "plan", "bypassPermissions"):
        argv = resume_argv("claude", "/bin/claude", "/root", NATIVE, mode)
        assert argv[-4:] == ["--resume", NATIVE, "--permission-mode", mode]
    for mode in ("auto", "x; reboot", None):
        with pytest.raises(Refused, match="execution_policy_unavailable"):
            resume_argv("claude", "/bin/claude", "/root", NATIVE, mode)
    with pytest.raises(Refused, match="execution_policy_unavailable"):
        resume_argv("codex", "/bin/codex", "/root", NATIVE, "bypassPermissions")


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


def test_accepted_owner_hook_records_scoped_default(client, catalog, tmp_path, monkeypatch):
    import pane_resolve, agent_status, agent_config_context, hook_identity
    from session_catalog.db import database
    root = str(tmp_path / 'claude-profile')
    monkeypatch.setattr(pane_resolve, 'is_our_socket', lambda *args: True)
    monkeypatch.setattr(hook_identity, 'classify', lambda *args: (hook_identity.OK, 42))
    monkeypatch.setattr(pane_resolve, 'resolve', lambda *args, **kwargs: ('sample', 'pane'))
    monkeypatch.setattr(agent_status, 'on_event', lambda *args, **kwargs: {'session_id': NATIVE, 'status': 'idle'})
    monkeypatch.setattr(agent_config_context, 'identify', lambda *args: {'tool': 'claude', 'root': root, 'pid': 42})
    assert _event(client, pane='%1', payload={'permission_mode': 'default'}).status_code == 200
    with database() as db:
        assert catalog.lookup(db, NATIVE, root=root) == 'default'


def test_unknown_profile_on_newer_owner_hook_invalidates_old_bypass(client, catalog, tmp_path, monkeypatch):
    import pane_resolve, agent_status, agent_config_context, hook_identity
    from session_catalog.db import database
    root = str(tmp_path / 'claude-profile')
    monkeypatch.setattr(pane_resolve, 'is_our_socket', lambda *args: True)
    monkeypatch.setattr(hook_identity, 'classify', lambda *args: (hook_identity.OK, 42))
    catalog.record(NATIVE, 'bypassPermissions', root=root, observed_ns=1)
    monkeypatch.setattr(pane_resolve, 'resolve', lambda *args, **kwargs: ('sample', 'pane'))
    monkeypatch.setattr(agent_status, 'on_event', lambda *args, **kwargs: {'session_id': NATIVE, 'status': 'idle'})
    def missing(*args):
        raise ValueError('process gone')
    monkeypatch.setattr(agent_config_context, 'identify', missing)
    assert _event(client, pane='%1', payload={'permission_mode': 'default'}).status_code == 200
    with database() as db:
        assert catalog.lookup(db, NATIVE, root=root) is None


def test_unverified_legacy_hook_keeps_reporting_but_cannot_supply_profile_policy(client, catalog, tmp_path, monkeypatch):
    import pane_resolve, agent_status
    from session_catalog.db import database
    root = str(tmp_path / 'claude-profile')
    catalog.record(NATIVE, 'bypassPermissions', root=root, observed_ns=1)
    monkeypatch.setattr(pane_resolve, 'resolve', lambda *args, **kwargs: ('sample', 'pane'))
    monkeypatch.setattr(agent_status, 'on_event', lambda *args, **kwargs: {'session_id': NATIVE, 'status': 'idle'})
    assert _event(client, payload={'permission_mode': 'default'}).status_code == 200
    assert _mode(catalog) == 'default'
    with database() as db:
        assert catalog.lookup(db, NATIVE, root=root) is None


def test_hook_agent_pid_must_match_the_profile_process(client, catalog, tmp_path, monkeypatch):
    import pane_resolve, agent_status, agent_config_context, hook_identity
    from session_catalog.db import database
    root = str(tmp_path / 'claude-profile')
    catalog.record(NATIVE, 'bypassPermissions', root=root, observed_ns=1)
    monkeypatch.setattr(pane_resolve, 'resolve', lambda *args, **kwargs: ('sample', 'pane'))
    monkeypatch.setattr(pane_resolve, 'is_our_socket', lambda *args: True)
    monkeypatch.setattr(hook_identity, 'classify', lambda *args: (hook_identity.OK, 42))
    monkeypatch.setattr(agent_status, 'on_event', lambda *args, **kwargs: {'session_id': NATIVE, 'status': 'idle'})
    monkeypatch.setattr(agent_config_context, 'identify', lambda *args: {'tool': 'claude', 'root': root, 'pid': 99})
    assert _event(client, pane='%1', payload={'permission_mode': 'default'}).status_code == 200
    with database() as db:
        assert catalog.lookup(db, NATIVE, root=root) is None
