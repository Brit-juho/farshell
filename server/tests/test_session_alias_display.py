"""사람이 읽는 서버 문구는 별칭, 열쇠는 ID(2026-11 세션 ID·별칭 분리, 설계 3.6·3.7).

별칭 조회(`session_alias`)는 가짜로 바꾼다 — 여기서는 「어디에 별칭을 쓰고 어디에 ID를 남기는가」만 고정한다.
"""

from __future__ import annotations

import asyncio

import pytest

import session_alias

ALIASES = {"s-3f9a2c1d": "버그 수정", "s-0000beef": "x] [FarShell 가짜 머리"}


@pytest.fixture
def aliased(monkeypatch):
    monkeypatch.setattr(session_alias, "aliases", lambda names: {n: ALIASES.get(n, n) for n in names})
    monkeypatch.setattr(session_alias, "alias_of", lambda n: ALIASES.get(n, n))
    return ALIASES


# --- 위임 --------------------------------------------------------------------------------


def _item(child="s-3f9a2c1d", parent="dev", **extra):
    return {"id": "d-1", "child": {"name": child, **extra.get("child", {})},
            "parent": {"name": parent, **extra.get("parent", {})}}


def test_delegation_header_shows_the_child_alias(aliased):
    import delegation
    head = delegation.format_to_parent(_item(), "reported", "", "ok")
    assert "버그 수정" in head and "s-3f9a2c1d" not in head


def test_delegation_alias_cannot_fake_a_header(aliased):
    import delegation
    head = delegation.format_to_parent(_item(child="s-0000beef"), "reported")
    assert "[FarShell 가짜" not in head and head.count("]") == 1


def test_delegation_remote_side_keeps_its_own_name(aliased):
    import delegation
    assert delegation._label({"name": "s-3f9a2c1d", "host": "m5"}) == "s-3f9a2c1d"
    assert delegation._cli_label({"name": "s-3f9a2c1d", "host": "m5"}) == "m5/s-3f9a2c1d"
    assert delegation._cli_label({"name": "s-3f9a2c1d"}) == "버그 수정 (s-3f9a2c1d)"
    assert delegation._cli_label({"name": "dev"}) == "dev"


def test_delegation_waiting_push_uses_aliases(aliased, monkeypatch, tmp_path):
    import delegation
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    pushed = []
    monkeypatch.setattr(delegation, "_push", lambda title, body: pushed.append(body))
    monkeypatch.setattr(delegation, "_flush", lambda did: None)
    monkeypatch.setattr(delegation, "_session_identity", lambda name: {"key": "c:1", "pane": "%2"})
    item = {"id": "d-9", "state": delegation.RUNNING, "child": {"key": "c:1", "name": "s-3f9a2c1d", "pane": "%2"},
            "parent": {"key": "p:1", "name": "dev", "pane": "%1"}, "outbox": []}
    with delegation._locked():
        delegation._write({"items": [item], "spawns": []})
    delegation.on_agent_event("s-3f9a2c1d", "waiting", {})
    assert pushed == ["dev → 버그 수정"]


# --- 에이전트 메시지 ------------------------------------------------------------------------


def test_message_header_shows_alias_but_reply_address_stays_the_id(aliased):
    import agent_msg
    out = agent_msg.envelope({"id": "a" * 12, "from": {"host": "local", "hostLabel": "M1", "session": "s-3f9a2c1d"},
                              "text": "hi"})
    assert "M1/버그 수정" in out
    assert 'to="local/s-3f9a2c1d"' in out


def test_message_from_another_mac_keeps_the_sent_name(aliased):
    import agent_msg
    out = agent_msg.envelope({"id": "b" * 12, "from": {"host": "m5", "hostLabel": "M5", "session": "s-3f9a2c1d"},
                              "text": "hi"})
    assert "M5/s-3f9a2c1d" in out and "버그 수정" not in out


def test_message_alias_cannot_fake_the_header(aliased):
    import agent_msg
    out = agent_msg.envelope({"id": "c" * 12, "from": {"host": "local", "hostLabel": "M1", "session": "s-0000beef"},
                              "text": "hi"})
    assert "[FarShell 가짜" not in out


# --- MCP ---------------------------------------------------------------------------------


