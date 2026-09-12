"""N7/N39 1단계 — 호스트 레지스트리 + 페어링 회귀.

수용 기준(80-multihost-agents.md): 레지스트리 CRUD · 인젝션 불가 · 연결 실패 표시.
여기에 실제 설계 검토(2026-09-12)에서 나온 위험 요소를 회귀로 고정한다:
`local` 예약어 · 서명 시간창 · nonce 재생 · 취소 즉시 무효 · 전이 금지.
"""

from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture
def hs(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    import host_store
    importlib.reload(host_store)
    return host_store


# --- id 정규화 / 예약어 ---------------------------------------------------------


@pytest.mark.parametrize("bad", ["local", "self", "me", "LOCAL", " local "])
def test_reserved_ids_are_rejected(hs, bad):
    """`local`은 '이 호스트 자신'을 뜻한다(tree.js leaf의 host 기본값).
    상대가 이 id로 등록되면 로컬 호스트를 가로챈다."""
    assert hs.normalize_id(bad) is None


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "a b", "../etc", "a" * 41, "id;rm -rf"])
def test_malformed_ids_are_rejected(hs, bad):
    assert hs.normalize_id(bad) is None


def test_valid_ids_are_lowercased(hs):
    assert hs.normalize_id("GPU-Box_1") == "gpu-box_1"


def test_add_grant_rejects_reserved_id(hs):
    assert hs.add_grant("local", "가짜") is None


def test_add_peer_rejects_reserved_id(hs):
    assert hs.add_peer("local", "https://x", "s") is None


# --- self 정체성 ------------------------------------------------------------------


def test_get_self_is_stable_across_calls(hs):
    a = hs.get_self()
    b = hs.get_self()
    assert a["id"] == b["id"]
    assert hs.normalize_id(a["id"]) == a["id"]


def test_set_self_label(hs):
    hs.get_self()
    me = hs.set_self_label("작업실 맥")
    assert me["label"] == "작업실 맥"
    assert hs.get_self()["label"] == "작업실 맥"


# --- 페어링 티켓 -------------------------------------------------------------------


def test_pair_ticket_is_single_use(hs):
    t = hs.issue_pair_ticket("노트북")
    assert hs.consume_pair_ticket(t) is not None
    assert hs.consume_pair_ticket(t) is None


def test_pair_ticket_expires(hs, monkeypatch):
    t = hs.issue_pair_ticket()
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + hs.PAIR_TICKET_TTL + 10)
    assert hs.consume_pair_ticket(t) is None


def test_unknown_ticket_is_rejected(hs):
    hs.issue_pair_ticket()
    assert hs.consume_pair_ticket("not-a-real-ticket") is None


# --- grants (inbound) --------------------------------------------------------------


def test_add_grant_defaults_to_view(hs):
    """기본이 읽기 전용이라는 것 자체가 SSH 대비 얻는 것 — 회귀로 고정."""
    grant, secret = hs.add_grant("laptop", "노트북")
    assert grant["level"] == hs.LEVEL_VIEW
    assert secret and len(secret) > 20


def test_grant_secret_is_not_exposed_in_list(hs):
    hs.add_grant("laptop", "노트북")
    rows = hs.list_grants()
    assert rows and "secret" not in rows[0]


def test_repairing_rotates_the_secret(hs):
    _, s1 = hs.add_grant("laptop", "노트북")
    _, s2 = hs.add_grant("laptop", "노트북")
    assert s1 != s2
    assert len(hs.list_grants()) == 1  # 중복 항목이 쌓이지 않는다
    assert hs.find_grant("laptop")["secret"] == s2  # 옛 secret은 그 즉시 무효


def test_set_grant_level_and_back(hs):
    hs.add_grant("laptop", "노트북")
    assert hs.set_grant_level("laptop", hs.LEVEL_CONTROL)["level"] == "control"
    assert hs.set_grant_level("laptop", hs.LEVEL_VIEW)["level"] == "view"


def test_set_grant_level_rejects_unknown_level(hs):
    hs.add_grant("laptop", "노트북")
    assert hs.set_grant_level("laptop", "root") is None


def test_remove_grant(hs):
    hs.add_grant("laptop", "노트북")
    assert hs.remove_grant("laptop") is True
    assert hs.find_grant("laptop") is None
    assert hs.remove_grant("laptop") is False


# --- peers (outbound) --------------------------------------------------------------


def test_add_and_find_peer(hs):
    hs.add_peer("gpu-box", "https://x.trycloudflare.com/", "sekret", label="작업실")
    p = hs.find_peer("gpu-box")
    assert p["url"] == "https://x.trycloudflare.com"  # 뒤 슬래시 정규화
    assert p["secret"] == "sekret"


