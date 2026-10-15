"""큐 드레인 — 쌓아둔 프롬프트를 tmux pane 에 순차 투입 (P4).

**언제 도는가**
  - 자동: Claude Code 의 stop 훅(`POST /api/agent/event`, event="stop") 직후.
    작업이 끝났다는 가장 정확한 신호다.
  - 수동: `vt queue run` / 웹 UI "지금 실행".

**자동 드레인의 한계를 분명히 해둔다.** stop 훅은 Claude Code 만 보낸다.
codex/aider/agy 는 훅이 없어서 자동 드레인이 걸리지 않는다 — 그 경우 수동 실행이
유일한 경로다. 출력 유휴(idle)로 추측해서 투입하는 방식은 검토했다가 뺐다:
빌드 로그가 잠깐 끊긴 것과 작업 완료를 구분할 수 없어서, 남의 입력 중간에
프롬프트를 끼워 넣는 사고가 난다. 조용히 틀리느니 안 하는 편이 낫다.

**투입 전 통과해야 하는 관문 4개**
  1. 유예 시간 + 사람 입력 확인 — stop 직후 잠깐 기다린 뒤, 그 세션에 사람이 최근
     입력했으면(pane_input) 넣지 않고 재시도로 미룬다. 자동 드레인에만 적용한다 —
     "지금 실행"은 사람이 직접 누른 것이다.
  2. safe_mode — 위험 명령이면 투입하지 않고 큐에 blocked 로 남긴다.
  3. 타깃 pane 생존 확인 — 없으면 큐를 유지한 채 중단한다(항목을 버리지 않는다).
  4. 한 번에 한 건 — 연속 투입하면 에이전트가 두 지시를 한 입력으로 붙여 읽는다.
  5. **타깃이 waiting(승인 대기)이 아닐 것** — A4. 승인 프롬프트가 떠 있는
     pane 에 send-keys 를 하면 큐 텍스트가 **승인 답변으로 소비된다**
     (`tmux_target.send_to_tmux` 는 무조건 Enter 를 붙인다). 텍스트가 유실되는
     정도가 아니라 사용자가 승인한 적 없는 동작이 승인돼버리는 안전 문제다.

**`waiting` 은 완료 판정이 아니라 투입 금지 신호다.** 위의 "출력 유휴로 추측한
완료 판정은 빼기로 했다"는 결정과 충돌하지 않는다 — waiting 이 아니라고 해서
투입하는 게 아니고(그건 여전히 stop 훅만 한다), waiting 이면 **투입하지 않을**
뿐이다. 방향이 한쪽뿐이라 오탐이 나도 "안 넣는다"로만 기운다.
"""

from __future__ import annotations

import asyncio
import logging
import os

import queue_store
import tmux_target
from i18n import tr

logger = logging.getLogger(__name__)


def _grace_sec() -> float:
    # 예전 기본값 3초는 "사람이 타이핑을 시작했을 수 있다"를 시간으로만 막았다. 이제
    # 실제 입력을 확인하므로(pane_input) 묶음 출력이 가라앉을 1초만 기다린다.
    try:
        return max(0.0, float(os.environ.get("VT_QUEUE_GRACE_SEC", "1")))
    except ValueError:
        return 1.0


def _sweep_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("VT_QUEUE_SWEEP_SEC", "60")))
    except ValueError:
        return 60.0


def autodrain_enabled() -> bool:
    return os.environ.get("VT_QUEUE_AUTODRAIN", "1").strip() not in ("0", "false", "no", "off")


# 동시에 두 드레인이 돌면 순서가 깨진다. 프로세스 내 단일 소유자로 만든다.
_drain_lock = asyncio.Lock()
# 예약은 (세션, 범위)마다 하나. 예전엔 서버 전체에 하나뿐이라, 한 세션의 stop 유예(1초) 동안 다른
# 세션의 SessionStart 예약이 조용히 버려졌다(2026-10-13 위임 E2E: 리드의 턴 종료와 하위 세션 시작이
# 겹쳐 하위 세션의 첫 지시가 큐에 남았다). 실제 투입은 여전히 _drain_lock 하나로 직렬화된다.
_pending_tasks: dict[tuple, asyncio.Task] = {}
# 서버 이벤트 루프. 워커 스레드(to_thread로 도는 위임·메시지 처리)에서 예약할 때 쓴다 —
# 스레드에서 create_task를 부르면 루프가 없어 예약이 조용히 버려졌다.
_loop: asyncio.AbstractEventLoop | None = None


