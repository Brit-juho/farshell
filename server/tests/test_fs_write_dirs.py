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
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fs_trash
import fs_write
import fsguard
import main
from resilience import mounts
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


# --- 리뷰 후속: 끝의 「/」·「.」, inode 기준 루트 보호, 마운트, git 저장소 모양, 서버 폴더 ---------------------
# 모두 고치기 전 코드에서 실패한다(링크 대상·루트 조상·마운트를 품은 폴더가 옮겨지거나, 저장소 모양이 조립된다).


@pytest.mark.parametrize("suffix", ["/", "/.", "/./", "//"])
def test_dir_ops_with_trailing_slash_or_dot_never_touch_the_link_target(client, root, monkeypatch, suffix):
    """`lstat("x/link/")`은 링크를 따라가고 `Path`는 끝의 `/`·`.`를 지운다 — 링크 **대상**이 옮겨지면 안 된다."""
    called = _no_trash(monkeypatch)
    d = _tree(root)
    link = root / "proj" / "lnin"
    link.symlink_to(d)
    r = client.post("/api/fs/delete", json={"path": str(link) + suffix, "kind": "dir"})
    assert r.status_code == 403, r.text
    r = client.post("/api/fs/rename", json={"path": str(link) + suffix, "to": str(root / "proj" / "moved"), "kind": "dir"})
    assert r.status_code == 403, r.text
    assert called == [] and d.is_dir() and link.is_symlink() and not (root / "proj" / "moved").exists()


@pytest.mark.parametrize("suffix", ["/.", "/./"])
def test_file_ops_with_trailing_dot_never_touch_the_link_target(client, root, monkeypatch, suffix):
    called = _no_trash(monkeypatch)
    target = root / "proj" / "a.txt"
    link = root / "proj" / "fl"
    link.symlink_to(target)
    r = client.post("/api/fs/delete", json={"path": str(link) + suffix})
    assert r.status_code == 403, r.text
    r = client.post("/api/fs/rename", json={"path": str(link) + suffix, "to": str(root / "proj" / "b.txt")})
    assert r.status_code == 403, r.text
    assert called == [] and target.read_text() == "one\ntwo\n" and not (root / "proj" / "b.txt").exists()


def test_rename_dir_alias_of_a_root_ancestor_is_refused_by_inode(client, root, monkeypatch, tmp_path):
    """루트 목록이 다른 경로(링크)로 같은 폴더를 가리키면 글자 비교는 놓친다 — inode로 거부한다."""
    called = _no_trash(monkeypatch)
    work = root / "proj" / "Work"
    (work / "c").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(work)
    real = fsguard.get_write_roots
    monkeypatch.setattr(fsguard, "get_write_roots", lambda: [*real(), alias / "c"])
    r = client.post("/api/fs/delete", json={"path": str(work), "kind": "dir"})
    assert r.status_code == 403, r.text
    r = client.post("/api/fs/rename", json={"path": str(work), "to": str(root / "proj" / "W2"), "kind": "dir"})
    assert r.status_code == 403, r.text
    assert called == [] and (work / "c").is_dir()


def _case_insensitive(p: Path) -> bool:
    probe = p / "CaseProbe"
    probe.mkdir()
    try:
        return (p / "caseprobe").exists()
    finally:
        probe.rmdir()


def test_delete_dir_case_variant_of_an_overlapping_root_ancestor_is_refused(client, root, monkeypatch):
    if not _case_insensitive(root):
        pytest.skip("대소문자를 가리는 파일시스템 — 이름 변형이 같은 폴더가 아니다")
    called = _no_trash(monkeypatch)
    inner = root / "proj" / "Work" / "c"
    inner.mkdir(parents=True)
    monkeypatch.setenv("VT_BROWSE_ROOTS", f"{root}:{inner}")
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "work"), "kind": "dir"})
    assert r.status_code == 403, r.text
    assert called == [] and inner.is_dir()


def test_install_and_state_dirs_and_their_ancestors_are_refused(root, monkeypatch):
    called = _no_trash(monkeypatch)
    inst = root / "proj" / "farshell"
    state = root / "proj" / "st" / "vtstate"
    (inst / "server").mkdir(parents=True)
    state.mkdir(parents=True)
    real = fsguard._server_dirs
    assert (real()[0] / "server" / "fsguard.py").is_file()       # 기본값은 이 서버의 체크아웃 폴더
    monkeypatch.setattr(fsguard, "_server_dirs", lambda: [inst, state])
    for p in (inst, root / "proj" / "st", state, root / "proj"):
        with pytest.raises(fs_write.WriteError) as e:
            fs_write.delete_dir(str(p))
        assert e.value.status == 403, p
        with pytest.raises(fs_write.WriteError) as e:
            fs_write.rename_dir(str(p), str(root / "moved"))
        assert e.value.status == 403, p
    assert called == [] and inst.is_dir() and state.is_dir() and not (root / "moved").exists()


def test_state_dir_comes_from_vt_state_dir(root, monkeypatch):
    called = _no_trash(monkeypatch)
    state = root / "proj" / "vtstate"
    state.mkdir()
    monkeypatch.setenv("VT_STATE_DIR", str(state))
    with pytest.raises(fs_write.WriteError) as e:
        fs_write.delete_dir(str(state))
    assert e.value.status == 403 and called == [] and state.is_dir()


