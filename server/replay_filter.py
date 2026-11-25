"""재접속 재생본(scrollback)에서 「한 번만 일어나야 하는 동작」을 빼는 스트림 필터.

재접속하면 서버는 저장해 둔 PTY 출력을 xterm.js에 처음부터 다시 실행시킨다
(`routes/pty.py`의 scrollback 재생). 날것 그대로 저장하면 그 안의 터미널 질의
(DA·DSR·DECRQM·XTVERSION·색 질의 …)에 xterm.js가 재접속할 때마다 다시 답하고,
그 답이 PTY 입력으로 흘러가 프롬프트에 `2026;0$y` 같은 글자로 쌓인다. 클립보드
쓰기(OSC 52)와 알림(BEL)도 재접속마다 다시 일어난다.

그래서 **저장하는 사본만** 이 필터를 거친다. 실시간 출력은 손대지 않는다 — 실시간
질의는 앱이 지금 답을 기다리는 것이라 xterm.js가 답해야 맞다. 규칙은 하나:
재생본에는 화면을 그리는 바이트(글자·색·커서·화면 모드·제목)만 남긴다.

빼는 것(재생할 때만 의미가 없는, 응답·부작용을 부르는 시퀀스):
  CSI  DA1/DA2/DA3(`c`), DSR(`n`, `? n`), DECRQM(`$ p`), XTVERSION(`> q`),
       창 보고 질의(`11/13/14/15/16/18/19/20/21 t`), kitty 키보드 질의(`? u`),
       XTSMGRAPHICS(`? S`), DECREQTPARM(`x`), DECRQPSR(`$ w`), DECRQTSR(`$ u`),
       DECRQCRA(`* y`)
  ESC  DECID(`ESC Z` — DA1과 같은 응답을 부른다)
  OSC  52(클립보드) 전부, 값 자리에 `?`가 있는 질의(OSC 4/10/11/… `;?`)
  DCS  DECRQSS(`$ q`), XTGETTCAP(`+ q`)
  BEL  화면 밖 알림(OSC를 끝내는 BEL은 OSC의 일부라 따로 다룬다)

시퀀스가 청크 경계에서 잘려 와도 상태를 들고 다음 청크에서 이어 판정한다.
판정을 기다리는 동안 붙잡는 바이트는 짧은 상한이 있다 — 큰 OSC/DCS(이미지 등)는
질의일 수 없으므로 상한을 넘으면 그대로 흘려 보낸다.
"""

from __future__ import annotations

import re

ESC = 0x1B
BEL = 0x07
CAN = 0x18
SUB = 0x1A

# CSI 한 개의 최대 길이. xterm도 이보다 긴 CSI는 버린다 — 넘으면 그대로 흘린다.
CSI_MAX = 256
# OSC를 판정하려고 붙잡아 두는 최대 길이. 질의(`10;?`, `4;1;?`, `7501;?`)는 짧다.
OSC_HOLD_MAX = 4096

# 빠른 길 — 출력 대부분은 평문·색·커서 이동이라 바이트 하나씩 보지 않는다. 뺄 **후보**(BEL, 질의일 수 있는
# 최종 바이트의 CSI, 문자열을 여는 ESC, DECID)만 정규식으로 바로 찾고 그 사이는 통째로 복사한다.
_INTEREST = re.compile(
    rb"\x07"
    rb"|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[cnpqtuSxwy]"
    rb"|\x1b[\]P_^XZ]"
    # 시퀀스 도중에 C0 제어 문자(ESC 포함 — 앞 시퀀스를 끊는다)가 끼면 상태 기계가 xterm과 같은 순서로 처리한다
    rb"|\x1b\[[\x20-\x3f]*[\x00-\x1f]"
    rb"|\x1b[\x20-\x2f]*[\x00-\x1f]"
)
# 청크 끝에서 아직 끝나지 않은 ESC·CSI(다음 청크에서 이어진다)
_TAIL_OPEN = re.compile(rb"\x1b(?:\[[\x20-\x3f]*|[\x20-\x2f]*)")
# 한 청크 안에서 끝나는 OSC(종결자 BEL 또는 ST)
_OSC_FULL = re.compile(rb"\x1b\]([^\x07\x1b\x18\x1a]{0,%d})(?:\x07|\x1b\\)" % OSC_HOLD_MAX)
# 문자열 본문을 끝내거나 끊는 바이트
_STR_STOP = re.compile(rb"[\x07\x1b\x18\x1a]")

