"""D7: session_store.py 역인덱스 단위 테스트."""

from session_store import SessionStore


def test_find_by_tmux_name_o1():
    s = SessionStore()
    s.add("sid1", name="test")
    s.update_tmux_name("sid1", "tmux-a")
    result = s.find_by_tmux_name("tmux-a")
    assert result is not None
    assert result.session_id == "sid1"


def test_update_tmux_name_removes_old_index():
    s = SessionStore()
    s.add("sid1")
    s.update_tmux_name("sid1", "tmux-a")
    s.update_tmux_name("sid1", "tmux-b")
    assert s.find_by_tmux_name("tmux-a") is None
    assert s.find_by_tmux_name("tmux-b").session_id == "sid1"


def test_remove_clears_index():
    s = SessionStore()
    s.add("sid1")
    s.update_tmux_name("sid1", "tmux-x")
    s.remove("sid1")
    assert s.find_by_tmux_name("tmux-x") is None
    assert s.get("sid1") is None


def test_find_nonexistent_returns_none():
    s = SessionStore()
    assert s.find_by_tmux_name("ghost") is None


def test_multiple_sessions_independent():
    s = SessionStore()
    s.add("sid1")
    s.add("sid2")
    s.update_tmux_name("sid1", "tmux-1")
    s.update_tmux_name("sid2", "tmux-2")
    assert s.find_by_tmux_name("tmux-1").session_id == "sid1"
    assert s.find_by_tmux_name("tmux-2").session_id == "sid2"


def test_none_tmux_name_does_not_corrupt_index():
    s = SessionStore()
    s.add("sid1")
    s.update_tmux_name("sid1", "tmux-a")
    s.update_tmux_name("sid1", None)  # tmux_name 초기화
    assert s.find_by_tmux_name("tmux-a") is None
