"""업데이트 시험 공용 도구 — 임시 bare 원격 + 작업 리포 + 설치본(clone) + 시험용 SSH 서명 키.

실제 설정·실제 GitHub·실제 ~/.vt 는 건드리지 않는다(`isolate_env` 가 HOME·VT_STATE_DIR 을 임시로 돌린다).
test_updater.py 의 세계와 같은 모양이다(건드리지 않으려고 복제했다).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def sh(cwd, *cmd) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def keygen(path: Path) -> Path:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)], check=True)
    return path


def sign_tag(repo, name, key, ref="HEAD", signed_as=None) -> None:
    sh(repo, "git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", "-m", signed_as or name, name, ref)


def isolate_env(tmp_path, monkeypatch) -> None:
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VT_TMUX_SOCKET", "fsh-updcheck-test-" + os.urandom(6).hex())
    monkeypatch.delenv("VT_TMUX_CONF", raising=False)
    monkeypatch.delenv("VT_UPDATE_CHECK", raising=False)


def make_world(tmp_path) -> dict:
    """origin(bare) + work(원본) + inst(설치본). master=릴리스 v1.0.0·v1.1.0, 설치본은 v1.0.0, 채널 stable 이전 상태."""
    key = keygen(tmp_path / "rel")
    other = keygen(tmp_path / "evil")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "master", str(origin))
    sh(tmp_path, "git", "init", "-q", "-b", "master", str(work))
    sh(work, "git", "remote", "add", "origin", str(origin))
    (work / "VERSION").write_text("1.0.0\n")
    (work / "app.txt").write_text("app\n")          # 추적되는 일반 파일 — 「로컬 변경」 시험에 쓴다
    sh(work, "git", "add", "."); sh(work, "git", "commit", "-qm", "r1")
    sign_tag(work, "v1.0.0", key)
    (work / "VERSION").write_text("1.1.0\n")
    sh(work, "git", "commit", "-qam", "r2")
    sign_tag(work, "v1.1.0", key)
    sh(work, "git", "checkout", "-q", "-b", "dev")
    (work / "dev.txt").write_text("x")
    sh(work, "git", "add", "."); sh(work, "git", "commit", "-qm", "dev work")
    sh(work, "git", "checkout", "-q", "master")
    sh(work, "git", "push", "-q", "origin", "master", "dev", "--tags")
    inst = tmp_path / "inst"
    sh(tmp_path, "git", "clone", "-q", str(origin), str(inst))
    sh(inst, "git", "checkout", "-q", "master")
    sh(inst, "git", "reset", "-q", "--hard", "v1.0.0")
    return dict(key=key, other=other, origin=origin, work=work, inst=inst)


def release(world, version: str, key=None, *, on_master: bool = True, signed_as=None) -> str:
    """work 에서 새 릴리스 커밋을 만들고 서명 태그를 붙여 origin 에 올린다. 태그 이름을 돌려준다."""
    work = world["work"]
    sh(work, "git", "checkout", "-q", "master")
    (work / "VERSION").write_text(version + "\n")
    sh(work, "git", "commit", "-qam", f"release {version}")
    tag = "v" + version
    sign_tag(work, tag, key or world["key"], signed_as=signed_as)
    if on_master:
        sh(work, "git", "push", "-q", "origin", "master", tag)
    else:
        sh(work, "git", "push", "-q", "origin", tag)
    return tag


def trust(world, key=None) -> None:
    import updater
    pub = Path(str(key or world["key"]) + ".pub")
    assert updater.cmd_trust(world["inst"], str(pub), False, True) == 0
