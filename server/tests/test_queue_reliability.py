"""큐 안정성 — 전송 중 보호, 재시도 백오프, 멈춤 점검, 전이 기록, 사람 입력 관문, pane 락.

멈춤 점검·백오프·전이 기록·입력 관문·보호 항목 규칙이 실제로 지켜지는지 본다.
"""

import threading
import time

import pytest

import agent_status
import pane_input
import queue_runner
import queue_store


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.delenv("VT_SAFE_MODE", raising=False)
    monkeypatch.setattr(queue_runner, "_notify", lambda *a: None)
    pane_input._reset_for_tests()
    agent_status._state.clear()
    yield
    pane_input._reset_for_tests()
    agent_status._state.clear()


# --- 전송 중 보호 -------------------------------------------------------------


def test_sending_item_cannot_be_removed():
    item = queue_store.add("a")["item"]
    queue_store.claim_next()
    r = queue_store.remove(item["id"])
    assert not r["ok"] and r["error"] == "item_sending"
    assert len(queue_store.list_items()) == 1


def test_clear_keeps_sending_items():
    queue_store.add("a")
    queue_store.add("b")
    queue_store.claim_next()
    r = queue_store.clear()
    assert r["removed"] == 1 and r["kept_sending"] == 1
    assert [x["text"] for x in queue_store.list_items()] == ["a"]


def test_delete_route_returns_409_for_sending_item(monkeypatch):
    from starlette.testclient import TestClient
    import main

    # 기동 복구(sweep_loop)가 sending을 blocked로 돌리므로 이 테스트에선 끈다.
    async def _noop():
        return None
    monkeypatch.setattr(queue_runner, "sweep_loop", _noop)

    item = queue_store.add("a")["item"]
    queue_store.claim_next()
    with TestClient(main.app) as c:
        r = c.delete(f"/api/queue/{item['id']}")
    assert r.status_code == 409 and r.json()["error"] == "item_sending"


def test_successful_drain_removes_item(monkeypatch):
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: True)
    queue_store.add("a")
    assert queue_runner.drain_once()["drained"] == 1
    assert queue_store.list_items() == []


