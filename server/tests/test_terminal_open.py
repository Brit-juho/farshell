"""terminal_open — 새 터미널 창 열기 단일 구현.

실제 앱을 띄우지 않는다: spawner와 설치 여부를 가짜로 바꿔 선택 순서·폴백·임시 스크립트만 본다.
"""

import os
import stat

import pytest

import platform_utils as pu
import terminal_open as t


@pytest.fixture(autouse=True)
def _private_tmpdir(tmp_path, monkeypatch):
    """launcher는 실제로는 실행되면서 스스로 지운다. 테스트의 가짜 spawner는 실행하지 않으므로
    사용자의 임시 폴더에 파일이 쌓이지 않게 테스트 전용 폴더를 쓴다."""
    monkeypatch.setattr(t.tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", True)
    monkeypatch.setattr(pu, "IS_LINUX", False)
    monkeypatch.delenv("VT_TERMINAL", raising=False)
    return monkeypatch


def _fake_spawners(mac, results):
    """results: {앱: (ok, err)} — 호출된 앱 순서를 calls에 기록."""
    calls = []

    def make(app):
        def spawn(line):
            calls.append((app, line))
            ok, err = results.get(app, (True, ""))
            return ok, err, ""
        return spawn

    mac.setattr(t, "_SPAWNERS", {a: make(a) for a in
                ("iterm", "ghostty", "terminal", "wezterm", "kitty", "alacritty")})
    return calls


def test_preference_defaults_to_auto_and_ignores_garbage(mac):
    assert t.preference() == "auto"
    mac.setenv("VT_TERMINAL", "GhosTTY")
    assert t.preference() == "ghostty"
    mac.setenv("VT_TERMINAL", "nonsense")
    assert t.preference() == "auto"


def test_auto_order_puts_iterm_first_when_installed(mac):
    mac.setattr(t, "is_installed", lambda n: n in ("iterm", "ghostty", "terminal"))
    calls = _fake_spawners(mac, {})
    res = t.open_terminal("echo hi")
    assert res.ok and res.app == "iterm"
    assert [a for a, _ in calls] == ["iterm"]


def test_preferred_app_wins_over_auto_order(mac):
    mac.setattr(t, "is_installed", lambda n: n in ("iterm", "ghostty", "terminal"))
    calls = _fake_spawners(mac, {})
    res = t.open_terminal("echo hi", preferred="ghostty")
    assert res.ok and res.app == "ghostty"
    assert [a for a, _ in calls] == ["ghostty"]


def test_preferred_not_installed_falls_back_with_note(mac):
    mac.setattr(t, "is_installed", lambda n: n in ("ghostty", "terminal"))
    calls = _fake_spawners(mac, {})
    res = t.open_terminal("echo hi", preferred="iterm")
    assert res.ok and res.app == "ghostty"
    assert "iterm" in res.note and "설치" in res.note
    assert [a for a, _ in calls] == ["ghostty"]


def test_preferred_failure_falls_back_to_next_app(mac):
    mac.setattr(t, "is_installed", lambda n: n in ("ghostty", "terminal"))
    calls = _fake_spawners(mac, {"ghostty": (False, "automation denied")})
    res = t.open_terminal("echo hi", preferred="ghostty")
    assert res.ok and res.app == "terminal"
    assert [a for a, _ in calls] == ["ghostty", "terminal"]
    assert "ghostty" in res.note


def test_all_fail_reports_errors_and_removes_launcher(mac):
    mac.setattr(t, "is_installed", lambda n: n == "terminal")
    seen = []
    mac.setattr(t, "_SPAWNERS", {"terminal": lambda line: (seen.append(line) or (False, "boom", ""))})
    res = t.open_terminal("echo hi")
    assert not res.ok and "boom" in res.err
    launcher = seen[0].split()[-1]
    assert not os.path.exists(launcher)  # 실패하면 임시 스크립트를 남기지 않는다


def test_launcher_keeps_quotes_and_removes_itself(mac):
    mac.setattr(t, "is_installed", lambda n: n == "terminal")
    seen = []
    mac.setattr(t, "_SPAWNERS", {"terminal": lambda line: (seen.append(line) or (True, "", ""))})
    cmd = "tmux -L fsh new -A -s dev 'claude --resume --disallowedTools \"Bash(rm:*)\"'"
    res = t.open_terminal(cmd)
    assert res.ok
    shell, flag, path = seen[0].split()
    assert flag == "-l" and shell.startswith("/")
    body = open(path, encoding="utf-8").read()
    try:
        lines = body.splitlines()
        assert lines[0] == f"rm -f -- {path}"       # 시작하자마자 자기 자신을 지운다
        assert lines[1] == f"exec {cmd}"             # 명령은 한 글자도 바뀌지 않는다
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    finally:
        os.unlink(path)


def test_launcher_path_never_reaches_applescript_unsafe(mac, monkeypatch):
    monkeypatch.setattr(t.tempfile, "mkstemp", lambda prefix="": (os.open(os.devnull, os.O_WRONLY), '/tmp/evil"; do shell script "x'))
    monkeypatch.setattr(os, "unlink", lambda p: None)
    with pytest.raises(ValueError):
        t._write_launcher("echo hi")


def test_empty_command_is_rejected(mac):
    res = t.open_terminal("   ")
    assert not res.ok and "비어" in res.err


def test_linux_delegates_to_linux_spawner(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", False)
    monkeypatch.setattr(pu, "IS_LINUX", True)
    monkeypatch.setattr(pu, "IS_WSL2", False)
    got = []
    monkeypatch.setattr(pu, "spawn_linux_terminal", lambda c: got.append(c) or True)
    res = t.open_terminal("echo hi")
    assert res.ok and got == ["echo hi"]


def test_platform_utils_spawn_mac_terminal_uses_single_implementation(mac):
    mac.setattr(t, "open_terminal", lambda c: t.OpenResult(True, "ghostty"))
    assert pu.spawn_mac_terminal("echo hi") is True
    mac.setattr(t, "open_terminal", lambda c: t.OpenResult(False, "", "nope"))
    assert pu.spawn_mac_terminal("echo hi") is False


def test_main_cli_outputs(mac, capsys):
    mac.setattr(t, "open_terminal", lambda c: t.OpenResult(True, "ghostty", "", "메모"))
    assert t.main(["--", "echo hi"]) == 0
    out = capsys.readouterr().out
    assert "opened:ghostty" in out and "note:메모" in out
    mac.setattr(t, "open_terminal", lambda c: t.OpenResult(False, "", "실패"))
    assert t.main(["--", "echo hi"]) == 1
    assert "실패" in capsys.readouterr().err
    assert t.main(["--choices"]) == 0


def test_ghostty_creates_window_then_activates_in_background(monkeypatch):
    scripts, popens = [], []
    monkeypatch.setattr(t, "_osascript", lambda s, timeout=8.0: (scripts.append(s) or (True, "")))
    monkeypatch.setattr(t, "_popen", lambda argv: (popens.append(argv) or (True, "")))
    ok, err, _ = t._spawn_ghostty("/bin/zsh -l /tmp/fsh-open.abc")
    assert ok and not err
    # 창 생성 스크립트에는 activate가 없다(같이 넣으면 호출이 2초 넘게 걸린다)
    assert "activate" not in scripts[0]
    assert 'command:"/bin/zsh -l /tmp/fsh-open.abc"' in scripts[0]
    # 앞으로 가져오기는 따로, 기다리지 않는 프로세스로
    assert popens == [["osascript", "-e", 'tell application "Ghostty" to activate']]


def test_ghostty_falls_back_to_open_na_when_applescript_fails(monkeypatch):
    popens = []
    monkeypatch.setattr(t, "_osascript", lambda s, timeout=8.0: (False, "not authorized"))
    monkeypatch.setattr(t, "_popen", lambda argv: (popens.append(argv) or (True, "")))
    ok, err, _ = t._spawn_ghostty("/bin/zsh -l /tmp/fsh-open.abc")
    assert ok
    assert popens[0][:5] == ["open", "-na", "Ghostty", "--args", "-e"]


def test_launcher_uses_exec_for_every_app(mac):
    path = t._write_launcher("tmux -L fsh attach -t dev")
    try:
        assert open(path, encoding="utf-8").read().splitlines()[1] == "exec tmux -L fsh attach -t dev"
    finally:
        os.unlink(path)
