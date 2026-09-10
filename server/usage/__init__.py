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
from .counter_jsonl import CounterJsonlProvider
from .null import NullProvider

_provider = None
_provider_mode: Optional[str] = None

# N41 — 누적형(CounterProvider)은 한도형(clauth)과 별개의 소스다. 파일 유무로만
# 켜지고 끄므로(한도형처럼 여러 모드가 필요 없다) 단일 인스턴스를 한 번만 만든다.
_counter_provider: Optional[CounterJsonlProvider] = None


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


# ── N41: 누적형(CounterProvider) ────────────────────────────────────────────
# `VT_USAGE_PROVIDER=none`은 "사용량을 아예 내보내고 싶지 않다"는 명시적
# 의사표시이므로(위 모듈 docstring) 누적형에도 똑같이 적용한다. auto/clauth는
# 한도형 쪽 강제 여부일 뿐 누적형과는 무관하므로, 그 두 모드에서는 파일
# 존재 여부로만(2.0 게이팅 규칙) 켜고 끈다.


def _get_counter_provider() -> CounterJsonlProvider:
    global _counter_provider
    if _counter_provider is None:
        _counter_provider = CounterJsonlProvider()
    return _counter_provider


def counter_capability() -> dict:
    if _mode() == "none":
        return {"available": False, "provider": "counter_jsonl", "models": 0, "reason": "disabled"}
    return _get_counter_provider().capability()


def counter_snapshot(since: float = 0) -> Optional[dict]:
    if _mode() == "none":
        return None
    return _get_counter_provider().snapshot(since)


def counter_add(model, tokens, seconds) -> dict:
    if _mode() == "none":
        return {"ok": False, "error": "disabled", "reason": "사용량 표시가 꺼져 있습니다 (VT_USAGE_PROVIDER=none)"}
    return _get_counter_provider().add(model, tokens, seconds)


def counter_running_models() -> list[str]:
    """ollama가 떠 있으면 실행 중 모델 이름. 없으면 빈 리스트(조용히)."""
    if _mode() == "none":
        return []
    from . import ollama
    return ollama.running_models()


def _reset_for_tests() -> None:
    global _provider, _provider_mode, _counter_provider
    _provider, _provider_mode = None, None
    _counter_provider = None
