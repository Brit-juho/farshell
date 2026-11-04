"""One-shot, idle-only native CLI resume. No prompt interruption or kill fallback."""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path

import agent_config
import agent_config_context as contexts
import agent_status
import config_io
import hook_identity
import mcp_plans
import pane_input
import tmux_runner
import vt_paths
from i18n import tr

_jobs: dict[str, dict] = {}
_lock = threading.Lock()



def _wait_exited(pid: int, tries: int = 100, delay: float = .2) -> bool:
    """`/exit` 를 보낸 CLI 가 끝났는지 기다린다 — `ps` 표에서 사라졌거나, 끝났지만 부모(tmux)가 아직 수거하지 않은
    좀비(`hook_identity._ps_zombies`)면 끝난 것이다. 못 끝나면 False(그러면 재시작을 중단한다)."""
    for _ in range(tries):
        if pid not in (hook_identity._ps_table() or {}) or pid in hook_identity._ps_zombies():
            return True
        time.sleep(delay)
    return False

def _pane_state(pane: str) -> tuple[bool, int | None]:
    """pane 의 (종료 여부, 첫 프로세스 pid). tmux 한 번 호출로 읽는다.

    종료된 pane(`remain-on-exit` 로 남은 칸 — CLI 가 pane 의 첫 프로세스일 때 `/exit` 뒤 상태)은 tmux 3.8 에서
    `#{pane_pid}` 가 빈 줄이다(살아 있으면 pid). 그래서 「pid 를 모름」을 두 경우로 가른다 — 종료된 pane 은
    점유할 프로세스가 없으니 확인할 것이 없고, 살아 있는데 pid 를 못 읽었으면 확인을 못 한 것이다.
    pid 는 양의 정수일 때만 돌려주고 나머지(빈 값·`\\n`·숫자 아님)는 None. tmux 호출 자체가 실패하면 ValueError.
    """
    text = tmux_runner.run_text(["display-message", "-p", "-t", pane, "#{pane_dead}\t#{pane_pid}"])
    if text is None:
        raise ValueError(tr("터미널 프로세스를 확인하지 못했습니다"))
    parts = text.rstrip("\r\n").split("\t")
    dead = parts[0].strip() == "1"
    raw = parts[1].strip() if len(parts) > 1 else ""
    pid = int(raw) if raw.isascii() and raw.isdigit() and int(raw) > 0 else None
    return dead, pid


def _ensure_pane_free(pane: str) -> None:
    """CLI 가 끝난 자리에 다른 프로세스가 앉았으면 ValueError — 그 위에 새 CLI 를 덮어쓰지 않는다.

    종료된 pane 에는 점유할 프로세스가 없다(tmux 가 pty 를 닫아 자식도 남지 못한다 — 3.8 에서 실측).
    살아 있는데 pid 를 못 읽었으면 확인을 못 한 것이니 덮어쓰지 않고 중단한다."""
    dead, pane_pid = _pane_state(pane)
    if dead:
        return
    if pane_pid is None:
        raise ValueError(tr("터미널 프로세스를 확인하지 못했습니다"))
    table = hook_identity._ps_table()
    if table is None:
        raise ValueError(tr("터미널 프로세스를 확인하지 못했습니다"))
    zombies = hook_identity._ps_zombies()
    from share_identity import chain_reaches
    for pid, (_parent, pgid, tpgid, comm) in table.items():
        if pid in zombies:
            continue   # 이미 끝난 프로세스다 — 수거가 늦을 뿐 터미널을 쓰고 있지 않다
        if pgid == tpgid and not hook_identity.is_shell(comm) and chain_reaches(pid, pane_pid):
            raise ValueError(tr("다른 프로세스가 터미널을 사용해 재시작을 중단했습니다"))


def _owner(ctx):
    entries = [e for e in agent_status.visible_state().values() if e.get("host", "local") == "local"
               and e.get("tmux_session") == ctx["session"] and e.get("agent") == ctx["tool"]]
    if len(entries) != 1 or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", entries[0].get("session_id", "")):
        raise ValueError(tr("이어갈 대화 ID를 확인하지 못했습니다"))
    return entries[0]


