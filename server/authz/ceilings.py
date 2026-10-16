"""상한(ceiling) — 판정식의 `s ∈ ceiling(r)`.

세션 리소스의 상한은 그 세션의 **공유 정책**이다: `read`→{보기, 읽기}, `readwrite`→+한 줄 입력(직접),
`full`→+raw 입력, 미공유→비소유자에게 ∅. 기존 `agent_share.effective = min(grant, mode)`를
대체한다. 호스트·pair 상한은 나중 단계.

공유 정책의 진실은 `~/.vt/authz/shares.json`, tmux `@fsh_share_*` 옵션은 표시용 미러다.
아직 옛 코드가 tmux 옵션만 쓰는 구간에서는, 판정 지점이 넘겨준 **실시간 tmux 값**과 저장소 값이
다르면: 실시간 모드(shadow 기본)는 tmux 값을, 저장소 모드(--verify)는 **낮은 쪽**을 쓴다.
"""

from __future__ import annotations

from typing import Optional

from . import defaults
from . import principals as P

LEVELS = ("read", "readwrite", "full")
_RANK = {None: 0, "read": 1, "readwrite": 2, "full": 3}

SHARE_SCOPES: dict[Optional[str], frozenset[str]] = {
    None: frozenset(),
    "read": frozenset({"session.view", "session.read"}),
    "readwrite": frozenset({"session.view", "session.read", "session.input"}),
    "full": frozenset({"session.view", "session.read", "session.input"}),
}
# session.input 은 공유 등급이 정하는 modes 안에서만, 큐 경유 없이 직접 입력만 연다.
SHARE_INPUT_MODES: dict[Optional[str], frozenset[str]] = {
    None: frozenset(), "read": frozenset(), "readwrite": frozenset({"line"}), "full": frozenset({"line", "raw"}),
}


def level_of(share: Optional[dict]) -> Optional[str]:
    if not isinstance(share, dict):
        return None
    lv = share.get("level", share.get("mode"))
    return lv if lv in LEVELS else None


def lower(a: Optional[str], b: Optional[str]) -> Optional[str]:
    return a if _RANK[a] <= _RANK[b] else b


def resolve_share(live: Optional[dict], stored: Optional[dict], *, prefer_live: bool) -> Optional[str]:
    """판정에 쓸 공유 등급. live 는 판정 지점이 읽은 실시간 표시(None = 미공유).
    prefer_live 면 실시간 값, 아니면 저장소 값이 있을 때 둘 중 낮은 쪽(불일치는 낮은 쪽으로 해소한다)."""
    lv = level_of(live)
    if prefer_live or stored is None:
        return lv
    return lower(lv, level_of(stored))


def applies(p: P.Principal, scope: str, resource: str) -> bool:
    """이 판정에 세션 공유 상한이 걸리는가 — 비소유 주체가 남의 세션에 `session.*`."""
    if not (resource.startswith("session:") and scope.startswith("session.")):
        return False
    if p.kind in (P.HUMAN, P.LEGACY):
        return False                     # 로컬 사람·legacy shim 은 소유자와 같다
    if p.kind == P.PEER and defaults.PEER_SESSION_CEILING_EXEMPT and not p.obo:
        return False                     # 호스트 단위 peer grant 만 면제다 — 다른 Mac 의 **에이전트**(대리 주체)는 공유 정책을 넘지 못한다
    if p.kind == P.AGENT and resource == f"session:local/{p.attrs.get('key', '')}":
        return False                     # 자기 세션
    return True


def allows(scope: str, level: Optional[str], ctx: Optional[dict] = None) -> bool:
    if scope not in SHARE_SCOPES.get(level, frozenset()):
        return False
    if scope == "session.input":
        c = ctx or {}
        return c.get("mode") in SHARE_INPUT_MODES.get(level, frozenset()) and (c.get("via") or "direct") == "direct"
    return True
