"""노출 포트 공유 정책 저장소 (2.2, plan-2.2/00-port-access.md §4).

`fsh tunnel expose`가 연 포트마다 "누가 열 수 있나"를 기록한다. 세 모드 —
`public`(URL 아는 누구나) · `session`(로그인 기기만) · `pin`(그 노출 전용 코드).

**왜 `/tmp`의 TSV 레지스트리가 아니라 여기인가.** `tunnel_registry.py`가 읽는
`/tmp/vt-pids/tunnels.tsv`는 bash가 쓰고 **world-readable**이다. PIN 해시가 거기
있으면 안 된다. 그래서 정책은 `~/.vt/port_shares.json`(0600)에 두고, TSV는 지금
역할(프로세스·URL 추적)만 그대로 둔다 — 둘 다 포트를 키로 쓰므로 어긋나지 않는다.

저장 규칙은 file_store.py/snippet_store.py와 동일: 0700 디렉토리 + 0600 파일 +
flock + atomic replace.

**PIN 원문은 저장하지 않는다.** scrypt 해시만 둔다(`auth.password`, 로그인
비밀번호와 같은 형식). `file_store.hash_pin`의 `sha256(salt:pin)`은 **쓰지 않는다** —
저엔트로피 값에 대한 오프라인 무차별 대입이 빠르다. 발급 시 1회 표시하고 잊으면
재발급이고, 재발급은 터널을 안 건드리므로 공개 URL이 죽지 않는다.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

import auth

logger = logging.getLogger(__name__)

VALID_MODES = {"public", "session", "pin"}
VALID_TTLS = {3600, 86400, 604800, 2592000}  # 1h/24h/7d/30d — share.py와 같은 값

# 프록시 리스너가 쓸 loopback 포트 범위. **한 번 할당하면 고정이다** —
# `fsh stop --keep-tunnel`이 추가 포트 터널을 살려두므로(bin/fsh:2318), 재기동한
# 프록시를 살아남은 cloudflared가 **같은 주소에서** 다시 찾아야 한다. 그래서
# ephemeral(bind :0)을 쓰지 않는다. 부수적으로 lsof 출력이 예측 가능해진다.
PROXY_PORT_BASE = int(os.environ.get("VT_PORT_PROXY_BASE", "7800"))
PROXY_PORT_SPAN = 100

# Crockford base32 — I·L·O·U가 빠져 있어 손으로 옮겨 적을 때 헷갈리지 않는다.
# 8자 = 40비트. 숫자 6자리는 공개 인터넷에 놓기엔 온라인 추측에 너무 약하다.
PIN_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PIN_LENGTH = 8

# 세션/PIN 통과 후 브라우저가 받는 쿠키 수명 상한. 실제 수명은
# min(share 만료, 이 값) — share보다 오래 사는 쿠키는 없다(§2-1).
COOKIE_MAX_TTL = 12 * 3600


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _meta_path() -> Path:
    return _state_dir() / "port_shares.json"


def _lock_path() -> Path:
    return _state_dir() / "port_shares.lock"


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


def _read_unlocked() -> list[dict]:
    p = _meta_path()
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # fail-closed: 읽기에 실패하면 "정책 없음"이 아니라 **정책을 모르는 상태**다.
        # 빈 목록을 돌려주면 그 포트는 share가 없는 것이 되어 프록시가 404를 내므로
        # 결과적으로 닫힌다 — 여는 쪽으로 틀리지 않는다(docs/ref/auth.md의 원칙).
        logger.warning(f"port_shares.json 읽기 실패({e}) — 빈 목록으로 시작")
        return []
    items = data.get("shares") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if not isinstance(x.get("port"), int) or not x.get("shareId"):
            continue
        x.setdefault("attempts", 0)
        x.setdefault("views", 0)
        x.setdefault("lastAccess", None)
        x.setdefault("broken", False)
        out.append(x)
    return out


def _write_unlocked(items: list[dict]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(p.parent, 0o700)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "shares": items}, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- PIN ---------------------------------------------------------------------


def generate_pin() -> str:
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))


def format_pin(pin: str) -> str:
    """표시용 — `K7M2QX9P` → `K7M2-QX9P`. 저장·검증은 언제나 정규형으로 한다."""
    half = PIN_LENGTH // 2
    return f"{pin[:half]}-{pin[half:]}" if len(pin) == PIN_LENGTH else pin


def normalize_pin(raw: str) -> str:
    """사용자가 친 값을 비교 가능한 형태로. 폰에서 옮겨 적는 값이라 관대하게 받는다.

    대문자화 + 영숫자만 남김 + Crockford 관례대로 I/L→1, O→0.
    알파벳에서 그 글자들을 이미 뺐으므로 이 치환은 모호하지 않다.
    """
    s = (raw or "").upper()
    s = "".join(c for c in s if c.isalnum())
    return s.translate(str.maketrans({"I": "1", "L": "1", "O": "0", "U": "V"}))


def verify_pin(share: dict, raw: str) -> bool:
    """해시 비교만 한다. **잠금은 여기서 하지 않는다** — 시도 제한은 IP 단위로
    호출부(프록시 앱)가 `auth.lockout`으로 건다. 파일 공유처럼 5회 실패에 share를
    지우면 지나가는 스캐너 봇 하나가 데모를 죽인다(plan §1)."""
    stored = share.get("pinHash") or ""
    if not stored:
        return False
    return auth.verify_password(normalize_pin(raw), stored)


# --- 조회 --------------------------------------------------------------------


def is_expired(share: dict) -> bool:
    return float(share.get("exp") or 0) <= time.time()


def list_shares(include_expired: bool = False) -> list[dict]:
    with _locked():
        items = _read_unlocked()
    return items if include_expired else [s for s in items if not is_expired(s)]


def get_share(port: int) -> dict | None:
    """만료된 것은 없는 것으로 본다 — 호출부가 만료를 따로 안 챙겨도 닫힌다."""
    for s in list_shares():
        if s["port"] == port:
            return s
    return None


def find_by_share_id(share_id: str) -> dict | None:
    if not share_id:
        return None
    for s in list_shares():
        if s["shareId"] == share_id:
            return s
    return None


def find_by_proxy_port(proxy_port: int) -> dict | None:
    for s in list_shares():
        if s.get("proxyPort") == proxy_port:
            return s
    return None


def cookie_ttl(share: dict) -> int:
    """min(share 남은 수명, COOKIE_MAX_TTL). 0 이하면 이미 만료된 share다."""
    remain = int(float(share.get("exp") or 0) - time.time())
    return max(0, min(remain, COOKIE_MAX_TTL))


# --- 쓰기 --------------------------------------------------------------------


def _alloc_proxy_port_unlocked(items: list[dict]) -> int:
    used = {s.get("proxyPort") for s in items}
    for n in range(PROXY_PORT_BASE, PROXY_PORT_BASE + PROXY_PORT_SPAN):
        if n not in used:
            return n
    raise RuntimeError(
        f"프록시 포트 범위({PROXY_PORT_BASE}~{PROXY_PORT_BASE + PROXY_PORT_SPAN - 1})가 가득 찼습니다"
    )


def create_share(port: int, label: str, mode: str, ttl: int) -> tuple[dict, str | None]:
    """새 노출 정책 발급 → (share, PIN 원문 또는 None).

    **PIN 원문을 돌려주는 건 이 호출 하나뿐이다.** 저장은 해시만 하므로 이후
    어떤 조회로도 다시 꺼낼 수 없다 — 잊으면 `regenerate_pin`으로 재발급한다.

    같은 포트에 이미 정책이 있으면 교체하되 `proxyPort`는 물려받는다 — 리스너가
    이미 그 번호에 바인드돼 있고, 번호가 바뀌면 살아남은 cloudflared가 길을 잃는다.
    shareId와 PIN은 새로 만든다(재노출 = 새 자격, plan §1).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"bad mode: {mode}")
    if ttl not in VALID_TTLS:
        raise ValueError(f"bad ttl: {ttl}")

    pin = generate_pin() if mode == "pin" else None
    with _locked():
        items = _read_unlocked()
        prev = next((s for s in items if s["port"] == port), None)
        proxy_port = (prev or {}).get("proxyPort") or _alloc_proxy_port_unlocked(items)
        items = [s for s in items if s["port"] != port]
        share = {
            "port": port,
            "label": (label or f"localhost:{port}")[:64],
            "shareId": secrets.token_urlsafe(6),
            "mode": mode,
            "proxyPort": proxy_port,
            "created": time.time(),
            "exp": time.time() + ttl,
            "attempts": 0,
            "views": 0,
            "lastAccess": None,
            "broken": False,
        }
        if pin is not None:
            share["pinHash"] = auth.hash_password(pin)
        items.append(share)
        _write_unlocked(items)
    return share, pin


