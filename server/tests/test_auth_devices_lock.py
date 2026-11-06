"""devices.json 동시 수정 — `last_seen` 갱신(스레드)이 폐기·등록(루프)을 덮어쓰지 않는다.

공유 링크 라우트가 `auth.verify_device`를 `asyncio.to_thread`로 부르면서, 하루 단위 `last_seen` 갱신이 루프의
`revoke_device`/`register_device`와 겹칠 수 있게 됐다. 락 없이 "읽기 → (그 사이 폐기) → 읽어 둔 목록 저장"이면
폐기한 기기가 되살아나고, "읽기 → (그 사이 등록) → 저장"이면 막 등록한 기기가 사라진다.
"""

import importlib
import threading

import pytest

import auth as auth_mod
from auth import devices


@pytest.fixture
def auth(tmp_path, monkeypatch):
    importlib.reload(auth_mod)
    importlib.reload(devices)
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(auth_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(auth_mod, "DEVICES_PATH", state_dir / "devices.json")
    yield auth_mod


def _stale(auth, device_id):
    """`last_seen`을 하루 넘게 전으로 돌려 다음 verify_device가 저장하게 만든다."""
    ds = devices._load_devices()   # 준비 단계 — 경쟁 상대가 아직 없다
    for d in ds:
        if d["id"] == device_id:
            d["last_seen"] = 1
    devices._save_devices(ds)


def _pause_first_read_in(thread_name, monkeypatch):
    """`thread_name` 스레드가 처음 devices.json을 읽은 **직후** 멈춘다 — 그 틈에 다른 쪽이 고친다."""
    loaded, resume = threading.Event(), threading.Event()
    real = devices._load_devices
    seen = []

    def load():
        out = real()
        if threading.current_thread().name == thread_name and not seen:
            seen.append(1)
            loaded.set()
            assert resume.wait(5)
        return out

    monkeypatch.setattr(devices, "_load_devices", load)
    return loaded, resume


def _verify_in_thread(secret, result):
    t = threading.Thread(target=lambda: result.append(devices.verify_device(secret)), name="verifier")
    t.start()
    return t


def test_last_seen_update_does_not_resurrect_a_revoked_device(auth, monkeypatch):
    secret, device_id = devices.register_device("phone")
    _stale(auth, device_id)
    loaded, resume = _pause_first_read_in("verifier", monkeypatch)
    result = []
    t = _verify_in_thread(secret, result)
    assert loaded.wait(5)
    assert [d["id"] for d in devices.revoke_device(device_id)] == [device_id]   # 루프 쪽 폐기
    resume.set()
    t.join(5)
    assert [d["id"] for d in devices.list_devices()] == [], "폐기한 기기가 last_seen 저장으로 되살아났다"
    assert result == [None]
    assert devices.verify_device(secret) is None


def test_last_seen_update_does_not_drop_a_device_registered_meanwhile(auth, monkeypatch):
    secret, old_id = devices.register_device("phone")
    _stale(auth, old_id)
    loaded, resume = _pause_first_read_in("verifier", monkeypatch)
    result = []
    t = _verify_in_thread(secret, result)
    assert loaded.wait(5)
    _, new_id = devices.register_device("laptop")
    resume.set()
    t.join(5)
    ids = {d["id"] for d in devices.list_devices()}
    assert ids == {old_id, new_id}, "그 사이 등록한 기기가 사라졌다"
    assert result and result[0]["id"] == old_id and result[0]["last_seen"] > 1


def test_many_threads_updating_last_seen_while_revoking_and_registering(auth, monkeypatch):
    """실제 스레드 경쟁: 매 검증이 저장하게 만들고 폐기·등록을 섞는다. 끝 상태가 정확해야 한다."""
    monkeypatch.setattr(devices, "_SEEN_INTERVAL", -1)
    keep_secret, keep_id = devices.register_device("keep")
    doomed = [devices.register_device(f"doomed-{i}") for i in range(5)]
    stop = threading.Event()
    errors = []

    def hammer(secret):
        while not stop.is_set():
            try:
                devices.verify_device(secret)
            except Exception as e:   # noqa: BLE001 — 스레드 예외를 테스트로 올린다
                errors.append(e)

    workers = [threading.Thread(target=hammer, args=(s,)) for s, _ in [(keep_secret, keep_id), *doomed]]
    for w in workers:
        w.start()
    added = []
    try:
        for _, did in doomed:
            devices.revoke_device(did)
            added.append(devices.register_device("new")[1])
    finally:
        stop.set()
        for w in workers:
            w.join(10)
    assert errors == []
    assert {d["id"] for d in devices.list_devices()} == {keep_id, *added}
    for s, _ in doomed:
        assert devices.verify_device(s) is None
