"""store_lock — 유계 대기 파일 락.

이 스위트의 핵심 단언은 **기한 안에 돌아온다**는 것 하나다. 예전 관용구
(`fcntl.flock(fd, LOCK_EX)`)는 상대가 락을 쥔 채 멈추면 영원히 안 돌아왔고,
그 함수들이 `async def` 라우트에서 동기로 불리므로 서버 전체가 정지했다.

flock은 **열린 파일 기술자(open file description)** 단위라 같은 프로세스가
`os.open`을 두 번 하면 서로 경쟁한다 — 그래서 경쟁 상황을 서브프로세스 없이
만들 수 있다.
"""

from __future__ import annotations

import fcntl
import os
import time

import pytest

import store_lock


def _hold(path):
    """다른 fd로 락을 쥔다. 호출자가 `os.close`로 놓는다."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_exclusive_acquires_and_releases(tmp_path):
    lock = tmp_path / "x.lock"
    with store_lock.exclusive(lock):
        pass
    # 놓았으니 곧바로 다시 잡힌다.
    with store_lock.exclusive(lock, deadline=0.2):
        pass


def test_lock_file_is_never_removed(tmp_path):
    """E5 — flock은 경로가 아니라 inode를 키로 잡는다.

    락 파일을 지우면 두 프로세스가 같은 경로의 **다른 inode**를 잠그는 창이
    생겨 상호배제가 조용히 깨진다. 그래서 우리는 절대 지우지 않는다.
    """
    lock = tmp_path / "keep.lock"
    with store_lock.exclusive(lock):
        pass
    assert lock.exists(), "락 파일을 지우면 상호배제가 깨진다"


def test_exclusive_gives_up_within_the_deadline(tmp_path):
    """§3의 핵심 — 무한이 아니라 유계다."""
    lock = tmp_path / "busy.lock"
    fd = _hold(lock)
    try:
        start = time.monotonic()
        with pytest.raises(store_lock.LockTimeout):
            with store_lock.exclusive(lock, deadline=0.2):
                pytest.fail("락을 쥔 상대가 있는데 임계구역에 들어갔다")
        elapsed = time.monotonic() - start
    finally:
        os.close(fd)
    assert 0.15 <= elapsed < 1.0, f"기한(0.2초) 근처에서 돌아와야 한다 (실측 {elapsed:.3f}초)"


def test_timeout_message_names_the_lock_file(tmp_path):
    """어느 파일에서 멈췄는지 모르면 진단이 안 된다."""
    lock = tmp_path / "named.lock"
    fd = _hold(lock)
    try:
        with pytest.raises(store_lock.LockTimeout) as exc:
            with store_lock.exclusive(lock, deadline=0.05):
                pass
    finally:
        os.close(fd)
    assert "named.lock" in str(exc.value)


def test_try_exclusive_fails_immediately(tmp_path):
    lock = tmp_path / "try.lock"
    fd = _hold(lock)
    try:
        start = time.monotonic()
        with pytest.raises(store_lock.LockContended):
            with store_lock.try_exclusive(lock):
                pass
        elapsed = time.monotonic() - start
    finally:
        os.close(fd)
    assert elapsed < 0.1, f"기다리지 않아야 한다 (실측 {elapsed:.3f}초)"


def test_try_exclusive_succeeds_when_free(tmp_path):
    lock = tmp_path / "free.lock"
    with store_lock.try_exclusive(lock):
        pass


def test_contended_is_a_locktimeout(tmp_path):
    """호출부가 둘을 따로 잡을 필요가 없게 한다."""
    assert issubclass(store_lock.LockContended, store_lock.LockTimeout)


def test_parent_directory_is_created(tmp_path):
    lock = tmp_path / "deep" / "nested" / "a.lock"
    with store_lock.exclusive(lock):
        pass
    assert lock.exists()


def test_lock_is_released_when_body_raises(tmp_path):
    """예외가 나가도 락은 풀려야 한다 — 안 그러면 한 번의 실패가 영구 교착이 된다."""
    lock = tmp_path / "boom.lock"
    with pytest.raises(ValueError):
        with store_lock.exclusive(lock):
            raise ValueError("의도된 실패")
    with store_lock.exclusive(lock, deadline=0.2):
        pass


def test_no_file_descriptors_leak_on_timeout(tmp_path):
    """기한 초과 경로에서 fd를 닫는지 — 여기서 새면 서버가 오래 돌수록 고갈된다."""
    lock = tmp_path / "fd.lock"
    fd = _hold(lock)
    try:
        before = len(os.listdir("/dev/fd"))
        for _ in range(20):
            with pytest.raises(store_lock.LockTimeout):
                with store_lock.exclusive(lock, deadline=0.0):
                    pass
        after = len(os.listdir("/dev/fd"))
    finally:
        os.close(fd)
    assert after <= before + 2, f"fd가 샌다: {before} → {after}"
