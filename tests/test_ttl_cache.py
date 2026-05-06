"""D7: ttl_cache.py TTL + 스레드 안전 단위 테스트."""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from ttl_cache import TTLCache


def test_set_and_get():
    c = TTLCache(ttl=10.0)
    c.set("k", 42)
    assert c.get("k") == 42


def test_ttl_expiry():
    c = TTLCache(ttl=0.05)
    c.set("k", 99)
    time.sleep(0.1)
    assert c.get("k") is None


def test_get_or_fetch_miss_calls_fetcher():
    c = TTLCache(ttl=10.0)
    calls = []
    def fetcher(): calls.append(1); return "val"
    result = c.get_or_fetch("k", fetcher)
    assert result == "val"
    assert len(calls) == 1


def test_get_or_fetch_hit_skips_fetcher():
    c = TTLCache(ttl=10.0)
    calls = []
    def fetcher(): calls.append(1); return "val"
    c.get_or_fetch("k", fetcher)
    c.get_or_fetch("k", fetcher)
    assert len(calls) == 1


def test_invalidate_removes_entry():
    c = TTLCache(ttl=10.0)
    c.set("k", 1)
    c.invalidate("k")
    assert c.get("k") is None


def test_clear_removes_all():
    c = TTLCache(ttl=10.0)
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


def test_thread_safe_concurrent_writes():
    """D7: 여러 스레드가 동시에 set/get 해도 크래시 없음."""
    c = TTLCache(ttl=10.0)
    errors = []

    def worker(i):
        try:
            for _ in range(100):
                c.set(f"key-{i}", i)
                c.get(f"key-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []
