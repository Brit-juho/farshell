"""폴더 쓰기(새 폴더 · 폴더 이름 바꾸기 · 폴더 휴지통) — 보안 회귀.

순진한 구현에서 실패하도록 짰다:
- `resolve_for_write`만 쓰는 구현은 루트 자신을 휴지통에 넣고, 심링크 폴더 경로를 resolve해 **링크 대상**을 옮긴다.
- 문자열 비교로 「자기 안으로」를 거르는 구현은 심링크를 거쳐 들어가는 경로를 놓친다.
- `os.rename`만 쓰는 구현은 같은 이름의 **빈 폴더를 말없이 갈아 끼운다**.
- `shutil.rmtree`로 떨어지는 구현은 휴지통 실패에서 폴더를 잃는다.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fs_trash
import fs_write
import main
from authz import principals as P
from shared_testkit import _Sock
from test_fs_write import _auth_env, client, root, unelevated  # noqa: F401 — 픽스처 재사용


def _outside(root: Path) -> Path:
    return root.parent / "outside"


def _tree(root: Path) -> Path:
    d = root / "proj" / "d"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "x.txt").write_text("x\n")
    (d / "y.txt").write_text("y\n")
    return d


def _no_trash(monkeypatch) -> list:
    called = []
    monkeypatch.setattr(fs_trash, "trash", lambda *a, **k: called.append(a))
    return called


# --- 새 폴더 -------------------------------------------------------------------------------


def test_mkdir_creates_without_overwrite(client, root):
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "new"})
    assert r.status_code == 200, r.text
    assert (root / "proj" / "new").is_dir() and r.json()["kind"] == "dir"
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "new"})
    assert r.status_code == 409 and r.json()["error"] == "exists"
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "a.txt"})
    assert r.status_code == 409 and (root / "proj" / "a.txt").read_text() == "one\ntwo\n"


@pytest.mark.parametrize("name", [".github", ".git", ".vscode", ".ssh", ".sometool", ".GitHub", "a/b", "..", ""])
def test_mkdir_refuses_exec_config_hidden_and_bad_names(client, root, name):
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": name})
    assert r.status_code in (400, 403), (name, r.text)
    assert sorted(os.listdir(root / "proj")) == ["a.txt"]


def test_mkdir_inside_exec_config_or_outside_or_over_symlink_is_refused(client, root):
    (root / "proj" / ".github").mkdir()
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj" / ".github"), "name": "workflows"})
    assert r.status_code == 403 and not (root / "proj" / ".github" / "workflows").exists()
    r = client.post("/api/fs/mkdir", json={"dir": str(_outside(root)), "name": "planted"})
    assert r.status_code == 403 and not (_outside(root) / "planted").exists()
    (root / "proj" / "ln").symlink_to(_outside(root))
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj" / "ln"), "name": "planted"})
    assert r.status_code == 403 and not (_outside(root) / "planted").exists()
    (root / "proj" / "dangling").symlink_to(_outside(root) / "made-by-link")
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "dangling"})
    assert r.status_code in (403, 409) and not (_outside(root) / "made-by-link").exists()


def test_mkdir_parent_swap_after_check_never_creates_outside(client, root, monkeypatch):
    real = fs_write._resolve_dir

    def swapping(raw):
        p = real(raw)
        if not (root / "proj").is_symlink():
            (root / "proj").rename(root / "proj.real")
            (root / "proj").symlink_to(_outside(root))
        return p
    monkeypatch.setattr(fs_write, "_resolve_dir", swapping)
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "planted"})
    assert r.status_code in (403, 404, 409), r.text
    assert not (_outside(root) / "planted").exists()


# --- 폴더 이름 바꾸기 -------------------------------------------------------------------------


def test_rename_dir_moves_contents_and_needs_kind(client, root):
    d = _tree(root)
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "e")})
    assert r.status_code == 400 and r.json()["error"] == "is_directory"        # kind 없이는 폴더를 다루지 않는다
    assert d.is_dir()
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "e"), "kind": "dir"})
    assert r.status_code == 200, r.text
    assert (root / "proj" / "e" / "sub" / "x.txt").read_text() == "x\n" and not d.exists()
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "a.txt"), "to": str(root / "proj" / "b"),
                                            "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "not_a_directory"


@pytest.mark.parametrize("empty", [True, False])
def test_rename_dir_onto_existing_never_replaces(client, root, empty):
    d = _tree(root)
    other = root / "proj" / "other"
    other.mkdir()
    if not empty:
        (other / "keep.txt").write_text("keep\n")
    ino = os.stat(other).st_ino
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(other), "kind": "dir"})
    assert r.status_code == 409 and r.json()["error"] == "exists", r.text
    assert os.stat(other).st_ino == ino and d.is_dir()
    assert sorted(os.listdir(other)) == ([] if empty else ["keep.txt"])
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "a.txt"), "kind": "dir"})
    assert r.status_code == 409 and (root / "proj" / "a.txt").read_text() == "one\ntwo\n"
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(other), "kind": "dir", "overwrite": True})
    assert r.status_code == 400 and os.stat(other).st_ino == ino


def test_rename_dir_onto_an_empty_folder_created_after_the_check_is_refused(client, root, monkeypatch):
    """확인과 옮기기 사이에 같은 이름의 빈 폴더가 생겨도 갈아 끼우지 않는다(RENAME_EXCL)."""
    d = _tree(root)
    late = root / "proj" / "late"
    real = fs_write._require_dir_unchanged

    def make_then_check(dir_fd, name, st):
        real(dir_fd, name, st)
        late.mkdir()
    monkeypatch.setattr(fs_write, "_require_dir_unchanged", make_then_check)
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(late), "kind": "dir"})
    if fs_write._renamex_fn() is None:
        pytest.skip("이 C 라이브러리에 RENAME_EXCL이 없다 — 잠금 안의 확인으로만 막는다(문서화한 한계)")
    assert r.status_code == 409 and r.json()["error"] == "exists", r.text
    assert late.is_dir() and not os.listdir(late) and d.is_dir()


def test_rename_dir_into_itself_is_refused_even_through_a_symlink(client, root):
    d = _tree(root)
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(d / "sub" / "d2"), "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "into_itself"
    (root / "proj" / "via").symlink_to(d / "sub")
    r = client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "via" / "d2"), "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "into_itself"
    assert (d / "sub" / "x.txt").read_text() == "x\n"


def test_rename_dir_kernel_einval_maps_to_into_itself(root, monkeypatch):
    """경로 판정을 비켜 가도(경합) 커널의 EINVAL이 `into_itself`로 나온다 — 다른 오류로 새지 않는다."""
    import errno
    err = fs_write._dir_os_error(OSError(errno.EINVAL, "Invalid argument"))
    assert err.status == 400 and err.body["error"] == "into_itself"


def test_rename_dir_refuses_symlinked_folders_and_exec_config_and_roots(client, root):
    d = _tree(root)
    (root / "proj" / "lnin").symlink_to(d)
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "lnin"), "to": str(root / "proj" / "z"), "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "is_symlink"
    assert d.is_dir() and (root / "proj" / "lnin").is_symlink() and not (root / "proj" / "z").exists()
    (root / "proj" / ".github" / "workflows").mkdir(parents=True)
    for src, dst in ((root / "proj" / ".github" / "workflows", root / "proj" / "w"),
                     (root / "proj" / ".github", root / "proj" / "gh"),
                     (d, root / "proj" / ".github"),
                     (d, root / "proj" / ".github" / "d"),
                     (d, root / "proj" / ".git"),
                     (d, _outside(root) / "d"),
                     (_outside(root), root / "proj" / "o"),
                     (root, root.parent / "root2"),
                     (root / "proj", root)):
        r = client.post("/api/fs/rename", json={"path": str(src), "to": str(dst), "kind": "dir"})
        assert r.status_code in (400, 403, 409), (src, dst, r.text)
    assert d.is_dir() and (root / "proj" / ".github" / "workflows").is_dir() and _outside(root).is_dir()


# --- 폴더 휴지통 -----------------------------------------------------------------------------


def test_delete_dir_moves_a_non_empty_folder_to_the_trash(client, root, monkeypatch, tmp_path):
    data = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setattr(fs_trash, "IMPL", "freedesktop")
    d = _tree(root)
    r = client.post("/api/fs/delete", json={"path": str(d)})
    assert r.status_code == 400 and r.json()["error"] == "is_directory" and d.is_dir()   # kind 없이는 거부
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 200, r.text
    assert not d.exists()
    trash = data / "Trash"
    assert (trash / "files" / "d" / "sub" / "x.txt").read_text() == "x\n"
    assert "Path=" in (trash / "info" / "d.trashinfo").read_text()


def test_delete_dir_outside_roots_root_itself_and_exec_config_are_refused(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    (root / "proj" / ".github" / "workflows").mkdir(parents=True)
    (root / "proj" / ".vscode").mkdir()
    for p in (_outside(root), root, root / "proj" / ".github", root / "proj" / ".github" / "workflows",
              root / "proj" / ".vscode", root / "proj" / ".GitHub"):
        r = client.post("/api/fs/delete", json={"path": str(p), "kind": "dir"})
        assert r.status_code == 403, (p, r.text)
    assert called == [] and (_outside(root) / "victim.txt").exists() and (root / "proj" / ".github" / "workflows").is_dir()


def test_delete_dir_root_itself_refused_also_for_write_roots(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    wr = root / "proj"
    monkeypatch.setenv("VT_WRITE_ROOTS", str(wr))
    r = client.post("/api/fs/delete", json={"path": str(wr), "kind": "dir"})
    assert r.status_code == 403 and called == [] and wr.is_dir()


def test_delete_dir_via_symlink_never_trashes_the_target(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    d = _tree(root)
    (root / "proj" / "lnin").symlink_to(d)                      # 루트 안을 가리키는 링크 — 대상이 휴지통에 가면 안 된다
    (root / "proj" / "lnout").symlink_to(_outside(root))
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "lnin"), "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "is_symlink"
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "lnout"), "kind": "dir"})
    assert r.status_code == 400
    (_outside(root) / "inner").mkdir()
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "lnout" / "inner"), "kind": "dir"})
    assert r.status_code == 403
    assert called == [] and d.is_dir() and (_outside(root) / "inner").is_dir()


def test_delete_dir_swapped_for_a_symlink_after_the_check_is_refused(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    d = _tree(root)
    real = fs_write._resolve_dir

    def swap(raw):
        p = real(raw)
        if not d.is_symlink():
            d.rename(root / "proj" / "d.real")
            d.symlink_to(_outside(root))
        return p
    monkeypatch.setattr(fs_write, "_resolve_dir", swap)
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code in (400, 409), r.text
    assert called == [] and (_outside(root) / "victim.txt").exists()


def test_macos_trash_refuses_when_the_folder_became_a_file(root, monkeypatch):
    """도우미에 넘기기 직전에 같은 자리가 다른 종류가 됐으면 넘기지 않는다."""
    monkeypatch.setattr(fs_trash, "IMPL", "macos")
    d = _tree(root)
    st = os.lstat(d)
    shutil.rmtree(d)
    d.write_text("now a file\n")
    with pytest.raises(fs_trash.TrashChanged):
        fs_trash._trash_macos(d, st)


def test_dir_trash_failure_never_deletes(client, root, monkeypatch):
    monkeypatch.setattr(fs_trash, "IMPL", "none")
    d = _tree(root)
    removed = []
    real_rmdir, real_unlink, real_rmtree = os.rmdir, os.unlink, shutil.rmtree
    monkeypatch.setattr(os, "rmdir", lambda *a, **k: (removed.append(a), real_rmdir(*a, **k)))
    monkeypatch.setattr(os, "unlink", lambda *a, **k: (removed.append(a), real_unlink(*a, **k)))
    monkeypatch.setattr(os, "remove", lambda *a, **k: (removed.append(a), real_unlink(*a, **k)))
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: (removed.append(a), real_rmtree(*a, **k)))
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 409 and r.json()["error"] == "trash_unavailable"
    assert (d / "sub" / "x.txt").read_text() == "x\n" and removed == []


def test_dir_macos_helper_failure_keeps_the_folder(client, root, monkeypatch):
    import json
    import subprocess
    monkeypatch.setattr(fs_trash, "IMPL", "macos")
    d = _tree(root)
    monkeypatch.setattr(fs_trash, "_run_helper", lambda path: subprocess.CompletedProcess(
        [str(path)], 0, json.dumps({"ok": False, "code": 513, "reason": "no trash"}).encode(), b""))
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 409 and (d / "y.txt").exists()
    moved = []

    def fake(path):
        dest = root.parent / "fake-trash-d"
        os.rename(path, dest)
        moved.append(dest)
        return subprocess.CompletedProcess([str(path)], 0, json.dumps({"ok": True, "path": str(dest)}).encode(), b"")
    monkeypatch.setattr(fs_trash, "_run_helper", fake)
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 200, r.text
    assert (moved[0] / "sub" / "x.txt").read_text() == "x\n"


def test_dir_kind_rejects_rev_and_bad_kind(client, root):
    d = _tree(root)
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir", "expected_rev": "1:2:3:4"})
    assert r.status_code == 400
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "folder"})
    assert r.status_code == 400 and r.json()["error"] == "bad_kind"
    assert d.is_dir()


# --- 인가 · 감사 -------------------------------------------------------------------------------


def test_folder_writes_need_elevation(unelevated, root):
    d = _tree(root)
    for path, body in (("/api/fs/mkdir", {"dir": str(root / "proj"), "name": "n"}),
                       ("/api/fs/rename", {"path": str(d), "to": str(root / "proj" / "e"), "kind": "dir"}),
                       ("/api/fs/delete", {"path": str(d), "kind": "dir"})):
        r = unelevated.post(path, json=body)
        assert r.status_code == 401 and r.json()["error"] == "elevation_required", (path, r.text)
    assert d.is_dir() and not (root / "proj" / "n").exists() and not (root / "proj" / "e").exists()


def test_folder_writes_refuse_machine_token_and_agent_principal(tmp_path, monkeypatch, root):
    _auth_env(tmp_path, monkeypatch)
    d = _tree(root)
    bodies = (("/api/fs/mkdir", {"dir": str(root / "proj"), "name": "n"}),
              ("/api/fs/rename", {"path": str(d), "to": str(root / "proj" / "e"), "kind": "dir"}),
              ("/api/fs/delete", {"path": str(d), "kind": "dir"}))
    with TestClient(main.app) as c:
        for path, body in bodies:
            r = c.post(path, json=body, headers={"Authorization": "Bearer machine-tok"})
            assert r.status_code == 403, (path, r.text)
    agent = TestClient(_Sock(main.app, P.agent("$1:101", name="alpha")))
    for path, body in bodies:
        assert agent.post(path, json=body).status_code in (401, 403), path
    assert d.is_dir() and not (root / "proj" / "n").exists() and not (root / "proj" / "e").exists()


def test_folder_cross_site_origin_is_refused(client, root):
    r = client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "n"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and not (root / "proj" / "n").exists()


def test_folder_writes_are_audited_without_paths(client, root, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(fs_trash, "IMPL", "none")
    d = _tree(root)
    client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "n"})
    client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "e"), "kind": "dir"})
    client.post("/api/fs/delete", json={"path": str(root / "proj" / "e"), "kind": "dir"})
    log = (tmp_path / "vt" / "audit" / "authz.jsonl").read_text()
    rows = [json.loads(x) for x in log.splitlines()]
    acts = [(r["action"], r["result"]) for r in rows if r.get("action", "").startswith("fs.")]
    assert acts == [("fs.mkdir", "started"), ("fs.mkdir", "ok"), ("fs.rename_dir", "started"), ("fs.rename_dir", "ok"),
                    ("fs.trash_dir", "started"), ("fs.trash_dir", "failed")]
    assert str(root) not in log


def test_write_roots_off_closes_folder_writes(client, root, monkeypatch):
    monkeypatch.setenv("VT_WRITE_ROOTS", "off")
    d = _tree(root)
    assert client.post("/api/fs/mkdir", json={"dir": str(root / "proj"), "name": "n"}).status_code == 403
    assert client.post("/api/fs/rename", json={"path": str(d), "to": str(root / "proj" / "e"), "kind": "dir"}).status_code == 403
    assert client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"}).status_code == 403
    assert d.is_dir()
