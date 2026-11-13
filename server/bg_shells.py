"""Claude Code 세션 하나의 백그라운드 셸 목록(디스크에 두지 않는다).

근거는 Claude Code가 턴 끝(Stop)과 서브에이전트 끝(SubagentStop)에 보내는 `background_tasks` 중 `type == "shell"`
항목이다(공식 hooks 문서 「Stop input」: 지금 돌고 있는 작업만 담기고, 둘 다 부모 세션 범위다). 셸이 끝나는 순간을
알리는 이벤트는 없으므로:

- 시작: Bash 도구를 `run_in_background: true`로 실행한 post 훅에서 바로 「확인 전(pending)」 행을 만든다.
- 확인: 목록이 오면 그 목록이 정본이다. pending 행은 같은 명령줄의 목록 항목으로 넘어가고, 목록에 없으면 이미 끝난 것이다.
- 끝남: 목록에 있다가 다음 목록에서 빠지면 끝남(done)으로 바꾸고 TTL 동안 보관한다.

명령줄에는 토큰·경로가 들어 있을 수 있어 이 맥 화면에만 나간다(agent_status.remote_view가 다른 맥 쪽에서 뺀다).
"""
from __future__ import annotations

import time

import subagents

LIMIT = 32
TTL = 3600
DESC_MAX = 200
CMD_MAX = 300
_FIELDS = ("id", "state", "description", "command", "started_at", "ended_at", "pending")


def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _mark(ent: dict, ts) -> None:
    """시각 경계 = 셸 목록을 바꾼 이벤트(실행·목록·새 대화) 중 가장 늦은 수신 시각. 줄어들지 않는다."""
    if isinstance(ts, int):
        last = ent.get("_shells_ts")
        ent["_shells_ts"] = max(ts, last) if isinstance(last, int) else ts


def clear(ent: dict, ts=None) -> bool:
    """새 대화 — 목록을 비우고 경계를 그 시각으로 옮긴다. 경계보다 옛 새 대화(늦게 온 훅)는 최신 목록을 지우지 않는다.
    시각 없는 새 대화(옛 훅)는 비우고 경계도 지운다 — 남기면 그 뒤의 시각 없는 이벤트를 영영 막는다."""
    if ts is not None and not fresh(ent, ts):
        return False
    ent.pop("_shells", None)
    ent.pop("_shells_checked", None)
    if isinstance(ts, int):
        _mark(ent, ts)
    else:
        ent.pop("_shells_ts", None)
    return True


def fresh(ent: dict, ts) -> bool:
    """이 이벤트가 셸 목록의 마지막 근거(목록·새 대화)보다 옛 것이 아닌가.
    훅(agent_hook.sh)은 늘 수신 시각을 보낸다. 경계가 있는데 시각이 없는 이벤트는 순서를 알 수 없어 버리고,
    경계가 없으면(시각 없는 옛 훅만 쓰는 설치) 받는다."""
    last = ent.get("_shells_ts")
    if not isinstance(last, int):
        return True
    return isinstance(ts, int) and ts >= last


def has_running(ent: dict) -> bool:
    return any(r["state"] == "running" for r in (ent.get("_shells") or {}).values())


def _make_room(rows: dict) -> bool:
    """자리가 없으면 가장 오래 전에 끝난 행부터 비운다. 실행 중인 행은 비우지 않는다."""
    if len(rows) < LIMIT:
        return True
    ended = sorted((r for r in rows.values() if r["state"] != "running"), key=lambda r: r.get("ended_at") or 0)
    for r in ended[: len(rows) - LIMIT + 1]:
        del rows[r["id"]]
    return len(rows) < LIMIT


