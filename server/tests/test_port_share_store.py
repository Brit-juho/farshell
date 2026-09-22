"""2.2 — port_share_store.py 단위 테스트 (plan-2.2/00-port-access.md §9 수용 기준).

권한·PIN 원문 미저장·재노출 시 새 자격·proxyPort 영속·fail-closed를 확인한다.
HTTP 레이어(프록시 앱·라우트)는 별도 파일로 분리한다.
"""

from __future__ import annotations

import importlib
import json
import threading
import time

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """매 테스트마다 격리된 ~/.vt를 가리키도록 재로드."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7800")
    import port_share_store
    importlib.reload(port_share_store)
    return port_share_store


HOUR = 3600


# --- 기본 왕복 -----------------------------------------------------------------


def test_create_and_get_roundtrip(store):
    share, pin = store.create_share(3000, "앱", "session", HOUR)
    assert pin is None  # session 모드에는 PIN이 없다
    got = store.get_share(3000)
    assert got is not None
    assert got["shareId"] == share["shareId"]
    assert got["mode"] == "session"


def test_get_share_for_unknown_port_is_none(store):
    assert store.get_share(9999) is None


def test_find_by_share_id_and_proxy_port(store):
    share, _ = store.create_share(3000, "앱", "public", HOUR)
    assert store.find_by_share_id(share["shareId"])["port"] == 3000
    assert store.find_by_proxy_port(share["proxyPort"])["port"] == 3000
    assert store.find_by_share_id("nope") is None
    assert store.find_by_share_id("") is None


# --- 파일 권한 -----------------------------------------------------------------


def test_meta_file_is_0600(store, tmp_path):
    store.create_share(3000, "앱", "pin", HOUR)
    mode = (tmp_path / "port_shares.json").stat().st_mode & 0o777
    assert mode == 0o600, f"세계 읽기 가능하면 PIN 해시가 새어나간다: {oct(mode)}"


def test_state_dir_is_0700(store, tmp_path):
    store.create_share(3000, "앱", "pin", HOUR)
    assert (tmp_path.stat().st_mode & 0o777) == 0o700


# --- PIN: 원문은 어디에도 남지 않는다 ---------------------------------------------


def test_pin_plaintext_is_never_written_to_disk(store, tmp_path):
    _, pin = store.create_share(3000, "앱", "pin", HOUR)
    assert pin is not None
    raw = (tmp_path / "port_shares.json").read_text(encoding="utf-8")
    assert pin not in raw
    assert store.normalize_pin(pin) not in raw


def test_pin_hash_is_scrypt_not_sha256(store):
    """저엔트로피 값이라 빠른 해시면 오프라인 무차별 대입의 출발점이 된다."""
    share, _ = store.create_share(3000, "앱", "pin", HOUR)
    assert store.get_share(3000)["pinHash"].startswith("scrypt$")


def test_pin_is_returned_only_at_creation(store):
    _, pin = store.create_share(3000, "앱", "pin", HOUR)
    got = store.get_share(3000)
    assert "pin" not in got
    assert got["pinHash"] != pin


def test_verify_pin_roundtrip(store):
    _, pin = store.create_share(3000, "앱", "pin", HOUR)
    share = store.get_share(3000)
    assert store.verify_pin(share, pin) is True
    assert store.verify_pin(share, "WRONGPIN") is False


def test_verify_pin_accepts_user_typed_forms(store):
    """폰에서 옮겨 적는 값이라 하이픈·소문자·공백을 받아야 한다."""
    _, pin = store.create_share(3000, "앱", "pin", HOUR)
    share = store.get_share(3000)
    assert store.verify_pin(share, store.format_pin(pin)) is True
    assert store.verify_pin(share, pin.lower()) is True
    assert store.verify_pin(share, f" {pin} ") is True


def test_verify_pin_without_hash_is_false(store):
    """public/session share에 PIN 검증을 걸면 통과가 아니라 거부여야 한다."""
    store.create_share(3000, "앱", "public", HOUR)
    assert store.verify_pin(store.get_share(3000), "") is False
    assert store.verify_pin(store.get_share(3000), "ANYTHING") is False


def test_generated_pin_uses_unambiguous_alphabet(store):
    for _ in range(20):
        pin = store.generate_pin()
        assert len(pin) == store.PIN_LENGTH
        assert set(pin) <= set(store.PIN_ALPHABET)
        assert not (set(pin) & set("ILOU")), "손으로 옮겨 적을 때 헷갈리는 글자"


# --- 재노출 = 새 자격 -------------------------------------------------------------


def test_recreate_after_remove_issues_new_share_id_and_pin(store):
    first, pin1 = store.create_share(3000, "앱", "pin", HOUR)
    assert store.remove_share(3000) is True
    assert store.get_share(3000) is None
    second, pin2 = store.create_share(3000, "앱", "pin", HOUR)
    assert second["shareId"] != first["shareId"]
    assert pin2 != pin1
    assert store.verify_pin(second, pin1) is False, "옛 PIN이 계속 먹으면 취소가 취소가 아니다"


def test_remove_unknown_port_is_false(store):
    assert store.remove_share(9999) is False


# --- 동시 쓰기 — flock이 실제로 update-lost를 막는지 --------------------------------


def test_concurrent_creates_do_not_lose_writes(store):
    """여러 스레드가 동시에 서로 다른 포트를 create_share하면, read-modify-write가
    겹쳐도(파일을 읽어서 통째로 다시 쓰는 구조라 경쟁이 실재한다) 락 없이는 뒤에
    쓴 쪽이 앞선 쓰기를 덮어써 일부가 사라진다. `_locked()`의 flock이 그걸 막는지
    N개 동시 생성 후 전부 살아있는지로 확인한다."""
    N = 20
    barrier = threading.Barrier(N)
    errors = []

    def _create(i):
        try:
            barrier.wait(timeout=5)  # 최대한 같은 순간에 파일을 두드리게 한다
            store.create_share(3000 + i, f"앱{i}", "public", HOUR)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_create, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    shares = store.list_shares()
    assert len(shares) == N, "일부가 유실됐다 — 락이 read-modify-write 경쟁을 못 막았다"
    assert len({s["port"] for s in shares}) == N
    assert len({s["proxyPort"] for s in shares}) == N, "동시 할당에서 proxyPort가 겹쳤다"


# --- proxyPort: 한 번 할당하면 고정 ------------------------------------------------


def test_proxy_ports_are_distinct_per_port(store):
    a, _ = store.create_share(3000, "a", "public", HOUR)
    b, _ = store.create_share(4000, "b", "public", HOUR)
    assert a["proxyPort"] != b["proxyPort"]
    assert a["proxyPort"] >= store.PROXY_PORT_BASE


def test_recreate_keeps_proxy_port(store):
    """살아남은 cloudflared가 재기동된 프록시를 같은 주소에서 찾아야 한다."""
    first, _ = store.create_share(3000, "앱", "public", HOUR)
    second, _ = store.create_share(3000, "앱", "pin", HOUR)
    assert second["proxyPort"] == first["proxyPort"]


def test_proxy_port_survives_reload(store, tmp_path, monkeypatch):
    share, _ = store.create_share(3000, "앱", "session", HOUR)
    import port_share_store
    importlib.reload(port_share_store)
    assert port_share_store.get_share(3000)["proxyPort"] == share["proxyPort"]


def test_proxy_port_range_exhaustion_raises(store, monkeypatch):
    monkeypatch.setattr(store, "PROXY_PORT_SPAN", 2)
    store.create_share(3000, "a", "public", HOUR)
    store.create_share(3001, "b", "public", HOUR)
    with pytest.raises(RuntimeError):
        store.create_share(3002, "c", "public", HOUR)


# --- fail-closed ----------------------------------------------------------------


def test_unknown_mode_is_rejected(store):
    """public으로 폴백하지 않는다 — 모르는 값은 거부다."""
    with pytest.raises(ValueError):
        store.create_share(3000, "앱", "anyone", HOUR)
    with pytest.raises(ValueError):
        store.set_mode(3000, "anyone")


def test_bad_ttl_is_rejected(store):
    with pytest.raises(ValueError):
        store.create_share(3000, "앱", "public", 60)


def test_expired_share_is_invisible(store):
    store.create_share(3000, "앱", "pin", HOUR)
    store.update_share(3000, exp=time.time() - 1)
    assert store.get_share(3000) is None
    assert store.list_shares() == []
    assert len(store.list_shares(include_expired=True)) == 1


def test_corrupt_file_fails_closed(store, tmp_path):
    """읽기에 실패하면 '정책 없음'이 아니라 share가 없는 상태 → 프록시가 404를 낸다."""
    store.create_share(3000, "앱", "pin", HOUR)
    (tmp_path / "port_shares.json").write_text("{ not json", encoding="utf-8")
    assert store.get_share(3000) is None
    assert store.list_shares() == []


def test_records_without_required_fields_are_dropped(store, tmp_path):
    (tmp_path / "port_shares.json").write_text(
        json.dumps({"version": 1, "shares": [{"port": "3000"}, {"shareId": "x"}, "junk"]}),
        encoding="utf-8",
    )
    assert store.list_shares(include_expired=True) == []


# --- 모드·PIN 변경은 터널을 안 건드린다 --------------------------------------------


def test_set_mode_keeps_share_id_and_proxy_port(store):
    """공개 URL이 살아남는 근거 — 이게 깨지면 모드 변경마다 주소가 죽는다."""
    first, _ = store.create_share(3000, "앱", "public", HOUR)
    changed = store.set_mode(3000, "session")
    assert changed["mode"] == "session"
    assert changed["shareId"] == first["shareId"]
    assert changed["proxyPort"] == first["proxyPort"]


def test_regenerate_pin_keeps_share_id_and_invalidates_old(store):
    first, old = store.create_share(3000, "앱", "pin", HOUR)
    share, new = store.regenerate_pin(3000)
    assert new != old
    assert share["shareId"] == first["shareId"]
    assert share["proxyPort"] == first["proxyPort"]
    assert store.verify_pin(store.get_share(3000), old) is False
    assert store.verify_pin(store.get_share(3000), new) is True


def test_regenerate_pin_for_unknown_port_is_none(store):
    assert store.regenerate_pin(9999) is None
    assert store.set_mode(9999, "public") is None


# --- 쿠키 수명 -------------------------------------------------------------------


def test_cookie_ttl_is_capped(store):
    store.create_share(3000, "앱", "session", 30 * 86400)
    assert store.cookie_ttl(store.get_share(3000)) == store.COOKIE_MAX_TTL


def test_cookie_ttl_follows_short_share(store):
    store.create_share(3000, "앱", "session", HOUR)
    ttl = store.cookie_ttl(store.get_share(3000))
    assert 0 < ttl <= HOUR, "share보다 오래 사는 쿠키는 없다"


def test_cookie_ttl_of_expired_share_is_zero(store):
    store.create_share(3000, "앱", "session", HOUR)
    store.update_share(3000, exp=time.time() - 1)
    expired = store.list_shares(include_expired=True)[0]
    assert store.cookie_ttl(expired) == 0


# --- API 노출 형태 ---------------------------------------------------------------


def test_public_view_drops_pin_hash(store):
    """목록 API는 승격을 요구하지 않는다 — 해시가 실리면 그 자체가 우회로다."""
    store.create_share(3000, "앱", "pin", HOUR)
    view = store.public_view(store.get_share(3000))
    assert "pinHash" not in view
    assert view["hasPin"] is True
    assert view["shareId"]


def test_public_view_reports_no_pin_for_other_modes(store):
    store.create_share(3000, "앱", "session", HOUR)
    assert store.public_view(store.get_share(3000))["hasPin"] is False


# --- 조회수 ----------------------------------------------------------------------


def test_note_view_increments(store):
    store.create_share(3000, "앱", "session", HOUR)
    store.note_view(3000)
    store.note_view(3000)
    got = store.get_share(3000)
    assert got["views"] == 2
    assert got["lastAccess"] is not None


def test_note_view_on_unknown_port_is_noop(store):
    store.note_view(9999)  # 예외가 나면 프록시 경로가 통째로 깨진다
