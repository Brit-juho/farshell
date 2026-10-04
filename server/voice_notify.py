"""작업 완료 알림 정책 — 소리를 낼지, 무엇을 낼지 결정하는 순수 함수.

Stop 훅(tts_hook.sh)이 불러 쓴다. 파일·네트워크를 만지지 않는 순수 함수라 훅(시스템
python)과 서버 테스트가 같은 코드를 쓴다. 설정은 `voice.notify*` 키(설정 「음성 및 알림」).

기본은 `off` — 소리·낭독은 켠 사람에게만 난다.
"""
from __future__ import annotations

from typing import Optional

MODES = ("off", "sound", "speak")
MAX_MIN_SEC = 600


def normalize(settings: Optional[dict]) -> dict:
    """저장된 설정(검증 안 된 값 포함)을 안전한 값으로 정규화한다."""
    s = settings or {}
    mode = s.get("voice.notifyMode")
    if mode not in MODES:
        mode = "off"
    try:
        min_sec = int(s.get("voice.notifyMinSec", 0))
    except (TypeError, ValueError):
        min_sec = 0
    min_sec = max(0, min(MAX_MIN_SEC, min_sec))
    success_only = s.get("voice.notifySuccessOnly", True)
    only_unfocused = s.get("voice.notifyOnlyUnfocused", False)
    return {
        "mode": mode,
        "min_sec": min_sec,
        "success_only": success_only is not False,   # 기본 true — 명시적 false만 끈다
        "only_unfocused": only_unfocused is True,
    }


def decide(cfg: dict, elapsed_sec: Optional[float], ok: bool, web_clients: int) -> str:
    """"none" | "sound" | "speak".

    elapsed_sec=None(작업 시간을 알 수 없음)이면 시간 조건은 적용하지 않는다 —
    알 수 없다는 이유로 알림을 삼키면 설정을 켠 사람이 이유를 모른 채 못 듣는다.
    """
    mode = cfg.get("mode", "off")
    if mode not in ("sound", "speak"):
        return "none"
    if not ok and cfg.get("success_only", True):
        return "none"
    min_sec = cfg.get("min_sec", 0)
    if min_sec and elapsed_sec is not None and elapsed_sec < min_sec:
        return "none"
    if cfg.get("only_unfocused") and web_clients > 0:
        return "none"
    return mode
