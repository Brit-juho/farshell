"""업데이트 확인(읽기 전용)과 자동 적용(기본 꺼짐) — `server/updater.py` 위에 얹는다.

이 모듈은 검증 로직을 새로 만들지 않는다. 태그 서명·이름 대조·`origin/master` 이력 확인은 전부
`updater.resolve_target`/`verify_tag` 이고, 코드를 옮기고 빌드하고 실패하면 되돌리는 것은 `updater.cmd_update` 다.

## 1단계 — 확인 (네트워크는 `git fetch` 하나)
- 서버가 뜬 뒤 지터(1~10분) 뒤에 한 번, 이후 24시간마다 `check_once()`. 설정 `check` 가 꺼져 있거나
  `VT_UPDATE_CHECK=0` 이면 **어떤 네트워크 요청도 하지 않는다**(수동 「지금 확인」도 거절).
- 보내는 것은 git 이 원격에 원래 보내는 요청뿐이다 — GitHub API·토큰·사용자 식별 정보는 쓰지 않는다.
- 모든 git 은 `updater.hardened()` 안에서 `resilience.proc.run`(새 세션, 기한에 그룹째 SIGKILL)으로 돌고, 호출은
  `pools.run("net", …, timeout=)` 이다. 점검이 서버를 멈출 수 없다.
- 결과는 `$VT_STATE_DIR/update-status.json` 에 원자적으로 쓴다(서버와 적용 자식이 같이 쓰므로 파일 잠금 안에서 읽고-고치고-쓴다).

## 2단계 — 자동 적용 (`update.auto`, 기본 `off`)
`stable-only` 일 때만, 아래가 **전부** 맞을 때만 적용한다 (`auto_blockers`). 하나라도 틀리면 이유를 status 에 남기고 알리기만 한다.
채널이 `stable` 로 저장돼 있다 · 브랜치가 master 이거나 분리된 HEAD · 추적 파일에 로컬 변경 없음 · 신뢰 키가 고정돼 있다
(`fsh update trust` — 자동 신뢰는 없다) · 서명 검증과 `origin/master` 이력 통과 · 메이저 버전이 오르지 않는다.
시점: 지역 시간 `auto_hour` 시 안에서, 최근 N분(기본 15) 사람 입력이 없고 working/waiting 에이전트가 없을 때. 못 맞추면 다음
틱(5분)으로 미루고 처음 본 날로부터 7일이 지나면 멈추고 알린다. 같은 버전의 실패는 3번까지.
적용은 **분리된 자식 프로세스**(`python -m update_check apply`, 단일 실행 잠금 `update.lock`)가 `updater.cmd_update` 로 하고,
서버는 코드를 덮어쓰지 않는다. 자식이 성공하면 서버가 화면들에 `update_restarting` 을 보낸 뒤 `fsh restart` 를 띄운다
(tmux 세션은 서버가 아니라 tmux 서버 소유라 유지된다).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import config_io
import store_lock
import updater
from i18n import tr
from resilience.locks import TimedLock

logger = logging.getLogger(__name__)

AUTO_MODES = ("off", "stable-only")
DEFAULT_SETTINGS = {"check": True, "auto": "off", "auto_hour": 4}

CHECK_INTERVAL = 24 * 3600.0       # 점검 주기
FIRST_DELAY = (60.0, 600.0)        # 기동 뒤 첫 점검까지의 지터
TICK_SEC = 300.0                   # 로컬 판단 주기(네트워크 없음)
FETCH_TIMEOUT = 60                 # git fetch 기한(초)
CHECK_DEADLINE = 150.0             # 풀 기한 — 원격 확인 + 서명 검증 몇 번
FRESH_SEC = 6 * 3600.0             # 적용 직전에 이보다 낡은 점검이면 한 번 더 fetch
DEFER_MAX_SEC = 7 * 24 * 3600.0    # 적용을 미루는 최대 기간
MAX_ATTEMPTS = 3                   # 같은 버전의 자동 적용 시도 상한
APPLY_TIMEOUT = 3600.0             # 자식 프로세스 기한
RESTART_NOTICE_SEC = 2.0           # update_restarting 을 보낸 뒤 재시작까지
_TAG_RE = updater._TAG_RE


# ── 경로 ─────────────────────────────────────────────────────────────────
def status_path() -> Path:
    return updater.state_dir() / "update-status.json"


def settings_path() -> Path:
    return updater.state_dir() / "update-settings.json"


def _status_lock() -> Path:
    return updater.state_dir() / "update-status.lock"


def log_path() -> Path:
    return updater.state_dir() / "update-auto.log"


# ── 설정 ─────────────────────────────────────────────────────────────────
def load_settings() -> dict:
    """저장된 설정. 파일이 없거나 깨졌거나 값이 이상하면 그 항목만 기본값."""
    out = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("check"), bool):
        out["check"] = raw["check"]
    if raw.get("auto") in AUTO_MODES:
        out["auto"] = raw["auto"]
    h = raw.get("auto_hour")
    if isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23:
        out["auto_hour"] = h
    return out


def env_check_off() -> bool:
    return os.environ.get("VT_UPDATE_CHECK", "").strip().lower() in ("0", "false", "no", "off")


def check_enabled(settings: dict | None = None) -> bool:
    return not env_check_off() and bool((settings or load_settings())["check"])


def validate_patch(patch: object) -> dict:
    """설정 변경 요청 검증. 알 수 없는 키·형식 오류는 ValueError(메시지는 사용자에게 보여도 된다)."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError(tr("바꿀 설정이 없습니다"))
    out: dict = {}
    for k, v in patch.items():
        if k == "check":
            if not isinstance(v, bool):
                raise ValueError(tr("update.check 는 true/false 입니다"))
        elif k == "auto":
            if v not in AUTO_MODES:
                raise ValueError(tr("update.auto 는 off 또는 stable-only 입니다"))
        elif k == "auto_hour":
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 23:
                raise ValueError(tr("update.auto_hour 는 0~23 입니다"))
        else:
            raise ValueError(tr("알 수 없는 설정입니다: {key}", key=str(k)[:40]))
        out[k] = v
    return out


