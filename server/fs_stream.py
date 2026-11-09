"""파일 바이트 내보내기 — `/api/fs/raw`(미리보기·미디어 Range)와 `/api/fs/download`(내려받기).

파일 개편 계획 4.6(미디어 Range)·4.7(다운로드)·7.3(SVG). 계약은 API.md, 설명은 docs/ref/files.md.

- **판정한 fd 하나로 끝까지 보낸다.** `fsguard.resolve_under_roots` 뒤 `fsguard.open_resolved`로 연 fd에서
  `fstat`(크기·rev)과 `pread`를 한다. `FileResponse(path)`는 경로를 다시 열어 판정과 열기 사이의 틈(중간 디렉토리
  바꿔치기)을 되살리므로 쓰지 않는다.
- **보내는 도중 파일이 바뀌면 끊는다.** 조각마다 같은 fd의 `fstat`으로 rev를 다시 보고, 처음과 다르면 스트림을
  예외로 끝낸다(연결이 끊겨 받는 쪽이 「덜 받음」을 안다 — 바뀐 바이트를 섞어 완성된 파일처럼 주지 않는다).
- **연결이 끊기거나 실패해도 fd는 닫힌다.** 스트림의 `finally`가 닫고, 응답(`FdStreamingResponse`)의 `__call__`이
  `finally`에서 한 번 더 닫는다 — 머리를 보내는 중 연결이 끊겨 생성기가 한 번도 돌지 않으면 생성기의 `finally`는
  돌지 않기 때문이다. `close`는 두 번 불려도 한 번만 닫는다(같은 번호를 다시 받은 남의 fd를 닫지 않게).
  여는 일이 기한을 넘겨 버려진 경우에도 열린 fd를 닫는다(`_Holder`).
- blocking I/O(열기·조각 읽기)는 전부 `fsops`(`fs` 풀, 기한, 차단기)에서 한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional, Union

from starlette.responses import StreamingResponse

import fs_read
import fsguard
from resilience import fsops, pools

CHUNK = 256 * 1024
_BUSY_RETRIES = 40          # 조각 읽기가 풀 혼잡(Busy)을 만나면 잠깐 쉬고 다시 — 긴 전송이 한순간의 혼잡으로 끊기지 않게
_BUSY_SLEEP = 0.05


class Unsatisfiable(ValueError):
    """Range가 파일 밖이다 — 416."""


class StreamChanged(RuntimeError):
    """보내는 도중 파일이 바뀌었다 — 연결을 끊는다."""


@dataclass
class Opened:
    fd: int
    size: int
    rev: str
    mime: Optional[str] = None
    closed: bool = False


# --- Range ----------------------------------------------------------------------------

# ASCII 숫자만. `str.isdigit()`은 '²'·'١' 같은 글자도 참이라 `int()`가 ValueError를 던진다(→ 500, fd 누수).
_DIGITS = re.compile(r"[0-9]+")
# 유효 자릿수가 이보다 많으면 `int()`로 바꾸지 않고 `_HUGE`로 본다 — 어떤 파일 크기(< 2**63 ≈ 9.2e18)보다 크다.
# (5000자리 수는 `int()`의 자릿수 상한(4300)에 걸려 ValueError가 난다.)
_MAX_DIGITS = 19
_HUGE = 10 ** _MAX_DIGITS


def _num(s: str) -> Optional[int]:
    """ASCII 숫자열 → 수(너무 길면 `_HUGE`). 숫자열이 아니면 None."""
    if not _DIGITS.fullmatch(s):
        return None
    s = s.lstrip("0") or "0"
    return _HUGE if len(s) > _MAX_DIGITS else int(s)


def parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """`Range` 헤더 → (시작, 끝 포함) 또는 None(전체로 답한다). 파일 밖이면 `Unsatisfiable`.

    - 단일 구간(`bytes=a-b`, `bytes=a-`)과 끝에서 센 구간(`bytes=-n`)만 받는다.
    - 여러 구간(`,`)은 전체 200으로 답한다(계획 4.6 — multipart/byteranges를 만들지 않는다).
    - 모양이 틀린 값은 무시하고 전체로 답한다(RFC 9110 14.2: 받을 수 없는 Range는 무시해도 된다).
    """
    if not header:
        return None
    unit, sep, spec = header.strip().partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if "," in spec:
        return None
    first, dash, last = spec.partition("-")
    first, last = first.strip(), last.strip()
    if not dash or (not first and not last):
        return None
    start = _num(first) if first else None
    end = _num(last) if last else None
    if (first and start is None) or (last and end is None):
        return None
    if start is None:                               # bytes=-n
        if end == 0 or size == 0:
            raise Unsatisfiable()
        return max(0, size - end), size - 1
    if end is None:
        end = size - 1
    elif end < start:
        return None
    if start >= size:
        raise Unsatisfiable()
    return start, min(end, size - 1)


def raw_etag(rev: str) -> str:
    """미디어용 **강한** ETag(rev에서). `If-Range`는 강한 비교만 하므로(RFC 9110 13.1.5) W/를 붙이지 않는다."""
    return '"' + hashlib.sha256(f"fsraw-1|{rev}".encode()).hexdigest()[:32] + '"'


def if_range_matches(if_range: Optional[str], etag: str) -> bool:
    """`If-Range`가 없으면 참(Range를 따른다). 있으면 지금 ETag와 글자 그대로 같을 때만 참 — 날짜·약한 태그는 거짓."""
    if if_range is None:
        return True
    v = if_range.strip()
    return not v.startswith("W/") and v == etag


# --- 열기 -----------------------------------------------------------------------------


class _Holder:
    """`fs` 풀에서 연 fd를 넘겨받는 자리. 기다리던 쪽이 기한 초과로 떠났으면 늦게 열린 fd를 그 자리에서 닫는다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.abandoned = False
        self.fd: Optional[int] = None

    def keep(self, fd: int) -> bool:
        with self.lock:
            if self.abandoned:
                os.close(fd)
                return False
            self.fd = fd
            return True

    def abandon(self) -> None:
        with self.lock:
            self.abandoned = True
            fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)


