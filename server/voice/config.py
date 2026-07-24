"""Voice Daemon 설정 — ~/.vt.env 로드, 핫키 파싱, 상수."""
import logging
import os
import sys
from pathlib import Path

# server/ 를 import 경로에 — vt_env(공용 .vt.env 파서)를 쓰기 위해.
# voice_daemon.py로 직접 실행될 땐 이미 들어있지만, 다른 진입점도 있으므로 보장한다.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import vt_env  # noqa: E402

from pynput import keyboard  # noqa: E402

logger = logging.getLogger("voice-daemon")

SAMPLE_RATE = 16000
TTS_CONFIRM = True  # STT 결과를 TTS로 읽어줄지


_VT_ENV = vt_env.load()


def vt_getenv(key: str, default: str = "") -> str:
    """환경변수 → ~/.vt.env → default 우선순위."""
    return vt_env.getenv(key, default, file_env=_VT_ENV)


# 토글식 녹음은 '끄기'를 놓치면(미디어 키 오작동/핫키 릴리즈 누락) _recording=True인 채
# 콜백이 초당 32KB(16kHz·16bit)씩 무한정 프레임을 쌓는다 → 수 시간이면 GB 단위 + 그 거대
# 오디오를 faster-whisper로 돌리면 CTranslate2 네이티브 메모리(OS 미반환)가 급증해 안 줄어든다.
# 이 상한을 넘으면 녹음을 강제 종료하고 버퍼를 버린다. VT_MAX_RECORD_SEC로 조정(0=무제한).
try:
    MAX_RECORDING_SECONDS = float(vt_getenv("VT_MAX_RECORD_SEC", "120"))
except ValueError:
    MAX_RECORDING_SECONDS = 120.0


# Phase 6 R5: 단일 tmux 서버 원칙
TMUX_SOCKET = vt_getenv("VT_TMUX_SOCKET", "vt")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]


# ───────────────────────────────────────────────────────────────
# 핫키 파싱
# ───────────────────────────────────────────────────────────────

def _modifier_alternatives(token: str) -> frozenset | None:
    if token in ("ctrl", "control"):
        return frozenset({keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl})
    if token in ("shift",):
        return frozenset({keyboard.Key.shift_l, keyboard.Key.shift_r, keyboard.Key.shift})
    if token in ("alt", "option", "opt"):
        return frozenset({keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt})
    if token in ("cmd", "command", "meta", "win", "super"):
        opts = set()
        for name in ("cmd", "cmd_l", "cmd_r"):
            attr = getattr(keyboard.Key, name, None)
            if attr is not None:
                opts.add(attr)
        return frozenset(opts) if opts else None
    return None


def parse_hotkey(spec: str) -> list[frozenset] | None:
    """문자열 'ctrl+shift+v' → 토큰별 후보 set 리스트."""
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
        attr = getattr(keyboard.Key, t, None)
        if attr is not None:
            tokens.append(frozenset({attr}))
        else:
            logger.warning(f"알 수 없는 키 토큰: '{t}'")
            return None
    return tokens if tokens else None


VOICE_HOTKEY_DISABLED = vt_getenv("VT_HOTKEY_VOICE_DISABLED", "").lower() == "true"
VOICE_HOTKEY_SPEC = vt_getenv("VT_HOTKEY_VOICE", "ctrl+shift+v")
HOTKEY_TOKENS: list[frozenset] = parse_hotkey(VOICE_HOTKEY_SPEC) or parse_hotkey("ctrl+shift+v")


def hotkey_match(pressed: set) -> bool:
    """모든 토큰 set이 pressed와 교집합 있으면 매칭 (좌/우 modifier 양쪽 OK)."""
    if not HOTKEY_TOKENS:
        return False
    return all(any(k in pressed for k in tok) for tok in HOTKEY_TOKENS)
