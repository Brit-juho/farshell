"""코드 뷰어 파일 읽기 2판 — 판 번호(rev), 조건부 요청(304), 구간 읽기, 인코딩.

`/api/fs/file`(routes/files.py)이 이 모듈 하나로 읽는다. 경로 판정은 fsguard가 하고(계약 2), 여기서는
**판정한 경로를 연 fd** 하나로 크기·종류·판 번호·내용을 모두 본다. 경로를 다시 `open()`하면 판정과 읽기
사이의 바꿔치기 틈이 다시 생긴다(`fsguard.open_resolved`).

계약(API.md `/api/fs/file`):
- `rev = "<dev>:<ino>:<size>:<mtime_ns>"` — 같은 fd의 `fstat`. 읽기 전후의 `fstat`이 다르면 읽는 도중 바뀐
  것이라 그 내용은 버린다(`Changed`).
- ETag는 rev + 요청한 인코딩 + 구간을 합친 값이다. 메타데이터라서 내용이 같아도 mtime이 바뀌면 다른 값이 된다.
- 구간 읽기의 단위는 바이트이고, 응답은 **줄 경계**에서 자른다. 줄 끝(`\\n`)에서 자르면 UTF-8·CP949·EUC-KR·
  Shift_JIS 문자가 중간에서 끊기지 않는다(이 인코딩들의 둘째 바이트는 0x40 이상이다). UTF-16은 2바이트 단위의
  `\\n` 글자를 찾는다. 창 안에 줄 끝이 없으면 문자 경계에서 자르고 `line_truncated`를 붙인다.
- `auto` 인코딩 판정: BOM → (NUL 없는) 엄격한 UTF-8 → BOM 없는 UTF-16 추정 → `unknown`. `unknown`은 지금처럼
  UTF-8 `replace`로 풀어서 준다. 바이너리 판정(NUL)은 UTF-16 판정 **뒤에** 한다.

전부 blocking이다 — `fsops.call`(`fs` 풀, 기한, 차단기)에서 부른다.
"""

from __future__ import annotations

import codecs
import hashlib
import os
from pathlib import Path
from typing import Optional

import fsguard

# 구간 하나의 최대 크기(바이트). 4MB 이하 파일은 클라이언트가 이 구간 4개를 이어 받는다.
MAX_RANGE_BYTES = 1024 * 1024

# 요청 `encoding` 값 → 파이썬 코덱. CP949와 EUC-KR은 서로 다른 선택지다(CP949가 EUC-KR의 확장).
ENCODINGS = {
    "utf-8": "utf-8",
    "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
    "cp949": "cp949",
    "euc-kr": "euc_kr",
    "shift_jis": "shift_jis",
    "latin-1": "latin-1",
}
REQUEST_ENCODINGS = ("auto", *ENCODINGS)

_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16le"),
    (codecs.BOM_UTF16_BE, "utf-16be"),
)

# rev 문자열 상한 — 숫자 네 개라 이보다 길면 잘못된 값이다.
MAX_REV_LEN = 128

# 응답 형식의 판. ETag에 들어가므로 응답 모양을 바꾸면 올린다(옛 캐시와 섞이지 않게).
_FORMAT = "fsfile-2"


class Changed(Exception):
    """파일이 기대한 판이 아니거나 읽는 도중 바뀌었다 — 409 `changed`."""

    def __init__(self, rev: str):
        super().__init__(rev)
        self.rev = rev


class BadRange(ValueError):
    """구간 값이 파일과 맞지 않는다(EOF 뒤 시작, UTF-16에서 홀수 위치) — 400 `bad_range`."""


def rev_of(st: os.stat_result) -> str:
    return f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}"


def make_etag(rev: str, encoding: str, mode: str, offset: Optional[int], length: Optional[int],
              tail: Optional[int]) -> str:
    """rev + 인코딩 + 구간. 메타데이터에서 만든 검증값이라 약한(W/) ETag로 낸다."""
    key = f"{_FORMAT}|{rev}|{encoding}|{mode}|{offset}|{length}|{tail}|{fsguard.MAX_BYTES}"
    return 'W/"' + hashlib.sha256(key.encode()).hexdigest()[:32] + '"'


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """`If-None-Match`의 약한 비교(RFC 9110 13.1.2) — `W/` 접두사를 떼고 견준다. `*`도 일치로 본다."""
    if not if_none_match:
        return False
    want = etag[2:] if etag.startswith("W/") else etag
    for tag in if_none_match.split(","):
        tag = tag.strip()
        if tag == "*":
            return True
        if (tag[2:] if tag.startswith("W/") else tag) == want:
            return True
    return False


