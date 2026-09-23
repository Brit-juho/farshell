"""ADR-43(2.3.0) — 스크롤백 영속화 제거. `docs/plan-2.3/87-remove-scrollback-persist.md`.

기능 자체(`server/scrollback_persist.py`)는 삭제됐다. 여기서 확인하는 것은
남은 자취 둘이다 — 1회성 정리 함수(`main._purge_legacy_scrollback`)의 안전성과,
정말로 코드 어디에도 옛 모듈을 참조하는 곳이 없는지.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import main

SERVER = Path(main.__file__).resolve().parent
REPO_ROOT = SERVER.parent


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    return tmp_path


def test_e1_purges_the_scrollback_subdirectory(tmp_path):
    d = tmp_path / "scrollback"
    d.mkdir()
    (d / "bench.log").write_bytes(b"x" * 100)
    (d / "old.log.1").write_bytes(b"y" * 50)

    main._purge_legacy_scrollback()

    assert not d.exists()


def test_e1_is_a_noop_when_nothing_to_purge(tmp_path):
    """디렉터리가 아예 없으면 조용히 아무 일도 안 한다 — mkdir하지 않는다."""
    d = tmp_path / "scrollback"
    main._purge_legacy_scrollback()
    assert not d.exists()


def test_e2_permission_error_does_not_raise(tmp_path, monkeypatch):
    """정리가 실패해도(권한 등) 서버 기동을 막으면 안 된다 — 경고만 남긴다."""
    d = tmp_path / "scrollback"
    d.mkdir()
    (d / "bench.log").write_bytes(b"x")

    def _boom(path):
        raise OSError("permission denied (simulated)")

    monkeypatch.setattr(main.shutil, "rmtree", _boom)
    main._purge_legacy_scrollback()  # 예외가 밖으로 안 나가야 한다


def test_e3_only_the_scrollback_subdirectory_is_removed(tmp_path):
    """상태 디렉터리 자체와 형제 파일(queue.json 등)은 그대로다."""
    (tmp_path / "scrollback").mkdir()
    (tmp_path / "scrollback" / "x.log").write_bytes(b"x")
    sibling = tmp_path / "queue.json"
    sibling.write_text("{}")

    main._purge_legacy_scrollback()

    assert not (tmp_path / "scrollback").exists()
    assert tmp_path.is_dir()
    assert sibling.is_file()
    assert sibling.read_text() == "{}"


def test_grep_finds_no_remaining_references_to_the_removed_module():
    """4번 검증 항목 — `grep -rn "scrollback_persist" server frontend` → 0건.

    소스(.py/.js/.ts/.tsx)만 본다 — `__pycache__`의 컴파일 산물은 옛 코드를
    지운 뒤에도 다음 빌드 전까지 남아 있을 수 있어 오탐이 된다.
    """
    out = subprocess.run(
        ["grep", "-rln", "--include=*.py", "--include=*.js",
         "--include=*.ts", "--include=*.tsx", "scrollback_persist",
         str(REPO_ROOT / "server"), str(REPO_ROOT / "frontend")],
        capture_output=True, text=True,
    )
    hits = [
        line for line in out.stdout.splitlines()
        if "test_scrollback_removal.py" not in line  # 이 파일 자신의 언급 제외
    ]
    assert hits == [], f"옛 모듈을 여전히 참조하는 파일이 있다: {hits}"


def test_scrollback_module_file_is_gone():
    assert not (SERVER / "scrollback_persist.py").exists()


def test_purge_runs_during_real_server_startup(tmp_path):
    """단위 호출이 아니라 실제 lifespan을 통해서도 정리가 도는지 — 함수만
    옳고 배선이 빠지는 실수를 잡는다."""
    from starlette.testclient import TestClient

    d = tmp_path / "scrollback"
    d.mkdir()
    (d / "leftover.log").write_bytes(b"x" * 10)

    with TestClient(main.app):
        pass

    assert not d.exists()
