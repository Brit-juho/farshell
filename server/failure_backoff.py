"""실패 백오프 (P3-4, 60-observability.md §2) — 반복 프로빙 실패를 잠시 쉰다.

실패한 키를 일정 시간(기본 30초) 재시도 대상에서 뺀다. 실패는 비싸고, 곧 다시 실패할 가능성이
높다 — 파일을 열 때마다 `exec.LookPath`와 spawn을 반복하지 않기 위한 장치다.

## 이 모듈을 쓰기 전에 감사부터 했다

`60-observability.md` §2가 "우리가 반복 프로빙하는 곳" 셋을 짚었는데,
2026-09-23 감사 결과 **셋 다 지금 당장 이 모듈이 필요하지 않았다**:

- `peer_client.ping_sync` — 반복 프로빙이 아니다. `POST /api/hosts/{id}/ping`
  라우트(`fsh host ping <id>`)로만 불리고, 이건 사용자가 명시적으로 한 번
  누르는 동작이다. 프런트의 15초 주기 폴링(`Rail.tsx`/`Fleet.tsx`의
  `refreshHosts`)은 `GET /api/hosts`(호스트 저장소 조회, 네트워크 프로브
  아님)만 부른다.
- `tunnel_watchdog.py` — 이미 동등한 장치가 있다. `COOLDOWN_SEC`(기본
  120초)가 `_restart_tunnel()`(비싼 행동 — `fsh tunnel restart` 서브프로세스)
  을 재시도 사이 최소 간격으로 이미 제한한다. 이름만 다를 뿐 같은 규율이다.
- `portscan.scan()` — `_CACHE_TTL`(3초) TTL 캐시가 이미 lsof/ps 호출 빈도를
  누르고 있다. 프로세스 spawn처럼 비싸고 "곧 다시 실패할" 성질이 뚜렷한 실패와
  달리, lsof/ps 실패는 대개 바이너리
  부재(영구 상태)라 "잠시 쉬었다 재시도"가 딱히 도움이 안 된다.

**그래서 이 클래스는 아직 어디에도 연결돼 있지 않다.** "백오프가 없는 곳만
추가한다"는 설계 지시를 따른 결과다 — 없어도 되는 곳에 넣으면 다음 사람이
"방금 고친 게 왜 반영이 안 되지"를 겪는다. 다음에 진짜로 반복 프로빙하는
자리(예: 외부 서버를 실제로 붙이는 경로)가 생기면 여기를 쓴다.
"""

from __future__ import annotations

import threading
import time


class FailureBackoff:
    """실패한 키를 일정 시간 재시도 대상에서 뺀다. 성공하면 즉시 해제한다.

    **해제 경로(`clear`)가 없으면 백오프가 "죽은 것으로 낙인"이 된다.** 오프라인이던 대상이 돌아와도
    성공한 그 순간 바로 풀어줘야 한다. 사용자가 "다시 시도"를 눌렀을 때도
    `clear`를 불러 강제로 재시도를 허용할 수 있다.
    """

    def __init__(self, delay: float = 30.0):
        self._delay = delay
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_skip(self, key: str) -> bool:
        """지금 이 키를 건너뛰어야 하는가(최근 실패해서 백오프 중인가)."""
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if time.monotonic() >= until:
                # 기한이 지났다 — 더 이상 스킵 대상이 아니다. 여기서 지운다,
                # 다음 mark_failed가 새 기한으로 다시 쓸 것이므로.
                del self._until[key]
                return False
            return True

    def mark_failed(self, key: str) -> None:
        """방금 실패했다 — `delay`초 동안 `should_skip`이 참을 돌려준다."""
        with self._lock:
            self._until[key] = time.monotonic() + self._delay

    def clear(self, key: str) -> None:
        """백오프를 즉시 해제한다(성공했거나, 사용자가 강제로 재시도)."""
        with self._lock:
            self._until.pop(key, None)
