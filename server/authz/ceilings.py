"""상한(ceiling) — 판정식의 `s ∈ ceiling(r)` (10-policy.md A4).

세션 리소스의 상한은 그 세션의 **공유 정책**이다: `read`→{list, read}, `readwrite`→+input.line,
새 `full`→+input.raw, 미공유→비소유자에게 ∅. 기존 `agent_share.effective = min(grant, mode)`를
대체한다. 호스트·pair 상한은 3d·5단계.

공유 정책의 진실은 `~/.vt/authz/shares.json`, tmux `@fsh_share_*` 옵션은 표시용 미러다(B1).
1단계에서는 아직 옛 코드가 tmux 옵션만 쓰므로, 판정 지점이 넘겨준 **실시간 tmux 값**과 저장소 값이
다르면: 실시간 모드(shadow 기본)는 tmux 값을, 저장소 모드(--verify)는 **낮은 쪽**을 쓴다(B1 해소 규칙).
"""

from __future__ import annotations

from typing import Optional

from . import defaults
from . import principals as P

LEVELS = ("read", "readwrite", "full")
_RANK = {None: 0, "read": 1, "readwrite": 2, "full": 3}

SHARE_SCOPES: dict[Optional[str], frozenset[str]] = {
    None: frozenset(),
    "read": frozenset({"session.list", "session.read"}),
    "readwrite": frozenset({"session.list", "session.read", "session.input.line"}),
    "full": frozenset({"session.list", "session.read", "session.input.line", "session.input.raw"}),
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
    prefer_live 면 실시간 값, 아니면 저장소 값이 있을 때 둘 중 낮은 쪽(B1 해소 규칙)."""
    lv = level_of(live)
    if prefer_live or stored is None:
        return lv
    return lower(lv, level_of(stored))


def applies(p: P.Principal, scope: str, resource: str) -> bool:
    """이 판정에 세션 공유 상한이 걸리는가 — 비소유 주체가 남의 세션에 `session.*`."""
    if not (resource.startswith("session:") and scope.startswith("session.")):
        return False
    if p.kind in (P.HUMAN, P.LEGACY, P.ANON):
        return False                     # 로컬 사람·legacy shim 은 소유자와 같다(A3, B1)
    if p.kind == P.PEER and defaults.PEER_SESSION_CEILING_EXEMPT:
        return False
    if p.kind == P.AGENT and resource == f"session:local/{p.attrs.get('key', '')}":
        return False                     # 자기 세션
    return True


def allows(scope: str, level: Optional[str]) -> bool:
    return scope in SHARE_SCOPES.get(level, frozenset())
