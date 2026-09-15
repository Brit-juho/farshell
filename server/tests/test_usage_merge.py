"""96번 계획서(2.1.5) — usage.merged_capability()/merged_snapshot().

`/api/usage`가 실제로 그리는 것(clauth+codex 프로필을 한 목록으로 합친다)과
`usage.capability()`(설정 →「정보」의 clauth 전용 진단, 안 바뀌어야 한다)가
서로 다른 함수라는 게 이 테스트의 핵심 불변식이다 — 섞으면 진단 패널이
codex 상태에 가려 틀린 말을 하게 된다(96번 계획서 §3 설계 절).
"""

from __future__ import annotations

import io
import json
import time
from urllib.error import HTTPError

import pytest
from starlette.testclient import TestClient

import main
import usage
from usage.codex import CodexProvider


def _clauth_feed(**over):
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    data = {
        "schema": 1, "generated_at": now, "active_profile": "brit",
        "refresh_interval_ms": 90000,
        "profiles": [{
            "name": "brit", "active": True, "rolling_token": False,
            "provider": "anthropic", "tier": "Pro", "has_live_session": True,
            "auth_status": "ok", "fetch_status": "Fresh", "stale": False,
            "windows": [{"label": "5h", "utilization_pct": 62.0, "resets_at": None}],
        }],
    }
    data.update(over)
    return data


def _codex_payload(pct=55):
    return {
        "plan_type": "pro",
        "rate_limit": {"primary_window": {"used_percent": pct, "reset_after_seconds": 100}, "secondary_window": None},
        "code_review_rate_limit": None, "additional_rate_limits": [],
        "credits": {"balance": "0"},  # 새면 안 됨 — 아래서 검사
    }


class _FakeResp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_CLAUTH_STATUS", str(tmp_path / "status.json"))
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("VT_USAGE_PROVIDER", raising=False)
    usage._reset_for_tests()
    yield tmp_path
    usage._reset_for_tests()


def _write_clauth(tmp_path, data):
    (tmp_path / "status.json").write_text(json.dumps(data))


def _write_codex_auth(tmp_path):
    d = tmp_path / "codex"
    d.mkdir(exist_ok=True)
    (d / "auth.json").write_text(json.dumps({"tokens": {"access_token": "t", "account_id": "a"}}))


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_merged_capability_true_when_only_clauth_available(isolated):
    _write_clauth(isolated, _clauth_feed())
    cap = usage.merged_capability()
    assert cap["available"] is True
    assert cap["profiles"] == 1


def test_merged_capability_true_when_only_codex_available(isolated, monkeypatch):
    _write_codex_auth(isolated)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload()))
    cap = usage.merged_capability()
    assert cap["available"] is True
    assert cap["profiles"] == 1


def test_merged_capability_false_and_clauth_reason_when_neither_available(isolated):
    # 피드도 없고 codex auth도 없다
    cap = usage.merged_capability()
    assert cap["available"] is False
    assert cap["reason"] == "no-feed"  # capability()(clauth) 것을 대표로 그대로 쓴다


def test_merged_snapshot_combines_profiles_from_both_sources(isolated, monkeypatch):
    _write_clauth(isolated, _clauth_feed())
    _write_codex_auth(isolated)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload(pct=42)))

    snap = usage.merged_snapshot()
    assert snap is not None
    names = {p["name"] for p in snap["profiles"]}
    assert names == {"brit", "codex"}
    codex_profile = next(p for p in snap["profiles"] if p["name"] == "codex")
    assert codex_profile["windows"][0]["pct"] == 42.0


def test_merged_snapshot_none_when_neither_has_anything(isolated):
    assert usage.merged_snapshot() is None


def test_merged_snapshot_stale_if_either_source_is_stale(isolated, monkeypatch):
    _write_clauth(isolated, _clauth_feed())
    _write_codex_auth(isolated)
    # 첫 호출로 codex 캐시를 채우고, 그 다음 429를 흉내내 stale 표시가 되게 한다.
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload()))
    provider = usage._get_codex_provider()
    provider.snapshot()

    def rate_limited(req, timeout):
        raise HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b""))
    monkeypatch.setattr("usage.codex.urlopen", rate_limited)
    provider._cache_at = 0

    snap = usage.merged_snapshot()
    assert snap["stale"] is True


def test_route_does_not_leak_codex_sensitive_fields(isolated, monkeypatch, client):
    """/api/usage 응답에도 credits 등이 새어나가면 안 된다 — 제공자 테스트와
    별개로 라우트 왕복까지 확인한다."""
    _write_codex_auth(isolated)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload()))
    r = client.get("/api/usage")
    assert r.status_code == 200
    blob = r.text
    assert "credits" not in blob and "balance" not in blob


def test_route_available_when_only_codex_present(isolated, monkeypatch, client):
    _write_codex_auth(isolated)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload()))
    r = client.get("/api/usage")
    body = r.json()
    assert body["available"] is True
    assert [p["name"] for p in body["profiles"]] == ["codex"]


def test_capabilities_diagnostic_field_stays_clauth_only(isolated, monkeypatch, client):
    """설정 →「정보」가 읽는 `caps.usage`는 codex가 있어도 그대로 clauth 전용
    이어야 한다 — 이게 merged_*를 쓰지 않고 `capability()`를 그대로 둔 이유다."""
    _write_codex_auth(isolated)  # codex만 있고 clauth 피드는 없음
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResp(_codex_payload()))
    r = client.get("/api/capabilities")
    body = r.json()
    assert body["usage"]["available"] is False
    assert body["usage"]["reason"] == "no-feed"
    assert body["usage_codex"]["available"] is True