def _resolve_pane(item: dict) -> tuple[str | None, str]:
    """항목이 타깃을 명시했으면 그 세션, 아니면 음성과 같은 규칙으로 결정."""
    target = queue_store.target_session(item)
    if target:
        pane = tmux_target.session_pane(target)
        return pane, (f"session:{target}" if pane else "none")
    return tmux_target.resolve_voice_target_pane()


def _check_safe(text: str) -> tuple[bool, str]:
    try:
        import safe_mode
    except ImportError:
        return True, ""
    # tmux 로 보내는 전체 텍스트가 여러 줄일 수 있다 — 첫 줄만 검사하면
    # "echo hi\nsudo rm -rf /" 처럼 뒤 줄의 위험 명령이 통과해버린다.
    # pty_manager.py 의 Enter 단위 라인 검사와 같은 기준으로 모든 줄을 검사한다.
    for line in (text or "").splitlines():
        ok, reason = safe_mode.check(line)
        if not ok:
            return ok, (reason or "")
    return True, ""


def _send_remote(host: str, session: str, text: str) -> tuple[bool, str]:
    """A3 — 원격 호스트 투입. `POST /api/peer/input`(control 등급)을 쓴다.

    로컬 경로가 `send_to_tmux`(Enter 포함)인 것과 맞추려고 `enter: true`를 보낸다 —
    큐 항목은 "실행돼야 하는 지시"라 Enter 없이 넣으면 영원히 프롬프트에 떠 있는다
    (파일 경로 삽입과 정반대 요구다).

    실패는 예외가 아니라 (False, 사람이 읽을 이유)로 돌려준다 — 호출부가 그대로
    큐에 blocked로 남기고 이유를 화면에 띄운다.
    """
    try:
        import host_store
        import peer_client
    except ImportError:
        return False, "멀티호스트 모듈을 불러올 수 없습니다"
    peer = host_store.find_peer(host)
    if peer is None:
        return False, f"등록되지 않은 호스트입니다: {host}"
    try:
        peer_client._call_sync(peer, "POST", "/api/peer/input",
                               {"session": session, "data": text, "enter": True})
    except peer_client.PeerError as e:
        return False, e.reason
    return True, ""


def _is_waiting(target: str | None, host: str = "local") -> bool:
    """타깃(tmux 세션 이름 또는 `세션:윈도.페인` 표기)이 승인 대기 상태인가.

    상태를 모르면 False — 감지가 아직 없는 에이전트(codex 등)에서 큐가 영구히
    막히면 안 된다. "모르면 막는다"가 아니라 "알 때만 막는다"이다.
    """
    if not target:
        return False
    try:
        import agent_status
    except ImportError:
        return False
    name = str(target).split(":", 1)[0]
    # host 차원까지 봐야 한다 — 원격의 같은 이름 세션이 waiting일 때 로컬 투입이
    # 막히거나, 그 반대가 되면 안 된다(A1에서 넣은 복합키가 여기서 쓰인다).
    return agent_status.status_for_session(name, host) == agent_status.WAITING


def drain_once(session: str | None = None, session_scoped: bool = False,
               targeted_only: bool = False, automatic: bool = False) -> dict:
    """pending 한 건을 투입한다. blocking — 호출부가 to_thread 로 감싼다.

    automatic=True(stop 훅·메시지 도착·재시도)면 사람 입력 확인을 거친다."""
    result = _drain_once(session, session_scoped, targeted_only, automatic)
    item = result.get("item")
    if item and item.get("exhausted"):
        _notify_exhausted(item)
    if item and item.get("source"):
        # 에이전트 메시지 항목이면 그 메시지의 상태(전달됨/막힘)를 맞춘다.
        try:
            import agent_msg
            agent_msg.on_queue_result(item, bool(result.get("drained")),
                                      "" if result.get("drained") else result.get("reason", ""))
        except Exception as e:  # noqa: BLE001 — 기록 실패가 큐를 멈추면 안 된다
            logger.warning(f"에이전트 메시지 상태 갱신 실패: {e}")
    return result


def _block(item: dict, reason: str, *, retriable: bool) -> dict:
    """blocked로 돌리고 갱신된 항목을 돌려준다(재시도 횟수·exhausted 반영)."""
    return queue_store.mark_blocked(item, reason, retriable=retriable)


