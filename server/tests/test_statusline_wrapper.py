"""Hotswap H2 — `bin/fsh-statusline` (the usage-collecting statusLine wrapper) and its settings sync.

Disposable HOME/state, fake profile folders. The wrapper is run as a real process but only ever runs
tiny shell commands of the test's own; no `claude`, no `codex`, no real account.
"""
import hashlib
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

import agent_profile_settings as sync
import agent_profiles as profiles

WRAPPER = Path(sync.__file__).resolve().parent.parent / "bin" / "fsh-statusline"
PID = "a" * 32
LIMITS = '{"model":{"id":"m"},"cwd":"/work","rate_limits":{"five_hour":{"used_percentage":12.5,"resets_at":4102444800}}}'


def run(args, stdin, env=None):
    return subprocess.run([str(WRAPPER), *args], input=stdin.encode(), capture_output=True,
                          env={"PATH": os.environ["PATH"], "HOME": "/nonexistent", **(env or {})}, timeout=20)


@pytest.fixture
def usage_dir(tmp_path):
    return tmp_path / "usage" / "statusline"


def test_wrapper_is_executable_posix_sh():
    assert os.access(WRAPPER, os.X_OK) and WRAPPER.read_text().startswith("#!/bin/sh\n")


def test_wrapper_passes_input_output_and_exit_code_through_and_stores_the_last_value(usage_dir):
    result = run(["--profile", PID, "--dir", str(usage_dir), "--", "tr a-z A-Z; exit 3"], LIMITS)
    assert result.stdout.decode() == LIMITS.upper() and result.returncode == 3
    stored = usage_dir / f"{PID}.json"
    assert json.loads(stored.read_text())["rate_limits"]["five_hour"]["used_percentage"] == 12.5
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600 and stat.S_IMODE(usage_dir.stat().st_mode) == 0o700
    assert [p.name for p in usage_dir.iterdir()] == [stored.name]   # the temp file was renamed away


def test_a_document_without_rate_limits_never_replaces_the_last_value(usage_dir):
    run(["--profile", PID, "--dir", str(usage_dir), "--", "cat"], LIMITS)
    before = (usage_dir / f"{PID}.json").read_bytes()
    result = run(["--profile", PID, "--dir", str(usage_dir), "--", "cat"], '{"cwd":"/other"}')
    assert result.stdout.decode() == '{"cwd":"/other"}'
    assert (usage_dir / f"{PID}.json").read_bytes() == before


def test_no_original_command_prints_nothing_and_still_records(usage_dir):
    result = run(["--profile", PID, "--dir", str(usage_dir), "--"], LIMITS)
    assert result.stdout == b"" and result.returncode == 0
    assert (usage_dir / f"{PID}.json").exists()
    assert run(["--profile", PID, "--dir", str(usage_dir)], LIMITS).stdout == b""


def test_command_with_spaces_quotes_and_pipes_behaves_like_the_original(usage_dir):
    command = "echo 'a  b' \"c d\" | sed 's/ /_/g'"
    result = run(["--profile", PID, "--dir", str(usage_dir), "--", command], LIMITS)
    assert result.stdout.decode() == "a__b_c_d\n"
    direct = run(["--profile", PID, "--dir", str(usage_dir), "--", "echo", "x y", "z"], LIMITS)   # several words: exec'd as given
    assert direct.stdout.decode() == "x y z\n"


