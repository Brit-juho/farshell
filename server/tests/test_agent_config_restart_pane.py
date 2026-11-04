"""설정 변경 재시작의 「터미널 점유 확인」 — 종료된 pane 의 빈 `#{pane_pid}` 를 다룬다(2026-11-04).

tmux 3.8 은 종료된 pane(remain-on-exit) 의 `#{pane_pid}` 를 빈 줄로 돌려준다. 예전 코드는
`int((출력 or "0").strip())` 라서 "\\n" 이 `or "0"` 을 통과해 `int("")` 로 터졌고, 그 ValueError 가 그대로
사용자에게 보이는 사유가 됐다. 「pid 를 모름」을 종료된 pane(확인할 것이 없다)과 살아 있는 pane(확인을 못 했다)으로
가르는 것이 이 테스트의 목적이다.
"""
import os
import shutil
import subprocess
import time

import pytest

import agent_config_restart as restart
import hook_identity
import share_identity
import tmux_runner


def _fake_tmux(monkeypatch, output, calls=None):
    def fake(args, timeout=2.0):
        if calls is not None:
            calls.append(args)
        return output
    monkeypatch.setattr(tmux_runner, "run_text", fake)


# --- _pane_state: 해석 ------------------------------------------------------------------


@pytest.mark.parametrize("text, want", [
    ("0\t45742\n", (False, 45742)),     # 살아 있는 pane
    ("1\t\n", (True, None)),            # tmux 3.8 의 종료된 pane — 예전 코드가 터진 입력
    ("1\t45742\n", (True, 45742)),      # 옛 tmux 는 종료된 pane 도 옛 pid 를 준다
    ("0\t\n", (False, None)),           # 살아 있는데 pid 가 비었다 = 확인 못 함
    ("0\t0\n", (False, None)),
    ("0\t-5\n", (False, None)),
    ("0\tabc\n", (False, None)),
    ("0\t４５\n", (False, None)),       # 전각 숫자는 pid 가 아니다
    ("0\n", (False, None)),             # 칸이 모자란 출력
    ("\n", (False, None)),
    ("", (False, None)),
])
def test_pane_state_parses_every_shape_of_tmux_output(monkeypatch, text, want):
    _fake_tmux(monkeypatch, text)
    assert restart._pane_state("%1") == want


def test_pane_state_raises_when_tmux_itself_fails(monkeypatch):
    _fake_tmux(monkeypatch, None)
    with pytest.raises(ValueError):
        restart._pane_state("%1")


def test_pane_state_asks_tmux_exactly_once(monkeypatch):
    calls = []
    _fake_tmux(monkeypatch, "0\t12\n", calls)
    restart._pane_state("%1")
    assert len(calls) == 1
    assert "#{pane_dead}\t#{pane_pid}" in calls[0]


# --- _ensure_pane_free: 분기 ------------------------------------------------------------

SHELL = (1, 1, 1, "zsh")


def _world(monkeypatch, state, table, *, reaches=lambda pid, target: False, zombies=()):
    monkeypatch.setattr(restart, "_pane_state", lambda pane: state)
    monkeypatch.setattr(hook_identity, "_ps_table", lambda: table)
    monkeypatch.setattr(hook_identity, "_ps_zombies", lambda: set(zombies))
    monkeypatch.setattr(share_identity, "chain_reaches", reaches)


def test_dead_pane_is_free_without_looking_at_processes(monkeypatch):
    _world(monkeypatch, (True, None), None)    # ps 표가 없어도(None) 확인할 것이 없다
    restart._ensure_pane_free("%1")


def test_live_pane_with_unreadable_pid_stops_instead_of_guessing(monkeypatch):
    _world(monkeypatch, (False, None), {900: (1, 900, 900, "vim")}, reaches=lambda pid, target: True)
    with pytest.raises(ValueError):
        restart._ensure_pane_free("%1")


def test_live_pane_without_ps_table_stops(monkeypatch):
    _world(monkeypatch, (False, 10), None)
    with pytest.raises(ValueError):
        restart._ensure_pane_free("%1")


def test_foreground_process_under_the_pane_blocks_the_restart(monkeypatch):
    table = {10: (1, 10, 900, "zsh"), 900: (10, 900, 900, "vim")}
    _world(monkeypatch, (False, 10), table, reaches=lambda pid, target: (pid, target) == (900, 10))
    with pytest.raises(ValueError):
        restart._ensure_pane_free("%1")


@pytest.mark.parametrize("table, zombies", [
    ({900: (10, 900, 900, "zsh")}, ()),                    # 셸은 점유가 아니다
    ({900: (10, 900, 901, "vim")}, ()),                    # 전경 프로세스 그룹이 아니다
    ({900: (10, 900, 900, "codex")}, (900,)),              # 수거 전 좀비
    ({901: (1, 901, 901, "vim")}, ()),                     # 다른 터미널의 프로세스(pane 아래가 아니다)
])
def test_processes_that_do_not_hold_the_pane_are_ignored(monkeypatch, table, zombies):
    _world(monkeypatch, (False, 10), table, reaches=lambda pid, target: pid == 900, zombies=zombies)
    restart._ensure_pane_free("%1")


# --- 실제 tmux: 살아 있는 pane 과 종료된 pane ----------------------------------------------


@pytest.fixture
def isolated_tmux(monkeypatch):
    if not shutil.which("tmux"):
        pytest.skip("tmux required")
    socket = "fsh-pane-state-" + str(os.getpid())
    monkeypatch.setenv("VT_TMUX_SOCKET", socket)
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", socket)
    yield socket
    tmux_runner.run(["kill-server"])


def test_real_tmux_alive_and_dead_panes(isolated_tmux):
    rc, _, error = tmux_runner.run(["new-session", "-d", "-s", "alive", "sleep 60"])
    assert rc == 0, error
    dead, pid = restart._pane_state("alive")
    assert dead is False and isinstance(pid, int) and pid > 0

    # 첫 프로세스가 잠깐 살아 있다 끝나게 해서, 그 사이에 remain-on-exit 를 켠다(켜기 전에 끝나면 세션째 사라진다).
    rc, _, error = tmux_runner.run(["new-session", "-d", "-s", "gone", "sleep 1"])
    assert rc == 0, error
    rc, _, error = tmux_runner.run(["set-option", "-w", "-t", "gone", "remain-on-exit", "on"])
    assert rc == 0, error
    for _ in range(100):                      # 종료를 기다린다
        if restart._pane_state("gone")[0]:
            break
        time.sleep(.05)
    dead, pid = restart._pane_state("gone")
    assert dead is True                       # pid 는 tmux 버전에 따라 None 이거나 옛 pid — 어느 쪽이든 터지지 않는다
    restart._ensure_pane_free("gone")         # 종료된 pane 은 통과한다
