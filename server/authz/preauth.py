"""사전 인증 실패 한도 — 자격을 증명하기 전의 요청을 출처(source)별로 센다. **메모리에만 있고 재시작하면 비워진다.**

  - 실패한 peer 서명 · 무효한 쿠키/토큰 · 틀린 로그인/티켓/페어링 코드가 출처별로 쌓인다.
    60초 안에 10번이면 그 출처는 5분 동안 429(`Retry-After`)다. 잠금 중에는 맞는 자격도 받지 않는다(추측 속도를 묶는 것이 목적).
  - 자격 교환 엔드포인트(`EXCHANGE_PATHS`: 로그인·peer 페어링)는 출처와 무관하게 전역 분당 300회. 넘으면 429.
  - unix socket 요청은 이 한도를 지나지 않는다(커널이 확인한 peer 라 추측할 자격이 없다) — `main.PreauthMiddleware` 가 건너뛴다.
  - Origin 검사는 루프백도 면제하지 않는다(`main.OriginGuardMiddleware` 는 출처 주소를 보지 않는다).
  - 거부는 `denylog` 에 남는다(분당 상한 안에서). 기록 실패는 요청을 막지 않는다.

출처 키: `VT_TRUST_PROXY=1` 이면 프록시가 알려 준 실제 IP. 아니면 직결 주소 — 단 루프백에서 온 요청이 터널 헤더(`CF-Connecting-IP`,
`X-Forwarded-For` 의 맨 오른쪽)를 달고 있으면 그 값을 키로 쓴다(터널 뒤의 모든 원격 사용자가 한 키로 뭉쳐 서로 잠그지 않게).
헤더가 없는 루프백은 `127.0.0.1` 한 키다. 시계는 주입할 수 있다(`Limiter(clock=…)`).
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Callable, Mapping, Optional

FAILS = 10
WINDOW = 60.0
LOCK_SEC = 300.0
EXCHANGE_PER_MIN = 300
MAX_KEYS = 4096

# 로그인·페어링처럼 자격을 처음 주고받는 경로. 전역 상한의 대상이다.
EXCHANGE_PATHS = ("/api/auth", "/api/peer/pair")
# 잠금 중에도 열어 두는 경로: 로그인 화면·정적 파일과 tmux 훅 신호(자체 비밀값이 인증이다).
PUBLIC_PATHS = ("/", "/sw.js", "/manifest.json", "/favicon.ico", "/api/auth/status", "/api/tmux/kick")
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


class Limiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, *, fails: int = FAILS, window: float = WINDOW,
                 lock_sec: float = LOCK_SEC, exchange_per_min: int = EXCHANGE_PER_MIN):
        self.clock = clock
        self.fails, self.window, self.lock_sec, self.exchange_per_min = fails, window, lock_sec, exchange_per_min
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._exchange: list[float] = []
        self._mu = threading.Lock()

    def lock_remaining(self, source: str) -> int:
        """남은 잠금 초(올림). 잠겨 있지 않으면 0."""
        with self._mu:
            until = self._locked_until.get(source)
            if until is None:
                return 0
            left = until - self.clock()
            if left <= 0:
                del self._locked_until[source]
                self._failures.pop(source, None)
                return 0
            return max(1, math.ceil(left))

    def note_failure(self, source: str) -> int:
        """실패 한 번. 이 실패로(또는 이미) 잠겼으면 남은 초, 아니면 0."""
        with self._mu:
            now = self.clock()
            until = self._locked_until.get(source)
            if until is not None and until > now:
                return max(1, math.ceil(until - now))
            hits = [t for t in self._failures.get(source, []) if now - t < self.window]
            hits.append(now)
            self._failures[source] = hits
            self._evict(now)
            if len(hits) >= self.fails:
                self._locked_until[source] = now + self.lock_sec
                self._failures.pop(source, None)
                return int(self.lock_sec)
            return 0

    def _evict(self, now: float) -> None:
        if len(self._failures) > MAX_KEYS:
            self._failures = {k: v for k, v in self._failures.items() if v and now - v[-1] < self.window}
        if len(self._locked_until) > MAX_KEYS:
            self._locked_until = {k: v for k, v in self._locked_until.items() if v > now}

    def exchange_allowed(self) -> bool:
        """교환 엔드포인트 호출 하나를 센다. 분당 상한을 넘으면 False(그 호출은 세지 않는다)."""
        with self._mu:
            now = self.clock()
            self._exchange = [t for t in self._exchange if now - t < 60.0]
            if len(self._exchange) >= self.exchange_per_min:
                return False
            self._exchange.append(now)
            return True

    def exchange_retry_after(self) -> int:
        with self._mu:
            if not self._exchange:
                return 1
            return max(1, math.ceil(60.0 - (self.clock() - self._exchange[0])))

    def reset(self) -> None:
        with self._mu:
            self._failures.clear()
            self._locked_until.clear()
            self._exchange.clear()


LIMITER = Limiter()


def reset() -> None:
    """프로세스 안 카운터를 비운다. 테스트 격리용."""
    LIMITER.reset()


def effective_ip(raw_host: Optional[str], headers: Mapping[str, str]) -> Optional[str]:
    """`VT_TRUST_PROXY=1` 일 때만 프록시 헤더로 실제 클라이언트 IP 를 낸다(`main._effective_client_ip` 와 같은 규칙)."""
    if os.environ.get("VT_TRUST_PROXY", "").strip() != "1":
        return raw_host
    cf = (headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return raw_host


def source_key(raw_host: Optional[str], headers: Mapping[str, str]) -> str:
    """한도를 세는 출처. 모듈 머리말의 「출처 키」."""
    ip = effective_ip(raw_host, headers)
    if os.environ.get("VT_TRUST_PROXY", "").strip() != "1" and (raw_host or "") in _LOOPBACK:
        cf = (headers.get("cf-connecting-ip") or "").strip()
        xff = (headers.get("x-forwarded-for") or "").strip()
        via = cf or (xff.split(",")[-1].strip() if xff else "")
        if via:
            return "via:" + via[:64]
    return (ip or "unknown")[:64]


def source_of(request) -> str:
    """Starlette `Request` 의 출처 키."""
    client = request.client
    return source_key(client.host if client else None, request.headers)


def is_exchange(path: str) -> bool:
    return path in EXCHANGE_PATHS


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/static")


def _deny(source: str, reason: str, action: str) -> None:
    """거부 기록(분당 상한 있음, 실패는 삼킨다)."""
    try:
        from . import denylog
        denylog.record({"principal": f"src:{source}", "action": action, "resource": "authz:*", "decision": "deny",
                        "result": "denied", "reason": reason})
    except Exception:  # noqa: BLE001
        pass


def fail(source: str, kind: str) -> int:
    """실패 한 번을 센다(`kind`: peer_sig · session · token · login · ticket · pair). 이 실패로 잠기면 남은 초를 돌려준다."""
    left = LIMITER.note_failure(source)
    _deny(source, kind, "auth.failure" if not left else "auth.lockout")
    return left


def blocked(source: str, path: str) -> Optional[tuple[int, str]]:
    """이 요청을 429 로 막아야 하면 (Retry-After 초, 사유 코드), 아니면 None. 사유: `source_locked` | `exchange_cap`."""
    if is_public(path) and not is_exchange(path):
        return None
    left = LIMITER.lock_remaining(source)
    if left:
        _deny(source, "source_locked", "preauth.locked")
        return left, "source_locked"
    if is_exchange(path) and not LIMITER.exchange_allowed():
        _deny(source, "exchange_cap", "preauth.exchange_cap")
        return LIMITER.exchange_retry_after(), "exchange_cap"
    return None
