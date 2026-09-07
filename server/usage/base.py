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
      "active_profile": "alpha",
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
"""

from __future__ import annotations

from typing import Optional, Protocol


class UsageProvider(Protocol):
    name: str

    def capability(self) -> dict:
        """{"available": bool, "provider": str, "profiles": int, "reason"?: str}"""
        ...

    def snapshot(self) -> Optional[dict]:
        """정규화된 사용량. 읽을 수 없으면 None."""
        ...
