"""P4 회귀: 프롬프트 큐가 지시를 잃거나 순서를 뒤집지 않아야 한다.

큐가 틀리는 방식은 전부 사용자에게 치명적이다:
  - 순서가 섞이면 "테스트 고쳐줘 → 커밋해줘"가 뒤집혀 커밋이 먼저 나간다
  - 동시 add 에서 lost update 가 나면 넣은 줄 알았던 지시가 사라진다
  - 상한 초과를 조용히 버리면 같은 일이 벌어진다
  - safe_mode 에 막힌 항목이 사라지면 사용자는 실행된 줄 안다
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import queue_runner
import queue_store


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.delenv("VT_SAFE_MODE", raising=False)
    yield


def test_add_and_list_preserves_order():
    for t in ["첫째", "둘째", "셋째"]:
        assert queue_store.add(t)["ok"]
    assert [x["text"] for x in queue_store.list_items()] == ["첫째", "둘째", "셋째"]


def test_pop_next_is_fifo():
    for t in ["a", "b", "c"]:
        queue_store.add(t)
    assert queue_store.pop_next()["text"] == "a"
    assert queue_store.pop_next()["text"] == "b"
    assert queue_store.pop_next()["text"] == "c"
    assert queue_store.pop_next() is None


def test_empty_text_rejected():
    r = queue_store.add("   ")
    assert not r["ok"] and r["error"] == "empty"


def test_too_long_rejected():
    r = queue_store.add("x" * (queue_store.MAX_TEXT_LEN + 1))
    assert not r["ok"] and r["error"] == "too_long"


def test_cap_rejects_instead_of_silently_dropping():
    for i in range(queue_store.MAX_ITEMS):
        assert queue_store.add(f"item-{i}")["ok"]
    r = queue_store.add("넘침")
    assert not r["ok"] and r["error"] == "full"
    # 기존 항목은 그대로 — 새 것을 넣겠다고 오래된 것을 버리지 않는다
    assert len(queue_store.list_items()) == queue_store.MAX_ITEMS
    assert queue_store.list_items()[0]["text"] == "item-0"


def test_remove_and_clear():
    queue_store.add("a")
    b = queue_store.add("b")["item"]
    queue_store.add("c")
    assert queue_store.remove(b["id"])["ok"]
    assert [x["text"] for x in queue_store.list_items()] == ["a", "c"]
    assert queue_store.remove("nope")["error"] == "not_found"
    assert queue_store.clear()["removed"] == 2
    assert queue_store.list_items() == []


def test_file_permissions_are_0600():
    queue_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "queue.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert oct(p.parent.stat().st_mode & 0o777) == "0o700"


def test_survives_corrupt_file():
    """파일이 깨져도 서버가 죽지 않고 빈 큐로 시작해야 한다."""
    queue_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "queue.json"
    p.write_text("{ this is not json")
    assert queue_store.list_items() == []
    assert queue_store.add("b")["ok"]


def test_blocked_item_stays_in_queue():
    item = queue_store.add("sudo rm -rf /")["item"]
    queue_store.pop_next()
    queue_store.mark_blocked(item, "sudo")
    items = queue_store.list_items()
    assert len(items) == 1
    assert items[0]["status"] == queue_store.STATUS_BLOCKED
    assert items[0]["blocked_reason"] == "sudo"
    # blocked 는 pop 대상이 아니다 — 다시 투입되지 않는다
    assert queue_store.pop_next() is None
    assert queue_store.pending_count() == 0


def test_unblock_makes_it_pending_again():
    item = queue_store.add("x")["item"]
    queue_store.pop_next()
    queue_store.mark_blocked(item, "테스트")
    assert queue_store.unblock(item["id"])["ok"]
    assert queue_store.pop_next()["text"] == "x"


def test_blocked_item_is_reinserted_at_front():
    """막힌 항목이 큐 뒤로 밀리면 순서가 뒤집힌다."""
    a = queue_store.add("a")["item"]
    queue_store.add("b")
    queue_store.pop_next()
    queue_store.mark_blocked(a, "테스트")
    assert [x["text"] for x in queue_store.list_items()] == ["a", "b"]


def test_two_blocked_items_keep_submission_order():
    """A가 B보다 먼저 막히면, 최종 순서는 [A, B]여야 한다(뒤집히면 안 된다)."""
    a = queue_store.add("a")["item"]
    b = queue_store.add("b")["item"]
    queue_store.pop_next()  # a
    queue_store.mark_blocked(a, "테스트")
    queue_store.pop_next()  # b
    queue_store.mark_blocked(b, "테스트")
    assert [x["text"] for x in queue_store.list_items()] == ["a", "b"]


def test_concurrent_adds_do_not_lose_items(tmp_path):
    """별도 프로세스 4개가 동시에 add — flock 이 없으면 항목이 유실된다."""
    state = str(tmp_path / "vt-concurrent")
    server_dir = str(Path(__file__).resolve().parent.parent)
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import queue_store\n"
        "for i in range(10): queue_store.add(f'{sys.argv[1]}-{i}')\n" % server_dir
    )
    env = {**os.environ, "VT_STATE_DIR": state}
    procs = [
        subprocess.Popen([sys.executable, "-c", code, f"w{n}"], env=env)
        for n in range(4)
    ]
    for p in procs:
        p.wait(timeout=30)
    items = json.loads((Path(state) / "queue.json").read_text())
    assert len(items) == 40, f"유실 발생: {len(items)}/40"
    assert len({x["id"] for x in items}) == 40      # id 중복도 없어야 한다


def test_drain_blocks_dangerous_command(monkeypatch):
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    queue_store.add("sudo rm -rf /")
    r = queue_runner.drain_once()
    assert not r["ok"] and r["error"] == "blocked"
    items = queue_store.list_items()
    assert len(items) == 1 and items[0]["status"] == queue_store.STATUS_BLOCKED


def test_drain_blocks_dangerous_command_in_later_line(monkeypatch):
    """첫 줄만 검사하면 뒤 줄의 위험 명령이 tmux 로 그대로 나간다."""
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    sent = []
    monkeypatch.setattr(queue_runner.tmux_target, "resolve_voice_target_pane",
                        lambda: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux",
                        lambda p, t: (sent.append(t), True)[1])
    queue_store.add("echo hi\nsudo rm -rf /")
    r = queue_runner.drain_once()
    assert not r["ok"] and r["error"] == "blocked"
    assert sent == []  # 아무것도 tmux 로 보내지지 않아야 한다
    items = queue_store.list_items()
    assert len(items) == 1 and items[0]["status"] == queue_store.STATUS_BLOCKED


def test_drain_keeps_item_when_no_target(monkeypatch):
    """보낼 pane 이 없어도 지시를 버리지 않는다."""
    monkeypatch.setattr(queue_runner.tmux_target, "resolve_voice_target_pane",
                        lambda: (None, "none"))
    queue_store.add("echo hi")
    r = queue_runner.drain_once()
    assert not r["ok"] and r["error"] == "no_target"
    assert len(queue_store.list_items()) == 1


def test_drain_keeps_item_when_send_fails(monkeypatch):
    monkeypatch.setattr(queue_runner.tmux_target, "resolve_voice_target_pane",
                        lambda: ("%9", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: False)
    queue_store.add("echo hi")
    r = queue_runner.drain_once()
    assert not r["ok"] and r["error"] == "send_failed"
    assert len(queue_store.list_items()) == 1


def test_drain_sends_one_at_a_time(monkeypatch):
    """한 번에 두 건을 붙여 보내면 에이전트가 한 입력으로 읽는다."""
    sent = []
    monkeypatch.setattr(queue_runner.tmux_target, "resolve_voice_target_pane",
                        lambda: ("%1", "auto"))
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux",
                        lambda p, t: (sent.append(t), True)[1])
    queue_store.add("첫째")
    queue_store.add("둘째")
    r = queue_runner.drain_once()
    assert r["drained"] == 1 and sent == ["첫째"]
    assert r["remaining"] == 1
    queue_runner.drain_once()
    assert sent == ["첫째", "둘째"]


def test_drain_on_empty_queue_is_noop():
    r = queue_runner.drain_once()
    assert r["ok"] and r["drained"] == 0


def test_explicit_target_uses_named_session(monkeypatch):
    monkeypatch.setattr(queue_runner.tmux_target, "session_pane",
                        lambda name: "%7" if name == "dev" else None)
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: True)
    queue_store.add("echo hi", target="dev")
    r = queue_runner.drain_once()
    assert r["ok"] and r["pane"] == "%7" and r["mode"] == "session:dev"


def test_session_scoped_pop_skips_other_sessions_target(monkeypatch):
    """세션 스코프 드레인에서는 다른 세션 몫 항목을 건너뛰고 큐에 남겨야 한다."""
    queue_store.add("for-dev", target="dev")
    queue_store.add("untargeted")
    item = queue_store.pop_next("staging", session_scoped=True)
    assert item["text"] == "untargeted"  # 미지정 항목은 여전히 대상
    remaining = queue_store.list_items()
    assert len(remaining) == 1 and remaining[0]["text"] == "for-dev"


def test_session_scoped_pop_matches_own_session(monkeypatch):
    queue_store.add("for-dev", target="dev")
    item = queue_store.pop_next("dev", session_scoped=True)
    assert item["text"] == "for-dev"


def test_unscoped_pop_ignores_target_mismatch(monkeypatch):
    """수동 실행(session_scoped=False)은 세션 매칭 없이 맨 앞 항목을 그대로 꺼낸다."""
    queue_store.add("for-dev", target="dev")
    item = queue_store.pop_next("staging")
    assert item["text"] == "for-dev"


def test_drain_once_session_scoped_leaves_mismatched_item_pending(monkeypatch):
    monkeypatch.setattr(queue_runner.tmux_target, "session_pane",
                        lambda name: "%7" if name == "dev" else None)
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda p, t: True)
    queue_store.add("for-dev", target="dev")
    r = queue_runner.drain_once(session="staging", session_scoped=True)
    assert r["ok"] and r["drained"] == 0
    assert queue_store.pending_count() == 1


def test_autodrain_toggle(monkeypatch):
    monkeypatch.setenv("VT_QUEUE_AUTODRAIN", "0")
    assert queue_runner.autodrain_enabled() is False
    monkeypatch.setenv("VT_QUEUE_AUTODRAIN", "1")
    assert queue_runner.autodrain_enabled() is True