def save_settings(patch: dict) -> dict:
    """검증된 `patch` 를 합쳐 원자적으로 쓴다. 쓴 뒤의 전체 설정을 돌려준다."""
    patch = validate_patch(patch)
    with store_lock.exclusive(_status_lock(), deadline=3.0):
        cur = load_settings()
        cur.update(patch)
        config_io.atomic_write(settings_path(), json.dumps(cur, sort_keys=True) + "\n")
    return cur


# ── 상태 파일 ────────────────────────────────────────────────────────────
def empty_status() -> dict:
    return {
        "checked_at": None, "channel": None, "current": None, "latest": None, "latest_tag": None,
        "available": False, "signature_ok": None, "signer": None, "error": None,
        "blocked": None,                      # 이 설치가 업데이트를 못 받는 이유 코드(수동·자동 공통)
        "attempts": {"version": None, "count": 0},
        "auto": {"first_seen": None, "reason": None, "policy": None, "policy_tag": None},
        "last_apply": None,
    }


def _merge_defaults(raw: object) -> dict:
    out = empty_status()
    if not isinstance(raw, dict):
        return out
    for k in out:
        if k not in raw:
            continue
        if isinstance(out[k], dict):
            if isinstance(raw[k], dict):
                out[k] = {**out[k], **{kk: vv for kk, vv in raw[k].items() if kk in out[k]}}
        else:
            out[k] = raw[k]
    la = raw.get("last_apply")
    out["last_apply"] = la if isinstance(la, dict) else None
    return out


def read_status() -> dict:
    """상태 파일. 없거나 깨졌으면 빈 상태(읽기는 파일을 건드리지 않는다)."""
    try:
        raw = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_status()
    return _merge_defaults(raw)


def mutate_status(fn: Callable[[dict], None]) -> dict:
    """잠금 안에서 읽고-고치고-원자적으로 쓴다. 깨진 파일은 `.corrupt` 로 치우고 빈 상태에서 시작한다."""
    with store_lock.exclusive(_status_lock(), deadline=5.0):
        p = status_path()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError):
            try:
                os.replace(p, p.with_name(p.name + ".corrupt"))
            except OSError:
                pass
            raw = {}
        st = _merge_defaults(raw)
        fn(st)
        config_io.atomic_write(p, json.dumps(st, ensure_ascii=False, sort_keys=True) + "\n")
        return st