Result = Union[Opened, tuple]


def _open_raw(path: str, holder: _Holder) -> Result:
    p = fsguard.resolve_under_roots(path)
    fd = fsguard.open_resolved(p)
    if not holder.keep(fd):
        return ("abandoned",)
    st = os.fstat(fd)
    head = fs_read._pread_full(fd, fsguard.SNIFF_BYTES, 0)
    mime = fsguard.raw_type(p, head)
    if not mime:
        return ("not_allowed",)
    if fsguard.is_capped_preview(mime) and st.st_size > fsguard.MAX_IMAGE_BYTES:
        return ("too_large",)
    return Opened(fd=fd, size=st.st_size, rev=fs_read.rev_of(st), mime=mime)


def _open_download(path: str, rev: Optional[str], cap: int, holder: _Holder) -> Result:
    p = fsguard.resolve_under_roots(path)
    fd = fsguard.open_resolved(p)
    if not holder.keep(fd):
        return ("abandoned",)
    st = os.fstat(fd)
    now = fs_read.rev_of(st)
    if rev is not None and rev != now:
        return ("changed", now)
    if st.st_size > cap:
        return ("too_large", st.st_size)
    return Opened(fd=fd, size=st.st_size, rev=now)


async def open_for(kind: str, path: str, **kw) -> Result:
    """`kind`: `raw` | `download`. 성공하면 `Opened`(fd는 호출자 — 보통 `stream()` — 가 닫는다).

    실패 모양: `("not_allowed",)`, `("too_large", …)`, `("changed", rev)`. fsguard·OS 예외는 그대로 던진다
    (라우트가 403/404/503으로 나눈다). 결과가 `Opened`가 아니면 fd는 이미 닫혀 있다."""
    holder = _Holder()
    fn = _open_raw if kind == "raw" else _open_download
    try:
        out = await fsops.call(fn, path, *([kw.get("rev"), kw["cap"]] if kind == "download" else []), holder,
                               path=path, op=f"fs.{kind}")
    except BaseException:
        holder.abandon()
        raise
    if not isinstance(out, Opened):
        holder.abandon()
    return out


