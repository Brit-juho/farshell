"""읽을 수 없거나 파싱할 수 없는 저장소는 쓰기를 거부한다 — 빈 문서로 덮어 모든 권한을 지우지 않는다.

모든 authz 문서와 옛 `agent_grants.json` 이 대상이다. 읽기는 그대로 fail closed(손상 표시), 손상 파일은 지우지도 고치지도
않고, 같은 내용의 복사본은 한 번만 만든다. 진단(`migrate.status`·`store-status`)은 읽을 수 없는 파일을 사람 말로 알린다.
"""

from __future__ import annotations

import json

import pytest

import agent_share
from authz import __main__ as cli
from authz import migrate, store


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    return tmp_path / "vt"


def _mutate(name: str) -> None:
    with store.txn() as docs:
        if name in store._DICT_DOCS:
            docs[name]["items"]["k"] = {"v": 1}
        else:
            docs[name]["items"].append({"id": "x", "v": 1})


@pytest.mark.parametrize("name", store.DOCS)
@pytest.mark.parametrize("bad", ["{ not json", '{"items": 5}', "[]", ""])
def test_every_store_refuses_writes_when_unreadable_and_keeps_the_file(name, bad):
    _mutate(name)                                           # 정상일 때는 쓴다
    p = store.doc_path(name)
    p.write_text(bad, encoding="utf-8")
    for _ in range(2):
        with pytest.raises(store.StoreError) as ei:
            _mutate(name)
        assert ei.value.code == "store_unreadable" and ei.value.status == 503
        assert name in ei.value.reason
    assert p.read_text(encoding="utf-8") == bad             # 손상 파일은 그대로
    copies = list(p.parent.glob(f"{p.name}.corrupt-*"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == bad     # 같은 내용의 복사본은 한 번만
    assert (copies[0].stat().st_mode & 0o777) == 0o600
    assert store.snapshot().corrupt >= ({name} & {"grants", "approvals", "windows", "shares"})   # 읽기는 손상 표시(fail closed)


def test_a_different_corruption_gets_its_own_copy_and_nothing_is_deleted():
    p = store.doc_path("grants")
    _mutate("grants")
    for text in ("{ a", "{ b"):
        p.write_text(text)
        with pytest.raises(store.StoreError):
            _mutate("grants")
    assert sorted(c.read_text() for c in p.parent.glob("grants.json.corrupt-*")) == ["{ a", "{ b"]
    assert p.exists()


def test_corrupt_doc_does_not_block_writes_to_other_docs_but_a_mixed_txn_saves_nothing():
    _mutate("grants")
    store.doc_path("shares").write_text("{ not json")
    _mutate("grants")                                       # shares 는 손상이지만 이번 변경 대상이 아니다
    assert len(store.snapshot().grants) == 2
    with pytest.raises(store.StoreError):
        with store.txn() as docs:
            docs["grants"]["items"].append({"id": "y"})
            docs["shares"]["items"]["k"] = {"nick": "n"}
    assert len(store.snapshot().grants) == 2                # 같은 트랜잭션의 정상 문서도 저장하지 않았다


def test_unchanged_txn_on_a_corrupt_doc_is_not_a_write():
    store.root().mkdir(parents=True)
    store.doc_path("grants").write_text("{ not json")
    with store.txn() as docs:
        assert docs["grants"]["items"] == []
    assert store.doc_path("grants").read_text() == "{ not json"
    assert list(store.root().glob("grants.json.corrupt-*")) == []


def test_unreadable_file_permission_is_also_a_refusal(monkeypatch):
    _mutate("grants")
    real = type(store.doc_path("grants")).read_text

    def deny(self, *a, **k):
        if self.name == "grants.json":
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **k)

    monkeypatch.setattr(type(store.doc_path("grants")), "read_text", deny)
    with pytest.raises(store.StoreError) as ei:
        _mutate("grants")
    assert ei.value.code == "store_unreadable"


def test_legacy_agent_grants_file_refuses_writes_flag_off_and_is_kept(monkeypatch):
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    p = agent_share._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    with pytest.raises(agent_share.ShareError) as ei:
        with agent_share._txn(None) as data:
            data["requests"].append({"id": "r1", "state": "pending", "expires": 9e12})
    assert ei.value.code == "store_unreadable" and ei.value.status == 503
    assert p.read_text() == "{ not json"
    assert len(list(p.parent.glob("agent_grants.json.corrupt-*"))) == 1
    with agent_share._txn(None):                            # 바꾸지 않는 트랜잭션은 쓰기가 아니다
        pass
    assert p.read_text() == "{ not json"


def test_status_and_store_status_report_unreadable_files(capsys):
    _mutate("grants")
    store.doc_path("grants").write_text("{ not json")
    p = agent_share._path()
    p.write_text("[1,")
    bad = {u["name"]: u for u in migrate.status()["unreadable"]}
    assert set(bad) == {"grants", "agent_grants"}
    assert "JSON" in bad["grants"]["reason"] and bad["grants"]["path"].endswith("grants.json")
    assert cli.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "저장소 읽기 불가: grants" in out and "저장소 읽기 불가: agent_grants" in out and "변경 거부 중" in out
    assert cli.store_status([]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert sorted(x.split("|")[0] for x in lines) == ["agent_grants", "grants"]


def test_healthy_state_reports_nothing(capsys):
    _mutate("grants")
    assert migrate.status()["unreadable"] == []
    assert cli.store_status([]) == 0
    assert capsys.readouterr().out == ""


def test_msg_decide_with_unreadable_grants_is_refused_and_request_stays(monkeypatch):
    from authz import msg_svc, principals as P
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    a, b = "$1:1", "$2:2"
    rec, _ = msg_svc.request_access(a, "a", b, "b", {a, b})
    store.doc_path("grants").write_text("{ not json")
    with pytest.raises(store.StoreError) as ei:
        msg_svc.decide(rec["id"], "allow", None, P.human("d"), {a, b})
    assert ei.value.code == "store_unreadable"
    assert store.doc_path("grants").read_text() == "{ not json"
    assert [r["state"] for r in msg_svc.msg_requests({"approvals": store._read("approvals")[0]})] == ["pending"]
