"""재시도 잠금 — OTP·비밀번호 공용. **메모리에만 있다**(파일 없음).

서버가 재시작되면 잠금이 풀리는데 그건 의도다: 잠금의 목적은 온라인 무차별
대입을 늦추는 것이지 영구 차단이 아니고, 파일로 만들면 그 파일 자체가 또
하나의 동기화 대상이자 공격 표면이 된다.
"""

from __future__ import annotations

import time

import auth

# D14: 같은 구조를 비밀번호 재시도 잠금에도 그대로 재사용한다 — OTP만 잠금이 있고 비밀번호는
# 무제한 시도가 가능했던 비일관성을 없앤다.
class _KeyedLockout:
    def __init__(self, max_fails: int, lock_sec: int):
        self._max_fails = max_fails
        self._lock_sec = lock_sec
        self._failures: dict[str, list[float]] = {}

    def lock_remaining(self, key: str) -> int:
        fails = self._failures.get(key) or []
        if len(fails) < self._max_fails:
            return 0
        elapsed = time.time() - fails[-1]
        return max(0, int(self._lock_sec - elapsed))

    def note_failure(self, key: str) -> None:
        now = time.time()
        fails = [t for t in self._failures.get(key, []) if now - t < self._lock_sec]
        fails.append(now)
        self._failures[key] = fails
        self._evict_stale(now)

    def reset_failures(self, key: str) -> None:
        self._failures.pop(key, None)

    def failure_count(self, key: str) -> int:
        return len(self._failures.get(key) or [])

    def _evict_stale(self, now: float) -> None:
        """키 수가 상한을 넘으면 이미 잠금이 풀린(만료된) 키부터 정리한다."""
        if len(self._failures) <= auth._MAX_LOCKOUT_KEYS:
            return
        self._failures = {
            k: v for k, v in self._failures.items()
            if v and now - v[-1] < self._lock_sec
        }


class _GlobalWindow:
    """출처와 무관한 전체 실패 상한(슬라이딩 창 여러 개). 키별 잠금은 출처를 바꿔 가며 때리면(IP 순환) 뚫리므로,
    그 위에 전체 총량을 둔다. code-server의 「분당 2회 + 시간당 12회」와 같은 발상이고 값은 넉넉히 잡았다."""

    def __init__(self, windows: tuple[tuple[int, int], ...]):
        self._windows = windows  # (창 길이 초, 그 안의 최대 실패 수)
        self._fails: list[float] = []

    def remaining(self) -> int:
        now = time.time()
        wait = 0
        for span, limit in self._windows:
            recent = [t for t in self._fails if now - t < span]
            if len(recent) >= limit:
                wait = max(wait, int(span - (now - recent[-limit])) + 1)
        return wait

    def note(self) -> None:
        now = time.time()
        longest = max(span for span, _ in self._windows)
        self._fails = [t for t in self._fails if now - t < longest]
        self._fails.append(now)


_otp_lockout = _KeyedLockout(auth.OTP_MAX_FAILS, auth.OTP_LOCK_SEC)
_password_lockout = _KeyedLockout(auth.PASSWORD_MAX_FAILS, auth.PASSWORD_LOCK_SEC)


def otp_lock_remaining(key: str) -> int:
    """key(보통 클라이언트 IP)가 잠금 중이면 남은 초, 아니면 0."""
    return _otp_lockout.lock_remaining(key)


def otp_note_failure(key: str) -> None:
    _otp_lockout.note_failure(key)


def otp_reset_failures(key: str) -> None:
    _otp_lockout.reset_failures(key)


def otp_failure_count(key: str) -> int:
    return _otp_lockout.failure_count(key)


def password_lock_remaining(key: str) -> int:
    """key(보통 클라이언트 IP)가 잠금 중이면 남은 초, 아니면 0."""
    return _password_lockout.lock_remaining(key)


def password_note_failure(key: str) -> None:
    _password_lockout.note_failure(key)


def password_reset_failures(key: str) -> None:
    _password_lockout.reset_failures(key)


# 등록되지 않은 기기의 비밀번호·OTP 실패 전체 총량. 등록된 기기(유효한 vt_device 쿠키)는 이 상한에서 빠진다 —
# 안 그러면 URL을 아는 누구나 실패를 채워 주인의 폰까지 잠근다. 상한에 걸리면 새 기기 등록만 잠시 막힌다.
_new_device_failures = _GlobalWindow(auth.NEW_DEVICE_FAIL_WINDOWS)


def new_device_lock_remaining() -> int:
    return _new_device_failures.remaining()


def new_device_note_failure() -> None:
    _new_device_failures.note()


def reset_all() -> None:
    """모든 잠금 카운터를 비운다. **테스트 격리 전용.**

    예전에는 `importlib.reload(auth)`가 이 파일까지 다시 돌려 카운터가 새로
    만들어졌다. 패키지로 바뀐 뒤로는 reload가 `__init__`만 다시 돌리고 하위
    모듈은 sys.modules에 그대로 남으므로, 앞 테스트의 실패 횟수가 다음
    테스트로 새어 들어간다(실측: 첫 로그인부터 429). reload 흉내를 내는
    대신 **무엇을 비우는지 이름으로 말하는** 훅을 둔다.
    """
    _otp_lockout._failures.clear()
    _password_lockout._failures.clear()
    _new_device_failures._fails.clear()
