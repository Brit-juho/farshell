"""에이전트 세션 공유 (2026-10-06) — 사용자가 연 tmux 세션을 에이전트가 읽고(필요하면) 입력한다.

## 구조 (사용자 승인된 설계)
**에이전트는 세션을 만들지 못한다.** 사용자가 평범한 세션(빈 터미널)을 만들고 닉네임을 붙여
「공유」하면 에이전트가 MCP 로 그 세션을 읽고, 쓰기를 허용받았으면 입력도 보낸다. 에이전트가 건드릴
수 있는 터미널은 사용자가 이미 정해 둔 것뿐이라 자원 소진·임의 cwd 면이 없다.

  - 공유 표시: tmux 사용자 옵션 `@fsh_share_nick`(`[A-Za-z0-9_-]{1,32}`, 전체에서 유일 — 대소문자
    무시) + `@fsh_share_mode`(`read`|`readwrite`). tmux 가 살아 있는 동안 유지된다.
  - 권한(grant): 에이전트 세션 A 가 공유 세션 S 에 대해 받는다. **읽기와 쓰기는 별도 승인**이고,
    유효 권한 = min(허용된 권한, 지금의 공유 모드). A 와 S 는 이름이 아니라 `#{session_id}`+
    `#{session_created}` 로 식별한다 — 이름을 바꿔도 따라가고, 같은 이름으로 죽였다 다시 만든
    세션은 옛 권한을 물려받지 못한다. Claude/Codex 의 `/new`·`/clear`·재시작은 같은 tmux 세션이므로
    권한이 유지된다(Claude session_id 에 묶지 않는다). A 나 S 가 사라지면 다음 조회 때 정리된다.
  - 승인은 사람만 한다(브라우저 로그인 세션 — `auth.require_human_session`). 기계 토큰은 에이전트도
    가진 값이라 승인 주체가 될 수 없다. 에이전트는 권한이 없으면 **보류 요청**을 만들 뿐이다.
  - 호출자 식별은 `share_identity.py`. 감사 기록은 이 파일의 `audit()`.

## 권한 이관
`VT_AUTHZ_ENFORCE=share` 이면 판정·저장·감사는 `authz/share_svc.py`(엔진)가 맡고, 이 파일은 동작부(`read_pane`·
`send_keys`·`check_send_*`·tmux 옵션)와 공개 함수의 **분기**만 한다. 꺼져 있으면(기본) 아래 옛 구현이 그대로 돈다.
켜도 옛 저장소(`agent_grants.json`·`agent_access.log`)는 같이 쓴다 — 플래그를 끄면 곧바로 롤백된다.

## 저장
`~/.vt/agent_grants.json`(0600 + flock) — grants 와 보류·거부 요청을 한 파일에 둔다(한 락으로
승인 = 요청 삭제 + 권한 기록이 원자적). `~/.vt/agent_access.log`(jsonl 0600, 1MB 에서 한 단계 순환).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import share_identity
import store_lock
import tmux_runner
import vt_paths
from authz import flags as authz_flags
from authz.errors import ShareError
from i18n import tr

logger = logging.getLogger(__name__)

NICK_RE = tmux_runner.SHARE_NICK_RE
MODES = tmux_runner.SHARE_MODES            # ("read", "readwrite")
KIND_READ, KIND_WRITE = "read", "write"
LEVEL_READ, LEVEL_READWRITE = "read", "readwrite"

DEFAULT_LINES = 200
MAX_LINES = 2000
MAX_OUTPUT_BYTES = 64 * 1024
MAX_TEXT_LEN = 4000
READ_PER_MIN = 30        # (에이전트 세션, 공유 세션)별
SEND_PER_MIN = 10
REQUEST_TTL = 24 * 3600
DENY_COOLDOWN = 300
MAX_PENDING_PER_AGENT = 20
MAX_REQUESTS = 200
AUDIT_MAX_BYTES = 1024 * 1024


def enforced() -> bool:
    """`VT_AUTHZ_ENFORCE` 에 `share` 가 있는가 — 켜면 엔진이 공유 세션 판정·저장·감사를 쥔다."""
    return authz_flags.enforced("share")


def _svc():
    from authz import share_svc
    return share_svc


# --- 세션 스냅샷 -------------------------------------------------------------------------


@dataclass(frozen=True)
class Sess:
    sid: str
    created: int
    name: str
    pane_id: str       # 활성 pane — 읽기·입력의 대상
    command: str
    share: Optional[dict]

    @property
    def key(self) -> str:
        return f"{self.sid}:{self.created}"


def snapshot(rows: Optional[list] = None) -> Optional[dict[str, Sess]]:
    """`{세션 key: Sess}`. tmux 를 못 읽었으면 None(권한 정리를 건너뛴다)."""
    if rows is None:
        rows = share_identity.list_pane_rows()
    if rows is None:
        return None
    best: dict[str, object] = {}
    for r in rows:
        key = f"{r.sid}:{r.created}"
        cur = best.get(key)
        if cur is None or (r.active and not cur.active):
            best[key] = r
    tmux_shares = {k: tmux_runner.parse_share(r.nick, r.mode) for k, r in best.items()}
    if enforced():
        # tmux 옵션은 표시용 미러 — 진실은 shares.json 이고 둘이 다르면 낮은 쪽.
        tmux_shares = _svc().resolve_shares(tmux_shares)
    return {k: Sess(r.sid, r.created, r.name, r.pane_id, r.command, tmux_shares[k]) for k, r in best.items()}


def find_by_nick(snap: dict[str, Sess], nick: str) -> Sess:
    """닉네임(대소문자 무시)으로 공유 세션을. 없거나 같은 닉네임이 둘이면(tmux 로 직접 심은 경우) 404 —
    모호한 쪽을 임의로 고르지 않는다. 공유되지 않은 세션과 없는 닉네임은 같은 답이다(추측 방지)."""
    low = (nick or "").strip().lower()
    hits = [s for s in snap.values() if s.share and s.share["nick"].lower() == low]
    if len(hits) != 1:
        raise ShareError("not_found", f"'{nick}' 라는 공유 세션이 없습니다", 404)
    return hits[0]


# --- 입력 검증 ---------------------------------------------------------------------------


def check_nick(nick) -> str:
    if not isinstance(nick, str) or not NICK_RE.fullmatch(nick):
        raise ShareError("bad_nick", "닉네임은 영문·숫자·_·- 1~32자입니다", 400)
    return nick


def check_mode(mode) -> str:
    if mode not in MODES:
        raise ShareError("bad_mode", "공유 모드는 read 또는 readwrite 입니다", 400)
    return mode


_BAD_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def check_send_text(text) -> str:
    """입력할 글 — 한 줄, 4000자 이하, 제어 문자·ESC·줄바꿈·탭 거부. 줄바꿈은 `enter` 와 별개로 명령을
    실행하게 되므로(여러 줄 = 명령 여러 개) 받지 않는다 — 실행은 `enter: true` 로만."""
    if not isinstance(text, str) or text == "":
        raise ShareError("empty", "보낼 글이 비었습니다", 400)
    if len(text) > MAX_TEXT_LEN:
        raise ShareError("too_long", f"글이 너무 깁니다 (최대 {MAX_TEXT_LEN}자)", 400)
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in _BAD_CATEGORIES or "‪" <= ch <= "‮" or "⁦" <= ch <= "⁩":
            raise ShareError(
                "bad_text",
                "제어 문자·보이지 않는 문자·ESC·줄바꿈·탭은 보낼 수 없습니다 — 한 줄만 보내고 실행하려면 enter 를 true 로 하세요", 400)
    return text


def check_lines(lines) -> int:
    if lines is None:
        return DEFAULT_LINES
    if isinstance(lines, bool) or not isinstance(lines, int) or lines < 1:
        raise ShareError("bad_lines", f"lines 는 1~{MAX_LINES} 정수입니다", 400)
    return min(lines, MAX_LINES)


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# --- 저장소 (grants + requests) ----------------------------------------------------------


def _path() -> Path:
    return vt_paths.state_dir() / "agent_grants.json"


@contextmanager
def _locked(name: str = "agent_grants.lock"):
    d = vt_paths.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(d / name):
        yield


def _load() -> tuple[dict, bool]:
    p = _path()
    data: dict = {}
    corrupt = False
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"에이전트 권한 파일 읽기 실패({e}) — 빈 상태로 시작, 다음 저장 전 원본 보존")
            corrupt = True
    if not isinstance(data, dict):
        data = {}
        corrupt = True
    grants, requests = data.get("grants", []), data.get("requests", [])
    if not isinstance(grants, list) or not isinstance(requests, list):
        grants, requests = [], []
        corrupt = True
    return {"grants": [g for g in grants if isinstance(g, dict)],
            "requests": [r for r in requests if isinstance(r, dict)]}, corrupt


def _backup_corrupt() -> Optional[Path]:
    """깨진 저장소의 원본 바이트를 0600 복사본으로 보존한다(같은 내용의 복사본이 있으면 새로 만들지 않는다).
    원본은 지우지도 고치지도 않는다. 복사에 실패하면 None."""
    p = _path()
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    for old in p.parent.glob(f"{p.name}.corrupt-*"):
        try:
            if old.read_bytes() == raw:
                return old
        except OSError:
            continue
    try:
        backup = p.with_name(f"{p.name}.corrupt-{uuid.uuid4().hex}")
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return backup
    except OSError as e:
        logger.warning(f"손상된 권한 파일의 복사본을 만들지 못했다: {e}")
        return None


def refuse_corrupt_write() -> None:
    """읽지 못한 옛 저장소를 빈 상태로 덮으면 모든 권한이 사라진다 — 복사본만 남기고 쓰기를 거부한다(503). 파일은 그대로 둔다.
    호출자는 저장소 락을 쥐고 있다."""
    _backup_corrupt()
    raise ShareError("store_unreadable",
                     tr("권한 파일(agent_grants.json)을 읽을 수 없어 변경을 거부했습니다 — 파일은 그대로 두었습니다. "
                        "파일을 고치거나 'fsh migrate-auth --status' 로 확인하세요"), 503)


def store_problem() -> Optional[str]:
    """옛 저장소를 읽을 수 없으면 이유 한 줄, 아니면 None(진단용 — 읽기만 한다)."""
    p = _path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        return f"읽기 실패: {e.strerror or e}"
    except ValueError as e:
        return f"JSON 파싱 실패: {e}"
    if not isinstance(data, dict) or not isinstance(data.get("grants", []), list) \
            or not isinstance(data.get("requests", []), list):
        return "모양이 올바르지 않음"
    return None


def _save(data: dict) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def _purge(data: dict, snap: Optional[dict[str, Sess]], now: float) -> bool:
    """사라진 세션·만료된 요청을 걷어낸다. snap 이 None(tmux 를 못 읽음)이면 세션 정리는 건너뛴다."""
    before = (len(data["grants"]), len(data["requests"]))

    def alive(ref: dict) -> bool:
        return snap is None or f"{ref.get('sid')}:{ref.get('created')}" in snap

    data["grants"] = [g for g in data["grants"] if alive(g.get("agent") or {}) and alive(g.get("target") or {})]
    keep = []
    for r in data["requests"]:
        if not (alive(r.get("agent") or {}) and alive(r.get("target") or {})):
            continue
        if r.get("state") == "denied":
            if now - float(r.get("decided", 0)) >= DENY_COOLDOWN:
                continue
        elif now >= float(r.get("expires", 0)):
            continue
        keep.append(r)
    data["requests"] = keep
    return before != (len(data["grants"]), len(data["requests"]))


@contextmanager
def _txn(snap: Optional[dict[str, Sess]]):
    """락 안에서 읽고 정리한 뒤 변경이 있으면 저장하는 트랜잭션. `with _txn(snap) as data:` 로 data 를
    고치면 빠져나올 때 저장된다."""
    with _locked():
        data, corrupt = _load()
        before = json.dumps(data, sort_keys=True)
        _purge(data, snap, time.time())   # 정리 결과도 변경으로 저장된다
        yield data
        if json.dumps(data, sort_keys=True) != before or not _path().exists():
            if corrupt and _path().exists():
                refuse_corrupt_write()
            _save(data)


def _ref(s) -> dict:
    return {"sid": s.sid, "created": s.created, "name": s.name}


def _same(ref: dict, s) -> bool:
    """ref(dict) 가 s(Sess·Caller 또는 같은 모양의 dict)와 같은 tmux 세션인가 — sid+created 둘 다 같아야 한다."""
    sid, created = (s.get("sid"), s.get("created")) if isinstance(s, dict) else (s.sid, s.created)
    return ref.get("sid") == sid and ref.get("created") == created


def _find_grant(data: dict, agent, target: Sess) -> Optional[dict]:
    return next((g for g in data["grants"]
                 if _same(g.get("agent") or {}, agent) and _same(g.get("target") or {}, target)), None)


def effective(grant: Optional[dict], share: Optional[dict]) -> dict:
    """유효 권한 = min(허용된 권한, 현재 공유 모드)."""
    mode = (share or {}).get("mode")
    return {"read": bool(grant and grant.get("read") and mode in MODES),
            "write": bool(grant and grant.get("write") and mode == LEVEL_READWRITE)}


def _legacy_request_access(snap: dict[str, Sess], agent, target: Sess, kind: str) -> tuple[dict, bool]:
    """권한이 없을 때 보류 요청. (요청, 새로 만들었는가). 거부 쿨다운 중이면 ShareError."""
    now = time.time()
    with _txn(snap) as data:
        for r in data["requests"]:
            if r.get("kind") == kind and _same(r.get("agent") or {}, agent) and _same(r.get("target") or {}, target):
                if r.get("state") == "denied":
                    wait = int(DENY_COOLDOWN - (now - float(r.get("decided", 0))))
                    raise ShareError("request_denied",
                                     f"사용자가 거부했습니다 — {max(wait, 1)}초 뒤에 다시 요청할 수 있습니다",
                                     403, state="denied", retryAfter=max(wait, 1))
                return dict(r), False
        mine = sum(1 for r in data["requests"] if r.get("state") == "pending" and _same(r.get("agent") or {}, agent))
        if mine >= MAX_PENDING_PER_AGENT or len(data["requests"]) >= MAX_REQUESTS:
            raise ShareError("too_many_requests", "대기 중인 승인 요청이 너무 많습니다 — 사용자가 처리한 뒤 다시 하세요", 429)
        req = {"id": uuid.uuid4().hex[:12], "agent": _ref(agent), "target": {"sid": target.sid, "created": target.created},
               "kind": kind, "state": "pending", "created": now, "expires": now + REQUEST_TTL}
        data["requests"].append(req)
        return dict(req), True


def _legacy_decide(request_id: str, decision: str, level: Optional[str], snap: Optional[dict[str, Sess]]) -> dict:
    """사람의 결정. allow(level=요청 종류와 같거나 낮게) 또는 deny. 결과 `{request, grant}`."""
    if decision not in ("allow", "deny"):
        raise ShareError("bad_decision", "decision 은 allow 또는 deny 입니다", 400)
    now = time.time()
    with _txn(snap) as data:
        req = next((r for r in data["requests"] if r.get("id") == request_id and r.get("state") == "pending"), None)
        if req is None:
            raise ShareError("not_found", "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)", 404)
        if decision == "deny":
            req["state"], req["decided"] = "denied", now
            return {"request": dict(req), "grant": None}
        kind = req["kind"]
        level = level or (LEVEL_READ if kind == KIND_READ else LEVEL_READWRITE)
        if level not in (LEVEL_READ, LEVEL_READWRITE) or (kind == KIND_READ and level != LEVEL_READ):
            raise ShareError("bad_level", "요청보다 높은 권한은 줄 수 없습니다(읽기 요청에는 read 만)", 400)
        tgt = (snap or {}).get(f"{req['target']['sid']}:{req['target']['created']}")
        if level == LEVEL_READWRITE and snap is not None and tgt and (tgt.share or {}).get("mode") != LEVEL_READWRITE:
            raise ShareError("read_only_share", "이 공유는 읽기 전용입니다 — 공유 모드를 읽기·쓰기로 올린 뒤 허용하세요", 409)
        grant = next((g for g in data["grants"]
                      if _same(g.get("agent") or {}, req["agent"]) and _same(g.get("target") or {}, req["target"])), None)
        if grant is None:
            grant = {"id": uuid.uuid4().hex[:12], "agent": dict(req["agent"]), "target": dict(req["target"]),
                     "read": False, "write": False, "granted": now}
            data["grants"].append(grant)
        grant["read"] = True
        if level == LEVEL_READWRITE:
            grant["write"] = True
        grant["updated"] = now
        data["requests"] = [r for r in data["requests"] if r is not req]
        return {"request": dict(req), "grant": dict(grant)}


def _legacy_revoke(grant_id: str, scope: str, snap: Optional[dict[str, Sess]]) -> dict:
    """권한 철회. scope `all`(전부) | `write`(쓰기만 거두고 읽기는 남긴다)."""
    if scope not in ("all", "write"):
        raise ShareError("bad_scope", "scope 는 all 또는 write 입니다", 400)
    with _txn(snap) as data:
        g = next((x for x in data["grants"] if x.get("id") == grant_id), None)
        if g is None:
            raise ShareError("not_found", "그런 권한이 없습니다", 404)
        if scope == "write":
            g["write"] = False
            g["updated"] = time.time()
            return dict(g)
        data["grants"] = [x for x in data["grants"] if x is not g]
        return dict(g)


def _legacy_drop_target(target: Sess, snap: Optional[dict[str, Sess]]) -> tuple[int, list[str]]:
    """공유 해제 — 그 세션에 대한 모든 권한과 대기·거부 요청을 지운다. (지운 권한 수, 지운 요청 id들)."""
    with _txn(snap) as data:
        g0 = len(data["grants"])
        gone = [r.get("id") for r in data["requests"] if _same(r.get("target") or {}, target)]
        data["grants"] = [g for g in data["grants"] if not _same(g.get("target") or {}, target)]
        data["requests"] = [r for r in data["requests"] if not _same(r.get("target") or {}, target)]
        return g0 - len(data["grants"]), gone


def _legacy_list_state(snap: Optional[dict[str, Sess]]) -> dict:
    with _txn(snap) as data:
        return {"grants": [dict(g) for g in data["grants"]], "requests": [dict(r) for r in data["requests"]]}


def _legacy_get_grant(snap, agent, target: Sess) -> Optional[dict]:
    with _txn(snap) as data:
        g = _find_grant(data, agent, target)
        return dict(g) if g else None


# --- 공개 분기: 플래그가 켜져 있으면 엔진(authz/share_svc.py), 아니면 위의 옛 구현 ---------------------


def request_access(snap: dict[str, Sess], agent, target: Sess, kind: str) -> tuple[dict, bool]:
    """권한이 없을 때 보류 요청. (요청, 새로 만들었는가). 거부 쿨다운 중이면 ShareError."""
    if enforced():
        return _svc().request_access(snap, agent, target, kind)
    return _legacy_request_access(snap, agent, target, kind)


def decide(request_id: str, decision: str, level: Optional[str], snap: Optional[dict[str, Sess]], actor=None) -> dict:
    """사람의 결정. 결과 `{request, grant}`(켜져 있으면 `grantId` 등이 더 붙는다). actor: 결정한 주체(`authz.principals`)."""
    if enforced():
        return _svc().decide(request_id, decision, level, snap, actor)
    return _legacy_decide(request_id, decision, level, snap)


def revoke(grant_id: str, scope: str, snap: Optional[dict[str, Sess]], actor=None) -> dict:
    if enforced():
        return _svc().revoke(grant_id, scope, snap, actor)
    return _legacy_revoke(grant_id, scope, snap)


def drop_target(target: Sess, snap: Optional[dict[str, Sess]]) -> tuple[int, list[str]]:
    if enforced():
        return _svc().drop_target(target, snap)
    return _legacy_drop_target(target, snap)


def list_state(snap: Optional[dict[str, Sess]], provenance: bool = False) -> dict:
    """provenance: 엔진 켜짐에서 grant 에 `approvedBy`·`approvedVia` 를 더한다(목록 응답용). 꺼짐에서는 무시한다."""
    if enforced():
        return _svc().list_state(snap, provenance)
    return _legacy_list_state(snap)


def get_grant(snap, agent, target: Sess) -> Optional[dict]:
    if enforced():
        return _svc().get_grant(snap, agent, target)
    return _legacy_get_grant(snap, agent, target)


@dataclass
class Access:
    """`authorize()` 결과. allowed 면 읽기·쓰기를 계속하고, 아니면 보류 요청 흐름이다."""
    allowed: bool
    grant: Optional[dict] = None
    grant_id: Optional[str] = None      # 켜져 있을 때만 — 엔진 grant id(`ag-…`)
    reason: str = ""


def authorize(kind: str, snap, agent, target: Sess) -> Access:
    """`kind`=`read`|`write` 접근 판정(+ 허용이면 분당 횟수 확인). 속도 제한을 넘으면 ShareError(429).

    꺼짐: grant × 공유 모드(`effective`) — 옛 판정 그대로. 켜짐: `engine.decide`(grant·공유 상한·한도). 어느 쪽이든
    shadow 지점(`authz.hooks.agent_share`)을 한 번 지난다."""
    from authz import hooks as authz_hooks
    if enforced():
        try:
            acc = _svc().authorize(kind, snap, agent, target)
        except ShareError:
            raise
        except Exception as e:  # noqa: BLE001 — 켜진 뒤 엔진이 판정권자다: 못 쓰면 열지 않고 닫는다(shadow 단계의 「예외를 삼킨다」와는 다르다)
            logger.warning(f"권한 엔진 판정 실패 — 거부: {type(e).__name__}: {e}")
            raise ShareError("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503) from e
        authz_hooks.agent_share(kind, acc.allowed, acc.grant, agent, target, snap)
        if acc.rate_limited:
            raise ShareError("rate_limited", f"너무 자주 호출했습니다 (분당 {acc.limit}회)", 429)
        return Access(acc.allowed and not acc.rate_limited, acc.grant, acc.grant_id, acc.reason)
    grant = get_grant(snap, agent, target)
    ok = effective(grant, target.share)["read" if kind == KIND_READ else "write"]
    authz_hooks.agent_share(kind, ok, grant, agent, target, snap)
    if ok:
        check_rate("read" if kind == KIND_READ else "send", agent.key, target.key)
    return Access(ok, grant, None, "ok" if ok else "no_grant")


# --- 공유 지정·해제 ----------------------------------------------------------------------

_share_lock = threading.Lock()


def _by_name(snap: dict[str, Sess], name: str) -> Sess:
    hits = [s for s in snap.values() if s.name == name]
    if len(hits) != 1:
        raise ShareError("not_found", f"'{name}' 세션이 없습니다", 404)
    return hits[0]


def set_share(name: str, nick, mode, actor=None) -> dict:
    """세션을 공유하거나 모드·닉네임을 바꾼다. 닉네임 유일성(대소문자 무시)은 락 안에서 확인한다."""
    nick, mode = check_nick(nick), check_mode(mode)
    with _share_lock:
        snap = snapshot()
        if snap is None:
            raise ShareError("tmux_unavailable", "tmux 상태를 읽지 못했습니다", 503)
        target = _by_name(snap, name)
        for s in snap.values():
            if s.key != target.key and s.share and s.share["nick"].lower() == nick.lower():
                raise ShareError("nick_taken", f"'{nick}' 닉네임은 이미 다른 세션이 쓰고 있습니다", 409)
        if enforced():
            # 정책 저장소(진실)를 먼저 — tmux 미러 쓰기가 실패해도 낮은 쪽으로 해소돼 안전하다.
            _svc().put_share(target, nick, mode, snap, actor)
        # 닉네임을 먼저 심으면 모드가 비어 있는 순간 read 로 읽힌다(낮은 쪽이라 안전).
        if not (tmux_runner.set_option(target.sid, tmux_runner.SHARE_NICK_OPTION, nick)
                and tmux_runner.set_option(target.sid, tmux_runner.SHARE_MODE_OPTION, mode)):
            raise ShareError("tmux_failed", "tmux 옵션을 쓰지 못했습니다", 500)
    return {"nick": nick, "mode": mode}


def unset_share(name: str, actor=None) -> dict:
    with _share_lock:
        snap = snapshot()
        if snap is None:
            raise ShareError("tmux_unavailable", "tmux 상태를 읽지 못했습니다", 503)
        target = _by_name(snap, name)
        was = target.share
        if enforced():
            # 정책·권한·요청을 한 트랜잭션에서 먼저 지운다(감사 실패면 503 — 아무것도 안 지운 채 중단).
            grants, reqs = _svc().unshare(target, snap, actor)
        for opt in (tmux_runner.SHARE_NICK_OPTION, tmux_runner.SHARE_MODE_OPTION):
            tmux_runner.run(["set-option", "-u", "-t", target.sid, opt], timeout=2.0)
        if not enforced():
            grants, reqs = drop_target(target, snap)
    return {"was": was, "revokedGrants": grants, "clearedRequests": reqs}


# --- 속도 제한 ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_rate: dict[str, list[float]] = {}
_read_seen: dict[tuple, tuple[float, int]] = {}   # authz 감사의 읽기 표본 추출 — (주체, 리소스, 동작) → (마지막 기록, 건너뜀)


def check_rate(kind: str, agent_key: str, target_key: str, now: Optional[float] = None) -> None:
    limit = READ_PER_MIN if kind == KIND_READ else SEND_PER_MIN
    now = time.time() if now is None else now
    k = f"{kind}|{agent_key}|{target_key}"
    with _rate_lock:
        hits = [t for t in _rate.get(k, []) if now - t < 60]
        if len(hits) >= limit:
            _rate[k] = hits
            raise ShareError("rate_limited", f"너무 자주 호출했습니다 (분당 {limit}회)", 429)
        hits.append(now)
        _rate[k] = hits


# --- 읽기·쓰기 실행 ----------------------------------------------------------------------


def read_pane(target: Sess, lines: int) -> dict:
    rc, out, _ = tmux_runner.run(["capture-pane", "-p", "-J", "-t", target.pane_id, "-S", f"-{lines}"], timeout=3.0)
    if rc != 0:
        raise ShareError("tmux_failed", "화면을 읽지 못했습니다", 502)
    rows = out.decode("utf-8", errors="replace").split("\n")
    while rows and not rows[-1].strip():
        rows.pop()
    rows = rows[-lines:]
    text = "\n".join(rows)
    truncated = False
    data = text.encode("utf-8")
    if len(data) > MAX_OUTPUT_BYTES:
        # 뒤쪽(최근)이 우선 — 앞을 자르고, 잘린 첫 줄은 버린다.
        tail = data[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="ignore")
        nl = tail.find("\n")
        text = tail[nl + 1:] if nl != -1 else tail
        truncated = True
        rows = text.split("\n")
    return {"text": text, "lines": len(rows) if text else 0, "bytes": len(text.encode("utf-8")), "truncated": truncated}


def agent_state_of(session_name: str) -> Optional[str]:
    try:
        import agent_status
        return agent_status.known_status_for_session(session_name)
    except ImportError:  # pragma: no cover
        return None


def check_send_allowed(target: Sess) -> None:
    """에이전트 CLI 가 떠 있고 상태를 아는 세션만 가린다 — 모르면(일반 셸) 허용."""
    st = agent_state_of(target.name)
    if st == "waiting":
        raise ShareError("target_waiting", "대상 세션이 승인 대기 중입니다 — 입력이 승인 답으로 소비될 수 있어 보내지 않습니다", 409)
    if st == "working":
        raise ShareError("target_busy", "대상 세션의 에이전트가 작업 중입니다 — 끝난 뒤에 보내세요", 409)
    import pane_input
    if pane_input.human_typing(target.name):
        raise ShareError("target_human_typing", "사람이 대상 세션에 입력하는 중입니다 — 잠시 뒤에 보내세요", 409)


def recheck_send(agent, target: Sess) -> None:
    """입력 직전 재평가 — 켜짐(`VT_AUTHZ_ENFORCE=share`)에서만. 승인~입력 사이에 grant 가 회수되거나 공유가 내려가거나 세션이 바뀌었으면
    ShareError(403 `grant_revoked`). 분당 횟수는 세지 않는다(이미 `authorize` 가 센 호출이다). 꺼짐이면 아무것도 하지 않는다."""
    if not enforced():
        return
    try:
        fresh = snapshot()
        if fresh is None:
            raise ShareError("tmux_unavailable", "tmux 상태를 읽지 못했습니다", 503)
        cur = fresh.get(target.key)
        ok = cur is not None and cur.pane_id == target.pane_id and _svc().still_allowed("write", fresh, agent, cur)
    except ShareError:
        raise
    except Exception as e:  # noqa: BLE001 — 엔진을 못 쓰면 입력하지 않는다
        logger.warning(f"입력 직전 재평가 실패 — 거부: {type(e).__name__}: {e}")
        raise ShareError("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503) from e
    if not ok:
        raise ShareError("grant_revoked", tr("입력 직전에 권한이 회수되어 보내지 않았습니다"), 403)


def send_keys(target: Sess, text: str, enter: bool, guard=None) -> None:
    # 한 줄 계약(check_send_text)이라 붙여넣기 경로가 필요 없다. 다른 자동 입력과
    # 섞이지 않게 pane 락만 같이 쓴다(pane_input).
    # `guard`: pane 락을 잡은 직후, 첫 키 전에 부른다. 예외(ShareError)를 올리면 아무것도 입력하지 않는다.
    import pane_input
    with pane_input.lock(target.pane_id):
        if guard is not None:
            guard()
        rc, _, _ = tmux_runner.run(["send-keys", "-t", target.pane_id, "-l", "--", text], timeout=3.0)
        if rc == 0 and enter:
            rc, _, _ = tmux_runner.run(["send-keys", "-t", target.pane_id, "Enter"], timeout=3.0)
    if rc != 0:
        raise ShareError("tmux_failed", "입력을 보내지 못했습니다", 502)


# --- 감사 기록 ---------------------------------------------------------------------------


def _audit_path() -> Path:
    return vt_paths.state_dir() / "agent_access.log"


# 엔진이 커밋 전에 직접 감사에 쓰는 변경 동작(fail closed) — 여기서 또 옮기면 두 번 기록된다.
_SVC_AUDITED = frozenset({"decide", "revoke", "share", "unshare"})


def audit(action: str, result: str, *, agent=None, nick: Optional[str] = None,
          by: Optional[str] = None, **extra) -> None:
    """한 줄 jsonl. **원문은 남기지 않는다** — 글자 수와 sha 앞 8자리만. 실패해도 호출을 막지 않는다."""
    rec: dict = {"ts": round(time.time(), 3), "action": action, "result": result,
                 "by": by or ("agent" if agent is not None else "user")}
    if agent is not None:
        rec["agent"] = {"name": agent.name, "id": agent.sid}
        if getattr(agent, "weak", False):
            rec["pidCheck"] = "claimed"
    if nick:
        rec["nick"] = nick
    for k in ("kind", "level", "lines", "chars", "sha8", "enter", "reason", "request", "grant", "mode", "truncated"):
        if k in extra and extra[k] is not None:
            rec[k] = extra[k]
    try:
        with _locked("agent_access.lock"):
            p = _audit_path()
            try:
                if p.is_file() and p.stat().st_size >= AUDIT_MAX_BYTES:
                    os.replace(str(p), str(p.with_name(p.name + ".1")))
            except OSError:
                pass
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
    except Exception as e:  # noqa: BLE001 — 기록 실패가 사용자 작업을 막으면 안 된다
        logger.warning(f"에이전트 접근 기록 실패: {e}")
    if enforced() and action not in _SVC_AUDITED and not action.startswith("delegate_"):
        _svc().mirror_audit(action, result, rec, agent, nick)


def read_audit(limit: int = 100) -> list[dict]:
    """최근 순(새것이 먼저). 순환된 한 단계 앞 파일까지 본다. 켜져 있으면 authz 감사(hash chain)에서 읽는다."""
    if enforced():
        return _svc().read_audit(limit)
    limit = max(1, min(int(limit), 500))
    rows: list[dict] = []
    with _locked("agent_access.lock"):
        for p in (_audit_path(), _audit_path().with_name("agent_access.log.1")):
            if not p.is_file():
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            part = []
            for ln in lines:
                try:
                    part.append(json.loads(ln))
                except ValueError:
                    continue
            rows = part + rows if p.name.endswith(".1") else rows + part
    return list(reversed(rows[-limit:]))
