"""Local Mic — MacBook 로컬 마이크로 push-to-talk. 브라우저 없이 동작.

sounddevice로 녹음 → WAV → voice_handler.transcribe → 활성 세션 PTY 입력.
"""

import asyncio
import io
import logging
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


def start_recording() -> dict:
    """로컬 마이크 녹음 시작 (push-to-talk)."""
    global _recording, _audio_frames, _stream

    if _recording:
        return {"status": "already_recording"}

    _audio_frames = []
    _recording = True

    def callback(indata, frames, time_info, status):
        if _recording:
            _audio_frames.append(indata.copy())

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
    global _recording, _stream

    if not _recording:
        return {"status": "not_recording", "text": ""}

    _recording = False
    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None

    if not _audio_frames:
        return {"status": "empty", "text": ""}

    # numpy → WAV bytes
    audio_data = np.concatenate(_audio_frames, axis=0)
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
