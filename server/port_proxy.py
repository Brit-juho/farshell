"""노출 포트 프록시 리스너 수명 관리 (2.2, plan-2.2/00-port-access.md ADR-31).

share 하나당 `127.0.0.1:<proxyPort>`에 uvicorn 리스너를 하나 올린다. 그 리스너에
올라가는 앱은 `routes/port_proxy_app.py`이고 라우트가 셋뿐이다 — **FarShell의
라우터는 거기 없다.** 그게 이 구조를 택한 이유 전부다.

## loopback에만 바인드한다

cloudflared는 같은 기계에서 돈다. 0.0.0.0에 열면 같은 망의 누구나 인증 게이트를
**건너뛰고** 프록시에 직접 붙을 수 있다 — 접근 제어를 붙이려고 만든 것이 접근
제어를 여는 구멍이 된다.

## proxyPort가 고정이어야 하는 이유

`fsh stop --keep-tunnel`은 추가 포트 터널을 일부러 살려둔다(bin/fsh:2318).
2.2부터 그 터널은 **우리 프록시**를 front하므로, 재기동한 프록시를 살아남은
cloudflared가 같은 주소에서 다시 찾아야 한다. 그래서 포트는 store가 1회 할당해
영속하고(`port_share_store._alloc_proxy_port_unlocked`), 여기서는 그 번호로만
바인드한다. 바인드에 실패하면 **조용히 죽지 않고** share에 `broken`을 남겨 UI가
그 사실을 말하게 한다.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import socket

import uvicorn

import port_share_store as store
from routes.port_proxy_app import build_proxy_app

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT = 5.0
BACKLOG = 128


class _Listener:
    __slots__ = ("port", "proxy_port", "server", "task", "sock")

    def __init__(self, port: int, proxy_port: int, server, task, sock):
        self.port = port
        self.proxy_port = proxy_port
        self.server = server
        self.task = task
        self.sock = sock


_listeners: dict[int, _Listener] = {}


def _bind(proxy_port: int) -> socket.socket | None:
    """소켓을 **우리가** 만든다 — uvicorn에 맡기면 바인드 실패가
    `SystemExit`로 나와서 '왜 안 뜨는지'를 구조적으로 다룰 수 없다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", proxy_port))
    except OSError as e:
        sock.close()
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            return None
        raise
    sock.listen(BACKLOG)
    sock.setblocking(False)
    return sock


async def start(share: dict) -> bool:
    """share 하나의 리스너를 띄운다. 이미 떠 있으면 아무것도 안 한다."""
    port = share["port"]
    proxy_port = share.get("proxyPort")
    if not proxy_port:
        logger.warning(f"[port-proxy] proxyPort 없음 — 건너뜀 (port={port})")
        return False

    existing = _listeners.get(port)
    if existing is not None:
        if existing.proxy_port == proxy_port:
            return True
        await stop(port)  # 번호가 바뀌었으면 옮겨 탄다

    sock = await asyncio.to_thread(_bind, proxy_port)
    if sock is None:
        logger.error(
            f"[port-proxy] {proxy_port} 바인드 실패 — 포트 {port} 공유가 동작하지 않습니다. "
            f"이 번호를 쓰는 다른 프로세스를 확인하세요."
        )
        await asyncio.to_thread(store.update_share, port, broken=True)
        return False

    config = uvicorn.Config(
        build_proxy_app(port),
        log_level="warning",
        access_log=False,  # 프록시된 요청마다 한 줄씩 찍으면 서버 로그가 앱 로그가 된다
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # 메인 서버가 이미 시그널을 잡고 있다 — 여기서 또 잡으면 종료 처리가 갈린다.
    server.install_signal_handlers = lambda: None

    task = asyncio.create_task(server.serve(sockets=[sock]), name=f"port-proxy-{port}")
    _listeners[port] = _Listener(port, proxy_port, server, task, sock)
    if share.get("broken"):
        await asyncio.to_thread(store.update_share, port, broken=False)
    logger.info(f"[port-proxy] 포트 {port} → 127.0.0.1:{proxy_port} 리스너 시작")
    return True


async def stop(port: int) -> None:
    lis = _listeners.pop(port, None)
    if lis is None:
        return
    lis.server.should_exit = True
    try:
        await asyncio.wait_for(lis.task, timeout=SHUTDOWN_TIMEOUT)
    except (TimeoutError, asyncio.TimeoutError):
        lis.task.cancel()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 종료 경로에서 예외로 다른 리스너를 막지 않는다
        logger.info(f"[port-proxy] 포트 {port} 리스너 종료 중 오류: {e}")
    finally:
        try:
            lis.sock.close()
        except OSError:
            pass
    logger.info(f"[port-proxy] 포트 {port} 리스너 종료")


async def sync() -> dict:
    """store를 진실로 보고 리스너 집합을 맞춘다.

    기동 시(main.py lifespan)와 expose/unexpose 직후에 부른다. 멱등이라 여러 번
    불러도 안전하다 — 그게 '재기동 후 같은 주소로 복귀'를 보장하는 방식이다.
    """
    shares = await asyncio.to_thread(store.list_shares)
    wanted = {s["port"]: s for s in shares}

    for port in list(_listeners):
        if port not in wanted:
            await stop(port)

    started, failed = [], []
    for port, share in wanted.items():
        ok = await start(share)
        (started if ok else failed).append(port)
    return {"active": started, "failed": failed}


async def shutdown_all() -> None:
    for port in list(_listeners):
        await stop(port)


def status() -> list[dict]:
    return [
        {"port": l.port, "proxyPort": l.proxy_port, "running": not l.task.done()}
        for l in _listeners.values()
    ]


def proxy_ports() -> set[int]:
    """portscan이 목록에서 감추고 보호 목록에 넣을 번호들."""
    return {l.proxy_port for l in _listeners.values()}
