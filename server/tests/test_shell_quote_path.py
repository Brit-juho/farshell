"""S9: 파일 경로를 pane 에 입력할 때 셸 인용(`$(…)`·백틱·`;` 가 든 업로드 이름이 Enter 에 실행되지 않게).

frontend/js/lib/shell-quote.js 와 같은 규칙이다(그쪽은 frontend/tests/shell-quote.test.js).
"""
import subprocess

import pytest

import tmux_target
from tmux_target import shell_quote_path

HOSTILE = [
    "/x/a$(touch PWNED)b.png", "/x/a`touch PWNED`b.png", "/x/a;touch PWNED;b.png", "/x/a&&touch PWNED.png",
    "/x/it's.png", '/x/a"b.png', "/x/a\\b.png", "/x/a|b>c<d.png", "/x/*?[a].png", "/x/a#b!c{d}.png",
    "/x/line1\nline2.png", "/x/tab\there.png", "/x/공백 이름.png", "=ls", "%1", "-rf", "~root", "",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_round_trips_through_a_real_shell_and_runs_nothing(tmp_path, name):
    out = subprocess.run(["sh", "-c", f"printf '%s' {shell_quote_path(name)}"], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout
    assert out == name
    assert list(tmp_path.iterdir()) == [], "명령이 실행돼 파일이 생겼다"


def test_ordinary_paths_are_left_alone():
    for p in ("/Users/u/.vt/files/ab12__photo.png", "/Users/u/.vt/files/ab12__스크린샷.png", "/a/b-c_d.e+f@g,h=i:j%k"):
        assert shell_quote_path(p) == p


def test_insert_types_the_quoted_path(monkeypatch):
    sent = []
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda pane, text: sent.append((pane, text)) or True)
    assert tmux_target.type_path_to_tmux("%3", "/x/a$(id).png")
    assert sent == [("%3", "'/x/a$(id).png'")]


# --- 제어문자: 인용으로는 못 막는다 ---------------------------------------------------
#
# `send-keys -l` 은 바이트를 그대로 pty 에 넣는다. `^U`·`\r` 같은 바이트는 셸이 따옴표를 읽기 전에 tty 줄 규칙·
# readline·ZLE 가 먼저 처리하므로, 그런 경로는 tmux 에 한 바이트도 보내지 않아야 한다. 아래는 실제로 tmux 를
# 부르는 자리(subprocess.run)를 잡아 무엇이 나가는지 본다.

CONTROL_NAMES = ["/x/x\x15touch /tmp/pwn\r", "/x/a\nb", "/x/a\rb", "/x/a\x1b[201~b", "/x/a\x7fb",
                 "/x/a\x00b", "/x/a\x03b", "/x/a\x17b", "/x/a\tb"]


@pytest.fixture
def tmux_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_target.subprocess, "run", lambda argv, **kw: calls.append(list(argv)))
    return calls


@pytest.mark.parametrize("name", CONTROL_NAMES)
def test_paths_with_control_chars_are_never_typed(tmux_calls, name):
    assert tmux_target.has_control_chars(name)
    assert tmux_target.type_path_to_tmux("%3", name) is False
    assert tmux_calls == [], "제어문자가 든 경로를 tmux 로 보냈다"


def test_safe_paths_reach_tmux_quoted_and_literal(tmux_calls):
    assert tmux_target.type_path_to_tmux("%3", "/x/a$(id) 이름.png") is True
    assert tmux_target.type_path_to_tmux("%3", "/x/plain.png") is True
    assert [c[-5:] for c in tmux_calls] == [
        ["-t", "%3", "-l", "--", "'/x/a$(id) 이름.png'"],
        ["-t", "%3", "-l", "--", "/x/plain.png"],
    ]


def test_has_control_chars_leaves_printable_text_alone():
    for p in ("/x/a b.png", "/x/스크린샷.png", "/x/it's $(id).png", "/x/\u0085nel.png", ""):
        assert not tmux_target.has_control_chars(p), repr(p)


def test_both_server_insert_paths_use_the_quoting_entry_point():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("routes/files.py", "file_store.py", "routes/peer.py"):
        text = (root / rel).read_text()
        assert "type_path_to_tmux" in text and "tmux_target.type_to_tmux(" not in text, rel


# --- 훅 등록 명령(셸이 해석한다) ------------------------------------------------------


def test_hook_commands_quote_unsafe_install_dirs_and_leave_normal_ones(tmp_path):
    from pathlib import Path
    import claude_hooks
    import codex_hooks
    for mod in (claude_hooks, codex_hooks):
        ev = next(iter(mod.EVENTS))
        normal = mod.hook_command(ev, Path("/Users/u/GitHub/farshell"))
        assert normal.startswith("/Users/u/GitHub/farshell/server/agent_hook.sh "), normal   # 이미 등록된 항목과 같다
        spaced = mod.hook_command(ev, Path("/Users/Foo Bar/farshell"))
        assert spaced.startswith("'/Users/Foo Bar/farshell/server/agent_hook.sh' "), spaced
        evil = mod.hook_command(ev, Path(tmp_path / "x$(touch PWNED)"))
        out = subprocess.run(["sh", "-c", f"set -- {evil}; printf '%s' \"$1\""], cwd=tmp_path, capture_output=True, text=True).stdout
        assert out.endswith("x$(touch PWNED)/server/agent_hook.sh") and not (tmp_path / "PWNED").exists()


def test_codex_still_recognises_its_own_quoted_entry():
    from pathlib import Path
    import codex_hooks
    ev = next(iter(codex_hooks.EVENTS))
    cmd = codex_hooks.hook_command(ev, Path("/Users/Foo Bar/farshell"))
    assert codex_hooks._script_args(cmd).split()[1] == "codex"
    assert codex_hooks._is_ours({"command": cmd})
