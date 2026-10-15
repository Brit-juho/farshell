"""공유 세션 오류 — `agent_share.ShareError` 의 본체(둘이 같은 클래스다). authz 쪽 코드가 `agent_share` 를 import 하지
않고도 같은 예외를 던질 수 있게 여기 둔다."""

from __future__ import annotations


class ShareError(Exception):
    def __init__(self, code: str, reason: str, status: int = 400, **extra):
        super().__init__(reason)
        self.code, self.reason, self.status, self.extra = code, reason, status, extra

    def body(self) -> dict:
        return {"error": self.code, "reason": self.reason, **self.extra}
