"""워크트리 캐시 계층 — 2026-09-16 실측 정지 사고의 회귀 방지.

증상: `/api/capabilities`(파일시스템을 안 만지는 최경량 엔드포인트) 300회 중
2회가 14.8초·20.0초였다(중앙값 0.9ms · p90 1.3ms). 서버 전체가 그 동안 멎으므로
HTTP·WebSocket·PTY 출력 브로드캐스트가 같이 멈춘다 — 터미널이 덜컥거리는 증상의
실체다.

`sample`로 뜬 스택은 표본 2582개 중 2575개(99.7%)가 `os_scandir` → `__opendir2`
→ `open$NOCANCEL` 한 지점이었다. 원인은 결함 **세 개가 곱해진 것**이다:

  1. 라우트가 이벤트 루프 위에서 동기 탐색을 직접 호출했다
     (`routes/worktree.py`. 같은 저장소의 `routes/git.py`는 to_thread를 쓴다)
  2. TTL(5초)이 프런트 폴링 주기(8초)보다 짧아 **캐시 적중률이 0**이었다
  3. single-flight가 없어 동시 요청이 각자 전체 탐색(실측 1.0~1.7초)을 직렬로
     반복했다 — 1.3초가 15~20초로 불어난 경로가 이것이다

아래 테스트가 셋을 각각 고정한다.
"""

import asyncio
import json
import threading
import time

import pytest

import worktree
import routes.worktree as worktree_routes


@pytest.fixture(autouse=True)
def _clean_cache():
    worktree.invalidate_cache()
    yield
    worktree.invalidate_cache()


# --- 1) 이벤트 루프를 막지 않는다 ---------------------------------------------

def test_route_does_not_block_the_event_loop(monkeypatch):
    """탐색이 도는 동안에도 이벤트 루프가 계속 돌아야 한다.

    핵심 결함이라 "to_thread를 쓰는가"를 소스에서 찾지 않고 **실제로 루프가
    살아 있는지**를 잰다. 회귀하면(동기 호출로 되돌아가면) 심장박동 간격이
    탐색 시간만큼 벌어져 바로 걸린다.
    """
    BLOCK = 0.5

    def slow_list(force: bool = False):
        time.sleep(BLOCK)
        return []

    monkeypatch.setattr(worktree, "list_worktrees", slow_list)

    async def scenario():
        stop = asyncio.Event()
        gaps = []

        async def heartbeat():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        hb = asyncio.create_task(heartbeat())
        await worktree_routes.list_worktrees()
        stop.set()
        await hb
        return gaps

    gaps = asyncio.run(scenario())
    assert gaps, "심장박동이 한 번도 안 뛰었다"
    worst = max(gaps)
    assert worst < BLOCK / 2, (
        f"이벤트 루프가 {worst:.2f}초 멈췄다(탐색 {BLOCK}초) — "
        "라우트가 동기 호출로 되돌아갔다"
    )


async def _measure_max_gap(coro_factory) -> float:
    """coro_factory()가 도는 동안 심장박동 간격의 최댓값을 잰다."""
    stop = asyncio.Event()
    gaps = []

    async def heartbeat():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    await coro_factory()
    stop.set()
    await hb
    assert gaps, "심장박동이 한 번도 안 뛰었다"
    return max(gaps)


class _FakeBody:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


# ADR-29 A0(2026-09-18) — create/open/delete 세 라우트가 to_thread 없이
# worktree.py의 동기 함수를 직접 불렀다. create는 git worktree add +
# node_modules 복사(수십 초 가능), open/delete는 find_by_id(force=True)로
# 캐시를 무시한 전체 재탐색(실측 1.0~1.7초)을 돈다 — 워크트리 버튼을 누를
# 때마다 서버 전체(HTTP·WS·PTY 출력)가 멎었다. list_worktrees(위 테스트)와
# 같은 방식으로 셋 다 고정한다.

def test_create_route_does_not_block_the_event_loop(monkeypatch):
    BLOCK = 0.5
    monkeypatch.setattr(worktree, "create_worktree", lambda body: (time.sleep(BLOCK), {"ok": True})[1])

    worst = asyncio.run(_measure_max_gap(lambda: worktree_routes.create_worktree(_FakeBody({}))))
    assert worst < BLOCK / 2, f"POST /api/worktrees가 이벤트 루프를 {worst:.2f}초 막았다"


def test_open_route_does_not_block_the_event_loop(monkeypatch):
    BLOCK = 0.5
    monkeypatch.setattr(worktree, "open_worktree", lambda wt_id: (time.sleep(BLOCK), {"ok": True})[1])

    worst = asyncio.run(_measure_max_gap(lambda: worktree_routes.open_worktree("x")))
    assert worst < BLOCK / 2, f"POST /api/worktrees/{{id}}/open이 이벤트 루프를 {worst:.2f}초 막았다"


def test_delete_route_does_not_block_the_event_loop(monkeypatch):
    BLOCK = 0.5
    monkeypatch.setattr(
        worktree, "delete_worktree",
        lambda wt_id, force=False, kill_sessions=False: (time.sleep(BLOCK), {"ok": True})[1],
    )

    worst = asyncio.run(_measure_max_gap(lambda: worktree_routes.delete_worktree("x", _FakeBody({}))))
    assert worst < BLOCK / 2, f"DELETE /api/worktrees/{{id}}가 이벤트 루프를 {worst:.2f}초 막았다"


def test_create_route_still_returns_worktree_error_payload(monkeypatch):
    """to_thread로 감싼 뒤에도 WorktreeError가 여전히 잡혀서 상태코드로 나가야 한다."""
    def boom(body):
        raise worktree.WorktreeError(409, {"error": "이미 존재하는 경로입니다"})

    monkeypatch.setattr(worktree, "create_worktree", boom)
    resp = asyncio.run(worktree_routes.create_worktree(_FakeBody({})))
    assert resp.status_code == 409
    assert json.loads(resp.body)["error"] == "이미 존재하는 경로입니다"


