"""원격 호스트로 나가는 요청 (N7/N39 1단계) — host_store의 짝.

`notify.py`와 같은 규칙: 외부 HTTP는 `urllib.request` + `asyncio.to_thread`로만
한다(새 의존성을 들이지 않는다). 여기서 만드는 요청에는 **secret이 실리지 않는다** —
`X-Peer-Sig`(HMAC)만 나간다. host_store 모듈 주석의 "왜 해시가 아니라 원문인가" 참고.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import urllib.error
import urllib.request

import host_store

logger = logging.getLogger(__name__)

TIMEOUT = 8.0
# 페어링만 조금 더 준다 — 상대가 막 기동했거나 터널이 아직 덜 풀렸을 수 있다.
PAIR_TIMEOUT = 15.0


class PeerError(Exception):
    """원격 호출 실패 — 사람이 읽을 수 있는 이유를 담는다(CLI가 그대로 출력)."""

    def __init__(self, reason: str, status: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _request(url: str, method: str, headers: dict, body: dict | None,
             timeout: float) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                # 상대가 JSON이 아닌 걸 돌려줬다 — 대개 로그인 게이트 HTML이다.
                # 이 경우를 "알 수 없는 오류"로 뭉개면 원인 파악이 불가능하다.
                raise PeerError(
                    f"JSON이 아닌 응답({resp.status}) — URL이 farshell 서버가 맞는지 확인하세요",
                    resp.status,
                )
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        return e.code, payload
    except urllib.error.URLError as e:
        raise PeerError(f"연결 실패: {e.reason}")
    except OSError as e:
        raise PeerError(f"연결 실패: {e}")


def _signed_headers(peer: dict, method: str, path: str) -> dict:
    """서명 헤더. 상대 시계와의 차이(clockSkew)를 보정해 ts를 만든다 —
    두 맥의 시계가 몇 초 틀어져 있어도 서명 창(60초)에서 떨어지지 않게."""
    ts = int(time.time() + peer.get("clockSkew", 0))
    nonce = secrets.token_urlsafe(12)
    me = host_store.get_self()
    return {
        "X-Peer-Id": me["id"],
        "X-Peer-Ts": str(ts),
        "X-Peer-Nonce": nonce,
        "X-Peer-Sig": host_store.sign_request(peer["secret"], method, path, ts, nonce),
    }


def _call_sync(peer: dict, method: str, path: str, body: dict | None = None,
               timeout: float = TIMEOUT) -> dict:
    url = peer["url"].rstrip("/") + path
    status, payload = _request(url, method, _signed_headers(peer, method, path), body, timeout)
    if status == 404 and not payload:
        # peer 네임스페이스 자체가 없다 = 상대가 구버전이다. 이 진단이 없으면
        # 사용자는 그냥 "404"만 보고 원인을 못 찾는다.
        raise PeerError("상대 서버에 /api/peer 가 없습니다 — 구버전일 수 있습니다(양쪽 업데이트 필요)", 404)
    if status == 401:
        raise PeerError(payload.get("reason") or "인증 거부 — 페어링이 취소됐거나 만료됐습니다", 401)
    if status == 403:
        raise PeerError(payload.get("reason") or "권한 부족", 403)
    if not (200 <= status < 300):
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {status}", status)
    return payload


async def call(peer: dict, method: str, path: str, body: dict | None = None) -> dict:
    return await asyncio.to_thread(_call_sync, peer, method, path, body, TIMEOUT)


def ping_sync(peer: dict) -> dict:
    """왕복 시간을 재서 latencyMs와 함께 반환. 시계 오차도 여기서 다시 측정해
    갱신한다 — 맥이 절전에서 깨거나 NTP가 튀면 오차가 달라진다."""
    t0 = time.monotonic()
    payload = _call_sync(peer, "GET", "/api/peer/ping")
    rtt = (time.monotonic() - t0) * 1000
    skew = _measure_skew(payload, rtt)
    host_store.update_peer(
        peer["id"], lastSeen=int(time.time()), latencyMs=round(rtt),
        clockSkew=round(skew, 3), version=str(payload.get("version", ""))[:32],
    )
    return {**payload, "latencyMs": round(rtt), "clockSkew": round(skew, 3)}


def _measure_skew(payload: dict, rtt_ms: float) -> float:
    """상대 시각 - 내 시각. 편도 지연(rtt/2)을 빼서 보정한다."""
    try:
        server_time = float(payload.get("serverTime", 0))
    except (TypeError, ValueError):
        return 0.0
    if not server_time:
        return 0.0
    return server_time - (time.time() - rtt_ms / 2000)


def pair_sync(url: str, ticket: str, label: str = "") -> dict:
    """상대에게 티켓을 제출하고 이 연결 전용 secret을 받아온다(A 쪽에서 실행).

    티켓은 1회용이라 실패해도 재사용할 수 없다 — 실패 시 상대에서 `fsh host pair`를
    다시 실행해야 한다. 그 사실을 에러 메시지에 담는다.
    """
    me = host_store.get_self()
    version = _self_version()
    t0 = time.monotonic()
    status, payload = _request(
        url.rstrip("/") + "/api/peer/pair", "POST", {},
        {"ticket": ticket, "id": me["id"], "label": label or me["label"], "version": version},
        PAIR_TIMEOUT,
    )
    rtt = (time.monotonic() - t0) * 1000
    if status == 404 and not payload:
        raise PeerError("상대 서버에 /api/peer 가 없습니다 — 구버전일 수 있습니다(양쪽 업데이트 필요)", 404)
    if status == 401:
        raise PeerError(
            payload.get("reason")
            or "티켓이 유효하지 않거나 만료됐습니다 — 상대 맥에서 'fsh host pair'를 다시 실행하세요",
            401,
        )
    if not (200 <= status < 300):
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {status}", status)

    secret = payload.get("secret")
    remote_id = host_store.normalize_id(str(payload.get("id", "")))
    if not secret or not remote_id:
        raise PeerError("상대 응답에 id/secret이 없습니다")
    skew = _measure_skew(payload, rtt)
    peer = host_store.add_peer(
        remote_id, url, secret,
        label=str(payload.get("label") or remote_id),
        version=str(payload.get("version", ""))[:32],
        clock_skew=skew,
    )
    if peer is None:
        raise PeerError(f"상대가 보낸 호스트 id를 쓸 수 없습니다: {payload.get('id')!r}")
    return {
        "peer": {k: v for k, v in peer.items() if k != "secret"},
        "clockSkew": round(skew, 3),
        "latencyMs": round(rtt),
        "remoteVersion": payload.get("version", ""),
    }


def _self_version() -> str:
    try:
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
