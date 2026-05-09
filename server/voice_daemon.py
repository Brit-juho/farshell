#!/usr/bin/env python3
"""Voice Daemon — macOS 글로벌 핫키 + 이어폰 버튼으로 음성 입력 → tmux 주입.

서버 없이 독립 동작. tmux + STT(faster-whisper) + sounddevice만 사용.
- Ctrl+Shift+V 토글: 녹음 시작/종료
- 이어폰 Play/Pause 버튼: 녹음 토글 (macOS Media Key)
→ STT → 활성 tmux pane에 타이핑.

실행:
    /opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python server/voice_daemon.py
"""

import io
import logging
import os
import subprocess
import sys
import threading
import wave

import numpy as np
import sounddevice as sd
from pynput import keyboard
import platform_utils

# ---------------------------------------------------------------------------
# 이어폰 미디어 버튼 (macOS NSEvent 기반)
# ---------------------------------------------------------------------------

_media_listener_thread: threading.Thread | None = None


def _start_media_key_listener(toggle_fn):
    """macOS NSSystemDefined 이벤트로 이어폰 Play/Pause 버튼 감지.

    pyobjc-framework-Cocoa 필요:
        pip install pyobjc-framework-Cocoa
    """
    if not platform_utils.IS_MACOS:
        return

    try:
        from AppKit import NSEvent, NSApplication, NSApp
        from Foundation import NSObject
        import AppKit
    except ImportError:
        logger.warning("pyobjc-framework-Cocoa 미설치 → 이어폰 버튼 비활성")
        logger.warning("  pip install pyobjc-framework-Cocoa")
        return

    # NSSystemDefined subtype 8 = 미디어 키
    _MEDIA_PLAY_PAUSE = 16
    _MEDIA_NEXT = 17
    _MEDIA_PREV = 18

    def _monitor():
        try:
            mask = AppKit.NSSystemDefinedMask
            tap = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask,
                lambda event: _handle(event),
            )
            AppKit.NSRunLoop.mainRunLoop().run()
        except Exception as e:
            logger.error(f"미디어 키 모니터 오류: {e}")

    def _handle(event):
        try:
            if event.type() != 14:  # NSSystemDefined = 14
                return
            if event.subtype() != 8:
                return
            data1 = event.data1()
            key_code = (data1 & 0xFFFF0000) >> 16
            key_flags = data1 & 0x0000FFFF
            key_down = ((key_flags & 0xFF00) >> 8) == 0xA
            if not key_down:
                return
            if key_code == _MEDIA_PLAY_PAUSE:
                logger.info("🎧 이어폰 Play/Pause → 녹음 토글")
                toggle_fn()
        except Exception as e:
            logger.debug(f"미디어 이벤트 처리 오류: {e}")

    global _media_listener_thread
    _media_listener_thread = threading.Thread(target=_monitor, daemon=True)
    _media_listener_thread.start()
    logger.info("🎧 이어폰 버튼 감지 활성 (Play/Pause → 녹음 토글)")

logging.basicConfig(
    level=logging.INFO,
    format="[voice-daemon] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
TTS_CONFIRM = True  # STT 결과를 macOS say로 읽어줄지


# W2-1: ~/.vt.env 직접 읽기 (bash source는 export 안 하므로 환경변수로 안 옴)
def _load_vt_env_file() -> dict[str, str]:
    """~/.vt.env에서 KEY=VALUE 라인 파싱. export 접두 + 단/이중 따옴표 처리."""
    env_file = os.path.expanduser("~/.vt.env")
    out: dict[str, str] = {}
    try:
        if not os.path.isfile(env_file):
            return out
        with open(env_file) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("export "):
                    s = s[len("export "):]
                if "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
                    v = v[1:-1]
                out[k] = v
    except Exception:
        pass
    return out


_VT_ENV = _load_vt_env_file()


def _vt_getenv(key: str, default: str = "") -> str:
    """환경변수 → ~/.vt.env → default 우선순위."""
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return _VT_ENV.get(key, default)


# Phase 6 R5: 단일 tmux 서버 원칙 — vt CLI / server / daemon이 모두 같은 소켓 공유
TMUX_SOCKET = _vt_getenv("VT_TMUX_SOCKET", "vt")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]


