"""Web Push (VAPID) — 앱이 닫혀 있어도 알림을 받는다 (P5).

기존 알림(`/ws-notify` → Notification API)은 **PWA 탭이 살아 있어야만** 동작한다.
폰 화면을 끄면 "Claude가 승인 대기 중"을 놓친다. 그 마지막 격차를 메운다.

## 이 기능이 성립하는 조건 (하나라도 빠지면 조용히 안 온다)

  - **secure context.** https 또는 localhost 여야 Service Worker 가 등록된다.
    지금은 cloudflared 가 https 를 주므로 성립한다. 평문 http(예: tailnet IP 직결)로
    바꾸면 이 기능은 통째로 죽는다.
  - **iOS 는 홈 화면에 PWA 로 설치해야 한다** (iOS 16.4+). 사파리 탭에서는 구독이
    아예 안 만들어진다. 우회 방법은 없다 — UI에서 안내만 한다.

## origin 이 바뀌면 구독은 죽는다

Push 구독은 origin 에 묶인다. trycloudflare quick tunnel 은 URL 이 임시라서
터널이 재시작되면(워치독 작동·재부팅) 기존 구독이 전부 무효가 된다. 그래서
구독마다 origin 을 같이 저장하고, 현재 origin 과 다른 것은 발송 대상에서 제외한다.
푸시 서버가 404/410 을 주면 그 자리에서 삭제한다.

## 알림 본문에 무엇을 넣지 않는가

잠금화면에 뜨는 내용이다. 명령어·파일 경로·코드를 넣지 않는다. "작업 완료" 수준의
사실만 싣고, 자세한 내용은 앱을 열어서 보게 한다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 알림 본문 상한 — 잠금화면에 길게 뜰 이유가 없고, 길수록 유출 표면만 넓어진다.
MAX_BODY = 120

_SUBJECT = os.environ.get("VT_PUSH_SUBJECT", "mailto:vt@localhost")


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _subs_path() -> Path:
    return _state_dir() / "push-subs.json"


def _vapid_path() -> Path:
    return _state_dir() / "vapid.json"


def available() -> bool:
    try:
        import pywebpush  # noqa: F401
        import py_vapid    # noqa: F401
        return True
    except ImportError:
        return False


def _write_secure(path: Path, data) -> None:
    """0700 디렉토리 + 0600 파일 + atomic replace (auth._write_json_secure 와 동일)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"{path.name} 읽기 실패({e})")
        return default


# --- VAPID 키 --------------------------------------------------------------


def get_keys() -> dict | None:
    """VAPID 키쌍. 없으면 생성한다.

    개인키를 잃어버리면 기존 구독이 전부 무효가 되므로 파일을 지우지 말 것.
    """
    if not available():
        return None
    keys = _read_json(_vapid_path(), None)
    if keys and keys.get("private") and keys.get("public"):
        return keys

    from py_vapid import Vapid01
    v = Vapid01()
    v.generate_keys()
    # application server key는 URL-safe base64 (브라우저가 그대로 쓴다)
    keys = {
        "private": v.private_pem().decode("utf-8"),
        "public": _public_key_b64(v),
        "created_at": time.time(),
    }
    _write_secure(_vapid_path(), keys)
    logger.info("VAPID 키쌍 생성 — ~/.vt/vapid.json (0600). 지우면 기존 구독이 무효화된다")
    return keys


def _public_key_b64(vapid) -> str:
    import base64
    from cryptography.hazmat.primitives import serialization
    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def public_key() -> str | None:
    keys = get_keys()
    return keys["public"] if keys else None


# --- 구독 -------------------------------------------------------------------


def list_subs() -> list[dict]:
    subs = _read_json(_subs_path(), [])
    return subs if isinstance(subs, list) else []


def add_sub(subscription: dict, origin: str = "", label: str = "") -> dict:
    ep = (subscription or {}).get("endpoint")
    if not ep:
        return {"ok": False, "error": "bad_subscription"}
    subs = list_subs()
    # endpoint 기준 dedup — 같은 기기가 재구독하면 갱신이지 추가가 아니다.
    subs = [s for s in subs if s.get("subscription", {}).get("endpoint") != ep]
    subs.append({
        "subscription": subscription,
        "origin": origin,
        "label": label[:40],
        "added_at": time.time(),
    })
    _write_secure(_subs_path(), subs)
    return {"ok": True, "count": len(subs)}


def remove_sub(endpoint: str) -> dict:
    subs = list_subs()
    rest = [s for s in subs if s.get("subscription", {}).get("endpoint") != endpoint]
    _write_secure(_subs_path(), rest)
    return {"ok": True, "removed": len(subs) - len(rest), "count": len(rest)}


def _current_origin() -> str:
    """지금 유효한 origin(터널 URL). 못 구하면 빈 문자열 → origin 필터를 끈다."""
    try:
        import tunnel
        st = tunnel.get_tunnel_status()
        return (st.get("url") or "").rstrip("/")
    except Exception:
        return ""


# --- 발송 -------------------------------------------------------------------


def send(title: str, body: str = "", url: str = "/") -> dict:
    """모든 유효 구독에 발송. blocking — 호출부가 to_thread 로 감싼다."""
    if not available():
        return {"ok": False, "error": "unavailable", "sent": 0}
    keys = get_keys()
    if not keys:
        return {"ok": False, "error": "no_keys", "sent": 0}

    subs = list_subs()
    if not subs:
        return {"ok": True, "sent": 0, "reason": "구독 없음"}

    cur = _current_origin()
    payload = json.dumps({
        "title": title[:MAX_BODY],
        "body": (body or "")[:MAX_BODY],
        "url": url,
    }, ensure_ascii=False)

    from pywebpush import WebPushException, webpush

    sent = 0
    stale: list[str] = []
    skipped = 0
    for s in subs:
        sub = s.get("subscription") or {}
        ep = sub.get("endpoint", "")
        # origin 이 바뀌었으면(터널 URL 변경) 이 구독은 더 이상 유효하지 않다.
        if cur and s.get("origin") and s["origin"] != cur:
            skipped += 1
            continue
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=keys["private"],
                vapid_claims={"sub": _SUBJECT},
                timeout=10,
            )
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                stale.append(ep)          # 구독 만료 — 그 자리에서 청소
            else:
                logger.warning(f"web push 실패({code}): {e}")
        except Exception as e:
            logger.warning(f"web push 오류: {e}")

    if stale:
        rest = [x for x in list_subs()
                if x.get("subscription", {}).get("endpoint") not in stale]
        _write_secure(_subs_path(), rest)
        logger.info(f"만료된 push 구독 {len(stale)}건 정리")

    return {"ok": True, "sent": sent, "expired": len(stale), "skipped_origin": skipped}


def status() -> dict:
    subs = list_subs()
    cur = _current_origin()
    return {
        "available": available(),
        "configured": bool(get_keys()) if available() else False,
        "subscriptions": len(subs),
        "current_origin": cur,
        # origin 이 어긋난 구독 수 — UI가 "재구독 필요"를 안내할 근거
        "stale_origin": sum(1 for s in subs if cur and s.get("origin") and s["origin"] != cur),
    }
