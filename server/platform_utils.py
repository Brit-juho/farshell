"""Platform Utils — macOS/Linux/WSL2 크로스 플랫폼 유틸리티.

모든 OS 분기를 이 파일에 집중. 다른 모듈은 이 파일만 import.
"""

import logging
import os
import platform
import shutil
import socket
import subprocess

logger = logging.getLogger(__name__)

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

try:
    IS_WSL2 = IS_LINUX and "microsoft" in open("/proc/version").read().lower()
except (FileNotFoundError, PermissionError):
    IS_WSL2 = False

PLATFORM_NAME = "macOS" if IS_MACOS else ("WSL2" if IS_WSL2 else ("Linux" if IS_LINUX else platform.system()))


def get_default_shell() -> str:
    if IS_MACOS:
        return os.environ.get("SHELL", "/bin/zsh")
    return os.environ.get("SHELL", "/bin/bash")


def find_tmux() -> str:
    found = shutil.which("tmux")
    if found:
        return found
    # macOS homebrew 경로 fallback
    for p in ["/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"]:
        if os.path.isfile(p):
            return p
    return "tmux"


def check_port(port: int) -> bool:
    """포트가 사용 중인지 확인. lsof 대체."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    result = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    return result


def get_local_ip() -> str:
    if IS_MACOS:
        try:
            r = subprocess.run(
                ["ipconfig", "getifaddr", "en0"],
                capture_output=True, text=True, timeout=3,
            )
            ip = r.stdout.strip()
            if ip:
                return ip
        except Exception:
            pass
    # Linux / fallback
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def tts_speak(text: str) -> None:
    """크로스 플랫폼 TTS. macOS say → WSL2 PowerShell Speech → 무시."""
    short = text[:200]
    try:
        if IS_MACOS:
            subprocess.Popen(["say", "-v", "Yuna", short])
        elif IS_WSL2 and shutil.which("powershell.exe"):
            safe = short.replace("'", "''").replace('"', '\\"')
            subprocess.Popen([
                "powershell.exe", "-NoProfile", "-c",
                f"Add-Type -AssemblyName System.Speech;"
                f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Speak('{safe}')",
            ])
        else:
            logger.debug("TTS not available on this platform")
    except Exception as e:
        logger.warning(f"TTS failed: {e}")


def play_sound(event: str) -> None:
    """효과음 재생. event: 'start' 또는 'stop'."""
    try:
        if IS_MACOS:
            sounds = {
                "start": "/System/Library/Sounds/Tink.aiff",
                "stop": "/System/Library/Sounds/Pop.aiff",
            }
            subprocess.Popen(["afplay", sounds.get(event, sounds["start"])])
        elif IS_WSL2 and shutil.which("powershell.exe"):
            sounds = {
                "start": "C:\\Windows\\Media\\notify.wav",
                "stop": "C:\\Windows\\Media\\chord.wav",
            }
            path = sounds.get(event, sounds["start"])
            subprocess.Popen([
                "powershell.exe", "-NoProfile", "-c",
                f"(New-Object Media.SoundPlayer '{path}').PlaySync()",
            ])
    except Exception as e:
        logger.debug(f"Sound playback failed: {e}")
