"""fsh update — 채널·서명 검증·롤백. 임시 git 리포와 임시 SSH 키만 쓴다(실제 설정·네트워크 없음)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import updater

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("ssh-keygen")),
                                reason="git/ssh-keygen 필요")

_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **_ENV}).stdout.strip()


def keygen(path: Path) -> Path:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)], check=True)
    return path


def sign_tag(repo, name, key, ref="HEAD", signed_as=None):
    sh(repo, "git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", "-m",
       signed_as or name, name, ref)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    monkeypatch.setattr(updater, "post_checkout", lambda root, old, new: None)
    yield


@pytest.fixture
def world(tmp_path):
    """origin(bare) + 작업 리포(원본) + 설치본(clone). master=릴리스, dev=앞서 간 개발."""
    key = keygen(tmp_path / "rel")
    other = keygen(tmp_path / "evil")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "master", str(origin))
    sh(tmp_path, "git", "init", "-q", "-b", "master", str(work))
    sh(work, "git", "remote", "add", "origin", str(origin))
    (work / "VERSION").write_text("1.0.0\n")
    sh(work, "git", "add", "."); sh(work, "git", "commit", "-qm", "r1")
    sign_tag(work, "v1.0.0", key)
    (work / "VERSION").write_text("1.1.0\n")
    sh(work, "git", "commit", "-qam", "r2")
    sign_tag(work, "v1.1.0", key)
    sh(work, "git", "checkout", "-q", "-b", "dev")
    (work / "VERSION").write_text("1.1.0\n"); (work / "dev.txt").write_text("x")
    sh(work, "git", "add", "."); sh(work, "git", "commit", "-qm", "dev work")
    sh(work, "git", "push", "-q", "origin", "master", "dev", "--tags")
    inst = tmp_path / "inst"
    sh(tmp_path, "git", "clone", "-q", str(origin), str(inst))
    sh(inst, "git", "checkout", "-q", "master")
    sh(inst, "git", "reset", "-q", "--hard", "v1.0.0")  # 설치본은 옛 릴리스에 머문다
    return dict(key=key, other=other, origin=origin, work=work, inst=inst)


def trust(world, key=None):
    pub = Path(str(key or world["key"]) + ".pub")
    assert updater.cmd_trust(world["inst"], str(pub), False, True) == 0


# ── 서명 ──
def test_signer_of_tag_matches_ssh_keygen_fingerprint(world):
    _, _, fp = updater.signer_of_tag(world["inst"], "v1.1.0")
    out = subprocess.run(["ssh-keygen", "-lf", str(world["key"]) + ".pub"], capture_output=True, text=True).stdout
    assert fp == out.split()[1]


def test_verify_requires_trusted_key(world):
    with pytest.raises(updater.UpdateError, match="신뢰하는 서명 키"):
        updater.verify_tag(world["inst"], "v1.1.0")


def test_verify_ok_and_wrong_key_refused(world):
    trust(world)
    assert updater.verify_tag(world["inst"], "v1.1.0").startswith("SHA256:")
    work = world["work"]
    sign_tag(work, "v9.0.0", world["other"])
    sh(work, "git", "push", "-q", "origin", "v9.0.0")
    sh(world["inst"], "git", "fetch", "-q", "--tags")
    with pytest.raises(updater.UpdateError, match="맞지 않습니다"):
        updater.verify_tag(world["inst"], "v9.0.0")


def test_unsigned_tag_refused(world):
    trust(world)
    work = world["work"]
    sh(work, "git", "tag", "-a", "-m", "x", "v2.0.0")
    sh(work, "git", "push", "-q", "origin", "v2.0.0")
    sh(world["inst"], "git", "fetch", "-q", "--tags")
    with pytest.raises(updater.UpdateError, match="서명이 없습니다"):
        updater.verify_tag(world["inst"], "v2.0.0")


def test_replayed_old_signed_tag_under_new_name_refused(world):
    """공격자가 v1.0.0 의 서명 태그 객체를 v9.9.9 라는 ref 에 붙여도 이름이 달라 거부."""
    trust(world)
    work = world["work"]
    obj = sh(work, "git", "rev-parse", "v1.0.0")
    sh(work, "git", "update-ref", "refs/tags/v9.9.9", obj)
    sh(work, "git", "push", "-q", "origin", "v9.9.9")
    sh(world["inst"], "git", "fetch", "-q", "--tags")
    with pytest.raises(updater.UpdateError, match="다른 이름"):
        updater.verify_tag(world["inst"], "v9.9.9")


def test_trust_refuses_second_key_without_replace(world):
    trust(world)
    with pytest.raises(updater.UpdateError, match="--replace"):
        updater.cmd_trust(world["inst"], str(world["other"]) + ".pub", False, True)
    assert updater.cmd_trust(world["inst"], str(world["other"]) + ".pub", True, True) == 0


def test_trust_from_latest_signed_tag(world):
    assert updater.cmd_trust(world["inst"], None, False, True) == 0
    assert len(updater.trusted_keys()) == 1


def test_trust_rejects_non_pubkey_file(world, tmp_path):
    bad = tmp_path / "bad.pub"
    bad.write_text("not a key")
    with pytest.raises(updater.UpdateError):
        updater.cmd_trust(world["inst"], str(bad), False, True)


# ── 채널 ──
def test_stable_updates_to_latest_signed_tag_detached(world):
    trust(world)
    assert updater.cmd_update(world["inst"], "stable", None, False, False, do_sync=False) == 0
    assert (world["inst"] / "VERSION").read_text().strip() == "1.1.0"
    assert updater.current_branch(world["inst"]) is None
    assert updater.read_channel() == "stable"


def test_stable_without_trust_changes_nothing(world):
    before = updater.rev(world["inst"], "HEAD")
    with pytest.raises(updater.UpdateError):
        updater.cmd_update(world["inst"], "stable", None, False, False, do_sync=False)
    assert updater.rev(world["inst"], "HEAD") == before


def test_stable_refuses_tag_not_in_master_history(world):
    trust(world)
    work = world["work"]
    sh(work, "git", "checkout", "-q", "dev")
    (work / "x").write_text("1"); sh(work, "git", "add", "."); sh(work, "git", "commit", "-qm", "off")
    sign_tag(work, "v3.0.0", world["key"])
    sh(work, "git", "push", "-q", "origin", "v3.0.0")
    with pytest.raises(updater.UpdateError, match="이력에 없습니다"):
        updater.cmd_update(world["inst"], "stable", None, False, False, do_sync=False)


def test_to_older_signed_tag_is_allowed_for_rollback(world):
    trust(world)
    updater.cmd_update(world["inst"], "stable", None, False, False, do_sync=False)
    assert updater.cmd_update(world["inst"], "stable", "v1.0.0", False, False, do_sync=False) == 0
    assert (world["inst"] / "VERSION").read_text().strip() == "1.0.0"


def test_dev_channel_fast_forwards_branch(world):
    assert updater.cmd_update(world["inst"], "dev", None, False, False, do_sync=False) == 0
    assert (world["inst"] / "dev.txt").exists()
    assert updater.current_branch(world["inst"]) == "dev"
    assert updater.read_channel() == "dev"


def test_dev_channel_refuses_diverged_local_dev(world):
    inst = world["inst"]
    sh(inst, "git", "checkout", "-q", "-b", "dev", "v1.0.0")
    (inst / "mine").write_text("1"); sh(inst, "git", "add", "."); sh(inst, "git", "commit", "-qm", "mine")
    sh(inst, "git", "checkout", "-q", "master")
    before = updater.rev(inst, "dev")
    with pytest.raises(updater.UpdateError, match="갈라졌습니다"):
        updater.cmd_update(inst, "dev", None, False, False, do_sync=False)
    assert updater.rev(inst, "dev") == before


def test_devmode_never_moves_code(world):
    before = updater.rev(world["inst"], "HEAD")
    assert updater.cmd_update(world["inst"], "devmode", None, False, False, do_sync=False) == 0
    assert updater.rev(world["inst"], "HEAD") == before
    assert updater.read_channel() == "devmode"


def test_check_changes_nothing_even_when_dirty(world):
    inst = world["inst"]
    trust(world)
    (inst / "VERSION").write_text("dirty\n")
    before = updater.rev(inst, "HEAD")
    assert updater.cmd_update(inst, "stable", None, True, False, do_sync=False) == 0
    assert updater.rev(inst, "HEAD") == before
    assert (inst / "VERSION").read_text() == "dirty\n"


def test_dirty_tree_refused(world):
    trust(world)
    (world["inst"] / "VERSION").write_text("dirty\n")
    with pytest.raises(updater.UpdateError):
        updater.cmd_update(world["inst"], "stable", None, False, False, do_sync=False)
    assert (world["inst"] / "VERSION").read_text() == "dirty\n"


def test_unset_channel_on_feature_branch_requires_explicit_choice(world):
    sh(world["inst"], "git", "checkout", "-q", "-b", "feat/x")
    with pytest.raises(updater.UpdateError, match="채널이 정해지지"):
        updater.cmd_update(world["inst"], None, None, False, False, do_sync=False)


# ── 실패 복구·롤백 ──
def test_build_failure_restores_original_commit_and_branch(world, monkeypatch):
    trust(world)
    inst = world["inst"]
    before = updater.rev(inst, "HEAD")

    def boom(root, old, new):
        raise updater.UpdateError("빌드 실패")
    monkeypatch.setattr(updater, "post_checkout", boom)
    assert updater.cmd_update(inst, "stable", None, False, False, do_sync=False) == 1
    assert updater.rev(inst, "HEAD") == before
    assert updater.current_branch(inst) == "master"
    assert (inst / "VERSION").read_text().strip() == "1.0.0"


def test_dev_build_failure_does_not_move_dev_branch(world, monkeypatch):
    inst = world["inst"]
    sh(inst, "git", "checkout", "-q", "-b", "dev", "origin/dev~1")
    before = updater.rev(inst, "dev")

    def boom(root, old, new):
        raise updater.UpdateError("빌드 실패")
    monkeypatch.setattr(updater, "post_checkout", boom)
    assert updater.cmd_update(inst, "dev", None, False, False, do_sync=False) == 1
    assert updater.rev(inst, "dev") == before
    assert updater.current_branch(inst) == "dev"


def test_rollback_returns_to_previous_commit(world):
    trust(world)
    inst = world["inst"]
    before = updater.rev(inst, "HEAD")
    updater.cmd_update(inst, "stable", None, False, False, do_sync=False)
    assert updater.cmd_rollback(inst) == 0
    assert updater.rev(inst, "HEAD") == before


def test_rollback_without_record(world):
    with pytest.raises(updater.UpdateError, match="기록이 없습니다"):
        updater.cmd_rollback(world["inst"])


# ── 훅·스킬 동기화 ──
def test_sync_skips_missing_config_dirs(world):
    assert updater.sync(world["inst"]) == 0


def test_sync_creates_skill_link_and_keeps_foreign_copy(world, tmp_path, monkeypatch):
    inst = world["inst"]
    (inst / ".claude" / "skills" / "fsh").mkdir(parents=True)
    (inst / ".claude" / "skills" / "fsh" / "SKILL.md").write_text("s")
    cfg = tmp_path / "claude"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    updater.sync(inst)
    link = cfg / "skills" / "fsh"
    assert link.is_symlink() and link.resolve() == (inst / ".claude" / "skills" / "fsh").resolve()
    link.unlink()
    link.mkdir()
    (link / "SKILL.md").write_text("mine")
    updater.sync(inst)
    assert (link / "SKILL.md").read_text() == "mine" and not link.is_symlink()


def test_sync_installs_claude_hooks_idempotently(world, tmp_path, monkeypatch):
    cfg = tmp_path / "claude"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    root = Path(updater.ROOT)
    assert updater.sync(root) == 0
    first = (cfg / "settings.json").read_text()
    assert "agent_hook.sh" in first
    assert updater.sync(root) == 0
    assert (cfg / "settings.json").read_text() == first
