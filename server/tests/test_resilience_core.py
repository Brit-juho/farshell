"""resilience/ 기반 단위 테스트 — 풀·잠금·서브프로세스·마운트·차단기·브로드캐스트.

2026-11-05 M5 사고(NFS open() 정지 → 잠금 무기한 대기 → 기본 스레드 풀 고갈)의 고리를 하나씩
고정한다. 사고 경로 전체를 재현하는 회귀 테스트는 test_resilience_incident.py.
"""
import asyncio
import os
import subprocess
import threading
import time

import pytest

from resilience import breaker, locks, mounts, pools, proc, registry, wsutil


@pytest.fixture(autouse=True)
def _clean():
    pools._reset_for_tests()
    registry._reset_for_tests()
    breaker._reset_for_tests()
    mounts._set_for_tests(None)
    yield
    pools._reset_for_tests()
    registry._reset_for_tests()
    breaker._reset_for_tests()
    mounts._set_for_tests(None)


# --- pools ---------------------------------------------------------------------------------

def test_timeout_frees_caller_and_counts_abandoned_until_thread_ends():
    gate = threading.Event()
    p = pools.get("fs")

    async def scenario():
        t0 = time.monotonic()
        with pytest.raises(pools.OpTimeout):
            await p.run(gate.wait, op="stuck", key="/x", timeout=0.2)
        assert time.monotonic() - t0 < 1.0          # 호출자는 기한에 풀려났다
        assert p.abandoned == 1 and p.inflight == 1  # 스레드는 아직 묶여 있다
        gate.set()
        for _ in range(50):
            if p.inflight == 0:
                break
            await asyncio.sleep(0.02)
        assert p.abandoned == 0 and p.inflight == 0  # 스레드가 끝나면 자리가 돌아온다

    asyncio.run(scenario())


def test_full_pool_rejects_immediately_instead_of_queueing_forever():
    gate = threading.Event()
    p = pools.Pool("t", size=1, queue=1)

    async def scenario():
        a = asyncio.ensure_future(p.run(gate.wait))
        b = asyncio.ensure_future(p.run(gate.wait))
        await asyncio.sleep(0.05)
        with pytest.raises(pools.Busy):
            await p.run(gate.wait)
        gate.set()
        await asyncio.gather(a, b)

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
        p.shutdown()


def test_cancelled_queued_work_returns_its_slot():
    gate = threading.Event()
    p = pools.Pool("t2", size=1, queue=4)

    async def scenario():
        running = asyncio.ensure_future(p.run(gate.wait))
        await asyncio.sleep(0.05)
        queued = asyncio.ensure_future(p.run(lambda: None))
        await asyncio.sleep(0.05)
        queued.cancel()                      # 요청이 끊긴 경우 — 실행 전 취소
        await asyncio.sleep(0.05)
        assert p.inflight == 1
        gate.set()
        await running

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
        p.shutdown()


def test_probe_detects_pool_whose_workers_are_all_stuck():
    gate = threading.Event()

    async def scenario():
        p = pools.get("proc")
        tasks = [asyncio.ensure_future(p.run(gate.wait)) for _ in range(p.size)]
        await asyncio.sleep(0.1)
        assert await pools.probe("proc", timeout=0.3) is False
        assert await pools.probe("git", timeout=0.3) is True     # 다른 풀은 영향받지 않는다
        gate.set()
        await asyncio.gather(*tasks)
        assert await pools.probe("proc", timeout=1.0) is True

    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_default_executor_is_instrumented_with_real_function_names():
    seen = {}

    def work_named_for_test():
        seen["ops"] = [r.op for r in registry.snapshot()]

    async def scenario():
        pools.install_default_executor(asyncio.get_running_loop())
        await asyncio.to_thread(work_named_for_test)

    asyncio.run(scenario())
    assert any("work_named_for_test" in op for op in seen["ops"])


# --- locks ---------------------------------------------------------------------------------

