"""터미널 안에서 바꾼 tmux 세션 이름을 웹 세션이 따라간다(세션 로직 점검 단계 1).

예전에는 웹 세션 레코드에 옛 이름이 남아 `GET /api/sessions`가 「없는 세션」으로 보고 웹 PTY를 지웠다.
세션 id(`$N`)는 이름이 바뀌어도 그대로라, 기억해 둔 id가 살아 있고 이름만 다르면 개명으로 본다.
"""

import types

import pytest

import deps


@pytest.fixture
def web_session(monkeypatch):
    store = deps.session_store
    sid = "web-rename-1"
    store.add(sid, name="old")
    store.update_tmux_name(sid, "old")
    fake = types.SimpleNamespace(tty="/dev/ttys999")
    monkeypatch.setitem(deps.pty_mgr._sessions, sid, fake)
    yield sid
    store.remove(sid)
    deps._pty_tmux_uid.pop(sid, None)
    deps._pty_tmux_now.pop(sid, None)


def test_first_kick_learns_the_id_then_a_rename_is_followed(web_session):
    deps.apply_tmux_state([("$7", "old")], {"/dev/ttys999": "old"})
    assert deps._pty_tmux_uid[web_session] == "$7"
    deps.apply_tmux_state([("$7", "new")], {"/dev/ttys999": "new"})
    info = deps.session_store.get(web_session)
    assert info.tmux_name == "new" and info.name == "new"
    assert deps.session_store.find_by_tmux_name("new").session_id == web_session
    assert deps.session_store.find_by_tmux_name("old") is None
    assert web_session in deps.pty_mgr.sessions, "개명은 세션 종료가 아니다"


def test_a_session_that_really_ended_is_not_treated_as_a_rename(web_session, monkeypatch):
    ended = []
    monkeypatch.setattr(deps, "_end_pty_of_dead_session", lambda sid: ended.append(sid))
    deps.apply_tmux_state([("$7", "old"), ("$8", "other")], {"/dev/ttys999": "old"})
    # $7이 끝나고 클라이언트가 $8(other)로 옮겨졌다(detach-on-destroy off).
    deps.apply_tmux_state([("$8", "other")], {"/dev/ttys999": "other"})
    assert deps.session_store.get(web_session).tmux_name == "old"
    assert ended == [web_session]


def test_a_custom_web_label_is_kept_when_the_tmux_name_changes(web_session):
    deps.session_store.get(web_session).name = "내 라벨"
    deps.apply_tmux_state([("$7", "old")], {"/dev/ttys999": "old"})
    deps.apply_tmux_state([("$7", "new")], {"/dev/ttys999": "new"})
    info = deps.session_store.get(web_session)
    assert info.tmux_name == "new" and info.name == "내 라벨"
