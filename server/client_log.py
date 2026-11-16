"""브라우저가 보낸 오류·경고 기록 저장소 — `$VT_STATE_DIR/client-log/YYYY-MM-DD.jsonl` (폴더 0700, 파일 0600).

왜 있나: 폰에서 난 오류는 개발자 도구가 없어 볼 수 없고, 탭을 닫으면 브라우저 기록(sessionStorage)도 사라진다.
화면이 모아 둔 오류·경고를 서버 파일에 남기면 맥에서(또는 에이전트가 파일을 직접 읽어) 되짚을 수 있다.

설계 규칙
- **오류(`error`)·경고(`warn`)만** 받는다. info 이하는 브라우저 기록에만 남는다.
- 받은 줄은 허용 키만 남기고 길이를 자른다. 요청·응답 **본문은 애초에 받지 않는다**(화면에서도 보내지 않는다).
- 가리기를 서버에서 한 번 더 한다(`redact`). 브라우저(frontend/js/core/redact.js)와 같은 규칙이고,
  `frontend/tests/fixtures/redact-cases.json` 을 두 쪽 시험이 함께 읽어 어긋남을 잡는다.
  홈 디렉터리 경로는 `~`로 바꿔 저장한다.
- 상한: 한 요청 64KB·50건, 프로세스 전체 분당 600줄, 보관 7일·합계 20MB(넘으면 오래된 날부터 지운다).
  넘친 건 조용히 버리되 개수를 세어 다음 쓰기 때 `dropped N` 한 줄로 남긴다.
- 같은 id 가 여러 줄일 수 있다(반복 횟수가 늘 때 화면이 다시 보낸다). 읽을 때 id 별로 가장 새 줄만 쓴다.
- 이 파일은 권한 감사 체인(`authz/audit.py`)과 무관하다. 인가는 라우트가 `human` 등급으로 막는다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import vt_paths

MAX_BODY_BYTES = 64 * 1024
MAX_BATCH = 50
RATE_PER_MIN = 600
RETAIN_DAYS = 7
MAX_TOTAL_BYTES = 20 * 1024 * 1024
READ_TAIL_BYTES = 2 * 1024 * 1024
ACCEPT_LEVELS = ("error", "warn")
SOURCES = ("toast", "api", "js", "ws", "net", "ui", "console")
_DAY_MS = 86_400_000

# ── 가리기 (frontend/js/core/redact.js 와 같은 규칙) ──────────────────────────
_SECRET = r"(?:token|ticket|otp|secret|passw(?:or)?d|api_?key|auth|signature|session|cookie)"
_NAME = r"[A-Za-z0-9_.-]*" + _SECRET + r"[A-Za-z0-9_.-]*"
_JSON_KV = re.compile(r'("' + _NAME + r'"[ \t\r\n]*:[ \t\r\n]*)"(?:[^"\\]|\\.)*"', re.I | re.A)
_QUERY_KV = re.compile(r"((?:^|[?&;,\s])" + _NAME + r"=)[^&\s\"';,]+", re.I | re.A)
_BEARER = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I | re.A)
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def redact(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    text = _JSON_KV.sub(lambda m: m.group(1) + '"***"', text)
    text = _QUERY_KV.sub(lambda m: m.group(1) + "***", text)
    return _BEARER.sub(lambda m: m.group(1) + "***", text)


def _home_marker(text: str) -> str:
    try:
        home = str(vt_paths.user_home())
    except Exception:
        return text
    return text.replace(home, "~") if len(home) > 1 else text


def _clean_str(v, n: int) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    return _home_marker(redact(s))[:n]


# ── 저장 위치 ────────────────────────────────────────────────────────────
def log_dir() -> Path:
    return vt_paths.state_dir() / "client-log"


def _ensure_dir() -> Path:
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _day_file(now_s: float) -> Path:
    return log_dir() / (time.strftime("%Y-%m-%d", time.localtime(now_s)) + ".jsonl")


def _files() -> list[Path]:
    """최신 날부터. 파일 이름이 날짜라 이름순이 곧 시간순이다."""
    try:
        return sorted((p for p in log_dir().glob("*.jsonl") if p.is_file()), key=lambda p: p.name, reverse=True)
    except OSError:
        return []


# ── 검증 ─────────────────────────────────────────────────────────────────
def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and abs(v) != float("inf") else None


def normalize(rec, now_ms: float) -> Optional[dict]:
    """한 줄을 검증·정리한다. 받을 수 없으면 None."""
    if not isinstance(rec, dict):
        return None
    level = rec.get("level")
    if level not in ACCEPT_LEVELS:
        return None
    rid = rec.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        return None
    ts = _num(rec.get("ts"))
    if ts is None or ts < now_ms - RETAIN_DAYS * _DAY_MS or ts > now_ms + _DAY_MS:
        return None
    last = _num(rec.get("last"))
    last = ts if last is None else min(max(last, ts), now_ms + _DAY_MS)
    count = _num(rec.get("count"))
    out: dict = {
        "v": 1,
        "id": rid,
        "ts": int(ts),
        "last": int(last),
        "count": int(min(max(count if count is not None else 1, 1), 10000)),
        "level": level,
        "source": rec.get("source") if rec.get("source") in SOURCES else "ui",
        "msg": _clean_str(rec.get("msg"), 500),
    }
    detail = _clean_str(rec.get("detail"), 4000)
    if detail:
        out["detail"] = detail
    req = rec.get("req")
    if isinstance(req, dict):
        r: dict = {}
        for k, n in (("method", 10), ("path", 300), ("code", 80), ("reason", 300), ("host", 80)):
            s = _clean_str(req.get(k), n)
            if s:
                r[k] = s
        for k in ("status", "ms"):
            x = _num(req.get(k))
            if x is not None:
                r[k] = int(x)
        if r:
            out["req"] = r
    rel = rec.get("rel")
    if isinstance(rel, str) and _ID.match(rel):
        out["rel"] = rel
        if rec.get("relGuess") is True:
            out["relGuess"] = True
    ctx = rec.get("ctx")
    if isinstance(ctx, dict):
        c = {k: _clean_str(ctx.get(k), 200) for k in ("page", "view", "screen", "ua", "session") if ctx.get(k)}
        if c:
            out["ctx"] = c
    return out


# ── 쓰기 ─────────────────────────────────────────────────────────────────
class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__("rate_limited")
        self.retry_after = retry_after


_lock = threading.Lock()
_window_start = 0.0
_window_count = 0
_dropped = 0
_last_prune = 0.0


def _reset_for_tests() -> None:
    global _window_start, _window_count, _dropped, _last_prune
    with _lock:
        _window_start = 0.0
        _window_count = 0
        _dropped = 0
        _last_prune = 0.0


def _prune(now_s: float) -> None:
    """7일이 지난 날 파일과, 합계가 20MB 를 넘는 만큼의 오래된 파일을 지운다."""
    files = _files()
    keep = []
    cutoff = now_s - RETAIN_DAYS * 86400
    for p in files:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                continue
        except OSError:
            continue
        keep.append(p)
    total = 0
    for p in keep:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    for p in reversed(keep):      # 가장 오래된 날부터
        if total <= MAX_TOTAL_BYTES or len(keep) <= 1:
            break
        try:
            total -= p.stat().st_size
            p.unlink()
        except OSError:
            pass


def append(records: list, *, now: Optional[float] = None) -> dict:
    """검증을 통과한 줄을 쓴다. 분당 상한을 넘으면 `RateLimited`. 반환: {accepted, ignored}."""
    global _window_start, _window_count, _dropped, _last_prune
    now_s = time.time() if now is None else now
    now_ms = now_s * 1000
    good = [n for n in (normalize(r, now_ms) for r in records) if n]
    ignored = len(records) - len(good)
    if not good:
        return {"accepted": 0, "ignored": ignored}
    with _lock:
        if now_s - _window_start >= 60:
            _window_start, _window_count = now_s, 0
        room = RATE_PER_MIN - _window_count
        if room <= 0:
            _dropped += len(good)
            raise RateLimited(max(1, int(60 - (now_s - _window_start)) + 1))
        if len(good) > room:
            _dropped += len(good) - room
            good = good[:room]
        _ensure_dir()
        if now_s - _last_prune > 60:
            _last_prune = now_s
            _prune(now_s)
        path = _day_file(now_s)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines = []
        if _dropped:
            lines.append({"v": 1, "id": f"srv_{int(now_s * 1000)}", "ts": int(now_ms), "last": int(now_ms), "count": 1,
                          "level": "warn", "source": "server", "msg": f"dropped {_dropped} records (rate or size limit)"})
        lines.extend(good)
        blob = "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in lines).encode()
        if size + len(blob) > MAX_TOTAL_BYTES:       # 오늘 파일 하나가 합계 상한을 넘기면 그날은 더 쓰지 않는다
            _dropped += len(good)
            return {"accepted": 0, "ignored": ignored}
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
        _dropped = 0
        _window_count += len(good)
    return {"accepted": len(good), "ignored": ignored}


# ── 읽기·지우기 ──────────────────────────────────────────────────────────
def _tail_lines(path: Path) -> list[bytes]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            start = max(0, end - READ_TAIL_BYTES)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]          # 잘린 첫 줄
    return [ln for ln in lines if ln.strip()]


def read(limit: int = 200, *, before: Optional[float] = None, level: Optional[str] = None, q: str = "") -> list[dict]:
    """최신순. 같은 id 는 가장 새 줄만. 파일마다 끝에서 2MB 까지만 본다."""
    limit = max(1, min(int(limit), 500))
    q = (q or "").lower()[:100]
    seen: set = set()
    out: list[dict] = []
    for p in _files():
        for raw in reversed(_tail_lines(p)):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                continue
            if rec["id"] in seen:
                continue
            seen.add(rec["id"])
            if level and rec.get("level") != level:
                continue
            if before is not None and not (rec.get("last", 0) < before):
                continue
            if q:
                req = rec.get("req") or {}
                hay = " ".join(str(x) for x in (rec.get("msg", ""), rec.get("detail", ""), req.get("method", ""), req.get("path", ""), req.get("status", ""))).lower()
                if q not in hay:
                    continue
            out.append(rec)
    out.sort(key=lambda r: r.get("last", 0), reverse=True)
    return out[:limit]


def clear() -> int:
    n = 0
    with _lock:
        for p in _files():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n