def test_a_write_failure_is_ignored_and_the_command_still_runs(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")
    result = run(["--profile", PID, "--dir", str(blocked / "d"), "--", "echo still-ran"], LIMITS)
    assert result.stdout.decode() == "still-ran\n" and result.returncode == 0
    readonly = tmp_path / "ro"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        result = run(["--profile", PID, "--dir", str(readonly), "--", "echo ok"], LIMITS)
        assert result.stdout.decode() == "ok\n" and not list(readonly.iterdir())
    finally:
        readonly.chmod(0o700)


@pytest.mark.parametrize("bad", ["", "../escape", "A" * 32, "a" * 31, "a" * 33, "a/b" + "a" * 29])
def test_a_profile_id_that_is_not_32_hex_is_never_a_file_name(usage_dir, bad):
    result = run(["--profile", bad, "--dir", str(usage_dir), "--", "echo fine"], LIMITS)
    assert result.stdout.decode() == "fine\n" and not usage_dir.exists()


def test_default_directory_follows_the_state_dir_environment(tmp_path):
    run(["--profile", PID, "--", "cat"], LIMITS, env={"VT_STATE_DIR": str(tmp_path / "state")})
    assert (tmp_path / "state" / "usage" / "statusline" / f"{PID}.json").exists()


def test_wrapper_start_cost_is_small(usage_dir):
    args = [str(WRAPPER), "--profile", PID, "--dir", str(usage_dir), "--", "cat"]
    env = {"PATH": os.environ["PATH"], "HOME": "/nonexistent"}
    started = time.perf_counter()
    for _ in range(100):
        subprocess.run(args, input=LIMITS.encode(), capture_output=True, env=env)
    average = (time.perf_counter() - started) / 100
    print(f"wrapper average over 100 runs: {average * 1000:.1f} ms")
    assert average < 0.25   # a very loose bound; the measured figure is printed for the report


# --- settings sync ---------------------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key, value in {"HOME": home, "VT_STATE_DIR": home / ".vt-test", "VT_RUN_DIR": home / "run", "VT_MCP_HOME": home,
                       "VT_CONFIG": home / "isolated.env", "VT_WORKSPACE_PATH": home / "workspace.json",
                       "VT_TUNNEL_PROVIDER": "none"}.items():
        monkeypatch.setenv(key, str(value))
    for key in ("VT_TOKEN", "VT_AUTH_TOKEN", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "VT_CODEX_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sync, "_active", lambda profile_id: False)
    return home


def _global(home, **settings):
    base = home / ".claude"
    base.mkdir(parents=True, exist_ok=True)
    (base / "settings.json").write_text(json.dumps(settings))
    return base / "settings.json"


def _mine(pid):
    return json.loads((profiles.root(pid) / "settings.json").read_text())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_global_command_becomes_the_wrapped_command_in_the_profile_only(sandbox):
    original = "~/.claude/scripts/context-bar.sh --compact 'x y'"
    gpath = _global(sandbox, statusLine={"type": "command", "command": original, "padding": 1}, theme="dark")
    before = _sha(gpath)
    pid = profiles.create("wrapped", "claude")["id"]
    report = sync.sync(pid)
    line = _mine(pid)["statusLine"]
    assert line["type"] == "command" and line["padding"] == 1
    assert line["command"] == sync.wrap_command(pid, original) and sync._wrapped(line) == (True, original)
    assert str(WRAPPER) in line["command"] and f"--profile {pid}" in line["command"]
    assert "statusLine" in report["follow"] and "statusLine" not in report["own"]
    assert _sha(gpath) == before   # the global file is only ever read
    assert sync.sync(pid)["follow"] == []   # a second run changes nothing


def test_an_unwrapped_equal_value_from_before_is_followed_not_marked_own(sandbox):
    original = "echo bar"
    _global(sandbox, statusLine={"type": "command", "command": original})
    pid = profiles.create("equal", "claude")["id"]
    (profiles.root(pid) / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": original}}))
    report = sync.sync(pid)
    assert "statusLine" not in report["own"]
    assert sync._wrapped(_mine(pid)["statusLine"]) == (True, original)


def test_the_profile_follows_a_changed_global_statusline_and_a_moved_wrapper(sandbox, monkeypatch, tmp_path):
    gpath = _global(sandbox, statusLine={"type": "command", "command": "echo one"})
    pid = profiles.create("follows", "claude")["id"]
    sync.sync(pid)
    gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo two"}}))
    report = sync.sync(pid)
    assert "statusLine" in report["follow"] and sync._wrapped(_mine(pid)["statusLine"]) == (True, "echo two")
    # The checkout moved: the next sync rewrites the path and still does not call it the profile's own.
    moved = tmp_path / "elsewhere" / "fsh-statusline"
    moved.parent.mkdir()
    moved.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sync, "WRAPPER", moved)
    report = sync.sync(pid)
    assert str(moved) in _mine(pid)["statusLine"]["command"] and "statusLine" not in report["own"] + report["keep"]
    assert sync._wrapped(_mine(pid)["statusLine"]) == (True, "echo two")


def test_no_global_statusline_puts_the_wrapper_alone_and_removal_follows(sandbox):
    gpath = _global(sandbox, theme="dark")
    pid = profiles.create("alone", "claude")["id"]
    sync.sync(pid)
    line = _mine(pid)["statusLine"]
    assert sync._wrapped(line) == (True, None) and line["command"] == sync.wrap_command(pid, None)
    gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo later"}}))
    sync.sync(pid)
    assert sync._wrapped(_mine(pid)["statusLine"]) == (True, "echo later")
    gpath.write_text(json.dumps({}))
    sync.sync(pid)
    assert sync._wrapped(_mine(pid)["statusLine"]) == (True, None)


def test_a_statusline_the_profile_chose_itself_stays_its_own_and_is_not_wrapped(sandbox):
    gpath = _global(sandbox, statusLine={"type": "command", "command": "echo global"})
    pid = profiles.create("choosy", "claude")["id"]
    sync.sync(pid)
    mine = _mine(pid)
    mine["statusLine"] = {"type": "command", "command": "echo mine"}
    (profiles.root(pid) / "settings.json").write_text(json.dumps(mine))
    for change in ("echo global-2", "echo global-3"):
        gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": change}}))
        report = sync.sync(pid)
        assert _mine(pid)["statusLine"] == {"type": "command", "command": "echo mine"}
        assert "statusLine" in report["keep"] + report["own"]
    # Taking the global value back wraps it again.
    sync.sync(pid, take_global=["statusLine"])
    assert sync._wrapped(_mine(pid)["statusLine"]) == (True, "echo global-3")


def test_a_wrapper_that_is_missing_is_never_written_and_a_non_command_statusline_is_left_alone(sandbox, monkeypatch):
    _global(sandbox, statusLine={"type": "command", "command": "echo x"})
    pid = profiles.create("no-wrapper", "claude")["id"]
    monkeypatch.setattr(sync, "WRAPPER", Path("/nonexistent/fsh-statusline"))
    sync.sync(pid)
    assert _mine(pid)["statusLine"] == {"type": "command", "command": "echo x"}
    other = {"type": "static", "text": "hi"}
    assert sync._statusline_want(pid, other) == other


def test_the_global_files_are_byte_identical_after_every_run(sandbox):
    gpath = _global(sandbox, statusLine={"type": "command", "command": "echo x"}, enabledPlugins={"a@m": True})
    mcp = sandbox / ".claude.json"
    mcp.write_text(json.dumps({"mcpServers": {}}))
    before = (_sha(gpath), _sha(mcp))
    pid = profiles.create("hash", "claude")["id"]
    sync.sync(pid)
    sync.sync(pid, dry_run=True)
    sync.sync(pid)
    assert (_sha(gpath), _sha(mcp)) == before


def test_an_existing_folder_open_to_others_is_closed_but_a_linked_one_is_never_used(usage_dir, tmp_path):
    usage_dir.mkdir(parents=True)
    usage_dir.chmod(0o755)
    run(["--profile", PID, "--dir", str(usage_dir), "--", "cat"], LIMITS)
    assert stat.S_IMODE(usage_dir.stat().st_mode) & 0o077 == 0          # group/other bits are gone (hotswap-review)
    assert (usage_dir / f"{PID}.json").exists()
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    result = run(["--profile", PID, "--dir", str(link), "--", "echo through"], LIMITS)
    assert result.stdout.decode() == "through\n" and not list(real.iterdir())   # the command still runs; nothing is written through a link


# --- the automatic (statusline_only) path never resurrects hooks ---------------------------

def _hook_cmds(data):
    return {e: [h["command"] for g in gs for h in g.get("hooks", [])] for e, gs in data.get("hooks", {}).items()}


def test_statusline_only_sync_does_not_readd_removed_hooks_but_follows_the_statusline(sandbox):
    import claude_hooks
    gpath = _global(sandbox, statusLine={"type": "command", "command": "echo one"})
    pid = profiles.create("nohooks", "claude")["id"]
    sync.sync(pid)
    spath = profiles.root(pid) / "settings.json"
    data = _mine(pid)
    data["hooks"] = {"Notification": [{"hooks": [{"type": "command", "command": "/mine/notify.sh"}]}]}   # FarShell hooks removed on purpose
    spath.write_text(json.dumps(data))
    gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo two"}}))
    sync.sync(pid, statusline_only=True)
    after = _mine(pid)
    assert sync._wrapped(after["statusLine"]) == (True, "echo two")
    assert _hook_cmds(after) == {"Notification": ["/mine/notify.sh"]}
    gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo three"}}))
    sync.sync(pid)   # the explicit path still installs the full set
    assert set(_hook_cmds(_mine(pid))) >= set(claude_hooks.EVENTS)


def test_statusline_only_sync_refreshes_only_an_old_farshell_path_and_leaves_mcp_alone(sandbox):
    import claude_hooks
    gpath = _global(sandbox, statusLine={"type": "command", "command": "echo one"})
    pid = profiles.create("moved", "claude")["id"]
    sync.sync(pid)
    spath = profiles.root(pid) / "settings.json"
    data = _mine(pid)
    data["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": "/old/server/agent_hook.sh stop claude"},
                                         {"type": "command", "command": "/mine/own.sh"}]}]}
    spath.write_text(json.dumps(data))
    cpath = profiles.root(pid) / ".claude.json"
    cpath.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    cbefore = _sha(cpath)
    gpath.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo two"}, "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "/global/hook.sh"}]}]}}))
    sync.sync(pid, statusline_only=True)
    assert _hook_cmds(_mine(pid)) == {"Stop": [claude_hooks.hook_command("Stop"), "/mine/own.sh"]}
    assert _sha(cpath) == cbefore
