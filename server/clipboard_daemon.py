#!/usr/bin/env python3
"""macOS 클립보드 변경 감지 → 서버로 push (OSC52가 못 잡는 복사 보완용).

OSC52는 vim/tmux copy-mode 등 '터미널 프로그램 안'에서 일어난 복사만 잡는다.
Safari/Finder 등 임의 앱에서 복사한 건 PTY 출력 스트림에 실리지 않으므로,
macOS 시스템 클립보드 자체를 폴링해서 서버로 push하는 이 데몬이 보완한다.

NSPasteboard.changeCount는 클립보드가 바뀔 때마다 증가하는 정수라 값만 비교
하면 되고 매번 클립보드 내용을 읽지 않아도 된다 — 가벼운 폴링으로 상시
백그라운드 실행 가능(voice_daemon의 STT 모델 상주와는 리소스 성격이 다름).

실행: "$VT_PYTHON" server/clipboard_daemon.py
"""
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import platform_utils

logger = logging.getLogger("clipboard-daemon")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[clipboard-daemon] %(message)s")

MAX_TEXT_LEN = 200_000


def _load_vt_env_file() -> dict:
    """~/.vt.env에서 KEY=VALUE 로드. voice/config.py와 동일한 최소 파서를
    중복 구현 — 이 데몬을 voice 패키지(pynput 의존)와 무관하게 독립 실행 가능하게 유지."""
    env_file = os.path.expanduser("~/.vt.env")
    out: dict = {}
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
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
                    v = v[1:-1]
                out[k] = v
    except Exception:
        pass
    return out


_VT_ENV = _load_vt_env_file()


def _vt_getenv(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return _VT_ENV.get(key, default)


POLL_SEC = float(_vt_getenv("VT_CLIPBOARD_POLL_SEC", "0.7"))
_PORT = _vt_getenv("VT_PORT", "7777")
_TOKEN = _vt_getenv("VT_TOKEN", "")
PUSH_URL = f"http://127.0.0.1:{_PORT}/api/clipboard/push"


def _push(text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _TOKEN:
        headers["Authorization"] = f"Bearer {_TOKEN}"
    req = urllib.request.Request(PUSH_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3):
            pass
    except urllib.error.URLError as e:
        logger.warning(f"push 실패 (서버 미실행?): {e}")


def main():
    if not platform_utils.IS_MACOS:
        logger.error("클립보드 폴링은 현재 macOS 전용입니다 (NSPasteboard)")
        return
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
    except ImportError:
        logger.error("pyobjc-framework-Cocoa 미설치 — pip install pyobjc-framework-Cocoa")
        return

    pb = NSPasteboard.generalPasteboard()
    last_count = pb.changeCount()
    last_text = None
    logger.info(f"클립보드 폴링 시작 ({POLL_SEC}s 간격) → {PUSH_URL}")

    while True:
        try:
            time.sleep(POLL_SEC)
            count = pb.changeCount()
            if count == last_count:
                continue
            last_count = count
            text = pb.stringForType_(NSPasteboardTypeString)
            if not text or text == last_text:
                continue
            last_text = text
            if len(text) > MAX_TEXT_LEN:
                logger.info(f"클립보드 {len(text)}자 — 상한({MAX_TEXT_LEN}) 초과로 스킵")
                continue
            _push(text)
            preview = text if len(text) <= 40 else text[:40] + "..."
            logger.info(f"push: {preview!r}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.warning(f"폴링 오류(무시): {e}")


if __name__ == "__main__":
    main()
