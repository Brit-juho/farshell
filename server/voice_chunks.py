"""PCM 청크 수집기 — 긴 녹음을 조각으로 받아 끊김에서도 이어 붙인다.

브라우저가 16kHz mono PCM16(LE)을 약 0.5초 단위로 `POST /voice/chunk`로 올리면 여기서 seq 순서로 이어 붙인다.
순서가 뒤바뀐 조각은 앞 seq가 채워질 때까지 보류하고, 이미 받은 seq는 무시하되 ack는 다시 돌려준다
(응답만 유실돼 클라이언트가 같은 seq를 다시 보내는 경우). final 표시가 온 seq까지 빠짐없이 모이면 완결이다.

- 이 모듈은 상태만 다룬다(네트워크·STT 없음, await 없음). 모든 변경이 이벤트 루프의 한 번에 끝나므로 락이 없어도
  요청 사이에 상태가 갈라지지 않는다. 인식·주입은 routes/voice.py가 기존 /voice/input 파이프라인으로 넘긴다.
- 소유자(principal)당 진행 중 녹음은 1건이다. 새 녹음이 시작되면 이전 열린 녹음은 폐기된다(STT가 직렬이다).
- 마지막 요청 후 IDLE_TTL초가 지나면 폐기하고, 폐기·취소한 id는 잠깐 묘비로 남겨 늦게 온 조각이
  새 녹음으로 오인되지 않게 한다.
- 녹음 중 처리(서버 VAD 자동 종료·부분 인식)는 voice_live.py가 `Recording.live`에 붙어서 한다. 여기서는 기록을 버리거나
  끝낼 때 `live.close()`를 불러 그 백그라운드 작업을 정리하는 것만 책임진다(asyncio를 import하지 않는다).
"""
from __future__ import annotations

import io
import time
import wave
from typing import Optional

from i18n import tr

SAMPLE_RATE = 16000
BYTES_PER_SEC = SAMPLE_RATE * 2              # PCM16 mono

MAX_TOTAL_BYTES = 60 * BYTES_PER_SEC         # 녹음 전체 상한 60초(약 1.9MB) — 엄격
MAX_CHUNK_BYTES = 5 * BYTES_PER_SEC          # 조각 하나 최대 5초분
MAX_PENDING_BYTES = 30 * BYTES_PER_SEC       # 앞 seq가 빈 채 쌓아 둘 수 있는 보류분 상한
MAX_PENDING_CHUNKS = 1000
MAX_SEQ = 4000                               # 조각 개수 상한(0.5초 조각이면 60초에 120개 — 아주 넉넉한 값)
IDLE_TTL = 30.0                              # 요청이 없으면 폐기
DONE_TTL = 120.0                             # 완료·폐기 기록 보관(재전송 응답용, /voice/input의 중복 방지와 같은 길이)
MAX_TOMBSTONES = 1024
MAX_RECORDINGS = 64                          # 동시에 들고 있는 기록 수 상한(소유자 1건 규칙 + 완료 기록 포함)


