"""음성 입력 대상 고정은 세션 열쇠로 지금 이름을 찾는다(세션 로직 점검 단계 1, N2)."""

import tmux_target
import tmux_runner


def test_old_one_line_file_still_works(tmp_path):
    f = tmp_path / "voice_target"
    f.write_text("dev\n")
    assert tmux_target.read_voice_target_lock(f) == "dev"


def test_key_follows_a_rename_and_drops_an_ended_session(tmp_path, monkeypatch):
    f = tmp_path / "voice_target"
    f.write_text("dev\n$2:30\n")
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: {"$2:30": "dev-renamed"})
    assert tmux_target.read_voice_target_lock(f) == "dev-renamed"
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: {"$9:99": "dev"})
    assert tmux_target.read_voice_target_lock(f) is None, "끝난 세션 대신 같은 이름의 새 세션으로 가지 않는다"
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: None)
    assert tmux_target.read_voice_target_lock(f) == "dev"


def test_written_text_carries_the_key(monkeypatch):
    monkeypatch.setattr(tmux_runner, "key_for_name", lambda n: "$2:30" if n == "dev" else None)
    assert tmux_target.voice_target_lock_text("dev") == "dev\n$2:30\n"
    assert tmux_target.voice_target_lock_text("gone") == "gone\n"
