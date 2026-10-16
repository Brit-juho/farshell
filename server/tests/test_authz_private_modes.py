"""authz 저장소·로그·소켓 실행 디렉터리의 권한 계약 — umask 와 부모 디렉터리 모드에 상관없이 정확히.

디렉터리는 정확히 0700, 파일은 정확히 0600. `mkdir(parents=True, mode=)` 는 마지막 단계에만 mode 를 쓰고 umask 를
받으므로(CI 의 umask 022 + 0755 임시 디렉터리에서 중간 디렉터리가 0755 로 남았다), 시험은 그 조건을 일부러 만든다.
"""

from __future__ import annotations

import os
import stat

import pytest

from authz import audit, denylog, legacy_use, shadow, sock, store


def mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def loose(tmp_path, monkeypatch):
    """umask 022, 0755 부모 아래의 상태 디렉터리(아직 없음)."""
    old = os.umask(0o022)
    os.chmod(tmp_path, 0o755)
    sd = tmp_path / "vt"
    monkeypatch.setenv("VT_STATE_DIR", str(sd))
    yield sd
    os.umask(old)


def assert_private_tree(root, *, dirs_from=None):
    """root 아래(root 포함 여부는 dirs_from) 모든 항목이 정확히 0700/0600."""
    seen = 0
    for p in [root, *root.rglob("*")]:
        if p.is_symlink() or p.is_socket():
            continue
        want = 0o700 if p.is_dir() else 0o600
        assert mode(p) == want, (p, oct(mode(p)))
        seen += 1
    assert seen


def test_write_private_nested_dirs(loose):
    p = store.root() / "legacy" / "20260101T000000" / "x.log"
    store.write_private(p, b"x")
    assert_private_tree(store.root())


def test_ensure_dir_fixes_existing_loose_dirs_inside_authz(loose):
    d = store.root() / "a" / "b"
    d.mkdir(parents=True)                     # umask 022 → 0755
    assert mode(d) == 0o755
    store._ensure_dir(d)
    assert_private_tree(store.root())


def test_ensure_dir_leaves_dirs_outside_authz_alone(loose):
    loose.mkdir(mode=0o755)
    os.chmod(loose, 0o755)
    store._ensure_dir(store.root() / "x")
    assert mode(loose) == 0o755                 # 이미 있던 authz 바깥 상위는 건드리지 않는다


def test_audit_chain_private(loose):
    audit.append({"actor": "a", "decision": "allow", "scope": "s"}, now=1.0)
    assert mode(audit.path().parent) == 0o700 and mode(audit.path()) == 0o600


def test_denylog_private(loose):
    assert denylog.record({"actor": "a", "decision": "deny", "scope": "s"}, now=1.0)
    assert mode(denylog.path().parent) == 0o700 and mode(denylog.path()) == 0o600


def test_legacy_use_private(loose):
    legacy_use.record("/api/x", "ua", now=1.0)
    legacy_use.record("/api/y", "ua", now=2.0, denied=True)
    legacy_use.flush()
    assert_private_tree(store.root())


def test_shadow_log_private(loose):
    shadow._write({"site": "s", "ts": 1.0})
    assert mode(shadow.path().parent) == 0o700 and mode(shadow.path()) == 0o600


def test_corrupt_copy_private(loose):
    store._ensure_dir(store.root())
    store.doc_path("grants").write_bytes(b"{broken")
    b = store.preserve_copy("grants")
    assert b is not None and mode(b) == 0o600


def test_prepare_socket_run_dir_private(tmp_path):
    old = os.umask(0o022)
    try:
        d = __import__("tempfile").mkdtemp(prefix="fs", dir="/tmp")
        os.chmod(d, 0o755)
        path = __import__("pathlib").Path(d) / "run" / "x" / "fsh.sock"
        s = sock.prepare_socket(path)
        try:
            assert mode(path.parent) == 0o700 and mode(path.parent.parent) == 0o700 and mode(path) == 0o600
        finally:
            s.close()
            __import__("shutil").rmtree(d, ignore_errors=True)
    finally:
        os.umask(old)
