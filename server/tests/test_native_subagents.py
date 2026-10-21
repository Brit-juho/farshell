"""Native roster, isolation and JSONL projection regressions."""
import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_status as A
import fsguard
import subagents as S
import subagent_transcript as T
from routes import agents


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    A._state.clear()
    A._owners.clear()
    A._retired.clear()
    monkeypatch.setattr(A, "_probe", lambda ent: None)
    yield
    A._state.clear()
    A._owners.clear()
    A._retired.clear()


def event(kind, child=None, ts=None, **extra):
    payload = {"session_id": "parent", **extra}
    if child:
        payload["agent_id"] = child
    return A.on_event(kind, payload, session="sample", agent="claude", ts=ts)


def test_parent_and_children_have_independent_ordering():
    event("session_start", ts=10, source="startup")
    event("subagent_start", "child", ts=20)
    event("stop", ts=40)
    event("subagent_stop", "child", ts=30)
    ent = A.get_state("parent")
    assert ent["status"] == "done"
    assert ent["_ts"] == 40
    assert ent["_subagents"]["child"]["state"] == "done"
    assert event("pre", "child", ts=25) is None
    assert event("subagent_start", "child", ts=30) is None


def test_child_wait_contract_and_private_fields():
    event("session_start", ts=1, source="startup")
    event("permission", "child", ts=2, tool_name="Bash", tool_use_id="tool-a")
    event("post", "other", ts=3, tool_use_id="tool-b")
    assert A.get_state("parent")["status"] == "waiting"
    event("post", "child", ts=4, tool_use_id="tool-a")
    ent = A.get_state("parent")
    assert ent["status"] == "working"
    pub = A.public_entry(ent)
    assert "_subagents" not in pub
    assert all(not k.startswith("_") for r in pub["subagents"]["items"] for k in r)


def test_children_cannot_create_parent_or_cross_clear_boundary():
    assert event("subagent_start", "child") is None
    event("session_start", ts=10, source="startup")
    event("subagent_start", "child", ts=20)
    event("session_start", ts=30, source="clear")
    assert not A.get_state("parent").get("_subagents")
    assert event("pre", "child", ts=25) is None
    event("session_end", ts=40)
    assert event("subagent_start", "child", ts=50) is None


def test_unknown_stop_and_bad_ids_do_not_create_roster():
    event("session_start", source="startup")
    event("subagent_stop", "internal")
    for child in ("../bad", "a" * 65, ["bad"], {"bad": True}):
        assert event("subagent_start", child) is None
    assert not A.get_state("parent").get("_subagents")


def test_cap_and_idle_expiry_and_stale():
    ent = {}
    for i in range(S.LIMIT):
        assert S.accept(ent, "subagent_start", {"agent_id": f"c{i}"}, "codex", i)
    assert not S.accept(ent, "subagent_start", {"agent_id": "overflow"}, "codex", 40)
    assert S.public(ent)["limited"]
    S.accept(ent, "subagent_stop", {"agent_id": "c0"}, "codex", 41)
    assert S.accept(ent, "subagent_start", {"agent_id": "new"}, "codex", 42)
    assert "c0" not in ent["_subagents"]
    assert len(ent["_subagents"]) == S.LIMIT
    now = time.time()
    S.sweep(ent, now + S.STALE + 1)
    assert all(r["state"] == "idle" and r["stale"] for r in ent["_subagents"].values())
    S.public(ent)
    S.sweep(ent, now + S.STALE + S.TTL + 2)
    assert S.public(ent)["items"] == []


