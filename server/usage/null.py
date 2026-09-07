"""U1 — 사용량 소스가 없을 때. UI는 capability만 보고 조용히 사라진다."""

from __future__ import annotations

from typing import Optional


class NullProvider:
    name = "none"

    def __init__(self, reason: str = "none"):
        # reason은 사용자에게 "왜 안 보이는지" 설명하기 위한 것이다. 미설치와
        # "일부러 껐음"은 다르게 안내해야 한다.
        self._reason = reason

    def capability(self) -> dict:
        return {"available": False, "provider": "none", "profiles": 0, "reason": self._reason}

    def snapshot(self) -> Optional[dict]:
        return None