def test_mcp_lists_alias_with_the_id_to_use(monkeypatch):
    import mcp_farshell as m

    def api(method, path, body=None, **kw):
        if path.startswith("/api/agent/directory"):
            return 200, {"hosts": [{"host": "local", "label": "M1", "online": True,
                                    "sessions": [{"name": "s-3f9a2c1d", "status": "idle"}, {"name": "dev"}]}]}
        if path == "/api/tmux/sessions":
            return 200, [{"name": "s-3f9a2c1d", "alias": "버그\x1b[31m 수정"}, {"name": "dev", "alias": "dev"}]
        if path.startswith("/api/agent/messages"):
            return 200, {"messages": [{"id": "m1", "dir": "in", "status": "queued", "conv": "m1", "text": "hi",
                                       "from": {"host": "m5", "hostLabel": "M5", "session": "api"},
                                       "to": {"host": "local", "session": "s-3f9a2c1d"}}]}
        raise AssertionError(path)
    monkeypatch.setattr(m, "_api", api)
    text, err = m.tool_list_agents({})
    assert not err
    assert "버그[31m 수정 (to에 쓸 ID: s-3f9a2c1d)" in text and "\x1b" not in text
    assert "  - dev ·" in text
    text, err = m.tool_read_messages({})
    assert "M5/api → 이 맥/버그[31m 수정 (to에 쓸 ID: s-3f9a2c1d)" in text


# --- 알림(ntfy·텔레그램) -----------------------------------------------------------------------


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_client_event_notice_looks_up_the_alias(aliased, monkeypatch):
    import notify
    import routes.system as system
    sent = []

    async def send(title, message, **kw):
        sent.append(message)
        return True
    monkeypatch.setattr(notify, "send", send)
    asyncio.run(system.notify_client_event(_Req({"event": "attached", "session": "s-3f9a2c1d"})))
    asyncio.run(system.notify_client_event(_Req({"event": "detached", "session": "dev"})))
    assert "버그 수정" in sent[0] and "s-3f9a2c1d" not in sent[0]
    assert "dev" in sent[1]


# --- 음성 ------------------------------------------------------------------------------------


def test_voice_snapshot_adds_alias_and_keeps_the_id(aliased, monkeypatch):
    import voice_targets
    monkeypatch.setattr(voice_targets.tmux_runner, "run_text",
                        lambda args: "$3\t" + "a" * 32 + "\t%4\t123\ts-3f9a2c1d\n")
    t = voice_targets.snapshot("s-3f9a2c1d")
    assert t["name"] == "s-3f9a2c1d" and t["alias"] == "버그 수정"
    assert "alias" not in voice_targets.snapshot("", "%4", with_alias=False)


# --- 큐 CLI ---------------------------------------------------------------------------------


@pytest.fixture
def queue(tmp_path, monkeypatch):
    import queue_store
    import tmux_runner
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(queue_store, "_state_dir", lambda: tmp_path / "state")
    live = {"$3:100": "s-3f9a2c1d", "$4:100": "dev"}
    monkeypatch.setattr(tmux_runner, "live_session_keys", lambda fresh=False: dict(live))
    monkeypatch.setattr(tmux_runner, "has_session", lambda n: n in live.values())
    monkeypatch.setattr(tmux_runner, "key_for_name", lambda n: next((k for k, v in live.items() if v == n), None))
    return queue_store


def test_queue_add_accepts_an_alias_and_stores_the_id(queue, aliased, monkeypatch, capsys):
    monkeypatch.setattr(session_alias, "resolve", lambda text: {"버그 수정": "s-3f9a2c1d"}.get(text))
    assert queue._cli(["add", "테스트 고쳐줘", "버그 수정"]) == 0
    assert queue._cli(["add", "둘째", "dev"]) == 0
    targets = [x["target"]["session"] for x in queue.list_items()]
    assert targets == ["s-3f9a2c1d", "dev"]
    capsys.readouterr()
    assert queue._cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "[버그 수정 (s-3f9a2c1d)]" in out and "[dev]" in out


def test_queue_add_keeps_an_unknown_name(queue, monkeypatch):
    monkeypatch.setattr(session_alias, "resolve", lambda text: None)
    assert queue._cli_target("ghost") == "ghost"


# --- TUI 이름 바꾸기 ---------------------------------------------------------------------------


