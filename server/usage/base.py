"""U1 — 사용량 provider 인터페이스 (ADR-6).

**왜 추상화인가**: clauth를 쓰는 환경과 안 쓰는 환경이 공존하고, 사내 토큰
프록시로 갈아탈 가능성도 열려 있다(docs/TODOS.md 보류 절). 데이터 소스를
바꿔 끼울 수 있게 해두면 두 판단이 충돌하지 않는다 — 없으면 UI가 조용히
사라지고, 있으면 같은 컴포넌트가 그대로 그린다.

정규화 스키마(UI가 아는 유일한 형태):

    {
      "provider": "clauth",
      "generated_at": "...",
      "stale": false,
      "active_profile": "brit",
      "profiles": [
        {"name", "active", "tier", "auth_ok", "fetch_status", "has_live_session",
         "rolling_token", "provider",
         "windows": [{"label", "pct", "resets_at", "resets_in_sec"}],
         "fallback": {"position", "threshold", "armed"}}
      ]
    }

`tier`와 `windows[].label`은 **표시 전용**이다 — 분기 키로 쓰지 않는다.
clauth가 새 tier/label을 추가해도 안 깨지게 하기 위한 규칙이고, 이 규칙은
서버(여기)와 UI 양쪽에 똑같이 적용된다.

---

N41(60-settings-palette.md §5) — **한도형(LimitProvider) / 누적형(CounterProvider)**.
두 인터페이스는 "얼마나 남았나"(한도, %) 와 "얼마나 썼나"(누적, 한도 없음)라는
서로 다른 질문에 답한다 — 한 인터페이스로 억지로 합치면 clauth처럼 %가 있는
소스와 로컬 LLM처럼 %가 없는 소스 중 하나가 항상 거짓 필드를 채워야 한다.

기획서 원안은 `LimitProvider.read() -> list[LimitGauge]` /
`CounterProvider.read(since) -> list[Counter]` 형태다. 이 파일의 `UsageProvider`
(위, `capability()`/`snapshot()`)가 이미 LimitProvider 역할을 하고 있고 clauth
어댑터·`/api/usage`·프런트가 전부 이 두 메서드 이름에 맞물려 있다 — 지금 와서
`read()`로 이름을 바꾸면 "기존 연동을 깨지 말라"는 지시와 정면으로 부딪힌다.
그래서 **`UsageProvider` = LimitProvider**로 두고, `CounterProvider`는 같은
`capability()`/`snapshot(since)` 호출 관례를 따르는 별도 Protocol로 신설한다 —
패키지 안에서 프로바이더 종류마다 호출 규약이 갈리지 않게 하기 위해서다.
"""

from __future__ import annotations

from typing import Optional, Protocol


class UsageProvider(Protocol):
    """한도형(LimitProvider) — clauth 등, 남은 비율(%)이 있는 소스."""

    name: str

    def capability(self) -> dict:
        """{"available": bool, "provider": str, "profiles": int, "reason"?: str}"""
        ...

    def snapshot(self) -> Optional[dict]:
        """정규화된 사용량. 읽을 수 없으면 None."""
        ...


# 기획서 어휘와의 매핑용 별칭 — 타입 자체는 UsageProvider와 동일.
LimitProvider = UsageProvider


class CounterProvider(Protocol):
    """누적형(CounterProvider) — 로컬 LLM 등, 한도 없이 누적만 있는 소스.

    정규화 스키마:

        {
          "provider": "counter_jsonl",
          "generated_at": "...",
          "counters": [
            {"label": "qwen2.5-coder", "tokens": 184000, "seconds": 4100,
             "tok_per_sec": 44.9, "running": false,
             "samples": [{"day": "2026-09-04", "tokens": 12000}, ...]}
          ]
        }

    `samples`는 항상 **최근 7일** 일별 버킷이다(`since`와 무관 — 스파크라인은
    고정 창이라는 화면 2e 규칙). `tokens`/`seconds`/`tok_per_sec`는 `since` 이후
    누적이고, `since=0`(기본)이면 파일에 남은 전체 기간의 누적이다.
    """

    name: str

    def capability(self) -> dict:
        """{"available": bool, "provider": str, "models": int, "reason"?: str}"""
        ...

    def snapshot(self, since: float = 0) -> Optional[dict]:
        """정규화된 누적 사용량. 읽을 수 없으면 None."""
        ...
