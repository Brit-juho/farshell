"""A0 — Claude Code 훅(`~/.claude/settings.json`) 멱등 등록기.

왜 필요한가: FarShell의 에이전트 상태(working/done, 큐 자동 투입, TTS 요약)는
전부 Claude Code 훅이 `POST /api/agent/event`를 때려줘야 돌아간다. 그런데
지금까지 그 등록은 **문서로만** 안내됐고(README의 JSON 예시), 실제로 등록해
본 사용자가 아무도 없으면 서버는 영원히 이벤트를 한 건도 못 받는다 —
실제로 이 저장소 개발자의 설정 파일에도 `agent_hook.sh`가 등록돼 있지
않았다(2026-09-07 확인). 그래서 "문서 대신 명령"으로 바꾼다.

설계 원칙(이 파일이 남의 설정 파일을 건드리기 때문에 전부 중요하다):

1. **멱등** — 몇 번을 돌려도 항목이 늘지 않는다. 우리 훅인지 여부는 명령
   문자열이 `agent_hook.sh`(또는 우리 `tts_hook.sh`)를 가리키는지로 판정한다.
2. **남의 훅 보존** — 사용자가 손으로 넣은 다른 훅은 절대 건드리지 않는다.
   제거도 우리가 넣은 항목만 한다.
3. **경로 이동 추적** — 저장소를 옮겼으면 옛 경로를 가리키던 우리 항목은
   지우지 않고 **새 경로로 갱신**한다(그냥 추가하면 죽은 훅이 남는다).
4. **`tts_hook.sh` 직접 등록은 `agent_hook.sh stop`으로 교체** —
   `agent_hook.sh stop`이 내부에서 `tts_hook.sh`에 stdin을 위임하므로, 둘 다
   등록돼 있으면 TTS가 두 번 재생된다.
5. **쓰기 전 백업 + 원자적 교체** — `settings.json.vtbak`으로 복사한 뒤
   같은 디렉토리의 임시 파일에 쓰고 rename. 중간에 죽어도 반쪽짜리 JSON이
   남지 않는다.
6. **읽을 수 없으면 아무것도 안 한다** — JSON이 깨져 있으면 덮어쓰지 않고
   에러로 끝낸다(사용자 설정을 날리는 것보다 실패가 낫다).

CLI:
    python claude_hooks.py status     # 등록 상태 진단 (fsh doctor가 사용)
    python claude_hooks.py install    # 등록/갱신
    python claude_hooks.py uninstall  # 우리 항목만 제거
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import config_io
import store_lock
from shell_quote import shell_quote_path
from i18n import tr

# 훅 이벤트 → agent_hook.sh 인자. Claude Code의 이벤트 이름이 키다.
EVENTS = {
    "PreToolUse": "pre",
    "PostToolUse": "post",
    # 2026-11-10 — PostToolUse는 도구가 **성공했을 때만** 온다(공식). 질문(AskUserQuestion)·승인이
    # 거절·오류로 끝나고 모델이 다른 도구를 이어 부르면 같은 tool_use_id의 post가 영영 오지
    # 않아 waiting이 TTL(2분)까지 남았다. 실패 이벤트도 같은 `tool_use_id`를 싣고 와 대기를 푼다.
    # 실행 전에 거절된 호출(권한 거부·스키마 오류)은 이 이벤트도 안 온다 — 그건 다음 pre/prompt/Stop이 푼다.
    "PostToolUseFailure": "post_failure",
    "Stop": "stop",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "StopFailure": "stop_failure",
    "TeammateIdle": "teammate_idle",
    # 2026-09-28 — 막 띄운 에이전트는 턴을 한 번 돌기 전까지 상태 엔트리가 없어
    # "모른다"였고, 그 사이 온 에이전트 메시지·큐 항목이 첫 지시를 사람이 칠 때까지
    # 묶여 있었다(M1↔M5 실측). 세션이 열리면 idle로 알린다.
    "SessionStart": "session_start",
    # 2026-10-03 — 입력 대기 알림(`permission_prompt`/`idle_prompt`). waiting의 kind를
    # 정하는 데는 **쓰지 않는다**(서버는 보조 기록만 한다) — 근거는 tool_name과 화면.
    "Notification": "notification",
    # 2026-10-04 — 허가 대화상자가 뜨기 직전에 온다. tool_name(Bash/AskUserQuestion/
    # ExitPlanMode…)으로 waiting의 kind를 훅이 정한다(훅 > 화면 > 폴백). 출력 없이
    # 끝나므로 허용·거부를 결정하지 않고 기존 대화상자가 그대로 뜬다(실측 2026-10-03).
    # 승인·거절 시점을 알리는 이벤트는 없어 해제는 다음 pre/post/stop·화면 패턴이 맡는다.
    "PermissionRequest": "permission",
    # 2026-10-13 — 설치기가 관리하지 않는 태그 없는 항목으로 등록돼 있던 둘을 정식으로 맡는다.
    # 프롬프트 제출은 턴 시작(working)을 도구 호출 전에 알리고, 세션 종료는 **반납**이다
    # (agent_status 「세션 소유자」: 완료가 아니라 엔트리를 지운다 — `/clear`마다 「완료」와
    # 큐 투입이 생기던 경로).
    "UserPromptSubmit": "prompt",
    "SessionEnd": "session_end",
}

# 이벤트별 matcher. Pre/PostToolUse는 도구 이름으로 거른다 — 우리는 모든 도구를 본다.
# SessionStart는 시작 이유로 거른다: `compact`(대화 압축)는 **작업 도중**에도 오므로
# 거기서 idle로 알리면 작업 중인 pane에 큐가 끼어든다. 서버도 같은 값을 한 번 더
# 거른다(agent_status.on_event) — 옛 설정·수동 등록이 matcher 없이 와도 안전하게.
MATCHERS = {
    "PreToolUse": "*",
    "PostToolUse": "*",
    "PostToolUseFailure": "*",
    "SessionStart": "startup|resume|clear",
    "Notification": "permission_prompt|idle_prompt",
}

# 우리 소유로 간주하는 스크립트 이름. 경로가 어디로 옮겨졌든 파일명으로 알아본다.
OUR_SCRIPTS = ("agent_hook.sh", "tts_hook.sh")

REPO_ROOT = Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    """`~/.claude/settings.json`. CLAUDE_CONFIG_DIR을 존중한다(테스트 격리에도 쓴다)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "settings.json"