def started(ent: dict, payload: dict, now=None, ts=None) -> bool:
    """post 훅: Bash를 백그라운드로 실행했으면 확인 전 행을 만든다. 바뀌었으면 True."""
    if payload.get("tool_name") != "Bash" or not fresh(ent, ts):
        return False
    ti = payload.get("tool_input")
    if not isinstance(ti, dict) or ti.get("run_in_background") is not True:
        return False
    now = time.time() if now is None else now
    rows = ent.setdefault("_shells", {})
    tuid = payload.get("tool_use_id")
    key = "pending-" + (tuid if subagents.valid_id(tuid) else f"{now:.6f}".replace(".", "-"))
    if key in rows or not _make_room(rows):
        return False
    rows[key] = {"id": key, "state": "running", "description": _text(ti.get("description"), DESC_MAX),
                 "command": _text(ti.get("command"), CMD_MAX), "started_at": now, "ended_at": None, "pending": True}
    _mark(ent, ts)   # 이 실행보다 옛 목록이 늦게 와서 확인 전 행을 끝남으로 바꾸지 못하게
    return True


def fold(ent: dict, payload: dict, now=None, ts=None) -> bool:
    """Stop·SubagentStop의 `background_tasks`로 목록을 맞춘다. 목록이 없거나 모양이 틀리거나 이미 받은 목록보다 옛 것이면
    아무것도 바꾸지 않는다."""
    if not fresh(ent, ts):
        return False
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list) or len(tasks) > 1000:
        return False
    if payload.get("background_tasks_truncated") or payload.get("background_tasks_complete") is False:
        return False
    now = time.time() if now is None else now
    listed = {}
    for t in tasks:
        if isinstance(t, dict) and t.get("type") == "shell" and subagents.valid_id(t.get("id")):
            listed[t["id"]] = t
    # 시각 경계는 빈 목록이어도 옮긴다 — 늦게 온 옛 목록이 끝난 셸을 되살리지 못하게.
    _mark(ent, ts)
    rows = ent.setdefault("_shells", {})
    if not rows and not listed and ent.get("_shells_checked") is None:
        return False   # 셸을 쓴 적이 없는 세션 — 화면에 아무것도 안 나가게 둔다
    ent["_shells_checked"] = now
    # 먼저 빠진 셸을 끝남으로 — 그래야 자리가 꽉 찼을 때도 같은 목록의 새 셸이 들어갈 자리를 비울 수 있다.
    for i, row in rows.items():
        if row["state"] == "running" and not row.get("pending") and i not in listed:
            row.update(state="done", ended_at=now)
    carried: dict[str, float] = {}
    for key, row in list(rows.items()):
        if not row.get("pending"):
            continue
        del rows[key]
        match = next((i for i, t in listed.items()
                      if i not in rows and i not in carried and _text(t.get("command"), CMD_MAX) == row["command"]), None)
        if match:
            carried[match] = row["started_at"]
        elif _make_room(rows):
            # 다음 목록이 오기 전에 이미 끝났다(짧은 백그라운드 명령).
            rows[key] = {**row, "state": "done", "ended_at": now, "pending": False}
    for i, t in listed.items():
        row = rows.get(i)
        desc, cmd = _text(t.get("description"), DESC_MAX), _text(t.get("command"), CMD_MAX)
        if row is None:
            if not _make_room(rows):
                continue
            rows[i] = {"id": i, "state": "running", "description": desc, "command": cmd,
                       "started_at": carried.get(i, now), "ended_at": None, "pending": False}
        else:
            row.update(state="running", ended_at=None, description=desc or row["description"], command=cmd or row["command"])
    return True


def sweep(ent: dict, now=None) -> bool:
    now = time.time() if now is None else now
    rows = ent.get("_shells") or {}
    gone = [i for i, r in rows.items() if r["state"] != "running" and r.get("ended_at") and now - r["ended_at"] > TTL]
    for i in gone:
        del rows[i]
    return bool(gone)


def public(ent: dict) -> dict | None:
    rows = ent.get("_shells") or {}
    if not rows and ent.get("_shells_checked") is None:
        return None
    items = sorted(({k: r.get(k) for k in _FIELDS} for r in rows.values()), key=lambda r: r.get("started_at") or 0)
    return {"items": items, "checked_at": ent.get("_shells_checked")}


def without_commands(shells: dict) -> dict:
    """다른 맥으로 나갈 모양 — 명령줄을 뺀다(설명·상태·시각만)."""
    return {**shells, "items": [{k: v for k, v in r.items() if k != "command"} for r in shells.get("items") or []]}
