"""llama.cpp 배포본 — 바이너리 탐색 우선순위, 자산 선택, 검증 설치(네트워크 없이)."""
import hashlib
import io
import os
import tarfile

import pytest

import vt_paths
from stt_engines import llama_release as lr
from stt_engines.env import HostEnv


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(vt_paths, "state_dir", lambda: state)
    monkeypatch.delenv("VT_LLAMA_SERVER", raising=False)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))          # PATH의 진짜 llama-server 차단
    return state


def _exe(path, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


# ── find_binary ────────────────────────────────────────────────────
def test_find_none():
    assert lr.find_binary() is None


def test_find_path(tmp_path, monkeypatch):
    b = _exe(tmp_path / "pathbin" / "llama-server")
    monkeypatch.setenv("PATH", str(b.parent))
    assert lr.find_binary() == b


def test_find_managed_beats_path(tmp_path, monkeypatch):
    p = _exe(tmp_path / "pathbin" / "llama-server")
    monkeypatch.setenv("PATH", str(p.parent))
    m = _exe(lr.install_dir() / "sub" / "llama-server")
    assert lr.find_binary() == m


def test_find_env_beats_all(tmp_path, monkeypatch):
    _exe(lr.install_dir() / "llama-server")
    mine = _exe(tmp_path / "mine" / "ls")
    monkeypatch.setenv("VT_LLAMA_SERVER", str(mine))
    assert lr.find_binary() == mine


def test_find_env_not_executable_is_none_no_fallback(tmp_path, monkeypatch):
    _exe(lr.install_dir() / "llama-server")
    bad = _exe(tmp_path / "mine" / "ls", mode=0o644)
    monkeypatch.setenv("VT_LLAMA_SERVER", str(bad))
    assert lr.find_binary() is None


def test_find_env_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_LLAMA_SERVER", str(tmp_path / "ghost"))
    assert lr.find_binary() is None


def test_find_ignores_nonexec_managed(tmp_path):
    _exe(lr.install_dir() / "llama-server", mode=0o644)
    assert lr.find_binary() is None


def test_find_env_blank_ignored(monkeypatch):
    monkeypatch.setenv("VT_LLAMA_SERVER", "   ")
    m = _exe(lr.install_dir() / "llama-server")
    assert lr.find_binary() == m


# ── asset_for ──────────────────────────────────────────────────────
@pytest.mark.parametrize("os_,arch,ver", [("macos", "arm64", (26, 0)), ("macos", "x86_64", (14, 0)),
                                          ("linux", "x86_64", None), ("linux", "arm64", None)])
def test_asset_supported(os_, arch, ver):
    a = lr.asset_for(HostEnv(os_, arch, ver))
    assert a is not None and a[0].endswith(".tar.gz") and len(a[1]) == 64 and a[2] > 0


def test_asset_wsl2_is_linux():
    assert lr.asset_for(HostEnv("linux", "x86_64", None, wsl2=True)) == lr.ASSETS["linux-x86_64"]


@pytest.mark.parametrize("env", [HostEnv("other", "x86_64", None), HostEnv("linux", "riscv64", None),
                                 HostEnv("macos", "ppc", (10, 5))])
def test_asset_unsupported(env):
    assert lr.asset_for(env) is None


# ── download_and_install ───────────────────────────────────────────
ENV = HostEnv("linux", "x86_64", None)


def _tgz(members):
    """members: [(name, bytes|None, mode, type, linkname)] → tar.gz 바이트."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, mode, typ, link in members:
            ti = tarfile.TarInfo(name)
            ti.mode = mode
            ti.type = typ
            if typ in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                ti.linkname = link
                tar.addfile(ti)
            elif typ == tarfile.DIRTYPE:
                tar.addfile(ti)
            else:
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _f(name, data=b"BIN", mode=0o755):
    return (name, data, mode, tarfile.REGTYPE, None)


class _Opener:
    def __init__(self, data, chunk=None):
        self.data, self.calls = data, []

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        return io.BytesIO(self.data)


@pytest.fixture
def patch_asset(monkeypatch):
    def apply(data, sha=None, size=None):
        monkeypatch.setitem(lr.ASSETS, ENV.key, ("fake.tar.gz", sha or hashlib.sha256(data).hexdigest(),
                                                 size if size is not None else len(data)))
    return apply


def _no_leftovers():
    root = lr.install_root()
    return [p.name for p in root.iterdir() if p.name.startswith(".dl-")]


def test_install_success(patch_asset):
    data = _tgz([_f("llama-b1/llama-server"), _f("llama-b1/libx.so", b"L", 0o644)])
    patch_asset(data)
    op = _Opener(data)
    seen = []
    b = lr.download_and_install(ENV, lambda d, t: seen.append((d, t)), _opener=op)
    assert b.name == "llama-server" and os.access(b, os.X_OK) and b.read_bytes() == b"BIN"
    assert lr.install_dir() in b.parents
    assert (b.parent / "libx.so").exists()
    assert op.calls == [f"https://github.com/ggml-org/llama.cpp/releases/download/{lr.TAG}/fake.tar.gz"]
    assert seen and seen[-1] == (len(data), len(data))
    assert _no_leftovers() == []
    assert lr.find_binary() == b


def test_install_chmods_when_archive_lacks_exec_bit(patch_asset):
    data = _tgz([_f("llama-server", mode=0o644)])
    patch_asset(data)
    b = lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert os.access(b, os.X_OK)


def test_install_replaces_existing(patch_asset):
    old = lr.install_dir() / "stale.txt"
    old.parent.mkdir(parents=True)
    old.write_text("x")
    data = _tgz([_f("llama-server")])
    patch_asset(data)
    lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert not old.exists()


def test_sha_mismatch_discards(patch_asset):
    data = _tgz([_f("llama-server")])
    patch_asset(data, sha="0" * 64)
    with pytest.raises(lr.ReleaseError, match="sha256"):
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert not lr.install_dir().exists()
    assert lr.find_binary() is None
    assert _no_leftovers() == []


def test_sha_mismatch_keeps_previous_install(patch_asset):
    prev = _exe(lr.install_dir() / "llama-server")
    data = _tgz([_f("llama-server")])
    patch_asset(data, sha="f" * 64)
    with pytest.raises(lr.ReleaseError):
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert prev.exists()


def test_size_overrun_aborts(patch_asset):
    data = _tgz([_f("llama-server", b"A" * 5000)])
    patch_asset(data, sha=hashlib.sha256(data).hexdigest(), size=100)   # 예상 100B인데 더 큼
    with pytest.raises(lr.ReleaseError, match="예상 크기"):
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert not lr.install_dir().exists() and _no_leftovers() == []


@pytest.mark.parametrize("name,kind,link", [
    ("../evil", tarfile.REGTYPE, None),
    ("dir/../../evil", tarfile.REGTYPE, None),
    ("esc", tarfile.SYMTYPE, "../../outside"),
    ("esc2", tarfile.SYMTYPE, "/etc/passwd"),
])
def test_tar_escape_rejected(patch_asset, tmp_path, name, kind, link):
    members = [_f("llama-server")]
    members.append((name, b"pwn", 0o644, kind, link))
    data = _tgz(members)
    patch_asset(data)
    with pytest.raises((lr.ReleaseError, tarfile.TarError)):
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert not lr.install_dir().exists()
    assert _no_leftovers() == []
    assert not (tmp_path / "evil").exists() and not (lr.install_root().parent / "evil").exists()


def test_tar_without_binary(patch_asset):
    data = _tgz([_f("readme.txt", b"hi", 0o644)])
    patch_asset(data)
    with pytest.raises(lr.ReleaseError, match="llama-server"):
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    assert not lr.install_dir().exists() and _no_leftovers() == []


def test_unsupported_env_raises_without_network():
    class Boom:
        def __call__(self, *a, **k):
            raise AssertionError("network used")
    with pytest.raises(lr.ReleaseError, match="공식 배포본이 없습니다"):
        lr.download_and_install(HostEnv("other", "x86_64", None), lambda *_: None, _opener=Boom())


def test_opener_error_cleans_tmp(patch_asset):
    patch_asset(b"x")

    def boom(req, timeout=None):
        raise OSError("down")
    with pytest.raises(OSError):
        lr.download_and_install(ENV, lambda *_: None, _opener=boom)
    assert _no_leftovers() == []


def test_remove_installed(patch_asset):
    assert lr.remove_installed() == 0
    _exe(lr.install_dir() / "llama-server").write_text("12345")
    assert lr.remove_installed() == 5
    assert not lr.install_root().exists()


def test_tar_absolute_path_not_written_outside(patch_asset, tmp_path):
    """절대경로 멤버는 거부되거나 스테이징 안으로 눌려 들어간다 — 실제 절대 위치에는 절대 안 써진다."""
    target = tmp_path / "abs-target" / "evil.txt"
    data = _tgz([_f("llama-server"), (str(target), b"pwn", 0o644, tarfile.REGTYPE, None)])
    patch_asset(data)
    try:
        lr.download_and_install(ENV, lambda *_: None, _opener=_Opener(data))
    except (lr.ReleaseError, tarfile.TarError):
        pass
    assert not target.exists()
    assert _no_leftovers() == []
