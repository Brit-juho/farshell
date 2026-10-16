"""`gstack` 어댑터 자리 표시 — 인터페이스만 만족하고 사용할 수 없다(`available=False`). 요청이 있을 때 하나씩 구현한다."""

from __future__ import annotations

from .base import StubAdapter


class Adapter(StubAdapter):
    name = "gstack"
    description = "local CLI adapter (not implemented yet)"
    stub_reason = "not_implemented"