def test_failure_and_teammate_inventory():
    ent = {}
    for aid in ("one", "aresearch-abcd", "aworkflow-abcd"):
        S.accept(ent, "subagent_start", {"agent_id": aid}, "claude")
    S.accept(ent, "stop_failure", {"agent_id": "one", "last_assistant_message": "API error"}, "claude")
    assert ent["_subagents"]["one"]["state"] == "failed"
    tasks = [{"id": "team-task", "type": "teammate", "status": "running"},
             {"id": "aworkflow-abcd", "type": "subagent", "status": "running"}]
    S.fold(ent, {"background_tasks": tasks})
    S.fold(ent, {"background_tasks": tasks[:1]})
    assert ent["_subagents"]["aresearch-abcd"]["state"] == "working"
    assert ent["_subagents"]["aworkflow-abcd"]["state"] == "done"
    assert S.idle_teammate(ent, {"teammate_name": "research"})
    assert ent["_subagents"]["aresearch-abcd"]["state"] == "idle"
    assert not S.fold(ent, {"background_tasks": [], "background_tasks_truncated": True})
    S.fold(ent, {"background_tasks": []})
    assert ent["_subagents"]["aresearch-abcd"]["state"] == "done"


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    home = tmp_path / "cli"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    parent = home / "projects" / "sample" / "parent.jsonl"
    parent.parent.mkdir(parents=True)
    parent.write_text("")
    path = parent.parent / "parent" / "subagents" / "agent-child.jsonl"
    path.parent.mkdir(parents=True)
    row = {"id": "child", "cli": "claude", "label": "Explore", "_parent_transcript": str(parent)}
    return path, row


def line(text="hello"):
    return (json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n").encode()


def test_incremental_partial_utf8_and_no_reasoning(transcript):
    path, row = transcript
    pending = T.read(row, "parent")
    assert pending["pending"]
    first = line("안녕")
    path.write_bytes(first + line("second")[:20])
    result = T.read(row, "parent")
    assert [i["text"] for i in result["items"]] == ["안녕"]
    with path.open("ab") as f:
        f.write(line("second")[20:])
        f.write(b'{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"secret"}]}}\n')
    new = T.read(row, "parent", result["next_cursor"])
    # Prefix changes while a tiny file grows can trigger a safe generation reset.
    assert new["items"][-1]["text"] == "second"
    assert "secret" not in json.dumps(new)
    assert T.read(row, "parent", new["next_cursor"])["items"] == []


def test_replacement_and_truncation(transcript):
    path, row = transcript
    path.write_bytes(line("a" * 200))
    first = T.read(row, "parent")
    other = path.with_suffix(".new")
    other.write_bytes(line("b" * 200))
    other.replace(path)
    replaced = T.read(row, "parent", first["next_cursor"])
    assert replaced["reset"] and replaced["items"][0]["text"].startswith("b")
    path.write_bytes(line("small"))
    assert T.read(row, "parent", replaced["next_cursor"])["reset"]


@pytest.mark.parametrize("cursor", ["", "bad!", "a" * 257, "WzEsIngiLC0xXQ", "WzEsIngiLHRydWVd"])
def test_bad_cursor(transcript, cursor):
    with pytest.raises(T.BadCursor):
        T.read(transcript[1], "parent", cursor)


def test_overlong_line_tail_and_malformed_json(transcript):
    path, row = transcript
    path.write_bytes(b'x' * (T.MAX_BYTES + 100) + b'\ninvalid\n' + line("tail"))
    result = T.read(row, "parent")
    assert result["truncated"]
    assert result["items"][0]["text"] == "tail"


def test_symlink_and_fifo_and_other_parent(transcript, tmp_path):
    path, row = transcript
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(line("private"))
    path.symlink_to(outside)
    with pytest.raises(fsguard.FsDenied):
        T.read(row, "parent")
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(fsguard.FsDenied):
        T.read(row, "parent")
    path.unlink()
    path.write_bytes(line())
    with pytest.raises(fsguard.FsDenied):
        T.read({**row, "_transcript": str(outside)}, "parent")


def test_codex_lineage_and_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path))
    path = tmp_path / "sessions" / "sample.jsonl"
    path.parent.mkdir()
    meta = {"type": "session_meta", "payload": {"id": "child", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}, "agent_nickname": "Scout"}}
    records = [meta, {"type": "response_item", "payload": {"type": "function_call", "name": "Bash", "arguments": '{"command":"pwd"}'}},
               {"type": "response_item", "payload": {"type": "reasoning", "text": "private"}}]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    row = {"id": "child", "cli": "codex", "label": "child", "_transcript": str(path)}
    result = T.read(row, "parent")
    assert result["label"] == "Scout"
    assert result["items"][0]["text"] == "pwd"
    assert len(result["items"]) == 1
    with pytest.raises(T.Unsupported):
        T.read(row, "other")


