"""2.2 — port_share_store CLI.

`fsh tunnel expose`가 서버 프로세스 없이 정책을 직접 쓰기 위해 부르는 진입점.
CLI는 auth의 otp/device 명령과 같은 신뢰 등급(로컬 셸)이라 승격을 요구하지
않는다 — HTTP 레이어(elevated_router)와는 다른 경계다.
"""

from __future__ import annotations

import importlib
import json

import pytest

HOUR = "3600"


@pytest.fixture
def store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7900")
    import port_share_store
    importlib.reload(port_share_store)
    return port_share_store


def _out(capsys):
    return json.loads(capsys.readouterr().out.strip())


def test_cli_create_prints_pin_once(store, capsys):
    rc = store._cli(["cli-create", "3000", "앱", "pin", HOUR])
    assert rc == 0
    body = _out(capsys)
    assert "-" in body["pin"]
    assert "pinHash" not in body
    assert body["proxyPort"] >= store.PROXY_PORT_BASE


def test_cli_create_public_has_no_pin_key_value(store, capsys):
    store._cli(["cli-create", "3000", "앱", "public", HOUR])
    body = _out(capsys)
    assert "pin" not in body


def test_cli_create_bad_mode_exits_nonzero(store, capsys):
    rc = store._cli(["cli-create", "3000", "앱", "anyone", HOUR])
    assert rc == 1


def test_cli_remove_roundtrip(store, capsys):
    store._cli(["cli-create", "3000", "앱", "public", HOUR])
    capsys.readouterr()
    rc = store._cli(["cli-remove", "3000"])
    assert rc == 0
    assert _out(capsys)["ok"] is True
    assert store.get_share(3000) is None


def test_cli_remove_unknown_port_is_nonzero(store, capsys):
    assert store._cli(["cli-remove", "9999"]) == 1


def test_cli_regenerate_pin(store, capsys):
    store._cli(["cli-create", "3000", "앱", "pin", HOUR])
    first = _out(capsys)["pin"]
    rc = store._cli(["cli-regenerate-pin", "3000"])
    assert rc == 0
    second = _out(capsys)["pin"]
    assert second != first


def test_cli_regenerate_pin_unknown_port(store, capsys):
    assert store._cli(["cli-regenerate-pin", "9999"]) == 1


def test_cli_set_mode_rotates_share_id(store, capsys):
    store._cli(["cli-create", "3000", "앱", "public", HOUR])
    before = json.loads(capsys.readouterr().out)["shareId"]
    store._cli(["cli-set-mode", "3000", "session"])
    after = _out(capsys)
    assert after["mode"] == "session"
    assert after["shareId"] != before


def test_cli_set_mode_to_pin_generates_pin(store, capsys):
    """PIN 없이 pin 모드가 되면 아무도 못 들어온다."""
    store._cli(["cli-create", "3000", "앱", "public", HOUR])
    capsys.readouterr()
    store._cli(["cli-set-mode", "3000", "pin"])
    body = _out(capsys)
    assert body["pin"]
    assert store.get_share(3000)["pinHash"]


def test_cli_set_mode_unknown_port(store, capsys):
    assert store._cli(["cli-set-mode", "9999", "public"]) == 1


def test_cli_list_outputs_tsv(store, capsys):
    store._cli(["cli-create", "3000", "a", "public", HOUR])
    capsys.readouterr()
    store._cli(["cli-create", "4000", "b", "pin", HOUR])
    capsys.readouterr()
    store._cli(["cli-list"])
    lines = capsys.readouterr().out.strip().splitlines()
    # 열: 포트 · 모드 · broken · PIN 실패 누적 · 자동 재노출
    assert sorted(lines) == ["3000\tpublic\t0\t0\t0", "4000\tpin\t0\t0\t0"]


def test_cli_unknown_command(store, capsys):
    assert store._cli(["nope"]) == 2