def _argv(ctx, sid):
    args = ctx["argv"]
    if not args or Path(args[0]).name not in ("claude", "codex") or "runtime-" in ctx["root"]:
        raise ValueError(tr("이 실행 방식은 자동 재시작을 지원하지 않습니다 — CLI에서 직접 다시 시작하세요"))
    tool = ctx["tool"]
    binary = args[0] if Path(args[0]).is_absolute() else shutil.which(args[0], path=ctx["env"].get("PATH"))
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        raise ValueError(tr("기존 CLI 실행 파일을 확인하지 못했습니다"))
    # Preserve only explicitly understood startup flags. Do not silently discard
    # policy flags, inline config overrides, hooks, wrappers or positional prompts.
    flags = []
    i = 1
    while i < len(args):
        flag = args[i]
        if flag in ("--resume", "-r") or (tool == "codex" and flag == "resume"):
            if i + 1 >= len(args) or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", args[i + 1]):
                raise ValueError(tr("기존 실행 인자를 안전하게 재현할 수 없습니다"))
            i += 2
        elif flag in ("--model", "-m", "--profile") or (tool == "codex" and flag == "-p"):
            if i + 1 >= len(args) or not re.fullmatch(r"[A-Za-z0-9_.:/\[\]-]{1,160}", args[i + 1]):
                raise ValueError(tr("기존 실행 인자를 안전하게 재현할 수 없습니다"))
            flags += args[i:i + 2]
            i += 2
        else:
            raise ValueError(tr("기존 실행 인자를 안전하게 재현할 수 없습니다"))
    return [binary, *flags, *( ["--resume", sid] if tool == "claude" else ["resume", sid])]


def _empty_prompt(ctx) -> bool:
    screen = tmux_runner.run_text(["capture-pane", "-p", "-t", ctx["pane"], "-S", "-16"])
    if not screen:
        return False
    # Fail closed for changed prompt formats or any composer draft. ANSI is
    # already stripped by tmux capture-pane (without -e).
    lines = screen.splitlines()
    prompts = [i for i, line in enumerate(lines) if re.match(r"^\s*[›❯]\s?", line)]
    if not prompts or not re.fullmatch(r"\s*[›❯]\s*", lines[prompts[-1]]):
        return False
    footer = re.compile(r"\s*(?:\? for shortcuts|\d+% context left(?:\s*[·|].*)?|\d+ tokens? left|[─━]+)\s*")
    return all(not line.strip() or footer.fullmatch(line) for line in lines[prompts[-1] + 1:])


def preview(session: str) -> dict:
    ctx = contexts.identify(session)
    owner = _owner(ctx)
    argv = _argv(ctx, owner["session_id"])
    return {**mcp_plans.issue("agent-restart", {"session": session, "identity": ctx["identity"],
                       "sid": owner["session_id"], "argv": argv}),
            "context": contexts.public(ctx), "state": owner["status"],
            "note": tr("작업·승인 대기·입력 중에는 기다립니다. 정상 종료 후 같은 대화를 이어갑니다")}


