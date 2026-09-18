"""세션 그룹 API — ADR-29 A단계. 화면은 아직 이 라우트를 안 쓴다(B/C/D단계가
쓴다) — 라우트 자체의 검증·에러 처리만 여기서 확인한다.
"""

from __future__ import annotations

import asyncio

import pytest

import group_store
import routes.groups as groups_routes
import tmux_runner


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))


# --- GET /api/groups -----------------------------------------------------------

def test_list_groups_empty(tmp_path):
    assert asyncio.run(groups_routes.list_groups()) == {"groups": []}


def test_list_groups_reflects_store(tmp_path):
    group_store.set_label("abc123456789", "farshell")
    data = asyncio.run(groups_routes.list_groups())
    assert data == {"groups": [{"id": "abc123456789", "label": "farshell"}]}


# --- PATCH /api/groups/{id} -----------------------------------------------------

def test_rename_group_success():
    resp = asyncio.run(groups_routes.rename_group("abc123456789", _FakeRequest({"label": "새 이름"})))
    assert resp == {"ok": True, "id": "abc123456789", "label": "새 이름"}
    assert group_store.get_label("abc123456789") == "새 이름"


def test_rename_group_rejects_bad_id():
    resp = asyncio.run(groups_routes.rename_group("not-hex", _FakeRequest({"label": "x"})))
    assert resp.status_code == 400


def test_rename_group_rejects_missing_label():
    resp = asyncio.run(groups_routes.rename_group("abc123456789", _FakeRequest({})))
    assert resp.status_code == 400
    resp2 = asyncio.run(groups_routes.rename_group("abc123456789", _FakeRequest({"label": "   "})))
    assert resp2.status_code == 400


# --- POST /api/groups/reorder ---------------------------------------------------

def test_reorder_groups_success():
    group_store.set_label("aaa000000001", "A")
    group_store.set_label("bbb000000002", "B")
    resp = asyncio.run(groups_routes.reorder_groups(
        _FakeRequest({"order": ["bbb000000002", "aaa000000001"]}),
    ))
    assert resp == {"ok": True, "order": ["bbb000000002", "aaa000000001"]}


def test_reorder_groups_rejects_non_list():
    resp = asyncio.run(groups_routes.reorder_groups(_FakeRequest({"order": "nope"})))
    assert resp.status_code == 400


# --- POST /api/tmux/{name}/group ------------------------------------------------

def test_set_session_group_success(monkeypatch):
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: True)
    calls = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda s, k, v: calls.append((s, k, v)) or True)

    resp = asyncio.run(groups_routes.set_session_group("dev", _FakeRequest({"groupId": "abc123456789"})))
    assert resp == {"ok": True, "name": "dev", "groupId": "abc123456789"}
    assert calls == [("dev", "@fsh_grp", "abc123456789")]


def test_set_session_group_none_clears_to_ungrouped(monkeypatch):
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: True)
    calls = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda s, k, v: calls.append((s, k, v)) or True)

    resp = asyncio.run(groups_routes.set_session_group("dev", _FakeRequest({"groupId": None})))
    assert resp == {"ok": True, "name": "dev", "groupId": None}
    assert calls == [("dev", "@fsh_grp", "")], "묶지 않음은 빈 문자열로 적는다(태그 없음과 같은 표현)"


def test_set_session_group_rejects_bad_session_name(monkeypatch):
    resp = asyncio.run(groups_routes.set_session_group("../etc", _FakeRequest({"groupId": None})))
    assert resp.status_code == 400


def test_set_session_group_rejects_bad_group_id(monkeypatch):
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: True)
    resp = asyncio.run(groups_routes.set_session_group("dev", _FakeRequest({"groupId": "not-hex"})))
    assert resp.status_code == 400


def test_set_session_group_404s_for_unknown_session(monkeypatch):
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: False)
    resp = asyncio.run(groups_routes.set_session_group("ghost", _FakeRequest({"groupId": None})))
    assert resp.status_code == 404
