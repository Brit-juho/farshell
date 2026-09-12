"""호스트 레지스트리 + 페어링 (N7/N39 1단계) — 맥 두 대의 farshell 서버를 잇는다.

계획서(80-multihost-agents.md §1)는 SSH 경유 tmux attach를 전제했지만, 실제로는
**양쪽에 farshell을 깔고 서버끼리 직접 통신**하는 쪽으로 결정했다(2026-09-12).
SSH 대비 얻는 것은 속도가 아니라 **되돌릴 수 있음**이다:
  - 연결마다 독립 토큰 → 한 연결만 끊어도 다른 기기 로그인엔 영향이 없다
  - 기본이 `view`(읽기 전용) → SSH처럼 붙는 순간 풀 셸 제어가 되지 않는다
  - 감사 로그 → 누가 언제 뭘 했는지 남는다
SSH 키는 이 셋 중 무엇도 못 한다.

## 저장 구조 (`~/.vt/hosts.json`, 0600)

두 방향을 **따로** 둔다 — 페어링은 단방향이고, 양방향을 원하면 반대로 한 번 더 한다.
방향마다 토큰이 독립이라 한쪽이 새도 반대 방향은 안전하다.

  self   : 이 호스트 자신의 id/label. 처음 쓸 때 1회 생성.
  peers  : **내가 붙는** 원격 호스트(outbound). 내가 요청을 보내야 하므로 secret 보관.
  grants : **나를 보도록 허용한** 상대(inbound). 상대가 보낸 서명을 검증해야 하므로
           역시 secret 보관 — 아래 "왜 해시가 아니라 원문인가" 참고.

## 왜 해시가 아니라 원문을 보관하는가 (devices.json과 다른 선택)

`devices.json`은 쿠키의 sha256만 저장한다. 여기서 같은 방식을 쓰면 bearer 비교밖에
못 하고, 그러면 **매 요청이 토큰 자체를 실어 나른다** — 공개 터널 경유 요청이
한 번이라도 캡처되면 그 토큰이 그대로 털린다.

대신 challenge 없는 HMAC 서명을 쓴다: 요청은 `id + ts + nonce + HMAC(secret, ...)`만
싣고 **secret 자체는 절대 전송되지 않는다.** 검증하려면 양쪽이 같은 secret을 가져야
하므로 원문 보관이 강제된다. 트레이드오프는 명확하다 —
  잃는 것: 파일이 통째로 유출되면 토큰을 쓸 수 있다(0600, 사용자 자기 맥).
  얻는 것: 노출면인 **네트워크**에서 토큰이 사라진다. 재생 공격도 nonce로 막힌다.
`~/.ssh/id_ed25519`가 정확히 같은 절충이다(디스크엔 평문, 선로엔 안 나감).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# `local`은 "이 호스트 자신"을 뜻하는 예약어다(tree.js leaf의 host 기본값도 'local').
# 상대가 이 id로 등록되면 로컬 호스트를 가로챈다 — 등록 시점에 거부한다.
RESERVED_IDS = {"local", "self", "me"}

# 접근 등급. SSH가 못 하는 것이 바로 이 구분이다 — 붙는 것과 조작하는 것을 나눈다.
LEVEL_VIEW = "view"        # 세션 목록·출력 구독·에이전트 상태 (읽기 전용)
LEVEL_CONTROL = "control"  # + 입력 전송. 3단계에서 실제로 쓰인다.
VALID_LEVELS = {LEVEL_VIEW, LEVEL_CONTROL}
DEFAULT_LEVEL = LEVEL_VIEW  # 기본은 항상 안전한 쪽 — control은 명시적으로 켜야 한다

PAIR_TICKET_TTL = 300      # 5분 — auth.TICKET_TTL(기기 등록 티켓)과 같은 관례
SIGNATURE_WINDOW_SEC = 60  # 요청 서명의 유효 시간창(양쪽 시계 오차 보정 후 기준)
ID_MAX_LEN = 40
LABEL_MAX_LEN = 60


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "hosts.json"


def _lock_path() -> Path:
    return _state_dir() / "hosts.lock"


def _chmod_quiet(p: Path, mode: int) -> None:
    try:
        os.chmod(p, mode)
    except OSError:
        pass


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(d, 0o700)
    fd = os.open(str(_lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_unlocked() -> dict:
    p = _path()
    if not p.is_file():
        return {"version": 1, "self": None, "peers": [], "grants": []}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"hosts.json 읽기 실패({e}) — 빈 레지스트리로 시작")
        return {"version": 1, "self": None, "peers": [], "grants": []}
    if not isinstance(data, dict):
        return {"version": 1, "self": None, "peers": [], "grants": []}
    data.setdefault("version", 1)
    data.setdefault("self", None)
    data.setdefault("peers", [])
    data.setdefault("grants", [])
    return data


def _write_unlocked(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(p.parent, 0o700)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def normalize_id(raw: str) -> str | None:
    """호스트 id 정규화 — 소문자 영숫자/dash/underscore만. 예약어와 빈 값은 None."""
    s = (raw or "").strip().lower()
    if not s or len(s) > ID_MAX_LEN:
        return None
    if s in RESERVED_IDS:
        return None
    if not all(c.isalnum() or c in "-_" for c in s):
        return None
    return s


# --- 자기 정체성 ---------------------------------------------------------------


def get_self() -> dict:
    """이 호스트의 id/label. 없으면 hostname 기반으로 1회 생성해 저장한다."""
    with _locked():
        data = _read_unlocked()
        if data.get("self"):
            return data["self"]
        base = normalize_id(socket.gethostname().split(".")[0]) or "host"
        me = {"id": f"{base}-{secrets.token_hex(2)}", "label": socket.gethostname().split(".")[0]}
        data["self"] = me
        _write_unlocked(data)
        return me


def set_self_label(label: str) -> dict:
    with _locked():
        data = _read_unlocked()
        me = data.get("self") or {"id": f"host-{secrets.token_hex(2)}", "label": ""}
        me["label"] = (label or "").strip()[:LABEL_MAX_LEN] or me.get("label") or me["id"]
        data["self"] = me
        _write_unlocked(data)
        return me


# --- 페어링 티켓 (B 쪽: "나를 등록해도 좋다"를 5분간 연다) -----------------------


def issue_pair_ticket(label: str = "") -> str:
    """1회용 페어링 티켓. auth.issue_ticket(기기 등록)과 같은 원리 — 티켓을 보여주는
    시점에 이 맥에 대한 물리적 접근이 이미 증명됐다고 본다."""
    raw = secrets.token_urlsafe(24)
    with _locked():
        data = _read_unlocked()
        now = int(time.time())
        tickets = [t for t in data.get("pairTickets", []) if int(t.get("exp", 0)) > now]
        tickets.append({
            "hash": hashlib.sha256(raw.encode()).hexdigest(),
            "exp": now + PAIR_TICKET_TTL,
            "label": (label or "")[:LABEL_MAX_LEN],
        })
        data["pairTickets"] = tickets
        _write_unlocked(data)
    return raw


def consume_pair_ticket(raw: str) -> dict | None:
    """티켓 검증 + 즉시 소멸. auth.consume_ticket과 같은 계약."""
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with _locked():
        data = _read_unlocked()
        now = int(time.time())
        tickets = [t for t in data.get("pairTickets", []) if int(t.get("exp", 0)) > now]
        hit = next((t for t in tickets if hmac.compare_digest(t.get("hash", ""), digest)), None)
        if hit is None:
            data["pairTickets"] = tickets
            _write_unlocked(data)
            return None
        tickets.remove(hit)
        data["pairTickets"] = tickets
        _write_unlocked(data)
    return hit


# --- grants (inbound — 나를 보도록 허용한 상대) ---------------------------------


def add_grant(peer_id: str, label: str, level: str = DEFAULT_LEVEL,
              version: str = "") -> tuple[dict, str] | None:
    """상대를 허용 목록에 넣고 **그 연결 전용** secret을 새로 발급한다.

    반환 (grant, secret). 같은 id가 이미 있으면 secret을 갈아끼운다(재페어링) —
    옛 secret은 그 즉시 무효가 되므로 재페어링이 곧 회전(rotation)이다.
    """
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    secret = secrets.token_urlsafe(32)
    now = int(time.time())
    with _locked():
        data = _read_unlocked()
        grants = [g for g in data["grants"] if g.get("id") != pid]
        grant = {
            "id": pid,
            "label": (label or pid).strip()[:LABEL_MAX_LEN],
            "secret": secret,
            "level": level if level in VALID_LEVELS else DEFAULT_LEVEL,
            "version": version[:32],
            "added": now,
            "lastSeen": 0,
        }
        grants.append(grant)
        data["grants"] = grants
        _write_unlocked(data)
    return grant, secret


def find_grant(peer_id: str) -> dict | None:
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    with _locked():
        return next((g for g in _read_unlocked()["grants"] if g["id"] == pid), None)


def list_grants() -> list[dict]:
    """secret을 뺀 목록 — 화면/CLI 표시용."""
    with _locked():
        return [{k: v for k, v in g.items() if k != "secret"}
                for g in sorted(_read_unlocked()["grants"], key=lambda x: x.get("added", 0))]


def remove_grant(peer_id: str) -> bool:
    pid = normalize_id(peer_id)
    if pid is None:
        return False
    with _locked():
        data = _read_unlocked()
        rest = [g for g in data["grants"] if g["id"] != pid]
        if len(rest) == len(data["grants"]):
            return False
        data["grants"] = rest
        _write_unlocked(data)
    return True


def set_grant_level(peer_id: str, level: str) -> dict | None:
    """`view` ↔ `control` 승격/강등. control은 그 pane에서 셸 명령을 칠 수 있다는
    뜻이라 SSH가 뚫린 것과 같은 노출이 된다 — 명시적 조작으로만 켠다."""
    if level not in VALID_LEVELS:
        return None
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    with _locked():
        data = _read_unlocked()
        g = next((x for x in data["grants"] if x["id"] == pid), None)
        if g is None:
            return None
        g["level"] = level
        _write_unlocked(data)
        return {k: v for k, v in g.items() if k != "secret"}


def touch_grant(peer_id: str) -> None:
    """lastSeen 갱신 — 매 요청마다 디스크를 쓰지 않도록 하루 단위로만
    (auth.verify_device의 같은 절충)."""
    pid = normalize_id(peer_id)
    if pid is None:
        return
    now = int(time.time())
    with _locked():
        data = _read_unlocked()
        g = next((x for x in data["grants"] if x["id"] == pid), None)
        if g is None or now - int(g.get("lastSeen", 0)) <= 86400:
            return
        g["lastSeen"] = now
        _write_unlocked(data)


# --- peers (outbound — 내가 붙는 원격 호스트) ------------------------------------


def add_peer(peer_id: str, url: str, secret: str, label: str = "",
             version: str = "", clock_skew: float = 0.0) -> dict | None:
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    now = int(time.time())
    with _locked():
        data = _read_unlocked()
        peers = [p for p in data["peers"] if p.get("id") != pid]
        peer = {
            "id": pid,
            "label": (label or pid).strip()[:LABEL_MAX_LEN],
            "url": url.rstrip("/"),
            "secret": secret,
            "version": version[:32],
            "clockSkew": round(clock_skew, 3),
            "added": now,
            "lastSeen": 0,
            "latencyMs": None,
        }
        peers.append(peer)
        data["peers"] = peers
        _write_unlocked(data)
    return peer


def find_peer(peer_id: str) -> dict | None:
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    with _locked():
        return next((p for p in _read_unlocked()["peers"] if p["id"] == pid), None)


def list_peers() -> list[dict]:
    with _locked():
        return [{k: v for k, v in p.items() if k != "secret"}
                for p in sorted(_read_unlocked()["peers"], key=lambda x: x.get("added", 0))]


def remove_peer(peer_id: str) -> bool:
    pid = normalize_id(peer_id)
    if pid is None:
        return False
    with _locked():
        data = _read_unlocked()
        rest = [p for p in data["peers"] if p["id"] != pid]
        if len(rest) == len(data["peers"]):
            return False
        data["peers"] = rest
        _write_unlocked(data)
    return True


def rename_peer(peer_id: str, label: str) -> dict | None:
    """별명은 **내 쪽에서만** 쓰는 이름이다 — 상대에게 전파하지 않는다.
    내가 상대를 뭐라 부르든, 상대가 나를 뭐라 부르든 서로 무관하다."""
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    with _locked():
        data = _read_unlocked()
        p = next((x for x in data["peers"] if x["id"] == pid), None)
        if p is None:
            return None
        p["label"] = (label or "").strip()[:LABEL_MAX_LEN] or p["id"]
        _write_unlocked(data)
        return {k: v for k, v in p.items() if k != "secret"}


def update_peer(peer_id: str, **fields) -> dict | None:
    """lastSeen/latencyMs/url/clockSkew 등 갱신. url 갱신은 익명 cloudflare 터널이
    재시작마다 주소가 바뀌는 문제(§5)를 위해 쓴다."""
    pid = normalize_id(peer_id)
    if pid is None:
        return None
    with _locked():
        data = _read_unlocked()
        p = next((x for x in data["peers"] if x["id"] == pid), None)
        if p is None:
            return None
        p.update(fields)
        _write_unlocked(data)
        return {k: v for k, v in p.items() if k != "secret"}


def revoke_all() -> dict:
    """비상 킬스위치 — 양방향 전부 무효화. 맥 분실 등."""
    with _locked():
        data = _read_unlocked()
        n_p, n_g = len(data["peers"]), len(data["grants"])
        data["peers"] = []
        data["grants"] = []
        data["pairTickets"] = []
        _write_unlocked(data)
    logger.warning(f"[peer] revoke-all — peers {n_p}건, grants {n_g}건 전부 무효화")
    return {"peers": n_p, "grants": n_g}


# --- 요청 서명 -------------------------------------------------------------------


def sign_request(secret: str, method: str, path: str, ts: int, nonce: str) -> str:
    """서명 대상에 method와 path를 포함한다 — 서명 하나를 다른 엔드포인트에
    돌려쓰지 못하게(예: view용 GET 서명을 control용 POST에 재사용) 막는다."""
    payload = f"{method.upper()}\n{path}\n{ts}\n{nonce}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, method: str, path: str, ts: int, nonce: str,
                     sig: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if abs(now - ts) > SIGNATURE_WINDOW_SEC:
        return False
    return hmac.compare_digest(sign_request(secret, method, path, ts, nonce), sig or "")


class NonceCache:
    """재생 공격 차단 — 서명 창(60초) 안에서 같은 nonce를 두 번 받지 않는다.

    유효 시간창이 짧아 메모리 상한이 자연히 잡힌다(창 밖 항목은 어차피 서명
    검증에서 먼저 떨어지므로 지워도 안전). auth._Lockout의 _evict_stale과 같은 패턴.
    """

    def __init__(self, window: float = SIGNATURE_WINDOW_SEC):
        self._window = window
        self._seen: dict[str, float] = {}

    def check_and_add(self, key: str, now: float | None = None) -> bool:
        """처음 보는 nonce면 True(통과), 이미 본 것이면 False(거부)."""
        now = time.time() if now is None else now
        self._evict(now)
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    def _evict(self, now: float) -> None:
        cutoff = now - self._window * 2
        for k in [k for k, t in self._seen.items() if t < cutoff]:
            self._seen.pop(k, None)


# --- 감사 로그 -------------------------------------------------------------------
#
# peer 요청은 "내 맥 밖에서 들어온 것"이라 무슨 일이 있었는지 남는 기록이 있어야 한다.
# 공유 링크(routes/share.py)가 실패를 logger.warning으로만 남기는 것과 달리, 이쪽은
# 성공까지 전부 남긴다 — 나중에 "저 맥이 언제 뭘 봤나"를 되짚을 수 있어야 하기 때문.
# 한 줄 JSON(JSONL) — 회전은 scrollback_persist와 같은 방식으로 단순하게.

AUDIT_MAX_BYTES = 5 * 1024 * 1024


def _audit_path() -> Path:
    return _state_dir() / "peer_audit.log"


def audit(peer_id: str, action: str, ok: bool, detail: str = "") -> None:
    """peer 요청 1건 기록. 실패해도 요청 처리를 막지 않는다(로그가 본체가 아니다)."""
    line = json.dumps({
        "ts": int(time.time()), "peer": peer_id, "action": action,
        "ok": bool(ok), "detail": detail[:200],
    }, ensure_ascii=False)
    try:
        p = _audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _chmod_quiet(p.parent, 0o700)
        if p.is_file() and p.stat().st_size >= AUDIT_MAX_BYTES:
            os.replace(str(p), str(p.with_suffix(".log.1")))
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning(f"peer 감사 로그 기록 실패: {e}")


def read_audit(peer_id: str = "", limit: int = 50) -> list[dict]:
    """최근 기록부터 limit건. peer_id를 주면 그 상대 것만."""
    p = _audit_path()
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if peer_id and rec.get("peer") != peer_id:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


# --- CLI (`fsh host`) -------------------------------------------------------------
#
# queue_store.py/file_store.py와 같은 방식 — 서버가 안 떠 있어도 동작한다.
# 다만 pair/ping은 **상대 서버**가 떠 있어야 한다(내 서버는 필요 없다).

def _fmt_when(ts: int) -> str:
    if not ts:
        return "기록 없음"
    delta = int(time.time()) - int(ts)
    if delta < 60:
        return "방금"
    if delta < 3600:
        return f"{delta // 60}분 전"
    if delta < 86400:
        return f"{delta // 3600}시간 전"
    return f"{delta // 86400}일 전"


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("list", "ls"):
        me = get_self()
        peers, grants = list_peers(), list_grants()
        print()
        print(f"  🖥  이 호스트: {me['label']}  ({me['id']})")
        print()
        print("  ▸ 내가 붙는 호스트 (peers)")
        if not peers:
            print("      없음 — 상대 맥에서 'fsh host pair' 후 'fsh host add' 하세요")
        for p in peers:
            lat = f"{p['latencyMs']}ms" if p.get("latencyMs") is not None else "-"
            print(f"      {p['id']:<16} {p['label']:<16} {p['url']}")
            print(f"      {'':<16} 마지막 확인 {_fmt_when(p.get('lastSeen', 0))} · {lat}"
                  f" · v{p.get('version') or '?'}")
        print()
        print("  ▸ 나를 보도록 허용한 호스트 (grants)")
        if not grants:
            print("      없음")
        for g in grants:
            print(f"      {g['id']:<16} {g['label']:<16} 등급 {g['level']}"
                  f" · 마지막 접속 {_fmt_when(g.get('lastSeen', 0))}")
        print()
        print("  추가: fsh host pair (상대 맥) → fsh host add <url> --ticket <티켓> (내 맥)")
        print()
        return 0

    if cmd == "pair":
        # B 쪽에서 실행 — "나를 등록해도 좋다"를 5분간 연다.
        label = rest[0] if rest and not rest[0].startswith("--") else ""
        ticket = issue_pair_ticket(label)
        me = get_self()
        print()
        print(f"  🔑 페어링 티켓 (5분간 유효, 1회용)")
        print()
        print(f"      {ticket}")
        print()
        print(f"  이 호스트: {me['label']} ({me['id']})")
        print("  상대 맥에서:  fsh host add <이 맥의 접속 URL> --ticket <위 티켓>")
        print()
        print("  ⚠ 상대는 기본 'view'(읽기 전용)로 등록됩니다.")
        print("    입력까지 허용하려면 등록 후: fsh host allow-control <id>")
        print()
        return 0

    if cmd == "add":
        # A 쪽에서 실행 — 티켓을 들고 상대에게 등록을 요청한다.
        if not rest:
            print("  ✗ 사용법: fsh host add <url> --ticket <티켓> [--label 별명]", file=sys.stderr)
            return 2
        url = rest[0]
        ticket = label = ""
        i = 1
        while i < len(rest):
            if rest[i] == "--ticket" and i + 1 < len(rest):
                ticket, i = rest[i + 1], i + 2
            elif rest[i] == "--label" and i + 1 < len(rest):
                label, i = rest[i + 1], i + 2
            else:
                i += 1
        if not ticket:
            print("  ✗ --ticket 이 필요합니다 (상대 맥에서 'fsh host pair'로 발급)", file=sys.stderr)
            return 2
        if not url.startswith(("http://", "https://")):
            print(f"  ✗ URL은 http(s):// 로 시작해야 합니다: {url}", file=sys.stderr)
            return 2
        import peer_client
        try:
            r = peer_client.pair_sync(url, ticket, label)
        except peer_client.PeerError as e:
            print(f"  ✗ 페어링 실패: {e.reason}", file=sys.stderr)
            return 1
        p = r["peer"]
        print()
        print(f"  ✓ 페어링 완료 — {p['label']} ({p['id']})")
        print(f"      URL      {p['url']}")
        print(f"      지연      {r['latencyMs']}ms")
        print(f"      상대 버전  v{r['remoteVersion'] or '?'}")
        if abs(r["clockSkew"]) > 5:
            print(f"      ⚠ 시계 차이 {r['clockSkew']:+.1f}초 — 보정해서 저장했습니다")
        print()
        print("  이 호스트는 읽기 전용(view)입니다. 상대 맥에서 등급을 올리려면:")
        print(f"      fsh host allow-control {host_store_self_id_hint()}")
        print()
        return 0

    if cmd == "ping":
        if not rest:
            print("  ✗ id가 필요합니다: fsh host ping <id>", file=sys.stderr)
            return 2
        peer = find_peer(rest[0])
        if peer is None:
            print(f"  ✗ '{rest[0]}' 호스트가 없습니다", file=sys.stderr)
            return 1
        import peer_client
        try:
            r = peer_client.ping_sync(peer)
        except peer_client.PeerError as e:
            print(f"  ✗ {peer['label']} ({peer['id']}) 연결 안 됨 — {e.reason}", file=sys.stderr)
            return 1
        print(f"  ✓ {r['label']} ({r['id']}) · {r['latencyMs']}ms · v{r.get('version') or '?'}"
              f" · 내 등급 {r.get('level', '?')}")
        if abs(r["clockSkew"]) > 5:
            print(f"    ⚠ 시계 차이 {r['clockSkew']:+.1f}초")
        return 0

    if cmd in ("rm", "remove"):
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        pid = rest[0]
        did_peer = remove_peer(pid)
        did_grant = remove_grant(pid)
        if not (did_peer or did_grant):
            print(f"  ✗ '{pid}' 호스트가 없습니다", file=sys.stderr)
            return 1
        parts = []
        if did_peer:
            parts.append("내가 붙던 연결")
        if did_grant:
            parts.append("상대에게 준 접근 권한")
        print(f"  ✓ {pid} 삭제됨 ({', '.join(parts)})")
        return 0

    if cmd == "rename":
        if len(rest) < 2:
            print("  ✗ 사용법: fsh host rename <id> <새 별명>   (self = 이 호스트)", file=sys.stderr)
            return 2
        if rest[0] == "self":
            me = set_self_label(" ".join(rest[1:]))
            print(f"  ✓ 이 호스트의 이름: {me['label']} ({me['id']})")
            return 0
        p = rename_peer(rest[0], " ".join(rest[1:]))
        if p is None:
            print(f"  ✗ '{rest[0]}' 호스트가 없습니다", file=sys.stderr)
            return 1
        print(f"  ✓ {p['id']} → {p['label']}  (내 쪽에서만 쓰는 이름입니다)")
        return 0

    if cmd in ("allow-control", "deny-control"):
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        level = LEVEL_CONTROL if cmd == "allow-control" else LEVEL_VIEW
        g = set_grant_level(rest[0], level)
        if g is None:
            print(f"  ✗ '{rest[0]}' 를 허용 목록에서 찾을 수 없습니다", file=sys.stderr)
            return 1
        if level == LEVEL_CONTROL:
            print(f"  ✓ {g['id']} 등급 → control")
            print("    ⚠ 이제 그 호스트가 세션에 입력을 보낼 수 있습니다 — SSH로 들어온 것과")
            print("      같은 수준의 노출입니다. 되돌리려면: fsh host deny-control " + g["id"])
        else:
            print(f"  ✓ {g['id']} 등급 → view (읽기 전용)")
        return 0

    if cmd == "log":
        pid = rest[0] if rest else ""
        rows = read_audit(pid, limit=50)
        print()
        if not rows:
            print("  기록이 없습니다")
        for r in rows:
            mark = "✓" if r.get("ok") else "✗"
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            detail = f"  {r['detail']}" if r.get("detail") else ""
            print(f"  {mark} {when}  {r.get('peer', '?'):<16} {r.get('action', '')}{detail}")
        print()
        return 0

    if cmd == "revoke-all":
        r = revoke_all()
        print(f"  ✓ 전부 무효화 — peers {r['peers']}건, grants {r['grants']}건")
        print("    상대 맥에도 남아 있는 항목은 그쪽에서 'fsh host rm' 하세요.")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def host_store_self_id_hint() -> str:
    """`fsh host add` 안내문에서 "상대 맥에서 이 id로 등급을 올리세요"를 찍기 위한 값."""
    return get_self()["id"]


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