# Wave 2 W2-1: 핫키 설정 파일 기반
# ~/.vt.env의 VT_HOTKEY_VOICE="ctrl+shift+v" 형태로 변경 가능.
# 비활성화: VT_HOTKEY_VOICE_DISABLED=true
# 토큰 → frozenset of Key/KeyCode 후보들 (좌/우 modifier 모두 매칭)
def _modifier_alternatives(token: str) -> frozenset | None:
    if token in ("ctrl", "control"):
        return frozenset({keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl})
    if token in ("shift",):
        return frozenset({keyboard.Key.shift_l, keyboard.Key.shift_r, keyboard.Key.shift})
    if token in ("alt", "option", "opt"):
        return frozenset({keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt})
    if token in ("cmd", "command", "meta", "win", "super"):
        # macOS cmd_l/cmd_r는 일부 pynput 빌드에 없을 수 있음 → fallback
        opts = set()
        for name in ("cmd", "cmd_l", "cmd_r"):
            attr = getattr(keyboard.Key, name, None)
            if attr is not None:
                opts.add(attr)
        return frozenset(opts) if opts else None
    return None


def _parse_hotkey(spec: str) -> list[frozenset] | None:
    """문자열 'ctrl+shift+v' → 토큰별 후보 set 리스트.

    각 토큰마다 매칭 가능한 키들의 frozenset. modifier는 좌/우 모두 매칭.
    on_press 핸들러는 모든 토큰 set이 _pressed_keys와 교집합 있으면 트리거.
    """
    if not spec or not spec.strip():
        return None
    tokens: list[frozenset] = []
    for token in spec.lower().strip().split("+"):
        t = token.strip()
        if not t:
            continue
        alts = _modifier_alternatives(t)
        if alts is not None:
            tokens.append(alts)
            continue
        if len(t) == 1:
            tokens.append(frozenset({keyboard.KeyCode.from_char(t)}))
            continue
        # 'f1'~'f12', 'space', 'enter' 등
        attr = getattr(keyboard.Key, t, None)
        if attr is not None:
            tokens.append(frozenset({attr}))
        else:
            logger.warning(f"알 수 없는 키 토큰: '{t}'")
            return None
    return tokens if tokens else None


_voice_hotkey_disabled = _vt_getenv("VT_HOTKEY_VOICE_DISABLED", "").lower() == "true"
_voice_hotkey_spec = _vt_getenv("VT_HOTKEY_VOICE", "ctrl+shift+v")
HOTKEY_TOKENS: list[frozenset] = _parse_hotkey(_voice_hotkey_spec) or _parse_hotkey("ctrl+shift+v")


def _hotkey_match(pressed: set) -> bool:
    """모든 토큰 set이 pressed와 교집합 있으면 매칭 (좌/우 modifier 양쪽 OK)."""
    if not HOTKEY_TOKENS:
        return False
    return all(any(k in pressed for k in tok) for tok in HOTKEY_TOKENS)

# ---------------------------------------------------------------------------
# STT 엔진 (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model = None


def _init_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return
    try:
        import mlx_whisper  # noqa: F401
        _whisper_model = "mlx"
        logger.info("STT: mlx-whisper")
        return
    except ImportError:
        pass
    from faster_whisper import WhisperModel
    _whisper_model = WhisperModel("base", compute_type="int8")
    logger.info("STT: faster-whisper (base)")


def transcribe(audio_np: np.ndarray) -> str:
    """numpy int16 배열 → 텍스트."""
    _init_whisper()

    # WAV 바이트로 변환
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_np.tobytes())
    wav_bytes = buf.getvalue()

    if _whisper_model == "mlx":
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_bytes, path_or_hf_repo="mlx-community/whisper-base"
        )
        return result.get("text", "").strip()
    else:
        # faster-whisper
        buf.seek(0)
        segments, _ = _whisper_model.transcribe(buf, language="ko")
        return " ".join(seg.text for seg in segments).strip()


# ---------------------------------------------------------------------------
# tmux 헬퍼
# ---------------------------------------------------------------------------


def get_active_tmux_pane() -> str | None:
    """현재 활성 tmux pane ID를 반환. tmux 밖이면 None."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def get_any_tmux_pane() -> str | None:
    """아무 tmux 세션의 첫 번째 pane을 반환."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        panes = result.stdout.strip().split("\n")
        return panes[0] if panes and panes[0] else None
    except Exception:
        return None


# Wave 2 W2-4: V3-R 음성 타깃 lock
# ~/.vt/voice_target 파일에 세션 이름 있으면 그 세션 active pane으로 보냄.
# 없거나 빈 값이면 most-recent (현재 동작).
def _read_voice_target_lock() -> str | None:
    try:
        path = os.path.expanduser("~/.vt/voice_target")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            target = f.read().strip()
        return target if target else None
    except Exception:
        return None


