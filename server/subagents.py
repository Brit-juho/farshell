"""Bounded native-child roster owned by one agent_status entry; no disk state."""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LIFECYCLE = frozenset({"subagent_start", "subagent_stop", "stop_failure", "teammate_idle"})
LIMIT = 32
TTL = 3600
STALE = 900
_FIELDS = ("id", "cli", "type", "label", "state", "started_at", "ended_at",
           "tool", "tool_count", "last_message", "stale")


def valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def text(value, limit=200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def teammate(agent_id: str) -> bool:
    return re.fullmatch(r"a.+-[0-9a-fA-F]+", agent_id) is not None


def clear(ent: dict) -> None:
    ent.pop("_subagents", None)
    ent.pop("_subagents_limited", None)
    ent.pop("_fold_warned", None)


def sweep(ent: dict, now=None) -> bool:
    now = time.time() if now is None else now
    rows = ent.get("_subagents", {})
    changed = False
    for aid, row in list(rows.items()):
        if row["state"] == "working" and now - row["_updated"] > STALE:
            row.update(state="idle", idle_since=now, stale=True)
            changed = True
        at = row.get("ended_at") if row["state"] in ("done", "failed") else row.get("idle_since")
        if row["state"] != "working" and at is not None and now - at > TTL:
            del rows[aid]
            changed = True
    if len(rows) < LIMIT and ent.pop("_subagents_limited", False):
        changed = True
    return changed


def public(ent: dict) -> dict:
    rows = ent.get("_subagents", {})
    return {"parent_id": ent.get("session_id", ""), "items": [{k: r.get(k) for k in _FIELDS} for r in rows.values()],
            "limited": bool(ent.get("_subagents_limited"))}


def _finish(row, state, now):
    row.update(state=state, tool=None, stale=False, _updated=now)
    row["ended_at"] = now if state in ("done", "failed") else None
    row["idle_since"] = now if state == "idle" else None


def accept(ent: dict, event: str, payload: dict, cli: str, ts=None) -> bool:
    """Reject reordered events independently per child. Returns whether accepted."""
    aid = payload.get("agent_id")
    if not valid_id(aid):
        return False
    row = ent.get("_subagents", {}).get(aid)
    barrier = ent.get("_subagents_since")
    if isinstance(ts, int):
        if isinstance(barrier, int) and ts < barrier:
            return False
        if row and isinstance(row.get("_ts"), int) and ts <= row["_ts"]:
            return False
    if row is None and event not in ("subagent_start", "pre", "post", "permission", "permission_request"):
        return False
    now = time.time()
    rows = ent.setdefault("_subagents", {})
    if row is None:
        if len(rows) >= LIMIT:
            choices = [r for r in rows.values() if r["state"] in ("done", "failed")]
            if not choices:
                choices = [r for r in rows.values() if r["state"] == "idle"]
            if not choices:
                ent["_subagents_limited"] = True
                return False
            victim = min(choices, key=lambda r: r.get("ended_at") or r.get("idle_since") or r["_updated"])
            del rows[victim["id"]]
        kind = text(payload.get("agent_type"))
        row = {"id": aid, "cli": cli, "type": kind, "label": kind or aid,
               "state": "working", "started_at": now, "ended_at": None,
               "tool": None, "tool_count": 0, "last_message": "", "stale": False}
        rows[aid] = row
    if isinstance(ts, int):
        row["_ts"] = ts
    if event in ("subagent_start", "pre", "post", "permission", "permission_request") and row["state"] != "working":
        row.pop("_last_message", None)
        row["last_message"] = ""
    row["_updated"] = now
    parent_path = payload.get("transcript_path")
    if cli == "claude" and isinstance(parent_path, str):
        row["_parent_transcript"] = parent_path
    path = payload.get("agent_transcript_path")
    if cli == "codex" and event == "subagent_start":
        path = path or parent_path
    if isinstance(path, str):
        row["_transcript"] = path
    message = text(payload.get("last_assistant_message"), 2000)
    if message:
        row["_last_message"] = message
        row["last_message"] = message[:500]
    if event in ("subagent_start", "pre", "post", "permission", "permission_request"):
        row.update(state="working", ended_at=None, idle_since=None, stale=False)
        if event in ("pre", "permission", "permission_request"):
            row["tool"] = text(payload.get("tool_name")) or None
            row["tool_count"] += 1
        elif event == "post":
            row["tool"] = None
    elif event == "stop_failure":
        _finish(row, "failed", now)
    elif event == "subagent_stop":
        idle = cli == "codex" or (teammate(aid) and not row.get("_listed"))
        _finish(row, "idle" if idle else "done", now)
    return True


def fold(ent: dict, payload: dict, now=None, ts=None) -> bool:
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list) or len(tasks) > 1000:
        return False
    if payload.get("background_tasks_truncated") or payload.get("background_tasks_complete") is False:
        return False
    if any(not isinstance(t, dict) or not valid_id(t.get("id"))
           or not isinstance(t.get("type"), str) or not isinstance(t.get("status"), str) for t in tasks):
        return False
    now = time.time() if now is None else now
    listed = {t["id"]: t for t in tasks if t["type"] == "subagent"}
    has_team = any(t["type"] == "teammate" for t in tasks)
    changed = False
    unlisted = []
    for aid, row in ent.get("_subagents", {}).items():
        if row["state"] in ("done", "failed"):
            continue
        if isinstance(ts, int) and isinstance(row.get("_ts"), int) and row["_ts"] > ts:
            continue
        task = listed.get(aid)
        if task:
            row["_listed"] = True
            if task["status"] == "running":
                row.update(state="working", ended_at=None, idle_since=None, stale=False)
                changed = True
                continue
        elif has_team and teammate(aid) and not row.get("_listed"):
            continue
        elif not teammate(aid) and not row.get("_listed"):
            # 목록에 한 번도 나타난 적 없는 행은 「목록에 없다」만으로 끝났다고 보지 않는다
            # (훅 agent_id 와 background_tasks id 가 같다는 실측이 없다). 끝남은 subagent_stop /
            # stop_failure, 또는 목록에 있다가 사라진 경우로 한정하고, 응답이 끊기면 sweep 의 STALE 이 맡는다.
            unlisted.append(aid)
            continue
        _finish(row, "done", now)
        changed = True
    if unlisted and not any(t["status"] == "running" for t in listed.values()) and not ent.get("_fold_warned"):
        # 진단: 목록에 running 서브에이전트가 없는데 행은 working 인 불일치(id 형식 차이 가능성)를 부모당 한 번만 남긴다.
        # 내용(프롬프트·메시지·경로)은 쓰지 않고 id 앞 6자와 개수만.
        ent["_fold_warned"] = True
        logger.warning("subagent fold: roster has no running subagent ids but %d rows are working (id prefixes: %s)",
                       len(unlisted), ",".join(a[:6] for a in unlisted[:3]))
    return changed


def idle_teammate(ent: dict, payload: dict, ts=None) -> bool:
    name = payload.get("teammate_name")
    if not isinstance(name, str) or not name or len(name) > 100:
        return False
    matches = [r for aid, r in ent.get("_subagents", {}).items()
               if aid.startswith(f"a{name}-") and teammate(aid) and not r.get("_listed")]
    if len(matches) != 1:
        return False
    row = matches[0]
    if isinstance(ts, int):
        barrier = ent.get("_subagents_since")
        if (isinstance(barrier, int) and ts < barrier) or (isinstance(row.get("_ts"), int) and ts <= row["_ts"]):
            return False
        row["_ts"] = ts
    row["_teammate"] = True
    _finish(row, "idle", time.time())
    return True
