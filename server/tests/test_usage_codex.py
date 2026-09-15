"""96번 계획서(2.1.5) — usage/codex.py.

clauth 테스트(test_usage.py)와 같은 무게 배분: "값이 맞는가"만큼 **"안 실려야
할 것이 안 실리는가"**(credits·model_usage·spend_control은 절대 새어나가면
안 된다)와 **"이상한 응답/장애에 안 죽는가"**(401·429·네트워크 오류·TTL)에
무게를 둔다. 실제 outbound 호출은 monkeypatch로 막는다 — 네트워크를 타지 않는다.
"""

from __future__ import annotations

import io
import json
import time
from urllib.error import HTTPError, URLError

import pytest

from usage.codex import CodexProvider, auth_path


def _auth_file(tmp_path, monkeypatch, *, account_id="acc-1"):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "x", "access_token": "tok-123", "refresh_token": "y",
            "account_id": account_id,
        },
        "last_refresh": "2026-09-14T00:00:00Z",
    }))
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path))
    return path


# 2026-09-14 이 맥에서 실제로 받은 응답 형태(계획서 §2) 그대로 — 민감할 수
# 있는 필드(credits·model_usage·spend_control)를 일부러 채워, 안 새는지 검증한다.
def _real_shaped_response(used_percent=55):
    return {
        "user_id": "user-abc",
        "account_id": "acc-1",
        "email": "someone@example.com",
        "plan_type": "pro",
        "rate_limit": {
            "allowed": True, "limit_reached": False,
            "primary_window": {"used_percent": used_percent, "limit_window_seconds": 604800,
                                "reset_after_seconds": 432873, "reset_at": 1789805401},
            "secondary_window": None,
        },
        "code_review_rate_limit": None,
        "additional_rate_limits": [
            {"limit_name": "GPT-5.3-Codex-Spark", "metered_feature": "codex_bengalfox",
             "rate_limit": {"allowed": True, "limit_reached": False,
                            "primary_window": {"used_percent": 0, "limit_window_seconds": 18000,
                                                "reset_after_seconds": 18000, "reset_at": 1789390529},
                            "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800,
                                                  "reset_after_seconds": 604800, "reset_at": 1789977329}},
             "normal_model_slug": None},
        ],
        "model_usage": {"gpt-6-astra": {"available": True, "available_at": None, "credits_would_enable": False}},
        "credits": {"has_credits": False, "unlimited": False, "overage_limit_reached": False,
                    "balance": "0", "approx_local_messages": [0, 0], "approx_cloud_messages": [0, 0]},
        "spend_control": {"reached": False, "individual_limit": None},
        "rate_limit_reset_credits": {"available_count": 2, "applicable_available_count": 0},
    }


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_auth_path_honors_vt_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path))
    assert auth_path() == tmp_path / "auth.json"


def test_no_auth_file_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path))  # 파일 없음
    p = CodexProvider()
    cap = p.capability()
    assert cap == {"available": False, "provider": "codex", "profiles": 0, "reason": "no-auth"}
    assert p.snapshot() is None


def test_auth_file_without_access_token_is_unavailable(tmp_path, monkeypatch):
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {}}))
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path))
    assert CodexProvider().capability()["available"] is False


def test_successful_response_normalizes_windows(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResponse(_real_shaped_response()))

    p = CodexProvider()
    cap = p.capability()
    assert cap == {"available": True, "provider": "codex", "profiles": 1}

    snap = p.snapshot()
    assert snap["provider"] == "codex"
    assert snap["stale"] is False
    profile = snap["profile"]
    assert profile["tier"] == "pro"
    labels = {w["label"]: w["pct"] for w in profile["windows"]}
    assert labels["5h"] == 55.0
    assert labels["GPT-5.3-Codex-Spark"] == 0.0
    assert "weekly" not in labels, "secondary_window가 None이면 만들지 않는다"


