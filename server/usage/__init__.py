"""U1 — 사용량 provider 팩토리.

`VT_USAGE_PROVIDER`(~/.vt.env):
    auto (기본)  피드 파일이 있으면 clauth, 없으면 null
    clauth       강제. 피드가 없으면 available:false + 이유 표시
    none         기능 자체를 끈다(사용량을 남에게 보이기 싫은 경우)

`auto`가 "있으면 켜고 없으면 조용히 사라진다"라서, 대부분의 사용자는 이 값을
건드릴 일이 없다. `clauth`는 "왜 안 뜨는지 알고 싶다"(강제 진단), `none`은
"공개 터널로 사용량을 내보내기 싫다"는 명시적 의사표시다.
"""

from __future__ import annotations

import os
from typing import Optional

from .clauth import ClauthProvider, feed_path
from .null import NullProvider

_provider = None
_provider_mode: Optional[str] = None


def _mode() -> str:
    raw = (os.environ.get("VT_USAGE_PROVIDER") or "auto").strip().lower()
    return raw if raw in ("auto", "clauth", "none") else "auto"


def get_provider():
    """현재 설정에 맞는 provider. 모드가 바뀌면 새로 만든다(서버 재시작 불필요)."""
    global _provider, _provider_mode
    mode = _mode()
    if _provider is not None and _provider_mode == mode:
        return _provider

    if mode == "none":
        provider = NullProvider(reason="disabled")
    elif mode == "clauth":
        provider = ClauthProvider()
    else:  # auto
        provider = ClauthProvider() if feed_path().exists() else NullProvider(reason="no-feed")

    _provider, _provider_mode = provider, mode
    return provider


def capability() -> dict:
    return get_provider().capability()


def snapshot() -> Optional[dict]:
    return get_provider().snapshot()


def _reset_for_tests() -> None:
    global _provider, _provider_mode
    _provider, _provider_mode = None, None
