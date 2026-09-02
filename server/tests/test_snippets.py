"""L3 회귀: 프롬프트 스니펫이 지시를 잃지 않아야 한다. queue_store 테스트와 같은 톤 —
CRUD만 있으니 큐보다 훨씬 단순하다(순서/드레인/safe_mode 상태 기계가 없음)."""

import os
from pathlib import Path

import pytest

import snippet_store


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    yield


def test_add_and_list():
    snippet_store.add("git commit -m 'wip'", "wip 커밋")
    snippet_store.add("cd ~/proj\npython run.py")
    items = snippet_store.list_items()
    assert [x["text"] for x in items] == ["git commit -m 'wip'", "cd ~/proj\npython run.py"]
    assert items[0]["label"] == "wip 커밋"
    assert items[1]["label"] == ""


def test_empty_text_rejected():
    r = snippet_store.add("   ")
    assert not r["ok"] and r["error"] == "empty"


def test_too_long_rejected():
    r = snippet_store.add("x" * (snippet_store.MAX_TEXT_LEN + 1))
    assert not r["ok"] and r["error"] == "too_long"


def test_cap_rejects_instead_of_silently_dropping():
    for i in range(snippet_store.MAX_ITEMS):
        assert snippet_store.add(f"item-{i}")["ok"]
    r = snippet_store.add("넘침")
    assert not r["ok"] and r["error"] == "full"
    assert len(snippet_store.list_items()) == snippet_store.MAX_ITEMS
    assert snippet_store.list_items()[0]["text"] == "item-0"


def test_remove():
    a = snippet_store.add("a")["item"]
    snippet_store.add("b")
    assert snippet_store.remove(a["id"])["ok"]
    assert [x["text"] for x in snippet_store.list_items()] == ["b"]
    assert snippet_store.remove("nope")["error"] == "not_found"


def test_label_truncated_to_max_len():
    r = snippet_store.add("x", "l" * (snippet_store.MAX_LABEL_LEN + 10))
    assert len(r["item"]["label"]) == snippet_store.MAX_LABEL_LEN


def test_file_permissions_are_0600():
    snippet_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert oct(p.parent.stat().st_mode & 0o777) == "0o700"


def test_survives_corrupt_file():
    snippet_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    p.write_text("{ this is not json")
    assert snippet_store.list_items() == []
    assert snippet_store.add("b")["ok"]
