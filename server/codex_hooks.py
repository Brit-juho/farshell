"""Codex 훅(`~/.codex/config.toml`)의 멱등 등록기.

Codex 설정은 TOML이고 표준 라이브러리에는 writer가 없다. 전체를 재직렬화하지
않고 FarShell 소유 마커 구간만 교체한다. Codex가 마커 안에 추가하는
``hooks.state`` 신뢰 해시는 별도 경계 아래에 두고 설치/갱신 때 보존한다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

import config_io
import store_lock
from i18n import tr

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
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
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
        raise ValueError(tr("{path} 를 TOML로 읽을 수 없습니다: {error}", path=path, error=exc)) from exc
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
        raise ValueError(tr("FarShell Codex 훅 시작 마커는 있지만 끝 마커가 없습니다"))
    end += len(END)
    while end < len(text) and text[end] == "\n":
        end += 1
    return text[:start], text[start:end], text[end:]


def plan_text(text: str, repo_root: Path | None = None) -> dict[str, tuple[str, str | None]]:
    try:
        data = tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(tr("Codex 설정을 TOML로 읽을 수 없습니다: {error}", error=exc)) from exc
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
    return merged, changes or [tr("Codex 훅 블록 갱신")]


def apply_uninstall(text: str) -> tuple[str, list[str]]:
    before, owned, after = _split_owned(text)
    if owned is None:
        return text, []
    merged = (before.rstrip("\n") + "\n\n" + after.lstrip("\n")).lstrip("\n")
    if merged.strip():
        tomllib.loads(merged)
    return merged, [tr("Codex 훅 블록 제거")]


_UNCHECKED = object()


def write_text(path: Path, text: str, expected_stamp=_UNCHECKED) -> Path | None:
    """백업 후 원자적으로 쓴다. `expected_stamp`를 주면 쓰기 직전에 파일이 그대로인지 다시
    확인한다(`config_io.ensure_unchanged`). 링크는 따라가고 권한은 물려받는다."""
    target = config_io.resolve_target(path)
    if expected_stamp is not _UNCHECKED:
        config_io.ensure_unchanged(path, expected_stamp)
    backup = None
    if target.exists():
        backup = target.with_suffix(target.suffix + ".vtbak")
        shutil.copy2(target, backup)
    config_io.atomic_write(path, text)
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
        with config_io.locked(path):
            stamp = config_io.stamp(path)
            try:
                merged, changes = apply_install(load_text(path))
            except (ValueError, tomllib.TOMLDecodeError) as exc:
                print(f"  ✗ {exc}")
                print(tr("  설정 파일을 고친 뒤 다시 실행하세요 — 덮어쓰지 않았습니다."))
                return 2
            if not changes:
                print(tr("  ✓ 이미 등록돼 있습니다 ({path})", path=path))
                return 0
            backup = write_text(path, merged, stamp)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as exc:
        print(f"  ✗ {exc}")
        return 2
    for change in changes:
        print(f"  · {change}")
    print(tr("  ✓ {path} 갱신", path=path) + (tr(" (백업: {backup})", backup=backup) if backup else ""))
    print(tr("  ⓘ 이미 실행 중인 codex 세션에는 적용되지 않습니다 — 새로 시작하세요."))
    return 0


def _cmd_uninstall() -> int:
    path = settings_path()
    try:
        with config_io.locked(path):
            stamp = config_io.stamp(path)
            try:
                merged, changes = apply_uninstall(load_text(path))
            except ValueError as exc:
                print(f"  ✗ {exc}")
                return 2
            if not changes:
                print(tr("  ✓ 등록된 FarShell Codex 훅이 없습니다"))
                return 0
            backup = write_text(path, merged, stamp)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as exc:
        print(f"  ✗ {exc}")
        return 2
    print(tr("  ✓ {path} 갱신", path=path) + (tr(" (백업: {backup})", backup=backup) if backup else ""))
    return 0


# ── hooks.json 중복 (Codex 앱 「Claude 설정 가져오기」) ─────────────────────
# FarShell은 hooks.json에 **쓰지 않는다**(계약). 그런데 Codex 데스크톱 앱의
# 가져오기가 Claude의 FarShell 훅을 거기 복제한다(`agent_hook.sh pre claude` …).
# Codex는 config.toml과 hooks.json을 모두 읽으므로 같은 이벤트가 두 번(그중 하나는
# claude 태그로) 들어와 에이전트 라벨이 어긋난다. 여기서 발견되면 전부 외부 복제본이다.
SCRIPT_NAME = "agent_hook.sh"


def hooks_json_path() -> Path:
    return settings_path().with_name("hooks.json")


def display_path(path: Path) -> str:
    """개인 경로(홈)는 `~` 로 가려서 보여준다."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _load_hooks_json(path: Path) -> tuple[dict | None, str]:
    """(데이터, 원문). 없으면 (None, ""). 깨졌으면 ValueError."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(tr("{path} 를 읽을 수 없습니다: {error}", path=path, error=exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(tr("{path} 를 JSON으로 읽을 수 없습니다: {error}", path=path, error=exc)) from exc
    if not isinstance(data, dict):
        raise ValueError(tr("{path} 의 최상위가 객체가 아닙니다", path=path))
    return data, raw


def _iter_groups(data: dict):
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if isinstance(groups, list):
            yield event, groups


def _script_args(command: str) -> str:
    return command.split(SCRIPT_NAME, 1)[1].strip()


def _is_ours(hook) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and SCRIPT_NAME in hook["command"]


def _entry(event: str, command: str) -> tuple[str, str, str | None]:
    args = _script_args(command)
    tokens = args.split()
    return event, args, tokens[1] if len(tokens) > 1 else None


def find_foreign_farshell_hooks(path: Path | None = None) -> list[tuple[str, str, str | None]]:
    """hooks.json 안의 FarShell 항목 → [(event, 스크립트 인자부분, agent_tag 또는 None)].

    파일이 없거나 깨졌으면 빈 목록(예외 없음).
    """
    try:
        data, _ = _load_hooks_json(path or hooks_json_path())
    except ValueError:
        return []
    found: list[tuple[str, str, str | None]] = []
    for event, groups in _iter_groups(data or {}):
        for group in groups:
            nested = group.get("hooks") if isinstance(group, dict) else None
            for hook in nested if isinstance(nested, list) else []:
                if _is_ours(hook):
                    found.append(_entry(event, hook["command"]))
    return found


def _detect_indent(raw: str):
    for line in raw.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) < len(line):
            lead = line[: len(line) - len(stripped)]
            return "\t" if lead.startswith("\t") else len(lead)
    return None  # 한 줄짜리 JSON


def apply_dedupe(data: dict) -> tuple[dict, list[tuple[str, str, str | None]]]:
    """FarShell 항목만 제거(다른 훅 보존, 빈 그룹/이벤트 정리). (새 데이터, 제거 목록)."""
    removed: list[tuple[str, str, str | None]] = []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data, removed
    new_hooks = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            new_hooks[event] = groups
            continue
        kept_groups = []
        for group in groups:
            nested = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(nested, list):
                kept_groups.append(group)
                continue
            kept = []
            for hook in nested:
                if _is_ours(hook):
                    removed.append(_entry(event, hook["command"]))
                else:
                    kept.append(hook)
            if len(kept) == len(nested):
                kept_groups.append(group)
            elif kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups or not groups:
            new_hooks[event] = kept_groups
    return {**data, "hooks": new_hooks}, removed


def write_hooks_json(path: Path, data: dict, raw: str, expected_stamp=_UNCHECKED) -> Path:
    target = config_io.resolve_target(path)
    if expected_stamp is not _UNCHECKED:
        config_io.ensure_unchanged(path, expected_stamp)
    backup = target.with_suffix(target.suffix + ".vtbak")
    shutil.copy2(target, backup)
    text = json.dumps(data, indent=_detect_indent(raw), ensure_ascii=False)
    if raw.endswith("\n"):
        text += "\n"
    config_io.atomic_write(path, text)
    return backup


def _cmd_duplicates() -> int:
    """기계가 읽는 출력: event<TAB>args<TAB>tag(없으면 -). 없으면 아무것도 안 낸다."""
    for event, args, tag in find_foreign_farshell_hooks():
        print(f"{event}\t{args}\t{tag or '-'}")
    return 0


def _cmd_dedupe(dry_run: bool) -> int:
    path = hooks_json_path()
    try:
        with config_io.locked(path):
            return _dedupe(path, dry_run)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as exc:
        print(f"  ✗ {exc}")
        return 2


def _dedupe(path: Path, dry_run: bool) -> int:
    stamp = config_io.stamp(path)
    try:
        data, raw = _load_hooks_json(path)
    except ValueError as exc:
        print(f"  ✗ {exc}")
        print(tr("  파일을 고친 뒤 다시 실행하세요 — 건드리지 않았습니다."))
        return 2
    if data is None:
        print(tr("  ✓ {path} 가 없습니다 — 중복 없음", path=display_path(path)))
        return 0
    new_data, removed = apply_dedupe(data)
    if not removed:
        print(tr("  ✓ {path} 에 FarShell 훅이 없습니다 — 중복 없음", path=display_path(path)))
        return 0
    verb = tr("제거 예정") if dry_run else tr("제거")
    for event, args, tag in removed:
        print(f"  · {verb}: {event} — {SCRIPT_NAME} {args}" + (f" [{tag}]" if tag else tr(" [태그 없음]")))
    if dry_run:
        print(tr("  ⓘ --dry-run: {path} 는 바꾸지 않았습니다 ({n}개)", path=display_path(path), n=len(removed)))
        return 0
    try:
        backup = write_hooks_json(path, new_data, raw, stamp)
    except OSError as exc:
        print(tr("  ✗ 쓰기 실패: {error}", error=exc))
        return 2
    print(tr("  ✓ {path} 에서 {n}개 제거 (백업: {backup})", path=display_path(path), n=len(removed), backup=display_path(backup)))
    print(tr("  ⓘ 이미 실행 중인 codex 세션에는 적용되지 않습니다 — 새로 시작하세요."))
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        return _cmd_status()
    if command == "install":
        return _cmd_install()
    if command == "uninstall":
        return _cmd_uninstall()
    if command == "duplicates":
        return _cmd_duplicates()
    if command == "dedupe":
        extra = argv[2:]
        if any(a != "--dry-run" for a in extra):
            print(tr("사용법: codex_hooks.py dedupe [--dry-run]"), file=sys.stderr)
            return 64
        return _cmd_dedupe("--dry-run" in extra)
    print(tr("사용법: codex_hooks.py [status|install|uninstall|duplicates|dedupe]"), file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
