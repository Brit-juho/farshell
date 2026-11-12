"""Resume launch: by default the CLI replaces the shell (the session ends with it, a fast failure stays on screen);
with the setting off it is typed into a shell that survives it."""
import json
import os
import shutil
import time

import pytest

from session_catalog import lifecycle
import tmux_runner


@pytest.mark.parametrize('control', ['\n', '\r', '\t', '\x03', '\x1b', '\x7f'])
def test_terminal_control_bytes_never_reach_a_shell(monkeypatch, control):
    def unexpected(*args, **kwargs):
        pytest.fail('must reject before creating a pane')
    monkeypatch.setattr(tmux_runner, 'run', unexpected)
    assert lifecycle._launch_in_shell('sample', '/tmp', ['echo', 'a' + control + 'b'], None, None) is False


@pytest.mark.parametrize('failure', ['create', 'pane', 'literal', 'enter'])
def test_launch_stops_on_uncertain_step(monkeypatch, failure):
    calls = []

    def run(args, *rest, **kwargs):
        calls.append(args)
        step = len(calls)
        if failure == 'pane' and step == 2:
            return 0, b'not-a-pane\n', b''
        if step == {'create': 1, 'literal': 3, 'enter': 4}.get(failure):
            return 124, b'', b'timeout'
        return 0, b'%42\n', b''

    monkeypatch.setattr(tmux_runner, 'run', run)
    assert lifecycle._launch_in_shell('sample', '/tmp', ['echo', 'hello'], None, None) is False
    assert len(calls) == {'create': 1, 'pane': 2, 'literal': 3, 'enter': 4}[failure]
    if len(calls) > 2:
        assert calls[2] == ['send-keys', '-t', '%42', '-l', '--', 'echo hello']


@pytest.mark.parametrize('shell', ['bash', 'zsh', 'fish'])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_real_cli_exit_preserves_shell(tmp_path, monkeypatch, shell, exit_code):
    binary = shutil.which(shell)
    if not binary or not shutil.which('tmux'):
        pytest.skip(f'{shell} or tmux unavailable')
    socket = 'fsh-resume-test-' + os.urandom(8).hex()
    config = tmp_path / 'tmux.conf'
    config.write_text(f'set -g default-shell {binary}\nset -g status off\n')
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_SOCKET', socket)
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_CONF', str(config))
    # A delayed startup exercises queued terminal input before the prompt exists.
    (tmp_path / '.bash_profile').write_text('sleep 0.2\n')
    (tmp_path / '.zshrc').write_text('sleep 0.2\n')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ZDOTDIR', str(tmp_path))
    monkeypatch.delenv('VT_TOKEN', raising=False)
    monkeypatch.setenv('VT_CONFIG', str(tmp_path / 'vt.env'))
    monkeypatch.setenv('VT_WORKSPACE_PATH', str(tmp_path / 'workspace.json'))
    result = tmp_path / 'received.json'
    cli = tmp_path / 'sample cli.py'
    cli.write_text('import json,sys\nfrom pathlib import Path\n'
                   'Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n'
                   f'sys.exit({exit_code})\n')
    import sys
    values = ['a b', "quote'and\"double", '$(touch INJECTED)', '; touch INJECTED', 'bypassPermissions']
    try:
        assert lifecycle._launch_in_shell('sample', tmp_path, [sys.executable, str(cli), str(result), *values], None, None)
        deadline = time.monotonic() + 8
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert json.loads(result.read_text()) == values
        # Send another command after CLI termination: success proves a usable shell.
        marker = tmp_path / 'shell-returned'
        import shlex
        time.sleep(0.1)
        assert tmux_runner.run(['send-keys', '-t', '=sample:', '-l', '--', 'touch ' + shlex.quote(str(marker))])[0] == 0
        assert tmux_runner.run(['send-keys', '-t', '=sample:', 'Enter'])[0] == 0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        assert not (tmp_path / 'INJECTED').exists()
        assert tmux_runner.run(['has-session', '-t', '=sample'])[0] == 0
    finally:
        tmux_runner.run(['kill-server'])