def _pread_full(fd: int, n: int, offset: int) -> bytes:
    """`os.pread`는 짧게 돌려줄 수 있다 — n바이트나 EOF까지 채운다."""
    chunks = []
    while n > 0:
        b = os.pread(fd, n, offset)
        if not b:
            break
        chunks.append(b)
        n -= len(b)
        offset += len(b)
    return b"".join(chunks)


# --- 인코딩 ---------------------------------------------------------------------


def bom_encoding(head: bytes) -> tuple[Optional[str], int]:
    for bom, name in _BOMS:
        if head.startswith(bom):
            return name, len(bom)
    return None, 0


def guess_utf16(head: bytes) -> Optional[str]:
    """BOM 없는 UTF-16 추정 — ASCII 위주 UTF-16 텍스트는 짝수 또는 홀수 자리에 NUL이 몰린다.

    LE의 `a`는 `61 00`(홀수 자리 NUL), BE는 `00 61`(짝수 자리 NUL). 한쪽이 30% 이상이고 다른 쪽이 거의 없을
    때만 추정한다 — 바이너리는 NUL이 양쪽에 고르게 흩어진다."""
    pairs = len(head) // 2
    if pairs < 2:
        return None
    even = head[0:pairs * 2:2].count(0)
    odd = head[1:pairs * 2:2].count(0)
    if odd >= pairs * 0.3 and even <= pairs * 0.05:
        guess = "utf-16le"
    elif even >= pairs * 0.3 and odd <= pairs * 0.05:
        guess = "utf-16be"
    else:
        return None
    # NUL 자리만 보면 `00 01 02 ff` 같은 바이너리도 걸린다. 풀어 본 글자가 거의 다 글자(제어문자 아님)일 때만.
    try:
        text = codecs.getincrementaldecoder(ENCODINGS[guess])("strict").decode(head[:pairs * 2], False)
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\n\r\f\v")
    return guess if printable >= len(text) * 0.95 else None


def _decode(data: bytes, codec: str, final: bool) -> tuple[str, int, bool]:
    """(글자, 소비한 바이트 수, 엄격하게 풀렸는가). `final=False`면 끝의 덜 끝난 문자는 남긴다(소비하지 않음)."""
    strict = True
    try:
        d = codecs.getincrementaldecoder(codec)("strict")
        text = d.decode(data, final)
    except UnicodeDecodeError:
        strict = False
        d = codecs.getincrementaldecoder(codec)("replace")
        text = d.decode(data, final)
    pending = d.getstate()[0]
    return text, len(data) - len(pending), strict


def _is_utf16(enc: str) -> bool:
    return enc in ("utf-16le", "utf-16be")


def _newline(enc: str) -> bytes:
    if enc == "utf-16le":
        return b"\n\x00"
    if enc == "utf-16be":
        return b"\x00\n"
    return b"\n"


def _rfind_line_end(data: bytes, enc: str) -> int:
    """창 안 마지막 줄 끝 **다음** 위치(없으면 -1). UTF-16은 짝수 자리의 2바이트 글자만 센다."""
    nl = _newline(enc)
    i = data.rfind(nl)
    if len(nl) == 2:
        while i >= 0 and i % 2:
            i = data.rfind(nl, 0, i + 1)
    return -1 if i < 0 else i + len(nl)


def _find_line_end(data: bytes, enc: str) -> int:
    """창 안 첫 줄 끝 **다음** 위치(없으면 -1)."""
    nl = _newline(enc)
    i = data.find(nl)
    if len(nl) == 2:
        while i >= 0 and i % 2:
            i = data.find(nl, i + 1)
    return -1 if i < 0 else i + len(nl)


