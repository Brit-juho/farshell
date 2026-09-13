"""호스트 목록 + 원격 세션 집계 (N7/N39 2단계) — 브라우저가 부르는 쪽.

`routes/peer.py`가 **들어오는** peer 요청을 받는다면, 이 파일은 **나가는** 쪽을
로컬 UI에게 하나의 목록으로 합쳐 준다. 레일/플릿이 호스트를 전환할 때 보는 값.

## 왜 별도 라우터인가
`/api/peer/*`는 peer 서명으로만 열리고 브라우저 세션으로는 못 연다(그게 격리의
핵심이다). 반대로 이 라우터는 평소의 `TokenAuthMiddleware`가 지키는 일반 API다 —
로그인한 사람만 본다.

## 캐시
원격 호출은 네트워크 왕복이라, 레일이 2초마다 새로 고치면 그대로 왕복이 된다.
계획서(80 §1)가 정한 30초 TTL을 `TTLCache`로 건다. 호스트가 꺼져 있으면 그
사실도 같은 TTL로 캐시한다 — 죽은 호스트에 매번 8초 타임아웃을 물지 않기 위해서.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import agent_status
import host_store
import peer_client
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

router = APIRouter()

# 계획서 80 §1의 "30초 캐시".
_sessions_cache: TTLCache[dict] = TTLCache(ttl=30.0)


def _fetch_remote(peer: dict) -> dict:
    """원격 세션 목록 1회 조회. 실패도 **값으로** 돌려준다(예외로 올리지 않는다) —
    호스트 하나가 꺼져 있다고 목록 전체가 실패하면 안 되기 때문."""
    t0 = time.monotonic()
    try:
        payload = peer_client._call_sync(peer, "GET", "/api/peer/sessions")
    except peer_client.PeerError as e:
        return {"online": False, "reason": e.reason, "sessions": []}
    rtt = round((time.monotonic() - t0) * 1000)
    sessions = payload.get("sessions") or []
    # 원격 상태를 로컬 상태 머신에 host=<peer id>로 기록해둔다. A1에서 넣은 host
    # 차원 덕에 같은 이름의 로컬 세션과 절대 안 섞인다.
    for s in sessions:
        name = s.get("name")
        status = s.get("status")
        if name and status in agent_status.STATUSES:
            agent_status.report(f"peer:{name}", status, session=name, host=peer["id"])
    host_store.update_peer(peer["id"], lastSeen=int(time.time()), latencyMs=rtt)
    return {"online": True, "latencyMs": rtt, "sessions": sessions}


def _remote_entry(peer: dict, fresh: bool) -> dict:
    key = f"sessions:{peer['id']}"
    if fresh:
        _sessions_cache.invalidate(key)
    got = _sessions_cache.get_or_fetch(key, lambda: _fetch_remote(peer))
    return {
        "id": peer["id"],
        "label": peer["label"],
        "url": peer["url"],
        "version": peer.get("version") or "",
        "lastSeen": peer.get("lastSeen", 0),
        **got,
    }


def _local_entry() -> dict:
    """로컬 호스트도 같은 모양으로 — 프런트가 '로컬은 특별 케이스'를 따로 안 다루게."""
    import tmux_runner

    fmt = "#{session_name}\t#{session_windows}\t#{session_attached}"
    text = tmux_runner.run_text(["list-sessions", "-F", fmt], timeout=2.0) or ""
    panes = {}
    for p in tmux_runner.get_all_panes():
        panes.setdefault(p.session, p)
    sessions = []
    for line in text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        pane = panes.get(name)
        sessions.append({
            "name": name,
            "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
            "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "command": pane.command if pane else "",
            "cwd": pane.path if pane else "",
            "status": agent_status.status_for_session(name),
        })
    me = host_store.get_self()
    return {
        "id": agent_status.LOCAL_HOST,
        "label": me["label"],
        "url": "",
        "version": "",
        "online": True,
        "latencyMs": 0,
        "lastSeen": int(time.time()),
        "sessions": sessions,
    }


@router.get("/api/hosts")
async def list_hosts(fresh: bool = Query(False)):
    """로컬 + 등록된 원격 호스트를 세션 목록과 함께.

    로컬은 항상 첫 항목이고 id는 `local`이다(예약어 — host_store가 원격에게
    이 id를 못 쓰게 막는다). 원격 조회는 서로 독립이라 병렬로 돈다 — 꺼진
    호스트 하나가 나머지를 기다리게 하지 않는다.
    """
    peers = await asyncio.to_thread(host_store.list_peers)
    full = [host_store.find_peer(p["id"]) for p in peers]  # secret이 필요하다(서명)
    results = await asyncio.gather(*[
        asyncio.to_thread(_remote_entry, p, fresh) for p in full if p
    ])
    local = await asyncio.to_thread(_local_entry)
    return {"hosts": [local, *results]}


@router.get("/api/hosts/self")
async def get_self_host():
    """이 호스트의 id/label — 페어링 안내 문구와 설정 화면이 쓴다."""
    me = await asyncio.to_thread(host_store.get_self)
    return me


# ── 원격 세션의 「연결된 화면」·스크롤백 검색 (2.1.3) ────────────────────────
# 둘 다 2.1.2에서는 원격에서 **꺼져 있던** 기능이다. `/api/tmux/clients`도
# `/api/search/scrollback`도 이 맥의 tmux·이 맥의 버퍼만 보므로, 원격 탭에서
# 부르면 남의 세션 목록을 보여주거나(그리고 「이 화면만 남기기」가 자기 자신을
# 끊거나) 아무것도 못 찾는다. PTY를 소유한 호스트에게 물어보는 게 유일하게 맞는
# 답이고, 등급 경계는 자연스럽게 갈린다 — **보는 건 view, 끊는 건 control.**


async def _peer_or_404(host_id: str):
    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    return peer, None


def _peer_error(e: "peer_client.PeerError") -> JSONResponse:
    # 원격이 거부한 이유(등급 부족 403 등)를 그대로 넘긴다 — 여기서 500으로
    # 뭉개면 화면이 "왜 안 되는지"를 말할 수 없다.
    status = e.status if 400 <= e.status < 600 else 502
    return JSONResponse({"error": "peer_error", "reason": e.reason}, status_code=status)


@router.get("/api/hosts/{host_id}/clients")
async def host_clients(host_id: str, session: str, screen: str = Query("")):
    peer, err = await _peer_or_404(host_id)
    if err:
        return err
    try:
        return await asyncio.to_thread(
            peer_client._call_sync, peer, "GET", "/api/peer/clients", None,
            peer_client.TIMEOUT, {"session": session, "screen": screen},
        )
    except peer_client.PeerError as e:
        return _peer_error(e)


@router.post("/api/hosts/{host_id}/clients/detach")
async def host_clients_detach(host_id: str, request: Request):
    peer, err = await _peer_or_404(host_id)
    if err:
        return err
    body = await request.json()
    try:
        return await peer_client.call(peer, "POST", "/api/peer/clients/detach", body)
    except peer_client.PeerError as e:
        return _peer_error(e)


@router.post("/api/hosts/{host_id}/clients/solo")
async def host_clients_solo(host_id: str, request: Request):
    peer, err = await _peer_or_404(host_id)
    if err:
        return err
    body = await request.json()
    try:
        return await peer_client.call(peer, "POST", "/api/peer/clients/solo", body)
    except peer_client.PeerError as e:
        return _peer_error(e)


@router.get("/api/hosts/{host_id}/search")
async def host_search(host_id: str, q: str):
    """원격 스크롤백 검색. 결과의 `host`/`host_label`을 여기서 붙인다 — 팔레트가
    "이건 다른 맥의 출력"임을 표시할 수 있어야 하고, 그 판단을 원격이 자기
    응답에 담게 하면(자기를 뭐라고 부르든) 화면 라벨이 상대 말대로 정해진다."""
    peer, err = await _peer_or_404(host_id)
    if err:
        return err
    try:
        payload = await asyncio.to_thread(
            peer_client._call_sync, peer, "GET", "/api/peer/search", None,
            peer_client.TIMEOUT, {"q": q},
        )
    except peer_client.PeerError as e:
        return _peer_error(e)
    for r in payload.get("results") or []:
        r["host"] = host_id
        r["host_label"] = peer["label"]
    return payload


@router.post("/api/hosts/{host_id}/ping")
async def ping_host(host_id: str):
    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        r = await asyncio.to_thread(peer_client.ping_sync, peer)
    except peer_client.PeerError as e:
        # 연결 실패는 서버 오류가 아니라 **상태**다 — 200으로 내려 화면이
        # "연결 안 됨"을 그릴 수 있게 한다(500이면 프런트가 에러 토스트를 띄운다).
        return {"ok": False, "online": False, "reason": e.reason}
    _sessions_cache.invalidate(f"sessions:{host_id}")
    return {"ok": True, "online": True, **r}