@pytest.mark.parametrize('exit_code', [0, 7])
def test_exec_launch_ends_with_the_cli_and_keeps_a_failure_on_screen(tmp_path, monkeypatch, exit_code):
    """기본 실행 방식: CLI가 셸을 대신한다. 정상 종료하면 세션이 끝나고(자동 보관 대상), 실패하면 화면이 남는다.
    인자는 셸을 거치지 않으므로 따옴표·`$(...)`가 그대로 전달된다. PATH는 넘긴 값으로 실행된다."""
    if not shutil.which('tmux'):
        pytest.skip('tmux unavailable')
    import sys
    socket = 'fsh-resume-exec-' + os.urandom(8).hex()
    config = tmp_path / 'tmux.conf'
    config.write_text('set -g status off\n')
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_SOCKET', socket)
    monkeypatch.setattr(tmux_runner, 'VT_TMUX_CONF', str(config))
    monkeypatch.setenv('HOME', str(tmp_path))
    result = tmp_path / 'received.json'
    cli = tmp_path / 'cli.py'
    cli.write_text('import json,os,sys\nfrom pathlib import Path\n'
                   'Path(sys.argv[1]).write_text(json.dumps({"args": sys.argv[2:], "path": os.environ.get("PATH")}))\n'
                   f'sys.exit({exit_code})\n')
    values = ['a b', "quote'and\"double", '$(touch INJECTED)']
    try:
        assert lifecycle._launch_exec('sample', tmp_path, [sys.executable, str(cli), str(result), *values], '/custom/bin:/usr/bin:/bin', None)
        deadline = time.monotonic() + 8
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        got = json.loads(result.read_text())
        assert got['args'] == values and got['path'] == '/custom/bin:/usr/bin:/bin'
        assert not (tmp_path / 'INJECTED').exists()
        alive = None
        while time.monotonic() < deadline:
            alive = tmux_runner.run(['has-session', '-t', '=sample'])[0] == 0
            if alive == (exit_code != 0):
                break
            time.sleep(0.05)
        if exit_code == 0:
            assert alive is False, '정상 종료하면 세션도 끝난다'
        else:
            assert alive is True
            dead = tmux_runner.run_text(['display-message', '-p', '-t', '=sample:', '#{pane_dead} #{pane_dead_status}'])
            assert dead.strip() == f'1 {exit_code}', '실패는 화면에 남는다'
    finally:
        tmux_runner.run(['kill-server'])


def test_path_goes_after_env_unset_options():
    argv = ['env', '-u', 'A', '-u', 'B', 'CLAUDE_CONFIG_DIR=/r', '/bin/claude', '--resume', 'x']
    assert lifecycle._with_path(argv, '/p') == ['env', '-u', 'A', '-u', 'B', 'PATH=/p', 'CLAUDE_CONFIG_DIR=/r', '/bin/claude', '--resume', 'x']
    assert lifecycle._with_path(['/bin/x'], '/p') == ['env', 'PATH=/p', '/bin/x']
    assert lifecycle._with_path(['/bin/x'], None) == ['/bin/x']


def test_an_account_resume_records_its_profile_like_a_new_account_session(monkeypatch):
    """계정으로 이어서 시작한 세션도 레지스트리에 남긴다 — 없으면 그 세션에서 위임한 자식이 거절된다."""
    import session_profile
    recorded = []
    monkeypatch.setattr(session_profile, 'spawn', lambda *a: (0, '', {'cleared_env': []}))
    monkeypatch.setattr(session_profile, 'record', lambda name, selection: recorded.append((name, selection)))
    monkeypatch.setattr(lifecycle.tmux_runner, 'run_text', lambda *a, **k: '%9\n')
    assert lifecycle._spawn_shell_session('app', '/tmp', 'SEL', None) == '%9'
    assert recorded == [('app', 'SEL')]