def test_sensitive_fields_never_leak_into_the_snapshot(tmp_path, monkeypatch):
    """계획서 §3-1 — credits·model_usage·spend_control은 화이트리스트 밖이다."""
    _auth_file(tmp_path, monkeypatch)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResponse(_real_shaped_response()))
    snap = CodexProvider().snapshot()
    blob = json.dumps(snap)
    for forbidden in ("credits", "model_usage", "spend_control", "balance", "user_id", "email"):
        assert forbidden not in blob, f"{forbidden!r}가 응답에 새어나갔다"


def test_access_token_itself_is_never_cached_or_returned(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)
    monkeypatch.setattr("usage.codex.urlopen", lambda req, timeout: _FakeResponse(_real_shaped_response()))
    p = CodexProvider()
    p.snapshot()
    blob = json.dumps(p.__dict__, default=str)
    assert "tok-123" not in blob


def test_401_is_reported_as_expired_not_generic_unavailable(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)

    def raise_401(req, timeout):
        raise HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b""))
    monkeypatch.setattr("usage.codex.urlopen", raise_401)

    p = CodexProvider()
    assert p.capability() == {"available": False, "provider": "codex", "profiles": 0, "reason": "expired"}


def test_429_reuses_prior_snapshot_marked_stale(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)
    calls = {"n": 0}

    def first_ok(req, timeout):
        calls["n"] += 1
        return _FakeResponse(_real_shaped_response())
    monkeypatch.setattr("usage.codex.urlopen", first_ok)
    p = CodexProvider()
    first = p.snapshot()
    assert first["stale"] is False

    def then_429(req, timeout):
        raise HTTPError(req.full_url, 429, "rate limited", {}, io.BytesIO(b""))
    monkeypatch.setattr("usage.codex.urlopen", then_429)
    p._cache_at = 0  # TTL을 강제로 만료시켜 재요청을 유도한다
    second = p.snapshot()
    assert second is not None and second["stale"] is True, "완전히 끄기보다 이전 값을 흐리게 보여준다"


def test_network_error_without_prior_cache_is_unavailable(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)

    def boom(req, timeout):
        raise URLError("no route to host")
    monkeypatch.setattr("usage.codex.urlopen", boom)
    p = CodexProvider()
    assert p.snapshot() is None
    assert p.capability()["reason"] == "unavailable"


def test_ttl_cache_avoids_refetching_within_window(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)
    calls = {"n": 0}

    def counting(req, timeout):
        calls["n"] += 1
        return _FakeResponse(_real_shaped_response())
    monkeypatch.setattr("usage.codex.urlopen", counting)

    p = CodexProvider()
    p.snapshot()
    p.snapshot()
    p.capability()
    assert calls["n"] == 1, "TTL 안에서는 다시 호출하지 않는다(여러 탭이 동시 폴링해도 429 방지)"


def test_ttl_expiry_triggers_a_fresh_fetch(tmp_path, monkeypatch):
    _auth_file(tmp_path, monkeypatch)
    calls = {"n": 0}

    def counting(req, timeout):
        calls["n"] += 1
        return _FakeResponse(_real_shaped_response(used_percent=calls["n"] * 10))
    monkeypatch.setattr("usage.codex.urlopen", counting)

    p = CodexProvider()
    p.snapshot()
    p._cache_at = time.time() - 61  # TTL(60초) 경과를 흉내
    second = p.snapshot()
    assert calls["n"] == 2
    assert second["profile"]["windows"][0]["pct"] == 20.0


def test_account_id_header_only_sent_when_present(tmp_path, monkeypatch):
    seen = {}
    _auth_file(tmp_path, monkeypatch, account_id=None)

    def capture(req, timeout):
        seen["headers"] = dict(req.header_items())
        return _FakeResponse(_real_shaped_response())
    monkeypatch.setattr("usage.codex.urlopen", capture)
    CodexProvider().snapshot()
    assert "Chatgpt-Account-Id" not in seen["headers"]  # urllib이 헤더 키를 정규화한다