def test_delete_dir_containing_a_mount_is_refused(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    d = _tree(root)
    monkeypatch.setattr(mounts, "_override", [mounts.Mount(path=str(d / "sub"), fstype="ext4", source="/dev/x", local=True)])
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 400 and r.json()["error"] == "mount_point", r.text
    assert called == [] and d.is_dir()


def test_delete_dir_refused_when_the_mount_table_cannot_be_read(client, root, monkeypatch):
    called = _no_trash(monkeypatch)
    d = _tree(root)
    monkeypatch.setattr(mounts, "current_mounts", lambda: None)
    r = client.post("/api/fs/delete", json={"path": str(d), "kind": "dir"})
    assert r.status_code == 409 and r.json()["error"] == "trash_unavailable", r.text
    assert called == [] and d.is_dir()


def test_contains_mount_matches_only_mounts_below(monkeypatch, tmp_path):
    d = tmp_path / "Proj"
    M = mounts.Mount

    def table(*paths):
        monkeypatch.setattr(mounts, "_override", [M(path=p, fstype="x", source="x", local=True) for p in paths])

    table("/", str(d), str(tmp_path / "Proj2" / "m"), str(tmp_path))
    assert fsguard.contains_mount(d) is False                    # 자신·형제·조상은 아래가 아니다
    table(str(d / "a" / "b"))
    assert fsguard.contains_mount(d) is True
    monkeypatch.setattr(sys, "platform", "darwin")
    table("/System/Volumes/Data" + str(d / "m"))                  # firmlink 너머로 기록된 마운트 경로
    assert fsguard.contains_mount(d) is True
    table(str(tmp_path / "proj" / "m"))                           # 대소문자만 다른 경로(macOS 디스크는 같은 폴더)
    assert fsguard.contains_mount(d) is True


def test_linux_mountinfo_bind_mount_is_found(monkeypatch, tmp_path):
    d = tmp_path / "with space"
    esc = str(d).replace(" ", "\\040")
    line = f"36 35 98:0 /src {esc}/bind rw,noatime - ext4 /dev/root rw\n"
    monkeypatch.setattr(mounts, "_override", mounts._linux_mounts(line))
    monkeypatch.setattr(sys, "platform", "linux")
    assert fsguard.contains_mount(d) is True


def test_mkdir_and_create_cannot_assemble_a_bare_git_dir(client, root):
    x = root / "proj" / "x"
    x.mkdir()
    assert client.post("/api/fs/new", json={"dir": str(x), "name": "config"}).status_code == 200
    assert client.post("/api/fs/new", json={"dir": str(x), "name": "HEAD"}).status_code == 200
    assert client.post("/api/fs/mkdir", json={"dir": str(x), "name": "objects"}).status_code == 200
    r = client.post("/api/fs/mkdir", json={"dir": str(x), "name": "refs"})
    assert r.status_code == 403, r.text
    assert not (x / "refs").exists()
    # 다른 순서로 조립해도 마지막 조각은 거부된다 — 새 파일·저장·옮겨 오기 모두.
    y = root / "proj" / "y"
    (y / "objects").mkdir(parents=True)
    (y / "refs").mkdir()
    r = client.post("/api/fs/new", json={"dir": str(y), "name": "HEAD"})
    assert r.status_code == 403 and not (y / "HEAD").exists()
    (root / "proj" / "h").write_text("ref: refs/heads/main\n")
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "h"), "to": str(y / "HEAD")})
    assert r.status_code == 403 and not (y / "HEAD").exists()
    r = client.put("/api/fs/file", json={"path": str(y / "HEAD"), "content": "ref: x\n", "encoding": "utf-8",
                                         "expected_rev": None, "save_id": "s-git-head-0001"})
    assert r.status_code == 403 and not (y / "HEAD").exists()
    z = root / "proj" / "z"
    (z / "objects").mkdir(parents=True)
    (z / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "proj" / "r").mkdir()
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "r"), "to": str(z / "refs"), "kind": "dir"})
    assert r.status_code == 403 and not (z / "refs").exists()


def test_no_writes_inside_an_existing_bare_git_dir(client, root):
    from test_fs_write import save_body
    g = root / "proj" / "g.git"
    for n in ("objects", "refs", "hooks"):
        (g / n).mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n")
    (g / "config").write_text("[core]\n")
    r = client.put("/api/fs/file", json=save_body(g / "config", "[core]\n\tfsmonitor = touch /tmp/pwn\n"))
    assert r.status_code == 403 and (g / "config").read_text() == "[core]\n"
    for name in ("config2", "description"):
        r = client.post("/api/fs/new", json={"dir": str(g), "name": name})
        assert r.status_code == 403 and not (g / name).exists()
    r = client.post("/api/fs/mkdir", json={"dir": str(g), "name": "info"})
    assert r.status_code == 403 and not (g / "info").exists()
    # 저장소 모양이 아닌 폴더의 `config`는 평범한 파일이다.
    assert client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "config"}).status_code == 200