def start(token: str) -> dict:
    plan = mcp_plans.take(token, "agent-restart")
    if not plan:
        raise ValueError(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    ctx = contexts.identify(plan["session"])
    if ctx["identity"] != plan["identity"] or _owner(ctx)["session_id"] != plan["sid"]:
        raise ValueError(tr("선택한 세션이 바뀌었습니다. 다시 검토하세요"))
    with _lock:
        if any(j["session"] == plan["session"] and j["state"] in ("waiting", "restarting") for j in _jobs.values()):
            raise ValueError(tr("이 세션의 재시작이 이미 예약되어 있습니다"))
        for key in list(_jobs):
            if _jobs[key].get("finished_at", time.time()) < time.time() - 3600:
                del _jobs[key]
        if len(_jobs) >= 128:
            raise ValueError(tr("재시작 작업이 너무 많습니다"))
        job_id = secrets.token_hex(16)
        _jobs[job_id] = {"id": job_id, "session": plan["session"], "state": "waiting", "created_at": time.time()}
    threading.Thread(target=_run, args=(job_id, plan), daemon=True, name="agent-config-resume").start()
    return status(job_id)


def status(job_id: str) -> dict:
    with _lock:
        if job_id not in _jobs:
            raise ValueError(tr("재시작 작업을 찾지 못했습니다"))
        return dict(_jobs[job_id])


def list_jobs() -> dict:
    with _lock:
        return {"jobs": [dict(job) for job in _jobs.values() if job["state"] in ("waiting", "restarting")]}


def cancel(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job["state"] != "waiting":
            raise ValueError(tr("대기 중인 재시작만 취소할 수 있습니다"))
        job.update(state="cancelled", finished_at=time.time())
        return dict(job)


def _set(job_id, **values):
    with _lock:
        _jobs[job_id].update(values)


def _run(job_id, plan):
    launch_file = None
    remain = None
    pane = None
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if status(job_id)["state"] == "cancelled":
                return
            ctx = contexts.identify(plan["session"])
            if ctx["identity"] != plan["identity"]:
                raise ValueError(tr("선택한 세션이 바뀌었습니다. 다시 검토하세요"))
            owner = _owner(ctx)
            if owner["session_id"] != plan["sid"]:
                raise ValueError(tr("이어갈 대화 ID를 확인하지 못했습니다"))
            if owner["status"] not in ("idle", "done") or pane_input.human_typing(ctx["session"], 5) or not _empty_prompt(ctx):
                time.sleep(2)
                continue
            pane = ctx["pane"]
            with pane_input.lock(pane):
                if pane_input.human_typing(ctx["session"], 5) or not _empty_prompt(ctx) or _owner(ctx)["status"] not in ("idle", "done"):
                    continue
                # Cancel and the irreversible phase transition share the same lock.
                with _lock:
                    if _jobs[job_id]["state"] == "cancelled":
                        return
                    _jobs[job_id]["state"] = "restarting"
                env = agent_config.prepare_launch_environment(ctx)
                folder = vt_paths.state_dir() / "agent-launch"
                folder.mkdir(parents=True, mode=0o700, exist_ok=True)
                launch_file = folder / (job_id + ".json")
                config_io.atomic_write(launch_file, json.dumps({"argv": plan["argv"], "env": env, "cwd": ctx["cwd"]}))
                os.chmod(launch_file, 0o600)
                remain = tmux_runner.run_text(["show-options", "-p", "-v", "-t", pane, "remain-on-exit"])
                if remain is None or tmux_runner.run(["set-option", "-p", "-t", pane, "remain-on-exit", "on"])[0]:
                    raise ValueError(tr("터미널 보존 설정을 확인하지 못했습니다"))
                # Native normal exit only; never send Ctrl-C, SIGKILL or an
                # arbitrary command into a busy composer.
                if contexts.identify(plan["session"])["identity"] != plan["identity"]:
                    raise ValueError(tr("선택한 세션이 바뀌었습니다. 다시 검토하세요"))
                requested_at = time.time()
                if tmux_runner.run(["send-keys", "-t", pane, "-l", "/exit"])[0] or tmux_runner.run(["send-keys", "-t", pane, "Enter"])[0]:
                    raise ValueError(tr("CLI 정상 종료 요청을 보내지 못했습니다"))
                if not _wait_exited(ctx["pid"]):
                    raise ValueError(tr("CLI가 정상 종료되지 않아 재시작을 중단했습니다"))
                # A surviving non-shell foreground process means somebody else
                # took over the pane. Never respawn over that process.
                _ensure_pane_free(pane)
                command = shlex.join([sys.executable, str(Path(__file__).with_name("agent_config_launch.py")), str(launch_file)])
                if tmux_runner.run(["respawn-pane", "-k", "-t", pane, "-c", ctx["cwd"], command])[0]:
                    raise ValueError(tr("CLI 재시작에 실패했습니다 — 터미널에서 직접 이어가세요"))
            for _ in range(100):
                time.sleep(.3)
                try:
                    fresh = contexts.identify(plan["session"])
                    owner = _owner(fresh)
                    if (fresh["pid"] != ctx["pid"] and fresh["root"] == ctx["root"]
                            and owner["session_id"] == plan["sid"]
                            and owner.get("_session_start_at", 0) > requested_at
                            and owner.get("_session_start_verified") is True
                            and owner.get("_session_start_source") == "resume"):
                        import agent_profile_runtime
                        agent_profile_runtime.note_verified_restart(fresh)
                        _set(job_id, state="resumed", runtime_state="unknown", finished_at=time.time(),
                             note=tr("같은 대화로 재시작했습니다. MCP 실제 연결은 CLI에서 확인하세요"))
                        return
                except (ValueError, OSError):
                    continue
            raise ValueError(tr("재시작 요청 후 같은 대화의 시작을 확인하지 못했습니다"))
        raise ValueError(tr("안전한 재시작 조건을 기다리다 시간이 초과됐습니다"))
    except Exception as exc:
        _set(job_id, state="failed", finished_at=time.time(),
             reason=str(exc) if isinstance(exc, ValueError) else tr("재시작 중 오류가 발생했습니다"))
    finally:
        if pane and remain is not None:
            tmux_runner.run(["set-option", "-p", "-t", pane, "remain-on-exit", remain.strip()])
        if launch_file:
            launch_file.unlink(missing_ok=True)
