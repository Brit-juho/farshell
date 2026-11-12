"""대기 지시는 세션 이름이 아니라 열쇠로 대상을 찾는다(세션 로직 점검 단계 1, N2).

넣을 때 그 이름의 세션 열쇠(`$id:생성시각`)를 적어 두고, 배달할 때는 열쇠로 **지금** 이름을 찾는다.
이름이 바뀌어도 따라가고, 그 세션이 끝나면 같은 이름의 새 세션이 생겨도 그쪽으로 가지 않는다.
"""

import pytest

import queue_store
import tmux_runner


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(queue_store, "_state_dir", lambda: tmp_path / "state")
    live = {"$3:100": "app"}
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: dict(live))
    return live


def test_add_records_the_key_of_the_named_session(store):
    item = queue_store.add("hello", {"session": "app"})["item"]
    assert item["target"] == {"session": "app", "key": "$3:100"}


def test_rename_is_followed(store):
    item = queue_store.add("hello", {"session": "app"})["item"]
    store["$3:100"] = "app-renamed"
    assert queue_store.target_session(item) == "app-renamed"


def test_an_ended_session_is_not_replaced_by_a_new_one_with_the_same_name(store):
    item = queue_store.add("hello", {"session": "app"})["item"]
    store.clear()
    store["$9:200"] = "app"          # 끝난 뒤 같은 이름으로 새 세션
    assert queue_store.target_session(item) == queue_store.GONE
    claimed = queue_store.claim_next(session="app", session_scoped=True)
    assert claimed is None, "끝난 세션 몫이 같은 이름의 새 세션으로 가면 안 된다"


def test_untargeted_and_missing_sessions_keep_the_old_behaviour(store):
    assert queue_store.add("a")["item"]["target"] is None
    item = queue_store.add("b", {"session": "nobody"})["item"]
    assert item["target"] == {"session": "nobody"}
    assert queue_store.target_session(item) == "nobody"


def test_remote_targets_never_carry_a_local_key(store):
    item = queue_store.add("c", {"session": "app", "host": "gpu", "key": "$3:100"})["item"]
    assert "key" not in item["target"]


def test_unreadable_tmux_falls_back_to_the_written_name(store, monkeypatch):
    item = queue_store.add("hello", {"session": "app"})["item"]
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: None)
    assert queue_store.target_session(item) == "app"


def test_live_session_keys_parses_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_runner, "_KEYS_CACHE", (0.0, None))
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: calls.append(args) or (0, b"$1:10\tdev\n$2:20\tapp\nbad\n", b""))
    assert tmux_runner.live_session_keys() == {"$1:10": "dev", "$2:20": "app"}
    tmux_runner.live_session_keys()
    assert len(calls) == 1
    assert tmux_runner.key_for_name("app") == "$2:20" and len(calls) == 2
