"""Codex 훅(`~/.codex/config.toml`)의 멱등 등록기.

Codex 설정은 TOML이고 표준 라이브러리에는 writer가 없다. 전체를 재직렬화하지
않고 FarShell 소유 마커 구간만 교체한다. Codex가 마커 안에 추가하는
``hooks.state`` 신뢰 해시는 별도 경계 아래에 두고 설치/갱신 때 보존한다.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BEGIN = "# >>> farshell:hooks (managed by `fsh hooks install` — do not edit by hand) >>>"
STATE_BOUNDARY = "# --- farshell:hooks: codex가 이 아래에 신뢰 상태를 쓸 수 있다 — 보존됨 ---"
END = "# <<< farshell:hooks <<<"

EVENTS = {
    "PreToolUse": "pre",
    "PermissionRequest": "permission",
    "PostToolUse": "post",
    "UserPromptSubmit": "prompt",
    "Stop": "stop",
    "SessionEnd": "session_end",
}


def settings_path() -> Path:
    base = os.environ.get("VT_CODEX_HOME") or os.environ.get("CODEX_HOME")
    return (Path(base) if base else Path.home() / ".codex") / "config.toml"


def hook_command(event: str, repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    return f"{root / 'server' / 'agent_hook.sh'} {EVENTS[event]} codex"


def load_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} 를 TOML로 읽을 수 없습니다: {exc}") from exc
    return text


def _managed_block(repo_root: Path | None = None) -> str:
    lines = [BEGIN]
    for event in EVENTS:
        lines.append(f"[[hooks.{event}]]")
        if event in ("PreToolUse", "PermissionRequest", "PostToolUse"):
            lines.append('matcher = "*"')
        lines.append(f"[[hooks.{event}.hooks]]")
        lines.append('type = "command"')
        command = hook_command(event, repo_root).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'command = "{command}"')
        lines.append("")
    lines.append(STATE_BOUNDARY)
    return "\n".join(lines) + "\n"


def _split_owned(text: str) -> tuple[str, str | None, str]:
    start = text.find(BEGIN)
    if start < 0:
        return text, None, ""
    end = text.find(END, start)
    if end < 0:
        raise ValueError("FarShell Codex 훅 시작 마커는 있지만 끝 마커가 없습니다")
    end += len(END)
    while end < len(text) and text[end] == "\n":
        end += 1
    return text[:start], text[start:end], text[end:]


def plan_text(text: str, repo_root: Path | None = None) -> dict[str, tuple[str, str | None]]:
    try:
        data = tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Codex 설정을 TOML로 읽을 수 없습니다: {exc}") from exc
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    result = {}
    for event in EVENTS:
        want = hook_command(event, repo_root)
        groups = hooks.get(event) if isinstance(hooks, dict) else None
        commands = []
        for group in groups if isinstance(groups, list) else []:
            nested = group.get("hooks") if isinstance(group, dict) else None
            for hook in nested if isinstance(nested, list) else []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
        if want in commands:
            result[event] = ("ok", want)
        elif any("agent_hook.sh" in command for command in commands):
            result[event] = ("update", commands[0] if commands else None)
        else:
            result[event] = ("add", None)
    return result


def apply_install(text: str, repo_root: Path | None = None) -> tuple[str, list[str]]:
    before, owned, after = _split_owned(text)
    preserved = ""
    if owned and STATE_BOUNDARY in owned:
        preserved = owned.split(STATE_BOUNDARY, 1)[1]
        preserved = preserved.rsplit(END, 1)[0].strip("\n")
    block = _managed_block(repo_root)
    if preserved:
        block += "\n" + preserved + "\n"
    block += END + "\n"
    prefix = before
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    merged = prefix + block + after.lstrip("\n")
    # 쓰기 전에 결과 전체를 파싱한다. 마커 수술이 다른 TOML 테이블과 충돌하면 거절.
    tomllib.loads(merged)
    if merged == text:
        return text, []
    current = plan_text(text, repo_root)
    changes = [f"{event}: {state}" for event, (state, _) in current.items() if state != "ok"]
    return merged, changes or ["Codex 훅 블록 갱신"]


def apply_uninstall(text: str) -> tuple[str, list[str]]:
    before, owned, after = _split_owned(text)
    if owned is None:
        return text, []
    merged = (before.rstrip("\n") + "\n\n" + after.lstrip("\n")).lstrip("\n")
    if merged.strip():
        tomllib.loads(merged)
    return merged, ["Codex 훅 블록 제거"]


def write_text(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_suffix(path.suffix + ".vtbak")
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return backup


def _cmd_status() -> int:
    try:
        states = plan_text(load_text(settings_path()))
    except ValueError as exc:
        print(f"broken\t{exc}")
        return 2
    for event, (state, current) in states.items():
        print(f"{event}\t{state}\t{current or '-'}")
    return 0 if all(state == "ok" for state, _ in states.values()) else 1


def _cmd_install() -> int:
    path = settings_path()
    try:
        merged, changes = apply_install(load_text(path))
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"  ✗ {exc}")
        print("  설정 파일을 고친 뒤 다시 실행하세요 — 덮어쓰지 않았습니다.")
        return 2
    if not changes:
        print(f"  ✓ 이미 등록돼 있습니다 ({path})")
        return 0
    backup = write_text(path, merged)
    for change in changes:
        print(f"  · {change}")
    print(f"  ✓ {path} 갱신" + (f" (백업: {backup})" if backup else ""))
    print("  ⓘ 이미 실행 중인 codex 세션에는 적용되지 않습니다 — 새로 시작하세요.")
    return 0


def _cmd_uninstall() -> int:
    path = settings_path()
    try:
        merged, changes = apply_uninstall(load_text(path))
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return 2
    if not changes:
        print("  ✓ 등록된 FarShell Codex 훅이 없습니다")
        return 0
    backup = write_text(path, merged)
    print(f"  ✓ {path} 갱신" + (f" (백업: {backup})" if backup else ""))
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        return _cmd_status()
    if command == "install":
        return _cmd_install()
    if command == "uninstall":
        return _cmd_uninstall()
    print("사용법: codex_hooks.py [status|install|uninstall]", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
