"""브라우저 broker 설정 — `~/.vt/authz/browser.json`(0600). 어댑터별 켜짐과 첫 사용 동의(N-26).

  {"version": 1, "consent": <동의한 시각>|null, "adapters": {"playwright": {"enabled": true, "at": <시각>}}}

**잘못된 설정은 꺼짐으로 읽는다**: 파일이 없거나, 해석할 수 없거나, 모양이 틀리거나, `enabled` 가 정확히 `true`(JSON 불리언)가 아니면 그 어댑터는 꺼져 있다
(`"yes"`·`1`·`"true"` 도 꺼짐). 알 수 없는 어댑터 이름은 무시한다. 읽기 오류는 `error` 로 보이되 어떤 것도 켜지 않는다.
기능 전체의 마스터 스위치는 `VT_AUTHZ_ENFORCE` 의 `browser` 토큰이고(기본 꺼짐), 어댑터는 그 위에서 따로 켜야 한다. 처음 켤 때는 사람이 동의를 명시해야 한다(`consent_required`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import store_lock
import vt_paths
from authz import store as authz_store

logger = logging.getLogger(__name__)

VERSION = 1

def consent_text() -> str:
    from i18n import tr
    return tr("브라우저 broker 를 처음 켭니다. 켜면 에이전트가 승인된 범위에서 FarShell 이 관리하는 격리된 브라우저 탭을 열고 조작할 수 있습니다. "
              "사용자의 Chrome 프로필과 로그인 세션에는 연결하지 않으며, 기본으로는 about:blank 와 FarShell 이 노출한 localhost 포트만 열립니다. "
              "스크립트 실행·쿠키/저장소 값·네트워크 내용은 별도 승인이 필요합니다. 언제든 어댑터를 끌 수 있습니다.")


def path() -> Path:
    return vt_paths.state_dir() / "authz" / "browser.json"


def load() -> dict:
    """`{consent, adapters: {이름: bool}, error}` — 잘못된 설정은 전부 꺼짐."""
    out = {"consent": None, "adapters": {}, "error": None}
    p = path()
    if not p.is_file():
        return out
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("adapters", {}), dict):
            raise ValueError("shape")
    except (OSError, ValueError):
        out["error"] = "unreadable"
        return out
    c = d.get("consent")
    out["consent"] = c if isinstance(c, (int, float)) and not isinstance(c, bool) else None
    for name, rec in d.get("adapters", {}).items():
        if isinstance(rec, dict) and rec.get("enabled") is True:
            out["adapters"][str(name)] = True
    return out


def is_enabled(name: str) -> bool:
    return bool(load()["adapters"].get(name))


def _write(d: dict) -> None:
    p = path()
    authz_store.write_private(p, (json.dumps(d, indent=1, sort_keys=True) + "\n").encode())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


@contextlib.contextmanager
def _locked():
    authz_store._ensure_dir(path().parent)
    with store_lock.exclusive(path().parent / "browser.lock"):
        yield


def set_enabled(name: str, enabled: bool, *, consent: bool = False, now: Optional[float] = None) -> dict:
    """어댑터를 켜거나 끈다. 처음 켜는 것(`consent` 기록이 없을 때)은 `consent=True` 가 있어야 한다 — 없으면 ValueError("consent_required").
    읽을 수 없는 설정 파일은 덮어쓰지 않는다(OSError)."""
    t = time.time() if now is None else now
    with _locked():
        p = path()
        d = {"version": VERSION, "consent": None, "adapters": {}}
        if p.is_file():
            try:
                cur = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(cur, dict) or not isinstance(cur.get("adapters", {}), dict):
                    raise ValueError("shape")
            except ValueError as e:
                raise OSError("browser.json 을 해석할 수 없어 바꾸지 않았습니다") from e
            d.update({k: cur.get(k) for k in ("consent",) if k in cur})
            d["adapters"] = {str(k): v for k, v in cur.get("adapters", {}).items() if isinstance(v, dict)}
        if enabled:
            has = isinstance(d.get("consent"), (int, float)) and not isinstance(d.get("consent"), bool)
            if not has:
                if consent is not True:
                    raise ValueError("consent_required")
                d["consent"] = round(t, 3)
            d["adapters"][name] = {"enabled": True, "at": round(t, 3)}
        else:
            d["adapters"][name] = {"enabled": False, "at": round(t, 3)}
        d["version"] = VERSION
        _write(d)
    return load()