def _drain_once(session: str | None, session_scoped: bool, targeted_only: bool = False,
                automatic: bool = False) -> dict:
    item = queue_store.claim_next(session, session_scoped=session_scoped,
                                  targeted_only=targeted_only)
    if item is None:
        return {"ok": True, "drained": 0, "reason": tr("큐가 비었습니다")}

    ok, reason = _check_safe(item["text"])
    if not ok:
        item = _block(item, reason, retriable=False)
        logger.warning(f"큐 항목이 safe_mode 에 막힘: {reason}")
        return {"ok": False, "drained": 0, "error": "blocked",
                "reason": tr("위험 명령으로 차단됨: {reason}", reason=reason), "item": item}

    # 5관문(A4): 승인 대기 중인 pane 에는 절대 넣지 않는다. 항목은 버리지 않고
    # blocked 로 남겨 승인이 끝난 뒤 다시 흘려보낼 수 있게 한다.
    target_session = queue_store.target_session(item) or session
    target_host = queue_store.target_host(item)
    if _is_waiting(target_session, target_host):
        item = _block(item, "타깃이 승인 대기 중", retriable=True)
        return {"ok": False, "drained": 0, "error": "waiting",
                "reason": tr("타깃이 승인 대기 중입니다 (큐에 유지됨)"), "item": item}

    # A3 — 원격 호스트 항목은 로컬 tmux를 아예 보지 않는다. pane 해석(send-keys)은
    # 로컬 소켓 전용이라 원격 세션 이름으로 로컬 pane을 찾으면 **같은 이름의
    # 로컬 세션**에 명령을 쳐버린다.
    if target_host != "local":
        if not target_session:
            item = _block(item, "원격 항목에 세션이 없음", retriable=False)
            return {"ok": False, "drained": 0, "error": "no_target",
                    "reason": tr("원격 항목은 대상 세션을 지정해야 합니다 (큐에 유지됨)"),
                    "item": item}
        remote_text = (f"{item['preface']}\n{item['text']}" if item.get("preface") else item["text"])
        ok, reason = _send_remote(target_host, target_session, remote_text)
        if not ok:
            item = _block(item, reason or "원격 전송 실패", retriable=True)
            return {"ok": False, "drained": 0, "error": "send_failed",
                    "reason": tr("{host}에 전송 실패: {reason} (큐에 유지됨)", host=target_host, reason=reason),
                    "item": item}
        queue_store.complete(item)
        logger.info(f"큐 투입 → {target_host}:{target_session}: {item['text'][:60]}")
        return {"ok": True, "drained": 1, "pane": f"{target_host}:{target_session}",
                "mode": f"host:{target_host}", "item": item,
                "remaining": queue_store.pending_count()}

    pane, mode = _resolve_pane(item)
    if not pane:
        # 항목을 버리지 않는다 — 세션이 돌아오면 그대로 실행돼야 한다.
        item = _block(item, "타깃 tmux 세션 없음", retriable=True)
        return {"ok": False, "drained": 0, "error": "no_target",
                "reason": tr("보낼 tmux 세션이 없습니다 (큐에 유지됨)"), "item": item}

    if automatic:
        import pane_input
        typing_session = target_session or tmux_target.session_of_pane(pane)
        if pane_input.human_typing(typing_session):
            item = _block(item, "사용자가 입력하는 중", retriable=True)
            return {"ok": False, "drained": 0, "error": "human_typing",
                    "reason": tr("사용자가 입력하는 중이라 미뤘습니다 (큐에 유지됨)"), "item": item}

    preface = item.get("preface")
    sent = (tmux_target.send_to_tmux(pane, item["text"], preface=preface) if preface
            else tmux_target.send_to_tmux(pane, item["text"]))
    if not sent:
        item = _block(item, "tmux 전송 실패", retriable=True)
        return {"ok": False, "drained": 0, "error": "send_failed",
                "reason": tr("tmux 전송에 실패했습니다 (큐에 유지됨)"), "item": item}

    queue_store.complete(item)

    logger.info(f"큐 투입 → {pane} ({mode}): {item['text'][:60]}")
    return {"ok": True, "drained": 1, "pane": pane, "mode": mode, "item": item,
            "remaining": queue_store.pending_count()}


async def drain_after_grace(delay: float | None = None, session: str | None = None,
                             session_scoped: bool = False, targeted_only: bool = False) -> dict:
    """유예 시간을 두고 한 건 투입. stop 훅이 호출한다."""
    wait = _grace_sec() if delay is None else delay
    if wait > 0:
        await asyncio.sleep(wait)
    async with _drain_lock:
        return await asyncio.to_thread(drain_once, session, session_scoped, targeted_only, True)


async def drain_now() -> dict:
    """즉시 한 건 투입 (수동 실행) — 세션 매칭 없이 맨 앞 항목 그대로."""
    async with _drain_lock:
        return await asyncio.to_thread(drain_once)