def hook_command(event: str, repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    # P2-1(ADR-34) — `agent_hook.sh`의 `$2`(에이전트 이름)는 안 실으면 기본값
    # "claude"로 떨어지지만, `codex_hooks.py:39`는 이미 "codex"를 명시한다.
    # 암묵적 기본값에 기대지 말고 여기도 명시한다 — 훅 스크립트의 기본값이
    # 나중에 바뀌면 여기 아무 변경 없이 조용히 깨지는 걸 막는다.
    # 훅은 셸이 해석한다 — 설치 폴더에 공백이나 `$`·`;`가 있으면 그대로 쓰면 깨지거나 해석된다. 안전한 경로는
    # 그대로 두므로(보통의 설치) 이미 등록된 항목과 문자열이 같다.
    return f"{shell_quote_path(str(root / 'server' / 'agent_hook.sh'))} {EVENTS[event]} claude"


def load_settings(path: Path) -> dict:
    """설정을 읽는다. 파일이 없으면 빈 dict, 깨져 있으면 ValueError."""
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(tr("{path} 를 JSON으로 읽을 수 없습니다: {error}", path=path, error=e)) from e
    if not isinstance(data, dict):
        raise ValueError(tr("{path} 의 최상위가 객체가 아닙니다", path=path))
    return data


def _entry_commands(group: dict) -> list[str]:
    """matcher 그룹 하나에 들어 있는 command 문자열들."""
    if not isinstance(group, dict):
        return []
    return [
        h.get("command", "")
        for h in (group.get("hooks") or [])
        if isinstance(h, dict) and isinstance(h.get("command"), str)
    ]


def _is_ours(command: str) -> bool:
    # 명령 문자열 안에 우리 스크립트 파일명이 들어 있으면 우리 것. 경로가
    # 바뀌었거나 인자가 붙어 있어도(예: "... agent_hook.sh stop") 잡힌다.
    return any(name in command for name in OUR_SCRIPTS)


def plan(settings: dict, repo_root: Path | None = None) -> dict:
    """지금 설정을 보고 이벤트별로 무엇을 할지 판정한다(파일은 안 건드린다).

    반환: {event: ("ok" | "add" | "update", 현재 명령 또는 None)}
      ok     — 이미 정확한 명령이 등록돼 있다
      add    — 우리 항목이 아예 없다
      update — 우리 항목은 있는데 다른 것을 가리킨다(옛 경로, tts_hook 직접 등록 등)
    """
    hooks = settings.get("hooks") or {}
    result = {}
    for event in EVENTS:
        want = hook_command(event, repo_root)
        groups = hooks.get(event) or []
        found = None
        for g in groups if isinstance(groups, list) else []:
            for cmd in _entry_commands(g):
                if cmd == want:
                    found = cmd
                    break
                if _is_ours(cmd) and found is None:
                    found = cmd
            if found == want:
                break
        if found == want:
            result[event] = ("ok", found)
        elif found is None:
            result[event] = ("add", None)
        else:
            result[event] = ("update", found)
    return result


def apply_install(settings: dict, repo_root: Path | None = None) -> tuple[dict, list[str]]:
    """설정 dict에 우리 훅을 병합한 새 dict와, 사람이 읽을 변경 요약을 반환한다."""
    settings = json.loads(json.dumps(settings))  # 깊은 복사 — 입력을 변형하지 않는다
    hooks = settings.setdefault("hooks", {})
    changes: list[str] = []

    for event in EVENTS:
        want = hook_command(event, repo_root)
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        replaced = False
        kept: list[dict] = []
        for g in groups:
            cmds = _entry_commands(g)
            ours = [c for c in cmds if _is_ours(c)]
            if not ours:
                kept.append(g)  # 남의 훅 — 그대로 보존
                continue
            if len(cmds) > len(ours):
                # 한 그룹에 우리 것과 남의 것이 섞여 있다 — 우리 항목만 갱신하고
                # 그룹 자체는 남긴다(남의 훅을 그룹째 날리지 않기 위해).
                for h in g.get("hooks") or []:
                    if isinstance(h, dict) and _is_ours(h.get("command", "")):
                        if h["command"] != want:
                            changes.append(f"{event}: {h['command']} → {want}")
                        h["command"] = want
                        replaced = True
                kept.append(g)
                continue
            if replaced:
                # 우리 항목이 두 개 이상 — 첫 번째만 남기고 나머지는 버린다(중복 제거).
                changes.append(tr("{event}: 중복 항목 제거 ({command})", event=event, command=ours[0]))
                continue
            if ours[0] != want:
                changes.append(f"{event}: {ours[0]} → {want}")
            for h in g.get("hooks") or []:
                if isinstance(h, dict) and _is_ours(h.get("command", "")):
                    h["command"] = want
            kept.append(g)
            replaced = True

        if not replaced:
            entry: dict = {"hooks": [{"type": "command", "command": want}]}
            if event in MATCHERS:
                entry["matcher"] = MATCHERS[event]
            kept.append(entry)
            changes.append(tr("{event}: 등록 ({command})", event=event, command=want))

        hooks[event] = kept

    return settings, changes


def refresh_installed(settings: dict, repo_root: Path | None = None) -> tuple[dict, list[str]]:
    """이미 들어 있는 우리 훅의 명령(스크립트 경로)만 현재 체크아웃 것으로 고친다.

    apply_install과 달리 없는 이벤트·항목을 절대 추가하지 않고 아무것도 지우지 않는다
    (사람이 일부러 뺀 훅을 되살리지 않기 위해). 한 그룹에 남의 훅이 섞여 있어도 우리 항목만 바뀐다.
    """
    settings = json.loads(json.dumps(settings))
    changes: list[str] = []
    hooks = settings.get("hooks")
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        if event not in EVENTS or not isinstance(groups, list):
            continue
        want = hook_command(event, repo_root)
        for g in groups:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and isinstance(h.get("command"), str) and _is_ours(h["command"]):
                    if h["command"] != want:
                        changes.append(f"{event}: {h['command']} → {want}")
                        h["command"] = want
    return settings, changes


def apply_uninstall(settings: dict) -> tuple[dict, list[str]]:
    """우리가 넣은 항목만 제거한다. 남의 훅과 빈 이벤트 키는 그대로 둔다."""
    settings = json.loads(json.dumps(settings))
    hooks = settings.get("hooks")
    changes: list[str] = []
    if not isinstance(hooks, dict):
        return settings, changes

    for event in EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            cmds = _entry_commands(g)
            ours = [c for c in cmds if _is_ours(c)]
            if not ours:
                kept.append(g)
                continue
            if len(cmds) > len(ours):
                g["hooks"] = [
                    h for h in (g.get("hooks") or [])
                    if not (isinstance(h, dict) and _is_ours(h.get("command", "")))
                ]
                kept.append(g)
            for c in ours:
                changes.append(tr("{event}: 제거 ({command})", event=event, command=c))
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    return settings, changes


_UNCHECKED = object()


def write_settings(path: Path, data: dict, expected_stamp=_UNCHECKED) -> Path | None:
    """백업 후 원자적으로 쓴다. 백업 경로를 반환(원본이 없었으면 None).

    `expected_stamp`를 주면(읽기 직전의 `config_io.stamp`) 쓰기 직전에 파일이 그대로인지
    다시 확인하고, 바뀌었으면 `ConfigWriteRefused`다. 링크는 따라가고 권한은 물려받는다
    (`config_io`).
    """
    target = config_io.resolve_target(path)
    if expected_stamp is not _UNCHECKED:
        config_io.ensure_unchanged(path, expected_stamp)
    backup = None
    if target.exists():
        backup = target.with_suffix(target.suffix + ".vtbak")
        shutil.copy2(target, backup)
    config_io.atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return backup


# ── CLI ───────────────────────────────────────────────────────────────────
def _cmd_status() -> int:
    path = settings_path()
    try:
        settings = load_settings(path)
    except ValueError as e:
        print(f"broken\t{e}")
        return 2
    st = plan(settings)
    for event, (state, cur) in st.items():
        print(f"{event}\t{state}\t{cur or '-'}")
    return 0 if all(s == "ok" for s, _ in st.values()) else 1


def _cmd_install() -> int:
    path = settings_path()
    try:
        with config_io.locked(path):
            stamp = config_io.stamp(path)
            try:
                settings = load_settings(path)
            except ValueError as e:
                print(f"  ✗ {e}")
                print(tr("  설정 파일을 고친 뒤 다시 실행하세요 — 덮어쓰지 않았습니다."))
                return 2
            merged, changes = apply_install(settings)
            if not changes:
                print(tr("  ✓ 이미 등록돼 있습니다 ({path})", path=path))
                return 0
            backup = write_settings(path, merged, stamp)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as e:
        print(f"  ✗ {e}")
        return 2
    for c in changes:
        print(f"  · {c}")
    print(tr("  ✓ {path} 갱신", path=path) + (tr(" (백업: {backup})", backup=backup) if backup else ""))
    print(tr("  ⓘ 이미 실행 중인 claude 세션에는 적용되지 않습니다 — 새로 시작하세요."))
    return 0


def _cmd_uninstall() -> int:
    path = settings_path()
    try:
        with config_io.locked(path):
            stamp = config_io.stamp(path)
            try:
                settings = load_settings(path)
            except ValueError as e:
                print(f"  ✗ {e}")
                return 2
            merged, changes = apply_uninstall(settings)
            if not changes:
                print(tr("  ✓ 등록된 FarShell 훅이 없습니다"))
                return 0
            backup = write_settings(path, merged, stamp)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as e:
        print(f"  ✗ {e}")
        return 2
    for c in changes:
        print(f"  · {c}")
    print(tr("  ✓ {path} 갱신", path=path) + (tr(" (백업: {backup})", backup=backup) if backup else ""))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        return _cmd_status()
    if cmd == "install":
        return _cmd_install()
    if cmd == "uninstall":
        return _cmd_uninstall()
    print(tr("사용법: claude_hooks.py [status|install|uninstall]"), file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
