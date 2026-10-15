"""옛 저장소 → authz 저장소 이전 (20-mapping.md B1) + 백업·복원.

## 번역(순수 함수)
`translate_*`는 옛 레코드를 새 grant·approval 로 바꾸는 **순수 함수**다. 두 곳이 같이 쓴다:
  - `fsh migrate-auth --apply`: 번역 결과를 저장소에 동기화(원본 id 기준 멱등).
  - `shadow`(1단계 기본 "live" 소스): 판정 지점이 쥔 옛 레코드를 그 자리에서 번역해 엔진에 넣는다 —
    1단계에선 옛 코드가 여전히 옛 저장소에만 쓰므로, 한 번 이전한 저장소로 비교하면 그 뒤에 생긴
    grant 가 전부 차이로 보인다.

| 옛 | 새 |
|---|---|
| agent_grants grant `{agent, target, read, write}` | Grant `agent:local/<k>` → `session:local/<k>`, read→[list, read], write→+[input.line] |
| agent_grants 대기 요청 | Approval `pending`(원래 expires) |
| agent_grants 거절 | Approval `denied` + `cooldown_until = decided + 300` |
| tmux `@fsh_share_*` | shares.json `{nick, level}` — 저장소에 이미 다른 값이 있으면 **낮은 쪽**(경고) |
| hosts.json grants | `principals/peers.json`(secret 은 **sha256 만**, 아래) + Grant view/control(D3·D7) + agentMessages→obo msg.send |
| delegations.json 항목 | Grant 부모→`deleg.control`, 자식→`deleg.report`(`approved_by: system:spawn`) |
| agent_access.log*, peer_audit.log* | `~/.vt/authz/legacy/<ts>/`로 복사(체인 없음) + 감사에 가져오기 기록 |

**계획과 다른 점(1단계)**: B1 은 peer secret 을 peers.json 으로 "이동"한다. 1단계에서는 HMAC 인증이 여전히
hosts.json 을 읽으므로 secret 을 복사하면 같은 비밀이 두 파일에 생기고, 재페어링하면 사본이 낡는다.
그래서 지금은 `secret_sha256`만 적고 실제 이동은 인증을 옮기는 3d 단계에서 한다.

## 멱등
모든 이전 레코드는 `origin = "legacy:<저장소>:<원본 id>"`를 갖는다. `--apply`는 legacy origin
레코드를 번역 결과와 **동기화**한다(같으면 그대로, 바뀌었으면 갱신, 옛 저장소에서 사라졌으면 제거).
legacy origin 이 아닌 레코드(새 엔진이 만든 것)는 건드리지 않는다. 두 번 돌려도 결과가 같다.

## 백업·복원
`--apply`·`--backup`은 먼저 `~/.vt/authz/backup/<ts>.tgz`(0600) + `<ts>.manifest.json`(각 파일 sha256,
tgz sha256)을 만든다. 담는 것: 옛 저장소 파일, tmux 공유 옵션 snapshot, 그때의 authz 문서.
`--restore <ts>`는 manifest 를 검증한 뒤(이름·크기·sha256) 현재 상태를 먼저 백업하고 되돌린다.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Iterable, Optional

import vt_paths

from . import audit, defaults, store

logger = logging.getLogger(__name__)

LEGACY_FILES = ("agent_grants.json", "hosts.json", "delegations.json", "agent_messages.json")
LEGACY_LOG_PREFIXES = ("agent_access.log", "peer_audit.log")
TMUX_SNAPSHOT = "tmux_shares.json"
DENY_COOLDOWN = 300
_TS_FMT = "%Y%m%dT%H%M%SZ"


class MigrateError(Exception):
    pass


def _sd() -> Path:
    return vt_paths.state_dir()


def _ts(now: float) -> str:
    return time.strftime(_TS_FMT, time.gmtime(now))


def _key(ref: dict) -> Optional[str]:
    if not isinstance(ref, dict) or not ref.get("sid") or ref.get("created") in (None, ""):
        return None
    return f"{ref['sid']}:{ref['created']}"


# --- 번역(순수) ---------------------------------------------------------------------------


def translate_agent_grant(g: dict, *, stamp: str = "live") -> Optional[dict]:
    ak, tk = _key(g.get("agent") or {}), _key(g.get("target") or {})
    if not ak or not tk or not g.get("id"):
        return None
    scopes = []
    if g.get("read"):
        scopes += ["session.list", "session.read"]
    if g.get("write"):
        scopes += ["session.input.line"]      # 옛 effective 처럼 읽기와 쓰기는 따로 본다
    if not scopes:
        return None
    return {"id": f"ag-{g['id']}", "origin": f"legacy:agent_grants:{g['id']}",
            "subject": f"agent:local/{ak}", "resource": f"session:local/{tk}", "scopes": scopes,
            "subject_session": ak, "resource_session": tk, "state": "active",
            "approved_by": f"migrated:{stamp}", "expires": None, "conds": {},
            "created": g.get("granted"), "updated": g.get("updated"),
            "subject_name": (g.get("agent") or {}).get("name") or ""}


def translate_agent_request(r: dict, now: float) -> Optional[dict]:
    ak, tk = _key(r.get("agent") or {}), _key(r.get("target") or {})
    if not ak or not tk or not r.get("id"):
        return None
    kind = r.get("kind")
    scopes = ["session.list", "session.read"] + (["session.input.line"] if kind == "write" else [])
    base = {"id": f"ap-{r['id']}", "origin": f"legacy:agent_grants:req:{r['id']}",
            "subject": f"agent:local/{ak}", "resource": f"session:local/{tk}", "scopes": scopes,
            "subject_session": ak, "resource_session": tk, "created": r.get("created"),
            "subject_name": (r.get("agent") or {}).get("name") or ""}
    if r.get("state") == "denied":
        until = float(r.get("decided", 0)) + DENY_COOLDOWN
        if now >= until:
            return None
        return {**base, "state": "denied", "decided": r.get("decided"), "cooldown_until": until,
                "expires": r.get("expires")}
    if r.get("state") == "pending":
        if now >= float(r.get("expires", 0)):
            return None
        return {**base, "state": "pending", "expires": r.get("expires")}
    return None


def translate_peer_grant(g: dict, *, stamp: str = "live") -> list[dict]:
    pid = str(g.get("id") or "")
    if not pid:
        return []
    control = g.get("level") == "control"
    out = [{"id": f"pg-{pid}", "origin": f"legacy:hosts:{pid}", "subject": f"peer:{pid}", "resource": "*",
            "scopes": list(defaults.PEER_CONTROL_SCOPES if control else defaults.PEER_VIEW_SCOPES),
            "preset": "control" if control else "view", "state": "active",
            "approved_by": f"migrated:{stamp}", "expires": None, "conds": {}}]
    if g.get("agentMessages"):
        out.append({"id": f"pm-{pid}", "origin": f"legacy:hosts:{pid}:agentMessages",
                    "subject": f"peer:{pid}/obo:agent:*", "resource": "msg:local/*", "scopes": ["msg.send"],
                    "state": "active", "approved_by": f"migrated:{stamp}", "expires": None, "conds": {}})
    return out


def translate_peer_principal(g: dict) -> Optional[dict]:
    pid = str(g.get("id") or "")
    if not pid:
        return None
    sec = g.get("secret") or ""
    return {"id": pid, "origin": f"legacy:hosts:{pid}", "label": g.get("label") or "",
            "secret_sha256": hashlib.sha256(sec.encode()).hexdigest() if sec else None,
            "version": 1, "lastSeen": g.get("lastSeen"), "added": g.get("added")}


def translate_delegation(item: dict) -> list[dict]:
    did = str(item.get("id") or "")
    pk = ((item.get("parent") or {}).get("key")) or ""
    ck = ((item.get("child") or {}).get("key")) or ""
    out = []
    if did and pk:
        out.append({"id": f"dc-{did}", "origin": f"legacy:delegations:{did}:control",
                    "subject": f"agent:local/{pk}", "resource": f"deleg:local/{did}", "scopes": ["deleg.control"],
                    "state": "active", "approved_by": "system:spawn", "expires": None, "conds": {}})
    if did and ck:
        out.append({"id": f"dr-{did}", "origin": f"legacy:delegations:{did}:report",
                    "subject": f"agent:local/{ck}", "resource": f"deleg:local/{did}", "scopes": ["deleg.report"],
                    "state": "active", "approved_by": "system:spawn", "expires": None, "conds": {}})
    return out


def translate_share(nick: str, mode: str) -> Optional[dict]:
    import tmux_runner
    s = tmux_runner.parse_share(nick, mode)
    return {"nick": s["nick"], "level": s["mode"]} if s else None


def live_grants(*, agent_grants: Iterable[dict] = (), peer_grants: Iterable[dict] = (),
                delegations: Iterable[dict] = ()) -> list[dict]:
    """판정 지점이 쥔 옛 레코드를 그 자리에서 번역(shadow live 소스)."""
    out: list[dict] = []
    for g in agent_grants or ():
        t = translate_agent_grant(g)
        if t:
            out.append(t)
    for g in peer_grants or ():
        out += translate_peer_grant(g)
    for it in delegations or ():
        out += translate_delegation(it)
    return out


# --- 옛 저장소 읽기(읽기 전용, 락 없음 — 계획 단계) -----------------------------------------


def _read_json(p: Path) -> tuple[Optional[dict], Optional[str]]:
    if not p.is_file():
        return None, None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{p.name}: 읽기 실패({e}) — 이 파일은 건너뜀"
    if not isinstance(d, dict):
        return None, f"{p.name}: 형식이 dict 가 아님 — 건너뜀"
    return d, None


def tmux_snapshot() -> Optional[dict]:
    """`{key: {name, nick, mode}}` — tmux 를 못 읽으면 None(F3: 죽은 키 정리를 하지 않는다)."""
    import share_identity
    rows = share_identity.list_pane_rows()
    if rows is None:
        return None
    out: dict = {}
    for r in rows:
        k = f"{r.sid}:{r.created}"
        if k not in out or r.active:
            out[k] = {"name": r.name, "nick": r.nick, "mode": r.mode}
    return out


def plan(*, now: Optional[float] = None, tmux: Optional[dict] = "probe", stamp: Optional[str] = None) -> dict:
    """이전 계획(무엇을 쓸지) — 저장소를 바꾸지 않는다. tmux="probe"면 지금 tmux 를 읽는다."""
    now = time.time() if now is None else now
    stamp = stamp or _ts(now)
    if tmux == "probe":
        try:
            tmux = tmux_snapshot()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tmux snapshot 실패({e}) — 죽은 키 정리 없이 진행")
            tmux = None
    alive = set(tmux) if tmux is not None else None
    warnings: list[str] = []
    grants: list[dict] = []
    approvals: list[dict] = []
    peers: list[dict] = []
    dropped = {"dead_session": 0, "expired": 0, "empty": 0}
    sd = _sd()

    ag, w = _read_json(sd / "agent_grants.json")
    if w:
        warnings.append(w)
    for g in (ag or {}).get("grants", []) if isinstance((ag or {}).get("grants", []), list) else []:
        if not isinstance(g, dict):
            continue
        t = translate_agent_grant(g, stamp=stamp)
        if t is None:
            dropped["empty"] += 1
            continue
        if alive is not None and (t["subject_session"] not in alive or t["resource_session"] not in alive):
            dropped["dead_session"] += 1
            continue
        grants.append(t)
    for r in (ag or {}).get("requests", []) if isinstance((ag or {}).get("requests", []), list) else []:
        if not isinstance(r, dict):
            continue
        t = translate_agent_request(r, now)
        if t is None:
            dropped["expired"] += 1
            continue
        if alive is not None and (t["subject_session"] not in alive or t["resource_session"] not in alive):
            dropped["dead_session"] += 1
            continue
        approvals.append(t)

    hs, w = _read_json(sd / "hosts.json")
    if w:
        warnings.append(w)
    for g in (hs or {}).get("grants", []) if isinstance((hs or {}).get("grants", []), list) else []:
        if isinstance(g, dict):
            grants += translate_peer_grant(g, stamp=stamp)
            pp = translate_peer_principal(g)
            if pp:
                peers.append(pp)

    dl, w = _read_json(sd / "delegations.json")
    if w:
        warnings.append(w)
    for it in (dl or {}).get("items", []) if isinstance((dl or {}).get("items", []), list) else []:
        if isinstance(it, dict):
            grants += translate_delegation(it)

    shares: dict = {}
    existing, _bad = store._read("shares")
    for k, row in (tmux or {}).items():
        s = translate_share(row.get("nick", ""), row.get("mode", ""))
        if not s:
            continue
        cur = existing["items"].get(k)
        if isinstance(cur, dict) and cur.get("level") != s["level"]:
            from . import ceilings
            low = ceilings.lower(ceilings.level_of(cur), s["level"])
            warnings.append(f"공유 등급 불일치 {row.get('name')}({k}): 저장소 {cur.get('level')} ≠ tmux {s['level']} "
                            f"→ 낮은 쪽 {low}")
            s = {**s, "level": low}
        shares[k] = {**s, "origin": f"legacy:tmux:{k}"}
    if tmux is None:
        warnings.append("tmux 를 읽지 못함 — 공유 정책 snapshot 없음, 죽은 세션 정리 안 함(F3)")

    logs = sorted(p.name for p in sd.glob("*") if p.is_file() and p.name.startswith(LEGACY_LOG_PREFIXES)
                  and not p.name.endswith(".lock"))
    return {"stamp": stamp, "now": now, "grants": grants, "approvals": approvals, "peers": peers,
            "shares": shares, "logs": logs, "warnings": warnings, "dropped": dropped,
            "tmux_readable": tmux is not None, "tmux_sessions": len(tmux) if tmux is not None else None}


def _is_legacy(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith("legacy:")


def _sync(items: list, new: list, keep_fields: tuple = ("approved_by",)) -> tuple[list, dict]:
    """legacy origin 레코드를 new 와 동기화. (결과 목록, {added, updated, removed, same})."""
    by_origin = {x.get("origin"): x for x in items if _is_legacy(x)}
    native = [x for x in items if not _is_legacy(x)]
    stats = {"added": 0, "updated": 0, "removed": 0, "same": 0}
    out = []
    seen = set()
    for n in new:
        o = n["origin"]
        seen.add(o)
        cur = by_origin.get(o)
        if cur is None:
            stats["added"] += 1
            out.append(n)
            continue
        merged = {**n, **{k: cur[k] for k in keep_fields if k in cur}}
        if merged == cur:
            stats["same"] += 1
        else:
            stats["updated"] += 1
        out.append(merged)
    stats["removed"] = sum(1 for o in by_origin if o not in seen)
    return native + out, stats


def _dict_sync(items: dict, new: dict) -> tuple[dict, dict]:
    stats = {"added": 0, "updated": 0, "removed": 0, "same": 0}
    out = {k: v for k, v in items.items() if not (isinstance(v, dict) and _is_legacy(v))}
    for k, v in new.items():
        cur = items.get(k)
        stats["same" if cur == v else ("updated" if cur is not None else "added")] += 1
        out[k] = v
    stats["removed"] = sum(1 for k, v in items.items() if isinstance(v, dict) and _is_legacy(v) and k not in new)
    return out, stats


def diff(p: dict) -> dict:
    """계획을 저장소에 적용하면 바뀔 것(쓰지 않는다)."""
    snap = {n: store._read(n)[0]["items"] for n in ("grants", "approvals", "shares", "principals/peers")}
    _, g = _sync(snap["grants"], p["grants"])
    _, a = _sync(snap["approvals"], p["approvals"])
    _, s = _dict_sync(snap["shares"], p["shares"])
    _, pe = _sync(snap["principals/peers"], p["peers"], keep_fields=())
    return {"grants": g, "approvals": a, "shares": s, "peers": pe}


# --- 백업·복원 ---------------------------------------------------------------------------


def backup_dir() -> Path:
    return store.root() / "backup"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _backup_members(tmux: Optional[dict]) -> dict[str, bytes]:
    sd = _sd()
    out: dict[str, bytes] = {}
    for n in LEGACY_FILES:
        p = sd / n
        if p.is_file():
            out[n] = p.read_bytes()
    for p in sorted(sd.glob("*")):
        if p.is_file() and p.name.startswith(LEGACY_LOG_PREFIXES) and not p.name.endswith(".lock"):
            out[p.name] = p.read_bytes()
    out[TMUX_SNAPSHOT] = (json.dumps({"readable": tmux is not None, "sessions": tmux or {}},
                                     ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode()
    for n in store.DOCS:
        p = store.doc_path(n)
        if p.is_file():
            out[f"authz/{n}.json"] = p.read_bytes()
    return out


def backup(*, now: Optional[float] = None, tmux: Optional[dict] = "probe", tag: str = "") -> dict:
    now = time.time() if now is None else now
    if tmux == "probe":
        try:
            tmux = tmux_snapshot()
        except Exception:  # noqa: BLE001
            tmux = None
    members = _backup_members(tmux)
    d = backup_dir()
    store._ensure_dir(d)
    ts = _ts(now) + (f"-{tag}" if tag else "")
    i = 1
    base = ts
    while (d / f"{ts}.tgz").exists():
        i += 1
        ts = f"{base}-{i}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(now)
            info.mode = 0o600
            tf.addfile(info, io.BytesIO(data))
    raw = buf.getvalue()
    store.write_private(d / f"{ts}.tgz", raw)
    manifest = {"ts": ts, "created": now, "tgz_sha256": _sha(raw), "schema": store.SCHEMA_VERSION,
                "files": {n: {"sha256": _sha(b), "size": len(b)} for n, b in sorted(members.items())}}
    store.write_private(d / f"{ts}.manifest.json",
                        (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
    return manifest


def list_backups() -> list[dict]:
    out = []
    for m in sorted(backup_dir().glob("*.manifest.json")):
        try:
            j = json.loads(m.read_text(encoding="utf-8"))
            out.append({"ts": j.get("ts"), "created": j.get("created"), "files": len(j.get("files") or {})})
        except (OSError, ValueError):
            out.append({"ts": m.name[:-len(".manifest.json")], "error": "manifest 손상"})
    return out


def _allowed_member(name: str) -> bool:
    if name in LEGACY_FILES or name == TMUX_SNAPSHOT:
        return True
    if "/" not in name and name.startswith(LEGACY_LOG_PREFIXES) and not name.endswith(".lock") and ".." not in name:
        return True
    return name in {f"authz/{n}.json" for n in store.DOCS}


def _load_backup(ts: str) -> tuple[dict, dict[str, bytes]]:
    if not ts or "/" in ts or ts.startswith(".") or "\x00" in ts:
        raise MigrateError(f"잘못된 백업 이름: {ts!r}")
    d = backup_dir()
    mp, tp = d / f"{ts}.manifest.json", d / f"{ts}.tgz"
    if not mp.is_file() or not tp.is_file():
        raise MigrateError(f"백업 {ts} 이 없습니다(fsh migrate-auth --status 로 목록 확인)")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    raw = tp.read_bytes()
    if _sha(raw) != manifest.get("tgz_sha256"):
        raise MigrateError(f"백업 {ts}.tgz 의 sha256 이 manifest 와 다릅니다 — 복원 중단")
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not _allowed_member(m.name):
                raise MigrateError(f"백업에 허용되지 않은 항목: {m.name!r} — 복원 중단")
            f = tf.extractfile(m)
            members[m.name] = f.read() if f else b""
    files = manifest.get("files") or {}
    if set(files) != set(members):
        raise MigrateError("백업 항목 목록이 manifest 와 다릅니다 — 복원 중단")
    for n, b in members.items():
        if _sha(b) != files[n].get("sha256") or len(b) != files[n].get("size"):
            raise MigrateError(f"{n} 의 sha256/크기가 manifest 와 다릅니다 — 복원 중단")
    return manifest, members


def _legacy_locks(stack: ExitStack) -> None:
    """옛 저장소들의 락 — 서버가 떠 있어도 복원 도중 옛 코드가 끼어 쓰지 못하게."""
    import agent_share
    import delegation
    import host_store
    stack.enter_context(agent_share._locked())
    stack.enter_context(host_store._locked())
    stack.enter_context(delegation._locked())


def restore(ts: str, *, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    manifest, members = _load_backup(ts)
    pre = backup(now=now, tag="pre-restore")
    sd = _sd()
    restored, removed = [], []
    with ExitStack() as stack:
        stack.enter_context(store.locked())
        _legacy_locks(stack)
        for n in LEGACY_FILES:
            if n in members:
                store.write_private(sd / n, members[n])
                restored.append(n)
            elif (sd / n).exists():
                (sd / n).unlink()
                removed.append(n)
        for n in store.DOCS:
            key = f"authz/{n}.json"
            p = store.doc_path(n)
            if key in members:
                store.write_private(p, members[key])
                restored.append(key)
            elif p.exists():
                p.unlink()
                removed.append(key)
    # 로그는 덮지 않는다(그 뒤에 쌓인 기록을 지우지 않게) — 백업 안에 남아 있다.
    try:
        audit.append({"principal": "cli:migrate-auth", "action": "authz.migrate.restore", "resource": "authz:*",
                      "decision": "allow", "result": "ok", "reason": f"restore {ts}",
                      "detail": {"restored": len(restored), "removed": len(removed), "pre_restore": pre["ts"]}})
    except OSError as e:
        logger.warning(f"복원 감사 기록 실패: {e}")
    return {"restored": restored, "removed": removed, "pre_restore_backup": pre["ts"], "from": manifest["ts"]}


# --- 적용 ---------------------------------------------------------------------------------


def _import_logs(p: dict, stamp: str, meta: dict) -> dict:
    """옛 감사 로그를 `authz/legacy/<stamp>/`로 복사(체인 없음). 같은 sha 는 다시 가져오지 않는다."""
    seen = set(meta.get("imported_logs") or [])
    dst = store.root() / "legacy" / stamp
    stats = {"copied": 0, "skipped": 0, "lines": {}}
    for name in p["logs"]:
        src = _sd() / name
        try:
            b = src.read_bytes()
        except OSError:
            continue
        h = _sha(b)
        if h in seen:
            stats["skipped"] += 1
            continue
        store.write_private(dst / name, b)
        seen.add(h)
        stats["copied"] += 1
        stats["lines"][name] = b.count(b"\n")
    meta["imported_logs"] = sorted(seen)
    return stats


def apply(*, now: Optional[float] = None, tmux: Optional[dict] = "probe") -> dict:
    now = time.time() if now is None else now
    if tmux == "probe":
        try:
            tmux = tmux_snapshot()
        except Exception:  # noqa: BLE001
            tmux = None
    bk = backup(now=now, tmux=tmux)
    p = plan(now=now, tmux=tmux, stamp=bk["ts"])
    with store.txn() as docs:
        docs["grants"]["items"], g = _sync(docs["grants"]["items"], p["grants"])
        docs["approvals"]["items"], a = _sync(docs["approvals"]["items"], p["approvals"])
        docs["shares"]["items"], s = _dict_sync(docs["shares"]["items"], p["shares"])
        docs["principals/peers"]["items"], pe = _sync(docs["principals/peers"]["items"], p["peers"], keep_fields=())
        meta = docs["meta"]["items"]
        logs = _import_logs(p, bk["ts"], meta)
        changed = any(x["added"] or x["updated"] or x["removed"] for x in (g, a, s, pe))
        if changed or "applied_at" not in meta:
            meta["applied_at"] = now
        meta["last_backup"] = bk["ts"]
        if tmux:                      # tmux 를 읽었고 세션이 있다 → 공유 정책 snapshot 완료(3a 가 이 표시를 본다)
            meta["share_adopted"] = True
        meta["schema"] = store.SCHEMA_VERSION
        meta["counts"] = {"grants": len(docs["grants"]["items"]), "approvals": len(docs["approvals"]["items"]),
                          "shares": len(docs["shares"]["items"]), "peers": len(docs["principals/peers"]["items"])}
    result = {"backup": bk["ts"], "grants": g, "approvals": a, "shares": s, "peers": pe, "logs": logs,
              "warnings": p["warnings"], "dropped": p["dropped"]}
    try:
        audit.append({"principal": "cli:migrate-auth", "action": "authz.migrate.apply", "resource": "authz:*",
                      "decision": "allow", "result": "ok", "reason": f"backup {bk['ts']}",
                      "detail": {"grants": g, "approvals": a, "shares": s, "peers": pe}})
        for name, n in logs["lines"].items():
            src = "legacy:agent_access" if name.startswith("agent_access") else "legacy:peer_audit"
            audit.append({"principal": "cli:migrate-auth", "action": "authz.migrate.import_log", "resource": "authz:*",
                          "decision": "allow", "result": "ok", "source": src,
                          "detail": {"file": f"legacy/{bk['ts']}/{name}", "lines": n}})
    except OSError as e:
        # R11.5 — authz 변경은 감사 실패 시 fail closed 지만, 이전은 이미 백업이 있고 shadow 단계라 경고로 둔다.
        result["warnings"].append(f"감사 기록 실패: {e}")
    return result


def verify_store(*, now: Optional[float] = None, tmux: Optional[dict] = "probe") -> dict:
    """저장소가 지금의 옛 저장소 번역과 같은가(원본 id 기준). 차이가 없으면 diff 가 전부 same."""
    p = plan(now=now, tmux=tmux)
    d = diff(p)
    clean = all(not (x["added"] or x["updated"] or x["removed"]) for x in d.values())
    return {"clean": clean, "diff": d, "warnings": p["warnings"]}


def status() -> dict:
    meta, bad = store._read("meta")
    sh = store.root() / "shadow.jsonl"
    n = 0
    try:
        with open(sh, "rb") as f:
            n = sum(1 for _ in f)
    except OSError:
        pass
    ok, broken, lines = audit.verify()
    return {"migrated": "applied_at" in meta["items"], "meta": meta["items"], "meta_corrupt": bad,
            "backups": list_backups(), "shadow_mismatches": n,
            "audit": {"ok": ok, "broken_seq": broken, "lines": lines}}