def _resolve_encoding(requested: str, head: bytes, data: bytes, data_at_eof: bool,
                      starts_at_zero: bool) -> dict:
    """요청 인코딩과 파일 머리(`head`)·보낼 바이트(`data`)로 실제 인코딩을 정한다.

    돌려주는 것: `encoding`(응답에 쓸 이름 또는 `unknown`), `codec`, `confident`, `reason`(확신 못 할 때의
    기계 코드), `suggested`, `bom`(머리에 BOM이 있는가), `binary`."""
    bom_enc, bom_len = bom_encoding(head)
    out = {"bom": bool(bom_enc), "suggested": None, "reason": None, "binary": False}
    if requested != "auto":
        enc = requested
        out.update(encoding=enc, codec=ENCODINGS[enc], confident=True)
        # 사람이 UTF-16을 골랐으면 NUL은 글자의 일부다. 다른 인코딩에서 NUL은 지금처럼 바이너리다.
        out["binary"] = not _is_utf16(enc) and b"\x00" in head
        return out
    if bom_enc:
        out.update(encoding=bom_enc, codec=ENCODINGS[bom_enc], confident=True)
        return out
    if b"\x00" not in head:
        _, _, strict = _decode(data, "utf-8", data_at_eof)
        if strict:
            out.update(encoding="utf-8", codec="utf-8", confident=True)
            return out
    guess = guess_utf16(head)
    if guess:
        out.update(encoding=guess, codec=ENCODINGS[guess], confident=False,
                   reason="guessed_utf16", suggested=guess)
        return out
    out.update(encoding="unknown", codec="utf-8", confident=False, reason="not_utf8",
               binary=b"\x00" in head)
    return out


# --- 읽기 -----------------------------------------------------------------------


def _image_or_binary(p: Path, size: int, head: bytes) -> Optional[dict]:
    mime = fsguard.image_type(p, head)
    if mime:
        return {"binary": True, "image": True, "mime": mime,
                "too_large": size > fsguard.MAX_IMAGE_BYTES, "content": ""}
    return None


def _text_fields(enc: dict, text: str, strip_bom: bool, strict: bool) -> dict:
    if strip_bom and text.startswith("﻿"):
        text = text[1:]
    confident = enc["confident"] and strict
    reason = enc["reason"] or (None if strict else "decode_errors")
    out = {"binary": False, "image": False, "content": text, "encoding": enc["encoding"],
           "encoding_confident": confident, "bom": enc["bom"]}
    if reason:
        out["encoding_reason"] = reason
    if enc["suggested"]:
        out["suggested"] = enc["suggested"]
    return out


def _binary_fields(enc: dict) -> dict:
    return {"binary": True, "image": False, "content": "", "encoding": enc["encoding"],
            "encoding_confident": enc["confident"], "bom": enc["bom"]}


def _read_legacy(fd: int, p: Path, size: int, encoding: str) -> dict:
    """구간을 주지 않은 요청 — 옛 응답 모양 그대로(앞 `MAX_BYTES`, `truncated`) + rev·인코딩 필드."""
    head = _pread_full(fd, fsguard.SNIFF_BYTES, 0)
    img = _image_or_binary(p, size, head)
    if img:
        return {**img, "truncated": False}
    raw = head if size <= len(head) else head + _pread_full(fd, max(0, fsguard.MAX_BYTES - len(head)),
                                                            len(head))
    raw = raw[:fsguard.MAX_BYTES]
    truncated = size > len(raw)
    enc = _resolve_encoding(encoding, head, raw, not truncated, True)
    if enc["binary"]:
        return {**_binary_fields(enc), "truncated": False}
    data = raw
    if _is_utf16(enc["encoding"]) and len(data) % 2 and truncated:
        data = data[:-1]
    text, _, strict = _decode(data, enc["codec"], not truncated)
    return {**_text_fields(enc, text, True, strict), "truncated": truncated}


def _read_range(fd: int, p: Path, size: int, encoding: str, offset: int, length: int) -> dict:
    head = _pread_full(fd, fsguard.SNIFF_BYTES, 0)
    img = _image_or_binary(p, size, head)
    if img:
        return {**img, "offset": 0, "length": 0, "next_offset": None, "eof": True,
                "line_truncated": False, "truncated": False}
    if offset > size:
        raise BadRange("offset is past the end of the file")
    window = _pread_full(fd, min(length, size - offset), offset)
    at_eof = offset + len(window) >= size
    # 인코딩은 머리 + 보낼 창으로 정한다. 끝에서 덜 끝난 문자는 아직 모르므로 엄격 판정에서 봐준다.
    enc = _resolve_encoding(encoding, head, window, at_eof, offset == 0)
    if enc["binary"]:
        return {**_binary_fields(enc), "offset": offset, "length": 0, "next_offset": None,
                "eof": True, "line_truncated": False, "truncated": False}
    utf16 = _is_utf16(enc["encoding"])
    if utf16 and offset % 2:
        raise BadRange("UTF-16 offsets must be even")
    line_truncated = False
    data = window
    if not at_eof:
        cut = _rfind_line_end(window, enc["encoding"])
        if cut > 0:
            data = window[:cut]
        else:
            # 창 전체가 한 줄의 일부다. 문자 경계까지만 보내고, 다음 구간이 이 줄을 이어 받는다.
            line_truncated = True
    text, used, strict = _decode(data, enc["codec"], at_eof)
    if not at_eof:
        data = data[:used]
    if not data and window and not at_eof:
        # 창이 문자 하나보다도 짧다(length가 너무 작다) — 진척 없이 같은 offset을 돌려주면 클라이언트가 멈춘다.
        raise BadRange("length is shorter than one character")
    end = offset + len(data)
    out = _text_fields(enc, text, offset == 0, strict)
    out.update(offset=offset, length=len(data), next_offset=None if at_eof else end, eof=at_eof,
               line_truncated=line_truncated, truncated=not at_eof)
    return out


