"""녹음 중 처리 — 서버 VAD 자동 종료와 부분 인식. 청크 녹음 한 건(voice_chunks.Recording)마다 `Live` 하나가 붙는다.

routes/voice.py가 청크를 받아 수집기에 반영한 뒤 `Live.advance(rec)`를 부르고, 돌려받은 값(`endpoint`·`partial`)을
청크 응답에 싣는다. 수집기가 기록을 끝내거나 버릴 때(final 완결·취소·만료·교체·서버 종료) `Live.close()`를 부른다.

- **자동 종료**(설정 voice.autoStop, 기본 off): VAD가 발화를 확정한 뒤 침묵이 이어져 끝점을 내면 `endpoint: true`.
  클라이언트가 이를 받아 사용자가 「녹음 종료」를 누른 것과 같이 final을 보낸다. 서버는 녹음을 스스로 끝내지 않는다
  (마지막 조각은 클라이언트에만 있다).
- **부분 인식**(설정 voice.partialResults, 기본 on): 발화가 확정된 뒤 새 오디오가 간격만큼 늘 때마다 최근 창을 STT에
  돌려 결과를 `partial`로 싣는다. 표시용일 뿐 pane에 입력하지 않는다. 원칙:
  - 엔진을 기다리지 않는다(stt_service.transcribe_partial — 바쁘면 건너뜀, 모델을 올리지 않음, 줄 수에 안 들어감).
  - 녹음 하나에 진행 중 부분 인식은 최대 1건. final이 오거나 녹음이 버려지면 진행 중 결과는 버린다(세대 번호).
  - 실패해도 녹음은 계속한다. 텍스트는 로그에 남기지 않는다(길이·시간만).
- 이 모듈이 던진 예외는 청크 수신을 막지 않는다(routes가 삼킨다).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import stt_service
import voice_chunks
from voice_vad import EnergyVad

log = logging.getLogger(__name__)

SETTING_AUTO_STOP = "voice.autoStop"
SETTING_PARTIAL = "voice.partialResults"

BYTES_PER_SEC = voice_chunks.BYTES_PER_SEC
# 부분 인식 간격: 새 오디오가 1.5초 이상 늘었을 때. 0.5초 조각 세 개 — 화면이 "따라오는" 느낌을 주면서 엔진을 덜 쓴다.
PARTIAL_INTERVAL_SEC = 1.5
# 부분 인식 창: 최근 20초만. 전체를 매번 돌리면 60초 녹음 끝무렵 한 번에 60초를 인식해야 해 비용이 녹음 길이의
# 제곱으로 늘고, 그동안 엔진을 잡아 다른 인식을 오래 막는다. 20초면 보통 한두 문장이라 대개 전체이고, 넘으면 앞을
# 잘라 「…」로 표시한다(최종 결과는 어차피 전체를 다시 인식한다). 스냅샷 복사도 최대 640KB로 묶인다.
PARTIAL_WINDOW_SEC = 20
# 점유율 상한: 다음 부분 인식까지의 오디오 간격을 (직전 부분 인식에 걸린 시간 × 3) 이상으로 늘린다. 느린 엔진
# (CPU llama.cpp 등)에서 부분 인식이 엔진 시간의 1/3을 넘지 않게 해 최종 인식·다른 사용자가 기다리는 시간을 줄인다.
PARTIAL_DUTY_FACTOR = 3.0
PARTIAL_MAX_CHARS = 500         # 응답에 싣는 부분 텍스트 상한(20초 발화는 보통 100~200자)


def read_options() -> dict:
    """설정(workspace.json의 settings, 프런트 core/settings.js 스키마와 같은 키)을 읽는다. 블로킹 — 스레드에서 부른다.
    녹음 시작에 한 번 읽어 그 녹음 동안 고정한다(중간에 바꿔도 다음 녹음부터)."""
    try:
        import workspace
        s = workspace.load().get("settings") or {}
    except Exception:
        s = {}
    return {"auto_stop": s.get(SETTING_AUTO_STOP) is True,          # 기본 off — 명시적으로 켠 경우만
            "partial": s.get(SETTING_PARTIAL) is not False}        # 기본 on — 명시적으로 끈 경우만 끈다


def stt_language(query_lang: str) -> Optional[str]:
    """voice_handler.transcribe_timed와 같은 언어 결정(요청 lang → VT_STT_LANG → 자동)."""
    return (query_lang or "").strip() or os.environ.get("VT_STT_LANG", "").strip() or None


class Live:
    __slots__ = ("auto_stop", "partial_on", "lang", "vad", "fed_seq", "closed", "task", "gen", "partial",
                 "_last_bytes", "_last_speech", "_interval_bytes", "_fed_bytes")

    def __init__(self, opts: dict, lang: Optional[str] = None):
        self.auto_stop = bool(opts.get("auto_stop"))
        self.partial_on = bool(opts.get("partial"))
        self.lang = lang
        self.vad = EnergyVad() if (self.auto_stop or self.partial_on) else None
        self.fed_seq = -1               # VAD에 넣은 마지막 seq(이어 붙은 순서대로만 넣는다)
        self._fed_bytes = 0
        self.closed = False
        self.task: Optional[asyncio.Task] = None
        self.gen = 0                    # 부분 인식 세대 — close·새 시작마다 올려 늦게 끝난 결과를 버린다
        self.partial: Optional[dict] = None
        self._last_bytes = 0            # 마지막 부분 인식을 시작한 시점의 오디오 바이트
        self._last_speech = 0           # 그때의 누적 발화 ms
        self._interval_bytes = int(PARTIAL_INTERVAL_SEC * BYTES_PER_SEC)

    # ── 청크마다 ─────────────────────────────────────────────
    def advance(self, rec) -> dict:
        """새로 이어 붙은 조각을 VAD에 넣고, 필요하면 부분 인식을 시작하고, 응답에 붙일 값을 돌려준다."""
        if self.closed or self.vad is None or rec.state != "open":
            return {}
        while self.fed_seq < rec.ack_seq:
            self.fed_seq += 1
            part = rec.parts[self.fed_seq]
            self.vad.feed(part)
            self._fed_bytes += len(part)
        if self.partial_on:
            self._maybe_partial(rec)
        out: dict = {}
        if self.auto_stop and self.vad.endpoint:
            out["endpoint"] = True
        if self.partial is not None:
            out["partial"] = dict(self.partial)
        return out

    def _maybe_partial(self, rec) -> None:
        if self.task is not None and not self.task.done():
            return                                   # 진행 중이면 새로 시작하지 않는다
        if not self.vad.confirmed:
            return                                   # 말이 없으면 돌리지 않는다(무음에서 지어낸 글자 방지·비용)
        if self._fed_bytes - self._last_bytes < self._interval_bytes:
            return
        if self.vad.speech_ms <= self._last_speech:
            return                                   # 지난번 이후 새 발화가 없으면 결과도 같다
        pcm, clipped = rec.tail_pcm(PARTIAL_WINDOW_SEC * BYTES_PER_SEC)
        if not pcm:
            return
        prev = (self._last_bytes, self._last_speech)
        self._last_bytes, self._last_speech = self._fed_bytes, self.vad.speech_ms
        self.gen += 1
        task = asyncio.get_running_loop().create_task(
            self._run(rec, pcm, rec.ack_seq, clipped, self.gen, prev))
        self.task = task
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    async def _run(self, rec, pcm: bytes, seq: int, clipped: bool, gen: int, prev: tuple) -> None:
        t0 = time.perf_counter()
        audio_ms = len(pcm) * 1000 // BYTES_PER_SEC
        try:
            wav = voice_chunks.to_wav(pcm)
            del pcm                                  # 스냅샷은 WAV 사본만 남긴다
            text = await asyncio.to_thread(stt_service.transcribe_partial, wav, self.lang)
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # 부분 인식 실패는 녹음에 영향이 없다
            if gen == self.gen and not self.closed:
                self._last_bytes, self._last_speech = prev     # 다음 조각에서 다시 시도
            log.info("voice.partial failed audio_ms=%d err=%s", audio_ms, type(e).__name__)
            return
        elapsed = time.perf_counter() - t0
        if text is None:                             # 엔진이 바빠 건너뜀 — 다음 조각에서 다시 시도
            if gen == self.gen and not self.closed:
                self._last_bytes, self._last_speech = prev
            log.debug("voice.partial skipped (engine busy or not loaded) audio_ms=%d", audio_ms)
            return
        self._interval_bytes = max(int(PARTIAL_INTERVAL_SEC * BYTES_PER_SEC),
                                   int(elapsed * PARTIAL_DUTY_FACTOR * BYTES_PER_SEC))
        if self.closed or gen != self.gen or rec.state != "open":
            return                                   # final이 왔거나 녹음이 버려졌다 — 결과를 버린다
        text = " ".join(str(text).split())[:PARTIAL_MAX_CHARS]
        self.partial = {"text": text, "seq": seq, "clipped": clipped}
        log.info("voice.partial audio_ms=%d stt_ms=%d chars=%d", audio_ms, round(elapsed * 1000), len(text))

    def close(self) -> None:
        """멱등. 진행 중 부분 인식 작업을 취소하고 결과를 버린다. 엔진 스레드는 멈출 수 없어 끝까지 돌지만 결과는 버려진다."""
        self.closed = True
        self.gen += 1
        self.partial = None
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()


_tasks: "set[asyncio.Task]" = set()


def pending_tasks() -> int:
    """진행 중인 부분 인식 작업 수(테스트·진단용)."""
    return sum(1 for t in _tasks if not t.done())