# --- 2) TTL이 폴링 주기보다 길다 ----------------------------------------------

def test_cache_ttl_exceeds_client_poll_interval():
    """TTL은 프런트 폴링 주기보다 **길어야** 한다.

    짧으면 모든 폴링이 캐시를 빗나가 매번 전체 탐색이 돈다 — 캐시가 있는데도
    없는 것과 같아지는, 조용히 틀리는 종류의 결함이다. 실제로 TTL 5초에 폴링
    8초라 적중률이 0이었고 아무도 몰랐다.
    """
    poll_ms = 8000  # frontend/js/shell/Rail.tsx WORKTREES_POLL_MS
    assert worktree.CACHE_TTL_SEC > poll_ms / 1000.0


# --- 3) single-flight ----------------------------------------------------------

def test_concurrent_calls_run_discovery_once(monkeypatch):
    """동시 호출이 탐색을 한 번만 돌린다.

    잠금이 없으면 N개 요청이 각자 1.3초짜리 탐색을 직렬 반복해 정지가 N배가
    된다. 폴링 주체가 Rail(8초)·tab-worktree(10초)·Fleet이고 여기에 탭·기기
    수가 곱해지므로 N은 쉽게 커진다.
    """
    calls = []

    def slow_discover():
        calls.append(1)
        time.sleep(0.3)   # 뒤따르는 스레드가 확실히 잠금에서 대기하도록
        return [{"id": "x"}]

    monkeypatch.setattr(worktree, "_discover_all", slow_discover)

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(worktree.list_worktrees()))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(calls) == 1, f"탐색이 {len(calls)}회 돌았다 — single-flight가 깨졌다"
    assert results == [[{"id": "x"}]] * 8


# --- 4) stale-while-revalidate -------------------------------------------------

def test_stale_value_is_served_immediately(monkeypatch):
    """TTL은 지났지만 너무 낡지는 않은 값은 **즉시** 돌려주고 갱신은 뒤에서.

    호출자를 1초 넘게 붙잡는 비용이, 목록이 몇 초 늦는 비용보다 훨씬 크다.
    """
    gen = {"n": 0}
    refreshed = threading.Event()

    def discover():
        gen["n"] += 1
        if gen["n"] > 1:
            time.sleep(0.2)
            refreshed.set()
        return [{"gen": gen["n"]}]

    monkeypatch.setattr(worktree, "_discover_all", discover)
    assert worktree.list_worktrees() == [{"gen": 1}]

    with worktree._cache_lock:   # TTL 밖 · STALE 안으로 늙힌다
        worktree._cache["at"] = time.time() - (worktree.CACHE_TTL_SEC + 1)

    t0 = time.perf_counter()
    stale = worktree.list_worktrees()
    elapsed = time.perf_counter() - t0

    assert stale == [{"gen": 1}], "낡은 값을 즉시 돌려줘야 한다"
    assert elapsed < 0.1, f"낡은 값 반환에 {elapsed:.2f}초 — 배경 갱신을 기다렸다"

    assert refreshed.wait(timeout=5), "배경 갱신이 돌지 않았다"
    for _ in range(50):
        if worktree.list_worktrees() == [{"gen": 2}]:
            break
        time.sleep(0.05)
    assert worktree.list_worktrees() == [{"gen": 2}]


def test_background_refresh_does_not_pile_up(monkeypatch):
    """낡은 값을 여러 번 요청해도 갱신 스레드는 하나만 돈다.

    이 검사가 없으면 폴링 주체 수만큼 갱신 스레드가 동시에 생긴다.
    """
    calls = []

    def discover():
        calls.append(1)
        time.sleep(0.3)
        return []

    monkeypatch.setattr(worktree, "_discover_all", discover)
    worktree.list_worktrees()          # 1회차(캐시 채우기)
    with worktree._cache_lock:
        worktree._cache["at"] = time.time() - (worktree.CACHE_TTL_SEC + 1)

    for _ in range(10):
        worktree.list_worktrees()      # 전부 낡은 값 즉시 반환 + 배경 갱신 요청

    time.sleep(0.6)
    assert len(calls) == 2, f"탐색이 {len(calls)}회 — 배경 갱신 스레드가 쌓였다"


def test_too_stale_value_is_not_served(monkeypatch):
    """CACHE_STALE_SEC를 넘긴 값은 그대로 내주지 않고 새로 탐색한다.

    오래 idle이던 서버에서 처음 열린 화면이 몇 분 전 상태를 보면 안 된다.
    """
    gen = {"n": 0}

    def discover():
        gen["n"] += 1
        return [{"gen": gen["n"]}]

    monkeypatch.setattr(worktree, "_discover_all", discover)
    assert worktree.list_worktrees() == [{"gen": 1}]

    with worktree._cache_lock:
        worktree._cache["at"] = time.time() - (worktree.CACHE_STALE_SEC + 1)

    assert worktree.list_worktrees() == [{"gen": 2}]


def test_force_always_rediscovers(monkeypatch):
    """force=True는 캐시가 아무리 신선해도 다시 탐색한다.

    생성·삭제 직후 invalidate_cache()와 짝을 이루는 경로다.
    """
    calls = []
    monkeypatch.setattr(worktree, "_discover_all", lambda: calls.append(1) or [])
    worktree.list_worktrees()
    worktree.list_worktrees()          # 캐시 적중
    assert len(calls) == 1
    worktree.list_worktrees(force=True)
    assert len(calls) == 2
