"""세션 이름 규칙과 정확 매칭(세션 로직 점검 단계 0).

- 이름을 그대로 쓰는 생성 요청은 계정 선택 여부와 상관없이 앱 규칙을 지킨다. tmux는 `-s` 값을 서식으로
  확장해서 `#(...)`가 셸 명령으로 실행되고, 이 라우트는 기계 토큰도 부를 수 있다.
- 세션을 이름으로 찾을 때는 `=이름` 정확 매칭이다. 그냥 `-t app`은 `app-1`에도 맞는다.
"""

import asyncio

import pytest

import routes.pty as pty_route
import routes.tmux as tmux_route
import tmux_runner


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.parametrize('name', ['#(touch /tmp/x)', '#{session_id}', '-x', 'a b', 'a.b', 'a:b', '한글', 'x' * 65])
def test_create_refuses_names_outside_the_rule_before_touching_tmux(monkeypatch, name):
    calls = []
    monkeypatch.setattr(tmux_runner, 'run', lambda args, timeout=None: calls.append(list(args)) or (0, b'', b''))
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest({'name': name})))
    assert resp.status_code == 400
    assert b'invalid_name' in resp.body
    assert calls == []


@pytest.mark.parametrize('name', ['app', 'A_1', '_x', 'a-b', 'x' * 64])
def test_rule_accepts_ordinary_names(name):
    assert tmux_runner.valid_session_name(name)


def test_unique_name_still_slugs_instead_of_refusing(monkeypatch):
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (1, b'', b"can't find session") if args[0] == 'has-session' else (0, b'', b'')
    monkeypatch.setattr(tmux_runner, 'run', fake_run)

    async def fake_attach(name, cols, rows, request=None):
        return {'id': 's', 'name': name, 'tmux_session': name}
    monkeypatch.setattr(tmux_route, '_attach_tmux', fake_attach)
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest({'name': '한글 이름 #(x)', 'unique_name': True})))
    assert isinstance(resp, dict)
    new = next(a for a in calls if a[0] == 'new-session')
    assert '#' not in new[new.index('-s') + 1]


def test_exact_target_keeps_ids_and_marks_names():
    assert tmux_runner.exact_target('app') == '=app'
    assert tmux_runner.exact_target('$3') == '$3'
    assert tmux_runner.exact_target('=app') == '=app'


def test_has_session_asks_tmux_for_an_exact_name(monkeypatch):
    seen = []
    monkeypatch.setattr(tmux_runner, 'run', lambda args, timeout=None: seen.append(list(args)) or (1, b'', b''))
    tmux_runner.has_session('app')
    assert seen[-1] == ['has-session', '-t', '=app']
    tmux_runner.session_state('app')
    assert seen[-1] == ['has-session', '-t', '=app']


@pytest.mark.parametrize('name', ['-x', '#(id)', 'x' * 65])
def test_rename_refuses_names_outside_the_rule(monkeypatch, name):
    info = type('I', (), {'tmux_name': 'old', 'name': 'old'})()
    monkeypatch.setattr(pty_route.session_store, 'get', lambda sid: info)
    calls = []
    monkeypatch.setattr(tmux_runner, 'run', lambda args, timeout=None: calls.append(list(args)) or (0, b'', b''))
    resp = asyncio.run(pty_route.rename_session('sid', _FakeRequest({'name': name})))
    assert resp.status_code == 400
    assert not any(a[0] == 'rename-session' for a in calls)


def test_rename_targets_the_old_name_exactly(monkeypatch):
    info = type('I', (), {'tmux_name': 'app', 'name': 'app', 'session_id': 'sid'})()
    monkeypatch.setattr(pty_route.session_store, 'get', lambda sid: info)
    monkeypatch.setattr(pty_route.session_store, 'find_all_by_tmux_name', lambda n: [])
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (1, b'', b"can't find session") if args[0] == 'has-session' else (0, b'', b'')
    monkeypatch.setattr(tmux_runner, 'run', fake_run)
    asyncio.run(pty_route.rename_session('sid', _FakeRequest({'name': 'app2'})))
    rename = next(a for a in calls if a[0] == 'rename-session')
    assert rename == ['rename-session', '-t', '=app', 'app2']
    assert ['has-session', '-t', '=app2'] in calls
