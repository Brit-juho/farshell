"""2.1 D2 — 세션↔워크트리 소속을 tmux `@fsh_wt` 옵션으로 기록.

`server/session_store.py`는 메모리 전용이라 서버 재시작에 소속 정보가
날아간다(PTY도 같이 죽으므로 지금까지는 문제가 아니었지만, tmux 세션은
서버 재시작을 그대로 견딘다 — 그 사이의 간극이 이 기능의 존재 이유다).
`_sessions_for_path`가 태그를 1차로 보고, 없을 때만 cwd 추론으로 폴백하며
그 결과를 self-heal로 다시 태그해 두는지를 검증한다.
"""

from pathlib import Path

import worktree
import tmux_runner


def _pane(session, path, wt_tag=""):
    return tmux_runner.PaneInfo(session=session, command="zsh", pid=1, path=path, wt_tag=wt_tag)


def test_tag_match_wins_without_touching_cwd(tmp_path, monkeypatch):
    """태그가 있으면 cwd를 아예 안 본다 — cwd가 다른 곳을 가리켜도 태그가 이긴다."""
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    panes = [_pane("wt-a", str(other_dir), wt_tag="abc123456789")]

    calls = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: calls.append(a))

    names = worktree._sessions_for_path(tmp_path, panes, wt_id="abc123456789")
    assert names == ["wt-a"]
    assert not calls, "태그가 이미 맞으면 다시 심을 필요가 없다"


def test_fallback_to_cwd_and_self_heals(tmp_path, monkeypatch):
    """태그가 없는 세션은 cwd로 찾아내고, 찾아낸 자리에서 태그를 심는다."""
    panes = [_pane("wt-legacy", str(tmp_path))]

    calls = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: calls.append(a) or True)

    names = worktree._sessions_for_path(tmp_path, panes, wt_id="deadbeef0001")
    assert names == ["wt-legacy"]
    assert calls == [("wt-legacy", "@fsh_wt", "deadbeef0001")]


def test_session_tagged_for_another_worktree_is_not_stolen(tmp_path, monkeypatch):
    """다른 워크트리 태그가 붙은 세션은 cwd가 겹쳐도 폴백 대상에서 제외한다."""
    panes = [_pane("wt-other", str(tmp_path), wt_tag="other000001")]
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(
        AssertionError("남의 태그를 훔쳐 오려 했다")))

    names = worktree._sessions_for_path(tmp_path, panes, wt_id="mine00000001")
    assert names == []


def test_no_sessions_no_set_option_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(
        AssertionError("세션이 없는데 set_option을 불렀다")))
    assert worktree._sessions_for_path(tmp_path, [], wt_id="none00000001") == []


def test_open_worktree_tags_newly_created_session(monkeypatch, tmp_path):
    wt = {
        "id": "feedface0001",
        "sessions": [],
        "repoName": "farshell",
        "branch": "main",
        "path": str(tmp_path),
    }
    monkeypatch.setattr(worktree, "find_by_id", lambda wt_id, force=True: wt)
    monkeypatch.setattr(worktree.tmux_runner, "has_session", lambda name: False)
    monkeypatch.setattr(worktree.tmux_runner, "run", lambda args, timeout=None: (0, b"", b""))
    monkeypatch.setattr(worktree, "invalidate_cache", lambda: None)

    tagged = []
    monkeypatch.setattr(worktree.tmux_runner, "set_option", lambda s, k, v: tagged.append((s, k, v)) or True)

    result = worktree.open_worktree("feedface0001")
    assert result["created"] is True
    assert tagged == [("wt-farshell-main", "@fsh_wt", "feedface0001")]


def test_open_worktree_skips_tagging_when_session_reused(monkeypatch, tmp_path):
    """이미 세션이 있으면(`wt["sessions"]`) 새로 만들지 않으니 태그도 새로 안 심는다 —
    `_sessions_for_path`의 self-heal이 이미 책임진 자리다."""
    wt = {"id": "feedface0002", "sessions": ["wt-existing"], "repoName": "x", "branch": "y", "path": str(tmp_path)}
    monkeypatch.setattr(worktree, "find_by_id", lambda wt_id, force=True: wt)
    monkeypatch.setattr(worktree.tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(
        AssertionError("재사용 세션엔 여기서 다시 태그하면 안 된다")))

    result = worktree.open_worktree("feedface0002")
    assert result == {"ok": True, "tmux_session": "wt-existing", "created": False}