# ── semver ───────────────────────────────────────────────────────────────
def parse_ver(s: object) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(s or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest: object, current: object) -> bool:
    a, b = parse_ver(latest), parse_ver(current)
    return bool(a and b and a > b)


# ── 확인 ─────────────────────────────────────────────────────────────────
def _evaluate(root: Path) -> dict:
    """원격을 보고 status 의 확인 필드를 만든다. 예외를 밖으로 던지지 않는다."""
    out = {"channel": None, "current": updater.version_of(root), "latest": None, "latest_tag": None,
           "available": False, "signature_ok": None, "signer": None, "error": None, "blocked": None}
    try:
        channel = updater.effective_channel(root)
        out["channel"] = channel
        updater.fetch(root, timeout=FETCH_TIMEOUT)
        if channel == "dev":
            _, sha = updater.resolve_target(root, "dev", None)
            cur = updater.rev(root, "HEAD")
            out["latest"] = sha[:8]
            out["available"] = sha != cur and updater.git(
                root, "merge-base", "--is-ancestor", cur, sha, check=False).returncode == 0
        else:
            tags = updater.release_tags(root)
            if not tags:
                return out
            tag = tags[-1]
            out["latest_tag"], out["latest"] = tag, tag[1:]
            newer = is_newer(tag, out["current"])
            if not updater.trusted_keys():
                out["available"], out["blocked"] = newer, "no_trusted_key"
            else:
                try:
                    updater.resolve_target(root, "stable", tag)   # 서명 + 이름 + origin/master 이력
                except updater.UpdateError as e:
                    out["signature_ok"], out["error"] = False, str(e)
                    return out
                out["signature_ok"] = True
                out["signer"] = updater.signer_of_tag(root, tag)[2]
                out["available"] = newer
        if out["blocked"] is None:
            if channel is None:
                out["blocked"] = "channel_unset"
            elif channel == "devmode":
                out["blocked"] = "devmode"
            elif updater.dirty_files(root):
                out["blocked"] = "dirty"
    except updater.UpdateError as e:
        out["error"] = str(e)
    except subprocess.SubprocessError as e:   # 기한 초과 등 — 서버를 멈추지 않고 오류로 남긴다
        out["error"] = f"{type(e).__name__}"
    return out


_CHECK_LOCK = TimedLock("update.check")   # 점검은 한 번에 하나 — 주기 점검과 수동 점검이 같은 저장소를 동시에 fetch 하지 않게


def check_once(root: Path, now: float | None = None) -> dict:
    """한 번 확인하고 status 를 갱신해 돌려준다. 확인이 꺼져 있으면 네트워크 없이 현재 상태 그대로."""
    now = time.time() if now is None else now
    if not check_enabled():
        return read_status()
    with _CHECK_LOCK.hold(CHECK_DEADLINE, op="update.check"):
        with updater.hardened():
            res = _evaluate(root)

    # 원격에 닿기도 전에 실패했으면(네트워크 끊김 등) 알고 있던 최신 버전 정보는 그대로 두고 오류만 남긴다.
    failed_early = bool(res["error"]) and res["latest"] is None

    def apply(st: dict) -> None:
        old_tag = st["latest_tag"]
        if failed_early:
            st.update(error=res["error"], current=res["current"], channel=res["channel"])
        else:
            st.update(res)
        st["checked_at"] = now
        if st["latest_tag"] != old_tag:
            st["auto"].update(first_seen=None, reason=None, policy=None, policy_tag=None)
        if st["available"]:
            if st["auto"]["first_seen"] is None:
                st["auto"]["first_seen"] = now
        else:
            st["auto"].update(first_seen=None, reason=None, policy=None, policy_tag=None)
    return mutate_status(apply)


