"""SetupManager — 진행 갱신, 성공/실패, 중복 시작 방지."""
import threading

import pytest

from stt_engines.base import NotReady
from stt_engines.setup_jobs import SetupBusy, SetupManager


def test_status_none_initially():
    assert SetupManager().status() is None


def test_blocking_success_and_progress():
    m = SetupManager()

    def fn(progress):
        progress(10, 100)
        progress(50, 100)
    job = m.start("mdl", "model", "모델 받기", fn, blocking=True)
    assert job["state"] == "done" and job["error"] is None and job["model"] == "mdl"
    step = job["steps"][0]
    assert step == {"id": "model", "label": "모델 받기", "state": "done", "done": 50, "total": 100}
    assert m.status() == job                           # 끝난 작업이 남는다


def test_success_unknown_total_filled_from_done():
    m = SetupManager()
    job = m.start("m", "req:x", "x", lambda p: p(7, None), blocking=True)
    assert job["steps"][0]["total"] == 7


def test_success_without_progress():
    job = SetupManager().start("m", "model", "l", lambda p: None, blocking=True)
    assert job["state"] == "done" and job["steps"][0]["total"] is None


def test_zero_total_treated_as_unknown():
    m = SetupManager()
    job = m.start("m", "model", "l", lambda p: p(5, 0), blocking=True)
    assert job["steps"][0]["total"] == 5


def test_notready_failure_message():
    def fn(progress):
        raise NotReady("디스크 공간이 부족합니다")
    job = SetupManager().start("m", "model", "l", fn, blocking=True)
    assert job["state"] == "error" and job["error"] == "디스크 공간이 부족합니다"
    assert job["steps"][0]["state"] == "error"


def test_generic_exception_message_has_type():
    def fn(progress):
        raise ConnectionError("dns")
    job = SetupManager().start("m", "model", "l", fn, blocking=True)
    assert job["state"] == "error" and job["error"] == "ConnectionError: dns"


@pytest.mark.parametrize("exc", [NotReady, RuntimeError])
def test_error_length_capped(exc):
    def fn(progress):
        raise exc("가" * 5000)
    job = SetupManager().start("m", "model", "l", fn, blocking=True)
    assert len(job["error"]) == 300


def test_new_job_after_finished_allowed_and_replaces():
    m = SetupManager()
    a = m.start("a", "model", "l", lambda p: None, blocking=True)
    b = m.start("b", "model", "l", lambda p: None, blocking=True)
    assert m.status()["model"] == "b" and a["id"] != b["id"]


def test_new_job_after_error_allowed():
    m = SetupManager()

    def bad(p):
        raise NotReady("x")
    m.start("a", "model", "l", bad, blocking=True)
    assert m.start("a", "model", "l", lambda p: None, blocking=True)["state"] == "done"


def test_busy_while_running_threaded():
    m = SetupManager()
    go, started = threading.Event(), threading.Event()

    def slow(progress):
        progress(1, 10)
        started.set()
        assert go.wait(5)
    job = m.start("first", "model", "l", slow)
    assert job["state"] == "running"
    assert started.wait(5)
    with pytest.raises(SetupBusy, match="first"):
        m.start("second", "req:x", "l", lambda p: None)
    with pytest.raises(SetupBusy):
        m.start("second", "req:x", "l", lambda p: None, blocking=True)
    mid = m.status()
    assert mid["state"] == "running" and mid["steps"][0]["done"] == 1 and mid["steps"][0]["total"] == 10
    go.set()
    for _ in range(200):
        if m.status()["state"] != "running":
            break
        threading.Event().wait(0.02)
    assert m.status()["state"] == "done" and m.status()["model"] == "first"
    # 끝나면 다시 시작 가능
    assert m.start("second", "model", "l", lambda p: None, blocking=True)["state"] == "done"


def test_threaded_failure_recorded():
    m = SetupManager()
    done = threading.Event()

    def bad(p):
        try:
            raise NotReady("실패")
        finally:
            done.set()
    m.start("m", "model", "l", bad)
    assert done.wait(5)
    for _ in range(200):
        if m.status()["state"] != "running":
            break
        threading.Event().wait(0.02)
    assert m.status()["state"] == "error" and m.status()["error"] == "실패"


def test_status_is_a_copy():
    m = SetupManager()
    m.start("m", "model", "l", lambda p: p(1, 2), blocking=True)
    s = m.status()
    s["steps"][0]["done"] = 999
    s["state"] = "x"
    assert m.status()["steps"][0]["done"] == 1 and m.status()["state"] == "done"


def test_busy_does_not_clobber_running_job():
    m = SetupManager()
    go, started = threading.Event(), threading.Event()
    m.start("a", "model", "l", lambda p: (started.set(), go.wait(5)))
    started.wait(5)
    with pytest.raises(SetupBusy):
        m.start("b", "model", "l", lambda p: None, blocking=True)
    assert m.status()["model"] == "a"
    go.set()
