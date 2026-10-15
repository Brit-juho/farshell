"""세션 공유 정책 — 진실은 `~/.vt/authz/shares.json`, tmux `@fsh_share_nick`/`@fsh_share_mode` 는 표시용 미러.

`shares.json` 의 항목: `{<세션 key>: {nick, level, origin: "legacy:tmux:<key>"}}`. 엔진이 둘을 같이 쓴다(`share_svc.put_share`).
`origin` 은 1단계 이전 도구(`fsh migrate-auth`)가 같은 항목을 같은 모양으로 만들기 때문에 그대로 쓴다 — 그래야
`--verify` 가 dual-write 상태를 "차이 없음"으로 본다(8단계 `--finalize` 에서 정리).

## 불일치 해소: 낮은 쪽
사람이 tmux 옵션을 손으로 고쳤거나 한쪽 쓰기가 실패해 둘이 다르면 **낮은 등급**을 쓴다.
  - 저장소·tmux 둘 다 있음 → 등급은 둘 중 낮은 쪽, 닉네임은 저장소 값(정책의 진실).
  - 저장소만 있음(tmux 옵션이 지워짐) → 공유 아님.
  - tmux 만 있음 → 이미 snapshot 이 끝났으면(`meta.share_adopted`) 공유 아님. 끝나기 전이면 그 tmux 값을 채택해
    저장소에 적는다(= 이전 시 snapshot. 플래그를 처음 켠 순간 이미 공유 중이던 세션을 잃지 않게).
`full` 은 정의만 있고(ceilings.LEVELS) 쓰지 않는다 — tmux 쪽 모드가 `read`/`readwrite` 뿐이라 낮은 쪽 규칙으로 항상 그 안에 든다.
"""

from __future__ import annotations

from typing import Optional

from . import ceilings, store

ORIGIN = "legacy:tmux:"
ADOPTED_KEY = "share_adopted"


def entry(key: str, nick: str, level: str) -> dict:
    return {"nick": nick, "level": level, "origin": f"{ORIGIN}{key}"}


def valid(e) -> bool:
    return isinstance(e, dict) and isinstance(e.get("nick"), str) and e.get("level") in ceilings.LEVELS


def resolve(tmux_share: Optional[dict], stored, *, adopted: bool) -> Optional[dict]:
    """한 세션의 유효 공유 `{nick, mode}`(없으면 None). tmux_share 는 `{nick, mode}`(tmux 에서 읽은 값)."""
    if not valid(stored):
        return None if adopted else tmux_share
    if tmux_share is None:
        return None
    lv = ceilings.lower(ceilings.level_of({"level": stored["level"]}), ceilings.level_of(tmux_share))
    if lv not in ("read", "readwrite"):       # tmux 쪽이 read/readwrite 뿐이라 `full` 은 나올 수 없다 — 방어
        return None
    return {"nick": stored["nick"], "mode": lv}


def drift(tmux_share: Optional[dict], stored, *, adopted: bool = True) -> Optional[str]:
    """둘이 다르면 설명(진단용), 같으면 None."""
    s = resolve(tmux_share, stored, adopted=adopted)
    want = None if not valid(stored) else {"nick": stored["nick"], "mode": stored["level"]}
    if s == want == tmux_share or (tmux_share is None and not valid(stored)):
        return None
    return f"tmux={tmux_share} store={stored if valid(stored) else None} → 유효={s}"


def load() -> tuple[dict, bool, bool]:
    """(항목 dict, snapshot 완료 여부, 손상 여부) — 캐시 없이 읽는다(철회·강등이 즉시 보여야 한다)."""
    sh, bad_s = store._read("shares")
    meta, bad_m = store._read("meta")
    return dict(sh["items"]), bool(meta["items"].get(ADOPTED_KEY)), bad_s or bad_m