def _read_tail(fd: int, p: Path, size: int, encoding: str, tail: int) -> dict:
    """끝에서 `tail`바이트 — 앞의 잘린 줄은 버리고 줄 경계부터 준다. 줄 번호는 알 수 없다."""
    head = _pread_full(fd, fsguard.SNIFF_BYTES, 0)
    img = _image_or_binary(p, size, head)
    if img:
        return {**img, "offset": 0, "length": 0, "next_offset": None, "eof": True,
                "line_truncated": False, "truncated": False, "tail": True}
    start = max(0, size - tail)
    window = _pread_full(fd, size - start, start)
    enc = _resolve_encoding(encoding, head, window, True, start == 0)
    if enc["binary"]:
        return {**_binary_fields(enc), "offset": start, "length": 0, "next_offset": None,
                "eof": True, "line_truncated": False, "truncated": False, "tail": True}
    line_truncated = False
    if _is_utf16(enc["encoding"]) and start % 2:
        start += 1
        window = window[1:]
    if start > 0:
        first = _find_line_end(window, enc["encoding"])
        if 0 < first < len(window):
            start += first
            window = window[first:]
        else:
            # 창 안에 줄 끝이 없거나 마지막 바이트가 줄 끝이다 — 줄 하나의 꼬리를 그대로 준다.
            line_truncated = True
            if enc["codec"] == "utf-8":
                skip = 0
                while skip < len(window) and skip < 3 and 0x80 <= window[skip] < 0xC0:
                    skip += 1
                start += skip
                window = window[skip:]
    text, _, strict = _decode(window, enc["codec"], True)
    out = _text_fields(enc, text, start == 0, strict)
    out.update(offset=start, length=len(window), next_offset=None, eof=True,
               line_truncated=line_truncated, truncated=False, tail=True)
    return out


def read(p: Path, *, encoding: str = "auto", offset: Optional[int] = None, length: Optional[int] = None,
         tail: Optional[int] = None, expected_rev: Optional[str] = None,
         if_none_match: Optional[str] = None, attempts: int = 3) -> dict:
    """`p`(resolve된 경로)를 `fsguard.open_resolved`로 열어 읽는다.

    돌려주는 것: `{"status": 200, "etag", "body"}` 또는 `{"status": 304, "etag"}`.
    던지는 것: `Changed`(409), `BadRange`(400), `fsguard.NotRegularFile`, `FileNotFoundError`, `OSError`.
    `expected_rev`가 없으면 읽는 도중 바뀐 경우 `attempts`번까지 다시 읽는다(클라이언트에 기준이 없으므로).
    """
    mode = "tail" if tail is not None else ("range" if offset is not None or length is not None else "whole")
    if mode == "range":
        offset = offset or 0
        length = MAX_RANGE_BYTES if length is None else length
    for attempt in range(attempts):
        fd = fsguard.open_resolved(p)
        try:
            before = os.fstat(fd)
            rev = rev_of(before)
            if expected_rev is not None and expected_rev != rev:
                raise Changed(rev)
            etag = make_etag(rev, encoding, mode, offset, length, tail)
            if etag_matches(if_none_match, etag):
                return {"status": 304, "etag": etag, "rev": rev}
            size = before.st_size
            if mode == "whole":
                body = _read_legacy(fd, p, size, encoding)
            elif mode == "range":
                body = _read_range(fd, p, size, encoding, offset, length)
            else:
                body = _read_tail(fd, p, size, encoding, tail)
            after = rev_of(os.fstat(fd))
        finally:
            os.close(fd)
        if after == rev:
            return {"status": 200, "etag": etag, "rev": rev,
                    "body": {"path": str(p), "size": size, "rev": rev, **body}}
        if expected_rev is not None or attempt == attempts - 1:
            raise Changed(after)
    raise AssertionError("unreachable")
