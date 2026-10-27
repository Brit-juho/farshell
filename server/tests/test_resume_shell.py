"""A resumed CLI returns to its shell, including after a failed command."""
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
    assert lifecycle._launch_resume('sample', '/tmp', ['echo', 'a' + control + 'b']) is False


@pytest.mark.parametrize('failure', ['create', 'pane', 'literal', 'enter'])
def test_launch_stops_on_uncertain_step(monkeypatch, failure):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        step = len(calls)
        if failure == 'pane' and step == 1:
            return 0, b'not-a-pane\n', b''
        if step == {'create': 1, 'literal': 2, 'enter': 3}.get(failure):
            return 124, b'', b'timeout'
        return 0, b'%42\n', b''

    monkeypatch.setattr(tmux_runner, 'run', run)
    assert lifecycle._launch_resume('sample', '/tmp', ['echo', 'hello']) is False
    assert len(calls) == {'create': 1, 'pane': 1, 'literal': 2, 'enter': 3}[failure]
    if len(calls) > 1:
        assert calls[1] == ['send-keys', '-t', '%42', '-l', '--', 'echo hello']


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
        assert lifecycle._launch_resume('sample', tmp_path, [sys.executable, str(cli), str(result), *values])
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
