"""workspace.save() — 락 + 원자쓰기.

이 파일은 **동시 쓰기가 예외가 아니라 존재 목적**이다(기기 둘이 같은 서버에
붙어 각자 설정을 바꾼다). 예전 `save()`는 `load()`와 `write_text()` 사이에
아무 보호가 없었다:

  - **lost update** — 두 기기가 서로 다른 키를 동시에 바꾸면 한쪽이 사라진다.
    얕은 병합 로직은 맞았지만 그 로직을 감싸는 락이 없었다.
  - **부분 기록 노출** — `write_text`는 원자적이지 않다. 쓰는 도중 읽으면 잘린
    JSON을 보고 `load()`의 `except`가 그걸 삼켜 **통째로 DEFAULT로 떨어진다.**
    그 직후 `save()`가 불리면 그 DEFAULT가 디스크에 굳는다 — 탭 순서·활성
    세션·모든 UI 설정이 한 번에 리셋된다.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time

import pytest

import store_lock
import workspace


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """실제 ~/.config/vt/workspace.json을 건드리지 않도록 격리."""
    path = tmp_path / "workspace.json"
    monkeypatch.setattr(workspace, "WS_PATH", path)
    return path


def test_concurrent_writers_do_not_lose_each_others_keys(ws):
    """N개 기기가 서로 다른 설정 키를 동시에 바꿔도 **전부 살아남는다.**"""
    keys = [f"terminal.k{i}" for i in range(12)]
    errors = []

    def write(key):
        try:
            for _ in range(6):
                workspace.save({"settings": {key: key}})
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=write, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"저장이 예외를 냈다: {errors}"
    settings = workspace.load()["settings"]
    missing = [k for k in keys if settings.get(k) != k]
    assert missing == [], f"동시 쓰기에 키가 유실됐다(lost update): {missing}"


def test_readers_never_see_the_defaults_while_a_write_is_in_flight(ws):
    """부분 기록을 읽고 DEFAULT로 떨어지는 일이 없어야 한다.

    DEFAULT로 떨어진 뒤 그대로 저장되면 사용자 설정이 통째로 날아간다 —
    이 테스트가 지키는 것은 그 연쇄의 첫 고리다.
    """
    workspace.save({"settings": {"seed": "v"}})
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            data = workspace.load()
            if data["settings"].get("seed") != "v":
                bad.append(data)
                return

    r = threading.Thread(target=reader)
    r.start()
    try:
        for i in range(120):
            workspace.save({"settings": {"seed": "v", "n": i}, "tabs": [{"id": f"t{i}"}]})
    finally:
        stop.set()
        r.join()

    assert bad == [], "쓰기 도중 읽은 쪽이 잘린 JSON을 보고 DEFAULT로 떨어졌다"


def test_no_temp_files_left_behind(ws):
    for i in range(5):
        workspace.save({"tabs": [{"id": f"t{i}"}]})
    leftovers = [p.name for p in ws.parent.iterdir()
                 if p.name not in (ws.name, ws.name + ".lock")]
    assert leftovers == [], f"임시파일이 남았다: {leftovers}"


def test_failed_write_leaves_the_original_untouched(ws):
    workspace.save({"settings": {"keep": "yes"}})
    before = ws.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        # set은 JSON으로 직렬화할 수 없다 — 쓰는 도중 터진다.
        workspace.save({"settings": {"bad": {1, 2}}})

    assert ws.read_text(encoding="utf-8") == before, "실패한 쓰기가 원본을 덮었다"
    assert workspace.load()["settings"]["keep"] == "yes"
    leftovers = [p.name for p in ws.parent.iterdir()
                 if p.name not in (ws.name, ws.name + ".lock")]
    assert leftovers == [], f"실패 경로가 조각을 남겼다: {leftovers}"


def test_lock_path_follows_the_isolated_workspace_path(ws):
    """락 경로를 하드코딩하면 격리 테스트 서버가 **실사용자의 락**을 잡는다.

    격리 서버가 실사용자 workspace.json을 건드린 전례가 있어서 세운 방어선이다.
    """
    workspace.save({"tabs": []})
    assert workspace._lock_path() == ws.with_name(ws.name + ".lock")
    assert workspace._lock_path().exists()
    assert workspace._lock_path().parent == ws.parent


def test_save_gives_up_instead_of_hanging_forever(ws, monkeypatch):
    """`save()`는 `async def` 라우트에서 동기로 불린다.

    무기한 대기면 상대가 멈췄을 때 **서버 전체**가 선다 — 터미널 WebSocket까지.
    요청 하나가 실패하는 쪽을 택했다.
    """
    monkeypatch.setattr(store_lock, "DEFAULT_DEADLINE", 0.2)
    ws.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(workspace._lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        start = time.monotonic()
        with pytest.raises(store_lock.LockTimeout):
            workspace.save({"tabs": []})
        elapsed = time.monotonic() - start
    finally:
        os.close(fd)
    assert elapsed < 1.0, f"기한 안에 포기해야 한다 (실측 {elapsed:.3f}초)"


def test_reads_do_not_take_the_lock(ws):
    """읽기에 락을 걸면 매 폴링이 쓰기와 경쟁한다. `os.replace`가 원자적이라
    독자는 락 없이도 옛/새 버전 중 하나를 온전히 본다."""
    workspace.save({"settings": {"a": 1}})
    fd = os.open(str(workspace._lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        start = time.monotonic()
        assert workspace.load()["settings"]["a"] == 1
        assert time.monotonic() - start < 0.5, "load()가 락을 기다렸다"
    finally:
        os.close(fd)


def test_update_returns_the_merged_state(ws):
    workspace.save({"settings": {"a": 1}})
    out = workspace.update({"settings": {"b": 2}})
    assert out["settings"] == {"a": 1, "b": 2}


def test_written_file_is_valid_json_with_a_trailing_read(ws):
    workspace.save({"ui": {"theme": "light"}, "tabs": [{"id": "t1"}]})
    raw = json.loads(ws.read_text(encoding="utf-8"))
    assert raw["ui"]["theme"] == "light"
    assert raw["tabs"] == [{"id": "t1"}]


# ── 2026-10-03 세션·뷰 규칙 2단계 — 브라우저 탭별 레이아웃 ──────────────────

def _snap(saved_at):
    return {"v": 5, "savedAt": saved_at, "tabs": [], "closed": []}


def test_screen_layouts_merge_per_screen_instead_of_replacing(ws):
    """한 브라우저 탭의 PUT이 다른 탭의 레이아웃을 지우면 안 된다(ui는 얕은 병합)."""
    workspace.update({"ui": {"layouts": {"a" * 32: _snap(1)}}})
    out = workspace.update({"ui": {"layouts": {"b" * 32: _snap(2)}, "layout": _snap(2)}})
    assert set(out["ui"]["layouts"]) == {"a" * 32, "b" * 32}
    assert out["ui"]["layout"]["savedAt"] == 2
    # 같은 탭의 재저장은 덮어쓴다.
    out = workspace.update({"ui": {"layouts": {"a" * 32: _snap(3)}}})
    assert out["ui"]["layouts"]["a" * 32]["savedAt"] == 3
    assert out["ui"]["layouts"]["b" * 32]["savedAt"] == 2


def test_screen_layouts_keep_other_ui_keys(ws):
    workspace.update({"ui": {"rail_width": 280}})
    out = workspace.update({"ui": {"layouts": {"s1": _snap(1)}}})
    assert out["ui"]["rail_width"] == 280
    assert out["ui"]["theme"] == "dark"


def test_screen_layouts_null_removes_and_bad_values_are_ignored(ws):
    workspace.update({"ui": {"layouts": {"s1": _snap(1), "s2": _snap(2)}}})
    out = workspace.update({"ui": {"layouts": {"s1": None, "s3": "not-a-dict", "": _snap(9)}}})
    assert set(out["ui"]["layouts"]) == {"s2"}


def test_screen_layouts_are_capped_by_most_recent_save(ws):
    for i in range(workspace.MAX_SCREEN_LAYOUTS + 5):
        workspace.update({"ui": {"layouts": {f"s{i}": _snap(i)}}})
    layouts = workspace.load()["ui"]["layouts"]
    assert len(layouts) == workspace.MAX_SCREEN_LAYOUTS
    assert "s0" not in layouts
    assert f"s{workspace.MAX_SCREEN_LAYOUTS + 4}" in layouts
