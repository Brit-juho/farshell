"""카탈로그 관측의 견고성(세션 로직 점검 단계 0, C1·C2).

- 재부팅 뒤 소켓 파일이 없으면 tmux는 `error connecting to … (No such file or directory)`를 낸다. 그것도
  「서버 없음」으로 확정해야 직전 런타임이 몇 시간씩 「상태 모름」에 남지 않는다.
- 끝나고 남겨 둔 pane(pid 빈 값)이나 `list-sessions`/`list-panes` 사이의 경합 한 번이 스냅샷 전체를
  불완전으로 만들면 안 된다.
- 다시 관측된 끝난 런타임(신원 재사용)은 그 세션만 건너뛴다. 같은 관측의 다른 기록은 남는다.
"""

import pytest

import tmux_runner


@pytest.fixture
def private_catalog(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(home / 'state'))
    monkeypatch.setenv('VT_BROWSE_ROOTS', str(home))
    from session_catalog import db
    db.snapshot()
    return home


@pytest.mark.parametrize('err,absent', [
    (b'no server running on /tmp/tmux-501/vt\n', True),
    (b'error connecting to /tmp/tmux-501/vt (No such file or directory)\n', True),
    (b'error connecting to /tmp/tmux-501/vt (Permission denied)\n', False),
    (b'some timeout', False),
])
def test_server_absent_only_for_definite_answers(err, absent):
    assert tmux_runner.server_absent(1, err) is absent
    assert tmux_runner.server_absent(124, err) is False


def _fake(monkeypatch, responses):
    """args[0] → 차례로 돌려줄 (rc, out, err) 목록."""
    calls = []

    def run(args, timeout=None):
        calls.append(args[0])
        queue = responses[args[0]]
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        return value
    monkeypatch.setattr(tmux_runner, 'run', run)
    monkeypatch.setattr(tmux_runner, 'run_text', lambda args, timeout=None: (lambda r: r[1].decode() if r[0] == 0 else None)(run(args, timeout)))
    return calls


def test_missing_socket_is_a_complete_empty_snapshot(monkeypatch):
    from session_catalog import inventory
    _fake(monkeypatch, {'list-sessions': [(1, b'', b'error connecting to /tmp/x (No such file or directory)\n')]})
    assert inventory.observe() == {'complete': True, 'epoch': None, 'sessions': []}


def _identity(monkeypatch, tmp_path):
    from session_catalog import inventory
    sock = tmp_path / 'sock'
    sock.write_text('')
    monkeypatch.setattr(inventory.subprocess, 'run', lambda *a, **k: type('R', (), {'returncode': 0, 'stdout': b'Mon Jan 1'})())
    return f'1234\t{sock}'.encode()


def test_dead_pane_does_not_spoil_the_snapshot(monkeypatch, tmp_path):
    from session_catalog import inventory
    ident = _identity(monkeypatch, tmp_path)
    _fake(monkeypatch, {
        'list-sessions': [(0, b'$1\t100\tapp\tlocal\n$2\t200\tgone\t\n', b'')],
        'display-message': [(0, ident, b'')],
        'list-panes': [(0, b'$1\t%1\t555\t@1\t11\t/r\tzsh\t0\t\n$2\t%2\t\t@2\t11\t/r\tclaude\t1\t3\n', b'')],
    })
    snap = inventory.observe()
    assert snap['complete'] is True
    gone = next(s for s in snap['sessions'] if s['name'] == 'gone')
    assert gone['panes'][0]['pid'] is None and gone['panes'][0]['dead'] is True and gone['panes'][0]['dead_status'] == 3


def test_live_pane_without_pid_is_still_incomplete(monkeypatch, tmp_path):
    from session_catalog import inventory
    ident = _identity(monkeypatch, tmp_path)
    _fake(monkeypatch, {
        'list-sessions': [(0, b'$1\t100\tapp\tlocal\n', b'')],
        'display-message': [(0, ident, b'')],
        'list-panes': [(0, b'$1\t%1\t\t@1\t11\t/r\tzsh\t0\t\n', b'')],
    })
    assert inventory.observe()['complete'] is False


def test_race_between_the_two_listings_is_retried_once(monkeypatch, tmp_path):
    from session_catalog import inventory
    ident = _identity(monkeypatch, tmp_path)
    calls = _fake(monkeypatch, {
        # 첫 번째: 세션 $2가 list-panes에는 아직 없다(방금 생김). 두 번째 읽기는 맞는다.
        'list-sessions': [(0, b'$1\t100\tapp\t\n$2\t200\tnew\t\n', b''), (0, b'$1\t100\tapp\t\n$2\t200\tnew\t\n', b'')],
        'display-message': [(0, ident, b'')],
        'list-panes': [(0, b'$1\t%1\t5\t@1\t11\t/r\tzsh\t0\t\n', b''),
                       (0, b'$1\t%1\t5\t@1\t11\t/r\tzsh\t0\t\n$2\t%2\t6\t@2\t11\t/r\tzsh\t0\t\n', b'')],
    })
    snap = inventory.observe()
    assert snap['complete'] is True and {s['name'] for s in snap['sessions']} == {'app', 'new'}
    assert calls.count('list-sessions') == 2


def test_identity_conflict_skips_only_that_session(private_catalog):
    from session_catalog import inventory, db
    def item(key, name):
        return {'physical_key': key, 'tmux_id': key.split(':')[0], 'name': name, 'origin': 'external', 'cwd': '', 'panes': []}
    inventory.reconcile({'complete': True, 'epoch': 'e', 'sessions': [item('$1:1', 'reused'), item('$2:2', 'other')]})
    inventory.reconcile({'complete': True, 'epoch': 'e', 'sessions': [item('$2:2', 'other')]})   # reused 종료
    # 같은 epoch·키가 다시 보이고(신원 재사용), 동시에 other가 끝났다.
    inventory.reconcile({'complete': True, 'epoch': 'e', 'sessions': [item('$1:1', 'reused'), item('$3:3', 'fresh')]})
    rows = {r['tmux_name']: r for r in db.snapshot()['sessions']}
    assert rows['other']['execution_state'] == 'stopped'      # 같은 관측의 종료 기록은 되돌려지지 않는다
    assert rows['fresh']['execution_state'] == 'running'      # 새 세션 입양도 계속된다
    assert rows['reused']['execution_state'] == 'stopped'     # 충돌한 런타임은 되살리지 않는다