def test_peer_secret_is_not_exposed_in_list(hs):
    hs.add_peer("gpu-box", "https://x", "sekret")
    assert "secret" not in hs.list_peers()[0]


def test_rename_peer_is_local_only(hs):
    """별명은 내 쪽에서만 쓰는 이름 — 상대 id는 그대로다."""
    hs.add_peer("gpu-box", "https://x", "s", label="원래이름")
    p = hs.rename_peer("gpu-box", "작업실 맥")
    assert p["label"] == "작업실 맥"
    assert p["id"] == "gpu-box"


def test_update_peer_tracks_liveness(hs):
    hs.add_peer("gpu-box", "https://x", "s")
    hs.update_peer("gpu-box", lastSeen=123, latencyMs=42)
    p = hs.find_peer("gpu-box")
    assert p["lastSeen"] == 123 and p["latencyMs"] == 42


def test_remove_peer(hs):
    hs.add_peer("gpu-box", "https://x", "s")
    assert hs.remove_peer("gpu-box") is True
    assert hs.remove_peer("gpu-box") is False


def test_revoke_all_clears_both_directions(hs):
    hs.add_peer("gpu-box", "https://x", "s")
    hs.add_grant("laptop", "노트북")
    r = hs.revoke_all()
    assert r == {"peers": 1, "grants": 1}
    assert hs.list_peers() == [] and hs.list_grants() == []


# --- 요청 서명 ---------------------------------------------------------------------


def test_signature_round_trip(hs):
    ts, nonce = int(time.time()), "abc"
    sig = hs.sign_request("sekret", "GET", "/api/peer/ping", ts, nonce)
    assert hs.verify_signature("sekret", "GET", "/api/peer/ping", ts, nonce, sig)


def test_signature_is_bound_to_method_and_path(hs):
    """view용 GET 서명을 control용 POST에 돌려쓰지 못해야 한다."""
    ts, nonce = int(time.time()), "abc"
    sig = hs.sign_request("sekret", "GET", "/api/peer/ping", ts, nonce)
    assert not hs.verify_signature("sekret", "POST", "/api/peer/ping", ts, nonce, sig)
    assert not hs.verify_signature("sekret", "GET", "/api/peer/input", ts, nonce, sig)


def test_signature_rejects_wrong_secret(hs):
    ts, nonce = int(time.time()), "abc"
    sig = hs.sign_request("sekret", "GET", "/p", ts, nonce)
    assert not hs.verify_signature("other", "GET", "/p", ts, nonce, sig)


def test_signature_expires_outside_window(hs):
    """시계가 크게 어긋나면 거부 — 오래된 요청을 재사용하지 못한다."""
    ts = int(time.time()) - hs.SIGNATURE_WINDOW_SEC - 10
    sig = hs.sign_request("sekret", "GET", "/p", ts, "abc")
    assert not hs.verify_signature("sekret", "GET", "/p", ts, "abc", sig)


def test_signature_accepts_small_clock_skew(hs):
    """몇 초 차이는 통과해야 한다 — 두 맥의 시계가 정확히 같을 수는 없다."""
    ts = int(time.time()) + 5
    sig = hs.sign_request("sekret", "GET", "/p", ts, "abc")
    assert hs.verify_signature("sekret", "GET", "/p", ts, "abc", sig)


# --- nonce 재생 차단 ----------------------------------------------------------------


def test_nonce_cache_rejects_replay(hs):
    c = hs.NonceCache()
    assert c.check_and_add("peer:abc") is True
    assert c.check_and_add("peer:abc") is False


def test_nonce_cache_scopes_by_key(hs):
    c = hs.NonceCache()
    assert c.check_and_add("a:abc") is True
    assert c.check_and_add("b:abc") is True  # 다른 상대의 같은 nonce는 무관


def test_nonce_cache_evicts_old_entries(hs):
    """창이 짧아 메모리가 자연히 제한된다 — 무한히 쌓이면 안 된다."""
    c = hs.NonceCache(window=1)
    c.check_and_add("peer:old", now=1000)
    c.check_and_add("peer:new", now=1100)
    assert len(c._seen) == 1


# --- 감사 로그 -----------------------------------------------------------------------


def test_audit_records_success_and_failure(hs):
    hs.audit("laptop", "ping", True)
    hs.audit("laptop", "auth", False, "서명 불일치")
    rows = hs.read_audit()
    assert len(rows) == 2
    assert rows[0]["action"] == "auth" and rows[0]["ok"] is False  # 최신이 먼저
    assert rows[1]["ok"] is True


def test_audit_filters_by_peer(hs):
    hs.audit("laptop", "ping", True)
    hs.audit("gpu-box", "ping", True)
    assert [r["peer"] for r in hs.read_audit("laptop")] == ["laptop"]


def test_read_audit_on_missing_file_is_empty(hs):
    assert hs.read_audit() == []
