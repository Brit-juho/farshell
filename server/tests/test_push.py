"""P5 회귀: Web Push 가 조용히 틀리지 않아야 한다.

푸시는 실패해도 아무 소리가 안 나는 기능이라, 틀리면 "안 오는 줄도 모르는" 상태가 된다.
특히 아래는 실제로 물릴 수 있다:
  - trycloudflare URL 이 바뀌면 기존 구독은 죽는다(구독은 origin 에 묶인다).
    죽은 구독에 계속 쏘면서 "보냈다"고 보고하면 안 된다.
  - 만료(404/410) 구독을 청소하지 않으면 목록이 영원히 자란다.
  - VAPID 개인키는 0600 이어야 한다 — 새면 남이 내 폰으로 알림을 쏜다.
"""

import json
import os
from pathlib import Path

import pytest

import push


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    yield


def _sub(endpoint):
    return {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}}


def test_vapid_keys_are_generated_and_persisted():
    if not push.available():
        pytest.skip("pywebpush 미설치")
    k1 = push.get_keys()
    assert k1 and k1["public"] and k1["private"]
    k2 = push.get_keys()
    assert k1["public"] == k2["public"], "호출할 때마다 키가 바뀌면 기존 구독이 다 죽는다"


def test_vapid_file_permissions_are_0600():
    if not push.available():
        pytest.skip("pywebpush 미설치")
    push.get_keys()
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "vapid.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert oct(p.parent.stat().st_mode & 0o777) == "0o700"


def test_public_key_is_urlsafe_base64_unpadded():
    if not push.available():
        pytest.skip("pywebpush 미설치")
    key = push.public_key()
    assert "+" not in key and "/" not in key and "=" not in key


def test_add_and_remove_subscription():
    assert push.add_sub(_sub("https://fcm.example/a"), "https://x.trycloudflare.com")["ok"]
    assert len(push.list_subs()) == 1
    r = push.remove_sub("https://fcm.example/a")
    assert r["removed"] == 1 and push.list_subs() == []


def test_duplicate_endpoint_updates_instead_of_appending():
    push.add_sub(_sub("https://fcm.example/a"), "https://one.example")
    push.add_sub(_sub("https://fcm.example/a"), "https://two.example")
    subs = push.list_subs()
    assert len(subs) == 1
    assert subs[0]["origin"] == "https://two.example"


def test_bad_subscription_rejected():
    assert not push.add_sub({}, "")["ok"]
    assert not push.add_sub({"keys": {}}, "")["ok"]


def test_subs_file_permissions_are_0600():
    push.add_sub(_sub("https://fcm.example/a"), "")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "push-subs.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_send_skips_subscriptions_from_a_different_origin(monkeypatch):
    """터널 URL이 바뀌면 옛 origin 구독은 건너뛰어야 한다(쏴봐야 안 간다)."""
    if not push.available():
        pytest.skip("pywebpush 미설치")
    push.add_sub(_sub("https://fcm.example/old"), "https://old.trycloudflare.com")
    push.add_sub(_sub("https://fcm.example/new"), "https://new.trycloudflare.com")
    monkeypatch.setattr(push, "_current_origin", lambda: "https://new.trycloudflare.com")

    sent = []
    monkeypatch.setattr("pywebpush.webpush",
                        lambda **kw: sent.append(kw["subscription_info"]["endpoint"]))
    r = push.send("제목", "본문")
    assert r["sent"] == 1 and r["skipped_origin"] == 1
    assert sent == ["https://fcm.example/new"]


def test_send_prunes_expired_subscriptions(monkeypatch):
    """410 Gone 은 만료다 — 목록에서 지워야 무한히 자라지 않는다."""
    if not push.available():
        pytest.skip("pywebpush 미설치")
    push.add_sub(_sub("https://fcm.example/dead"), "")
    push.add_sub(_sub("https://fcm.example/live"), "")
    monkeypatch.setattr(push, "_current_origin", lambda: "")

    from pywebpush import WebPushException

    class Resp:
        status_code = 410

    def fake(**kw):
        if "dead" in kw["subscription_info"]["endpoint"]:
            raise WebPushException("gone", response=Resp())

    monkeypatch.setattr("pywebpush.webpush", fake)
    r = push.send("제목", "본문")
    assert r["sent"] == 1 and r["expired"] == 1
    eps = [s["subscription"]["endpoint"] for s in push.list_subs()]
    assert eps == ["https://fcm.example/live"]


def test_send_keeps_subscription_on_transient_error(monkeypatch):
    """500 같은 일시적 오류로 구독을 지우면 안 된다."""
    if not push.available():
        pytest.skip("pywebpush 미설치")
    push.add_sub(_sub("https://fcm.example/a"), "")
    monkeypatch.setattr(push, "_current_origin", lambda: "")
    from pywebpush import WebPushException

    class Resp:
        status_code = 500

    def fake(**kw):
        raise WebPushException("boom", response=Resp())

    monkeypatch.setattr("pywebpush.webpush", fake)
    r = push.send("제목")
    assert r["sent"] == 0 and r["expired"] == 0
    assert len(push.list_subs()) == 1


def test_send_with_no_subscriptions_is_noop():
    r = push.send("제목")
    assert r["ok"] and r["sent"] == 0


def test_payload_is_truncated(monkeypatch):
    """잠금화면에 뜨는 내용이다 — 길게 실어 보내지 않는다."""
    if not push.available():
        pytest.skip("pywebpush 미설치")
    push.add_sub(_sub("https://fcm.example/a"), "")
    monkeypatch.setattr(push, "_current_origin", lambda: "")
    captured = {}
    monkeypatch.setattr("pywebpush.webpush",
                        lambda **kw: captured.update(json.loads(kw["data"])))
    push.send("T" * 500, "B" * 500)
    assert len(captured["title"]) <= push.MAX_BODY
    assert len(captured["body"]) <= push.MAX_BODY


def test_status_reports_stale_origin_count(monkeypatch):
    push.add_sub(_sub("https://fcm.example/old"), "https://old.example")
    monkeypatch.setattr(push, "_current_origin", lambda: "https://new.example")
    st = push.status()
    assert st["subscriptions"] == 1 and st["stale_origin"] == 1
