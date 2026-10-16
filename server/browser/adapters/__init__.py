"""어댑터 registry — 이름(R9.1) → 어댑터. 실연동 둘(`cdp`·`playwright`)은 선택 의존성이라 **지연 import** 한다(없으면 사용 불가로 보일 뿐 서버가 깨지지 않는다).

`profile = "user"` 를 선언하는 어댑터는 이 빌드에서 쓸 수 없다: `describe()` 가 `available=False`(`profile_user_unsupported`)로 보인다(후속 과제, `docs/ref/agents.md`).
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Callable, Optional

from .base import OPS, PROFILES, AdapterDown, AdapterError, BrowserAdapter, Unavailable

logger = logging.getLogger(__name__)

# 정식 이름 -> 모듈. 순서는 기본 어댑터 선택 순서다(켜진 것 중 앞에서부터).
MODULES: dict[str, str] = {
    "playwright": "playwright",
    "cdp": "cdp",
    "chrome-devtools-mcp": "chrome_devtools_mcp",
    "gstack": "gstack",
    "browserskill": "browserskill",
    "agent-browser": "agent_browser",
}
NAMES: tuple[str, ...] = tuple(MODULES)
IMPLEMENTED: tuple[str, ...] = ("playwright", "cdp")

_instances: dict[str, BrowserAdapter] = {}
_factories: dict[str, Callable[[], BrowserAdapter]] = {}      # 시험·확장이 바꿔 끼운다
_lock = threading.Lock()


def known(name: str) -> bool:
    return name in MODULES or name in _factories


def register(name: str, factory: Callable[[], BrowserAdapter]) -> None:
    """어댑터를 바꿔 끼운다(시험의 fake 어댑터). 기존 인스턴스는 버린다."""
    with _lock:
        _factories[name] = factory
        _instances.pop(name, None)


def unregister(name: str) -> None:
    with _lock:
        _factories.pop(name, None)
        inst = _instances.pop(name, None)
    if inst is not None:
        try:
            inst.shutdown()
        except Exception:  # noqa: BLE001
            pass


def names() -> list:
    return list(dict.fromkeys([*NAMES, *_factories]))


def get(name: str) -> BrowserAdapter:
    with _lock:
        inst = _instances.get(name)
        if inst is not None:
            return inst
        if name in _factories:
            inst = _factories[name]()
        elif name in MODULES:
            mod = importlib.import_module(f"{__name__}.{MODULES[name]}")
            inst = mod.Adapter()
        else:
            raise KeyError(name)
        _instances[name] = inst
        return inst


def describe(name: str) -> dict:
    """GET /api/browser/adapters 의 한 줄 재료 — 어댑터가 예외를 내도 사용 불가로 접어서 돌려준다."""
    try:
        a = get(name)
    except KeyError:
        return {"name": name, "available": False, "reason": "unknown_adapter", "profile": "isolated", "ops": [], "intercepts": False}
    try:
        cap = dict(a.capabilities())
    except Exception as e:  # noqa: BLE001
        cap = {"available": False, "reason": f"capabilities_error:{type(e).__name__}", "ops": [], "intercepts": False}
    cap.setdefault("ops", [])
    cap.setdefault("intercepts", bool(a.intercepts))
    if a.profile not in PROFILES or a.profile != "isolated":
        cap.update(available=False, reason="profile_user_unsupported", ops=[])
    cap.setdefault("available", False)
    cap.setdefault("reason", None if cap["available"] else "unavailable")
    return {"name": name, "profile": a.profile, **cap}


def shutdown_all() -> None:
    with _lock:
        insts = list(_instances.values())
        _instances.clear()
    for a in insts:
        try:
            a.shutdown()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"어댑터 종료 실패: {e}")
