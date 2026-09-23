"""auth/fileio._write_json_secure — 동시 쓰기에도 파일이 깨지지 않는다.

예전에는 임시파일 이름이 `<이름>.tmp`로 **고정**이었다. 같은 경로에 쓰기 둘이
겹치면 둘이 같은 파일을 `O_TRUNC`로 열고 번갈아 쓴 뒤, 먼저 끝난 쪽이
`os.replace`로 **반쯤 섞인 내용**을 제자리에 놓았다. `os.replace`가 원자적인
것은 맞지만 옮기는 내용물이 이미 오염돼 있으면 소용이 없다.

여기 쓰는 쪽은 기기 목록(`auth/devices.py`)과 TOTP(`auth/totp.py`)다 —
**깨지면 로그인이 막힌다.**
"""

from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from auth.fileio import _write_json_secure

# 스레드 전환이 쓰기 중간에 끼어들 만큼 크게. 작은 payload는 한 번에 flush돼
# 예전 코드로도 우연히 통과할 수 있다.
_PAYLOAD_KEYS = 400


def _payload(tag: str) -> dict:
    return {"who": tag, "rows": [{"i": i, "tag": tag * 8} for i in range(_PAYLOAD_KEYS)]}


def test_concurrent_writes_never_produce_a_mixed_file(tmp_path):
    """N개가 동시에 써도 최종 파일은 **그중 정확히 하나**와 일치한다."""
    path = tmp_path / "devices.json"
    tags = [f"w{i}" for i in range(8)]
    errors = []

    def write(tag):
        try:
            for _ in range(10):
                _write_json_secure(path, _payload(tag))
        except BaseException as e:  # noqa: BLE001 — 스레드 예외를 본체로 올린다
            errors.append(e)

    threads = [threading.Thread(target=write, args=(t,)) for t in tags]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"쓰기가 예외를 냈다: {errors}"
    data = json.loads(path.read_text(encoding="utf-8"))   # 깨졌으면 여기서 터진다
    assert data["who"] in tags
    assert data == _payload(data["who"]), "서로 다른 writer의 내용이 섞였다"


def test_no_temp_files_left_behind(tmp_path):
    path = tmp_path / "devices.json"
    for i in range(5):
        _write_json_secure(path, {"n": i})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != path.name]
    assert leftovers == [], f"임시파일이 남았다: {leftovers}"


def test_permissions_are_locked_down(tmp_path):
    """0600 파일 / 0700 디렉토리 — 기기 목록과 TOTP 시드가 여기 들어간다."""
    d = tmp_path / "vt"
    path = d / "totp.json"
    _write_json_secure(path, {"secret": "s"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_failed_write_leaves_the_original_untouched(tmp_path):
    """직렬화가 중간에 터져도 원본은 그대로고 조각도 안 남는다."""
    path = tmp_path / "devices.json"
    _write_json_secure(path, {"version": 1, "devices": {"a": 1}})
    before = path.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        # set은 JSON으로 직렬화할 수 없다. 앞쪽 키를 먼저 쓰다가 터지므로
        # 임시파일에 **부분 기록이 남은 상태**로 예외가 나간다.
        _write_json_secure(path, {"version": 1, "devices": {"a": 1}, "bad": {1, 2}})

    assert path.read_text(encoding="utf-8") == before, "실패한 쓰기가 원본을 건드렸다"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != path.name]
    assert leftovers == [], f"실패 경로가 조각을 남겼다: {leftovers}"


def test_write_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "devices.json"
    _write_json_secure(path, {"ok": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