def remove_share(port: int) -> bool:
    with _locked():
        items = _read_unlocked()
        rest = [s for s in items if s["port"] != port]
        if len(rest) == len(items):
            return False
        _write_unlocked(rest)
    return True


def _update_unlocked(items: list[dict], port: int, **fields) -> dict | None:
    hit = next((s for s in items if s["port"] == port), None)
    if hit is None:
        return None
    hit.update(fields)
    return hit


def update_share(port: int, **fields) -> dict | None:
    with _locked():
        items = _read_unlocked()
        hit = _update_unlocked(items, port, **fields)
        if hit is None:
            return None
        _write_unlocked(items)
    return hit


def set_mode(port: int, mode: str) -> dict | None:
    """모드만 바꾼다 — 터널도 proxyPort도 건드리지 않으므로 **공개 URL이 살아남는다.**
    `pin`으로 바꿀 때는 PIN이 없으면 안 되므로 `regenerate_pin`을 따로 불러야 한다."""
    if mode not in VALID_MODES:
        raise ValueError(f"bad mode: {mode}")
    return update_share(port, mode=mode)


def regenerate_pin(port: int) -> tuple[dict, str] | None:
    """PIN만 새로 발급. 터널·URL·proxyPort 그대로."""
    pin = generate_pin()
    with _locked():
        items = _read_unlocked()
        hit = _update_unlocked(items, port, pinHash=auth.hash_password(pin), attempts=0)
        if hit is None:
            return None
        _write_unlocked(items)
    return hit, pin


