"""다른 맥(peer)의 권한 판정 — `VT_AUTHZ_ENFORCE=peer` 일 때 엔진이 판정권자다.

꺼짐에서는 이 모듈을 거치지 않는다(옛 동작 그대로). 켜져도 **결과는 같다**: 등급(view/control)·`agentMessages` 가 만들던 허용·거부가
라우트마다 그대로이고(parity 행렬 시험), 응답 본문도 같다.

## 1. 받는 쪽 (다른 맥 -> 이 맥, `/api/peer/*`)
HMAC 서명은 `peer:<id>` **인증**만 한다(`routes/peer_protocol.py`, 본문 해시·nonce·속도 제한은 그대로). 인가는 grant 다: 판정에 쓰는 grant 는
`hosts.json` 의 등급·`agentMessages` 를 **그 자리에서 번역**한 것이다(`migrate.translate_peer_grant`) — `hosts.json` 이 아직 소유
저장소라 저장소 사본이 낡아 결과가 갈라질 수 없다(`msg_svc` 와 같은 방식). 저장소의 비-legacy 규칙(deny 포함)은 함께 본다.

라우트마다 필요한 (scope, 조건)은 아래 `NEEDS` **한 표**에 적는다. 데코레이터(`peer_protocol.capability`)가 이 표를 읽고, 표에 없는 라우트는
시험이 실패한다(`test_peer_enforce.test_every_peer_route_is_declared`). 표에 없는 동작 이름은 옛 등급으로 내려가 판정한다.

  보기(view)   session.view · session.read(검색)            제어(control)  + session.input(raw) · session.manage(ops) ·
  fs.write(ops=drop) · relay(전 그룹) · (agentMessages) msg.send. 제어는 사실상 **원격 셸**이다(공개 문서에 말로 적는다).

## 2. 연결 중 재확인 (`peer_ws`, relay 소켓)
`GrantWatch` — 입력 프레임마다 `session.input`(raw)을 다시 묻고(hosts.json·grants.json 이 안 바뀌었으면 캐시), 2초마다 grant 를 읽어 지워졌거나
보기 권한을 잃었거나 입력 권한을 잃었으면 소켓을 닫는다(회수 후 5초 안). 옛 동작은 relay 소켓의 30초 점검뿐이었다.

호스트 조작(H13 -> `host_gate.py`)과 `fsh host allow-*` 승인(`host_grant_svc.py`)은 같은 플래그를 쓰는 별도 모듈이다.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import audit, engine, flags, migrate, store
from . import principals as P
from . import scopes as S

logger = logging.getLogger(__name__)

HOST_LOCAL = "host:local"
WATCH_INTERVAL = 2.0          # 연결 중 grant 재확인 주기(초) — 회수 후 5초 안에 끊는다
LEGACY_WATCH_INTERVAL = 30.0  # 꺼짐일 때 relay 소켓 점검 주기(옛 값)


def enforced() -> bool:
    """`VT_AUTHZ_ENFORCE` 에 `peer` 가 있는가."""
    return flags.enforced("peer")


# --- 1. 라우트가 필요로 하는 (scope, 조건) — 단일 표 ---------------------------------------------


@dataclass(frozen=True)
class Need:
    scope: str
    ctx: dict = field(default_factory=dict)
    resource: str = HOST_LOCAL
    obo: Optional[str] = None            # 대리 주체 템플릿(`{id}` = peer id)


_RAW = {"mode": "raw"}
NEEDS: dict[str, Need] = {
    # 보기(view)
    "sessions": Need("session.view"),
    "clients": Need("session.view"),
    "search": Need("session.read"),
    "ping": Need("ping"),
    "ws": Need("session.view"),                                           # 출력 스트림 연결
    # 제어(control)
    "input": Need("session.input", _RAW),
    "clients/detach": Need("session.manage", {"op": "detach"}),
    "clients/solo": Need("session.manage", {"op": "detach"}),
    "sessions/create": Need("session.manage", {"op": "create"}),
    "sessions/kill": Need("session.manage", {"op": "kill"}),
    "sessions/rename": Need("session.manage", {"op": "rename"}),
    "agent/message": Need("session.input", _RAW),                         # 제어 관문 — 이어서 아래 msg.send 도 본다
    "agent/message:msg": Need("msg.send", {}, "msg:local/-", obo="agent:{id}/-"),   # 옛 `agentMessages` 스위치
    "file": Need("fs.write", {"op": "drop"}),
    "ws:input": Need("session.input", _RAW),                              # 입력 프레임마다(리소스는 그 세션)
    # relay — 연결 관문(그룹 하나라도)과 그룹별 판정
    "relay": Need("relay", {"group": S.ANY_GROUP}, "relay:local"),
    "relay-ws": Need("relay", {"group": S.ANY_GROUP}, "relay:local"),
    "relay:group": Need("relay", {}, "relay:local"),                      # ctx.group 은 규칙에서 온다
    # 프로토콜 v2 (`routes/peer_v2.py`, `authz/remote.py`) — 인가는 grant: 핸들러가 리소스를 정해 엔진에 묻는다. 대리 주체 필수.
    "v2/authz/request": Need("authz.request", {}, "authz:*"),             # 요청 자체는 아무 대리 주체나 낼 수 있다(승인은 이 서버의 사람)
    "v2/authz/status": Need("ping"),
    "v2/authz/grants": Need("ping"),                                       # 서명을 통과한 호스트가 자기 권한을 본다(회수 확인의 근거)
    "v2/session/shared": Need("session.view", {}, "session:local/-"),
    "v2/session/read": Need("session.read", {}, "session:local/-"),
    "v2/session/send": Need("session.input", {"mode": "line"}, "session:local/-"),
    "v2/deleg/spawn": Need("deleg.spawn", {}, "deleg-quota:peer"),
    "v2/deleg/send": Need("deleg.control", {"role": "parent"}, "deleg:local/-"),
    "v2/deleg/close": Need("deleg.control", {"role": "parent"}, "deleg:local/-"),
    "v2/deleg/report": Need("deleg.control", {"role": "child"}, "deleg:local/-"),
    "v2/pair/join": Need("ping"),                                          # 합류는 코드(1회용)와 이 서버 사람의 승인이 인가다 — 관리 scope 가 아니다
    "v2/pair/status": Need("ping"),
    "v2/revoke": Need("ping"),                                             # 알림은 힌트 — 인정 여부는 A 가 B 에 직접 물어 정한다
    "v2/rotate": Need("ping"),
    "v2/rotate/confirm": Need("ping"),
}

# 이름이 붙은 capability 가 아닌 `/api/peer/*` 라우트 -> 동작 이름 (None = 엔진 밖: 서명 대신 1회용 티켓이 인증).
ROUTE_ACTIONS: dict[str, Optional[str]] = {
    "/api/peer/pair": None,
    "/api/peer/ws/{tmux_name}": "ws",
    "/api/peer/relay/{rest:path}": "relay",
    "/api/peer/relay-ws/{rest:path}": "relay-ws",
}

# 등급 이름 -> 대표 판정 (표에 없는 동작이 내려가는 곳). 모르는 등급은 모르는 scope 라 거부된다.
LEVEL_FALLBACK = {"view": Need("session.view"), "control": Need("session.input", _RAW)}


def need_for(action: Optional[str], level: str = "view") -> Need:
    n = NEEDS.get(action or "")
    return n if n is not None else LEVEL_FALLBACK.get(level, Need(f"level.{level}"))


def level_of(need: Need) -> str:
    """표의 한 항목이 옛 등급으로는 무엇인가(parity 시험용): 보기 범위 밖이면 control."""
    view = need.scope in ("session.view", "session.read", "ping") and not need.ctx
    return "view" if view else "control"


def _snapshot(grant: dict) -> store.Snapshot:
    """저장소 snapshot 에서 `legacy:hosts:` 사본을 걷어내고 hosts.json 의 지금 grant 를 번역해 얹는다."""
    snap = store.snapshot(time.time())
    snap.grants = [g for g in snap.grants if not str(g.get("origin", "")).startswith("legacy:hosts:")] \
        + migrate.translate_peer_grant(grant)
    snap.source = "live"
    return snap


def decide(grant: dict, action: Optional[str], *, level: str = "view", ctx: Optional[dict] = None,
           resource: Optional[str] = None) -> engine.Decision:
    """peer 의 한 동작에 대한 엔진 판정. 엔진·저장소 오류는 거부 `authz_unavailable` 이다(fail closed)."""
    need = need_for(action, level)
    obo = need.obo.format(id=str(grant.get("id") or "")) if need.obo else None
    try:
        return engine.decide(P.from_peer_grant(grant, obo=obo), need.scope, resource or need.resource,
                             {**need.ctx, **(ctx or {})}, _snapshot(grant))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"peer 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
        return engine.Decision(False, "authz_unavailable", None, "error")


def relay_group_decide(grant: dict, group: Optional[str]) -> engine.Decision:
    """relay 의 그룹별 판정 — 허용 목록(`relay_policy`)이 정한 그룹을 `group` 조건으로 묻는다. 목록 밖이면 그룹이 없어 엔진이
    `cond_missing` 으로 거부한다(필요 조건 값 누락)."""
    return decide(grant, "relay:group", ctx={"group": group} if group else {},
                  resource=f"relay:local/{group or 'unrouted'}")


def unavailable(d: engine.Decision) -> bool:
    return d.reason in ("authz_unavailable", "store_corrupt")


_denied_at: dict[tuple, float] = {}


def audit_denied(grant: dict, action: str, d: engine.Decision, resource: str = HOST_LOCAL) -> None:
    """엔진이 거부한 peer 요청을 authz 감사에 남긴다(같은 peer·동작·이유는 60초에 한 줄). 실패해도 응답을 바꾸지 않는다."""
    key = (grant.get("id"), action, d.reason)
    now = time.time()
    if now - _denied_at.get(key, 0.0) < 60.0:
        return
    if len(_denied_at) > 1024:
        _denied_at.clear()
    _denied_at[key] = now
    try:
        audit.append({"principal": f"peer:{grant.get('id')}", "action": f"peer.{action}", "resource": resource,
                      "scopes": [need_for(action).scope], "decision": "deny", "result": "denied", "reason": d.reason})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"peer 거부 감사 기록 실패: {e}")


# --- 2. 연결 중 재확인 --------------------------------------------------------------------------


def _sig(path) -> tuple:
    try:
        st = os.stat(path)
        return (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return ()


class GrantWatch:
    """열려 있는 peer 연결 하나의 grant 감시. `hosts.json`·`grants.json` 이 안 바뀌었으면 마지막 판정을 그대로 쓴다(입력 프레임마다
    부르는 값싼 경로). `refresh()` 는 파일을 읽을 수 있는 스레드에서 부른다."""

    def __init__(self, peer_id: str, tmux_name: str):
        import host_store
        self.peer_id, self.tmux = peer_id, tmux_name
        self._hosts = host_store._path()
        self._sigs: Optional[tuple] = None
        self.alive = True            # 아직 등록된 peer 인가
        self.view_ok = True
        self.input_ok = False
        self.reason = ""

    def _now_sigs(self) -> tuple:
        return (_sig(self._hosts), _sig(store.doc_path("grants")))

    def refresh(self, force: bool = False) -> None:
        import host_store
        sigs = self._now_sigs()
        if not force and sigs == self._sigs:
            return
        g = host_store.find_grant(self.peer_id)
        self._sigs = sigs
        if g is None:
            self.alive, self.view_ok, self.input_ok, self.reason = False, False, False, "peer_removed"
            return
        self.alive = True
        v = decide(g, "ws")
        i = decide(g, "ws:input", resource=f"session:local/name:{self.tmux}")
        self.view_ok, self.input_ok = v.allow, i.allow
        self.reason = "" if v.allow else v.reason

    def input_allowed(self) -> bool:
        """입력 프레임 직전 재확인 — 파일이 바뀌었으면 그 자리에서 다시 읽는다."""
        try:
            self.refresh()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"peer 입력 권한 재확인 실패 — 거부: {e}")
            return False
        return self.alive and self.view_ok and self.input_ok


def relay_ws_alive(peer_id: str) -> bool:
    """relay 소켓을 계속 열어 둘 수 있는가(grant 가 있고 relay 연결 관문을 통과한다)."""
    import host_store
    g = host_store.find_grant(peer_id)
    return g is not None and decide(g, "relay-ws").allow
