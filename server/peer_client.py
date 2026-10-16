"""원격 호스트로 나가는 요청 (N7/N39 1단계) — host_store의 짝.

`notify.py`와 같은 규칙: 외부 HTTP는 `urllib.request` + `asyncio.to_thread`로만
한다(새 의존성을 들이지 않는다). 여기서 만드는 요청에는 **secret이 실리지 않는다** —
`X-Peer-Sig`(HMAC)만 나간다. host_store 모듈 주석의 "왜 해시가 아니라 원문인가" 참고.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import host_store
from i18n import tr

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
                    tr("JSON이 아닌 응답({status}) — URL이 farshell 서버가 맞는지 확인하세요", status=resp.status),
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
        raise PeerError(tr("연결 실패: {reason}", reason=e.reason))
    except OSError as e:
        raise PeerError(tr("연결 실패: {reason}", reason=e))


def _v2_on() -> bool:
    """`VT_AUTHZ_ENFORCE=peer` — 켜져 있을 때만 프로토콜 v2 헤더(`X-Peer-Proto`)를 단다(꺼짐 = 예전과 헤더까지 같다)."""
    try:
        from authz import flags
        return flags.enforced("peer")
    except Exception:  # noqa: BLE001
        return False


def _signed_headers(peer: dict, method: str, path: str, body_hash: str = "", obo: str = "") -> dict:
    """서명 헤더. 상대 시계와의 차이(clockSkew)를 보정해 ts를 만든다 —
    두 맥의 시계가 몇 초 틀어져 있어도 서명 창(60초)에서 떨어지지 않게.

    `obo`(프로토콜 v2): 「이 요청은 내 에이전트 X 를 대신한다」는 보증. 서명에 들어가며 헤더 `X-Peer-Obo` 로도 나간다."""
    ts = int(time.time() + peer.get("clockSkew", 0))
    nonce = secrets.token_urlsafe(12)
    me = host_store.get_self()
    out = {
        "X-Peer-Id": me["id"],
        "X-Peer-Ts": str(ts),
        "X-Peer-Nonce": nonce,
        "X-Peer-Sig": host_store.sign_request(peer["secret"], method, path, ts, nonce, body_hash, obo),
        # 본문 해시를 쓴 요청만 이 헤더를 단다 — 상대는 헤더가 있으면 그 값으로
        # 서명을 검증하고, 실제 본문 해시와도 대조한다(A2).
        **({"X-Peer-Body": body_hash} if body_hash else {}),
    }
    if obo:
        out["X-Peer-Obo"] = obo
    if obo or _v2_on():
        out["X-Peer-Proto"] = str(host_store.PROTO_VERSION)
    return out


def _call_sync(peer: dict, method: str, path: str, body: dict | None = None,
               timeout: float = TIMEOUT, query: dict | None = None) -> dict:
    """`query`는 **서명에 들어가지 않는다** — 상대는 `request.url.path`(쿼리 제외)로
    서명을 검증하기 때문이다. 그래서 쿼리를 path에 직접 이어 붙이면 서명이 깨진다.
    쿼리로 보내도 되는 값은 "그 peer의 권한 안에서 무엇을 고를지"뿐이고(세션 이름·
    화면 토큰·검색어), 권한 자체를 바꾸는 값은 절대 쿼리에 싣지 않는다.
    """
    url = peer["url"].rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
    status, payload = _request(url, method, _signed_headers(peer, method, path), body, timeout)
    if status == 404 and not payload:
        # peer 네임스페이스 자체가 없다 = 상대가 구버전이다. 이 진단이 없으면
        # 사용자는 그냥 "404"만 보고 원인을 못 찾는다.
        raise PeerError(tr("상대 서버에 /api/peer 가 없습니다 — 구버전일 수 있습니다(양쪽 업데이트 필요)"), 404)
    if status == 401:
        raise PeerError(payload.get("reason") or tr("인증 거부 — 페어링이 취소됐거나 만료됐습니다"), 401)
    if status == 403:
        raise PeerError(payload.get("reason") or tr("권한 부족"), 403)
    if not (200 <= status < 300):
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {status}", status)
    return payload


def send_bytes_sync(peer: dict, path: str, data: bytes, headers: dict,
                    timeout: float = 120.0) -> dict:
    """원시 바이트 POST (A2 파일 전송). JSON base64로 감싸지 않는 이유는 단순하다 —
    200MB 파일이 base64로 267MB가 되고, 그걸 메모리에서 인코딩/디코딩까지 한다.

    서명에 본문 해시를 포함한다(`_signed_headers`의 body_hash). 타임아웃도 따로
    둔다: 파일 한 개가 터널을 넘어가는 시간은 ping 왕복과 자릿수가 다르다.
    """
    body_hash = hashlib.sha256(data).hexdigest()
    hdrs = {
        **_signed_headers(peer, "POST", path, body_hash),
        **headers,
        "Content-Type": "application/octet-stream",
    }
    url = peer["url"].rstrip("/") + path
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except ValueError:
                raise PeerError(tr("JSON이 아닌 응답({status})", status=resp.status), resp.status)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {e.code}", e.code)
    except urllib.error.URLError as e:
        raise PeerError(tr("연결 실패: {reason}", reason=e.reason))
    except OSError as e:
        raise PeerError(tr("연결 실패: {reason}", reason=e))


async def call(peer: dict, method: str, path: str, body: dict | None = None) -> dict:
    return await asyncio.to_thread(_call_sync, peer, method, path, body, TIMEOUT)


def ping_sync(peer: dict) -> dict:
    """왕복 시간을 재서 latencyMs와 함께 반환. 시계 오차도 여기서 다시 측정해
    갱신한다 — 맥이 절전에서 깨거나 NTP가 튀면 오차가 달라진다."""
    t0 = time.monotonic()
    payload = _call_sync(peer, "GET", "/api/peer/ping")
    rtt = (time.monotonic() - t0) * 1000
    skew = _measure_skew(payload, rtt)
    fields = dict(lastSeen=int(time.time()), latencyMs=round(rtt),
                  clockSkew=round(skew, 3), version=str(payload.get("version", ""))[:32])
    if "proto" in payload:       # v2 상대만 준다(꺼짐·옛 상대의 응답은 예전과 같아 이 키가 없다)
        fields["proto"] = int(payload["proto"] or 1)
    host_store.update_peer(peer["id"], **fields)
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
        raise PeerError(tr("상대 서버에 /api/peer 가 없습니다 — 구버전일 수 있습니다(양쪽 업데이트 필요)"), 404)
    if status == 401:
        raise PeerError(
            payload.get("reason")
            or tr("티켓이 유효하지 않거나 만료됐습니다 — 상대 맥에서 'fsh host pair'를 다시 실행하세요"),
            401,
        )
    if not (200 <= status < 300):
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {status}", status)

    secret = payload.get("secret")
    remote_id = host_store.normalize_id(str(payload.get("id", "")))
    if not secret or not remote_id:
        raise PeerError(tr("상대 응답에 id/secret이 없습니다"))
    skew = _measure_skew(payload, rtt)
    peer = host_store.add_peer(
        remote_id, url, secret,
        label=str(payload.get("label") or remote_id),
        version=str(payload.get("version", ""))[:32],
        clock_skew=skew,
    )
    if peer is None:
        raise PeerError(tr("상대가 보낸 호스트 id를 쓸 수 없습니다: {id}", id=repr(payload.get('id'))))
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


# --- 프로토콜 v2 호출 ---------------------------------------------------------------------------------


def v2_call_sync(peer: dict, method: str, path: str, body: dict | None = None, *, obo: str = "",
                 timeout: float = TIMEOUT) -> tuple[int, dict]:
    """`/api/peer/v2/*` 호출 — **본문 해시를 서명**하고(명령·작업 지시가 곧 본문이다) 대리 주체(`obo`)를 서명에 싣는다.
    (상태 코드, JSON) 을 돌려준다 — 4xx/5xx 도 예외가 아니다(호출부가 의미를 안다). 연결 실패·JSON 이 아닌 응답만 `PeerError`."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    digest = hashlib.sha256(data).hexdigest()
    headers = _signed_headers(peer, method, path, digest, obo)
    if data:
        headers["Content-Type"] = "application/json"
    url = peer["url"].rstrip("/") + path
    req = urllib.request.Request(url, data=data or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise PeerError(tr("연결 실패: {reason}", reason=e.reason))
    except OSError as e:
        raise PeerError(tr("연결 실패: {reason}", reason=e))
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return status, payload


async def v2_call(peer: dict, method: str, path: str, body: dict | None = None, *, obo: str = "",
                  timeout: float = TIMEOUT) -> tuple[int, dict]:
    return await asyncio.to_thread(v2_call_sync, peer, method, path, body, obo=obo, timeout=timeout)


ROTATE_PATH = "/api/peer/v2/rotate"
ROTATE_CONFIRM_PATH = "/api/peer/v2/rotate/confirm"


def settle_rotation(peer_id: str) -> str:
    """끝나지 않은 회전(`peers[].rotation == "unconfirmed"`)을 정리한다. 반환: `none`(할 일 없음) · `confirmed`(상대가 새 secret 을 알고 있다)
    · `rolled_back`(상대가 모른다 -> 옛 secret 으로 되돌림) · `unknown`(상대에게 닿지 못했다 — 그대로 둔다)."""
    cur = host_store.find_peer(peer_id)
    if not cur or cur.get("rotation") != "unconfirmed":
        return "none"
    try:
        st, _ = v2_call_sync(cur, "POST", ROTATE_CONFIRM_PATH, {})
        if st == 200:
            host_store.settle_peer_secret(peer_id, confirmed=True)
            return "confirmed"
        if st != 401:
            return "unknown"
        # 새 secret 을 상대가 모른다 — 옛 secret 이 아직 맞는지 본다(맞으면 되돌린다).
        old = {**cur, "secret": cur.get("prevSecret") or ""}
        st2, _ = v2_call_sync(old, "GET", "/api/peer/ping")
        if st2 == 200:
            host_store.settle_peer_secret(peer_id, confirmed=False)
            return "rolled_back"
    except PeerError:
        pass
    return "unknown"


def rotate_sync(peer: dict) -> dict:
    """연결의 secret 을 회전한다(`fsh host rotate`). 성공하면 `{id, rotatedAt, previousRotatedAt}`, 실패하면 `PeerError` 이고 **이전 secret 이 그대로**다.
    절차와 중단 복구는 `host_store` 의 「secret 회전」 주석."""
    pid = peer["id"]
    state = settle_rotation(pid)
    if state == "unknown":
        raise PeerError(tr("이전 회전이 끝났는지 확인하지 못했습니다 — 상대 서버가 켜져 있는지 보고 다시 시도하세요"))
    cur = host_store.find_peer(pid)
    if cur is None:
        raise PeerError(tr("'{id}' 호스트가 없습니다", id=pid))
    st, payload = v2_call_sync(cur, "POST", ROTATE_PATH, {}, timeout=PAIR_TIMEOUT)
    if st == 404:
        raise PeerError(tr("상대 서버가 secret 회전을 지원하지 않습니다 — 구버전이거나 상대 서버의 VT_AUTHZ_ENFORCE 에 peer 가 없습니다"), 404)
    if st == 401:
        raise PeerError(payload.get("reason") or tr("인증 거부 — 페어링이 취소됐거나 만료됐습니다"), 401)
    new = payload.get("secret") if st == 200 else None
    if not isinstance(new, str) or len(new) < 16:
        raise PeerError(payload.get("reason") or payload.get("error") or f"HTTP {st}", st)
    # 여기까지는 아무것도 바꾸지 않았다(상대에는 10분짜리 보류만 있다). 이제 내 쪽을 한 번에 바꾸고 새 secret 으로 확인한다.
    if not host_store.swap_peer_secret(pid, cur["secret"], new):
        raise PeerError(tr("회전하는 사이 이 연결이 바뀌어 중단했습니다 — 이전 secret 을 그대로 씁니다"))
    updated = host_store.find_peer(pid)
    st, confirm = 0, {}
    for attempt in range(3):
        try:
            st, confirm = v2_call_sync(updated, "POST", ROTATE_CONFIRM_PATH, {})
            break
        except PeerError:
            time.sleep(0.5 * (attempt + 1))
    if st == 200:
        host_store.settle_peer_secret(pid, confirmed=True)
        return {"id": pid, "rotatedAt": confirm.get("rotatedAt") or updated.get("rotatedAt"),
                "previousRotatedAt": updated.get("previousRotatedAt") or 0}
    if st == 401:                       # 상대가 새 secret 을 모른다(보류가 만료됐다) — 되돌린다
        host_store.settle_peer_secret(pid, confirmed=False)
        raise PeerError(tr("상대가 새 secret 을 받지 못해 이전 secret 으로 되돌렸습니다 — 다시 시도하세요"), 401)
    raise PeerError(tr("새 secret 이 적용됐는지 확인하지 못했습니다 — 'fsh host rotate {id}' 를 다시 실행하면 정리합니다", id=pid))
