"""pair — 두 참여자(에이전트 둘, 또는 사람 하나와 에이전트 하나)가 같은 작업 묶음을 함께 하는 장치. 문서 `~/.vt/authz/pairs.json`.

**pair 는 주체가 아니다.** 단독 인증 수단도 아니고, 구성원(사람·에이전트)이 호출한다. 승인되면 엔진이 구성원마다 역할 프리셋에서 만든
grant 묶음을 쓰고(`pair_id` 표시), pair 가 끝나면 그 grant·lease 가 함께 거둬진다. 켜는 스위치는 `VT_AUTHZ_ENFORCE` 의 `pair` 토큰 하나다 —
꺼져 있으면 모든 pair 라우트가 409 `pair_disabled` 이고 다른 어떤 동작도 바뀌지 않는다.

## 절차
  1. 만들기(`create`): 사람, 또는 `pair.manage` 를 가진 에이전트가 묶음(세션·폴더·lease 여부·요청할 elevated 항목)과 자기 역할로 만든다.
     같은 시작자가 같은 묶음으로 다시 만들면 같은 pair 를, 다른 묶음으로 만들면 이전의 아직 합류 전인 pair 를 폐기한다.
  2. 코드(`issue_code`): Crockford base32 8자(`ABCD-EFGH`)와 2단어 확인 문구. 5분, 1회용. 대기 중인 코드는 **같은 pair** 에서만 그대로 다시 준다(멱등).
     시작자당 시간당 10번까지 새로 만든다. 코드는 통째로 다시 만들면 되는 값이라 베어러로 쓰지 않는다 — 합류 결과는 코드가 아니라 합류자의 검증된 신원으로 안다.
  3. 합류(`join`): 확인된 에이전트가 코드를 낸다. 틀린 코드는 대기 중인 모든 코드의 시도 횟수를 올리고 5번이면 그 코드들이 폐기된다.
     맞으면 코드는 소진되고 승인 요청(kind=`pair`, id `pq-…`)이 생긴다.
  4. 승인(`decide`): 사람이 **한 화면**에서 묶음 전체·두 확인 문구·역할별 프리셋·elevated 항목을 보고 결정한다. elevated 항목(raw 입력, 세션 관리,
     릴레이, 브라우저 js/민감)은 명시로 체크해야만 주고, 체크가 있으면 재인증(elevated 세션)을 서버가 요구한다. 에이전트는 승인할 수 없다.
     승인한 뒤 권한을 넓히려면 언제나 새 승인이다.
  5. 합류자는 `status` 를 자기 신원으로 불러 결과를 안다(응답을 잃어도 다시 발급할 필요가 없다).

## 역할 · handoff
  역할은 `driver`·`navigator` 둘뿐이고 driver 는 언제나 정확히 한 명이다. navigator = 「협업」 프리셋(보기·읽기·메시지·파일 읽기), driver = 「운전」(+한 줄 입력·파일 쓰기·
  위임 spawn). 이 빌드에는 브라우저 중개가 없어 프리셋의 `browser` 는 빼고 준다. 사람 구성원은 역할과 무관하게 언제나 입력할 수 있고(사람 기본 권한),
  에이전트 입력은 기존 사람-입력 중 양보 규칙(`pane_input`)이 그대로 비켜 선다. handoff = driver 가 제안 → navigator 가 수락, 저장소 락 아래에서
  역할과 grant 를 한 번에 바꾸고 세대 번호를 하나 올린다. 2분 안에 수락하지 않으면 원래대로. 사람이 제안하면(override) 수락 없이 곧바로 바뀐다.

## 끝
  명시적 종료, 사람의 회수, 유휴 `VT_PAIR_IDLE_MIN`(기본 120분), 구성원 세션 소멸, 최대 8시간. 끝나면 grant 를 즉시 `revoking` 으로 돌리고 lease 를 놓은 뒤
  회수 saga(`saga.py`)가 큐·메시지·위임까지 정리하고 확인한다 — 멱등이고 서버가 도중에 죽어도 sweep 이 이어서 끝낸다.

## 한계
  같은 uid 의 프로세스는 `~/.vt` 를 직접 고칠 수 있다(`docs/ref/auth.md`). pair 는 신원 귀속·사고 차단·변조 탐지를 위한 장치이고 같은 계정 안의 적을 막지 못한다.
  다른 Mac 의 참여자(`{host, session}` 참조)는 아직 501 `pair_remote_unsupported` 이다 — 자료 모양(ref)만 준비돼 있다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from typing import Iterable, Optional

from i18n import tr

from . import audit, engine, flags, limits, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

# --- 상태 -------------------------------------------------------------------------------------

PROPOSED, CODE_ISSUED, JOINED, ACTIVE, HANDOFF_PENDING = "proposed", "code_issued", "joined", "active", "handoff_pending"
ENDED, EXPIRED, REVOKED = "ended", "expired", "revoked"
LIVE_STATES = (PROPOSED, CODE_ISSUED, JOINED, ACTIVE, HANDOFF_PENDING)
RUNNING_STATES = (ACTIVE, HANDOFF_PENDING)           # grant 가 효력을 갖는 상태
TERMINAL = (ENDED, EXPIRED, REVOKED)
DRIVER, NAVIGATOR = "driver", "navigator"
ROLES = (DRIVER, NAVIGATOR)
INITIATOR, JOINER = "initiator", "joiner"

# --- 상수 -------------------------------------------------------------------------------------

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32 (I L O U 제외)
CODE_LEN = 8
CODE_TTL = 300
MAX_ATTEMPTS = limits.PAIR_CODE_ATTEMPTS
OFFER_TTL = 120
IDLE_DEFAULT_MIN = 120
MAX_LIFETIME = 8 * 3600
PROPOSAL_TTL = 3600                                  # 합류 전 pair 가 살아 있는 시간
APPROVAL_TTL = limits.PENDING_TTL
PHRASE_MISS_LIMIT = 3
MAX_LIVE_PER_CREATOR = 5
MAX_SESSIONS = 8
MAX_DIRS = 8
KEEP_TERMINAL = 50
TERMINAL_RETENTION = 24 * 3600
WORDS = ("amber", "anchor", "apple", "birch", "bridge", "candle", "canyon", "cedar", "cloud", "comet", "coral", "dawn", "delta",
         "dune", "echo", "ember", "falcon", "fjord", "forest", "garden", "glacier", "harbor", "hazel", "iris", "island", "jasper",
         "juniper", "kernel", "koala", "lantern", "lotus", "maple", "marble", "meadow", "mirror", "nectar", "nimbus", "ocean",
         "olive", "orchard", "pebble", "planet", "prairie", "quartz", "quill", "raven", "river", "sable", "saddle", "silver",
         "spruce", "summit", "thunder", "timber", "tulip", "tundra", "umber", "valley", "velvet", "violet", "willow", "window",
         "yonder", "zephyr")
_KEY_RE = re.compile(r"^\$\d+:\d+$")
_ID_RE = re.compile(r"^pr-[0-9a-f]{10}$")
_REQ_RE = re.compile(r"^(?:ap-)?pq-([0-9a-f]{10})$")

# elevated 항목 — 승인 때 명시로 체크해야만 주는 것(`scopes.requires_elevated` 의 사유와 같다). 모두 driver 역할에만 붙는다.
ELEVATED_ITEMS = ("raw_input", "session_manage", "relay", "browser_js", "browser_sensitive")


def elevated_label(item: str) -> str:
    return {"raw_input": tr("raw 키 입력(세션에 키를 그대로 보냄)"),
            "session_manage": tr("세션 관리(만들기·종료·이름 바꾸기·접속 끊기)"),
            "relay": tr("릴레이(다른 기기를 거치는 요청 전체)"),
            "browser_js": tr("브라우저에서 스크립트 실행"),
            "browser_sensitive": tr("브라우저 민감 정보(쿠키·저장소·네트워크 등)")}.get(item, item)


class PairError(ShareError):
    pass


def _err(code: str, reason: str, status: int, **extra) -> PairError:
    return PairError(code, reason, status, **extra)


# --- 시계 · 스위치 ----------------------------------------------------------------------------


def now() -> float:
    """시험이 이 함수를 바꿔 가짜 시계를 쓴다."""
    return time.time()


def enabled() -> bool:
    return flags.enforced("pair")


def require_enabled() -> None:
    if not enabled():
        raise _err("pair_disabled", tr("pair 기능이 꺼져 있습니다 — 서버 설정 VT_AUTHZ_ENFORCE 에 pair 를 더해야 합니다"), 409)


def idle_seconds() -> float:
    raw = os.environ.get("VT_PAIR_IDLE_MIN", "")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = IDLE_DEFAULT_MIN
    return max(1, v) * 60.0


# --- 코드 -------------------------------------------------------------------------------------


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(raw) -> str:
    """입력 → 정규형 8자. Crockford 규칙(O→0, I·L→1, 하이픈·공백 무시, 대소문자 무시). 형식이 틀리면 빈 문자열."""
    s = "".join(ch for ch in str(raw or "").upper() if ch not in " -_")
    s = s.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    if len(s) != CODE_LEN or any(c not in CODE_ALPHABET for c in s):
        return ""
    return s


def _hash(code: str) -> str:
    return hashlib.sha256(("fsh-pair-code|" + code).encode()).hexdigest()


def new_phrase() -> str:
    return " ".join(secrets.choice(WORDS) for _ in range(2))


def normalize_phrase(raw) -> str:
    return " ".join(str(raw or "").lower().split())


_RATE = limits.Window(limits.LIMITS["pairing.code"][0], limits.LIMITS["pairing.code"][1])


def reset_rate() -> None:
    global _RATE
    _RATE = limits.Window(limits.LIMITS["pairing.code"][0], limits.LIMITS["pairing.code"][1])


# --- 환경 읽기(시험이 바꾼다) -----------------------------------------------------------------


def _pane_rows():
    """우리 tmux 의 pane 목록. 읽지 못하면 None."""
    import share_identity
    return share_identity.list_pane_rows()


def _alive_keys() -> Optional[set]:
    from . import leases
    return leases.alive_keys(fresh=True)


def _activity() -> Optional[dict]:
    """세션 key → 마지막 활동 시각(tmux `session_activity`). 읽지 못하면 None(유휴로 끝내지 않는다)."""
    try:
        import tmux_runner
        rc, out, _ = tmux_runner.run(["list-sessions", "-F", "#{session_id}\t#{session_created}\t#{session_activity}"], timeout=3.0)
        if rc != 0:
            return None
        res = {}
        for line in out.decode("utf-8", "replace").splitlines():
            a = line.split("\t")
            if len(a) == 3 and a[1].isdigit() and a[2].isdigit():
                res[f"{a[0]}:{a[1]}"] = float(a[2])
        return res
    except Exception:  # noqa: BLE001
        return None


def _local_host_ids() -> set:
    ids = {"", "local"}
    try:
        import host_store
        ids.add(str(host_store.get_self().get("id") or "-"))
    except Exception:  # noqa: BLE001
        pass
    return ids


# --- 참조(ref) · 묶음 -------------------------------------------------------------------------


def _resolve_ref(raw, rows) -> dict:
    """세션 참조 → `{key, name}`. 참조 모양: 문자열(세션 key 또는 이름) | `{local: key}` | `{host, session}`(다른 Mac 은 501)."""
    host, target = None, None
    if isinstance(raw, dict):
        if "host" in raw:
            host, target = str(raw.get("host") or ""), str(raw.get("session") or "")
        elif "local" in raw:
            target = str(raw.get("local") or "")
        else:
            raise _err("bad_ref", tr("세션 참조가 올바르지 않습니다"), 400)
    elif isinstance(raw, str):
        target = raw.strip()
    else:
        raise _err("bad_ref", tr("세션 참조가 올바르지 않습니다"), 400)
    if host is not None and host not in _local_host_ids():
        raise _err("pair_remote_unsupported", tr("다른 Mac 의 세션은 아직 pair 에 넣을 수 없습니다"), 501, host=host[:64])
    if not target or len(target) > 128:
        raise _err("bad_ref", tr("세션 참조가 올바르지 않습니다"), 400)
    by_key = {f"{r.sid}:{r.created}": r.name for r in rows} if rows is not None else {}
    if _KEY_RE.match(target):
        if rows is not None and target not in by_key:
            raise _err("session_not_found", tr("그런 세션이 없습니다"), 404)
        return {"key": target, "name": by_key.get(target, "")}
    if rows is None:
        raise _err("tmux_unavailable", tr("세션 목록을 읽을 수 없습니다"), 503)
    hits = [k for k, n in by_key.items() if n == target]
    if not hits:
        raise _err("session_not_found", tr("그런 세션이 없습니다"), 404)
    if len(hits) > 1:
        raise _err("ambiguous_session", tr("이름이 같은 세션이 여럿입니다 — 세션 key 로 지정하세요"), 400)
    return {"key": hits[0], "name": target}


def _bundle_hash(bundle: dict, role: str, with_key: Optional[str]) -> str:
    canon = json.dumps({"s": sorted(x["key"] for x in bundle["sessions"]), "d": sorted(bundle["dirs"]), "l": bool(bundle["leases"]),
                        "e": sorted(bundle["elevated"]), "r": role, "w": with_key or ""}, sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()


def _parse_bundle(body: dict, rows) -> dict:
    from . import leases
    sessions_raw = body.get("sessions") or []
    dirs_raw = body.get("dirs") or []
    elev = body.get("elevated") or []
    if not isinstance(sessions_raw, list) or not isinstance(dirs_raw, list) or not isinstance(elev, list):
        raise _err("bad_bundle", tr("sessions·dirs·elevated 는 목록이어야 합니다"), 400)
    if len(sessions_raw) > MAX_SESSIONS or len(dirs_raw) > MAX_DIRS:
        raise _err("bad_bundle", tr("묶음이 너무 큽니다 — 세션 {s}개, 폴더 {d}개까지", s=MAX_SESSIONS, d=MAX_DIRS), 400)
    sessions, seen = [], set()
    for r in sessions_raw:
        ref = _resolve_ref(r, rows)
        if ref["key"] not in seen:
            seen.add(ref["key"])
            sessions.append(ref)
    dirs = []
    for d in dirs_raw:
        c = leases.canonicalize(str(d))
        if c not in dirs:
            dirs.append(c)
    bad = [x for x in elev if x not in ELEVATED_ITEMS]
    if bad:
        raise _err("bad_elevated", tr("알 수 없는 elevated 항목입니다"), 400, items=[str(x)[:40] for x in bad[:5]])
    if not sessions and not dirs:
        raise _err("empty_bundle", tr("묶음에 세션이나 폴더가 하나는 있어야 합니다"), 400)
    return {"sessions": sessions, "dirs": dirs, "leases": bool(body.get("leases")) and bool(dirs), "elevated": sorted(set(elev))}


# --- 레코드 도우미 ----------------------------------------------------------------------------


def _raw(pid: str) -> str:
    return pid[3:] if pid.startswith("pr-") else pid


def _find(docs: dict, pid: str) -> Optional[dict]:
    return next((x for x in docs["pairs"]["items"] if x.get("id") == pid), None)


def _member(pair: dict, seat: str) -> dict:
    return next(m for m in pair["members"] if m["seat"] == seat)


def _other(pair: dict, m: dict) -> dict:
    return next(x for x in pair["members"] if x is not m)


def _driver(pair: dict) -> dict:
    ds = [m for m in pair["members"] if m["role"] == DRIVER]
    if len(ds) != 1:                                  # 불변식 — 깨지면 조용히 넘어가지 않는다
        raise _err("pair_corrupt", tr("pair 의 driver 가 정확히 한 명이 아닙니다"), 500)
    return ds[0]


def _is_agent_member(m: dict) -> bool:
    return m.get("kind") == "agent" and bool(m.get("principal"))


def _caller_member(pair: dict, p: P.Principal) -> Optional[dict]:
    if p.kind == P.AGENT:
        return next((m for m in pair["members"] if _is_agent_member(m) and m["principal"] == p.id), None)
    if p.kind == P.HUMAN:
        return next((m for m in pair["members"] if m.get("kind") == "human"), None)
    return None


def _creator_ok(pair: dict, p: P.Principal) -> bool:
    c = pair.get("creator") or {}
    if p.kind == P.HUMAN:
        return c.get("kind") == "human"
    return p.kind == P.AGENT and c.get("id") == p.id


def _new_id() -> str:
    return "pr-" + uuid.uuid4().hex[:10]


def _audit(action: str, who: str, pair: dict, *, result: str = "ok", reason: str = "", scopes: Iterable[str] = ()) -> None:
    """내용 없는 감사 한 줄 — 개수·상태·세대만(세션 이름·경로·코드·확인 문구는 싣지 않는다). 못 쓰면 503 으로 변경을 중단."""
    rec = {"principal": who, "action": f"pair.{action}", "resource": f"pair:{pair['id']}", "scopes": list(scopes), "decision": "allow",
           "result": result, "detail": {"state": pair.get("state"), "generation": pair.get("generation"),
                                        "sessions": len(pair["bundle"]["sessions"]), "dirs": len(pair["bundle"]["dirs"]),
                                        "leases": bool(pair["bundle"]["leases"]), "elevated": len(pair["bundle"]["elevated"])}}
    if reason:
        rec["reason"] = reason
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise _err("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


def _deny_log(who: str, action: str, reason: str) -> None:
    try:
        from . import denylog
        denylog.record({"principal": who, "action": f"pair.{action}", "resource": "pair:*", "decision": "deny", "result": "denied", "reason": reason})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"pair 거부 기록 실패: {e}")


# --- 역할 프리셋 · grant ----------------------------------------------------------------------


def role_caps(role: str) -> tuple[list, dict]:
    """역할 → (scopes, sconds). navigator = 「협업」, driver = 「운전」 — 브라우저 중개가 아직 없어 `browser` 는 뺀다."""
    if role not in ROLES:
        raise ValueError(role)
    sc, cd = S.preset("collab" if role == NAVIGATOR else "drive")
    sc = [x for x in sc if x != "browser"]
    cd = {k: v for k, v in cd.items() if k != "browser"}
    return sc, cd


def elevated_caps(item: str) -> tuple[str, str, list, dict]:
    """elevated 항목 → (scope, 리소스 종류 `session`|`host`|`browser`, [scope], sconds)."""
    if item == "raw_input":
        return "session", "session", ["session.input"], {"session.input": {"modes": ["line", "raw"]}}
    if item == "session_manage":
        return "session", "session", ["session.manage"], {"session.manage": {"ops": list(S.MANAGE_OPS)}}
    if item == "relay":
        return "host", "host", ["relay"], {"relay": {"groups": list(S.ALL_RELAY_GROUPS)}}
    if item == "browser_js":
        return "browser", "browser", ["browser.js"], {}
    if item == "browser_sensitive":
        return "browser", "browser", ["browser.sensitive"], {"browser.sensitive": {"ops": list(S.SENSITIVE_OPS)}}
    raise ValueError(item)


def elevated_reasons(item: str) -> list:
    """이 항목이 elevated 인 이유(`scopes.requires_elevated` 의 사유). 세션 관리는 일반 규칙에는 없지만 pair 에서는 명시 체크 + 재인증을 요구한다."""
    _k, _r, sc, cd = elevated_caps(item)
    return sorted(S.requires_elevated("agent", sc, "", cd)) or (["session.manage"] if item == "session_manage" else [])


def _res_fs(canon: str) -> str:
    from . import leases
    return "fs:local" + (canon if leases.is_glob(canon) else canon.rstrip("/") + "/*")


def _grant(pair: dict, m: dict, gid: str, resource: str, scopes: list, sconds: Optional[dict], now_: float, **extra) -> dict:
    g = {"id": gid, "origin": f"pair:grant:{pair['id']}", "kind": "pair", "subject": m["principal"], "resource": resource,
         "scopes": list(scopes), "schema": S.SCHEMA, "state": "active", "approved_by": pair["approval"].get("by") or "human",
         "approved_via": S.VIA_MANUAL, "expires": pair.get("hard_expires"), "conds": extra.pop("conds", {}),
         "pair_id": pair["id"], "subject_session": _own_key(m), "created": now_, "updated": now_, **extra}
    if sconds:
        g["sconds"] = sconds
    return g


def _own_key(m: dict) -> Optional[str]:
    return (m.get("ref") or {}).get("local") if m.get("kind") == "agent" else None


def desired_grants(pair: dict, now_: float) -> dict:
    """pair 의 지금 역할·체크한 elevated 에서 나오는 grant 전부 `{id: 레코드}`. id 는 (구성원, 리소스)마다 안정이다 — 역할이 바뀌면
    같은 id 의 scope 가 바뀌거나(운전 ⇄ 협업) 사라진다."""
    out: dict = {}
    raw = _raw(pair["id"])
    ticks = [t for t in (pair["approval"].get("ticked") or []) if t in ELEVATED_ITEMS]
    for m in pair["members"]:
        if not _is_agent_member(m):
            continue                                  # 사람 구성원은 사람 기본 권한이 전부다 — 따로 주지 않는다
        tag = m["seat"][0]
        role_sc, role_cd = role_caps(m["role"])
        driver = m["role"] == DRIVER
        own = _own_key(m)
        sess_sc = [x for x in role_sc if x in ("session.view", "session.read", "session.input")]
        sess_cd = {k: v for k, v in role_cd.items() if k in sess_sc}
        fs_sc = [x for x in role_sc if x in ("fs.read", "fs.write")]
        fs_cd = {k: v for k, v in role_cd.items() if k in fs_sc}
        for i, s in enumerate(pair["bundle"]["sessions"]):
            if s["key"] == own:
                continue
            sc, cd = list(sess_sc), json.loads(json.dumps(sess_cd))
            if driver and "raw_input" in ticks:
                cd["session.input"] = {"modes": ["line", "raw"]}
            if driver and "session_manage" in ticks:
                sc.append("session.manage")
                cd["session.manage"] = {"ops": list(S.MANAGE_OPS)}
            out[f"pp-{raw}-{tag}-s{i}"] = _grant(pair, m, f"pp-{raw}-{tag}-s{i}", f"session:local/{s['key']}", sc, cd, now_,
                                                  resource_session=s["key"])
        for i, d in enumerate(pair["bundle"]["dirs"]):
            out[f"pp-{raw}-{tag}-d{i}"] = _grant(pair, m, f"pp-{raw}-{tag}-d{i}", _res_fs(d), fs_sc, fs_cd, now_)
        other = _other(pair, m)
        if _is_agent_member(other) and _own_key(other):
            out[f"pp-{raw}-{tag}-m"] = _grant(pair, m, f"pp-{raw}-{tag}-m", f"msg:local/{_own_key(other)}", ["msg.send"], None, now_,
                                              resource_session=_own_key(other))
        if driver and "deleg.spawn" in role_sc:
            out[f"pp-{raw}-{tag}-q"] = _grant(pair, m, f"pp-{raw}-{tag}-q", f"deleg-quota:{m['principal']}", ["deleg.spawn"], None, now_,
                                              conds={"deleg_quota": True})
        if driver:
            for item in ("relay", "browser_js", "browser_sensitive"):
                if item in ticks:
                    _k, kind, sc, cd = elevated_caps(item)
                    res = "host:local" if kind == "host" else "browser:local/*"
                    out[f"pp-{raw}-{tag}-x-{item}"] = _grant(pair, m, f"pp-{raw}-{tag}-x-{item}", res, sc, cd, now_)
    return out


def _apply_grants(docs: dict, pair: dict, now_: float) -> None:
    """저장소의 pair grant 를 desired 와 맞춘다 — 없는 것은 더하고, 있는 것은 scope 만 갱신(생성 시각 유지), 더 이상 아닌 것은 지운다."""
    want = desired_grants(pair, now_)
    items = docs["grants"]["items"]
    have = {g["id"]: g for g in items if g.get("pair_id") == pair["id"]}
    for gid, g in want.items():
        cur = have.get(gid)
        if cur is None:
            items.append(g)
        else:
            cur.update(scopes=g["scopes"], updated=now_, expires=g["expires"], state="active")
            if g.get("sconds"):
                cur["sconds"] = g["sconds"]
            else:
                cur.pop("sconds", None)
    gone = set(have) - set(want)
    if gone:
        docs["grants"]["items"] = [g for g in items if g.get("id") not in gone]
    pair["grants"] = sorted(want)


def _sync_leases(docs: dict, pair: dict, now_: float, alive) -> None:
    """묶음의 lease — driver 가 폴더마다 write lease 를 하나 가진다(non-exclusive, 만료 = pair 의 최대 수명). 역할이 바뀌면 새 driver 로 옮긴다."""
    from . import leases
    items = docs["leases"]["items"]
    mine = [l for l in items if l.get("pair_id") == pair["id"]]
    for l in mine:
        leases._audit_lease("revoke", "system:pair", l, result="ended", reason="pair_role_changed")
    if mine:
        ids = {l["id"] for l in mine}
        docs["leases"]["items"] = [l for l in items if l["id"] not in ids]
    pair["leases"] = []
    if not pair["bundle"]["leases"]:
        return
    d = _driver(pair)
    if not _is_agent_member(d):
        return
    raw, tag = _raw(pair["id"]), d["seat"][0]
    for i, glob in enumerate(pair["bundle"]["dirs"]):
        rec = {"id": f"ls-{uuid.uuid4().hex[:10]}", "holder": d["principal"], "holder_key": _own_key(d), "holder_name": d.get("name") or "",
               "resource": "fs:local" + glob, "glob": glob, "mode": "write", "exclusive": False,
               "ttl": int(pair["hard_expires"] - now_), "reason": "", "created": now_, "expires": pair["hard_expires"],
               "max_expires": pair["hard_expires"], "derived_from": None, "deleg": None, "grant_id": f"pp-{raw}-{tag}-d{i}",
               "approved_via": leases.SYSTEM_VIA, "state": "active", "pair_id": pair["id"]}
        leases.insert_in_txn(docs, rec, who="system:pair", alive=alive)
        pair["leases"].append(rec["id"])


# --- 보기 -------------------------------------------------------------------------------------


def _bundle_view(pair: dict) -> dict:
    return {"sessions": [{"name": s.get("name") or ""} for s in pair["bundle"]["sessions"]], "dirs": list(pair["bundle"]["dirs"]),
            "leases": bool(pair["bundle"]["leases"]),
            "elevated": {"requested": list(pair["bundle"]["elevated"]), "granted": list(pair["approval"].get("ticked") or [])}}


def view(pair: dict, p: Optional[P.Principal] = None, now_: Optional[float] = None) -> dict:
    """pair 한 건의 보기. 세션 key·주체 id·코드 값은 내보내지 않는다(이름·역할·상태만)."""
    now_ = now() if now_ is None else now_
    me = _caller_member(pair, p) if p is not None else None
    members = []
    for m in pair["members"]:
        members.append({"seat": m["seat"], "kind": m["kind"], "name": m.get("name") or "", "role": m["role"],
                        "joined": bool(m.get("joined")), "you": m is me, "driver": m["role"] == DRIVER})
    ho = pair.get("handoff")
    out = {"id": pair["id"], "state": pair["state"], "created": pair["created"], "updated": pair.get("updated"),
           "generation": int(pair.get("generation", 0)), "members": members, "bundle": _bundle_view(pair),
           "approval": {"state": pair["approval"].get("state"), "id": pair["approval"].get("id")},
           "role": me["role"] if me else None, "isDriver": bool(me and me["role"] == DRIVER),
           "expires": pair.get("hard_expires"), "endReason": pair.get("end_reason"),
           "handoff": ({"to": ho["to"], "by": ho["by"], "expiresIn": max(0, int(ho["expires"] - now_))} if ho else None)}
    code = pair.get("code")
    if code:
        out["code"] = {"state": code.get("state"), "expiresIn": max(0, int(float(code.get("expires", 0)) - now_))}
    if pair["state"] in (CODE_ISSUED, JOINED) and me is not None and (code or {}).get("phrase"):
        out["phrase"] = code["phrase"]
    if pair["state"] in TERMINAL:
        out["cleanup"] = cleanup_info(pair)
    return out


def cleanup_info(pair: dict) -> dict:
    """끝난 pair 의 정리 현황 — 회수 saga 가 끝났는지와 남은 pair grant·lease 개수(개수만)."""
    try:
        g, gbad = store._read("grants")
        ls, lbad = store._read("leases")
        n_g = sum(1 for x in g["items"] if x.get("pair_id") == pair["id"])
        n_l = sum(1 for x in ls["items"] if x.get("pair_id") == pair["id"])
    except Exception:  # noqa: BLE001
        n_g = n_l = -1
    return {"done": bool(pair.get("saga_done")) and n_g == 0 and n_l == 0, "saga": bool(pair.get("saga_done")), "grants": n_g, "leases": n_l}


def preset_summary(role: str, ticks: Iterable[str] = ()) -> dict:
    sc, cd = role_caps(role)
    from . import inbox
    pr = inbox.preset_of(sc, cd)
    return {"role": role, "preset": pr, "scopes": sorted(sc), "conds": inbox._view_conds(cd)}


def approval_item(pair: dict, now_: Optional[float] = None) -> dict:
    """승인함 항목(kind=`pair`) — 한 화면에 필요한 전부: 두 확인 문구, 말로 쓴 묶음, 역할별 프리셋, 체크해야 하는 elevated 항목."""
    now_ = now() if now_ is None else now_
    ap = pair["approval"]
    j = _member(pair, JOINER)
    i = _member(pair, INITIATOR)
    phrase = (pair.get("code") or {}).get("phrase") or ap.get("phrase") or ""
    el = [{"id": it, "label": elevated_label(it), "reasons": elevated_reasons(it)} for it in pair["bundle"]["elevated"]]
    sessions = [s.get("name") or "" for s in pair["bundle"]["sessions"]]
    words = tr("세션 {sessions} · 폴더 {dirs} · lease {leases}",
               sessions=", ".join(sessions) or tr("없음"), dirs=", ".join(pair["bundle"]["dirs"]) or tr("없음"),
               leases=tr("잡음") if pair["bundle"]["leases"] else tr("잡지 않음"))
    all_scopes = sorted({s for r in ROLES for s in role_caps(r)[0]} | {x for it in pair["bundle"]["elevated"] for x in elevated_caps(it)[2]})
    return {"id": _raw_req(pair), "kind": "pair", "state": ap.get("state") or "pending",
            "subject": {"kind": "agent", "id": None, "name": j.get("name") or ""},
            "resource": {"id": f"pair:{pair['id']}", "label": tr("pair")},
            "scopes": all_scopes, "conds": {}, "preset": None, "elevated": sorted({r for it in pair["bundle"]["elevated"] for r in elevated_reasons(it)}),
            "lineage": None, "created": ap.get("created"), "expires": ap.get("expires"),
            "age": max(0, int(now_ - float(ap.get("created") or now_))), "expiresIn": max(0, int(float(ap.get("expires") or now_) - now_)),
            "pair": {"id": pair["id"], "phrases": {"initiator": phrase, "joiner": phrase},
                     "bundle": {"sessions": sessions, "dirs": list(pair["bundle"]["dirs"]), "leases": bool(pair["bundle"]["leases"]),
                               "summary": words},
                     "roles": {"initiator": {"name": i.get("name") or "", "kind": i["kind"], "role": i["role"]},
                               "joiner": {"name": j.get("name") or "", "kind": j["kind"], "role": j["role"]}},
                     "presets": {"driver": preset_summary(DRIVER), "navigator": preset_summary(NAVIGATOR)},
                     "elevatedItems": el, "needsReauth": bool(el),
                     "note": tr("승인하면 이 묶음 안에서 두 역할(driver·navigator) 중 하나를 각자 갖습니다. 나중에 권한을 넓히려면 새로 승인해야 합니다.")}}


def _raw_req(pair: dict) -> str:
    return "pq-" + _raw(pair["id"])


def request_id_of(pair_id: str) -> str:
    return "pq-" + _raw(pair_id)


def owns_request(request_id: str) -> bool:
    return bool(_REQ_RE.match(str(request_id or "")))


def pair_id_of_request(request_id: str) -> str:
    m = _REQ_RE.match(str(request_id or ""))
    return f"pr-{m.group(1)}" if m else ""


def pending_approvals(now_: Optional[float] = None) -> list:
    """사람의 승인함에 보일 pair 승인 요청들(만든 순). 꺼져 있으면 비어 있다."""
    if not enabled():
        return []
    now_ = now() if now_ is None else now_
    d, _bad = store._read("pairs")
    return [approval_item(x, now_) for x in d["items"]
            if x.get("state") == JOINED and x["approval"].get("state") == "pending" and now_ < float(x["approval"].get("expires") or 0)]


# --- 만들기 -----------------------------------------------------------------------------------


def _need_manage(p: P.Principal) -> None:
    snap = store.snapshot()
    d = engine.decide(p, "pair.manage", "pair:*", {"alive": None}, snap)
    if d.reason == "store_corrupt":
        raise _err("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503)
    if not d.allow:
        raise _err("forbidden", tr("pair 를 만들려면 pair.manage 권한이 필요합니다(request_access 로 사람에게 요청)"), 403,
                   need={"resource": "pair:*", "scopes": ["pair.manage"]}, why=d.reason)


def _supersede(docs: dict, creator_id: str, target: str, keep_id: Optional[str], bundle_hash: str, now_: float) -> None:
    """같은 (시작자, 대상)의 합류 전 pair 중 묶음이 다른 것은 폐기한다(코드도 함께) — 좁은 묶음으로 다시 만들었을 때 넓은 옛 코드가 TTL 까지 사는 틈을 막는다."""
    for x in docs["pairs"]["items"]:
        if x.get("id") == keep_id or (x.get("creator") or {}).get("id") != creator_id:
            continue
        if x.get("state") in (PROPOSED, CODE_ISSUED) and x.get("target") == target and x.get("bundle_hash") != bundle_hash:
            _drop_code(x)
            x.update(state=EXPIRED, ended=now_, end_reason="superseded", updated=now_)
            x["approval"]["state"] = "cancelled"


def _drop_code(pair: dict, state: str = "superseded") -> None:
    c = pair.get("code")
    if c and c.get("state") == "pending":
        c.update(state=state, value=None)
    elif c:
        c["value"] = None


def create(p: P.Principal, body: dict) -> tuple[dict, bool]:
    """pair 를 만든다 → (보기, 새로 만들었는가). 같은 시작자·같은 묶음의 합류 전 pair 가 있으면 그것을 돌려준다(멱등)."""
    require_enabled()
    if p.kind == P.AGENT and not P.is_hook(p):
        _need_manage(p)
    elif p.kind != P.HUMAN:
        raise _err("request_not_allowed", tr("pair 를 만들 수 없는 호출자입니다"), 403)
    role = str(body.get("role") or DRIVER)
    if role not in ROLES:
        raise _err("bad_role", tr("role 은 driver 또는 navigator 입니다"), 400)
    rows = _pane_rows()
    bundle = _parse_bundle(body, rows)
    with_key = None
    if body.get("with") not in (None, ""):
        with_key = _resolve_ref(body["with"], rows)["key"]
    me_ref = None
    if p.kind == P.AGENT:
        key = p.attrs.get("key")
        if not key:
            raise _err("identity_unverified", tr("호출자를 확인하지 못했습니다"), 403)
        me_ref = {"local": key}
    bh = _bundle_hash(bundle, role, with_key)
    target = with_key or "*"
    t = now()
    with store.txn() as docs:
        mine = [x for x in docs["pairs"]["items"] if (x.get("creator") or {}).get("id") == p.id and x.get("state") in LIVE_STATES]
        for x in mine:
            if x["state"] in (PROPOSED, CODE_ISSUED) and x.get("bundle_hash") == bh and x.get("target") == target \
                    and t - float(x.get("touched") or 0) < PROPOSAL_TTL:
                return view(x, p, t), False
        _supersede(docs, p.id, target, None, bh, t)
        live = [x for x in docs["pairs"]["items"] if (x.get("creator") or {}).get("id") == p.id and x.get("state") in LIVE_STATES]
        if len(live) >= MAX_LIVE_PER_CREATOR:
            raise _err("too_many_pairs", tr("진행 중인 pair 가 너무 많습니다 — 하나를 끝낸 뒤 다시 하세요"), 429)
        other = NAVIGATOR if role == DRIVER else DRIVER
        init = {"seat": INITIATOR, "kind": "agent" if p.kind == P.AGENT else "human", "ref": me_ref,
                "principal": p.id if p.kind == P.AGENT else None, "name": (p.attrs.get("name") or "") if p.kind == P.AGENT else "",
                "role": role, "joined": t}
        join = {"seat": JOINER, "kind": "agent", "ref": None, "principal": None, "name": "", "role": other, "joined": None}
        pair = {"id": _new_id(), "state": PROPOSED, "created": t, "updated": t, "touched": t,
                "creator": {"id": p.id, "kind": init["kind"]}, "members": [init, join], "with": ({"local": with_key} if with_key else None),
                "target": target, "bundle": bundle, "bundle_hash": bh, "code": None,
                "approval": {"id": None, "state": "none"}, "generation": 0, "handoff": None, "activated": None,
                "hard_expires": None, "ended": None, "end_reason": None, "grants": [], "leases": [], "saga": None}
        _audit("create", p.id, pair, reason=f"role={role}")
        docs["pairs"]["items"].append(pair)
        return view(pair, p, t), True


# --- 코드 발급 --------------------------------------------------------------------------------


def _expire_code(pair: dict, t: float) -> None:
    c = pair.get("code")
    if c and c.get("state") == "pending" and t >= float(c.get("expires", 0)):
        c.update(state="expired", value=None)


def issue_code(p: P.Principal, pid: str) -> dict:
    """코드를 발급한다 → `{code, phrase, expires, ttl, reused}`. 같은 pair 에 대기 중인(만료 전) 코드가 있으면 그대로 다시 준다(횟수에 안 센다)."""
    require_enabled()
    t = now()
    with store.txn() as docs:
        pair = _find(docs, pid)
        if pair is None or not _creator_ok(pair, p):
            raise _err("not_found", tr("그런 pair 가 없습니다"), 404)
        _tick_one(docs, pair, t)
        if pair["state"] not in (PROPOSED, CODE_ISSUED):
            raise _err("bad_state", tr("이 pair 는 코드를 발급할 수 없는 상태입니다"), 409, state=pair["state"])
        _supersede(docs, pair["creator"]["id"], pair.get("target") or "*", pair["id"], pair["bundle_hash"], t)
        c = pair.get("code")
        if c and c.get("state") == "pending":
            pair["touched"] = t
            return {"code": display_code(c["value"]), "phrase": c["phrase"], "expires": c["expires"], "ttl": CODE_TTL,
                    "reused": True, "pair": pair["id"]}
        if not _RATE.hit(p.id if p.kind != P.HUMAN else "human", t):
            _deny_log(p.id, "code", "rate_limited")
            raise _err("rate_limited", tr("코드 발급이 너무 잦습니다 — 시작자당 시간당 {n}번까지", n=limits.LIMITS["pairing.code"][0]), 429,
                       retryAfter=int(limits.LIMITS["pairing.code"][1]))
        code = new_code()
        pair["code"] = {"value": code, "hash": _hash(code), "phrase": new_phrase(), "issued": t, "expires": t + CODE_TTL,
                        "attempts": 0, "state": "pending"}
        pair["state"], pair["updated"], pair["touched"] = CODE_ISSUED, t, t
        _audit("code", p.id, pair, reason="issued")
        return {"code": display_code(code), "phrase": pair["code"]["phrase"], "expires": pair["code"]["expires"], "ttl": CODE_TTL,
                "reused": False, "pair": pair["id"]}


# --- 합류 -------------------------------------------------------------------------------------


def join(p: P.Principal, raw_code) -> dict:
    """확인된 에이전트가 코드로 합류한다 → 보기(+확인 문구). 승인 요청이 생긴다. 틀린 코드는 404 `invalid_code`."""
    require_enabled()
    if p.kind != P.AGENT or P.is_hook(p) or not p.attrs.get("key"):
        raise _err("request_not_allowed", tr("확인된 에이전트 세션만 합류할 수 있습니다"), 403)
    code = normalize_code(raw_code)
    t = now()
    miss = False
    with store.txn() as docs:
        pending = []
        for x in docs["pairs"]["items"]:
            _expire_code(x, t)
            if x.get("state") == CODE_ISSUED and (x.get("code") or {}).get("state") == "pending":
                pending.append(x)
        hit = None
        h = _hash(code) if code else ""
        for x in pending:                              # 전부 비교한다(어느 것이 맞았는지가 시간으로 새지 않게)
            if code and hmac.compare_digest(x["code"]["hash"], h) and hit is None:
                hit = x
        if hit is None:
            burned = 0
            for x in pending:
                c = x["code"]
                c["attempts"] = int(c.get("attempts", 0)) + 1
                if c["attempts"] >= MAX_ATTEMPTS:
                    c.update(state="burned", value=None)
                    x["updated"] = t
                    burned += 1
            miss = True
            result = ("burned", burned)
        else:
            pair = hit
            key = p.attrs["key"]
            ini = _member(pair, INITIATOR)
            if pair.get("with") and pair["with"]["local"] != key:
                raise _err("not_intended", tr("이 코드는 다른 세션을 위한 것입니다"), 403)
            if ini.get("ref") and ini["ref"].get("local") == key:
                raise _err("self_join", tr("자기가 만든 pair 에는 합류할 수 없습니다"), 400)
            j = _member(pair, JOINER)
            j.update(ref={"local": key}, principal=p.id, name=p.attrs.get("name") or "", joined=t)
            pair["code"].update(state="redeemed", value=None)
            pair["approval"] = {"id": request_id_of(pair["id"]), "state": "pending", "created": t, "expires": t + APPROVAL_TTL,
                                "phrase_misses": 0, "ticked": [], "phrase": pair["code"]["phrase"]}
            pair["state"], pair["updated"], pair["touched"] = JOINED, t, t
            _audit("join", p.id, pair, reason="redeemed")
            out = view(pair, p, t)
            out["phrase"] = pair["code"]["phrase"]
            out["pendingApproval"] = True
            return out
    # 틀린 코드 — 트랜잭션은 시도 횟수를 저장하고 끝났다. 거부 기록은 체인이 아닌 별도 파일에.
    _deny_log(p.id, "join", "invalid_code" if code else "bad_code_format")
    raise _err("invalid_code", tr("코드가 맞지 않거나 만료됐습니다"), 404)


# --- 조회 -------------------------------------------------------------------------------------


def status(p: P.Principal, pid: str) -> dict:
    """합류자·시작자가 자기 검증된 신원으로 결과를 읽는다. 참여자(또는 사람)가 아니면 404."""
    require_enabled()
    t = now()
    with store.txn() as docs:
        pair = _find(docs, pid)
        if pair is None or (_caller_member(pair, p) is None and p.kind != P.HUMAN):
            raise _err("not_found", tr("그런 pair 가 없습니다"), 404)
        _tick_one(docs, pair, t)
        if _caller_member(pair, p) is not None and pair["state"] in RUNNING_STATES:
            pair["touched"] = t
        out = view(pair, p, t)
        return out


def list_for(p: P.Principal, *, everyone: bool) -> list:
    require_enabled()
    t = now()
    d, _bad = store._read("pairs")
    rows = []
    for x in d["items"]:
        if x.get("state") in TERMINAL and t - float(x.get("ended") or 0) > 3600:
            continue
        if everyone or _caller_member(x, p) is not None:
            rows.append(x)
    rows.sort(key=lambda x: float(x.get("created") or 0))
    return [view(x, p, t) for x in rows]


def active_count() -> int:
    """`fsh doctor` — 효력이 있는 pair 수(꺼져 있어도 저장된 것을 센다)."""
    d, _bad = store._read("pairs")
    return sum(1 for x in d["items"] if x.get("state") in RUNNING_STATES)


# --- 승인 -------------------------------------------------------------------------------------


def decide(request_id: str, decision: str, actor: Optional[P.Principal], *, elevated=None, phrase=None, alive="probe") -> dict:
    """사람의 결정 `allow|deny` → `{ok, pair, state, replayed?}`. 선착순·멱등(같은 결정의 재전송은 처음 결과, 다른 결정은 409).
    `elevated`: 체크한 항목(요청된 것의 부분집합), 있으면 재인증한 사람만. `phrase`: 사람이 입력한 확인 문구(주면 맞아야 한다 — 3번 틀리면 거절)."""
    from . import approvals
    require_enabled()
    pid = pair_id_of_request(request_id)
    if not pid:
        raise _err("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)
    if decision not in ("allow", "deny"):
        raise _err("bad_decision", tr("decision 은 allow 또는 deny 입니다"), 400)
    ticks = list(elevated or [])
    if not isinstance(ticks, list) or any(not isinstance(x, str) for x in ticks):
        raise _err("bad_elevated", tr("elevated 는 항목 이름의 목록이어야 합니다"), 400)
    t = now()
    if alive == "probe":
        alive = _alive_keys()
    late_error: Optional[PairError] = None
    with store.txn() as docs:
        pair = _find(docs, pid)
        if pair is None or pair["approval"].get("id") != request_id_norm(request_id):
            raise _err("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)
        approvals.check_authority(actor, (), "session:local/-", None, approvals._snap(docs, t))     # 사람의 서명된 세션만
        ap = pair["approval"]
        if ap.get("state") != "pending":
            prior = ap.get("state")
            same = (decision == "allow" and prior == "allowed") or (decision == "deny" and prior == "denied")
            if not same:
                raise _err("already_decided", tr("이미 처리된 요청입니다 — 먼저 온 결정이 적용됐습니다"), 409,
                           decision="allow" if prior == "allowed" else "deny")
            return {"ok": True, "pair": view(pair, actor, t), "state": prior, "replayed": True}
        if pair["state"] != JOINED or t >= float(ap.get("expires") or 0):
            raise _err("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)
        who = (actor or P.human("-")).id
        if decision == "deny":
            ap.update(state="denied", decided=t)
            _terminate(docs, pair, ENDED, "denied", who, t)
            return {"ok": True, "pair": view(pair, actor, t), "state": "denied"}
        bad = [x for x in ticks if x not in ELEVATED_ITEMS]
        if bad:
            raise _err("bad_elevated", tr("알 수 없는 elevated 항목입니다"), 400)
        if any(x not in pair["bundle"]["elevated"] for x in ticks):
            raise _err("wider_than_requested", tr("요청하지 않은 elevated 항목은 줄 수 없습니다"), 400)
        if ticks and not (actor is not None and actor.elevated):
            raise _err("elevation_required", tr("이 권한은 재인증(elevated)한 사람만 승인할 수 있습니다"), 403,
                       items=sorted(ticks), scopes=sorted({r for x in ticks for r in elevated_reasons(x)}))
        if phrase is not None and normalize_phrase(phrase) != normalize_phrase(ap.get("phrase")):
            ap["phrase_misses"] = int(ap.get("phrase_misses", 0)) + 1
            _deny_log(who, "decide", "phrase_mismatch")
            if ap["phrase_misses"] >= PHRASE_MISS_LIMIT:
                ap.update(state="denied", decided=t)
                _terminate(docs, pair, ENDED, "phrase_mismatch", who, t)
                late_error = _err("phrase_mismatch_locked", tr("확인 문구가 계속 맞지 않아 요청을 거절했습니다"), 409)
            else:
                late_error = _err("phrase_mismatch", tr("확인 문구가 맞지 않습니다 — 두 에이전트가 보고 있는 문구와 같은지 확인하세요"), 400,
                                  remaining=PHRASE_MISS_LIMIT - ap["phrase_misses"])
        else:
            for m in pair["members"]:
                k = _own_key(m)
                if k and alive is not None and k not in alive:
                    ap.update(state="denied", decided=t)
                    _terminate(docs, pair, ENDED, "member_gone", who, t)
                    late_error = _err("member_gone", tr("참여한 세션이 사라져 요청을 닫았습니다"), 409)
                    break
            if late_error is None:
                ap.update(state="allowed", decided=t, by=who, ticked=sorted(set(ticks)))
                pair.update(state=ACTIVE, activated=t, hard_expires=t + MAX_LIFETIME, updated=t, touched=t)
                _apply_grants(docs, pair, t)
                _sync_leases(docs, pair, t, alive)
                _audit("approve", who, pair, scopes=sorted({s for g in docs["grants"]["items"] if g.get("pair_id") == pair["id"]
                                                          for s in g.get("scopes", ())}),
                       reason=f"elevated={len(ap['ticked'])}")
                return {"ok": True, "pair": view(pair, actor, t), "state": "allowed"}
    if late_error is not None:
        _post_terminate(pid, actor)
        raise late_error
    raise _err("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)    # pragma: no cover


def request_id_norm(request_id: str) -> str:
    return "pq-" + _raw(pair_id_of_request(request_id))


# --- handoff ----------------------------------------------------------------------------------


def _swap(docs: dict, pair: dict, t: float, who: str, reason: str, alive) -> None:
    """역할을 맞바꾸고 grant·lease 를 새 역할로 옮기고 세대를 올린다. 호출자가 저장소 락(txn)을 쥐고 있다."""
    a, b = pair["members"]
    a["role"], b["role"] = b["role"], a["role"]
    _driver(pair)                                    # 불변식 확인
    pair["generation"] = int(pair.get("generation", 0)) + 1
    pair["handoff"] = None
    pair.update(state=ACTIVE, updated=t, touched=t)
    _apply_grants(docs, pair, t)
    _sync_leases(docs, pair, t, alive)
    _audit("handoff", who, pair, reason=reason)


def handoff_offer(p: P.Principal, pid: str) -> dict:
    """driver 가 넘기겠다고 제안한다(navigator 가 수락해야 바뀐다). driver 가 아닌 사람(navigator 사람 구성원이나 지켜보는 사람)이 부르면
    수락 없이 곧바로 바뀐다(override)."""
    require_enabled()
    t = now()
    alive = _alive_keys() if p.kind == P.HUMAN else None
    with store.txn() as docs:
        pair = _find(docs, pid)
        me = _caller_member(pair, p) if pair else None
        if pair is None or (me is None and p.kind != P.HUMAN):
            raise _err("not_found", tr("그런 pair 가 없습니다"), 404)
        _tick_one(docs, pair, t)
        drv = _driver(pair) if pair["state"] in RUNNING_STATES else None
        if p.kind == P.HUMAN and pair["state"] in RUNNING_STATES and me is not drv:
            _swap(docs, pair, t, p.id, "human_override", alive)       # 운전대를 쥐지 않은 사람은 언제나 가져갈 수 있다(수락 없이)
            return view(pair, p, t)
        if pair["state"] == HANDOFF_PENDING and pair.get("handoff") and me is _member(pair, pair["handoff"]["by"]):
            return view(pair, p, t)                  # 같은 제안의 재요청 — 그대로
        if pair["state"] != ACTIVE:
            raise _err("bad_state", tr("활성 상태의 pair 만 handoff 할 수 있습니다"), 409, state=pair["state"])
        if me is not drv:
            raise _err("not_driver", tr("driver 만 handoff 를 제안할 수 있습니다"), 403)
        other = _other(pair, drv)
        pair["handoff"] = {"by": drv["seat"], "to": other["seat"], "expires": t + OFFER_TTL, "generation": int(pair.get("generation", 0))}
        pair.update(state=HANDOFF_PENDING, updated=t, touched=t)
        _audit("handoff_offer", p.id, pair)
        return view(pair, p, t)


def handoff_accept(p: P.Principal, pid: str) -> dict:
    """navigator 가 수락한다 → 저장소 락 아래에서 원자적으로 교체, 세대 +1. 제안이 없거나 만료됐으면 409."""
    require_enabled()
    t = now()
    alive = _alive_keys()
    with store.txn() as docs:
        pair = _find(docs, pid)
        me = _caller_member(pair, p) if pair else None
        if pair is None or me is None:
            raise _err("not_found", tr("그런 pair 가 없습니다"), 404)
        _tick_one(docs, pair, t)
        ho = pair.get("handoff")
        if pair["state"] != HANDOFF_PENDING or not ho:
            raise _err("no_offer", tr("수락할 handoff 제안이 없습니다(없거나 시간이 지났습니다)"), 409)
        if _member(pair, ho["to"]) is not me:
            raise _err("not_navigator", tr("제안을 받은 쪽(navigator)만 수락할 수 있습니다"), 403)
        if int(ho.get("generation", -1)) == int(pair.get("generation", 0)):
            _swap(docs, pair, t, p.id, "accepted", alive)
            return view(pair, p, t)
        pair.update(state=ACTIVE, handoff=None, updated=t)             # 낡은 제안은 거두고(저장하고) 거절한다
    raise _err("stale_offer", tr("제안이 낡았습니다 — 다시 제안하세요"), 409)


# --- 끝내기 · 회수 ----------------------------------------------------------------------------


def _terminate(docs: dict, pair: dict, state: str, reason: str, who: str, t: float) -> None:
    """pair 를 끝낸다(저장소 락 안). grant 는 곧바로 `revoking` — 엔진이 이 순간부터 거부한다 — 이고 lease 는 놓는다. 나머지 정리는 saga 가 한다."""
    if pair["state"] in TERMINAL:
        return
    from . import leases
    _drop_code(pair, "cancelled")
    pair["handoff"] = None
    pair.update(state=state, ended=t, end_reason=reason, ended_by=who, updated=t)
    for g in docs["grants"]["items"]:
        if g.get("pair_id") == pair["id"] and g.get("state", "active") == "active":
            g.update(state="revoking", revoking_at=t)
    mine = [l for l in docs["leases"]["items"] if l.get("pair_id") == pair["id"]]
    for l in mine:
        leases._audit_lease("revoke", "system:pair", l, result="ended", reason=f"pair_{reason}")
    if mine:
        ids = {l["id"] for l in mine}
        docs["leases"]["items"] = [l for l in docs["leases"]["items"] if l["id"] not in ids]
    if pair["approval"].get("state") == "pending":
        pair["approval"]["state"] = "cancelled"
    _audit("end", who, pair, result="ended", reason=reason)


def saga_target(pair: dict) -> dict:
    return {"pair": pair["id"], "grant_ids": list(pair.get("grants") or ()),
            "members": [{"key": _own_key(m), "name": m.get("name") or ""} for m in pair["members"] if _is_agent_member(m)],
            "since": pair.get("activated")}


def _post_terminate(pid: str, actor: Optional[P.Principal]) -> Optional[dict]:
    """끝난 pair 의 회수 saga 를 돌린다(락 밖). 실패해도 끝내기 응답은 막지 않는다 — 남은 것은 sweep 이 이어한다."""
    from . import saga
    d, _bad = store._read("pairs")
    pair = next((x for x in d["items"] if x.get("id") == pid), None)
    if pair is None or pair["state"] not in TERMINAL:
        return None
    try:
        rec = saga.start("pair", saga_target(pair), actor=actor)
    except saga.InjectedCrash:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pair 회수 saga 시작 실패(sweep 이 이어한다): {type(e).__name__}: {e}")
        return None
    try:
        with store.txn() as docs:
            x = _find(docs, pid)
            if x is not None and x.get("saga") != rec["id"]:
                x["saga"] = rec["id"]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"pair saga id 기록 실패: {e}")
    return rec


def end(p: P.Principal, pid: str, *, reason: Optional[str] = None) -> dict:
    """pair 를 끝낸다. 구성원은 `ended`, 구성원이 아닌 사람은 `revoked`(사람의 회수). 이미 끝난 pair 는 정리가 끝났는지만 다시 본다(멱등)."""
    require_enabled()
    t = now()
    with store.txn() as docs:
        pair = _find(docs, pid)
        me = _caller_member(pair, p) if pair else None
        if pair is None or (me is None and p.kind != P.HUMAN):
            raise _err("not_found", tr("그런 pair 가 없습니다"), 404)
        already = pair["state"] in TERMINAL
        if not already:
            state = ENDED if me is not None else REVOKED
            _terminate(docs, pair, state, reason or ("ended" if me is not None else "revoked_by_human"), p.id, t)
        out = view(pair, p, t)
    _post_terminate(pid, p)
    d, _bad = store._read("pairs")
    cur = next((x for x in d["items"] if x.get("id") == pid), None)
    out["cleanup"] = cleanup_info(cur) if cur is not None else {"done": True, "saga": True, "grants": 0, "leases": 0}
    out["already"] = already
    return out


def revoke_by_grant(grant_id: str, actor: Optional[P.Principal]) -> dict:
    """권한 화면에서 pair grant 하나를 회수하면 pair 전체를 끝낸다(`revoked`) — grant 한 조각만 빼면 역할 불변식이 깨진다."""
    d, _bad = store._read("pairs")
    pair = next((x for x in d["items"] if grant_id in (x.get("grants") or ())), None)
    if pair is None:
        raise _err("not_found", tr("그런 권한이 없습니다"), 404)
    t = now()
    with store.txn() as docs:
        x = _find(docs, pair["id"])
        _terminate(docs, x, REVOKED, "revoked_by_human", (actor or P.human("-")).id, t)
    _post_terminate(pair["id"], actor)
    return {"id": grant_id, "pair": pair["id"]}


def end_all(actor: Optional[P.Principal], reason: str = "panic") -> int:
    """비상 정지 — 살아 있는 pair 를 전부 끝낸다."""
    d, _bad = store._read("pairs")
    ids = [x["id"] for x in d["items"] if x.get("state") in LIVE_STATES]
    if not ids:
        return 0
    t = now()
    with store.txn() as docs:
        for x in docs["pairs"]["items"]:
            if x["id"] in ids:
                _terminate(docs, x, REVOKED, reason, (actor or P.human("-")).id, t)
    for pid in ids:
        _post_terminate(pid, actor)
    return len(ids)


# --- 점검(sweep) ------------------------------------------------------------------------------


def _tick_one(docs: dict, pair: dict, t: float) -> None:
    """읽거나 부를 때 함께 하는 가벼운 시간 정리 — 만료된 제안(원상복구)·코드. (세션 소멸·유휴는 sweep 이 본다.)"""
    _expire_code(pair, t)
    ho = pair.get("handoff")
    if pair["state"] == HANDOFF_PENDING and ho and t >= float(ho.get("expires", 0)):
        pair.update(state=ACTIVE, handoff=None, updated=t)


def sweep(t: Optional[float] = None) -> list:
    """시간이 지나 끝나야 하는 pair 를 끝낸다 → 끝낸 pair id 들. 이어서 끝나지 않은 정리 saga 는 `saga.sweep` 이 맡는다.
    구성원 세션 소멸·유휴는 tmux 를 읽을 수 있을 때만 본다(못 읽으면 건드리지 않는다)."""
    t = now() if t is None else t
    d, bad = store._read("pairs")
    if bad or not any(x.get("state") in LIVE_STATES or x.get("state") in TERMINAL for x in d["items"]):
        return []
    alive = _alive_keys()
    act = _activity() if any(x.get("state") in RUNNING_STATES for x in d["items"]) else None
    idle = idle_seconds()
    ended: list = []
    with store.txn() as docs:
        for pair in docs["pairs"]["items"]:
            if pair["state"] in TERMINAL:
                continue
            _tick_one(docs, pair, t)
            st = pair["state"]
            why = None
            if st in (PROPOSED, CODE_ISSUED) and t - float(pair.get("touched") or pair["created"]) >= PROPOSAL_TTL:
                why = ("expired", EXPIRED, "proposal_expired")
            elif st == JOINED and t >= float(pair["approval"].get("expires") or 0):
                why = ("expired", EXPIRED, "approval_expired")
            elif st in RUNNING_STATES:
                if pair.get("hard_expires") and t >= float(pair["hard_expires"]):
                    why = ("expired", EXPIRED, "max_lifetime")
            if why is None and alive is not None and st in (JOINED, ACTIVE, HANDOFF_PENDING, CODE_ISSUED, PROPOSED):
                gone = [m for m in pair["members"] if _own_key(m) and _own_key(m) not in alive]
                if gone:
                    why = ("ended", ENDED, "member_gone")
            if why is None and st in RUNNING_STATES and act is not None:
                keys = [_own_key(m) for m in pair["members"] if _own_key(m)]
                last = max([float(pair.get("touched") or 0)] + [act[k] for k in keys if k in act])
                if t - last >= idle:
                    why = ("ended", ENDED, "idle")
            if why:
                _terminate(docs, pair, why[1], why[2], "system:sweep", t)
                ended.append(pair["id"])
        # 오래된 끝난 pair 정리 — 최근 것 KEEP_TERMINAL 개는 남긴다
        term = sorted((x for x in docs["pairs"]["items"] if x["state"] in TERMINAL), key=lambda x: float(x.get("ended") or 0))
        old = {x["id"] for x in term[:-KEEP_TERMINAL]} | {x["id"] for x in term if t - float(x.get("ended") or 0) > TERMINAL_RETENTION
                                                         and x.get("saga_done")}
        if old:
            docs["pairs"]["items"] = [x for x in docs["pairs"]["items"] if x["id"] not in old]
    for pid in ended:
        _post_terminate(pid, None)
    return ended


async def sweep_loop(interval: float = 30.0) -> None:
    import asyncio
    while True:
        await asyncio.sleep(interval)
        if not enabled():
            continue
        try:
            await asyncio.to_thread(sweep)
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시
            logger.warning(f"pair 점검 실패: {type(e).__name__}: {e}")


# --- 관계(메시지 · lease) ---------------------------------------------------------------------


def _running_pairs() -> list:
    if not enabled():
        return []
    try:
        d, bad = store._read("pairs")
    except Exception:  # noqa: BLE001
        return []
    return [] if bad else [x for x in d["items"] if x.get("state") in RUNNING_STATES]


def related_keys(a_key: str, b_key: str) -> bool:
    """활성 pair 의 두 구성원 세션인가 — `msg_svc.pair_related`. 꺼져 있으면 항상 False."""
    if not a_key or not b_key or a_key == b_key:
        return False
    for x in _running_pairs():
        keys = {_own_key(m) for m in x["members"] if _is_agent_member(m)}
        if a_key in keys and b_key in keys:
            return True
    return False


def same_pair_holders(holder_a: str, holder_b: str) -> bool:
    """두 lease 보유자(주체 id)가 같은 활성 pair 의 구성원인가 — `leases.same_pair`."""
    if not holder_a or not holder_b or holder_a == holder_b:
        return False
    for x in _running_pairs():
        ps = {m.get("principal") for m in x["members"] if _is_agent_member(m)}
        if holder_a in ps and holder_b in ps:
            return True
    return False


# --- 회수 saga 단계 ---------------------------------------------------------------------------
# saga.py 의 단계 표가 이 함수들을 부른다. 모두 멱등이고 `t["pair"]` 가 없으면 아무것도 하지 않는다.


def _pid(t: dict) -> str:
    return str(t.get("pair") or "")


def saga_grants(t: dict) -> None:
    pid = _pid(t)
    if not pid:
        return
    now_ = time.time()
    with store.txn() as docs:
        for g in docs["grants"]["items"]:
            if g.get("pair_id") == pid and g.get("state", "active") == "active":
                g.update(state="revoking", revoking_at=now_)


def saga_leases(t: dict) -> None:
    pid = _pid(t)
    if not pid:
        return
    from . import leases
    leases.release_where(lambda l: l.get("pair_id") == pid, who="system:saga", reason="pair_ended")


def _member_names(t: dict) -> tuple[set, set]:
    names = {m.get("name") for m in t.get("members") or () if m.get("name")}
    keys = {m.get("key") for m in t.get("members") or () if m.get("key")}
    return names, keys


def _between_members(m: dict, names: set) -> bool:
    return (m.get("dir") == "in" and (m.get("from") or {}).get("host") == "local" and (m.get("from") or {}).get("session") in names
            and (m.get("to") or {}).get("session") in names)


def saga_messages(t: dict) -> None:
    """구성원끼리 오간 아직 전달되지 않은 메시지를 취소하고 그 큐 항목을 지운다."""
    if not _pid(t):
        return
    import agent_msg
    import queue_store
    names, _keys = _member_names(t)
    if len(names) < 2:
        return
    mids = {m["id"] for m in agent_msg.list_messages(limit=agent_msg.MAX_KEEP)
            if m.get("status") == agent_msg.STATUS_QUEUED and _between_members(m, names)}
    if mids:
        queue_store.remove_pending_by_source(lambda s: s.get("agentMsg") in mids or s.get("pair_id") == _pid(t))
        for mid in mids:
            agent_msg.set_status(mid, agent_msg.STATUS_FAILED, "revoked")


def _pair_delegations(t: dict) -> list:
    """pair 가 효력을 갖는 동안 구성원이 부모로 만든, 아직 열린 위임."""
    import delegation
    keys = _member_names(t)[1]
    since = t.get("since")
    if not keys or not since:
        return []
    open_states = tuple(delegation.OPEN_STATES) + ("starting",)
    try:
        items = delegation.list_all()
    except Exception as e:  # noqa: BLE001
        from .saga import SagaIncomplete
        raise SagaIncomplete(f"위임 장부를 읽지 못함: {type(e).__name__}") from e
    return [x["id"] for x in items if x.get("state") in open_states and ((x.get("parent") or {}).get("key") in keys)
            and float(x.get("created_at") or 0) >= float(since)]


def saga_delegations(t: dict) -> None:
    if not _pid(t):
        return
    import delegation
    for did in _pair_delegations(t):
        try:
            delegation.close(did, reason="revoked", system=True)
        except delegation.DelegationError as e:
            if e.status != 404:
                raise


def saga_verify(t: dict) -> None:
    """남은 것이 있으면 SagaIncomplete — pair grant 가 아직 active, pair lease, 구성원 간 대기 메시지, 열린 pair 위임."""
    pid = _pid(t)
    if not pid:
        return
    from . import leases
    from .saga import SagaIncomplete
    left: dict = {}
    d, bad = store._read("grants")
    if bad:
        raise SagaIncomplete("grants 문서를 읽지 못함")
    n = sum(1 for g in d["items"] if g.get("pair_id") == pid and g.get("state", "active") == "active")
    if n:
        left["grants_active"] = n
    items, lbad = leases.read_items()
    if lbad:
        raise SagaIncomplete("leases 문서를 읽지 못함")
    n = sum(1 for l in items if l.get("pair_id") == pid)
    if n:
        left["leases"] = n
    import agent_msg
    names, _k = _member_names(t)
    if len(names) >= 2:
        n = sum(1 for m in agent_msg.list_messages(limit=agent_msg.MAX_KEEP)
                if m.get("status") == agent_msg.STATUS_QUEUED and _between_members(m, names))
        if n:
            left["messages"] = n
    n = len(_pair_delegations(t))
    if n:
        left["delegations"] = n
    if left:
        raise SagaIncomplete(", ".join(f"{k}={v}" for k, v in sorted(left.items())))


def saga_finalize(t: dict) -> None:
    """모든 정리가 끝났다 — 회수된 pair grant 레코드를 지우고 pair 에 정리 완료를 적는다."""
    pid = _pid(t)
    if not pid:
        return
    now_ = time.time()
    with store.txn() as docs:
        docs["grants"]["items"] = [g for g in docs["grants"]["items"] if not (g.get("pair_id") == pid and g.get("state") == "revoked")]
        x = _find(docs, pid)
        if x is not None and not x.get("saga_done"):
            x["saga_done"] = now_