def test_timed_lock_reports_holder_and_times_out_waiters():
    lk = locks.TimedLock("test.lock")
    held = threading.Event()
    release = threading.Event()

    def holder():
        with lk.hold(None, op="slow-io"):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder, name="holder-thread")
    t.start()
    held.wait(2)
    t0 = time.monotonic()
    with pytest.raises(locks.LockTimeout) as ei:
        with lk.hold(0.2, op="waiter"):
            pass
    assert time.monotonic() - t0 < 1.0
    assert ei.value.holder["op"] == "slow-io"
    assert ei.value.holder["thread"] == "holder-thread"
    snap = lk.snapshot()
    assert snap["held"] and snap["timeouts"] == 1
    release.set()
    t.join(2)
    assert not lk.locked()


# --- proc ----------------------------------------------------------------------------------

def test_proc_run_kills_process_group_and_returns_promptly():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        # 자식이 손자를 만들어도 그룹째 죽인다(파이프를 쥔 손자 때문에 communicate 가 안 끝나는 일 방지)
        proc.run(["/bin/sh", "-c", "sleep 30 & sleep 30"], timeout=0.3)
    assert time.monotonic() - t0 < proc.REAP_TIMEOUT + 2


def test_proc_run_returns_completed_process():
    cp = proc.run(["/bin/echo", "hi"], timeout=5)
    assert cp.returncode == 0 and cp.stdout == b"hi\n"


def test_proc_run_requires_a_deadline():
    with pytest.raises(ValueError):
        proc.run(["/bin/true"], timeout=0)


# --- mounts --------------------------------------------------------------------------------

def test_linux_mountinfo_parsing_unescapes_and_classifies():
    text = (
        "22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
        "40 22 0:44 / /mnt/my\\040share rw - nfs4 server:/export rw\n"
        "41 22 0:45 / /home/u/remote rw - fuse.sshfs u@h:/ rw\n"
        "42 22 0:46 / /media/usb rw - fuseblk /dev/sdb1 rw\n"
    )
    ms = {m.path: m for m in mounts._linux_mounts(text)}
    assert ms["/mnt/my share"].risky
    assert ms["/home/u/remote"].risky
    assert not ms["/media/usb"].risky          # fuseblk(ntfs-3g 등)는 로컬 디스크
    assert not ms["/"].risky


def test_risky_reason_uses_longest_prefix_and_never_touches_fs(monkeypatch, tmp_path):
    nfs = tmp_path / "OrbStack"
    mounts._set_for_tests([
        mounts.Mount("/", "apfs", "/dev/disk3s1", True),
        mounts.Mount(str(nfs), "nfs", "OrbStack:/OrbStack", False),
    ])
    assert mounts.risky_reason(str(nfs / "docker" / "x")).startswith("nfs:")
    assert mounts.risky_reason(str(tmp_path / "OrbStackish")) is None   # 접두사가 아니라 경로 경계로 본다
    assert mounts.risky_reason(str(tmp_path / "GitHub")) is None


def test_scan_include_env_opts_back_in(monkeypatch, tmp_path):
    nfs = tmp_path / "share"
    mounts._set_for_tests([mounts.Mount(str(nfs), "smbfs", "//h/s", False)])
    assert mounts.skip_for_scan(str(nfs / "repo"))
    monkeypatch.setenv("VT_SCAN_INCLUDE_MOUNTS", str(nfs))
    assert mounts.skip_for_scan(str(nfs / "repo")) is None
    monkeypatch.setenv("VT_SCAN_INCLUDE_MOUNTS", "all")
    # 원격 마운트는 모두 다시 들어간다. macOS 보호 위치는 별도 설정(VT_SCAN_INCLUDE_PROTECTED)을 따른다.
    assert mounts.scan_skip_prefixes() == sorted(set(mounts.protected_roots()))
    monkeypatch.setenv("VT_SCAN_INCLUDE_PROTECTED", "1")
    assert mounts.scan_skip_prefixes() == []


# --- breaker -------------------------------------------------------------------------------

