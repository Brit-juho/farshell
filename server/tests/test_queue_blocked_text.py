"""큐 항목이 안정 코드로 막히면 사람이 읽는 문구(ko/en)를 `GET /api/queue` 와 `fsh queue` 출력에 싣는다."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import queue_store

CODES = ("grant_revoked", "delegation_closed", "relationship_gone", "authz_unavailable")


def test_each_code_has_a_distinct_human_text_in_both_languages(monkeypatch):
    ko = {c: queue_store.blocked_text(c) for c in CODES}
    assert all(ko.values()) and len(set(ko.values())) == 4 and not any(c in v for c, v in ko.items())
    monkeypatch.setenv("VT_LANG", "en")
    en = {c: queue_store.blocked_text(c) for c in CODES}
    assert all(en.values()) and len(set(en.values())) == 4
    assert all(ko[c] != en[c] and not any("가" <= ch <= "힣" for ch in en[c]) for c in CODES)


def test_unknown_reasons_have_no_text():
    assert queue_store.blocked_text("타깃 tmux 세션 없음") == "" and queue_store.blocked_text("") == ""
    item = {"id": "x", "blocked_reason": "타깃 tmux 세션 없음"}
    assert queue_store.with_blocked_text(item) is item


@pytest.fixture
def blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    r = queue_store.add("echo hi", {"session": "work"})
    item = queue_store.claim_next(None)
    return queue_store.mark_blocked(item, "grant_revoked", retriable=False)


def test_get_queue_exposes_blocked_text_without_touching_the_stored_item(blocked, monkeypatch):
    import main
    c = TestClient(main.app)
    items = c.get("/api/queue").json()["items"]
    it = next(x for x in items if x["id"] == blocked["id"])
    assert it["blocked_reason"] == "grant_revoked" and it["blocked_text"] == queue_store.blocked_text("grant_revoked")
    assert "blocked_text" not in next(x for x in queue_store.list_items() if x["id"] == blocked["id"])


def test_fsh_queue_list_prints_the_text(blocked, capsys):
    queue_store._cli(["list"]) if hasattr(queue_store, "_cli") else None
    out = capsys.readouterr().out
    assert queue_store.blocked_text("grant_revoked") in out and "grant_revoked" not in out
