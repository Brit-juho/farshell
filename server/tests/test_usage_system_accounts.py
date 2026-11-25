"""Default (system) accounts in the usage list: the global ~/.claude via a `system` statusline wrap, and ~/.codex.

Disposable HOME/state only; the wrapper runs as a real process with the test's own tiny commands, no `claude`,
no `codex`, no network (the Codex endpoint is replaced by a fake response).
"""
import json
import os
import stat
import subprocess
import time
import types

import pytest

import agent_profile_settings as sync
import agent_profiles as profiles
import usage
from usage import profiles as usage_profiles
from usage import statusline, system_accounts

ORIGINAL = "~/.claude/scripts/context-bar.sh --compact 'x y'"
LIMITS = '{"cwd":"/w","rate_limits":{"five_hour":{"used_percentage":41,"resets_at":4102444800}}}'


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key, value in {"HOME": home, "VT_STATE_DIR": home / ".vt-test", "VT_RUN_DIR": home / "run", "VT_MCP_HOME": home,
                       "VT_CONFIG": home / "isolated.env", "VT_WORKSPACE_PATH": home / "workspace.json",
                       "VT_TUNNEL_PROVIDER": "none", "VT_USAGE_PROVIDER": "native",
                       "VT_CLAUTH_STATUS": home / "no-clauth.json"}.items():
        monkeypatch.setenv(key, str(value))
    for key in ("VT_TOKEN", "VT_AUTH_TOKEN", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "VT_CODEX_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sync, "_active", lambda profile_id: False)
    monkeypatch.setattr(usage_profiles, "sys", types.SimpleNamespace(platform="linux"))   # never the real keychain
    usage._reset_for_tests()
    yield home
    usage._reset_for_tests()
    usage_profiles._instances.clear()


def _global(home, **settings):
    base = home / ".claude"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "settings.json"
    path.write_text(json.dumps(settings))
    return path


def _read(path):
    return json.loads(path.read_text())


# --- turning collection on and off --------------------------------------------------------------

def test_turning_on_wraps_only_the_statusline_and_turning_off_restores_it_exactly(sandbox):
    path = _global(sandbox, statusLine={"type": "command", "command": ORIGINAL, "padding": 1}, theme="dark")
    assert system_accounts.claude_state() == "off"
    assert system_accounts.set_claude(True) == "on"
    after = _read(path)
    assert after["theme"] == "dark" and after["statusLine"]["padding"] == 1
    assert after["statusLine"]["command"] == sync.wrap_command("system", ORIGINAL)
    assert sync._wrapped(after["statusLine"]) == (True, ORIGINAL)
    assert path.with_suffix(".json.vtbak").exists()            # the copy taken before the write
    assert system_accounts.set_claude(True) == "on"             # idempotent
    assert system_accounts.set_claude(False) == "off"
    assert _read(path) == {"statusLine": {"type": "command", "command": ORIGINAL, "padding": 1}, "theme": "dark"}


def test_no_global_statusline_gets_the_wrapper_alone_and_off_removes_the_key(sandbox):
    path = _global(sandbox, theme="dark")
    assert system_accounts.set_claude(True) == "on"
    assert _read(path)["statusLine"] == {"type": "command", "command": sync.wrap_command("system", None)}
    assert system_accounts.set_claude(False) == "off"
    assert _read(path) == {"theme": "dark"}


def test_a_statusline_that_is_not_a_command_is_refused_and_left_alone(sandbox):
    path = _global(sandbox, statusLine={"type": "static", "text": "hi"})
    before = path.read_bytes()
    assert system_accounts.claude_state() == "unsupported"
    with pytest.raises(system_accounts.Refused):
        system_accounts.set_claude(True)
    assert path.read_bytes() == before


def test_a_broken_global_settings_file_is_never_overwritten(sandbox):
    path = _global(sandbox)
    path.write_text("{not json")
    assert system_accounts.claude_state() == "unreadable"
    with pytest.raises(system_accounts.Refused):
        system_accounts.set_claude(True)
    assert path.read_text() == "{not json"


def test_a_wrap_from_another_install_path_is_stale_and_turning_on_rewraps_the_same_original(sandbox):
    old = "/old/place/bin/fsh-statusline --profile system --dir /old/state -- " + sync.shlex.quote(ORIGINAL)
    path = _global(sandbox, statusLine={"type": "command", "command": old})
    assert system_accounts.claude_state() == "stale"
    assert system_accounts.set_claude(True) == "on"
    assert _read(path)["statusLine"]["command"] == sync.wrap_command("system", ORIGINAL)


# --- profiles never record into the default account ---------------------------------------------

def test_a_profile_follows_the_users_command_not_the_system_wrapper(sandbox):
    _global(sandbox, statusLine={"type": "command", "command": ORIGINAL})
    pid = profiles.create("alice", "claude")["id"]
    sync.sync(pid, include_mcp=False)
    before = json.loads((profiles.root(pid) / "settings.json").read_text())["statusLine"]
    assert before["command"] == sync.wrap_command(pid, ORIGINAL)
    system_accounts.set_claude(True)
    report = sync.sync(pid, include_mcp=False)
    mine = json.loads((profiles.root(pid) / "settings.json").read_text())["statusLine"]
    assert mine == before and "statusLine" not in report["follow"] + report["own"]
    assert sync.wrapped_id(mine) == pid


def test_a_new_profile_created_while_wrapped_still_gets_only_its_own_wrapper(sandbox):
    _global(sandbox, statusLine={"type": "command", "command": ORIGINAL})
    system_accounts.set_claude(True)
    pid = profiles.create("bob", "claude")["id"]
    sync.sync(pid, include_mcp=False)
    mine = json.loads((profiles.root(pid) / "settings.json").read_text())["statusLine"]
    assert mine["command"] == sync.wrap_command(pid, ORIGINAL) and "--profile system" not in mine["command"]


def test_the_wrapper_records_the_system_file_and_runs_the_original(sandbox, tmp_path):
    folder = tmp_path / "statusline"
    result = subprocess.run([str(sync.WRAPPER), "--profile", "system", "--dir", str(folder), "--", "tr a-z A-Z"],
                            input=LIMITS.encode(), capture_output=True, timeout=20,
                            env={"PATH": os.environ["PATH"], "HOME": "/nonexistent"})
    assert result.stdout.decode() == LIMITS.upper()
    stored = folder / "system.json"
    assert json.loads(stored.read_text())["rate_limits"]["five_hour"]["used_percentage"] == 41
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600


@pytest.mark.parametrize("bad", ["System", "system/../x", "systemx", "../system"])
def test_only_the_exact_word_system_is_accepted_besides_profile_ids(tmp_path, bad):
    folder = tmp_path / "statusline"
    subprocess.run([str(sync.WRAPPER), "--profile", bad, "--dir", str(folder), "--"], input=LIMITS.encode(),
                   capture_output=True, timeout=20, env={"PATH": os.environ["PATH"], "HOME": "/nonexistent"})
    assert not folder.exists()


# --- the usage list -----------------------------------------------------------------------------

def _system_value(pct, mtime=None):
    folder = statusline.directory()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    path = folder / "system.json"
    path.write_text(json.dumps({"rate_limits": {"five_hour": {"used_percentage": pct, "resets_at": time.time() + 600}}}))
    path.chmod(0o600)
    if mtime:
        os.utime(path, (mtime, mtime))


def _claude_system(snapshot):
    return next(p for p in snapshot["profiles"] if p.get("system") and p["provider"] == "claude")


def test_native_mode_lists_the_default_claude_card_with_its_collect_state(sandbox):
    _global(sandbox, statusLine={"type": "command", "command": ORIGINAL})
    assert usage.merged_capability()["available"] is True
    card = _claude_system(usage.merged_snapshot())
    assert card["collect"] == "off" and card["windows"] == [] and card["profile_id"] is None
    system_accounts.set_claude(True)
    _system_value(23)
    card = _claude_system(usage.merged_snapshot())
    assert card["collect"] == "on" and card["windows"][0]["pct"] == 23 and card["source"] == "statusline"


def test_no_global_claude_folder_means_no_default_claude_card(sandbox):
    snap = usage.merged_snapshot()
    assert snap is None or not any(p.get("system") and p["provider"] == "claude" for p in snap["profiles"])


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _codex_home(home):
    folder = home / ".codex"
    folder.mkdir()
    (folder / "auth.json").write_text(json.dumps({"tokens": {"access_token": "fake-" + "c" * 30, "account_id": "acct-1"}}))


def test_native_mode_now_shows_the_default_codex_account(sandbox, monkeypatch):
    _codex_home(sandbox)
    import usage.codex as codex_mod
    monkeypatch.setattr(codex_mod, "urlopen", lambda req, timeout=None: _Resp(
        {"plan_type": "plus", "account_id": "acct-1", "rate_limit": {"primary_window": {"used_percent": 12, "reset_after_seconds": 60}}}))
    card = next(p for p in usage.merged_snapshot()["profiles"] if p.get("system") and p["provider"] == "codex")
    assert card["windows"][0]["pct"] == 12 and card["name"] == "codex"


def test_the_default_codex_card_merges_into_a_profile_proven_to_be_the_same_account(sandbox, monkeypatch):
    _codex_home(sandbox)
    import usage.codex as codex_mod
    answer = {"plan_type": "plus", "account_id": "acct-1", "rate_limit": {"primary_window": {"used_percent": 12, "reset_after_seconds": 60}}}
    monkeypatch.setattr(codex_mod, "urlopen", lambda req, timeout=None: _Resp(answer))
    pid = profiles.create("work", "codex")["id"]
    (profiles.root(pid) / "auth.json").write_text(json.dumps({"tokens": {"access_token": "fake-" + "d" * 30, "account_id": "acct-1"}}))
    monkeypatch.setattr(usage_profiles, "_request", lambda url, headers: answer)
    monkeypatch.setattr("usage.appserver.refresh_async", lambda profile: None)
    rows = usage.merged_snapshot()["profiles"]
    assert not any(p.get("system") and p["provider"] == "codex" for p in rows)
    assert next(p for p in rows if p.get("profile_id") == pid)["same_as_system"] is True


def test_a_profile_on_a_different_codex_account_keeps_both_cards(sandbox, monkeypatch):
    _codex_home(sandbox)
    import usage.codex as codex_mod
    monkeypatch.setattr(codex_mod, "urlopen", lambda req, timeout=None: _Resp(
        {"account_id": "acct-1", "rate_limit": {"primary_window": {"used_percent": 12, "reset_after_seconds": 60}}}))
    pid = profiles.create("other", "codex")["id"]
    (profiles.root(pid) / "auth.json").write_text(json.dumps({"tokens": {"access_token": "fake-" + "e" * 30, "account_id": "acct-2"}}))
    monkeypatch.setattr(usage_profiles, "_request", lambda url, headers: {
        "account_id": "acct-2", "rate_limit": {"primary_window": {"used_percent": 5, "reset_after_seconds": 60}}})
    monkeypatch.setattr("usage.appserver.refresh_async", lambda profile: None)
    rows = usage.merged_snapshot()["profiles"]
    assert any(p.get("system") and p["provider"] == "codex" for p in rows)
    assert "same_as_system" not in next(p for p in rows if p.get("profile_id") == pid)


def test_none_mode_still_hides_everything(sandbox, monkeypatch):
    _global(sandbox)
    _codex_home(sandbox)
    monkeypatch.setenv("VT_USAGE_PROVIDER", "none")
    usage._reset_for_tests()
    assert usage.merged_snapshot() is None and usage.codex_snapshot() is None



# --- the route: human session, elevated for writes, strict body ---------------------------------

@pytest.fixture
def client(sandbox, monkeypatch):
    import auth as _auth
    from starlette.testclient import TestClient

    state_dir = sandbox / ".vt-test"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    import main
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": "s3cret-pw"}).status_code == 200
        yield c


def test_route_reads_the_state_and_writes_only_after_elevation(client, sandbox):
    path = _global(sandbox, statusLine={"type": "command", "command": ORIGINAL})
    assert client.get("/api/usage/system-claude").json() == {"state": "off"}
    r = client.post("/api/usage/system-claude", json={"enabled": True})
    assert r.status_code == 401 and r.json() == {"error": "elevation_required"}
    assert _read(path)["statusLine"]["command"] == ORIGINAL
    assert client.post("/api/auth/elevate", json={"password": "s3cret-pw"}).status_code == 200
    for bad in ({}, {"enabled": "yes"}, {"enabled": True, "x": 1}, [True]):
        assert client.post("/api/usage/system-claude", json=bad).status_code == 400
    r = client.post("/api/usage/system-claude", json={"enabled": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "state": "on"}
    assert sync.wrapped_id(_read(path)["statusLine"]) == "system"
    r = client.post("/api/usage/system-claude", json={"enabled": False})
    assert r.json() == {"ok": True, "state": "off"} and _read(path)["statusLine"]["command"] == ORIGINAL


def test_route_answers_409_when_it_cannot_be_done_safely(client, sandbox):
    _global(sandbox, statusLine={"type": "static"})
    assert client.post("/api/auth/elevate", json={"password": "s3cret-pw"}).status_code == 200
    r = client.post("/api/usage/system-claude", json={"enabled": True})
    assert r.status_code == 409 and r.json()["ok"] is False
