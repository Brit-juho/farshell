"""U1 — 사용량 provider(clauth 어댑터).

이 데이터는 **공개 터널로 나가는 서버**가 내보내는 민감정보다. 그래서 테스트의
무게가 "값이 맞는가"만큼이나 **"안 실려야 할 것이 안 실리는가"**(필드 화이트
리스트)와 **"이상한 피드에 안 죽는가"**(없음/깨짐/모르는 schema/권한)에 있다.
"""

import json
import os
import time

import pytest
from starlette.testclient import TestClient

import main
import usage
from usage.clauth import ClauthProvider


def _feed(**over):
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    data = {
        "schema": 1,
        "generated_at": now,
        "active_profile": "brit",
        "refresh_interval_ms": 90000,
        "profiles": [
            {
                "name": "brit", "active": True, "rolling_token": False,
                "provider": "anthropic", "base_url": "https://api.anthropic.com",
                "tier": "Pro", "has_live_session": True, "auth_status": "ok",
                "fetch_status": "Fresh", "stale": False,
                "fallback": {"position": 1, "threshold": 95.0, "armed": True},
                "windows": [
                    {"label": "5h", "utilization_pct": 62.0, "resets_at": None},
                    {"label": "7d", "utilization_pct": 4.0,
                     "resets_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))},
                ],
                # 민감할 수 있는 필드 — 응답에 절대 나오면 안 된다
                "third_party": {"token": "SECRET-TOKEN"},
                "session_token": "SECRET",
            },
            {"name": "fornerds", "active": False, "provider": "generic", "windows": []},
        ],
    }
    data.update(over)
    return data


@pytest.fixture
def feed(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setenv("VT_CLAUTH_STATUS", str(path))
    monkeypatch.delenv("VT_USAGE_PROVIDER", raising=False)
    usage._reset_for_tests()
    yield path
    usage._reset_for_tests()


def write(path, data):
    path.write_text(json.dumps(data))


# ── 정상 경로 ─────────────────────────────────────────────────────────────
def test_snapshot_normalizes_feed(feed):
    write(feed, _feed())
    snap = ClauthProvider(feed).snapshot()
    assert snap["provider"] == "clauth"
    assert snap["active_profile"] == "brit"
    assert [p["name"] for p in snap["profiles"]] == ["brit", "fornerds"]
    brit = snap["profiles"][0]
    assert brit["windows"][0] == {"label": "5h", "pct": 62.0, "resets_at": None, "resets_in_sec": None}
    assert brit["fallback"] == {"position": 1, "threshold": 95.0, "armed": True}


def test_resets_in_sec_is_computed_server_side(feed):
    """클라이언트 시계가 틀어져 있어도 '몇 시간 후'가 맞아야 한다(모바일에서 흔하다)."""
    write(feed, _feed())
    week = ClauthProvider(feed).snapshot()["profiles"][0]["windows"][1]
    assert 3500 < week["resets_in_sec"] <= 3600


def test_secret_fields_are_dropped(feed):
    """화이트리스트 — 모르는 필드는 통과시키지 않고 버린다."""
    write(feed, _feed())
    blob = json.dumps(ClauthProvider(feed).snapshot())
    assert "SECRET" not in blob
    assert "third_party" not in blob
    assert "base_url" not in blob, "UI가 안 쓰는 필드는 애초에 싣지 않는다"


def test_profile_without_windows(feed):
    """provider:generic은 바를 못 그린다 — 빈 배열로 정상 처리돼야 한다."""
    write(feed, _feed())
    snap = ClauthProvider(feed).snapshot()
    assert snap["profiles"][1]["windows"] == []
    assert snap["profiles"][1]["auth_ok"] is True, "auth_status 없으면 ok로 본다"


# ── 이상한 피드 ───────────────────────────────────────────────────────────
def test_missing_feed(feed):
    cap = ClauthProvider(feed).capability()
    assert cap == {"available": False, "provider": "clauth", "profiles": 0, "reason": "no-feed"}
    assert ClauthProvider(feed).snapshot() is None


def test_broken_json(feed):
    feed.write_text("{ not json")
    cap = ClauthProvider(feed).capability()
    assert cap["available"] is False and cap["reason"] == "broken"


def test_unknown_schema_is_refused(feed):
    """모르는 버전은 추측해서 그리지 않는다 — 숫자가 조용히 틀리는 게 최악이다."""
    write(feed, _feed(schema=99))
    cap = ClauthProvider(feed).capability()
    assert cap["available"] is False and cap["reason"] == "schema"
    # 조용히 끄되 「무엇을 봤는지」는 남긴다 — 설정 화면이 이걸 보여준다.
    assert cap["schema_seen"] == 99
    assert 2 in cap["schema_supported"]


def _feed_v2(**over):
    """실제 clauth schema 2 피드 모양(2026-09-13 ~/.clauth/status.json 실측).

    2는 1에 필드를 더하기만 했다. 그런데 어댑터가 schema==1만 받고 있어서
    clauth가 올라간 순간 사용량이 통째로 꺼졌고, 아무 에러도 안 나서 몇 주
    동안 아무도 몰랐다. 이 픽스처가 그 회귀를 고정한다.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    data = {
        "schema": 2,
        "generated_at": now,
        "active_profile": "fornerds",
        "pending_switch": None,
        "wrap_off": False,
        "refresh_interval_ms": 90000,
        "profiles": [
            {
                "name": "brit", "active": False, "rolling_token": True,
                "provider": "anthropic", "base_url": None, "tier": "Pro",
                "has_live_session": False, "auth_status": "ok",
                "fetch_status": "Fresh", "stale": False,
                "fetched_at": now, "next_refresh_at": now,
                "auto_start": False, "auto_start_queue": None, "bell_threshold": None,
                "fallback": {"position": 1, "threshold": 95.0, "armed": False},
                "windows": [
                    {"label": "5h", "utilization_pct": 0.0, "resets_at": None},
                    {"label": "7d", "utilization_pct": 52.0, "resets_at": None},
                ],
                "third_party": None,
            },
            {
                "name": "fornerds", "active": True, "rolling_token": False,
                "provider": "generic", "base_url": "http://100.87.66.59:4747",
                "tier": None, "has_live_session": False, "auth_status": "ok",
                "fetch_status": None, "stale": False, "fetched_at": None,
                "next_refresh_at": None, "auto_start": False,
                "fallback": {"position": 2, "threshold": 95.0, "armed": True},
                "windows": [], "third_party": None,
            },
        ],
    }
    data.update(over)
    return data


def test_schema_2_is_accepted(feed):
    write(feed, _feed_v2())
    provider = ClauthProvider(feed)
    cap = provider.capability()
    assert cap["available"] is True and cap["profiles"] == 2
    snap = provider.snapshot()
    assert snap["active_profile"] == "fornerds"
    assert [p["name"] for p in snap["profiles"]] == ["brit", "fornerds"]
    brit = snap["profiles"][0]
    assert brit["tier"] == "Pro" and brit["auth_ok"] is True
    assert brit["windows"][1]["pct"] == 52.0
    # tier/fetch_status가 null인 프로필도 문자열로 정규화돼야 한다(폰에서 깨짐 방지)
    forn = snap["profiles"][1]
    assert forn["tier"] == "" and forn["fetch_status"] == "" and forn["active"] is True


def test_schema_2_does_not_leak_tokens(feed):
    """rolling_token은 bool로만 실린다 — 화이트리스트 원칙은 버전이 올라가도 같다."""
    write(feed, _feed_v2())
    snap = ClauthProvider(feed).snapshot()
    blob = json.dumps(snap)
    assert "base_url" not in blob and "100.87.66.59" not in blob
    assert snap["profiles"][0]["rolling_token"] is True


def test_permission_error(feed, monkeypatch):
    write(feed, _feed())
    provider = ClauthProvider(feed)

    real_open = type(feed).open

    def boom(self, *a, **k):
        raise PermissionError()

    monkeypatch.setattr(type(feed), "open", boom)
    cap = provider.capability()
    monkeypatch.setattr(type(feed), "open", real_open)
    assert cap["available"] is False and cap["reason"] == "permission"


def test_stale_when_generated_at_is_old(feed):
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 600))
    write(feed, _feed(generated_at=old))
    assert ClauthProvider(feed).snapshot()["stale"] is True


def test_not_stale_within_two_intervals(feed):
    recent = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 100))
    write(feed, _feed(generated_at=recent))
    assert ClauthProvider(feed).snapshot()["stale"] is False


def test_garbage_profile_entries_are_skipped(feed):
    write(feed, _feed(profiles=["nope", 42, {"name": "ok"}]))
    snap = ClauthProvider(feed).snapshot()
    assert [p["name"] for p in snap["profiles"]] == ["ok"]


def test_mtime_cache_avoids_reparsing(feed, monkeypatch):
    write(feed, _feed())
    provider = ClauthProvider(feed)
    provider.snapshot()

    calls = []
    real_load = json.load

    def counting_load(fp):
        calls.append(1)
        return real_load(fp)

    monkeypatch.setattr(json, "load", counting_load)
    provider.snapshot()
    provider.snapshot()
    assert calls == [], "mtime이 그대로면 다시 파싱하지 않는다"


# ── 팩토리 (VT_USAGE_PROVIDER) ────────────────────────────────────────────
def test_auto_uses_clauth_when_feed_exists(feed):
    write(feed, _feed())
    assert usage.capability()["provider"] == "clauth"


def test_auto_falls_back_to_null_without_feed(feed):
    cap = usage.capability()
    assert cap == {"available": False, "provider": "none", "profiles": 0, "reason": "no-feed"}


def test_none_disables_even_with_feed(feed, monkeypatch):
    write(feed, _feed())
    monkeypatch.setenv("VT_USAGE_PROVIDER", "none")
    usage._reset_for_tests()
    cap = usage.capability()
    assert cap["available"] is False and cap["reason"] == "disabled"
    assert usage.snapshot() is None


def test_forced_clauth_reports_reason_without_feed(feed, monkeypatch):
    """강제 모드는 '왜 안 뜨는지'를 알기 위한 것 — 조용히 사라지면 안 된다."""
    monkeypatch.setenv("VT_USAGE_PROVIDER", "clauth")
    usage._reset_for_tests()
    cap = usage.capability()
    assert cap["provider"] == "clauth" and cap["reason"] == "no-feed"


# ── 엔드포인트 ────────────────────────────────────────────────────────────
@pytest.fixture
def client(feed):
    with TestClient(main.app) as c:
        yield c


def test_api_usage_returns_snapshot(client, feed):
    write(feed, _feed())
    usage._reset_for_tests()
    body = client.get("/api/usage").json()
    assert body["available"] is True
    assert body["profiles"][0]["name"] == "brit"
    assert "SECRET" not in json.dumps(body)


def test_api_usage_without_feed_is_200_with_reason(client):
    """404가 아니다 — 프런트가 '기능 없음'과 '일시 실패'를 구분할 수 있어야 한다."""
    r = client.get("/api/usage")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_capabilities_includes_usage(client, feed):
    write(feed, _feed())
    usage._reset_for_tests()
    caps = client.get("/api/capabilities").json()
    assert caps["usage"]["available"] is True
    assert caps["usage"]["profiles"] == 2


def test_capabilities_etag_stable_while_feed_timestamp_changes(client, feed):
    """generated_at만 바뀌는 갱신으로 ETag가 흔들리면 캐시가 무의미해진다."""
    write(feed, _feed())
    usage._reset_for_tests()
    first = client.get("/api/usage").headers.get("etag")
    write(feed, _feed(generated_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 1))))
    second = client.get("/api/usage").headers.get("etag")
    assert first and first == second
