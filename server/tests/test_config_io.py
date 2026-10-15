"""남의 설정 파일 쓰기(config_io) — 지켜야 할 계약은 «안 망가뜨리는 것»이다.

모킹 없이 실제 파일·링크·권한으로 재현한다.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

import claude_hooks
import codex_hooks
import config_io


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vtstate"))


def _tmp_files(d):
    return [p.name for p in d.iterdir() if p.name.endswith(".vt-tmp")]


# ---------------------------------------------------------------- 심볼릭 링크

def test_write_through_symlink_keeps_the_link(tmp_path):
    real = tmp_path / "shared" / "real.json"
    real.parent.mkdir()
    real.write_text("{}")
    link = tmp_path / "acct" / "link.json"
    link.parent.mkdir()
    link.symlink_to(real)
    config_io.atomic_write(link, '{"a": 1}')
    assert link.is_symlink()
    assert real.read_text() == '{"a": 1}'


def test_relative_link_and_link_chain(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("old")
    (tmp_path / "mid.json").symlink_to("real.json")
    (tmp_path / "top.json").symlink_to("mid.json")
    config_io.atomic_write(tmp_path / "top.json", "new")
    assert (tmp_path / "top.json").is_symlink() and (tmp_path / "mid.json").is_symlink()
    assert real.read_text() == "new"


def test_dangling_link_creates_the_target(tmp_path):
    target = tmp_path / "later" / "real.json"
    target.parent.mkdir()
    link = tmp_path / "link.json"
    link.symlink_to(target)
    config_io.atomic_write(link, "x")
    assert link.is_symlink() and target.read_text() == "x"


def test_symlink_loop_is_refused(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.symlink_to(b)
    b.symlink_to(a)
    with pytest.raises(config_io.ConfigWriteRefused):
        config_io.atomic_write(a, "x")
    assert a.is_symlink() and b.is_symlink()


# ---------------------------------------------------------------- 권한

@pytest.mark.parametrize("mode", [0o644, 0o600, 0o664])
def test_mode_is_inherited(tmp_path, mode):
    p = tmp_path / "f.json"
    p.write_text("{}")
    os.chmod(p, mode)
    config_io.atomic_write(p, "{}\n")
    assert (p.stat().st_mode & 0o777) == mode


def test_new_file_uses_default_mode(tmp_path):
    p = tmp_path / "sub" / "new.json"
    config_io.atomic_write(p, "{}")
    assert (p.stat().st_mode & 0o777) == 0o600
    q = tmp_path / "new2.json"
    config_io.atomic_write(q, "{}", default_mode=0o644)
    assert (q.stat().st_mode & 0o777) == 0o644


# ---------------------------------------------------------------- 하드링크

def test_hardlink_is_refused_and_both_names_are_untouched(tmp_path):
    a = tmp_path / "a.json"
    a.write_text("original")
    b = tmp_path / "b.json"
    os.link(a, b)
    with pytest.raises(config_io.ConfigWriteRefused):
        config_io.atomic_write(a, "changed")
    assert a.read_bytes() == b"original" and b.read_bytes() == b"original"
    assert _tmp_files(tmp_path) == []


def test_directory_target_is_refused(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(config_io.ConfigWriteRefused):
        config_io.atomic_write(d, "x")


# ---------------------------------------------------------------- 락

def test_lock_name_follows_the_real_file(tmp_path):
    """링크 경로와 실제 경로로 연 락이 서로를 막는다(같은 락 파일)."""
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    got = []

    def other():
        try:
            with config_io.locked(link, deadline=0.2):
                got.append("acquired")
        except Exception as e:  # noqa: BLE001
            got.append(type(e).__name__)

    with config_io.locked(real):
        t = threading.Thread(target=other)
        t.start()
        t.join(3)
    assert got == ["LockTimeout"]


def test_different_files_do_not_block_each_other(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    got = []
    with config_io.locked(a):
        t = threading.Thread(target=lambda: (config_io.locked(b).__enter__(), got.append(1)))
        t.start()
        t.join(3)
    assert got == [1]


# ---------------------------------------------------------------- 충돌 재확인

def test_ensure_unchanged_detects_a_change(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("{}")
    s = config_io.stamp(p)
    config_io.ensure_unchanged(p, s)
    time.sleep(0.01)
    p.write_text('{"x": 1}')
    with pytest.raises(config_io.ConfigWriteRefused):
        config_io.ensure_unchanged(p, s)


def test_stamp_of_missing_file_is_none(tmp_path):
    assert config_io.stamp(tmp_path / "nope.json") is None
    config_io.ensure_unchanged(tmp_path / "nope.json", None)


# ---------------------------------------------------------------- 임시 파일

def test_failed_replace_leaves_no_temp_and_original_intact(tmp_path, monkeypatch):
    p = tmp_path / "f.json"
    p.write_text("original")

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(config_io.os, "replace", boom)
    with pytest.raises(OSError):
        config_io.atomic_write(p, "new")
    assert p.read_text() == "original"
    assert _tmp_files(tmp_path) == []


def test_only_stale_temp_files_are_removed(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("{}")
    stale = tmp_path / ".f.json.dead.vt-tmp"
    fresh = tmp_path / ".f.json.live.vt-tmp"
    other = tmp_path / ".g.json.dead.vt-tmp"
    for f in (stale, fresh, other):
        f.write_text("x")
    old = time.time() - config_io.STALE_TEMP_SEC - 60
    os.utime(stale, (old, old))
    os.utime(other, (old, old))
    config_io.atomic_write(p, "{}\n")
    assert not stale.exists()
    assert fresh.exists()          # 다른 프로세스가 쓰는 중일 수 있다
    assert other.exists()          # 다른 파일의 것은 건드리지 않는다


# ---------------------------------------------------------------- 훅 설치 통합

def _claude_env(tmp_path, monkeypatch):
    cfg = tmp_path / "claude"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return cfg / "settings.json"


def test_claude_hook_install_keeps_symlink_and_mode(tmp_path, monkeypatch, capsys):
    path = _claude_env(tmp_path, monkeypatch)
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"theme": "dark"}))
    os.chmod(real, 0o644)
    path.symlink_to(real)

    assert claude_hooks._cmd_install() == 0
    assert path.is_symlink()
    data = json.loads(real.read_text())
    assert data["theme"] == "dark" and "hooks" in data
    assert (real.stat().st_mode & 0o777) == 0o644
    # 백업은 실제 파일 옆에 남는다
    assert (tmp_path / "dotfiles" / "settings.json.vtbak").exists()

    assert claude_hooks._cmd_uninstall() == 0
    assert path.is_symlink()
    assert "hooks" not in json.loads(real.read_text()) or not json.loads(real.read_text())["hooks"]


def test_claude_write_refuses_when_file_changed_after_read(tmp_path, monkeypatch):
    path = _claude_env(tmp_path, monkeypatch)
    path.write_text("{}")
    stamp = config_io.stamp(path)
    time.sleep(0.01)
    path.write_text('{"edited": true}')          # 그 사이 Claude Code가 고쳤다
    with pytest.raises(config_io.ConfigWriteRefused):
        claude_hooks.write_settings(path, {"hooks": {}}, stamp)
    assert json.loads(path.read_text()) == {"edited": True}


def test_claude_install_reports_hardlink_without_touching_it(tmp_path, monkeypatch, capsys):
    path = _claude_env(tmp_path, monkeypatch)
    path.write_text("{}")
    other = tmp_path / "other.json"
    os.link(path, other)
    assert claude_hooks._cmd_install() == 2
    assert "하드링크" in capsys.readouterr().out
    assert path.read_text() == "{}" and other.read_text() == "{}"


def test_codex_install_keeps_symlink_and_mode(tmp_path, monkeypatch):
    cfg = tmp_path / "codex"
    cfg.mkdir()
    monkeypatch.setenv("VT_CODEX_HOME", str(cfg))
    real = tmp_path / "shared" / "config.toml"
    real.parent.mkdir()
    real.write_text('# keep me\nmodel = "x"\n')
    os.chmod(real, 0o644)
    (cfg / "config.toml").symlink_to(real)

    assert codex_hooks._cmd_install() == 0
    assert (cfg / "config.toml").is_symlink()
    text = real.read_text()
    assert "# keep me" in text and codex_hooks.BEGIN in text
    assert (real.stat().st_mode & 0o777) == 0o644