def schedule_drain(session: str | None = None, session_scoped: bool = False,
                   targeted_only: bool = False) -> bool:
    """stop 이벤트에서 호출. 이미 예약된 드레인이 있으면 중복 예약하지 않는다.

    session_scoped=True면 멈춘 세션(session) 몫이거나 타깃 미지정인 항목만
    대상이 된다 — 다른 세션에 쌓인 지시가 엉뚱하게 흘러가지 않는다.
    """
    if not autodrain_enabled():
        return False
    if queue_store.pending_count() == 0:
        return False
    key = (session, session_scoped, targeted_only)
    prev = _pending_tasks.get(key)
    if prev and not prev.done():
        return False
    try:
        _pending_tasks[key] = asyncio.create_task(
            drain_after_grace(session=session, session_scoped=session_scoped,
                              targeted_only=targeted_only)
        )
    except RuntimeError:
        # 이벤트 루프 밖에서 불린 경우 — 무시한다(수동 실행 경로가 있다).
        return False
    for k in [k for k, t in _pending_tasks.items() if t.done()]:
        _pending_tasks.pop(k, None)
    return True


def schedule_drain_threadsafe(session: str | None = None, session_scoped: bool = False,
                              targeted_only: bool = False) -> bool:
    """어느 스레드에서 불러도 되는 schedule_drain. 루프 스레드면 바로, 아니면 루프에 넘긴다."""
    try:
        asyncio.get_running_loop()
        return schedule_drain(session, session_scoped, targeted_only)
    except RuntimeError:
        pass
    loop = _loop
    if loop is None or loop.is_closed():
        return False
    loop.call_soon_threadsafe(schedule_drain, session, session_scoped, targeted_only)
    return True


# --- 재시도·점검 (멈춤 점검 / 백오프) ----------------------


def _notify(title: str, body: str) -> None:
    try:
        import push
        push.send(title, body, url="/")
    except Exception as e:  # noqa: BLE001 — 알림 실패가 큐를 멈추면 안 된다
        logger.debug(f"큐 알림 실패: {e}")


def _notify_exhausted(item: dict) -> None:
    _notify(tr("큐 항목이 계속 막힘"),
            tr("{n}번 시도했지만 투입하지 못했습니다: {reason} — {text}", n=queue_store.MAX_ATTEMPTS,
               reason=item.get('blocked_reason', ''), text=item.get('text', '')[:60]))


def _session_ready(name: str) -> bool:
    """재시도 항목을 지금 흘려도 되는가 — 상태를 알고, idle/done일 때만.

    working이면 다음 stop 훅이 흘린다. 모르면(hook 없는 에이전트) 흘리지 않는다 —
    출력 유휴로 완료를 추측하지 않는다는 모듈 원칙 그대로다.
    """
    try:
        import agent_status
    except ImportError:
        return False
    st = agent_status.known_status_for_session(name)
    return st in (agent_status.IDLE, agent_status.DONE)


def sweep_once() -> dict:
    """한 번 점검한다. blocking — to_thread로 부른다."""
    r = queue_store.sweep()
    for x in r["stale"]:
        _notify(tr("큐 투입이 멈춤"), tr("전송이 끝나지 않았습니다 — 확인 후 해제하세요: {text}", text=x.get('text', '')[:60]))
    return r


async def sweep_loop() -> None:
    """서버 수명 동안 돈다(main.lifespan). 기동 시 sending 잔여분을 먼저 정리한다."""
    global _loop
    _loop = asyncio.get_running_loop()
    try:
        n = await asyncio.to_thread(queue_store.recover_interrupted)
        if n:
            logger.warning(f"[queue] 전송 도중 중단된 항목 {n}건을 blocked로 돌렸다")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[queue] 기동 복구 실패: {e}")
    while True:
        await asyncio.sleep(_sweep_sec())
        try:
            r = await asyncio.to_thread(sweep_once)
        except Exception as e:  # noqa: BLE001 — 한 번 실패해도 루프는 산다
            logger.warning(f"[queue] 점검 실패: {e}")
            continue
        # 재시도로 풀린 항목 + 놓친 예약의 안전망: 세션을 지목한 pending이 있는데 그 세션이 확실히
        # 쉬고 있으면 흘린다. 상태를 모르는 세션은 건드리지 않는다(모듈 원칙).
        targets = {queue_store.target_session(x) for x in r["retried"]}
        try:
            targets |= {queue_store.target_session(x) for x in await asyncio.to_thread(queue_store.list_items)
                        if x.get("status") == queue_store.STATUS_PENDING
                        and queue_store.target_host(x) == "local"}
        except Exception:  # noqa: BLE001
            pass
        for target in targets:
            if target and _session_ready(target):
                schedule_drain(session=target, session_scoped=True, targeted_only=True)
