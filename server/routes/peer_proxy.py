"""원격 pane WS 프록시 (N7/N39 3단계) — 브라우저 ↔ 이 서버(A) ↔ 상대 서버(B).

## 왜 프록시인가
브라우저의 WebSocket API로는 커스텀 헤더를 못 보낸다. peer 인증은 서명 헤더
(`X-Peer-*`)에 얹혀 있으므로 **브라우저가 B에 직접 붙는 길은 없다**. 그래서 A의
서버가 중간에 서서, 브라우저에게는 평소의 로그인 세션으로, B에게는 peer 서명으로
말한다. ADR-24를 서버-서버로 바꾼 결정(SSH 대체)의 직접적인 귀결이다.

## 이 파일이 **하지 않는** 것 — 전부 의도된 것이다
- PTY를 만들지 않는다. 원격 pane의 PTY는 B에 있다(B의 `/api/peer/ws/{name}`).
- `output_watcher`/푸시 알림에 먹이지 않는다. **PTY를 소유한 호스트만 알린다** —
  A도 같이 알리면 같은 완료 알림이 두 번 온다.
- 스크롤백을 디스크에 쓰지 않는다. 영속화는 PTY 소유 호스트(B)의 설정만 따른다
  (A가 B의 출력을 A 디스크에 남기지 않는다).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import host_store
from authz import host_gate
from i18n import tr
import peer_client
from resilience import wsutil
from routes.pty import WS_HEARTBEAT_INTERVAL, WS_HEARTBEAT_TIMEOUT, _ws_auth_token

logger = logging.getLogger(__name__)

router = APIRouter()

CONNECT_TIMEOUT = 10.0

_SCREEN_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


def _screen_token(raw: str | None) -> str:
    """상대 URL에 그대로 들어가는 값이라 여기서도 문자셋을 좁힌다(B도 다시 검사한다)."""
    raw = (raw or "").strip()
    return raw if _SCREEN_RE.fullmatch(raw) else ""


# 상대(B)가 "다시 붙어도 소용없다"는 뜻으로 닫은 코드 → 브라우저가 같은 뜻으로
# 알아듣는 코드(term/ws.js의 TERMINAL_CLOSE_CODES). 예전엔 B의 종료 코드를 버리고
# 기본값(1000)으로 닫아서, 원격 세션이 사라진 탭(B에서 종료·이름 변경)이 없는
# 세션에 영원히 재연결을 시도했다.
def _terminal_upstream_codes() -> dict:
    # tr() 는 호출 시점에 평가해야 해서 상수가 아니라 함수로 둔다.
    return {
        4404: (4004, tr("원격 세션이 없습니다")),
        4401: (4001, tr("원격 호스트가 인증을 거부했습니다")),
    }


def browser_close_for(upstream_code: int | None) -> tuple[int, str]:
    """B의 종료 코드로 브라우저 쪽 종료 코드·이유를 정한다. 재시도해서 달라지지
    않는 경우만 옮기고, 나머지는 평소처럼(1000) 닫아 브라우저가 재연결하게 둔다."""
    return _terminal_upstream_codes().get(upstream_code or 0, (1000, ""))


# 열려 있는 원격 터미널 스트림(호스트 id -> {(브라우저 소켓, 그 소켓의 event loop)}). 상대 호스트가 «당신의 권한을 거뒀다»고 알려 와서 확인되면
# (`authz/remote.py` on_revoke) 상대의 종료 코드를 기다리지 않고 바로 닫는다 — 상대가 2초 안에 끊는 것(`GrantWatch`)과 함께 5초 한도를 지킨다.
_OPEN: dict[str, set] = {}


def close_streams(host_id: str) -> int:
    """`host_id` 로 열린 스트림을 닫는다(어느 스레드에서든 부를 수 있다). 닫기를 시작한 수."""
    n = 0
    for ws, loop in list(_OPEN.get(host_store.normalize_id(host_id) or host_id, ())):
        try:
            asyncio.run_coroutine_threadsafe(ws.close(code=4001, reason=tr("원격 호스트가 권한을 거뒀습니다")), loop)
            n += 1
        except Exception:  # noqa: BLE001 — 이미 닫혔다
            pass
    return n


def upstream_keepalive() -> dict:
    """상류(B) WS 의 프로토콜 ping/pong 설정 — `websockets.connect` 에 그대로 넘긴다.

    B 의 `/api/peer/ws` 는 앱 수준 ping 을 안 보내고 조용한 터미널은 바이트도 안 흘리므로, 상류가
    «조용한 것»인지 «죽은 것»인지는 앱 메시지로 알 수 없다. 프로토콜 ping(상대 websocket 스택이 pong 으로
    답한다 — 옛 B 도 마찬가지)으로 확인한다. 로컬 칸과 같은 기준: 15초마다 묻고 pong 이 15초 안에 안
    오면 1011 로 닫는다(라이브러리 기본값 20/20 에 기대지 않고 명시). 상류가 닫히면 `_down` 이 끝나
    브라우저가 평소 재연결 경로를 탄다."""
    return {"ping_interval": WS_HEARTBEAT_INTERVAL, "ping_timeout": WS_HEARTBEAT_INTERVAL}


async def browser_heartbeat(ws, last_seen, interval: float = WS_HEARTBEAT_INTERVAL,
                            timeout: float = WS_HEARTBEAT_TIMEOUT) -> None:
    """브라우저 ↔ 이 서버 구간의 생존 감시(로컬 `routes/pty.py` 의 `_heartbeat_loop` 와 같은 기준).

    B 는 앱 ping 을 안 보내므로 이 프록시가 직접 `{"type":"ping"}` 을 보내야 브라우저의 45초 침묵
    감시(core/ws-watchdog.js)가 조용한 원격 칸을 죽은 연결로 오인해 45초마다 끊지 않는다. 브라우저는 ping 에
    pong 으로 답하므로, `timeout` 동안 브라우저에서 아무것도 안 왔으면 반쯤 열린 연결로 보고 1001 로
    닫는다(`last_seen()` 은 마지막 수신 시각, loop.time 기준). 송신이 막혀도 같다(2초 한도).
    끝나면(닫았거나 송신 실패) 돌아온다 — 호출한 쪽이 프록시 전체를 정리한다."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval)
        if loop.time() - last_seen() > timeout:
            logger.info("[peer proxy] 브라우저 무응답 %.0fs — 끊음", timeout)
            try:
                await asyncio.wait_for(ws.close(code=1001, reason="heartbeat timeout"), 1.0)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            await asyncio.wait_for(ws.send_text('{"type":"ping"}'), wsutil.SEND_TIMEOUT)
        except Exception:  # noqa: BLE001 — 끊겼거나 막혔다
            try:
                await asyncio.wait_for(ws.close(code=1011, reason="send timeout"), 1.0)
            except Exception:  # noqa: BLE001
                pass
            return