def test_breaker_blocks_then_half_opens(monkeypatch, tmp_path):
    p = str(tmp_path / "slow")
    breaker.trip(p, "test")
    with pytest.raises(breaker.PathBlocked):
        breaker.check(p + "/child")
    breaker.check(str(tmp_path / "other"))          # 다른 경로는 그대로
    monkeypatch.setattr(breaker, "OPEN_SEC", 0.0)
    breaker.trip(p, "test")
    breaker.check(p)                                 # 시간이 지나 한 번 시도 허용(half-open)
    with pytest.raises(breaker.PathBlocked):
        breaker.check(p)                             # 시도 중엔 또 막는다
    breaker.success(p)
    breaker.check(p)


# --- wsutil --------------------------------------------------------------------------------

class _FakeWS:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.sent = []
        self.closed = False

    async def send_text(self, text):
        await asyncio.sleep(self.delay)
        self.sent.append(text)

    async def send_json(self, data):
        await self.send_text(data)

    async def close(self, code=1000, reason=""):
        self.closed = True


def test_broadcast_drops_slow_client_without_delaying_others():
    fast, slow = _FakeWS(), _FakeWS(delay=10)
    clients = {fast, slow}

    async def scenario():
        t0 = time.monotonic()
        sent = await wsutil.broadcast(clients, {"type": "x"}, timeout=0.2)
        return sent, time.monotonic() - t0

    sent, took = asyncio.run(scenario())
    assert sent == 1 and took < 1.0
    assert fast.sent and slow.closed
    assert clients == {fast}


# --- macOS 보호 위치(TCC) --------------------------------------------------------------------

def test_protected_locations_are_skipped_only_on_macos(monkeypatch, tmp_path):
    """자동 탐색이 데스크톱·문서·다운로드·Library·외장 볼륨에 들어가면 폴더마다 권한 창이 뜬다(2026-11-06)."""
    monkeypatch.setattr(mounts, "_home", lambda: str(tmp_path))
    monkeypatch.setattr(mounts.sys, "platform", "darwin")
    mounts._set_for_tests([mounts.Mount("/", "apfs", "/dev/disk3s1", True)])
    for rel in ("Desktop", "Documents/proj", "Downloads", "Library/Containers/x", "Pictures"):
        assert mounts.skip_for_scan(str(tmp_path / rel)).startswith("macos-protected:")
    assert mounts.skip_for_scan("/Volumes/USB/repo").startswith("macos-protected:")
    assert mounts.skip_for_scan(str(tmp_path / "GitHub" / "repo")) is None
    assert mounts.skip_for_scan(str(tmp_path / "Documentsish")) is None       # 경로 경계로 본다
    assert str(tmp_path / "Documents") in mounts.scan_skip_prefixes()
    monkeypatch.setenv("VT_SCAN_INCLUDE_PROTECTED", "1")                       # 전체 디스크 접근 권한을 준 경우
    assert mounts.skip_for_scan(str(tmp_path / "Documents" / "proj")) is None
    monkeypatch.delenv("VT_SCAN_INCLUDE_PROTECTED")
    monkeypatch.setattr(mounts.sys, "platform", "linux")
    assert mounts.protected_roots() == []
    assert mounts.skip_for_scan(str(tmp_path / "Documents" / "proj")) is None


def test_repo_scan_does_not_enter_protected_folders(monkeypatch, tmp_path):
    import worktree
    (tmp_path / "GitHub" / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "Documents" / "notes" / ".git").mkdir(parents=True)
    monkeypatch.setattr(mounts, "_home", lambda: str(tmp_path))
    monkeypatch.setattr(mounts.sys, "platform", "darwin")
    mounts._set_for_tests([mounts.Mount("/", "apfs", "/dev/disk3s1", True)])
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    found, _ = worktree._find_git_entrypoints([tmp_path])
    assert {p.name for p in found} == {"repo"}


def test_session_context_reports_protected_cwd(monkeypatch, tmp_path):
    import session_context
    monkeypatch.setattr(mounts, "_home", lambda: str(tmp_path))
    monkeypatch.setattr(mounts.sys, "platform", "darwin")
    mounts._set_for_tests([mounts.Mount("/", "apfs", "/dev/disk3s1", True)])
    out = session_context._context(str(tmp_path / "Downloads" / "x"))
    assert out["status"] == "unavailable" and out["reason"] == "protected"
