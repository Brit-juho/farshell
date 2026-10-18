"""P3-4(60-observability.md §2) — FailureBackoff 단위 테스트."""

import threading

import pytest

import failure_backoff
from failure_backoff import FailureBackoff


@pytest.fixture
def clock(monkeypatch):
    """가짜 단조 시계 — 실제 sleep 의 여유(수십 ms)는 부하가 있는 CI·전체 실행에서 깨졌다."""
    now = [1000.0]
    monkeypatch.setattr(failure_backoff.time, "monotonic", lambda: now[0])
    return now


def test_fresh_key_is_never_skipped():
    b = FailureBackoff(delay=10.0)
    assert b.should_skip("k") is False


def test_marking_failed_skips_within_the_delay():
    b = FailureBackoff(delay=10.0)
    b.mark_failed("k")
    assert b.should_skip("k") is True


def test_should_skip_stops_after_the_delay_elapses(clock):
    b = FailureBackoff(delay=0.05)
    b.mark_failed("k")
    assert b.should_skip("k") is True
    clock[0] += 0.1
    assert b.should_skip("k") is False


def test_clear_releases_immediately():
    """성공하면 바로 풀어야
    "죽은 것으로 낙인"이 안 된다."""
    b = FailureBackoff(delay=10.0)
    b.mark_failed("k")
    assert b.should_skip("k") is True
    b.clear("k")
    assert b.should_skip("k") is False


def test_clear_of_an_unknown_key_does_not_raise():
    b = FailureBackoff(delay=10.0)
    b.clear("never-failed")  # no-op, no exception


def test_keys_are_independent():
    b = FailureBackoff(delay=10.0)
    b.mark_failed("a")
    assert b.should_skip("a") is True
    assert b.should_skip("b") is False


def test_re_marking_failed_extends_the_backoff(clock):
    b = FailureBackoff(delay=0.1)
    b.mark_failed("k")
    clock[0] += 0.05
    b.mark_failed("k")  # 다시 실패 — 기한이 지금부터 다시 시작
    clock[0] += 0.07    # 첫 mark로부터는 0.12초 지났지만 두 번째로부터는 0.07초
    assert b.should_skip("k") is True


def test_thread_safe_concurrent_use():
    b = FailureBackoff(delay=10.0)
    errors = []

    def worker(i):
        try:
            for _ in range(100):
                b.mark_failed(f"k{i}")
                b.should_skip(f"k{i}")
                b.clear(f"k{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
