"""2026-11-05 M5 사고 재현 — 탐색이 멈춰도 서버는 계속 일을 받아야 한다.

사고: worktree 탐색 스레드가 NFS(~/OrbStack) 디렉터리 open()에서 영원히 멈춘 채 `_discover_lock`을 쥐었다.
캐시가 낡자(120초+) 요청들이 그 잠금을 기한 없이 기다렸고, 기본 스레드 풀 19개가 모두 묶여
`/api/capabilities`·`/api/tmux/sessions` 를 포함한 모든 to_thread API 가 응답하지 않았다.

여기서는 잠금을 쥔 채 멈춘 탐색을 흉내 내고(실제 FS 정지는 재현할 수 없으므로 Event 로 막는다):
1. list_worktrees 호출 30개가 전부 기한 안에 돌아오고(낡은 값 또는 503),
2. 그동안 기본 실행기가 비어 있으며,
3. 실제 앱의 다른 API·헬스 엔드포인트가 계속 답하는지 본다.
"""
import asyncio
import threading
import time

import pytest
from starlette.testclient import TestClient

import worktree
from resilience import mounts, pools, scan, quarantine


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    worktree.invalidate_cache()
    monkeypatch.setattr(worktree, "DISCOVER_WAIT_SEC", 0.3)
    yield
    worktree.invalidate_cache()


@pytest.fixture
def stuck_discovery():
    """다른 스레드가 탐색 잠금을 쥔 채 멈춰 있다."""
    release = threading.Event()
    held = threading.Event()

    def holder():
        worktree._discover_lock.acquire(op="worktree.discover(stuck)")
        held.set()
        release.wait(30)
        worktree._discover_lock.release()

    t = threading.Thread(target=holder, name="worktree-refresh", daemon=True)
    t.start()
    held.wait(2)
    yield
    release.set()
    t.join(5)


def test_waiters_return_stale_data_within_deadline(stuck_discovery):
    with worktree._cache_lock:
        worktree._cache["data"] = [{"id": "old"}]
        worktree._cache["at"] = time.time() - (worktree.CACHE_STALE_SEC + 5)   # 기다려야 하는 나이

    async def scenario():
        t0 = time.monotonic()
        results = await asyncio.gather(*(asyncio.to_thread(worktree.list_worktrees) for _ in range(30)))
        took = time.monotonic() - t0
        # 30개가 전부 0.3초 기한으로 풀려났다(직렬이어도 기본 풀 크기 단위로 몇 번이면 끝난다)
        assert took < 10, took
        assert all(r == [{"id": "old"}] for r in results)
        # 기본 실행기가 비어 있다 — 사고 때는 여기서 전부 잠금 대기였다
        assert await pools.probe("default", timeout=1.0)

    asyncio.run(scenario())


def test_waiters_get_503_when_there_is_no_previous_list(stuck_discovery):
    with pytest.raises(worktree.WorktreeError) as ei:
        worktree.list_worktrees()
    assert ei.value.status == 503 and ei.value.payload["error"] == "discovery_busy"
    assert worktree.list_worktrees_or_empty() == []


def test_app_keeps_answering_while_discovery_is_stuck(stuck_discovery, monkeypatch):
    import main
    with TestClient(main.app) as c:
        # 레일 목록 — 기다리지 않고 503
        r = c.get("/api/repos")
        assert r.status_code == 503 and r.json()["error"] == "discovery_busy"
        # 사고 때 멈췄던 경량 API 가 그대로 답한다
        t0 = time.monotonic()
        assert c.get("/api/capabilities").status_code == 200
        assert c.get("/api/health/live").json()["ok"] is True
        ready = c.get("/api/health/ready")
        assert ready.status_code == 200, ready.json()
        assert time.monotonic() - t0 < 5
        locks = {lk["name"]: lk for lk in ready.json()["locks"]}
        assert locks["worktree.discover"]["held"]
        assert locks["worktree.discover"]["holder"]["op"] == "worktree.discover(stuck)"


def test_scan_timeout_quarantines_the_directory_and_keeps_previous_list(monkeypatch, tmp_path):
    """탐색 자식 프로세스가 멈추면 죽이고, 마지막 디렉터리를 격리하고, 직전 목록을 계속 쓴다."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    hang = tmp_path / "hang_worker.py"
    hang.write_text(
        "import json,sys,time\n"
        "sys.stdin.readline()\n"
        f"print(json.dumps({{'enter': {str(tmp_path / 'nfs' / 'docker')!r}}}), flush=True)\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(scan, "WORKER", str(hang))
    monkeypatch.setattr(worktree, "SCAN_TIMEOUT", 0.5)
    with worktree._cache_lock:
        worktree._cache["data"] = [{"id": "prev"}]
        worktree._cache["at"] = 0.0
    t0 = time.monotonic()
    assert worktree.list_worktrees(force=True) == [{"id": "prev"}]
    assert time.monotonic() - t0 < 6
    assert str(tmp_path / "nfs" / "docker") in quarantine.paths()
    # 다음 탐색은 격리된 경로를 처음부터 건너뛴다
    assert str(tmp_path / "nfs" / "docker") in scan.skip_prefixes()


def test_scan_never_enters_remote_mounts(monkeypatch, tmp_path):
    (tmp_path / "GitHub" / "repo" / ".git").mkdir(parents=True)
    nfs = tmp_path / "OrbStack"
    (nfs / "docker" / "proj" / ".git").mkdir(parents=True)
    mounts._set_for_tests([mounts.Mount(str(nfs), "nfs", "OrbStack:/OrbStack", False)])
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    try:
        found, _ = worktree._find_git_entrypoints([tmp_path])
    finally:
        mounts._set_for_tests(None)
    names = {p.name for p in found}
    assert "repo" in names and "proj" not in names


def test_session_context_skips_remote_mount_cwd(tmp_path):
    import session_context
    nfs = tmp_path / "OrbStack"
    mounts._set_for_tests([mounts.Mount(str(nfs), "nfs", "x", False)])
    try:
        out = session_context._context(str(nfs / "proj"))
    finally:
        mounts._set_for_tests(None)
    assert out["status"] == "unavailable" and out["reason"] == "remote_mount"