def test_claim_is_exclusive_across_calls():
    """같은 항목을 두 드레인이 동시에 집지 않는다(flock + sending 표시)."""
    queue_store.add("only")
    got = []
    ts = [threading.Thread(target=lambda: got.append(queue_store.claim_next())) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sum(1 for g in got if g) == 1


# --- 재시작 복구 · 멈춤 점검 ----------------------------------------------------


def test_recover_interrupted_blocks_without_retry():
    queue_store.add("a")
    queue_store.claim_next()
    assert queue_store.recover_interrupted() == 1
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_BLOCKED
    assert "retry_at" not in x      # 이미 나갔을 수 있다 — 자동 재전송 금지


def test_sweep_blocks_stale_sending():
    queue_store.add("a")
    queue_store.claim_next()
    r = queue_store.sweep(now=time.time() + queue_store.SENDING_STALE_SEC + 1)
    assert len(r["stale"]) == 1
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_BLOCKED and "retry_at" not in x


def test_sweep_leaves_fresh_sending_alone():
    queue_store.add("a")
    queue_store.claim_next()
    r = queue_store.sweep()
    assert r == {"stale": [], "retried": []}
    assert queue_store.list_items()[0]["status"] == queue_store.STATUS_SENDING


# --- 재시도 백오프 ------------------------------------------------------------


def test_retriable_block_is_retried_after_backoff():
    item = queue_store.add("a")["item"]
    queue_store.claim_next()
    b = queue_store.mark_blocked(item, "타깃이 승인 대기 중", retriable=True)
    assert b["attempts"] == 1 and b["retry_at"] > time.time()
    assert queue_store.sweep()["retried"] == []                 # 아직 이르다
    r = queue_store.sweep(now=b["retry_at"] + 0.1)
    assert [x["id"] for x in r["retried"]] == [item["id"]]
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_PENDING and x["attempts"] == 1


def test_backoff_doubles_and_caps():
    assert [queue_store._retry_delay(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert queue_store._retry_delay(20) == queue_store.RETRY_MAX_SEC


def test_retries_stop_after_max_attempts():
    item = queue_store.add("a")["item"]
    for _ in range(queue_store.MAX_ATTEMPTS):
        queue_store.claim_next()
        b = queue_store.mark_blocked(item, "x", retriable=True)
        queue_store.sweep(now=time.time() + queue_store.RETRY_MAX_SEC + 1)
    assert b["exhausted"] is True and "retry_at" not in b
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_BLOCKED    # 더는 자동으로 안 풀린다


def test_exhausted_item_triggers_notification(monkeypatch):
    notified = []
    monkeypatch.setattr(queue_runner, "_notify", lambda t, b: notified.append(t))
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: False)
    queue_store.add("a")
    for _ in range(queue_store.MAX_ATTEMPTS):
        queue_runner.drain_once()
        queue_store.sweep(now=time.time() + queue_store.RETRY_MAX_SEC + 1)
    assert len(notified) == 1


def test_non_retriable_reasons_are_not_retried(monkeypatch):
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    queue_store.add("sudo rm -rf /")
    queue_runner.drain_once()
    assert "retry_at" not in queue_store.list_items()[0]


def test_unblock_resets_attempts():
    item = queue_store.add("a")["item"]
    queue_store.claim_next()
    queue_store.mark_blocked(item, "x", retriable=True)
    queue_store.unblock(item["id"])
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_PENDING and "attempts" not in x


# --- 전이 기록 ---------------------------------------------------------------


def test_transitions_are_logged_in_order(monkeypatch):
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: True)
    item = queue_store.add("a")["item"]
    queue_runner.drain_once()
    rows = queue_store.transitions(item["id"][:8])
    assert [(r["from"], r["to"]) for r in rows] == [
        (None, "pending"), ("pending", "sending"), ("sending", "sent")]


def test_transitions_rotate(monkeypatch):
    monkeypatch.setattr(queue_store, "TRANSITIONS_MAX_BYTES", 200)
    for i in range(20):
        queue_store.add(f"item {i}")
    p = queue_store._transitions_path()
    assert p.with_name(p.name + ".1").is_file()
    assert p.stat().st_size < 400


# --- 사람 입력 관문 ----------------------------------------------------------


def test_automatic_drain_defers_when_human_is_typing(monkeypatch):
    sent = []
    monkeypatch.setattr(queue_runner.tmux_target, "session_pane", lambda n: "%1")
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: sent.append(t) or True)
    queue_store.add("echo hi", target="dev")
    pane_input.note_human_input("dev")
    r = queue_runner.drain_once("dev", session_scoped=True, automatic=True)
    assert r["error"] == "human_typing" and sent == []
    x = queue_store.list_items()[0]
    assert x["status"] == queue_store.STATUS_BLOCKED and x.get("retry_at")


def test_manual_drain_ignores_human_typing(monkeypatch):
    sent = []
    monkeypatch.setattr(queue_runner.tmux_target, "session_pane", lambda n: "%1")
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: sent.append(t) or True)
    queue_store.add("echo hi", target="dev")
    pane_input.note_human_input("dev")
    assert queue_runner.drain_once()["drained"] == 1


def test_human_typing_window_expires(monkeypatch):
    monkeypatch.setenv("VT_HUMAN_INPUT_IDLE_SEC", "0.05")
    pane_input.note_human_input("dev")
    assert pane_input.human_typing("dev")
    time.sleep(0.08)
    assert not pane_input.human_typing("dev")


def test_terminal_reports_do_not_count_as_human_input():
    import deps

    deps.on_terminal_input("no-such-session", b"\x1b[?1;2c")
    assert pane_input._human_input_at == {}


# --- pane 락 -----------------------------------------------------------------


def test_send_to_tmux_holds_pane_lock_across_paste_and_enter(monkeypatch):
    """다른 자동 입력이 '붙여넣기 → Enter' 사이에 끼면 그쪽 Enter가 반쯤 제출한다."""
    import tmux_paste
    import tmux_runner
    import tmux_target

    log = []
    monkeypatch.setattr(tmux_paste, "tmux_version", lambda: (3, 5))
    monkeypatch.setenv("VT_PASTE_ENTER_GAP_MS", "30")

    def fake_run(args, timeout=2.0, input=None):
        log.append((threading.current_thread().name, args[0]))
        return 0, b"", b""

    monkeypatch.setattr(tmux_runner, "run", fake_run)
    monkeypatch.setattr(tmux_target.subprocess, "run",
                        lambda args, **kw: log.append((threading.current_thread().name, "type")))
    a = threading.Thread(target=tmux_target.send_to_tmux, args=("%1", "line1\nline2"), name="A")
    b = threading.Thread(target=tmux_target.type_to_tmux, args=("%1", "x"), name="B")
    a.start()
    time.sleep(0.01)
    b.start()
    a.join()
    b.join()
    names = [n for n, _ in log]
    assert names == ["A", "A", "A", "B"], log


