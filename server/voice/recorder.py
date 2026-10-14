"""마이크 녹음 + STT 호출 + tmux 주입 통합."""
import threading
import time

import numpy as np
import sounddevice as sd

import platform_utils

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, TTS_CONFIRM, VOICE_TARGET_NOTIFY, logger
from .stt import transcribe
from . import server_client
import secrets
import json
import io
import wave
import asyncio
import voice_handler
import voice_targets
from .tmux_target import pane_label, resolve_voice_target_pane, send_to_tmux
from i18n import tr

_recording = False
_audio_frames: list[np.ndarray] = []
_stream: sd.InputStream | None = None
_lock = threading.Lock()
# 토글 디바운스 — 미디어 키 중복 발생/경쟁 등 어떤 이중 이벤트에도 rapid on/off를 막는다.
_last_toggle = 0.0
_target = None
_processing = False
TOGGLE_DEBOUNCE_SEC = 0.25


def is_recording() -> bool:
    return _recording


def _notify_target():
    """녹음 시작에 고정한 대상을 안내한다."""
    try:
        pane = (_target or {}).get("pane")
        if not pane:
            return
        label = pane_label(pane) or pane
        prefix = "🔒 " if (_target or {}).get("mode", "").startswith("lock:") else ""
        platform_utils.notify(tr("🎙 음성 입력"), f"{prefix}→ {label}")
    except Exception as e:
        logger.debug(f"타깃 알림 실패: {e}")


def start_recording():
    global _recording, _audio_frames, _stream, _target
    with _lock:
        if _recording or _processing:
            return
        pane, mode = resolve_voice_target_pane()
        if not pane:
            platform_utils.notify(tr("음성 입력"), tr("입력 대상 tmux pane이 없습니다"))
            return
        try:
            _target = voice_targets.snapshot("", pane) if server_client.independent() else server_client.target(pane)
        except Exception as e:
            platform_utils.notify(tr("음성 준비 실패"), str(e)[:200])
            return
        _target["mode"] = mode
        _audio_frames = []
        _recording = True

    # 상한 프레임 수 — MAX_RECORDING_SECONDS 초과 시 자동 종료(끄기 토글 유실 방어).
    max_frames = int(MAX_RECORDING_SECONDS * SAMPLE_RATE) if MAX_RECORDING_SECONDS > 0 else 0
    captured = 0

    def callback(indata, frames, time_info, status):
        nonlocal captured
        if not _recording:
            return
        _audio_frames.append(indata.copy())
        captured += len(indata)
        if max_frames and captured >= max_frames:
            # 콜백 스레드에서 직접 stop하면 sounddevice가 데드락날 수 있어 별도 스레드로.
            logger.warning(
                f"녹음 {MAX_RECORDING_SECONDS:.0f}s 상한 도달 — 강제 종료 (메모리 폭주 방어)"
            )
            threading.Thread(target=stop_recording_and_process, daemon=True).start()

    # 방어: desync로 남은 이전 스트림이 있으면 먼저 닫아 누수 차단.
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None

    try:
        _stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback
        )
        _stream.start()
    except Exception as e:
        with _lock:
            _recording = False
            _audio_frames = []
        if _stream:
            try:
                _stream.close()
            except Exception:
                pass
            _stream = None
        if not server_client.independent():
            try:
                server_client.request('/voice/cancel', json.dumps({'server': True, 'pause': False}).encode())
            except Exception:
                pass
        platform_utils.notify(tr('녹음 시작 실패'), str(e)[:200])
        return
    logger.info("🎙 녹음 시작")
    platform_utils.play_sound("start")
    if VOICE_TARGET_NOTIFY:
        _notify_target()


def stop_recording_and_process():
    global _recording, _stream, _audio_frames, _processing
    with _lock:
        if not _recording:
            return
        _recording = False
        _processing = True

    stream, _stream = _stream, None
    if stream:
        try:
            stream.stop()
        except Exception as e:
            logger.warning('녹음 스트림 종료 실패: %s', e)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    platform_utils.play_sound("stop")

    if not _audio_frames:
        logger.info("녹음 데이터 없음")
        _processing = False
        if not server_client.independent():
            try:
                server_client.request("/voice/cancel", json.dumps({"server": True, "pause": False}).encode())
            except Exception:
                pass
        return

    # 프레임 리스트를 로컬로 넘기고 전역 버퍼는 즉시 비운다 — 다음 녹음 전까지 GB 단위
    # 오디오를 붙들지 않도록. concatenate 결과(audio)만 STT까지 살아 있으면 충분.
    frames, _audio_frames = _audio_frames, []
    audio = np.concatenate(frames, axis=0)
    del frames
    logger.info(f"녹음 종료: {len(audio)/SAMPLE_RATE:.1f}초")

    try:
        if server_client.independent():
            text = transcribe(audio)
            if text and voice_targets.valid(_target):
                send_to_tmux(_target['pane'], text)
        else:
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE); w.writeframes(audio.tobytes())
            result = server_client.request('/voice/input', buf.getvalue(), 'audio/wav',
                {'X-Voice-Id': secrets.token_hex(16), 'X-Voice-Target': _target['token']}, timeout=130)
            text = result.get('text', '')
            if result.get('error') or (text and not result.get('injected')):
                raise RuntimeError(result.get('error') or tr('입력되지 않았습니다'))
        if text:
            logger.info('STT → %s: %s', _target['pane'], text)
            if TTS_CONFIRM and server_client.independent():
                # 이 프로세스의 플레이어만 사용한다. 기본은 꺼짐.
                from voice_sessions import VoiceCoordinator
                async def confirm():
                    item = await voice_handler.synthesize_result(text, engine='local')
                    await VoiceCoordinator()._play_server({}, item)
                asyncio.run(confirm())
    except Exception as e:
        logger.error('STT 실패: %s', e)
        platform_utils.notify(tr('음성 입력 실패'), str(e)[:200])
    finally:
        _processing = False
        if not server_client.independent():
            try:
                server_client.request('/voice/cancel', json.dumps({'server': True, 'pause': False}).encode())
            except Exception:
                pass


def toggle_recording():
    """핫키와 이어폰 버튼 공통 진입점. 원자적 결정 + 디바운스."""
    global _last_toggle
    with _lock:
        now = time.monotonic()
        if now - _last_toggle < TOGGLE_DEBOUNCE_SEC:
            return  # 이중 이벤트 무시
        _last_toggle = now
        want_start = not _recording
    # 실제 start/stop은 각자 _lock을 다시 잡으므로 락 밖에서 호출(재진입 데드락 방지).
    if want_start:
        start_recording()
    else:
        threading.Thread(target=stop_recording_and_process, daemon=True).start()