def refresh_current(root: Path) -> dict:
    """서버가 막 뜬 직후 — 네트워크 없이 `current`/`available` 만 지금 VERSION 으로 맞춘다(방금 업데이트됐을 수 있다)."""
    cur = updater.version_of(root)

    def apply(st: dict) -> None:
        st["current"] = cur
        if st["latest_tag"]:
            st["available"] = is_newer(st["latest_tag"], cur) and st["signature_ok"] is not False
            if not st["available"]:
                st["auto"].update(first_seen=None, reason=None, policy=None, policy_tag=None)
        la = st["last_apply"]
        if la and la.get("result") == "running":
            # 서버가 뜬 시점에 적용 자식이 돌고 있지 않다면(잠금이 비어 있다) 중간에 죽은 것이다.
            try:
                with store_lock.try_exclusive(updater.lock_file()):
                    st["last_apply"] = {**la, "result": "failed", "reason": tr("적용이 중간에 끊겼습니다"), "at": time.time()}
            except store_lock.LockContended:
                pass
    return mutate_status(apply)


# ── 자동 적용 판정 ───────────────────────────────────────────────────────
def auto_blockers(root: Path, tag: str, settings: dict | None = None) -> tuple[str, str] | None:
    """이 태그를 자동으로 적용해선 안 되는 이유 (코드, 메시지). 적용해도 되면 None.

    `fsh update` 가 쓰는 같은 검증(`updater.resolve_target`)을 마지막에 다시 돌린다 — 확인 시점과 적용 시점 사이에 바뀌었을 수 있다.
    """
    s = settings or load_settings()
    if s["auto"] != "stable-only":
        return "auto_off", tr("자동 업데이트가 꺼져 있습니다")
    if updater.read_channel() != "stable":
        return "channel", tr("채널이 stable 로 저장돼 있지 않아 자동으로 적용하지 않습니다 (dev·devmode·미설정 설치는 알림만)")
    if not _TAG_RE.match(tag or ""):
        return "bad_tag", tr("릴리스 태그(vX.Y.Z)가 아닙니다")
    cur, new = parse_ver(updater.version_of(root)), parse_ver(tag)
    if not cur or not new or new <= cur:
        return "not_newer", tr("현재보다 새로운 버전이 아닙니다")
    if new[0] > cur[0]:
        return "major", tr("메이저 버전이 오르는 업데이트입니다 — 직접 'fsh update' 로 하세요")
    if updater.current_branch(root) not in (None, "master"):
        return "branch", tr("릴리스 브랜치가 아닌 곳에 체크아웃돼 있어 자동으로 적용하지 않습니다")
    dirty = updater.dirty_files(root)
    if dirty:
        return "dirty", tr("로컬 변경이 있어 자동으로 적용하지 않습니다: {files}", files=", ".join(dirty[:3]))
    if not updater.trusted_keys():
        return "no_trusted_key", tr("신뢰하는 서명 키가 고정돼 있지 않습니다 — 'fsh update trust' 를 사람이 먼저 해야 합니다")
    if updater.git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/master", check=False).returncode != 0:
        return "no_master", tr("origin/master 를 확인할 수 없어 릴리스 이력을 검증하지 못합니다")
    try:
        updater.resolve_target(root, "stable", tag)
    except updater.UpdateError as e:
        return "signature", str(e)
    return None


# ── 한가함 ───────────────────────────────────────────────────────────────
def idle_minutes() -> float:
    try:
        return max(1.0, float(os.environ.get("VT_UPDATE_IDLE_MIN", "15")))
    except ValueError:
        return 15.0


def is_idle(minutes: float | None = None) -> tuple[bool, str | None]:
    """(한가한가, 한가하지 않은 이유 코드). 사람 입력이 최근 N분 안에 있었거나 working/waiting 에이전트가 있으면 아니다."""
    import agent_status
    import pane_input
    n = idle_minutes() if minutes is None else minutes
    if pane_input.last_human_input_age() < n * 60:
        return False, "terminal_input"
    for ent in agent_status.get_state().values():
        if ent.get("status") in (agent_status.WORKING, agent_status.WAITING):
            return False, "agent_busy"
    return True, None


# ── 틱(로컬 판단) ────────────────────────────────────────────────────────
Notify = Callable[[dict], Awaitable[None]]


