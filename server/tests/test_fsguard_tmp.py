"""/tmp 열람 규칙: 내 소유 + 다른 사용자 읽기 가능한 일반 파일·폴더만, 읽기 전용."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

import fsguard


@pytest.fixture
def tmpbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    monkeypatch.delenv("VT_BROWSE_TMP", raising=False)
    d = Path(tempfile.mkdtemp(prefix="fsh-tmptest-", dir="/tmp"))
    os.chmod(d, 0o755)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_owned_world_readable_file_allowed(tmpbox):
    f = tmpbox / "poster.png"
    f.write_bytes(b"x")
    os.chmod(f, 0o644)
    assert fsguard.resolve_under_roots(str(f)) == f.resolve()


def test_nested_new_folder_needs_no_registration(tmpbox):
    (tmpbox / "a" / "b").mkdir(parents=True)
    os.chmod(tmpbox / "a", 0o755)
    os.chmod(tmpbox / "a" / "b", 0o755)
    f = tmpbox / "a" / "b" / "x.txt"
    f.write_text("hi")
    os.chmod(f, 0o644)
    assert fsguard.resolve_under_roots(str(f)).name == "x.txt"


def test_private_file_denied(tmpbox):
    f = tmpbox / "tok.txt"
    f.write_text("s")
    os.chmod(f, 0o600)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(f))


def test_private_dir_denied(tmpbox):
    d = tmpbox / "priv"
    d.mkdir()
    f = d / "x.txt"
    f.write_text("s")
    os.chmod(f, 0o644)
    os.chmod(d, 0o700)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(f))


def test_symlink_escape_denied(tmpbox, tmp_path):
    link = tmpbox / "link"
    link.symlink_to("/etc/hosts")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(link))


def test_symlink_to_private_denied(tmpbox):
    secret = tmpbox / "s.txt"
    secret.write_text("s")
    os.chmod(secret, 0o600)
    link = tmpbox / "l.txt"
    link.symlink_to(secret)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(link))


def test_fifo_denied(tmpbox):
    fifo = tmpbox / "pipe"
    os.mkfifo(fifo, 0o644)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(fifo))


def test_denylist_still_applies(tmpbox):
    f = tmpbox / ".env"
    f.write_text("A=1")
    os.chmod(f, 0o644)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(f))


def test_hidden_state_dir_denied(tmpbox):
    d = tmpbox / ".sometool"
    d.mkdir()
    os.chmod(d, 0o755)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(d))


@pytest.mark.parametrize("name", ["vt-pids", "claude-501", "tmux-501", "pytest-of-alice"])
def test_internal_state_folders_denied(name):
    root = fsguard.tmp_root()
    assert root is not None
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(root / name / "x"))


def test_switch_off(tmpbox, monkeypatch):
    f = tmpbox / "a.txt"
    f.write_text("a")
    os.chmod(f, 0o644)
    monkeypatch.setenv("VT_BROWSE_TMP", "0")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(f))


def test_other_tmp_sibling_prefix_not_matched(tmpbox):
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots("/tmpx/a")


def test_list_dir_hides_private(tmpbox):
    from routes import files
    (tmpbox / "ok.txt").write_text("a")
    os.chmod(tmpbox / "ok.txt", 0o644)
    (tmpbox / "sec.txt").write_text("a")
    os.chmod(tmpbox / "sec.txt", 0o600)
    names = {e["name"] for e in files._list_dir(tmpbox.resolve())["entries"]}
    assert names == {"ok.txt"}
