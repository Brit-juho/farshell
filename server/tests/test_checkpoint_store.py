"""체크포인트 켜기 상태 저장소(`checkpoint_store`) — 이 Mac 전용 파일, 기본 꺼짐, 저장소(공통 .git) 단위."""
import json
import os
import stat

import pytest

import checkpoint_store as store


@pytest.fixture
def state(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setenv("VT_STATE_DIR", str(d))
    store._cache = (None, frozenset())
    return d


def mk(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return str(p.resolve())


def test_default_is_all_off_and_makes_no_file(state, tmp_path):
    assert store.any_enabled() is False and store.is_enabled(mk(tmp_path, "r")) is False
    assert not (state / "checkpoints.json").exists()


def test_enable_disable_roundtrip_with_private_file(state, tmp_path):
    a, b = mk(tmp_path, "a"), mk(tmp_path, "b")
    assert store.set_enabled(a, True) == {"ok": True, "enabled": True, "count": 1}
    assert store.is_enabled(a) and not store.is_enabled(b) and store.any_enabled()
    f = state / "checkpoints.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(state.stat().st_mode) == 0o700
    assert json.loads(f.read_text())["repos"] == [{"key": a, "enabled": True}]
    store.set_enabled(b, True)
    store.set_enabled(a, True)                                  # 두 번 켜도 하나
    assert store.enabled_keys() == frozenset({a, b})
    store.set_enabled(a, False)
    assert store.enabled_keys() == frozenset({b})
    store.set_enabled(b, False)
    assert store.any_enabled() is False
    assert not list(state.glob("*.tmp"))


def test_cache_follows_file_changes_made_elsewhere(state, tmp_path):
    a = mk(tmp_path, "a")
    store.set_enabled(a, True)
    assert store.is_enabled(a)
    (state / "checkpoints.json").write_text(json.dumps({"version": 1, "repos": []}))     # 다른 프로세스가 바꿨다
    os.utime(state / "checkpoints.json", ns=(1, 1))
    assert store.is_enabled(a) is False


@pytest.mark.parametrize("bad", ["", "relative/path", "a\0b", None, 5])
def test_bad_keys_are_refused(state, bad):
    assert store.set_enabled(bad, True) == {"ok": False, "error": "bad_key"}


def test_corrupt_or_foreign_file_means_everything_off(state, tmp_path):
    state.mkdir()
    for text in ("not json", "[]", '{"repos": "x"}', '{"repos": [{"key": "rel", "enabled": true}, {"key": "/x", "enabled": false}, 5]}'):
        (state / "checkpoints.json").write_text(text)
        os.utime(state / "checkpoints.json", ns=(len(text), len(text)))
        assert store.enabled_keys() == frozenset(), text


def test_vanished_repositories_are_pruned_on_write(state, tmp_path):
    a, b = mk(tmp_path, "a"), mk(tmp_path, "b")
    store.set_enabled(a, True)
    os.rmdir(a)
    store.set_enabled(b, True)
    assert store.enabled_keys() == frozenset({b})


def test_limit(state, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_REPOS", 2)
    store.set_enabled(mk(tmp_path, "a"), True)
    store.set_enabled(mk(tmp_path, "b"), True)
    assert store.set_enabled(mk(tmp_path, "c"), True) == {"ok": False, "error": "too_many"}
    assert len(store.enabled_keys()) == 2