def get_locked_session_pane(session_name: str) -> str | None:
    """lock된 세션의 active pane id 반환. 세션 없으면 None."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "-t", session_name, "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def resolve_voice_target_pane() -> tuple[str | None, str]:
    """음성 daemon이 보낼 pane 결정. (pane_id, mode) 반환.
    mode: "lock:<name>" / "auto" / "none"
    """
    locked = _read_voice_target_lock()
    if locked:
        pane = get_locked_session_pane(locked)
        if pane:
            return pane, f"lock:{locked}"
        # lock된 세션이 사라진 경우 → auto로 폴백
        logger.warning(f"음성 타깃 lock된 세션 '{locked}' 없음 → AUTO 폴백")
    pane = get_active_tmux_pane() or get_any_tmux_pane()
    return pane, ("auto" if pane else "none")


def send_to_tmux(pane_id: str, text: str) -> bool:
    """tmux pane에 텍스트 전송."""
    try:
        subprocess.run(
            TMUX_BASE + ["send-keys", "-t", pane_id, "--", text, "Enter"],
            timeout=2, check=True,
        )
        return True
    except Exception as e:
        logger.error(f"tmux send-keys 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 녹음
# ---------------------------------------------------------------------------

_recording = False
_audio_frames: list[np.ndarray] = []
_stream: sd.InputStream | None = None
_lock = threading.Lock()


def start_recording():
    global _recording, _audio_frames, _stream
    with _lock:
        if _recording:
            return
        _audio_frames = []
        _recording = True

    def callback(indata, frames, time_info, status):
        if _recording:
            _audio_frames.append(indata.copy())

    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback
    )
    _stream.start()
    logger.info("🎙 녹음 시작")
    platform_utils.play_sound("start")


def stop_recording_and_process():
    global _recording, _stream
    with _lock:
        if not _recording:
            return
        _recording = False

    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None

    platform_utils.play_sound("stop")

    if not _audio_frames:
        logger.info("녹음 데이터 없음")
        return

    audio = np.concatenate(_audio_frames, axis=0)
    logger.info(f"녹음 종료: {len(audio)/SAMPLE_RATE:.1f}초")

    # STT
    text = transcribe(audio)
    if not text:
        logger.info("인식된 텍스트 없음")
        return

    logger.info(f"STT: {text}")

    # tmux에 주입 (W2-4: lock 우선)
    pane, mode = resolve_voice_target_pane()
    if not pane:
        logger.warning("활성 tmux pane 없음")
        if TTS_CONFIRM:
            platform_utils.tts_speak("tmux 세션이 없습니다")
        return

    if send_to_tmux(pane, text):
        logger.info(f"→ tmux {pane} ({mode}): {text}")
        if TTS_CONFIRM:
            platform_utils.tts_speak(text)


# ---------------------------------------------------------------------------
# 핫키 + 이어폰 공통 토글
# ---------------------------------------------------------------------------

def toggle_recording():
    """핫키와 이어폰 버튼 공통 진입점."""
    if _recording:
        threading.Thread(target=stop_recording_and_process, daemon=True).start()
    else:
        start_recording()


_pressed_keys: set = set()


def on_press(key):
    _pressed_keys.add(key)
    if _hotkey_match(_pressed_keys):
        toggle_recording()


def on_release(key):
    _pressed_keys.discard(key)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    logger.info(f"Voice Daemon 시작 ({platform_utils.PLATFORM_NAME})")

    if _voice_hotkey_disabled:
        logger.warning("핫키 비활성화됨 (VT_HOTKEY_VOICE_DISABLED=true)")
    else:
        logger.info(f"핫키: {_voice_hotkey_spec} (토글)")

    if platform_utils.IS_MACOS:
        logger.info("macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요")
    elif platform_utils.IS_WSL2 and not os.environ.get("DISPLAY"):
        logger.warning("WSL2에서 핫키 사용 시 WSLg 또는 X11 필요 ($DISPLAY 미설정)")
        logger.warning("브라우저 음성 입력은 서버 실행 후 웹에서 사용 가능")
    elif platform_utils.IS_LINUX:
        # W3-5: Wayland 글로벌 핫키 가드
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type == "wayland":
            logger.warning("Wayland 환경 — 글로벌 핫키가 캡처되지 않을 수 있음")
            logger.warning("X11 세션으로 전환하거나 모바일 🎤 / vt manage 사용 권장")

    # 음성 타깃 lock 상태 안내
    locked = _read_voice_target_lock()
    if locked:
        logger.info(f"음성 타깃 LOCK: {locked}")
    else:
        logger.info("음성 타깃 AUTO (most-recent)")

    # STT 모델 미리 로드
    _init_whisper()

    # 이어폰 미디어 버튼 리스너 시작 (W2-3: 환경변수/~/.vt.env로 토글)
    media_keys_off = _vt_getenv("VT_VOICE_MEDIA_KEYS", "on").lower() == "off"
    if media_keys_off:
        logger.info("이어폰 미디어 키 트리거 OFF (VT_VOICE_MEDIA_KEYS=off)")
    else:
        _start_media_key_listener(toggle_recording)

    if _voice_hotkey_disabled:
        # 핫키 비활성이면 그냥 대기 (미디어 키만 동작)
        try:
            import threading as _t
            _t.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
