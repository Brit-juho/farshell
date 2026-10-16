"""탭 소유 장부 — `~/.vt/authz/tabs.json`(디렉터리 0700 · 파일 0600, 명시 chmod). 브라우저 broker 가 연 탭마다 한 줄.

레코드: `{id, adapter, ref, owner, ownerName, pair, state, hostGen, created, lastUsed, humanTaken, released, orphanReason}`.
  - `owner`: 소유 주체 id(`agent:local/<sid>:<created>` 등). 없으면(None) 소유권이 풀린 탭이다.
  - `state`: `open`(조작 가능) · `orphaned`(어댑터/호스트가 사라져 조작 불가 — 소유는 그대로, F7) · `released`(소유권만 해제, 탭은 닫히지 않음 — N-15).
    닫으면 레코드가 지워진다.
  - `humanTaken`: 소유자가 `browser_handoff` 로 사람에게 넘긴 탭 — 회수·정리 절차가 건드리지 않는다(N-15 「사람이 이어받은 탭은 구분」).

## 호스트 임대 + 세대 (N-28)
이 장부를 쓰는 서버 프로세스는 시작할 때 **임대**를 잡고 세대 번호를 하나 올린다(`acquire_lease`). 탭 레코드에는 만들 때의 세대(`hostGen`)가 적힌다.
조작은 (1) 이 프로세스가 임대를 가졌고 (2) 파일의 임대 토큰이 내 것이며 (3) 탭의 세대가 현재 세대일 때만 허용한다 — 임대가 없거나 세대가 다르면 거부.
서버를 다시 띄우면 세대가 바뀌므로 옛 브라우저를 가리키던 탭은 `orphaned` 가 된다(이어받지 않는다). 다른 프로세스가 임대를 가져가도(토큰 불일치) 거부.

## 손상
파일을 읽거나 해석하지 못하면 읽기는 빈 장부로 보이지만 **조작은 fail closed**(503 `tabs_unreadable`)이고, 파일은 덮어쓰지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

import store_lock
import vt_paths
from authz import store as authz_store

logger = logging.getLogger(__name__)

OPEN, ORPHANED, RELEASED = "open", "orphaned", "released"
VERSION = 1


class TabsError(authz_store.StoreError):
    pass


def root() -> Path:
    return vt_paths.state_dir() / "authz"


def path() -> Path:
    return root() / "tabs.json"


def _empty() -> dict:
    return {"version": VERSION, "host": {"gen": 0}, "tabs": {}}


def _read() -> tuple[dict, bool]:
    p = path()
    if not p.is_file():
        return _empty(), False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"tabs.json 읽기 실패({e}) — 조작은 거부, 파일은 그대로")
        return _empty(), True
    if not isinstance(d, dict) or not isinstance(d.get("tabs"), dict) or not isinstance(d.get("host"), dict):
        return _empty(), True
    d["tabs"] = {str(k): v for k, v in d["tabs"].items() if isinstance(v, dict)}
    return d, False


def _write(d: dict) -> None:
    p = path()
    authz_store.write_private(p, (json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode())
    try:
        os.chmod(p, 0o600)
        os.chmod(p.parent, 0o700)
    except OSError:
        pass


@contextlib.contextmanager
def txn():
    """`with txn() as d:` — d["tabs"] 를 고치면 나갈 때 저장. 읽을 수 없는 파일은 덮지 않고 TabsError(503)."""
    authz_store._ensure_dir(root())
    with store_lock.exclusive(root() / "tabs.lock"):
        d, bad = _read()
        if bad:
            raise TabsError("tabs_unreadable", "탭 장부를 읽을 수 없어 조작을 거부했습니다 — 파일은 그대로 두었습니다", 503)
        before = json.dumps(d, sort_keys=True)
        yield d
        if json.dumps(d, sort_keys=True) != before:
            _write(d)


def snapshot() -> tuple[dict, bool]:
    """읽기 전용 (장부, 손상 여부)."""
    return _read()


# --- 호스트 임대 · 세대 -------------------------------------------------------------------------------

_lease: Optional[dict] = None          # 이 프로세스가 가진 임대 {gen, token}


def acquire_lease() -> dict:
    """임대를 잡고 세대를 올린다. 옛 세대의 열린 탭은 `orphaned` 로(소유 유지)."""
    global _lease
    with txn() as d:
        gen = int(d["host"].get("gen") or 0) + 1
        token = secrets.token_hex(8)
        d["host"] = {"gen": gen, "token": token, "pid": os.getpid(), "acquired": round(time.time(), 3)}
        for t in d["tabs"].values():
            if t.get("state") == OPEN and int(t.get("hostGen") or 0) != gen:
                t["state"] = ORPHANED
                t["orphanReason"] = "host_restart"
    _lease = {"gen": gen, "token": token}
    return dict(_lease)


def current_lease() -> Optional[dict]:
    return dict(_lease) if _lease else None


def drop_lease() -> None:
    global _lease
    _lease = None


def lease_problem(tab: dict) -> Optional[str]:
    """이 탭을 지금 조작해도 되는가 — 안 되면 이유 코드(`no_lease`·`lease_lost`·`stale_generation`), 되면 None."""
    if _lease is None:
        return "no_lease"
    d, bad = _read()
    if bad:
        return "tabs_unreadable"
    host = d["host"]
    if host.get("token") != _lease["token"] or int(host.get("gen") or 0) != _lease["gen"]:
        return "lease_lost"
    if int(tab.get("hostGen") or 0) != _lease["gen"]:
        return "stale_generation"
    return None


# --- 레코드 -------------------------------------------------------------------------------------------


def new_id() -> str:
    return "bt-" + secrets.token_hex(5)


def _read_strict() -> dict:
    d, bad = _read()
    if bad:
        raise TabsError("tabs_unreadable", "탭 장부를 읽을 수 없어 조작을 거부했습니다 — 파일은 그대로 두었습니다", 503)
    return d


def get(tab_id: str) -> Optional[dict]:
    t = _read_strict()["tabs"].get(tab_id)
    return dict(t) if t else None


def list_all() -> list:
    d = _read_strict()
    return sorted((dict(t) for t in d["tabs"].values()), key=lambda t: (t.get("created") or 0, t.get("id") or ""))


def create(*, adapter: str, ref: str, owner: Optional[str], owner_name: str = "", pair: Optional[str] = None,
           tab_id: Optional[str] = None, now: Optional[float] = None, **extra) -> dict:
    if _lease is None:
        raise TabsError("no_lease", "브라우저 호스트 임대가 없습니다", 503)
    t = time.time() if now is None else now
    rec = {"id": tab_id or new_id(), "adapter": adapter, "ref": ref, "owner": owner, "ownerName": owner_name, "pair": pair,
           "state": OPEN, "hostGen": _lease["gen"], "created": round(t, 3), "lastUsed": round(t, 3), "humanTaken": False, **extra}
    with txn() as d:
        d["tabs"][rec["id"]] = rec
    return dict(rec)


def update(tab_id: str, **fields) -> Optional[dict]:
    with txn() as d:
        t = d["tabs"].get(tab_id)
        if t is None:
            return None
        t.update(fields)
        return dict(t)


def touch(tab_id: str, now: Optional[float] = None) -> None:
    update(tab_id, lastUsed=round(time.time() if now is None else now, 3))


def delete(tab_id: str) -> bool:
    with txn() as d:
        return d["tabs"].pop(tab_id, None) is not None


def release_where(match: Callable[[dict], bool], reason: str, *, now: Optional[float] = None) -> list:
    """맞는 탭의 **소유권만** 푼다(닫지 않는다 — N-15). 사람이 이어받은 탭·이미 풀린 탭은 건드리지 않는다. 풀린 탭 id 들을 돌려준다."""
    t = time.time() if now is None else now
    out = []
    with txn() as d:
        for rec in d["tabs"].values():
            if rec.get("owner") is None or rec.get("humanTaken"):
                continue
            if match(rec):
                rec["owner"] = None
                rec["state"] = RELEASED if rec.get("state") == OPEN else rec.get("state")
                rec["released"] = {"at": round(t, 3), "reason": reason}
                out.append(rec["id"])
    return out


def handoff(tab_id: str, *, now: Optional[float] = None) -> Optional[dict]:
    """소유자가 사람에게 넘긴다 — 소유권이 풀리고 `humanTaken` 으로 구분된다(이후 회수가 닫거나 건드리지 않는다)."""
    t = time.time() if now is None else now
    with txn() as d:
        rec = d["tabs"].get(tab_id)
        if rec is None:
            return None
        rec.update(owner=None, humanTaken=True, state=RELEASED if rec.get("state") == OPEN else rec.get("state"),
                   released={"at": round(t, 3), "reason": "handoff"})
        return dict(rec)


def mark_orphaned(adapter: Optional[str], reason: str, refs: Optional[set] = None) -> list:
    """어댑터가 내려갔다(또는 탭이 사라졌다) — 열린 탭을 `orphaned` 로, 소유는 그대로(F7)."""
    out = []
    with txn() as d:
        for rec in d["tabs"].values():
            if rec.get("state") != OPEN:
                continue
            if adapter is not None and rec.get("adapter") != adapter:
                continue
            if refs is not None and rec.get("ref") in refs:
                continue
            rec["state"] = ORPHANED
            rec["orphanReason"] = reason
            out.append(rec["id"])
    return out


def view(rec: dict) -> dict:
    """API 로 내보내는 모양 — 어댑터 고유 ref 는 숨긴다."""
    return {"id": rec.get("id"), "adapter": rec.get("adapter"), "state": rec.get("state"), "owner": rec.get("owner"),
            "ownerName": rec.get("ownerName") or "", "pair": rec.get("pair"), "created": rec.get("created"),
            "lastUsed": rec.get("lastUsed"), "humanTaken": bool(rec.get("humanTaken")),
            "released": rec.get("released"), "orphanReason": rec.get("orphanReason"),
            "hostGen": rec.get("hostGen")}