def close(opened: Opened) -> None:
    """fd를 닫는다. 두 번째부터는 아무것도 안 한다 — 닫힌 번호는 곧 다른 파일에 다시 쓰이므로 두 번 닫으면 남의 fd를
    닫는다. 이벤트 루프에서만 부른다(스트림 `finally`와 응답 `__call__` — 같은 스레드라 깃발에 잠금이 필요 없다)."""
    if opened.closed:
        return
    opened.closed = True
    try:
        os.close(opened.fd)
    except OSError:
        pass


# --- 보내기 ---------------------------------------------------------------------------


def _read_chunk(fd: int, n: int, pos: int) -> tuple[bytes, str]:
    data = os.pread(fd, n, pos)
    return data, fs_read.rev_of(os.fstat(fd))


async def stream(opened: Opened, start: int, length: int, *, path: str, op: str) -> AsyncIterator[bytes]:
    """`opened.fd`의 [start, start+length)를 조각으로 낸다. 끝나거나 끊기면(취소) fd를 닫는다."""
    try:
        pos, left = start, length
        while left > 0:
            n = min(CHUNK, left)
            for attempt in range(_BUSY_RETRIES):
                try:
                    data, now = await fsops.call(_read_chunk, opened.fd, n, pos, path=path, op=op)
                    break
                except pools.Busy:
                    if attempt == _BUSY_RETRIES - 1:
                        raise
                    await asyncio.sleep(_BUSY_SLEEP)
            if now != opened.rev or not data:
                raise StreamChanged(now)
            yield data
            pos += len(data)
            left -= len(data)
    finally:
        close(opened)


class FdStreamingResponse(StreamingResponse):
    """`stream()`을 내보내고, 어떻게 끝나든(머리를 보내다 끊김·취소·예외) `__call__`의 `finally`에서 fd를 닫는다.

    `StreamingResponse`는 머리(`http.response.start`)를 보내다 연결이 끊기면 본문 생성기를 한 번도 돌리지 않는다 —
    시작하지 않은 async 생성기의 `finally`는 돌지 않으므로 생성기만 믿으면 fd가 GC 때까지 남는다(요청이 쌓이면
    서버 전체가 fd 고갈로 멈춘다)."""

    def __init__(self, opened: Opened, start: int, length: int, *, path: str, op: str, **kw):
        self._opened = opened
        super().__init__(stream(opened, start, length, path=path, op=op), **kw)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close(self._opened)
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass


# --- 헤더 -----------------------------------------------------------------------------


def _ascii_fallback(name: str) -> str:
    out = []
    for ch in name:
        o = ord(ch)
        out.append(ch if 0x20 <= o < 0x7F and ch not in '"\\%;' else "_")
    s = "".join(out).strip() or "download"
    return s[:150]


def content_disposition(kind: str, name: str) -> str:
    """`attachment; filename="…"; filename*=UTF-8''…` — 한글 이름을 RFC 5987/6266로 그대로 남긴다.
    따옴표·역슬래시·제어문자는 ASCII 대체 이름에서 `_`로 바꾼다(헤더 주입·이름 깨짐 방지)."""
    from urllib.parse import quote

    name = Path(name).name or "download"
    name = "".join(ch for ch in name if ord(ch) >= 0x20 and ord(ch) != 0x7F) or "download"
    return f"{kind}; filename=\"{_ascii_fallback(name)}\"; filename*=UTF-8''{quote(name, safe='')}"
