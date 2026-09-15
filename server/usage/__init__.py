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
from .codex import CodexProvider
from .counter_jsonl import CounterJsonlProvider
from .null import NullProvider

_provider = None
_provider_mode: Optional[str] = None

# N41 — 누적형(CounterProvider)은 한도형(clauth)과 별개의 소스다. 파일 유무로만
# 켜지고 끄므로(한도형처럼 여러 모드가 필요 없다) 단일 인스턴스를 한 번만 만든다.
_counter_provider: Optional[CounterJsonlProvider] = None

# 96번 계획서(2.1.5) — Codex도 clauth와 같은 한도형이지만 **독립적인 두 번째
# 소스**다. clauth와 달리 모드 스위칭(auto/clauth/none) 개념이 없다 — clauth
# 프로필 전환 같은 게 codex엔 없어서(계정 하나뿐) 더 켤 게 없다. 파일 유무
# 대신 인증 파일 유무로 켜지고 꺼진다(counter_jsonl과 같은 "파일이 있으면
# 켠다" 원칙).
_codex_provider: Optional[CodexProvider] = None


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


# ── 96번 계획서: Codex(한도형, 독립 소스) ────────────────────────────────────
# `VT_USAGE_PROVIDER=none`은 "사용량을 아예 내보내고 싶지 않다"는 명시적
# 의사표시이므로(모듈 docstring) codex에도 똑같이 적용한다 — clauth·codex·
# counter 셋 다 그 스위치 하나로 꺼진다.

def _get_codex_provider() -> CodexProvider:
    global _codex_provider
    if _codex_provider is None:
        _codex_provider = CodexProvider()
    return _codex_provider


def codex_capability() -> dict:
    if _mode() == "none":
        return {"available": False, "provider": "codex", "profiles": 0, "reason": "disabled"}
    return _get_codex_provider().capability()


def codex_snapshot() -> Optional[dict]:
    if _mode() == "none":
        return None
    return _get_codex_provider().snapshot()


# ── `/api/usage`(스냅샷 렌더링) 전용 — clauth·codex를 한 화면에 합친다 ─────────
# `capability()`(위)는 손대지 않는다 — 설정 →「정보」의 clauth 전용 진단
# 문구(스키마·권한 등)가 그 함수 하나를 그대로 읽고 있어서, 여기서 뜻을
# 바꾸면 그 진단이 codex 상태와 섞여 틀린 말을 하게 된다. "한쪽이라도
# 보여줄 게 있으면 보여준다"는 렌더링 문제라 별도 함수로 둔다.

def merged_capability() -> dict:
    caps = [capability(), codex_capability()]
    available = [c for c in caps if c.get("available")]
    if not available:
        return capability()  # 대표 사유는 기존 동작과 같게 clauth 것을 그대로
    return {"available": True, "profiles": sum(c.get("profiles", 0) for c in available)}


def merged_snapshot() -> Optional[dict]:
    clauth_snap = snapshot() or {}
    codex_snap = codex_snapshot()
    profiles = list(clauth_snap.get("profiles") or [])
    codex_profile = (codex_snap or {}).get("profile")
    if codex_profile:
        profiles.append(codex_profile)
    if not profiles:
        return None
    return {
        "provider": clauth_snap.get("provider") or (codex_snap or {}).get("provider") or "codex",
        "generated_at": clauth_snap.get("generated_at") or (codex_snap or {}).get("generated_at"),
        "stale": bool(clauth_snap.get("stale")) or bool((codex_snap or {}).get("stale")),
        "active_profile": clauth_snap.get("active_profile"),
        "profiles": profiles,
    }


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
    global _provider, _provider_mode, _counter_provider, _codex_provider
    _provider, _provider_mode = None, None
    _counter_provider = None
    _codex_provider = None