_GROUND = 0
_ESC = 1        # ESC를 봤다
_ESC_INTER = 2  # ESC + 중간 바이트(0x20-0x2f)들
_CSI = 3
_STR = 4        # OSC/DCS/APC/PM/SOS 본문
_STR_ESC = 5    # 문자열 본문 안에서 ESC를 봤다(ST인지 다음 바이트로 판정)

# 문자열 처리 방식
_HOLD = 0      # 판정 전 — pending에 모은다
_PASS = 1      # 남긴다 — 바로 내보낸다
_DROP = 2      # 뺀다 — 종결자까지 버린다

_REPORT_T = {11, 13, 14, 15, 16, 18, 19, 20, 21}
# 질의일 수 있는 CSI 최종 바이트(_INTEREST의 CSI 갈래와 같아야 한다)
_QUERY_FINALS = frozenset(b"cnpqtuSxwy")


def _csi_is_query(body: bytes) -> bool:
    """`ESC [` 뒤 본문(최종 바이트 포함)이 응답을 부르는 질의인가."""
    final = body[-1:]
    prefix = b""
    i = 0
    if body and body[0] in b"<=>?":
        prefix = body[:1]
        i = 1
    j = i
    while j < len(body) - 1 and 0x30 <= body[j] <= 0x3F:
        j += 1
    params = body[i:j]
    inter = body[j:-1]

    if final == b"c" and not inter:                         # DA1 / DA2(>) / DA3(=)
        return prefix in (b"", b">", b"=")
    if final == b"n" and not inter:                         # DSR, DECXCPR, ?996n …
        return prefix in (b"", b"?")
    if final == b"p" and inter == b"$":                     # DECRQM(ANSI · DEC)
        return prefix in (b"", b"?")
    if final == b"q" and prefix == b">" and not inter:      # XTVERSION
        return True
    if final == b"t" and not prefix and not inter:          # XTWINOPS 보고 질의
        first = params.split(b";", 1)[0]
        return first.isdigit() and int(first) in _REPORT_T
    if final == b"u" and prefix == b"?" and not inter:      # kitty 키보드 질의
        return True
    if final == b"S" and prefix == b"?" and not inter:      # XTSMGRAPHICS
        return True
    if final == b"x" and not prefix and not inter:          # DECREQTPARM
        return True
    if inter == b"$" and final in (b"w", b"u") and not prefix:  # DECRQPSR · DECRQTSR
        return True
    if inter == b"*" and final == b"y" and not prefix:      # DECRQCRA
        return True
    return False


def _osc_decision(head: bytes, complete: bool) -> int:
    """OSC 본문 앞부분으로 남길지 뺄지. 아직 모르면 _HOLD."""
    semi = head.find(b";")
    ps = head if semi < 0 else head[:semi]
    if not ps.isdigit():
        # 번호가 숫자가 아닌 OSC는 우리가 아는 질의가 아니다 — 한 바이트만 봐도 정해진다
        return _PASS if ps else (_HOLD if not complete and semi < 0 else _PASS)
    if semi < 0:
        if not complete and len(ps) < 8:
            return _HOLD
        return _PASS
    if ps == b"52":
        return _DROP
    if not complete:
        return _HOLD
    # 값 자리 어디든 `?` 하나뿐이면 질의(OSC 4;n;? · 10;? · 11;? · 7501;? …)
    if any(seg == b"?" for seg in head[semi + 1:].split(b";")):
        return _DROP
    return _PASS