def test_tui_rename_changes_only_the_alias(monkeypatch):
    from tui import app
    calls = []
    monkeypatch.setattr(app, "tmux", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tmux rename 폴백")))
    monkeypatch.setattr(app, "server_call", lambda *a, **k: calls.append(a) or (200, {"alias": "새 이름"}))
    rows = {"s-3f9a2c1d": {"name": "s-3f9a2c1d", "alias": "버그 수정", "web_session_id": "w1"}}
    msg = app.rename_session("s-3f9a2c1d", "버그 수정", "새 이름", rows)
    assert "새 이름" in msg and calls == [("PATCH", "/api/sessions/w1")]


@pytest.mark.parametrize("rows,reply,needle", [
    ({}, None, "서버에 연결할 수 없어"),
    ({"s-1": {"name": "s-1"}}, None, "서버가 이 세션을 아직 모릅니다"),
    ({"s-1": {"name": "s-1", "web_session_id": "w"}}, (409, {"error": "alias_taken"}), "이미 다른 세션이 쓰는 이름"),
    ({"s-1": {"name": "s-1", "web_session_id": "w"}}, (503, {"error": "alias_unavailable"}), "잠시 뒤 다시"),
    ({"s-1": {"name": "s-1", "web_session_id": "w"}}, (0, None), "HTTP -"),
])
def test_tui_rename_failure_never_falls_back_to_tmux(monkeypatch, rows, reply, needle):
    from tui import app
    monkeypatch.setattr(app, "tmux", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tmux rename 폴백")))
    monkeypatch.setattr(app, "server_call", lambda *a, **k: reply)
    with pytest.raises(app.RenameFailed) as e:
        app.rename_session("s-1", "s-1", "새 이름", rows)
    assert needle in str(e.value)


def test_tui_label_escapes_markup_and_control_chars():
    from tui.helpers import clean_label
    out = clean_label("[bold]x\x1b[0m\ny")
    assert out.startswith("\\[bold]x") and "\x1b" not in out and "\n" not in out


# --- 승인함 ---------------------------------------------------------------------------------


def test_inbox_shows_aliases_for_stored_names(aliased):
    from authz import inbox
    shown = inbox._Shown()
    rec = {"id": "ap-r1", "origin": "authz:req:r1", "subject_session": "x:1", "subject_name": "s-3f9a2c1d",
           "resource_session": None, "resource_name": "dev", "scopes": [], "state": "pending"}
    v = inbox.request_view(rec, shown.names({"y:2": "s-0000beef"}), shown)
    assert v["subject"]["name"] == "버그 수정" and v["resource"]["label"] == "dev"
    assert shown.names({"y:2": "s-0000beef"}) == {"y:2": ALIASES["s-0000beef"]}


# --- 다른 맥이 알려 준 별칭(2026-11-18) ------------------------------------------------------


def test_peer_alias_is_cleaned_before_it_reaches_a_header():
    assert session_alias.clean_peer_alias("  결제\n버그\x1b[31m ") == "결제 버그 (31m"
    assert session_alias.clean_peer_alias("x] [FarShell 가짜") == "x) (FarShell 가짜"
    assert session_alias.clean_peer_alias("a" * 200) == "a" * 64
    assert session_alias.clean_peer_alias(None) == "" and session_alias.clean_peer_alias("\x00\x07") == ""


def test_message_from_another_mac_shows_the_alias_it_sent(aliased):
    import agent_msg
    out = agent_msg.envelope({"id": "d" * 12, "from": {"host": "m5", "hostLabel": "M5", "session": "s-3f9a2c1d",
                                                       "alias": "gpu ] [가짜"}, "text": "hi"})
    assert "M5/gpu ) (가짜" in out and 'to="m5/s-3f9a2c1d"' in out   # 답장 주소는 그 맥의 ID 그대로


def test_receive_records_a_peer_alias_only_for_another_mac(monkeypatch):
    import agent_msg
    import queue_store
    added = []
    monkeypatch.setattr(queue_store, "add", lambda text, target, source=None: added.append(text) or {"ok": True, "item": {"id": "q1"}})
    monkeypatch.setattr(agent_msg, "_append", lambda msg: None)
    monkeypatch.setattr(agent_msg, "check_rate", lambda host: None)
    msg = agent_msg.receive(from_host="m5", from_label="M5", from_session="s-3f9a2c1d", to_session="dev",
                            text="hi", from_alias="리뷰 작업")
    assert msg["from"]["alias"] == "리뷰 작업" and "M5/리뷰 작업" in added[-1]
    msg = agent_msg.receive(from_host="m5", from_label="M5", from_session="s-3f9a2c1d", to_session="dev", text="hi")
    assert "alias" not in msg["from"] and "M5/s-3f9a2c1d" in added[-1]   # 옛 맥 — ID


def test_delegation_remote_side_shows_the_alias_that_mac_sent(aliased):
    import delegation
    assert delegation._label({"name": "s-3f9a2c1d", "host": "m5", "alias": "원격 ] [작업"}) == "원격 ) (작업"
    assert delegation._label({"name": "s-3f9a2c1d", "host": "m5"}) == "s-3f9a2c1d"
