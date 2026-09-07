"""U1 — clauth 어댑터. 데이터 소스는 **피드 파일** `~/.clauth/status.json`.

**왜 CLI가 아니라 파일인가**(계획서 §3 실측 비교):
  - `clauth status --json`은 매 폴링마다 10MB 바이너리를 spawn한다.
  - 파일은 `open()` + `json.load()`이고, mtime이 안 바뀌면 다시 파싱조차 안 한다.
  - `pending_switch`는 단발 CLI 실행에서는 항상 null이라 관측 자체가 불가능하다
    (데몬이 파일에 기록한다).
  - 실패 모드도 단순하다: 파일 없음 / 깨짐 / 권한 없음뿐. timeout·zombie 관리가 없다.
서버가 subprocess를 띄우지 않는 것은 이 프로젝트의 다른 결정(`clauth resume`은
tmux 안에서 실행)과도 일관된다.

**보안(계획서 §5)**: 사용량은 민감정보이고 이 서버는 공개 터널로 나간다.
  - 읽는 파일은 `status.json` **하나뿐**이다. `token_ledger.json`·`profiles/`·
    `session_profiles.json`은 쳐다보지 않는다.
  - 응답은 **필드 화이트리스트**로 만든다. 모르는 필드는 그대로 통과시키지 않고
    **버린다** — clauth가 나중에 토큰류 필드를 추가해도 새어나가지 않는다.
    (`base_url`·`third_party`처럼 지금 있는 필드도 UI가 안 쓰면 안 싣는다.)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 이 어댑터가 이해하는 피드 스키마 버전. 모르는 버전이면 **파싱을 포기한다** —
# 필드 의미가 바뀌었을 수 있는데 추측으로 그리면 숫자가 조용히 틀린다.
SUPPORTED_SCHEMA = 1

DEFAULT_REFRESH_MS = 90_000


def feed_path() -> Path:
    return Path(os.environ.get("VT_CLAUTH_STATUS", str(Path.home() / ".clauth" / "status.json")))


def _iso_to_epoch(value) -> Optional[float]:
    if not value or not isinstance(value, str):
        return None
    try:
        # clauth는 오프셋을 붙여 쓴다("...+00:00"). Z 표기도 방어적으로 받는다.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(value, default=None):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


class ClauthProvider:
    name = "clauth"

    def __init__(self, path: Optional[Path] = None):
        self._path = path or feed_path()
        # mtime 기반 캐시 — 피드는 90초마다 갱신되는데 페이지는 그보다 자주
        # 물어본다. 파일이 안 바뀌었으면 파싱 결과를 그대로 돌려준다.
        self._cache_mtime: Optional[float] = None
        self._cache: Optional[dict] = None
        self._cache_error: Optional[str] = None

    # ── 원본 읽기 ────────────────────────────────────────────────────────
    def _read_raw(self) -> tuple[Optional[dict], Optional[str]]:
        path = self._path
        try:
            st = path.stat()
        except FileNotFoundError:
            return None, "no-feed"
        except PermissionError:
            # 파일 권한 0600이라 서버가 다른 유저로 돌면 못 읽는다.
            return None, "permission"
        except OSError:
            return None, "no-feed"

        if self._cache is not None and self._cache_mtime == st.st_mtime:
            return self._cache, self._cache_error

        try:
            with path.open() as f:
                data = json.load(f)
        except PermissionError:
            self._cache_mtime, self._cache, self._cache_error = st.st_mtime, None, "permission"
            return None, "permission"
        except (json.JSONDecodeError, OSError):
            # 데몬이 쓰는 도중에 읽으면 깨진 JSON을 볼 수 있다. 다음 폴링에
            # 정상 파일이 오므로 에러로 취급하되 캐시에 남긴다(재파싱 폭주 방지).
            self._cache_mtime, self._cache, self._cache_error = st.st_mtime, None, "broken"
            return None, "broken"

        if not isinstance(data, dict):
            self._cache_mtime, self._cache, self._cache_error = st.st_mtime, None, "broken"
            return None, "broken"
        if data.get("schema") != SUPPORTED_SCHEMA:
            self._cache_mtime, self._cache, self._cache_error = st.st_mtime, None, "schema"
            return None, "schema"

        self._cache_mtime, self._cache, self._cache_error = st.st_mtime, data, None
        return data, None

    # ── 정규화 ───────────────────────────────────────────────────────────
    def _profile(self, raw: dict, now: float) -> dict:
        windows = []
        for w in raw.get("windows") or []:
            if not isinstance(w, dict):
                continue
            resets_at = w.get("resets_at")
            epoch = _iso_to_epoch(resets_at)
            windows.append({
                "label": str(w.get("label", "")),
                "pct": _num(w.get("utilization_pct"), 0.0),
                "resets_at": resets_at if isinstance(resets_at, str) else None,
                # 남은 시간은 서버가 계산해 내려준다 — 클라이언트 시계가 틀어져
                # 있어도 "몇 시간 후"가 맞게 보이도록(모바일에서 특히 흔하다).
                "resets_in_sec": max(0, int(epoch - now)) if epoch else None,
            })

        fallback = raw.get("fallback")
        fb = None
        if isinstance(fallback, dict):
            fb = {
                "position": _num(fallback.get("position")),
                "threshold": _num(fallback.get("threshold")),
                "armed": bool(fallback.get("armed")),
            }

        return {
            "name": str(raw.get("name", "")),
            "active": bool(raw.get("active")),
            "tier": str(raw.get("tier") or ""),          # 표시 전용
            "provider": str(raw.get("provider") or ""),  # 표시 전용
            # 없으면 "ok"로 본다(계획서 §3 읽기 계약 2번) — 옛 피드에는 이 필드가
            # 없을 수 있고, 그때 "인증 문제"로 잘못 표시하면 안 된다.
            "auth_ok": str(raw.get("auth_status", "ok")) == "ok",
            "auth_status": str(raw.get("auth_status", "ok")),
            "fetch_status": str(raw.get("fetch_status") or ""),
            "has_live_session": bool(raw.get("has_live_session")),
            "rolling_token": bool(raw.get("rolling_token")),
            "stale": bool(raw.get("stale")),
            "windows": windows,
            "fallback": fb,
        }

    def snapshot(self) -> Optional[dict]:
        data, err = self._read_raw()
        if data is None:
            return None
        now = time.time()
        generated = data.get("generated_at")
        gen_epoch = _iso_to_epoch(generated)
        interval_ms = _num(data.get("refresh_interval_ms"), DEFAULT_REFRESH_MS) or DEFAULT_REFRESH_MS
        # 갱신 주기의 2배를 넘겨도 안 바뀌었으면 데몬이 멈춘 것으로 본다.
        stale = bool(gen_epoch and (now - gen_epoch) > (interval_ms / 1000.0) * 2)

        profiles = [self._profile(p, now) for p in (data.get("profiles") or []) if isinstance(p, dict)]
        return {
            "provider": self.name,
            "generated_at": generated if isinstance(generated, str) else None,
            "stale": stale,
            "active_profile": str(data.get("active_profile") or "") or None,
            "profiles": profiles,
        }

    def capability(self) -> dict:
        data, err = self._read_raw()
        if data is None:
            return {"available": False, "provider": self.name, "profiles": 0, "reason": err or "no-feed"}
        profiles = [p for p in (data.get("profiles") or []) if isinstance(p, dict)]
        return {"available": True, "provider": self.name, "profiles": len(profiles)}