def test_real_http_transcript_errors_and_cursor(transcript):
    path, row = transcript
    path.write_bytes(line())
    event("session_start", source="startup")
    event("subagent_start", "child", transcript_path=row["_parent_transcript"])
    app = FastAPI()
    app.include_router(agents.router)
    with TestClient(app) as client:
        url = "/api/agents/sample/subagents/child/transcript"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"][0]["text"] == "hello"
        assert client.get(url, params={"cursor": "bad!"}).status_code == 400
        assert client.get(url.replace("child", "missing")).status_code == 404


def test_pure_child_events_do_not_schedule_queue(monkeypatch):
    import queue_runner
    monkeypatch.setattr(queue_runner, "schedule_drain", lambda **kw: pytest.fail("child must not drain"))
    monkeypatch.setattr(agents.pane_resolve, "resolve", lambda *a, **kw: ("sample", "test"))
    event("session_start", source="startup")
    event("stop")
    app = FastAPI()
    app.include_router(agents.router)
    with TestClient(app) as client:
        for kind in ("subagent_start", "subagent_stop"):
            r = client.post("/api/agent/event", json={"event": kind, "agent": "claude", "payload": {"session_id": "parent", "agent_id": "child"}})
            assert r.status_code == 200
            assert r.json()["queue_scheduled"] is False


def test_pure_child_does_not_overwrite_parent_context():
    event("session_start", source="startup", cwd="/sample/parent", ts=10)
    before = A.get_state("parent")["cwd"]
    event("subagent_start", "child", cwd="/sample/child", ts=20)
    assert A.get_state("parent")["cwd"] == before
    assert event("session_end", "child", ts=30) is None
    assert "parent" in A._state


def test_parent_inventory_cannot_end_a_newer_child_turn():
    event("session_start", source="startup", ts=10)
    event("subagent_start", "child", ts=30)
    event("stop", ts=20, background_tasks=[])
    assert A.get_state("parent")["_subagents"]["child"]["state"] == "working"


def test_late_teammate_idle_cannot_end_a_newer_turn():
    event("session_start", source="startup", ts=10)
    event("subagent_start", "aresearch-abcd", ts=30)
    event("teammate_idle", teammate_name="research", ts=20)
    assert A.get_state("parent")["_subagents"]["aresearch-abcd"]["state"] == "working"


def test_child_read_does_not_return_old_row_after_clear(transcript, monkeypatch):
    path, row = transcript
    path.write_bytes(line())
    event("session_start", source="startup")
    event("subagent_start", "child", transcript_path=row["_parent_transcript"])
    read = T.read
    def replacing(*args):
        result = read(*args)
        event("session_start", source="clear")
        event("subagent_start", "child", transcript_path=row["_parent_transcript"])
        return result
    monkeypatch.setattr(T, "read", replacing)
    app = FastAPI(); app.include_router(agents.router)
    with TestClient(app) as c:
        assert c.get("/api/agents/sample/subagents/child/transcript").status_code == 404


def test_new_turn_discards_previous_hook_answer():
    ent = {}
    S.accept(ent, "subagent_start", {"agent_id": "child"}, "codex")
    S.accept(ent, "subagent_stop", {"agent_id": "child", "last_assistant_message": "previous"}, "codex")
    S.accept(ent, "pre", {"agent_id": "child"}, "codex")
    assert not ent["_subagents"]["child"].get("_last_message")
    assert ent["_subagents"]["child"]["last_message"] == ""
