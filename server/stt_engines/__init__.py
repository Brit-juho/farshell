"""STT 엔진 계층 — 엔진 레지스트리와 환경 감지.

엔진은 지연 import한다: mlx나 llama.cpp가 없는 환경에서도 이 패키지를 불러올 수 있어야 한다.
"""
from __future__ import annotations

import importlib
from typing import Optional

from .base import Engine, NotReady, Requirement  # noqa: F401
from .env import HostEnv, detect  # noqa: F401

# 엔진 id → (모듈, 클래스). 새 엔진은 여기에 한 줄 추가한다.
_ENGINE_CLASSES = {
    "mlx_qwen3": ("stt_engines.mlx_qwen3", "MlxQwen3Engine"),
    "llamacpp_qwen3": ("stt_engines.llamacpp_qwen3", "LlamaCppQwen3Engine"),
    "apple_speech": ("stt_engines.apple_speech", "AppleSpeechEngine"),
}
_instances: dict = {}


def engine_ids() -> list:
    return list(_ENGINE_CLASSES)


def get_engine(engine_id: str) -> Engine:
    """엔진 인스턴스(프로세스당 하나). 모르는 id면 KeyError."""
    if engine_id not in _instances:
        mod_name, cls_name = _ENGINE_CLASSES[engine_id]
        _instances[engine_id] = getattr(importlib.import_module(mod_name), cls_name)()
    return _instances[engine_id]


def reset_instances() -> None:
    """테스트용: 인스턴스 캐시를 비운다(로드된 모델은 먼저 unload 해 둘 것)."""
    _instances.clear()
