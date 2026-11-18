"""세션 별칭(사람이 보는 이름) 조회 — 서버의 사람용 문구(알림·위임·메시지·음성·TUI)가 tmux 이름(ID) 대신 쓴다.

별칭의 주인은 세션 기록(session_catalog `sessions.title`)이다(docs/local/plans/session-alias-id-design.md).
기록을 못 읽거나 아직 기록 전인 세션은 받은 이름 그대로 돌려준다 — 문구가 비지 않게.
열쇠(답장 주소 `to`, 잠금 파일, 큐 대상 등)에는 쓰지 않는다 — 그 값은 ID여야 한다.
"""
from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def aliases(names: Iterable[str]) -> dict[str, str]:
    """{ID: 별칭}. 기록에 없는 이름은 자기 자신."""
    wanted = [n for n in dict.fromkeys(names) if isinstance(n, str) and n]
    found: dict[str, str] = {}
    if wanted:
        try:
            from session_catalog import db as catalog
            found = catalog.aliases_for_names(wanted)
        except Exception:  # noqa: BLE001 — 기록이 꺼졌거나 잠김: 이름 그대로
            logger.debug("session alias lookup unavailable", exc_info=False)
    return {n: found.get(n) or n for n in wanted}


def alias_of(name: str) -> str:
    """그 세션의 별칭. 없으면 이름 그대로."""
    if not isinstance(name, str) or not name:
        return name
    return aliases([name]).get(name, name)


def clean_peer_alias(text) -> str:
    """다른 맥이 보낸 별칭(믿을 수 없는 글)을 문구에 넣을 수 있게 다듬는다. 인쇄 가능한 글자만, 공백을 하나로 접고 64자까지,
    머리 줄의 `[ … ]` 틀을 흉내 내지 못하게 대괄호를 바꾼다. 쓸 게 없으면 빈 문자열."""
    if not isinstance(text, str):
        return ""
    # 인쇄할 수 없는 글자(줄바꿈·ESC 등)는 공백으로 바꾼 뒤 접는다 — 단어가 붙지 않게.
    value = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())[:64]
    return value.replace("[", "(").replace("]", ")")


def resolve(text: str) -> str | None:
    """사람이 친 값(별칭 또는 ID) → 살아 있는 세션의 ID. 정확한 ID가 먼저. 없거나 모호하면 None."""
    try:
        from session_catalog import db as catalog
        return catalog.resolve_alias(text)
    except Exception:  # noqa: BLE001
        return None