def _ws_url(base_url: str, path: str) -> str:
    u = base_url.rstrip("/")
    if u.startswith("https://"):
        return "wss://" + u[len("https://"):] + path
    if u.startswith("http://"):
        return "ws://" + u[len("http://"):] + path
    return u + path


@router.websocket("/ws/remote/{host_id}/{tmux_name}")
async def ws_remote(ws: WebSocket, host_id: str, tmux_name: str):
    # 브라우저 쪽 인증은 평소와 같다(로컬 터미널 WS와 동일한 다중 소스).
    if _ws_auth_token(ws) is None:
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    if host_gate.enforced():
        # H13 — 원격 터미널 스트림(control 이면 입력)이라 relay 의 터미널 그룹과 같은 권한을 요구한다.
        d = await asyncio.to_thread(host_gate.decide, ws, host_id, "relay", {"group": "ws.term"})
        if not d.allow:
            await ws.close(code=4003, reason="host access denied")
            return

    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        await ws.close(code=4004, reason="host not found")
        return

    try:
        import websockets
    except ImportError:  # pragma: no cover — requirements-core에 있다
        await ws.close(code=4500, reason=tr("websockets 미설치"))
        return

    path = f"/api/peer/ws/{tmux_name}"
    durable = [ws.query_params.get(k) for k in ('catalog_host', 'catalog_session_id', 'runtime_id')]
    if any(value is not None for value in durable):
        if not all(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) for value in durable):
            await ws.close(code=4004, reason='invalid runtime identity')
            return
        # All identities are part of the HMAC-covered path; never fall back to a name.
        path = f"/api/peer/ws-v2/{'/'.join(durable)}/{tmux_name}"
    # 서명은 **쿼리 없는 path**에 건다 — B도 `ws.url.path`로 검증한다. screen은
    # 브라우저가 만든 화면 토큰(term/remote.js)으로, B가 이 연결의 attach PTY에
    # 이름을 붙여 「연결된 화면」이 "이게 나"를 판정할 수 있게 하는 값이다.
    # 권한을 바꾸는 값이 아니라 자기 화면을 지목하는 이름표라 서명 밖이어도 된다.
    headers = peer_client._signed_headers(peer, "GET", path)
    screen = _screen_token(ws.query_params.get("screen"))
    url = _ws_url(peer["url"], path) + (f"?screen={screen}" if screen else "")

    try:
        # max_size=None: 터미널 출력은 한 프레임이 커질 수 있고, 여기서 자르면
        # 화면이 깨진 채로 끊긴다. 어차피 상대는 우리가 등록한 호스트다.
        upstream = await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers, max_size=None, **upstream_keepalive()),
            timeout=CONNECT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — 연결 실패는 상태이지 서버 오류가 아니다
        logger.info(f"[peer proxy] {host_id} 연결 실패: {e}")
        await ws.close(code=4502, reason=tr("원격 호스트에 연결할 수 없습니다"))
        return

    async def _down():
        """B → 브라우저."""
        async for message in upstream:
            if isinstance(message, bytes):
                await ws.send_bytes(message)
            else:
                await ws.send_text(message)

    loop = asyncio.get_running_loop()
    last_seen = loop.time()

    async def _up():
        """브라우저 → B. 내용은 보지 않는다 — 등급 판정은 **B가** 한다
        (A가 판정하면 A를 믿는 셈이 되고, 그건 등급의 의미가 없다).
        생존 감시용 pong 만 여기서 걸러 B 로 넘기지 않는다(B 는 모르는 메시지다)."""
        nonlocal last_seen
        prober = wsutil.Prober()
        while True:
            msg = await ws.receive()
            last_seen = loop.time()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                if msg["text"] == '{"type":"pong"}':
                    continue
                # 탐침은 브라우저 ↔ 이 서버 구간만 잰다 — 상류(B)로 넘기지 않고 여기서 답한다
                # (B가 옛 버전이면 모르는 메시지다). 문자열 비교로 먼저 거르고 그때만 파싱한다.
                if '"probe"' in msg["text"] and len(msg["text"]) < 128:
                    try:
                        probe = json.loads(msg["text"])
                    except ValueError:
                        probe = None
                    if wsutil.is_probe(probe):
                        await wsutil.send_probe_ack(ws, prober.ack_for(probe))
                        continue
                await upstream.send(msg["text"])

    down = asyncio.create_task(_down())
    up = asyncio.create_task(_up())
    hb = asyncio.create_task(browser_heartbeat(ws, lambda: last_seen))
    reg = (ws, asyncio.get_running_loop())
    _OPEN.setdefault(peer["id"], set()).add(reg)
    try:
        done, pending = await asyncio.wait({down, up, hb}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        _OPEN.get(peer["id"], set()).discard(reg)
        down.cancel()
        up.cancel()
        hb.cancel()
        try:
            await upstream.close()
        except Exception:
            pass
        code, reason = browser_close_for(upstream.close_code)
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass
