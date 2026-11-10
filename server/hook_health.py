"""에이전트 훅 건강 점검 — 훅이 **지금도** 제대로 걸려 있는지 계속 본다(읽기 전용).

왜: `fsh hooks status`·`fsh doctor`·설정 → 정보는 사람이 열어 봐야 알려 준다. 훅은 Claude를 막으면 안 돼서
깨져도 조용히 실패하고(상태 배지·큐 자동 투입·TTS 가 그냥 멎는다), 과거에 gstack setup 이 settings.json 에
사라질 `runtime-*` 경로를 박아 훅이 깨진 채 며칠을 간 적이 있다.

무엇: 기동 시 1회 + 10분 간격 + 설정 파일(Claude settings.json·Codex config.toml·hooks.json)의 stamp 가 바뀌면
즉시 1회, `claude_hooks.plan` / `codex_hooks.plan_text`(읽기 전용 계획 함수)로 상태를 계산한다.

    ok          모든 관리 이벤트가 이 설치 경로로 등록돼 있다
    missing     등록이 빠진 이벤트가 있다(`fsh hooks install`)
    mismatch    다른 경로·스크립트로 등록된 이벤트가 있다(옛 설치 경로·사라질 `runtime-*` 경로 등)
    unreadable  설정 파일을 읽거나 해석하지 못했다(깨진 JSON/TOML·권한)
    absent      그 CLI 의 설정 폴더가 없다 — 쓰지 않는 CLI 라서 이상으로 보지 않는다

⚠ **자동 복구는 만들지 않는다 — 서버는 에이전트 설정 파일(~/.claude/settings.json·~/.codex/config.toml)을 쓰지
않는다**(AGENTS.md 하드 계약). 이유: 사용자·gstack·clauth 와 쓰기 경합이 나고, 원격에서 닿는 프로세스에 설정 파일
쓰기 권한을 주는 것은 공격면 증가이며, 사용자가 일부러 지운 훅을 되살리게 된다. 고치는 길은 사용자가 치는
`fsh hooks install`(또는 `fsh sync`) 하나다.

멈추지 않는 구조(docs/ref/reliability.md): 파일 읽기는 전용 풀 `hooks`(`pools.run`, 기한 `DEADLINE_SEC`)에서만 하고,
캐시는 `TimedLock` 으로 짧게만 쥔다. 읽기가 매달리면 호출자는 기한에 풀려 마지막 결과(없으면 `unknown`)를 받고,
이상으로 판정하지 않는다(멈춤은 훅 고장이 아니다).

알림: 상태 서명(CLI별 상태·빠진 이벤트 이름)이 **바뀔 때만** `/ws-workspace` 로 `hooks_health` 를 보낸다 — 경로·설정
값은 싣지 않고 CLI 이름과 이벤트 이름만. 웹 푸시는 `VT_HOOK_HEALTH_PUSH=1` 일 때만, ok → 이상 전이 때 한 번.
끄기: `VT_HOOK_HEALTH=0`(주기 점검·알림만 끈다. `GET /api/hooks/status` 는 예전처럼 요청마다 읽는다).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

POOL = "hooks"
INTERVAL_SEC = 600.0      # 파일이 안 바뀌어도 이 간격마다 다시 본다(경로 아래 파일이 사라진 경우 등)
STAMP_POLL_SEC = 5.0      # 설정 파일 stamp 만 보는 간격(stat 세 번)
DEADLINE_SEC = 8.0        # 한 번의 점검(파일 읽기)에 주는 기한

OK, MISSING, MISMATCH, UNREADABLE, ABSENT, UNKNOWN = "ok", "missing", "mismatch", "unreadable", "absent", "unknown"
_BAD = (MISSING, MISMATCH, UNREADABLE)

_lock = None   # TimedLock — 늦게 만든다(resilience import 순서)
_cache: Optional[dict] = None      # {"payload": ..., "health": ..., "stamps": ..., "checked": epoch}
_signature: Optional[tuple] = None
_inflight: Optional["asyncio.Future"] = None
_sweeper_started = False


def enabled() -> bool:
    import vt_env
    return vt_env.getenv("VT_HOOK_HEALTH", "1").strip().lower() not in ("0", "false", "no", "off")


def push_enabled() -> bool:
    import vt_env
    return vt_env.getenv("VT_HOOK_HEALTH_PUSH", "0").strip().lower() in ("1", "true", "yes", "on")


def _state_lock():
    global _lock
    if _lock is None:
        from resilience import locks
        _lock = locks.TimedLock("hook_health", default_timeout=2.0)
    return _lock


# ── 읽기 전용 점검 ─────────────────────────────────────────────────────────
def _stamp(path: Path):
    """(경로, mtime_ns, ctime_ns, size, ino) — 없으면 (경로, None). 내용은 읽지 않는다."""
    try:
        st = os.stat(path)
    except OSError:
        return (str(path), None)
    return (str(path), st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def stamps() -> tuple:
    import claude_hooks
    import codex_hooks
    return (_stamp(claude_hooks.settings_path()), _stamp(codex_hooks.settings_path()),
            _stamp(codex_hooks.hooks_json_path()), str(claude_hooks.REPO_ROOT))


def _classify(plan: dict) -> dict:
    """plan 결과 → {state, missing:[이벤트], mismatch:[이벤트], transient}. 경로는 담지 않는다."""
    missing = [e for e, (s, _c) in plan.items() if s == "add"]
    mismatch = [e for e, (s, _c) in plan.items() if s == "update"]
    transient = any(s == "update" and isinstance(c, str) and "runtime-" in c for s, c in plan.values())
    state = MISMATCH if mismatch else MISSING if missing else OK
    return {"state": state, "missing": missing, "mismatch": mismatch, "transient": transient}


def compute() -> dict:
    """설정 파일을 읽어 `GET /api/hooks/status` 응답과 CLI별 건강 상태를 만든다(블로킹 — 풀에서만 부른다)."""
    import claude_hooks
    import codex_hooks

    stamp = stamps()
    health: dict = {}
    claude_dir = claude_hooks.settings_path().parent
    codex_dir = codex_hooks.settings_path().parent

    claude_plan: dict = {}
    claude_unreadable = None
    try:
        claude_plan = claude_hooks.plan(claude_hooks.load_settings(claude_hooks.settings_path()))
    except Exception as e:  # noqa: BLE001 — 읽기·해석 실패는 모두 unreadable
        claude_unreadable = str(e)
    codex_plan: dict = {}
    codex_error = None
    try:
        codex_plan = codex_hooks.plan_text(codex_hooks.load_text(codex_hooks.settings_path()))
    except Exception as e:  # noqa: BLE001 — 읽기·해석 실패는 모두 unreadable
        codex_error = str(e)

    if claude_unreadable is not None:
        health["claude"] = {"state": UNREADABLE, "missing": [], "mismatch": [], "transient": False}
    elif not claude_dir.is_dir():
        health["claude"] = {"state": ABSENT, "missing": [], "mismatch": [], "transient": False}
    else:
        health["claude"] = _classify(claude_plan)
    if codex_error is not None:
        health["codex"] = {"state": UNREADABLE, "missing": [], "mismatch": [], "transient": False}
    elif not codex_dir.is_dir():
        health["codex"] = {"state": ABSENT, "missing": [], "mismatch": [], "transient": False}
    else:
        health["codex"] = _classify(codex_plan)

    if claude_unreadable is not None:
        payload = {"ok": False, "error": "unreadable", "reason": claude_unreadable, "events": {}}
    else:
        tools = {
            "claude": {
                "ok": all(state == "ok" for state, _ in claude_plan.values()),
                "events": {event: state for event, (state, _) in claude_plan.items()},
            },
            "codex": {
                "ok": bool(codex_plan) and all(state == "ok" for state, _ in codex_plan.values()),
                "events": {event: state for event, (state, _) in codex_plan.items()},
            },
        }
        if codex_error:
            tools["codex"]["error"] = codex_error
        # hooks.json 에 복제된 FarShell 훅(Codex 앱 가져오기). 경고일 뿐 ok 에는 영향 없다.
        dup_events = [event for event, _args, _tag in codex_hooks.find_foreign_farshell_hooks()]
        tools["codex"]["duplicates"] = {"count": len(dup_events), "events": dup_events}
        payload = {
            "ok": all(tool["ok"] for tool in tools.values()),
            # 기존 클라이언트 호환: 최상위 events는 계속 Claude 이벤트다.
            "events": tools["claude"]["events"],
            "tools": tools,
        }
    return {"payload": payload, "health": health, "stamps": stamp, "checked": time.time()}


def _signature_of(health: dict) -> tuple:
    return tuple((cli, h["state"], tuple(sorted(h["missing"] + h["mismatch"]))) for cli, h in sorted(health.items()))


def is_bad(health: dict) -> bool:
    return any(h["state"] in _BAD for h in health.values())


def event_for(health: dict) -> dict:
    """`/ws-workspace` 로 보내는 내용 없는 알림 — CLI 이름·상태·이벤트 이름만. 경로·설정 값은 싣지 않는다."""
    return {
        "type": "hooks_health",
        "ok": not is_bad(health),
        "tools": {cli: {"state": h["state"], "events": sorted(h["missing"] + h["mismatch"])}
                  for cli, h in sorted(health.items())},
    }


def _commit(result: dict) -> Optional[tuple[bool, bool]]:
    """결과를 캐시에 올린다. 서명이 바뀌었으면 (알릴지, 푸시할지)를 돌려준다. 처음 계산은 기준선이라 알리지 않는다."""
    global _cache, _signature
    sig = _signature_of(result["health"])
    with _state_lock().hold(2.0, op="hook_health.commit"):
        prev_sig = _signature
        _cache = result
        _signature = sig
    if prev_sig is None or prev_sig == sig:
        return None
    prev_bad = any(state in _BAD for _cli, state, _ev in prev_sig)
    return True, (not prev_bad) and is_bad(result["health"])


def cached() -> Optional[dict]:
    with _state_lock().hold(2.0, op="hook_health.cached"):
        return _cache


def _fresh(entry: Optional[dict]) -> bool:
    if not entry or time.time() - entry["checked"] >= INTERVAL_SEC:
        return False
    try:
        return entry["stamps"] == stamps()
    except Exception:  # noqa: BLE001
        return False


def _check_blocking(force: bool) -> dict:
    """풀 안에서 도는 한 번의 점검: 캐시가 신선하면 그대로, 아니면 다시 읽는다."""
    entry = cached()
    if not force and _fresh(entry):
        return {"result": entry, "fresh": True}
    return {"result": compute(), "fresh": False}


async def refresh(force: bool = False) -> dict:
    """점검 결과(`payload`·`health`·`checked`). 읽기가 기한을 넘기면 마지막 결과(없으면 `unknown`)를 돌려준다."""
    global _inflight
    from resilience import pools
    if (_inflight is not None and not _inflight.done() and not force
            and _inflight.get_loop() is asyncio.get_running_loop()):
        fut = _inflight
    else:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _inflight = fut
        try:
            out = await pools.run(POOL, _check_blocking, force, op="hook_health.check", key="agent-settings",
                                  timeout=DEADLINE_SEC)
            result = out["result"]
            if not out["fresh"]:
                change = _commit(result)
                if change:
                    await _announce(result["health"], push=change[1])
            fut.set_result(result)
            return result
        except BaseException as e:  # noqa: BLE001
            stale = _stale_result(e)
            if not fut.done():
                fut.set_result(stale)
            if isinstance(e, asyncio.CancelledError):
                raise
            return stale
    return await asyncio.wait_for(asyncio.shield(fut), DEADLINE_SEC + 2)


def _stale_result(exc: BaseException) -> dict:
    """읽기가 기한을 넘기거나 풀이 가득 찼다 — 훅 고장이 아니므로 이상으로 판정하지 않는다."""
    logger.warning(f"[hook_health] 점검을 건너뜀({type(exc).__name__})")
    last = None
    try:
        last = cached()
    except Exception:  # noqa: BLE001
        pass
    if last:
        return {**last, "stale": True}
    return {"payload": {"ok": False, "error": "unknown", "reason": "check_timeout", "events": {}},
            "health": {}, "stamps": (), "checked": None, "stale": True}


async def _announce(health: dict, push: bool) -> None:
    try:
        import routes.system as system_routes
        await system_routes.broadcast_workspace_event(event_for(health))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[hook_health] 알림 실패: {e}")
    if push and push_enabled():
        try:
            import push as push_mod
            from i18n import tr
            await asyncio.to_thread(push_mod.send, tr("훅 점검 필요"),
                                    tr("에이전트 훅이 빠졌거나 어긋났습니다 — 터미널에서 fsh hooks status"), "/")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[hook_health] 푸시 실패: {e}")


async def status_payload() -> dict:
    """`GET /api/hooks/status` 의 본문. 켜져 있으면 점검기의 캐시(설정 파일 stamp 가 같을 때)를 쓴다.
    기존 응답 모양은 그대로이고 `health`(CLI별 상태)와 `checkedAt` 만 더해진다."""
    if enabled():
        result = await refresh()
    else:
        from resilience import pools
        try:
            result = await pools.run(POOL, compute, op="hook_health.compute", key="agent-settings", timeout=DEADLINE_SEC)
        except (pools.OpTimeout, pools.Busy) as e:
            result = _stale_result(e)
    body = dict(result["payload"])
    body["health"] = {cli: {"state": h["state"], "missing": list(h["missing"]), "mismatch": list(h["mismatch"]),
                            "transient": bool(h.get("transient"))} for cli, h in result["health"].items()}
    body["checkedAt"] = result["checked"]
    body["monitor"] = enabled()
    if result.get("stale"):
        body["stale"] = True
    return body


async def run_loop() -> None:
    """서버 수명 동안 돈다(main.lifespan). 기동 시 1회, 이후 stamp 변화 즉시·그 밖에는 `INTERVAL_SEC` 마다."""
    if not enabled():
        logger.info("[hook_health] VT_HOOK_HEALTH=0 — 주기 점검을 하지 않는다")
        return
    last_full = 0.0
    last_stamp = None
    while True:
        try:
            now = time.monotonic()
            from resilience import pools
            try:
                cur = await pools.run(POOL, stamps, op="hook_health.stamps", key="agent-settings", timeout=DEADLINE_SEC)
            except (pools.OpTimeout, pools.Busy):
                cur = last_stamp
            if cur is not None and (cur != last_stamp or now - last_full >= INTERVAL_SEC):
                await refresh(force=True)
                last_full, last_stamp = now, cur
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 한 번 실패해도 루프는 산다
            logger.warning(f"[hook_health] 점검 실패: {e}")
        await asyncio.sleep(STAMP_POLL_SEC)


def reset_for_tests() -> None:
    global _cache, _signature, _inflight
    _cache = None
    _signature = None
    _inflight = None


# ── `fsh doctor` 가 부르는 한 줄 ────────────────────────────────────────────
def _cli_last_check() -> int:
    """서버가 떠 있으면 점검기의 마지막 확인 시각을 한 줄로 찍는다: `<state>\\t<epoch>\\t<monitor>`. 못 닿으면 아무것도 안 찍는다."""
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import fsh_client
        port = os.environ.get("VT_PORT", "7777")
        token = os.environ.get("VT_AUTH_TOKEN") or os.environ.get("VT_TOKEN") or ""
        status, data = fsh_client.call_json("GET", "/api/hooks/status", timeout=4, svc="cli",
                                            server=os.environ.get("VT_SERVER") or f"http://localhost:{port}",
                                            token=token, port=port)
    except Exception:  # noqa: BLE001
        return 0
    if status != 200 or not isinstance(data, dict):
        return 0
    health = data.get("health") or {}
    state = "bad" if any((h or {}).get("state") in _BAD for h in health.values()) else "ok"
    print(f"{state}\t{data.get('checkedAt') or ''}\t{1 if data.get('monitor') else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_last_check() if sys.argv[1:] == ["last-check"] else 2)