class ReplayFilter:
    """세션 하나의 출력 스트림을 따라가며, 저장용 사본에서 질의·부작용을 뺀다."""

    __slots__ = ("_state", "_pending", "_kind", "_mode")

    def __init__(self) -> None:
        self._state = _GROUND
        self._pending = bytearray()   # 판정 전 시퀀스 바이트(원문 그대로)
        self._kind = 0                # 문자열 종류: ord(']') ord('P') …
        self._mode = _HOLD

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        pend = self._pending
        st = self._state
        n = len(data)
        i = 0
        while i < n:
            b = data[i]
            if st == _GROUND:
                # 남길 구간(span)은 모아 두었다가 뺄 것을 만났을 때만 한 번에 복사한다.
                span = i
                while True:
                    m = _INTEREST.search(data, i)
                    if m is None:
                        # 끝에 잘린 ESC·CSI가 있으면 거기서부터 상태 기계가 이어받는다
                        t = data.rfind(b"\x1b", max(i, n - CSI_MAX - 2))
                        i = t if t >= 0 and _TAIL_OPEN.fullmatch(data, t) else n
                        break
                    k = m.start()
                    if data[k] == BEL:
                        out += data[span:k]      # 화면 밖 알림 — 재생에서는 뺀다
                        i = span = k + 1
                        continue
                    nxt = data[k + 1] if k + 1 < n else -1
                    if nxt == 0x5B and data[m.end() - 1] in _QUERY_FINALS and m.end() - k <= CSI_MAX:
                        if _csi_is_query(data[k + 2:m.end()]):
                            out += data[span:k]
                            span = m.end()
                        i = m.end()
                        continue
                    if nxt == 0x5D:
                        o = _OSC_FULL.match(data, k)
                        if o is not None:
                            if _osc_decision(o.group(1), True) == _DROP:
                                out += data[span:k]
                                span = o.end()
                            i = o.end()
                            continue
                    # 청크 끝에서 잘렸거나 DCS·긴 OSC·DECID 등 — 아래 상태 기계로 넘긴다
                    i = k
                    break
                out += data[span:i]
                if i >= n:
                    break
                pend.clear()
                pend.append(ESC)
                st = _ESC
                i += 1
                continue

            if b < 0x20 and b != ESC and b != CAN and b != SUB and st in (_ESC, _ESC_INTER, _CSI):
                # xterm은 ESC·CSI 도중의 C0 제어 문자를 그 자리에서 실행하고 시퀀스를 이어 간다.
                # 시퀀스보다 먼저 내보내도 결과가 같다. BEL은 재생에서 뺀다.
                if b != BEL:
                    out.append(b)
                i += 1
                continue

            if st == _ESC:
                pend.append(b)
                i += 1
                if b == 0x5B:                       # '['
                    st = _CSI
                elif b in (0x5D, 0x50, 0x5F, 0x5E, 0x58):   # ] P _ ^ X
                    self._kind = b
                    self._mode = _HOLD if b in (0x5D, 0x50) else _PASS
                    if self._mode == _PASS:
                        out += pend
                        pend.clear()
                    st = _STR
                elif 0x20 <= b <= 0x2F:
                    st = _ESC_INTER
                elif b == ESC:
                    # ESC ESC — xterm은 앞 ESC를 버리고 새로 시작한다. 앞 ESC를 남기면 뒤에서 질의를 뺐을 때
                    # 다음 글자와 붙어 새 질의(`ESC Z` 등)가 생기므로 같이 버린다.
                    del pend[:-1]
                elif b == 0x5A:                     # 'Z' DECID
                    pend.clear()
                    st = _GROUND
                elif b == CAN or b == SUB:
                    # 시퀀스 취소 — xterm은 ESC를 버린다. 취소 바이트만 남긴다.
                    pend.clear()
                    out.append(b)
                    st = _GROUND
                else:
                    out += pend
                    pend.clear()
                    st = _GROUND
                continue

            if st == _ESC_INTER:
                if b == ESC or b == CAN or b == SUB:
                    # 끊긴 시퀀스 — xterm처럼 버린다(남기면 뒤 글자와 붙어 새 시퀀스가 될 수 있다)
                    pend.clear()
                    if b == ESC:
                        pend.append(b)
                        st = _ESC
                    else:
                        out.append(b)
                        st = _GROUND
                    i += 1
                    continue
                pend.append(b)
                i += 1
                if not (0x20 <= b <= 0x2F):
                    out += pend
                    pend.clear()
                    st = _GROUND
                continue

            if st == _CSI:
                if b == ESC or b == CAN or b == SUB:
                    # 중단된 CSI — xterm처럼 버린다(남기면 뒤 글자와 붙어 새 질의가 될 수 있다)
                    pend.clear()
                    if b == ESC:
                        pend.append(b)
                        st = _ESC
                    else:
                        out.append(b)
                        st = _GROUND
                    i += 1
                    continue
                pend.append(b)
                i += 1
                if b >= 0x80:
                    # CSI 안에 올 수 없는 바이트 — 질의가 아니다. 원문 그대로 둔다(빠른 길과 같은 판정).
                    out += pend
                    pend.clear()
                    st = _GROUND
                elif 0x40 <= b <= 0x7E:
                    if not _csi_is_query(bytes(pend[2:])):
                        out += pend
                    pend.clear()
                    st = _GROUND
                elif len(pend) > CSI_MAX:
                    out += pend
                    pend.clear()
                    st = _GROUND
                continue

            if st == _STR:
                # 종결자(BEL·ESC)나 중단(CAN·SUB)이 나올 때까지 본문을 한 번에 처리한다.
                m = _STR_STOP.search(data, i)
                j = n if m is None else m.start()
                if j > i:
                    self._take_str(data[i:j], out)
                    i = j
                    continue
                if b == ESC:
                    # ESC는 아직 어디에도 넣지 않는다 — ST(`ESC \\`)인지 다음 바이트로 판정한다.
                    st = _STR_ESC
                    i += 1
                    continue
                if b == BEL and self._kind == 0x5D:
                    # OSC는 BEL로도 끝난다
                    self._str_append(b, out)
                    self._finish_str(out)
                    st = _GROUND
                    i += 1
                    continue
                if b == BEL:
                    # DCS 등 본문 안의 BEL은 본문 바이트다
                    self._take_str(data[i:i + 1], out)
                    i += 1
                    continue
                # CAN/SUB — 문자열 중단. 판정 못 한 건 원문 그대로 둔다.
                self._abort_str(out)
                out.append(b)
                st = _GROUND
                i += 1
                continue

            # st == _STR_ESC
            if b == 0x5C:                           # ESC '\' = ST
                self._str_append(ESC, out)
                self._str_append(b, out)
                self._finish_str(out)
                st = _GROUND
                i += 1
                continue
            # ST가 아닌 ESC — 문자열은 여기서 끝나고 ESC는 새 시퀀스의 시작이다. 남기는 문자열은 ST를 붙여
            # 닫는다(열린 채 두면 뒤에서 무언가를 뺐을 때 뒤 글자가 본문으로 붙어 `OSC 10;?` 같은 질의가 될 수 있다).
            # 그다음 ESC부터 다시 읽는다(현재 바이트는 소비하지 않는다).
            self._finish_str(out, terminated=False)
            pend.clear()
            pend.append(ESC)
            st = _ESC
            continue

        self._state = st
        return bytes(out)

    # --- 문자열(OSC/DCS …) 도우미 ---

    def _str_append(self, b: int, out: bytearray) -> None:
        if self._mode == _HOLD:
            self._pending.append(b)
        elif self._mode == _PASS:
            out.append(b)

    def _take_str(self, chunk: bytes, out: bytearray) -> None:
        if self._mode == _PASS:
            out += chunk
            return
        if self._mode == _DROP:
            return
        pend = self._pending
        pend += chunk
        self._decide(out, complete=False)

    def _decide(self, out: bytearray, complete: bool) -> None:
        pend = self._pending
        body = bytes(pend[2:])
        if self._kind == 0x5D:
            mode = _osc_decision(body, complete)
        else:  # DCS
            if len(body) < 2 and not complete:
                # 질의인지(`$q`·`+q`) 보려면 본문 앞 두 바이트가 필요하다
                mode = _HOLD
            else:
                mode = _DROP if body[:2] in (b"$q", b"+q") else _PASS
        if mode == _HOLD and len(pend) > OSC_HOLD_MAX:
            mode = _PASS
        if mode == _HOLD:
            return
        self._mode = mode
        if mode == _PASS:
            out += pend
        pend.clear()

    def _finish_str(self, out: bytearray, terminated: bool = True) -> None:
        mode = self._mode
        if mode == _HOLD:
            pend = self._pending
            # 종결자를 빼고 본문만으로 판정한다
            term_len = 0
            if terminated:
                term_len = 1 if pend[-1:] == bytes([BEL]) else 2
            body_end = len(pend) - term_len
            body = bytes(pend[2:body_end])
            if self._kind == 0x5D:
                mode = _osc_decision(body, True)
            else:
                mode = _DROP if body[:2] in (b"$q", b"+q") else _PASS
            if mode != _DROP:
                out += pend
            pend.clear()
        if not terminated and mode == _PASS:
            out += b"\x1b\\"
        self._mode = _HOLD
        self._kind = 0

    def _abort_str(self, out: bytearray) -> None:
        if self._mode == _HOLD:
            out += self._pending
        self._pending.clear()
        self._mode = _HOLD
        self._kind = 0