class ChunkError(Exception):
    """클라이언트에 그대로 돌려줄 오류. status는 HTTP 코드, code는 기계가 읽는 이름."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Recording:
    __slots__ = ("voice_id", "owner", "token", "parts", "pending", "pending_bytes", "total_bytes",
                 "final_seq", "state", "result", "created", "last", "future", "live", "confirm_ui")

    def __init__(self, voice_id: str, owner: str, token: str, now: float):
        self.voice_id, self.owner, self.token = voice_id, owner, token
        self.parts: list[bytes] = []            # parts[seq] — 0..next_seq-1이 빠짐없이 이어 붙은 조각
        self.pending: dict[int, bytes] = {}     # 앞 seq가 비어 보류 중인 조각
        self.pending_bytes = 0
        self.total_bytes = 0                    # 받은 서로 다른 조각의 합(보류 포함)
        self.final_seq: Optional[int] = None
        self.state = "open"                     # open → finishing → done / (폐기는 묘비로)
        self.result: Optional[dict] = None
        self.created = self.last = now
        self.future = None                      # finishing 동안 결과를 기다리는 asyncio.Future(routes가 만든다)
        self.live = None                        # voice_live.Live — 녹음 중 VAD·부분 인식(routes가 붙인다)
        self.confirm_ui = False                 # 클라이언트가 X-Voice-Confirm: 1을 보냈는가(routes가 켠다 — 확인 후 전송)

    @property
    def next_seq(self) -> int:
        return len(self.parts)

    @property
    def ack_seq(self) -> int:
        """이어 붙은 마지막 seq(누적 ack). 하나도 없으면 -1."""
        return len(self.parts) - 1

    def contiguous_pcm(self) -> bytes:
        """지금까지 빠짐없이 이어 붙은 PCM의 복사본."""
        return b"".join(self.parts)

    def tail_pcm(self, max_bytes: int) -> tuple:
        """이어 붙은 PCM의 끝 max_bytes 이하(조각 경계 단위)의 복사본과 잘렸는지 여부. 부분 인식 스냅샷용 —
        전체(최대 1.9MB)를 매번 복사하지 않게 필요한 끝 조각만 붙인다."""
        picked, size = [], 0
        for part in reversed(self.parts):
            if size + len(part) > max_bytes and picked:
                break
            picked.append(part)
            size += len(part)
        return b"".join(reversed(picked)), len(picked) < len(self.parts)

    def complete(self) -> bool:
        return self.final_seq is not None and self.next_seq == self.final_seq + 1


class Outcome:
    """accept()의 결과. kind: ack(더 받아야 함) · complete(호출자가 인식을 돌려야 함) · finishing · done."""
    __slots__ = ("kind", "ack_seq", "rec")

    def __init__(self, kind: str, ack_seq: int, rec: Recording):
        self.kind, self.ack_seq, self.rec = kind, ack_seq, rec


_recordings: dict[tuple[str, str], Recording] = {}
_by_owner: dict[str, str] = {}                          # owner → 열린 녹음의 voice_id
_tombstones: dict[tuple[str, str], tuple[float, str]] = {}   # 폐기·취소한 id → (시각, 이유)


def _clock() -> float:
    return time.monotonic()


def _close_live(rec: Recording) -> None:
    live, rec.live = rec.live, None
    if live is not None:
        try:
            live.close()
        except Exception:                           # 정리 실패가 기록 정리를 막지 않게
            pass


def reset() -> None:
    """모든 기록을 버린다(테스트·서버 종료). 녹음 중 작업도 함께 정리한다."""
    for rec in list(_recordings.values()):
        _close_live(rec)
    _recordings.clear(); _by_owner.clear(); _tombstones.clear()


def peek(owner: str, voice_id: str) -> Optional[Recording]:
    return _recordings.get((owner, voice_id))


def _forget(rec: Recording, reason: Optional[str], now: float) -> None:
    """기록을 버린다. reason이 있으면 묘비를 남긴다. 조각 메모리는 즉시 놓는다."""
    key = (rec.owner, rec.voice_id)
    if _recordings.get(key) is rec:
        del _recordings[key]
    if _by_owner.get(rec.owner) == rec.voice_id:
        del _by_owner[rec.owner]
    _close_live(rec)
    rec.parts, rec.pending = [], {}
    rec.pending_bytes = 0
    if reason:
        _bury(key, reason, now)


def _bury(key: tuple[str, str], reason: str, now: float) -> None:
    _tombstones[key] = (now, reason)
    while len(_tombstones) > MAX_TOMBSTONES:        # 인증된 호출자가 취소를 난사해도 무한히 늘지 않게
        del _tombstones[next(iter(_tombstones))]


def sweep(now: Optional[float] = None) -> int:
    """만료된 기록·묘비를 치운다. 요청마다, 그리고 주기 작업에서 부른다."""
    now = _clock() if now is None else now
    n = 0
    for rec in list(_recordings.values()):
        if rec.state == "open" and now - rec.last > IDLE_TTL:
            _forget(rec, "expired", now); n += 1
        elif rec.state == "done" and now - rec.last > DONE_TTL:
            _forget(rec, None, now); n += 1
        elif rec.state == "finishing" and now - rec.last > DONE_TTL + 120:
            _forget(rec, None, now); n += 1         # 인식이 비정상적으로 멈춘 경우의 안전망
    for key in [k for k, (ts, _) in _tombstones.items() if now - ts > DONE_TTL]:
        del _tombstones[key]
    return n


async def sweep_loop(interval: float = 10.0) -> None:
    import asyncio
    while True:
        await asyncio.sleep(interval)
        try:
            sweep()
        except Exception:                           # 청소가 죽어 누수가 생기지 않게 삼킨다
            pass


def parse_seq(raw: str) -> int:
    """seq는 0..MAX_SEQ-1의 십진 정수 문자열만 받는다(부호·공백·소수·지수 표기·거대 값 거부)."""
    if not raw or not raw.isascii() or not raw.isdigit() or len(raw) > 6:
        raise ChunkError(400, "bad_seq", tr("청크 번호(seq)가 올바르지 않습니다"))
    n = int(raw)
    if n >= MAX_SEQ:
        raise ChunkError(400, "bad_seq", tr("청크 번호(seq)가 올바르지 않습니다"))
    return n


def accept(voice_id: str, owner: str, token: str, seq: int, data: bytes, final: bool,
           now: Optional[float] = None) -> Outcome:
    """조각 하나를 반영한다. 검증·상한 위반은 ChunkError(위반한 녹음은 폐기)."""
    now = _clock() if now is None else now
    sweep(now)
    key = (owner, voice_id)
    if key in _tombstones:
        raise ChunkError(410, "voice_expired", tr("녹음이 만료되거나 취소됐습니다. 새로 녹음해 주세요"))

    if len(data) % 2:
        raise ChunkError(400, "odd_length", tr("오디오 길이가 올바르지 않습니다"))
    if len(data) > MAX_CHUNK_BYTES:
        raise ChunkError(413, "chunk_too_large", tr("청크가 너무 큽니다"))
    if not data and not final:
        raise ChunkError(400, "empty_chunk", tr("빈 청크입니다"))

    rec = _recordings.get(key)
    if rec is not None and rec.token != token:
        # 같은 녹음 중에 입력 대상이 바뀌는 일은 없다 — 다른 pane으로 새지 않게 거부한다.
        raise ChunkError(409, "target_mismatch", tr("입력 대상이 다릅니다"))
    if rec is not None and rec.state == "done":
        rec.last = now
        return Outcome("done", rec.final_seq if rec.final_seq is not None else -1, rec)
    if rec is not None and rec.state == "finishing":
        rec.last = now
        return Outcome("finishing", rec.final_seq if rec.final_seq is not None else -1, rec)

    if rec is None:
        if len(_recordings) >= MAX_RECORDINGS:
            raise ChunkError(429, "too_many", tr("음성 요청이 너무 많습니다"))
        prev_id = _by_owner.get(owner)
        if prev_id is not None:                      # 소유자당 1건: 이전 열린 녹음은 폐기
            prev = _recordings.get((owner, prev_id))
            if prev is not None and prev.state == "open":
                _forget(prev, "replaced", now)
        rec = Recording(voice_id, owner, token, now)
        _recordings[key] = rec
        _by_owner[owner] = voice_id
    rec.last = now

    try:
        _store(rec, seq, data, final)
    except ChunkError:
        _forget(rec, "rejected", now)
        raise

    if rec.complete():
        if rec.total_bytes == 0:
            _forget(rec, "rejected", now)
            raise ChunkError(400, "empty_recording", tr("녹음된 소리가 없습니다"))
        rec.state = "finishing"
        _close_live(rec)                             # 최종 인식이 맡는다 — 진행 중 부분 인식 결과는 버린다
        if _by_owner.get(owner) == voice_id:        # 인식이 도는 동안 같은 소유자의 새 녹음을 막지 않는다
            del _by_owner[owner]
        return Outcome("complete", rec.ack_seq, rec)
    return Outcome("ack", rec.ack_seq, rec)


def _store(rec: Recording, seq: int, data: bytes, final: bool) -> None:
    if final:
        if rec.final_seq is not None and rec.final_seq != seq:
            raise ChunkError(409, "final_conflict", tr("마지막 청크가 이미 지정됐습니다"))
        if max(rec.next_seq - 1, max(rec.pending, default=-1)) > seq:
            raise ChunkError(409, "final_conflict", tr("마지막 청크 뒤에 청크가 있습니다"))
        rec.final_seq = seq
    elif rec.final_seq is not None and seq > rec.final_seq:
        raise ChunkError(409, "after_final", tr("마지막 청크 뒤에 청크가 있습니다"))

    if seq < rec.next_seq:                           # 이미 이어 붙인 seq — 내용이 같으면 무시(ack만 다시)
        if rec.parts[seq] != data:
            raise ChunkError(409, "seq_conflict", tr("같은 번호에 다른 오디오가 도착했습니다"))
        return
    if seq in rec.pending:
        if rec.pending[seq] != data:
            raise ChunkError(409, "seq_conflict", tr("같은 번호에 다른 오디오가 도착했습니다"))
        return

    if rec.total_bytes + len(data) > MAX_TOTAL_BYTES:
        raise ChunkError(413, "too_long", tr("녹음이 너무 깁니다 (최대 60초)"))
    if seq != rec.next_seq:                          # 앞 seq가 빔 — 보류
        if (len(rec.pending) + 1 > MAX_PENDING_CHUNKS
                or rec.pending_bytes + len(data) > MAX_PENDING_BYTES
                or seq - rec.next_seq > MAX_PENDING_CHUNKS):
            raise ChunkError(409, "too_many_pending", tr("누락된 청크가 너무 많습니다"))
        rec.pending[seq] = data
        rec.pending_bytes += len(data)
        rec.total_bytes += len(data)
        return
    rec.parts.append(data)
    rec.total_bytes += len(data)
    while rec.next_seq in rec.pending:               # 보류분 중 이제 이어지는 것을 끌어온다
        nxt = rec.pending.pop(rec.next_seq)
        rec.pending_bytes -= len(nxt)
        rec.parts.append(nxt)


def take_pcm(rec: Recording) -> bytes:
    """완결된 녹음의 PCM 전체(복사본). 인식이 실패해도 다시 시도할 수 있게 조각은 finish/reopen 때까지 둔다."""
    return b"".join(rec.parts)


def finish(rec: Recording, result: dict, now: Optional[float] = None) -> None:
    """인식이 끝났다 — 조각 메모리는 놓고 결과만 DONE_TTL 동안 남긴다(재전송 응답용)."""
    _close_live(rec)
    rec.state = "done"
    rec.result = result
    rec.parts, rec.pending, rec.pending_bytes = [], {}, 0
    rec.future = None
    rec.last = _clock() if now is None else now


def reopen(rec: Recording, now: Optional[float] = None) -> None:
    """인식이 예외(대기열 가득 등)로 끝났다 — 같은 조각으로 final을 다시 받을 수 있게 열린 상태로 되돌린다."""
    if _recordings.get((rec.owner, rec.voice_id)) is not rec:
        return
    rec.state = "open"
    rec.future = None
    rec.last = _clock() if now is None else now
    if rec.owner not in _by_owner:
        _by_owner[rec.owner] = rec.voice_id


def cancel(voice_id: str, owner: str, now: Optional[float] = None) -> str:
    """취소. 반환: cancelled · finishing(이미 인식 중이라 못 멈춤) · unknown(없음 — 멱등)."""
    now = _clock() if now is None else now
    rec = _recordings.get((owner, voice_id))
    if rec is not None and rec.state in ("finishing", "done"):
        return "finishing" if rec.state == "finishing" else "unknown"
    if rec is not None:
        _forget(rec, "cancelled", now)
    else:
        _bury((owner, voice_id), "cancelled", now)   # 아직 안 도착한 조각이 새 녹음을 만들지 않게
    return "cancelled"


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()
