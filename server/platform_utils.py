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
    """크로스 플랫폼 TTS. macOS say → Linux espeak-ng/piper/spd-say → WSL2 PowerShell → silent."""
    short = text[:200]
    try:
        if IS_MACOS:
            subprocess.Popen(["say", "-v", "Yuna", short])
            return

        if IS_WSL2 and shutil.which("powershell.exe"):
            safe = short.replace("'", "''").replace('"', '\\"')
            subprocess.Popen([
                "powershell.exe", "-NoProfile", "-c",
                f"Add-Type -AssemblyName System.Speech;"
                f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Speak('{safe}')",
            ])
            return

        if IS_LINUX:
            # W3-2: Linux TTS fallback chain
            if shutil.which("espeak-ng"):
                # espeak-ng가 한국어 일부 지원
                subprocess.Popen(
                    ["espeak-ng", "-v", "ko", short],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            if shutil.which("piper"):
                # piper는 voice model 별도 다운로드 필요. silent fallback
                logger.debug("piper detected but model loading skipped (configure VT_PIPER_MODEL)")
            if shutil.which("spd-say"):
                subprocess.Popen(
                    ["spd-say", short],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            if shutil.which("espeak"):
                subprocess.Popen(
                    ["espeak", short],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            logger.debug("Linux TTS not available — install espeak-ng or speech-dispatcher")
            return

        logger.debug("TTS not available on this platform")
    except Exception as e:
        logger.warning(f"TTS failed: {e}")


def _osascript_quote(s: str) -> str:
    """AppleScript 문자열 안전 escape — \\, ", 개행 처리."""
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", " ")
         .replace("\r", " ")
    )


def notify(title: str, message: str = "") -> None:
    """크로스 플랫폼 데스크톱 notification.

    macOS: osascript display notification
    Linux: notify-send (libnotify)
    실패 시 silent.
    """
    try:
        if IS_MACOS:
            t = _osascript_quote(title)
            m = _osascript_quote(message)
            script = f'display notification "{m}" with title "{t}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        if IS_LINUX and shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        logger.debug(f"notification not available: {title}")
    except Exception as e:
        logger.debug(f"notification failed: {e}")


def spawn_linux_terminal(command: str) -> bool:
    """Linux에서 새 터미널 창을 열고 지정 명령 실행.

    우선순위: gnome-terminal → konsole → alacritty → kitty → wezterm → xterm
    Returns: True if 명령 전달 성공.
    """
    if not IS_LINUX:
        return False

    # xfce4-terminal과 xterm은 -e 옵션이 단일 문자열을 받으므로 shlex.quote로 인젝션 방어
    import shlex
    qcmd = shlex.quote(f"{command}; exec bash")
    candidates = [
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-lc", f"{command}; exec bash"]),
        ("konsole", ["konsole", "-e", "bash", "-lc", f"{command}; exec bash"]),
        ("alacritty", ["alacritty", "-e", "bash", "-lc", f"{command}; exec bash"]),
        ("kitty", ["kitty", "bash", "-lc", f"{command}; exec bash"]),
        ("wezterm", ["wezterm", "start", "--", "bash", "-lc", f"{command}; exec bash"]),
        ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash -lc {qcmd}"]),
        ("xterm", ["xterm", "-e", f"bash -lc {qcmd}"]),
    ]
    for name, argv in candidates:
        if shutil.which(name):
            try:
                subprocess.Popen(argv)
                return True
            except Exception as e:
                logger.debug(f"{name} spawn failed: {e}")
                continue
    return False


def open_terminal_with_command(command: str) -> bool:
    """크로스 플랫폼 터미널 자동 오픈 (W3-1).

    macOS → spawn_mac_terminal
    Linux → spawn_linux_terminal
    그 외 → False
    """
    if IS_MACOS:
        return spawn_mac_terminal(command)
    if IS_LINUX and not IS_WSL2:
        return spawn_linux_terminal(command)
    return False


def spawn_mac_terminal(command: str) -> bool:
    """macOS에서 새 터미널 창을 열고 지정 명령 실행.

    지원 우선순위: iTerm2 → Ghostty → WezTerm → Kitty → Alacritty → Terminal.app
    command는 AppleScript/쉘에 넘어가므로 호출측에서 sanitize 필요.

    Returns: True if 터미널 앱에 명령 전달 성공, False if 모든 시도 실패.
    """
    if not IS_MACOS:
        return False

    def _osascript(script: str) -> bool:
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    # 1) iTerm2
    if os.path.isdir("/Applications/iTerm.app"):
        script = (
            'tell application "iTerm"\n'
            '  activate\n'
            '  create window with default profile\n'
            '  tell current session of current window\n'
            f'    write text "{command}"\n'
            '  end tell\n'
            'end tell'
        )
        if _osascript(script):
            return True

    # 2) Ghostty
    if os.path.isdir("/Applications/Ghostty.app"):
        try:
            subprocess.Popen(["open", "-na", "Ghostty", "--args", "-e",
                              "bash", "-lc", f"{command}; exec bash"])
            return True
        except Exception:
            pass

    # 3) WezTerm
    if shutil.which("wezterm"):
        try:
            subprocess.Popen(["wezterm", "cli", "spawn", "--new-window",
                              "--", "bash", "-lc", command])
            return True
        except Exception:
            pass

    # 4) Kitty
    if os.path.isdir("/Applications/kitty.app"):
        try:
            subprocess.Popen(["open", "-na", "kitty", "--args",
                              "bash", "-lc", f"{command}; exec bash"])
            return True
        except Exception:
            pass

    # 5) Alacritty
    if os.path.isdir("/Applications/Alacritty.app"):
        try:
            subprocess.Popen(["open", "-na", "Alacritty", "--args", "-e",
                              "bash", "-lc", f"{command}; exec bash"])
            return True
        except Exception:
            pass

    # 6) Terminal.app (최후 — 맥 기본 내장)
    script = (
        'tell application "Terminal"\n'
        '  activate\n'
        f'  do script "{command}"\n'
        'end tell'
    )
    if _osascript(script):
        return True

    return False


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
