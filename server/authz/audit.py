"""감사 로그 — `~/.vt/audit/authz.jsonl` (0600, hash chain).

각 줄: `seq, ts, prev_sha, principal, obo, action, resource, scopes, decision, grant_id/approval_id/
window_id, result, reason, sizes, sha8`. `prev_sha`는 **앞 줄 원문 바이트의 sha256**이라 중간 줄을
고치거나 지우면 `verify()`가 그 자리를 짚는다. 회전(5MB, 10세대) 뒤에도 체인은 이어진다 — 새 파일의
첫 줄이 회전된 파일 마지막 줄을 가리킨다.

**내용은 절대 기록하지 않는다.** 입력 텍스트·화면·메시지 본문·URL query·쿠키 값·파일 내용·
비밀이 들어올 만한 키는 `_scrub`이 버린다. 길이·sha8 같은 크기 정보만 남긴다. URL 은 origin +
path 의 sha8 로 바꾼다.

단일 writer: 서버와 `fsh migrate-auth` 가 같은 `audit.lock` 아래에서만 쓴다.

거부 판정은 이 체인에 쓰지 않는다 — `denylog.py`(별도 파일, 분당 상한, 쓰기 실패는 요청을 막지 않음). 체인에는 허용 판정과
authz·pair·lease·grant 변경(사람의 거절 결정 포함)이 남는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import store_lock
import vt_paths

MAX_BYTES = 5 * 1024 * 1024
GENERATIONS = 10
MAX_AGE_DAYS = 90
GENESIS = "0" * 64

# 값이 내용일 수 있는 키. 어떤 깊이에 있어도 버린다.
FORBIDDEN_KEYS = frozenset({
    "text", "content", "body", "screen", "output", "message", "msg", "summary", "task", "query",
    "fragment", "cookie", "cookies", "value", "values", "storage", "secret", "token", "password",
    "authorization", "data", "raw", "keys", "input", "lines_text", "headers",
})
ALLOWED_TOP = ("seq", "ts", "prev_sha", "principal", "obo", "action", "resource", "scopes", "decision",
               "grant_id", "approval_id", "window_id", "result", "reason", "sizes", "sha8", "source", "detail")


def path() -> Path:
    return vt_paths.state_dir() / "audit" / "authz.jsonl"


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def _scrub(v, depth: int = 0):
    if depth > 4:
        return None
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            k = str(k)
            if k.lower() in FORBIDDEN_KEYS:
                continue
            if k.lower() == "url" and isinstance(x, str):
                u = urlsplit(x)
                out["url"] = {"origin": f"{u.scheme}://{u.netloc}" if u.netloc else u.scheme, "path_sha8": sha8(u.path)}
                continue
            out[k] = _scrub(x, depth + 1)
        return out
    if isinstance(v, (list, tuple)):
        return [_scrub(x, depth + 1) for x in list(v)[:64]]
    if isinstance(v, str):
        return v[:200]
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)[:200]


def scrub(rec: dict) -> dict:
    return {k: v for k, v in _scrub(rec).items() if k in ALLOWED_TOP}


def _last_line(p: Path) -> Optional[bytes]:
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return None
            back = min(end, 64 * 1024)
            f.seek(end - back)
            chunk = f.read().rstrip(b"\n")
            return chunk.rsplit(b"\n", 1)[-1] if chunk else None
    except OSError:
        return None


def _rotated(p: Path, i: int) -> Path:
    return p.with_name(f"{p.name}.{i}")


def _rotate(p: Path, now: float) -> None:
    for i in range(GENERATIONS - 1, 0, -1):
        src = _rotated(p, i)
        if src.exists():
            os.replace(src, _rotated(p, i + 1))
    os.replace(p, _rotated(p, 1))
    old = _rotated(p, GENERATIONS)
    try:
        old.unlink()
    except OSError:
        pass
    for i in range(1, GENERATIONS):
        r = _rotated(p, i)
        try:
            if r.exists() and now - r.stat().st_mtime > MAX_AGE_DAYS * 86400:
                r.unlink()
        except OSError:
            pass


def append(rec: dict, *, now: Optional[float] = None) -> dict:
    """한 줄 추가. 실패는 OSError 로 올린다 — authz 변경은 호출자가 fail closed 한다.

    거부 판정(`denylog.is_denial`)은 체인이 아니라 `denials.jsonl` 로 간다(실패해도 예외 없음, 반환값에 `seq` 가 없다)."""
    from . import denylog
    if denylog.is_denial(rec):
        denylog.record(rec, now=now)
        return {**scrub(rec), "source": "denials"}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    now = time.time() if now is None else now
    with store_lock.exclusive(p.with_name("audit.lock")):
        last = _last_line(p)
        if last is None:
            # 회전 직후: 직전 세대의 마지막 줄에 잇는다.
            last = _last_line(_rotated(p, 1))
        prev_sha = hashlib.sha256(last).hexdigest() if last else GENESIS
        try:
            seq = int(json.loads(last).get("seq", 0)) + 1 if last else 1
        except (ValueError, AttributeError):
            seq = 1
        out = {**scrub(rec), "seq": seq, "ts": round(now, 3), "prev_sha": prev_sha}
        line = (json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n").encode()
        if p.exists() and p.stat().st_size + len(line) > MAX_BYTES:
            _rotate(p, now)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as f:
            f.write(line)
        return out


def verify(p: Optional[Path] = None) -> tuple[bool, Optional[int], int]:
    """체인 검증 — (온전한가, 처음 깨진 seq, 읽은 줄 수). 회전 세대는 오래된 것부터 이어 본다."""
    p = p or path()
    files = [_rotated(p, i) for i in range(GENERATIONS, 0, -1) if _rotated(p, i).exists()] + ([p] if p.exists() else [])
    prev: Optional[bytes] = None
    n = 0
    for fp in files:
        with open(fp, "rb") as f:
            for raw in f:
                raw = raw.rstrip(b"\n")
                if not raw:
                    continue
                n += 1
                try:
                    rec = json.loads(raw)
                except ValueError:
                    return False, None, n
                # 가장 오래된 줄은 지워진 세대를 가리킬 수 있다 — 그 한 줄만 앞 고리를 믿는다.
                want = hashlib.sha256(prev).hexdigest() if prev is not None else rec.get("prev_sha")
                if rec.get("prev_sha") != want:
                    return False, rec.get("seq"), n
                prev = raw
    return True, None, n


def read_recent(limit: int, action_prefix: str = "") -> list[dict]:
    """최근 줄(새 것 먼저). `action_prefix` 로 거른다. 파일 끝 512KB 만 보고 회전 세대는 한 단계 앞까지 본다."""
    limit = max(1, min(int(limit), 1000))
    out: list[dict] = []
    p = path()
    for fp in (p, _rotated(p, 1)):
        try:
            with open(fp, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))
                lines = f.read().splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and str(rec.get("action", "")).startswith(action_prefix):
                out.append(rec)
                if len(out) >= limit:
                    return out
    return out


def read_merged(limit: int, action_prefix: str = "") -> list[dict]:
    """`read_recent` 와 같지만 거부 기록(`denials.jsonl`)도 합친다(최근 순). 주 체인 줄은 `source: chain`, 거부 줄은 `denials`."""
    from . import denylog
    limit = max(1, min(int(limit), 1000))
    rows = [{**r, "source": "chain"} for r in read_recent(limit, action_prefix)]
    rows += [r for r in denylog.read_recent(limit) if str(r.get("action", "")).startswith(action_prefix)]
    rows.sort(key=lambda r: (float(r.get("ts") or 0), int(r.get("seq") or 0)), reverse=True)
    return rows[:limit]


# 조회 응답에 싣는 필드 — 기록 때 이미 내용을 거른 값이지만 한 번 더 허용 목록으로 자른다.
VIEW_KEYS = ("seq", "ts", "principal", "obo", "action", "resource", "scopes", "decision", "grant_id", "approval_id",
             "window_id", "result", "reason", "sizes", "sha8", "source", "detail", "dropped")


def query(*, principal: str = "", resource: str = "", since: float = 0.0, limit: int = 100) -> list[dict]:
    """주 체인과 거부 기록을 합쳐 최근 순으로. 각 줄에 `source`(`chain`|`denials`). 필터는 접두어 일치(주체·리소스)와 시각.
    내용은 기록 단계에서 이미 걸러져 있다 — 여기서도 허용 필드만 돌려준다."""
    from . import denylog
    limit = max(1, min(int(limit), 500))
    rows: list[dict] = []
    for r in read_recent(1000):
        rows.append({**r, "source": "chain"})
    for r in denylog.read_recent(1000):
        rows.append({**r, "source": "denials"})

    def keep(r: dict) -> bool:
        if principal and not (str(r.get("principal") or "").startswith(principal)
                              or str(r.get("obo") or "").startswith(principal)):
            return False
        if resource and not str(r.get("resource") or "").startswith(resource):
            return False
        return float(r.get("ts") or 0) >= since

    rows = [r for r in rows if keep(r)]
    rows.sort(key=lambda r: (float(r.get("ts") or 0), int(r.get("seq") or 0)), reverse=True)
    return [{k: v for k, v in _scrub_view(r).items() if k in VIEW_KEYS} for r in rows[:limit]]


def _scrub_view(r: dict) -> dict:
    out = _scrub({k: v for k, v in r.items() if k not in ("source", "seq", "ts", "dropped")})
    for k in ("source", "seq", "ts", "dropped"):
        if k in r:
            out[k] = r[k]
    return out
