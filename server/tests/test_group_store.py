"""세션 그룹 이름·순서 저장 — ADR-29 A단계.

소속(어느 세션이 이 그룹인가)은 여기서 다루지 않는다 — 그건 tmux
`@fsh_grp`의 몫이고, 이 파일은 이름·순서만 관리한다(group_store.py 머리말).
"""

from __future__ import annotations

import json

import pytest

import group_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    return group_store


def test_empty_store_has_no_groups(store):
    assert store.list_groups() == []


def test_set_label_creates_a_record(store):
    result = store.set_label("abc123456789", "내 프로젝트")
    assert result == {"ok": True, "id": "abc123456789", "label": "내 프로젝트"}
    assert store.list_groups() == [{"id": "abc123456789", "label": "내 프로젝트"}]


def test_set_label_on_existing_group_renames_in_place(store):
    store.set_label("abc123456789", "이름1")
    store.set_label("abc123456789", "이름2")
    groups = store.list_groups()
    assert len(groups) == 1, "같은 id를 두 번 만지면 레코드가 늘면 안 된다"
    assert groups[0]["label"] == "이름2"


def test_set_label_rejects_empty_id_or_label(store):
    assert store.set_label("", "이름")["ok"] is False
    assert store.set_label("abc123456789", "")["ok"] is False
    assert store.set_label("abc123456789", "   ")["ok"] is False


def test_get_label_returns_none_when_unset(store):
    assert store.get_label("nope00000000") is None
    store.set_label("nope00000000", "지음")
    assert store.get_label("nope00000000") == "지음"


def test_set_order_replaces_the_whole_order(store):
    store.set_label("aaa000000001", "A")
    store.set_label("bbb000000002", "B")
    result = store.set_order(["bbb000000002", "aaa000000001"])
    assert result == {"ok": True, "order": ["bbb000000002", "aaa000000001"]}
    assert [g["id"] for g in store.list_groups()] == ["bbb000000002", "aaa000000001"]


def test_set_order_drops_labels_for_groups_no_longer_listed(store):
    """order가 "지금 존재하는 그룹 전체"의 단일 진실이다 — 빠지면 라벨도 함께 잊는다."""
    store.set_label("aaa000000001", "A")
    store.set_label("bbb000000002", "B")
    store.set_order(["aaa000000001"])  # B가 빠짐
    assert store.list_groups() == [{"id": "aaa000000001", "label": "A"}]
    assert store.get_label("bbb000000002") is None


def test_set_order_dedups_and_ignores_non_string_entries(store):
    result = store.set_order(["a", "a", "b", 123, None, "a"])
    assert result["order"] == ["a", "b"]


def test_set_order_rejects_non_list(store):
    assert store.set_order("not-a-list")["ok"] is False


def test_new_group_id_is_12_hex_chars():
    gid = group_store.new_group_id()
    assert len(gid) == 12
    int(gid, 16)  # hex 파싱 가능해야 한다 — 저장소/워크트리 id와 자릿수만 맞춘 임의값


def test_storage_file_is_owner_only(store, tmp_path):
    store.set_label("abc123456789", "이름")
    path = tmp_path / "state" / "groups.json"
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert data["order"] == ["abc123456789"]
    assert data["labels"] == {"abc123456789": "이름"}


def test_corrupt_file_falls_back_to_empty(store, tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "groups.json").write_text("{not json", encoding="utf-8")
    assert group_store.list_groups() == []


def test_too_many_groups_is_rejected(store, monkeypatch):
    monkeypatch.setattr(group_store, "MAX_GROUPS", 2)
    assert store.set_label("aaa000000001", "A")["ok"] is True
    assert store.set_label("bbb000000002", "B")["ok"] is True
    result = store.set_label("ccc000000003", "C")
    assert result["ok"] is False
    assert result["error"] == "too_many"
