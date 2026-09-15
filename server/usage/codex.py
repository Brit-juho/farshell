"""96번 계획서(2.1.5) — Codex 어댑터. clauth와 같은 한도형(UsageProvider)
인터페이스지만 데이터 소스가 **파일이 아니라 원격 API 호출**이라 clauth
보다 위험이 하나 더 있다(계획서 §3-1). clauth를 그대로 베끼지 않고 이 파일
안에서 그 차이를 전부 감당한다.

**왜 로그 파일이 아니라 API인가**(계획서 §0 — 최초 설계였던 로그 파싱을
폐기한 이유): `~/.codex/sessions/*.jsonl`에는 누적 토큰만 있고 한도(%)가
없다. Paseo(`getpaseo/paseo`, 17.2k stars)의
`quota-fetcher/providers/codex.ts`가 이미 같은 문제를 풀어놨다 — Codex CLI
**자신이 쓰는 것과 같은** OAuth 사용량 API를 직접 부른다. 2026-09-14 이
맥에서 실제로 호출해 확인했다(계획서 §2, 실측 응답 그대로 여기 파서가
읽는 필드로 남아 있다).

**보안(계획서 §3-1)**:
  - access_token은 매 호출마다 `~/.codex/auth.json`에서 읽어 그 자리에서만
    쓰고 저장하지 않는다.
  - 응답 **전체**를 캐시하지 않는다 — 화이트리스트를 통과한 정규화 결과만
    캐시한다(`credits`·`model_usage`·`spend_control`을 그대로 들고 있으면
    나중에 실수로 새어나갈 표면이 생긴다).
  - clauth는 로컬 파일이라 **mtime 캐시**를 쓰지만, 이건 원격 API라 로컬
    mtime이 없다 — **TTL 캐시**(60초)로 간다. 여러 브라우저 탭이 동시에
    폴링해도 서버가 캐시를 돌려준다(429 방지).
  - 429/5xx는 `stale:true`로, 401/403은 clauth의 `auth_status:expired`와
    같은 취급(재인증 필요, 게이지보다 위에 표시).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request as _UrlRequest
from urllib.request import urlopen

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# Paseo 코드가 이 User-Agent를 쓴다 — Codex 서버 쪽 정책을 우리가 통제 못 하므로
# 실측대로 유지한다(빼면 다른 응답이 올 수 있다는 게 그쪽이 넣어둔 이유로 보인다).
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

TTL_SEC = 60.0
TIMEOUT_SEC = 10.0


def auth_path() -> Path:
    home = os.environ.get("VT_CODEX_HOME") or os.environ.get("CODEX_HOME")
    if home:
        return Path(home) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _read_auth() -> Optional[dict]:
    """access_token·account_id만 뽑아 즉시 반환한다 — 이 함수 리턴값 밖에는
    아무 데도 안 남긴다(호출자가 캐시하지 않는다)."""
    try:
        raw = json.loads(auth_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        return None
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token:
        return None
    account_id = tokens.get("account_id")
    return {"access_token": token, "account_id": account_id if isinstance(account_id, str) else None}


def _num(value, default=None):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _window(raw: Optional[dict], label: str, wid: str, now: float) -> Optional[dict]:
    """`{used_percent, reset_after_seconds}` → clauth와 같은 window 모양.
    `reset_after_seconds`는 **응답을 받은 시점 기준 상대값**이라, clauth의
    절대시각(`resets_at`) 방식과 맞추려면 지금 시각에 더해 절대 epoch로
    바꿔야 한다.
    """
    if not isinstance(raw, dict):
        return None
    pct = _num(raw.get("used_percent"))
    if pct is None:
        return None
    after = _num(raw.get("reset_after_seconds"))
    sec = max(0, int(after)) if after is not None else None
    return {
        "id": wid,
        "label": label,
        "pct": float(pct),
        "resets_at": datetime.fromtimestamp(now + sec, tz=timezone.utc).isoformat() if sec is not None else None,
        "resets_in_sec": sec,
    }


class CodexProvider:
    name = "codex"

    def __init__(self):
        self._cache_at: float = 0.0
        self._cache: Optional[dict] = None       # 정규화된 스냅샷만(원본 응답 아님)
        self._cache_error: Optional[str] = None

    def _fetch(self) -> tuple[Optional[dict], Optional[str]]:
        now = time.time()
        if self._cache is not None and (now - self._cache_at) < TTL_SEC:
            return self._cache, self._cache_error

        auth = _read_auth()
        if auth is None:
            self._cache_at, self._cache, self._cache_error = now, None, "no-auth"
            return None, "no-auth"

        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if auth["account_id"]:
            headers["ChatGPT-Account-Id"] = auth["account_id"]

        req = _UrlRequest(USAGE_URL, headers=headers)
        try:
            with urlopen(req, timeout=TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            if e.code in (401, 403):
                self._cache_at, self._cache, self._cache_error = now, None, "expired"
            else:
                # 429/5xx — 서버 쪽 일시 문제. 이전 정규화 캐시가 있으면 그걸
                # stale로 표시해 계속 보여준다(완전히 끄는 것보다 낫다).
                self._cache_at, self._cache_error = now, "unavailable"
                if self._cache is not None:
                    self._cache = {**self._cache, "stale": True}
            return self._cache, self._cache_error
        except (URLError, TimeoutError, OSError):
            self._cache_at, self._cache_error = now, "unavailable"
            if self._cache is not None:
                self._cache = {**self._cache, "stale": True}
            return self._cache, self._cache_error

        try:
            data = json.loads(body)
        except ValueError:
            self._cache_at, self._cache, self._cache_error = now, None, "unavailable"
            return None, "unavailable"
        if not isinstance(data, dict):
            self._cache_at, self._cache, self._cache_error = now, None, "unavailable"
            return None, "unavailable"

        normalized = self._normalize(data, now)
        self._cache_at, self._cache, self._cache_error = now, normalized, None
        return normalized, None

    def _normalize(self, data: dict, now: float) -> dict:
        """응답에서 **아는 필드만** 뽑는다 — `additional_rate_limits`(모델별
        한도)는 표시 전용 확장으로만 다루고, `credits`·`model_usage`·
        `spend_control`은 이 함수를 나가는 순간부터 존재하지 않는다(계획서
        §3-1 화이트리스트 원칙 — clauth의 "모르는 필드는 버린다"와 동일)."""
        windows = []
        rl = data.get("rate_limit")
        if isinstance(rl, dict):
            w = _window(rl.get("primary_window"), "5h", "session", now)
            if w:
                windows.append(w)
            w = _window(rl.get("secondary_window"), "weekly", "weekly", now)
            if w:
                windows.append(w)
        crl = data.get("code_review_rate_limit")
        if isinstance(crl, dict):
            w = _window(crl.get("primary_window"), "code review", "code_review", now)
            if w:
                windows.append(w)
        # 모델별 추가 한도 — 있으면 표시용으로 덧붙인다. 이름이 곧 label이라
        # 분기 키로 못 쓰게(clauth의 tier/label 규칙과 동일) 그대로 문자열로만.
        for extra in data.get("additional_rate_limits") or []:
            if not isinstance(extra, dict):
                continue
            label = extra.get("limit_name")
            sub = extra.get("rate_limit")
            if not isinstance(label, str) or not isinstance(sub, dict):
                continue
            w = _window(sub.get("primary_window"), label, f"extra:{label}", now)
            if w:
                windows.append(w)

        plan = data.get("plan_type")
        return {
            "provider": self.name,
            "generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "stale": False,
            "profile": {
                "name": "codex",
                "active": True,
                "tier": str(plan) if isinstance(plan, str) else "",
                "provider": "codex",
                "auth_ok": True,
                "auth_status": "ok",
                "fetch_status": "Fresh",
                "has_live_session": False,
                "rolling_token": False,
                "stale": False,
                "windows": windows,
                "fallback": None,
            },
        }

    def snapshot(self) -> Optional[dict]:
        # ⚠ clauth는 `resets_in_sec`을 매 snapshot() 호출마다 절대시각에서
        # 새로 계산한다(캐시된 건 원본 JSON뿐). 여기는 TTL 캐시 안에
        # `resets_in_sec`까지 이미 구운 채로 들어 있어 최대 TTL_SEC(60초)
        # 만큼 오차가 날 수 있다 — 원본 응답 자체를 캐시하지 않기로 한
        # 결정(계획서 §3-1)과의 트레이드오프로 받아들인다. 90초 주기 데몬을
        # 쓰는 clauth도 비슷한 수준의 지연은 이미 감수하고 있다.
        data, _ = self._fetch()
        return data

    def capability(self) -> dict:
        data, err = self._fetch()
        if data is None:
            return {"available": False, "provider": self.name, "profiles": 0, "reason": err or "no-auth"}
        return {"available": True, "provider": self.name, "profiles": 1}
