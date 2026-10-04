"""terminal_open.py — 새 터미널 창 열기의 단일 구현.

웹 서버(`POST /api/tmux/open-on-mac`, 세션 생성 시 auto_open_on_mac)와 CLI(`fsh attach`,
`fsh handoff desktop`, `fsh terminal`)가 모두 이 모듈 하나를 쓴다. 예전엔 bash(`bin/fsh`)와
파이썬(`platform_utils.spawn_mac_terminal`)이 같은 일을 따로 구현해, 한쪽만 고치면 다른 쪽이
어긋났다(`VT_TERMINAL` 설정이 CLI에만 먹고 웹은 iTerm2를 먼저 시도하던 것).

터미널 선택:
  1. `VT_TERMINAL`(환경변수 → ~/.vt.env)이 `auto`가 아니면 그 앱을 먼저 시도한다.
  2. 열지 못하면(미설치·실행 실패) 자동 순서로 넘어가고, 넘어갔다는 사실을 note로 돌려준다.
  3. 자동 순서: iTerm2 → Ghostty → WezTerm → Kitty → Alacritty → Terminal.app.

명령은 임시 스크립트에 써서 **경로만** 터미널에 넘긴다. 셸 따옴표를 AppleScript 문자열 안에
중첩해 이스케이프하면 명령에 따옴표가 있을 때 깨지거나 주입 경로가 되기 때문이다. 스크립트는
시작하자마자 스스로 지우고, 명령은 `exec`로 실행한다. 프로세스가 끝나면 대부분의 터미널 앱이 창을
닫지만 Ghostty의 AppleScript 창은 남는다(알려진 한계, 아래 `_write_launcher` 참고).

CLI:
  python -m terminal_open -- '<명령>'   새 창에서 명령 실행 (성공 시 opened:<앱>)
  python -m terminal_open --installed   설치된 앱 이름
  python -m terminal_open --choices     VT_TERMINAL에 쓸 수 있는 값
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple

import platform_utils as pu

logger = logging.getLogger(__name__)

CHOICES = ("auto", "ghostty", "iterm", "terminal", "wezterm", "warp", "kitty", "alacritty")
# auto일 때 시도하는 순서. Warp는 명령 주입이 클립보드 붙여넣기뿐이라 자동에선 쓰지 않는다.
AUTO_ORDER = ("iterm", "ghostty", "wezterm", "kitty", "alacritty", "terminal")

_APP_PATHS = {
    "iterm": "/Applications/iTerm.app",
    "ghostty": "/Applications/Ghostty.app",
    "warp": "/Applications/Warp.app",
    "kitty": "/Applications/kitty.app",
    "alacritty": "/Applications/Alacritty.app",
}

# 스크립트 경로는 mkstemp가 만든 값이라 안전하지만, AppleScript 문자열에 들어가므로
# 한 번 더 허용 문자만 통과시킨다.
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./+-]+$")


class OpenResult(NamedTuple):
    ok: bool
    app: str = ""
    err: str = ""
    note: str = ""


def preference() -> str:
    """사용자가 고른 메인 터미널. 모르는 값이면 auto로 본다."""
    try:
        from vt_env import getenv
        val = (getenv("VT_TERMINAL", "auto") or "auto").strip().lower()
    except Exception:
        val = "auto"
    return val if val in CHOICES else "auto"


def is_installed(name: str) -> bool:
    if not pu.IS_MACOS:
        return False
    if name == "terminal":
        return True  # 맥에 기본 내장
    if name == "wezterm":
        return os.path.isdir("/Applications/WezTerm.app") or shutil.which("wezterm") is not None
    path = _APP_PATHS.get(name)
    return bool(path) and os.path.isdir(path)


def installed() -> list[str]:
    return [c for c in CHOICES if c != "auto" and is_installed(c)]


def _write_launcher(command: str) -> str:
    """명령을 담은 1회용 스크립트. 시작하자마자 스스로 지운다.

    명령은 `exec`로 실행한다 — tmux 세션이 끝나면 프로세스가 끝나고 터미널 앱이 창을 닫는다.
    (Ghostty의 AppleScript 창은 프로세스가 끝나도 창이 남는다 — 알려진 한계. 창이 스스로 자기
    창을 닫게 시도했다가 Ghostty 앱이 통째로 종료되는 사고가 나서 제거했다, 2026-10-02.)
    """
    fd, path = tempfile.mkstemp(prefix="fsh-open.")
    if not _SAFE_PATH.match(path):
        os.close(fd)
        os.unlink(path)
        raise ValueError(f"임시 스크립트 경로가 안전하지 않음: {path!r}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"rm -f -- {path}\nexec {command}\n")
    os.chmod(path, 0o700)
    return path


def _osascript(script: str, timeout: float = 8.0) -> tuple[bool, str]:
    """(성공 여부, 성공이면 stdout / 실패면 stderr)."""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=timeout)
    except Exception as e:  # 타임아웃·실행 불가
        return False, str(e)
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, (r.stderr or "").strip()


def _popen(argv: list[str]) -> tuple[bool, str]:
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    except Exception as e:
        return False, str(e)


# 각 spawner: (실행 명령줄 "<셸> -l <스크립트>") -> (성공, 오류, note)
def _spawn_iterm(line: str) -> tuple[bool, str, str]:
    ok, err = _osascript(
        'tell application "iTerm"\n'
        '  activate\n'
        '  create window with default profile\n'
        '  tell current session of current window\n'
        f'    write text "exec {line}"\n'
        '  end tell\n'
        'end tell')
    return ok, err, ""


def _spawn_ghostty(line: str) -> tuple[bool, str, str]:
    # 1순위: 이미 켜진 Ghostty에 창만 추가한다(AppleScript, 1.3+). `open -na`는 매번 앱을
    # 하나 더 실행해 프로세스·Dock 아이콘이 늘고 더 느렸다(실측 0.29초 → 0.15초).
    # surface configuration은 레코드 타입이라 값을 따로 set하면 반영되지 않는다 —
    # 레코드 리터럴로 한 번에 넘겨야 명령이 실행된다.
    ok, err = _osascript(
        'tell application "Ghostty"\n'
        f'  new window with configuration {{command:"{line}"}}\n'
        'end tell')
    if ok:
        # 앞으로 가져오기(activate)는 창 생성과 따로, 기다리지 않고 돌린다. 같은 스크립트에
        # 넣으면 이 호출만 2초 넘게 걸려(실측 0.15초 → 2.2초) 창 열기가 느려진다.
        _popen(["osascript", "-e", 'tell application "Ghostty" to activate'])
        return True, "", ""
    # 2순위: AppleScript가 없는 구버전이거나 자동화 권한이 거부된 경우
    ok2, err2 = _popen(["open", "-na", "Ghostty", "--args", "-e", *line.split()])
    return ok2, ("" if ok2 else (err or err2)), ""


def _spawn_terminal(line: str) -> tuple[bool, str, str]:
    ok, err = _osascript(
        'tell application "Terminal"\n'
        '  activate\n'
        f'  do script "exec {line}"\n'
        'end tell')
    return ok, err, ""


def _spawn_wezterm(line: str) -> tuple[bool, str, str]:
    ok, err = _popen(["wezterm", "cli", "spawn", "--new-window", "--", *line.split()])
    return ok, err, ""


def _spawn_kitty(line: str) -> tuple[bool, str, str]:
    ok, err = _popen(["open", "-na", "kitty", "--args", *line.split()])
    return ok, err, ""


def _spawn_alacritty(line: str) -> tuple[bool, str, str]:
    ok, err = _popen(["open", "-na", "Alacritty", "--args", "-e", *line.split()])
    return ok, err, ""


_SPAWNERS: dict[str, Callable[[str], tuple[bool, str, str]]] = {
    "iterm": _spawn_iterm,
    "ghostty": _spawn_ghostty,
    "terminal": _spawn_terminal,
    "wezterm": _spawn_wezterm,
    "kitty": _spawn_kitty,
    "alacritty": _spawn_alacritty,
}


def _spawn_warp(command: str) -> OpenResult:
    # Warp는 명령 주입 경로가 없다 — 앱만 열고 명령은 클립보드에 둔다(붙여넣기 필요).
    if not is_installed("warp"):
        return OpenResult(False, "warp", "Warp가 설치돼 있지 않음")
    ok, err = _popen(["open", "-na", "Warp"])
    if not ok:
        return OpenResult(False, "warp", err)
    try:
        subprocess.run(["pbcopy"], input=command, text=True, timeout=3)
    except Exception:
        pass
    return OpenResult(True, "warp", "", "Warp는 명령을 자동 실행하지 못해 클립보드에 복사했습니다 — 붙여넣기 하세요")


def open_terminal(command: str, preferred: str | None = None) -> OpenResult:
    """새 터미널 창에서 command를 실행한다. command는 셸 명령 문자열."""
    if not command or not command.strip():
        return OpenResult(False, "", "명령이 비어 있음")
    if pu.IS_LINUX and not pu.IS_WSL2:
        ok = pu.spawn_linux_terminal(command)
        return OpenResult(ok, "linux" if ok else "", "" if ok else "지원하는 터미널 에뮬레이터가 없음")
    if not pu.IS_MACOS:
        return OpenResult(False, "", "지원하지 않는 OS")

    pref = (preferred or preference()).lower()
    if pref not in CHOICES:
        pref = "auto"

    order: list[str] = []
    if pref != "auto":
        order.append(pref)
    order += [a for a in AUTO_ORDER if a != pref]

    errors: list[str] = []
    note = ""
    for app in order:
        if not is_installed(app):
            if app == pref:
                note = f"VT_TERMINAL={pref}가 설치돼 있지 않아 자동 선택으로 열었습니다"
            continue
        if app == "warp":
            res = _spawn_warp(command)
            if res.ok:
                return res
            errors.append(f"warp: {res.err}")
            continue
        spawner = _SPAWNERS.get(app)
        if spawner is None:
            continue
        launcher = _write_launcher(command)
        line = f"{pu.get_default_shell()} -l {launcher}"
        try:
            ok, err, extra = spawner(line)
        except Exception as e:  # spawner가 예외를 내도 다음 앱으로 넘어간다
            ok, err, extra = False, str(e), ""
        if ok:
            # launcher는 실행되면서 스스로 지운다
            if app != pref and pref != "auto" and not note:
                note = f"VT_TERMINAL={pref}로 열지 못해 {app}로 열었습니다"
            return OpenResult(True, app, "", extra or note)
        try:
            os.unlink(launcher)
        except OSError:
            pass
        errors.append(f"{app}: {err}")
        if app == pref and not note:
            note = f"VT_TERMINAL={pref}로 열지 못해 자동 선택으로 넘어갔습니다"
    return OpenResult(False, "", "; ".join(e for e in errors if e) or "열 수 있는 터미널 앱이 없음", note)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--installed":
        print(" ".join(installed()))
        return 0
    if argv and argv[0] == "--choices":
        print(" ".join(CHOICES))
        return 0
    if argv and argv[0] == "--":
        argv = argv[1:]
    command = " ".join(argv).strip() if len(argv) != 1 else argv[0]
    res = open_terminal(command)
    if res.ok:
        print(f"opened:{res.app}")
        if res.note:
            print(f"note:{res.note}")
        return 0
    if res.err:
        print(res.err, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
