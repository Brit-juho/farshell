"""Local Mic — MacBook 로컬 마이크로 push-to-talk. 브라우저 없이 동작.

sounddevice로 녹음 → WAV → voice_handler.transcribe → 활성 세션 PTY 입력.
"""

import asyncio
import io
import logging
import os
import struct
import wave
from typing import Optional

import sounddevice as sd
import numpy as np

import voice_handler

logger = logging.getLogger(__name__)

_recording = False
_audio_frames: list[np.ndarray] = []
_sample_rate = 16000
_stream: Optional[sd.InputStream] = None

# stop을 놓친(브라우저 종료/네트워크 끊김) 녹음이 초당 32KB씩 무한정 쌓여 RSS가 GB로
# 폭주하는 것을 막는 상한. 초과분은 버리고 앞부분(최초 N초)만 유지 → 메모리 bounded.
# VT_MAX_RECORD_SEC로 조정(0=무제한).
try:
    _MAX_RECORD_SEC = float(os.environ.get("VT_MAX_RECORD_SEC", "120"))
except ValueError:
    _MAX_RECORD_SEC = 120.0


def start_recording() -> dict:
    """로컬 마이크 녹음 시작 (push-to-talk)."""
    global _recording, _audio_frames, _stream

    if _recording:
        return {"status": "already_recording"}

    _audio_frames = []
    _recording = True

    max_samples = int(_MAX_RECORD_SEC * _sample_rate) if _MAX_RECORD_SEC > 0 else 0
    _capped = {"n": 0, "warned": False}

    def callback(indata, frames, time_info, status):
        if not _recording:
            return
        if max_samples and _capped["n"] >= max_samples:
            if not _capped["warned"]:
                logger.warning(
                    f"local mic {_MAX_RECORD_SEC:.0f}s 상한 도달 — 이후 입력 버림 (메모리 폭주 방어)"
                )
                _capped["warned"] = True
            return
        _audio_frames.append(indata.copy())
        _capped["n"] += len(indata)

    _stream = sd.InputStream(
        samplerate=_sample_rate,
        channels=1,
        dtype="int16",
        callback=callback,
    )
    _stream.start()
    logger.info("Local mic recording started")
    return {"status": "recording"}


async def stop_recording() -> dict:
    """녹음 종료 → WAV 변환 → Whisper STT."""
    global _recording, _stream, _audio_frames

    if not _recording:
        return {"status": "not_recording", "text": ""}

    _recording = False
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None

    if not _audio_frames:
        return {"status": "empty", "text": ""}

    # 전역 버퍼를 로컬로 넘기고 즉시 비운다 — STT 진행 중 다음 녹음이 이전 오디오를 물지 않게.
    frames, _audio_frames = _audio_frames, []
    audio_data = np.concatenate(frames, axis=0)
    del frames
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(_sample_rate)
        wf.writeframes(audio_data.tobytes())

    wav_bytes = buf.getvalue()
    logger.info(f"Local mic: captured {len(audio_data)} samples, {len(wav_bytes)} bytes WAV")

    # STT
    text = await voice_handler.transcribe(wav_bytes, input_format="wav")
    return {"status": "ok", "text": text}