def test_multiline_text_is_passed_whole_to_one_paste(monkeypatch):
    import tmux_paste
    import tmux_runner
    import tmux_target

    calls = []
    monkeypatch.setattr(tmux_paste, "tmux_version", lambda: (3, 7))
    monkeypatch.setenv("VT_PASTE_ENTER_GAP_MS", "0")
    monkeypatch.setattr(tmux_runner, "run",
                        lambda args, timeout=2.0, input=None: (calls.append((args, input)), (0, b"", b""))[1])
    assert tmux_target.send_to_tmux("%2", "첫 줄\r\n둘째 줄")
    # 줄바꿈은 터미널 붙여넣기와 같은 CR 하나로(paste_prepare) — bracketed 안에서는 입력창 줄바꿈
    assert calls[0][1] == "첫 줄\r둘째 줄".encode()
    assert calls[1][0][:2] == ["paste-buffer", "-S"]       # 3.7+는 vis(3) 끔


def test_paste_failure_cleans_buffer_and_skips_enter(monkeypatch):
    import tmux_paste
    import tmux_runner
    import tmux_target

    calls = []

    def fake_run(args, timeout=2.0, input=None):
        calls.append(args[0])
        return (1, b"", b"no pane") if args[0] == "paste-buffer" else (0, b"", b"")

    monkeypatch.setattr(tmux_paste, "tmux_version", lambda: (3, 5))
    monkeypatch.setattr(tmux_runner, "run", fake_run)
    assert tmux_target.send_to_tmux("%2", "x") is False
    assert calls == ["load-buffer", "paste-buffer", "delete-buffer"]


def test_send_to_session_refuses_while_human_types(monkeypatch):
    import agent_share

    pane_input.note_human_input("dev")
    target = agent_share.Sess(sid="$1", created=0, name="dev", pane_id="%1",
                              command="claude", share=None)
    with pytest.raises(agent_share.ShareError) as e:
        agent_share.check_send_allowed(target)
    assert e.value.code == "target_human_typing"


def test_preface_is_typed_before_paste(monkeypatch):
    import tmux_paste
    import tmux_runner
    import tmux_target

    calls = []
    monkeypatch.setattr(tmux_paste, "tmux_version", lambda: (3, 5))
    monkeypatch.setenv("VT_PASTE_ENTER_GAP_MS", "0")
    monkeypatch.setattr(tmux_runner, "run",
                        lambda args, timeout=2.0, input=None: (calls.append(args), (0, b"", b""))[1])
    assert tmux_target.send_to_tmux("%2", "본문\n둘째", preface="머리말\n줄바꿈은 펴진다")
    assert calls[0] == ["send-keys", "-t", "%2", "-l", "--", "머리말 줄바꿈은 펴진다 "]
    assert [c[0] for c in calls[1:]] == ["load-buffer", "paste-buffer", "send-keys"]


def test_queue_passes_preface(monkeypatch):
    got = []
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux",
                        lambda p, t, preface=None: got.append((t, preface)) or True)
    queue_store.add("본문", preface="머리말")
    queue_runner.drain_once()
    assert got == [("본문", "머리말")]


def test_schedule_drain_is_per_session(monkeypatch):
    """한 세션의 유예 중인 예약이 다른 세션의 예약을 막으면 안 된다(위임 E2E 회귀)."""
    import asyncio

    async def scenario():
        monkeypatch.setenv("VT_QUEUE_GRACE_SEC", "5")
        queue_store.add("a", target="lead")
        queue_store.add("b", target="child")
        assert queue_runner.schedule_drain(session="lead", session_scoped=True)
        assert queue_runner.schedule_drain(session="child", session_scoped=True, targeted_only=True)
        assert not queue_runner.schedule_drain(session="lead", session_scoped=True)   # 같은 키는 중복 안 함
        for t in list(queue_runner._pending_tasks.values()):
            t.cancel()

    asyncio.run(scenario())


def test_schedule_from_worker_thread_reaches_loop(monkeypatch):
    import asyncio

    called = []

    async def scenario():
        queue_runner._loop = asyncio.get_running_loop()
        monkeypatch.setattr(queue_runner, "schedule_drain", lambda *a: called.append(a) or True)
        assert await asyncio.to_thread(queue_runner.schedule_drain_threadsafe, "child", True, True)
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    queue_runner._loop = None
    assert called == [("child", True, True)]