async def _no_notify(_msg: dict) -> None:
    return None


class LoopState:
    """점검기 한 벌의 메모리 상태. 시험은 시계와 난수를 직접 준다."""

    def __init__(self, now: float, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.first_due = now + self.rng.uniform(*FIRST_DELAY)
        self.did_first = False


def _sig(st: dict) -> tuple:
    la = st.get("last_apply") or {}
    return (st["latest_tag"], st["available"], st["error"], st["auto"]["reason"], la.get("result"), la.get("at"))


async def _check_in_pool(root: Path, now: float) -> dict:
    from resilience import pools
    return await pools.run("net", check_once, root, now, op="update.check", timeout=CHECK_DEADLINE)


async def _blockers_in_pool(root: Path, tag: str, settings: dict):
    from resilience import pools

    def run():
        with updater.hardened():
            return auto_blockers(root, tag, settings)
    return await pools.run("net", run, op="update.policy", timeout=CHECK_DEADLINE)


async def _mutate(fn: Callable[[dict], None]) -> dict:
    return await asyncio.to_thread(mutate_status, fn)


async def tick(root: Path, now: float, state: LoopState, *,
               check=_check_in_pool, blockers=_blockers_in_pool, idle=is_idle,
               apply=None, notify: Notify = _no_notify, local_hour: int | None = None) -> str:
    """한 번의 판단. 무슨 일을 했는지 짧은 이름으로 돌려준다(시험이 본다)."""
    s = load_settings()
    if not check_enabled(s):
        return "disabled"
    st = await asyncio.to_thread(read_status)
    did = "idle"
    before = _sig(st)
    just_checked = False
    if now >= state.first_due and (not state.did_first or st["checked_at"] is None
                                   or now - st["checked_at"] >= CHECK_INTERVAL):
        st = await check(root, now)
        state.did_first = just_checked = True
        did = "checked"
    if s["auto"] != "stable-only" or not st["available"] or not st["latest_tag"]:
        if _sig(st) != before:
            await notify({"type": "update_status", "kind": "checked"})
        return did
    tag = st["latest_tag"]
    att = st["attempts"]
    count = att["count"] if att["version"] == tag else 0
    first_seen = now if st["auto"]["first_seen"] is None else st["auto"]["first_seen"]
    hour = time.localtime(now).tm_hour if local_hour is None else local_hour
    in_window = hour == s["auto_hour"]

    reason: str | None = None
    go = False
    policy = st["auto"]["policy"] if st["auto"]["policy_tag"] == tag else None
    if just_checked or in_window or policy is None and st["auto"]["policy_tag"] != tag:
        blocked = await blockers(root, tag, s)
        policy = blocked[0] if blocked else ""
    if policy:
        reason = policy
    elif count >= MAX_ATTEMPTS:
        reason = "gave_up"
    elif now - first_seen > DEFER_MAX_SEC:
        reason = "stalled"
    elif not in_window:
        reason = "waiting_window"
    else:
        ok, why = idle()
        if not ok:
            reason = f"busy_{why}"
        else:
            go = True

    if go and apply is not None:
        # 적용 직전: 점검이 낡았으면 한 번 더 fetch 해서 더 새 태그가 있으면 그것으로 간다.
        if st["checked_at"] is None or now - st["checked_at"] > FRESH_SEC:
            st = await check(root, now)
            if not st["available"] or not st["latest_tag"]:
                return "checked"
            tag = st["latest_tag"]
            blocked = await blockers(root, tag, s)
            if blocked:
                policy, reason, go = blocked[0], blocked[0], False
        if go:
            def mark(st2: dict) -> None:
                a = st2["attempts"]
                st2["attempts"] = {"version": tag, "count": (a["count"] if a["version"] == tag else 0) + 1}
                st2["auto"].update(reason="applying", policy=None, policy_tag=tag)
            await _mutate(mark)
            await notify({"type": "update_status", "kind": "applying"})
            await apply(root, tag)
            return "applied"

    def record(st2: dict) -> None:
        st2["auto"]["reason"] = reason
        st2["auto"]["policy"], st2["auto"]["policy_tag"] = policy, tag
        if st2["auto"]["first_seen"] is None:
            st2["auto"]["first_seen"] = first_seen
    if reason != st["auto"]["reason"] or policy != st["auto"]["policy"] or st["auto"]["first_seen"] is None:
        st = await _mutate(record)
    if _sig(st) != before:
        await notify({"type": "update_status", "kind": "reason"})
    return f"wait:{reason}"


# ── 적용(서버 쪽: 자식을 띄우고, 성공하면 알린 뒤 재시작) ─────────────────
async def apply_via_child(root: Path, tag: str, *, notify: Notify = _no_notify,
                          restart: Optional[Callable[[Path], None]] = None,
                          cmd: list[str] | None = None, restart_delay: float = RESTART_NOTICE_SEC) -> bool:
    """분리된 자식(`python -m update_check apply`)이 적용하고, 성공하면 `update_restarting` 을 보낸 뒤 재시작한다."""
    env = dict(os.environ, VT_DIR=str(root))
    argv = cmd or [sys.executable, "-m", "update_check", "apply", "--to", tag]
    try:
        log = open(log_path(), "ab")
    except OSError:
        log = subprocess.DEVNULL
    try:
        proc = await asyncio.create_subprocess_exec(*argv, cwd=str(root / "server"), env=env, stdin=subprocess.DEVNULL,
                                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        await _mutate(lambda st: st.update(last_apply=_apply_record("failed", reason=f"{type(e).__name__}", to=tag[1:])))
        await notify({"type": "update_status", "kind": "failed"})
        return False
    finally:
        if log is not subprocess.DEVNULL:
            log.close()
    try:
        rc = await asyncio.wait_for(proc.wait(), APPLY_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        await _mutate(lambda st: st.update(last_apply=_apply_record("failed", reason=tr("시간 초과"), to=tag[1:])))
        await notify({"type": "update_status", "kind": "failed"})
        return False
    st = await asyncio.to_thread(read_status)
    la = st["last_apply"] or {}
    if rc == 0 and la.get("result") == "ok":
        await notify({"type": "update_restarting", "version": la.get("to")})
        await asyncio.sleep(restart_delay)       # 소켓이 닫히기 전에 화면들이 알림을 받을 시간
        (restart or restart_server)(root)
        return True
    await notify({"type": "update_status", "kind": "failed"})
    return False


def restart_server(root: Path) -> None:
    """`fsh restart` 를 분리해서 띄운다(이 프로세스를 포함해 서버가 내려가도 계속 돈다)."""
    try:
        log = open(log_path(), "ab")
    except OSError:
        log = subprocess.DEVNULL
    try:
        subprocess.Popen([str(root / "bin" / "fsh"), "restart"], cwd=str(root), env=dict(os.environ, VT_DIR=str(root)),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def _apply_record(result: str, *, frm: str | None = None, to: str | None = None, reason: str | None = None,
                  rolled_back: bool = False) -> dict:
    return {"result": result, "at": time.time(), "from": frm, "to": to, "reason": reason, "rolled_back": rolled_back}


def record_apply(result: str, **kw) -> None:
    mutate_status(lambda st: st.update(last_apply=_apply_record(result, **kw)))


# ── 적용(자식 쪽) ────────────────────────────────────────────────────────
def apply_main(root: Path, tag: str) -> int:
    """`python -m update_check apply --to vX.Y.Z` — 잠금을 잡고, 정책을 다시 확인하고, `updater.cmd_update` 로 적용한다."""
    frm = updater.version_of(root)
    try:
        with updater.update_lock():
            record_apply("running", frm=frm, to=tag[1:])
            try:
                updater.fetch(root)
                blocked = auto_blockers(root, tag)
                if blocked:
                    record_apply("failed", frm=frm, to=tag[1:], reason=blocked[1])
                    print(f"  ✗ {blocked[1]}")
                    return 2
                res: dict = {}
                rc = updater.cmd_update(root, None, tag, False, False, do_sync=True, result=res)
            except updater.UpdateError as e:
                record_apply("failed", frm=frm, to=tag[1:], reason=str(e))
                print(f"  ✗ {e}")
                return 1
            if res.get("ok"):
                record_apply("ok", frm=frm, to=updater.version_of(root))
                return 0
            record_apply("failed", frm=frm, to=tag[1:], reason=res.get("reason") or f"exit {rc}",
                         rolled_back=bool(res.get("rolled_back")))
            return 1
    except updater.UpdateError as e:    # 잠금 — 다른 업데이트가 돌고 있다. 그쪽 기록을 덮지 않는다.
        print(f"  ✗ {e}")
        return 3


# ── 서버 루프 ────────────────────────────────────────────────────────────
async def run_loop(root: Path, notify: Notify = _no_notify) -> None:
    """lifespan 에서 `asyncio.create_task`. 틱마다 예외는 삼켜 로그만 남긴다(점검이 서버를 흔들면 안 된다)."""
    try:
        await asyncio.to_thread(refresh_current, root)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[update] 상태 갱신 실패: {type(e).__name__}: {e}")
    state = LoopState(time.time())

    async def apply(r: Path, tag: str) -> bool:
        return await apply_via_child(r, tag, notify=notify)

    while True:
        try:
            await tick(root, time.time(), state, apply=apply, notify=notify)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[update] 틱 실패: {type(e).__name__}: {e}")
        await asyncio.sleep(TICK_SEC)


async def manual_check(root: Path, notify: Notify = _no_notify) -> tuple[dict, bool]:
    """수동 「지금 확인」. (status, 실제로 확인했는가). 이미 확인 중이면 현재 상태를 그대로 돌려준다."""
    if _CHECK_LOCK.locked():
        return await asyncio.to_thread(read_status), False
    before = await asyncio.to_thread(read_status)
    st = await _check_in_pool(root, time.time())
    if _sig(st) != _sig(before):
        await notify({"type": "update_status", "kind": "checked"})
    return st, True


# ── API 모양 ─────────────────────────────────────────────────────────────
def public_status() -> dict:
    s = load_settings()
    st = read_status()
    cur = None
    try:
        cur = updater.version_of(updater.ROOT)
    except Exception:  # noqa: BLE001
        pass
    out = dict(st)
    if cur and cur != "?" and st["latest_tag"]:
        out["current"] = cur
        out["available"] = bool(st["available"]) and is_newer(st["latest_tag"], cur)
    out["settings"] = dict(s)
    out["check_enabled"] = check_enabled(s)
    out["env_off"] = env_check_off()
    out["in_progress"] = _CHECK_LOCK.locked()
    out["applicable"] = bool(out["available"] and not st["blocked"] and st["channel"] in ("stable", "dev"))
    return out


# ── CLI ─────────────────────────────────────────────────────────────────
def summary_line(st: dict | None = None, s: dict | None = None) -> tuple[str, str]:
    """(상태 코드, 버전). 코드: off | available | latest | unknown — `fsh status`/`doctor` 가 번역해서 보여 준다."""
    s = s or load_settings()
    st = st or read_status()
    if not check_enabled(s):
        return "off", ""
    if st["checked_at"] is None:
        return "unknown", ""
    if st["available"] and st["latest"]:
        return "available", st["latest"]
    if st["error"] and not st["latest_tag"]:
        return "unknown", ""
    return "latest", st["current"] or ""


def cmd_status(line: bool = False) -> int:
    s, st = load_settings(), read_status()
    code, ver = summary_line(st, s)
    if line:
        print(f"{code} {ver}".strip())
        return 0
    if code == "off":
        print(tr("  업데이트 확인: 꺼져 있습니다") + (" (VT_UPDATE_CHECK=0)" if env_check_off() else ""))
    elif code == "unknown":
        print(tr("  업데이트 확인: 아직 확인한 기록이 없습니다 (서버가 뜬 뒤 몇 분 안에 확인합니다)"))
        if st["error"]:
            print(f"    {st['error']}")
    elif code == "available":
        print(tr("  업데이트: {ver} 사용 가능 (현재 {cur}) — fsh update", ver=ver, cur=st["current"]))
    else:
        print(tr("  업데이트: 최신입니다 ({cur})", cur=st["current"]))
    if st["checked_at"]:
        print(tr("    마지막 확인: {when}", when=time.strftime("%Y-%m-%d %H:%M", time.localtime(st["checked_at"]))))
    print(tr("    자동 업데이트: {mode} (시각 {hour:02d}시)", mode=s["auto"], hour=s["auto_hour"]))
    la = st["last_apply"]
    if la:
        print(tr("    마지막 자동 적용: {result} {frm} → {to}", result=la.get("result"), frm=la.get("from"), to=la.get("to"))
              + (f" — {la['reason']}" if la.get("reason") else ""))
    return 0


def cmd_auto(root: Path, mode: str, *, yes: bool = False, hour: int | None = None) -> int:
    if mode == "status":
        s, st = load_settings(), read_status()
        print(tr("  자동 업데이트: {mode} (시각 {hour:02d}시, 한가함 {n:g}분)", mode=s["auto"], hour=s["auto_hour"], n=idle_minutes()))
        if s["auto"] == "stable-only":
            blocked = auto_blockers(root, st["latest_tag"] or "v999999.0.0", s) if st["latest_tag"] else None
            if st["latest_tag"] and blocked:
                print(tr("    지금은 적용하지 않습니다: {why}", why=blocked[1]))
            elif st["auto"]["reason"]:
                print(tr("    상태: {why}", why=st["auto"]["reason"]))
        return 0
    patch: dict = {}
    if hour is not None:
        if not 0 <= hour <= 23:
            raise updater.UpdateError(tr("--hour 는 0~23 입니다."))
        patch["auto_hour"] = hour
    if mode == "off":
        patch["auto"] = "off"
    else:
        print(tr("  자동 업데이트를 켜면 FarShell 이 네트워크에서 받은 새 릴리스 코드로 스스로 바뀌고 서버를 다시 시작합니다."))
        print(tr("    - 서명 검증을 통과한 stable 릴리스 태그만, 메이저 버전이 오르지 않을 때만 적용합니다."))
        print(tr("    - 지역 시간 {hour:02d}시 안에, 최근 {n:g}분 입력이 없고 작업 중인 에이전트가 없을 때만 합니다.",
                 hour=patch.get("auto_hour", load_settings()["auto_hour"]), n=idle_minutes()))
        print(tr("    - dev 채널·devmode·로컬 변경이 있는 설치는 절대 자동 적용하지 않습니다. 열린 화면은 잠시 끊겼다 다시 붙습니다(tmux 세션은 유지)."))
        print(tr("    - 서명 키는 사람이 'fsh update trust' 로 고정해야 합니다. 서명 키를 믿는 만큼 릴리스 계정이 침해되면 영향을 받습니다."))
        if not yes:
            if not sys.stdin.isatty():
                raise updater.UpdateError(tr("확인이 필요합니다 — 터미널에서 실행하거나 위험을 이해했다면 --yes 를 주세요."))
            if input(tr("  자동 업데이트를 켤까요? [y/N] ")).strip().lower() != "y":
                print(tr("  취소했습니다."))
                return 1
        patch["auto"] = "stable-only"
        if updater.read_channel() != "stable":
            print(tr("  ⚠ 채널이 stable 로 저장돼 있지 않아 지금은 적용되지 않습니다 (fsh update --channel stable)."))
        if not updater.trusted_keys():
            print(tr("  ⚠ 신뢰 키가 없어 적용되지 않습니다 (fsh update trust)."))
    save_settings(patch)
    print(tr("  ✓ 자동 업데이트: {mode}", mode=patch.get("auto", load_settings()["auto"])))
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="update_check")
    sub = ap.add_subparsers(dest="action", required=True)
    ap_apply = sub.add_parser("apply")
    ap_apply.add_argument("--to", required=True, metavar="vX.Y.Z")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    root = Path(os.environ.get("VT_DIR") or updater.ROOT)
    if args.action == "apply":
        if not _TAG_RE.match(args.to):
            print("  ✗ --to must be vX.Y.Z", file=sys.stderr)
            return 2
        return apply_main(root, args.to)
    st = check_once(root)
    print(json.dumps(st, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
