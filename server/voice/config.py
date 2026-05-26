"""Voice Daemon 설정 — ~/.vt.env 로드, 핫키 파싱, 상수."""
import logging
import os

from pynput import keyboard

logger = logging.getLogger("voice-daemon")

SAMPLE_RATE = 16000
TTS_CONFIRM = True  # STT 결과를 TTS로 읽어줄지


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


def vt_getenv(key: str, default: str = "") -> str:
    """환경변수 → ~/.vt.env → default 우선순위."""
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return _VT_ENV.get(key, default)


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
