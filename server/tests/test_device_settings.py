"""N3(60-settings-palette.md §1) — GET/PUT /api/device-settings + device_settings.py.

/api/workspace(전역)와 완전히 분리된 저장소인지, 쿠키별로 파일이 갈리는지,
0600으로 저장되는지, 파일명 인젝션이 막히는지를 확인한다.
"""

import stat

import pytest
from starlette.testclient import TestClient

import auth
import device_settings
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "STATE_DIR", tmp_path / ".vt")
    monkeypatch.setattr(auth, "DEVICES_PATH", tmp_path / ".vt" / "devices.json")
    monkeypatch.setattr(device_settings, "DIR", tmp_path / ".vt" / "device-settings")
    with TestClient(main.app) as c:
        yield c


def test_no_cookie_falls_back_to_local(client):
    r = client.get("/api/device-settings")
    assert r.status_code == 200
    assert r.json()["device_id"] == "local"
    assert r.json()["settings"] == {}


def test_put_then_get_roundtrips(client):
    r = client.put("/api/device-settings", json={"settings": {"keybar.collapsed": True}})
    assert r.status_code == 200
    assert r.json()["settings"] == {"keybar.collapsed": True}

    r2 = client.get("/api/device-settings")
    assert r2.json()["settings"] == {"keybar.collapsed": True}


def test_put_merges_shallowly_does_not_drop_other_keys(client):
    client.put("/api/device-settings", json={"settings": {"a": 1}})
    r = client.put("/api/device-settings", json={"settings": {"b": 2}})
    assert r.json()["settings"] == {"a": 1, "b": 2}


def test_put_rejects_non_object_settings(client):
    r = client.put("/api/device-settings", json={"settings": "nope"})
    assert r.status_code == 400


def test_different_devices_get_different_files_and_dont_see_each_others_values(client):
    secret_a, id_a = auth.register_device("A")
    secret_b, id_b = auth.register_device("B")
    assert id_a != id_b

    client.cookies.set("vt_device", secret_a)
    client.put("/api/device-settings", json={"settings": {"x": "a"}})

    client.cookies.set("vt_device", secret_b)
    r = client.get("/api/device-settings")
    assert r.json()["device_id"] == id_b
    assert r.json()["settings"] == {}   # B는 A가 저장한 값을 못 본다

    client.cookies.set("vt_device", secret_a)
    r = client.get("/api/device-settings")
    assert r.json()["device_id"] == id_a
    assert r.json()["settings"] == {"x": "a"}


def test_unknown_device_cookie_falls_back_to_local(client):
    client.cookies.set("vt_device", "not-a-real-secret")
    r = client.get("/api/device-settings")
    assert r.json()["device_id"] == "local"


def test_file_is_0600(client, tmp_path):
    client.put("/api/device-settings", json={"settings": {"x": 1}})
    f = tmp_path / ".vt" / "device-settings" / "local.json"
    assert f.exists()
    mode = stat.S_IMODE(f.stat().st_mode)
    assert mode == 0o600, oct(mode)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "x" * 40, "UPPER"])
def test_device_id_for_rejects_unsafe_ids_and_falls_back(bad, monkeypatch):
    # verify_device()가 잘못된 형태의 id를 돌려줄 리는 없지만(sha256 앞 16자
    # 고정), 혹시라도 그런 값이 오면 파일명 인젝션으로 이어지지 않아야 한다.
    monkeypatch.setattr(auth, "verify_device", lambda secret: {"id": bad})
    assert device_settings.device_id_for("whatever") == "local"