def note_view(port: int) -> None:
    """게이트를 통과해 쿠키를 발급한 시점에만 부른다.

    **프록시가 요청마다 부르면 안 된다** — 매 요청이 flock + 파일 쓰기가 된다.
    의미 있는 사건은 "새 방문자가 통과했다"이지 "정적 파일 하나가 더 나갔다"가 아니다.
    """
    with _locked():
        items = _read_unlocked()
        hit = next((s for s in items if s["port"] == port), None)
        if hit is None:
            return
        hit["views"] = int(hit.get("views", 0)) + 1
        hit["lastAccess"] = time.time()
        _write_unlocked(items)


def _reveal(share: dict, pin: str | None) -> dict:
    out = public_view(share)
    if pin:
        out["pin"] = format_pin(pin)
    return out


def public_view(share: dict) -> dict:
    """API로 내려보낼 형태 — **비밀은 전부 뺀다.**

    `pinHash`가 새어나가면 오프라인 무차별 대입의 출발점이 된다. 목록 API는 승격을
    요구하지 않으므로(`docs/ref/files.md`가 공유 토큰에 대해 내린 것과 같은 판단),
    거기 실리는 순간 승격 게이트를 우회하는 길이 된다.
    """
    drop = {"pinHash"}
    out = {k: v for k, v in share.items() if k not in drop}
    out["hasPin"] = bool(share.get("pinHash"))
    return out


# ---------------------------------------------------------------------------
# CLI — bin/fsh가 서버 프로세스 없이 정책을 직접 읽고 쓴다 (python -m
# port_share_store <cmd>, auth의 `_cli`와 같은 방식).
#
# **CLI는 승격을 요구하지 않는다.** `fsh tunnel expose`는 이미 이 맥의 로컬
# 셸에서 실행되는 것이고, 그건 웹 UI의 승격 게이트가 지키려는 것(원격 브라우저
# 세션)과 다른 신뢰 등급이다 — `fsh git-account`, `fsh otp setup`이 이미 같은
# 경계를 긋고 있다. 실제로 리스너를 띄우는 것은 여전히 **실행 중인 서버
# 프로세스**만 할 수 있으므로, `fsh tunnel expose`는 이 CLI로 정책을 쓴 뒤
# `POST /api/ports/_proxy/sync`로 그 서버를 깨운다(routes/ports.py).
# ---------------------------------------------------------------------------


def _cli(argv: list) -> int:
    import json
    import sys

    cmd = argv[0] if argv else ""

    if cmd == "cli-create":
        port, label, mode, ttl = int(argv[1]), argv[2], argv[3], int(argv[4])
        try:
            share, pin = create_share(port, label, mode, ttl)
        except ValueError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            return 1
        print(json.dumps(_reveal(share, pin), ensure_ascii=False))
        return 0

    if cmd == "cli-remove":
        ok = remove_share(int(argv[1]))
        print(json.dumps({"ok": ok}))
        return 0 if ok else 1

    if cmd == "cli-regenerate-pin":
        result = regenerate_pin(int(argv[1]))
        if result is None:
            print(json.dumps({"error": "not_found"}), file=sys.stderr)
            return 1
        share, pin = result
        print(json.dumps(_reveal(share, pin), ensure_ascii=False))
        return 0

    if cmd == "cli-set-mode":
        port, mode = int(argv[1]), argv[2]
        share = get_share(port)
        if share is None:
            print(json.dumps({"error": "not_found"}), file=sys.stderr)
            return 1
        pin = None
        try:
            if mode == "pin" and not share.get("pinHash"):
                # PIN 없이 pin 모드가 되면 아무도 못 들어온다 — HTTP 핸들러와
                # 같은 규칙(routes/ports.py의 change_share_mode).
                _updated, pin = regenerate_pin(port)
            updated = set_mode(port, mode)
        except ValueError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            return 1
        print(json.dumps(_reveal(updated, pin), ensure_ascii=False))
        return 0

    if cmd == "cli-list":
        for s in list_shares():
            print(f"{s['port']}\t{s['mode']}\t{1 if s.get('broken') else 0}")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
