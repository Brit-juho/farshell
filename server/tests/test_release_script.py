"""scripts/release.py — 5분 릴리스 스크립트.

실제 GitHub 없이 전체 흐름을 돌린다: 로컬 bare 저장소가 원격, 가짜 `gh`가 CI·Release API, 임시 SSH 키가 서명 키다.
고정하는 것: CI 가 이미 성공한 origin/dev 만 릴리스한다(진행 중·실패면 태그 없이 멈춘다), 릴리스 커밋은 메타 파일
4개만 바꾼다, dev·master·release/X.Y·태그가 한 번에 올라간다, 태그 전에 멈춘 릴리스는 이어서 할 수 있다.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="ssh-keygen 필요")


# --- 순수 함수 --------------------------------------------------------------------------------

CHANGELOG = """# Changelog

---

## [Unreleased]

### Fixed
- 무언가를 고쳤다.

---

## [1.0.0] — 2026-01-01

### Added
- 처음.
"""


def test_bump_changelog_moves_unreleased_into_new_section():
    out = rel.bump_changelog(CHANGELOG, "1.0.1", "2026-11-06")
    assert "## [Unreleased]\n\n---\n\n## [1.0.1] — 2026-11-06\n\n### Fixed\n- 무언가를 고쳤다.\n\n---\n\n## [1.0.0]" in out


def test_bump_changelog_refuses_empty_or_duplicate():
    empty = CHANGELOG.replace("### Fixed\n- 무언가를 고쳤다.\n", "")
    with pytest.raises(rel.ReleaseError, match="비어"):
        rel.bump_changelog(empty, "1.0.1", "d")
    with pytest.raises(rel.ReleaseError, match="이미"):
        rel.bump_changelog(CHANGELOG, "1.0.0", "d")


def test_bump_readme_needs_exactly_one_badge():
    assert "version-2.0.0-blue" in rel.bump_readme("![v](https://img.shields.io/badge/version-1.0.0-blue.svg)", "2.0.0")
    with pytest.raises(rel.ReleaseError):
        rel.bump_readme("no badge", "2.0.0")


def test_ci_verdict():
    assert rel.ci_verdict([]) == "missing"
    assert rel.ci_verdict([{"status": "completed", "conclusion": "failure"},
                           {"status": "completed", "conclusion": "success"}]) == "success"
    assert rel.ci_verdict([{"status": "in_progress", "conclusion": None}]) == "pending"
    assert rel.ci_verdict([{"status": "completed", "conclusion": "failure"}]) == "failure"


def test_repo_slug():
    assert rel.repo_slug("https://github.com/acme/sample-repo.git") == "acme/sample-repo"
    assert rel.repo_slug("git@github.com:acme/sample-repo.git") == "acme/sample-repo"


# --- 전체 흐름 --------------------------------------------------------------------------------

FAKE_GH = r'''#!{python}
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
origin = os.environ["FAKE_GH_ORIGIN"]
path = sys.argv[2]
def tags():
    out = subprocess.run(["git", "-C", origin, "for-each-ref", "--format=%(refname:short) %(*objectname)", "refs/tags"],
                         capture_output=True, text=True).stdout.split("\n")
    return [l.split() for l in out if l.strip()]
if "/workflows/ci.yml/runs" in path:
    sha = path.split("head_sha=")[1].split("&")[0]
    v = state.get("ci", {{}}).get(sha, state.get("ci_default", "success"))
    runs = [] if v == "missing" else [{{"status": "in_progress" if v == "pending" else "completed",
                                         "conclusion": None if v == "pending" else v}}]
    print(json.dumps({{"workflow_runs": runs}}))
elif "/workflows/release.yml/runs" in path:
    print(json.dumps({{"workflow_runs": [{{"head_branch": t, "head_sha": s, "status": "completed",
                                         "conclusion": "success", "html_url": "u"}} for t, s in tags()]}}))
elif "/releases/tags/" in path:
    v = path.rsplit("/", 1)[1].removeprefix("v")
    print(json.dumps({{"html_url": "https://example.invalid/r", "assets": [{{"name": f"farshell-{{v}}.tar.gz"}},
                                                                          {{"name": f"farshell-{{v}}.tar.gz.sha256"}}]}}))
else:
    print("{{}}")
'''


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "init", "--quiet", "-b", "dev", str(work))
    for k, v in (("user.name", "Release Bot"), ("user.email", "bot@example.invalid"), ("commit.gpgsign", "false"),
                 ("tag.gpgsign", "false")):
        _git(work, "config", k, v)
    (work / "VERSION").write_text("1.0.0\n")
    (work / "README.md").write_text("![v](https://img.shields.io/badge/version-1.0.0-blue.svg)\n")
    (work / "README.ko.md").write_text("![v](https://img.shields.io/badge/version-1.0.0-blue.svg)\n")
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "app.py").write_text("print('hi')\n")
    (work / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "release_notes.py", work / "scripts" / "release_notes.py")
    _git(work, "add", "-A")
    _git(work, "commit", "--quiet", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "--quiet", "origin", "dev", "dev:master")

    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)], check=True)
    signers = tmp_path / "signers"
    signers.write_text('farshell-release namespaces="git" ' + " ".join((tmp_path / "key.pub").read_text().split()[:2]) + "\n")

    state = tmp_path / "gh.json"
    state.write_text(json.dumps({"ci_default": "success"}))
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(0o755)
    for k, v in {"FSH_RELEASE_GH": str(gh), "FAKE_GH_STATE": str(state), "FAKE_GH_ORIGIN": str(origin),
                 "FSH_RELEASE_REPO": "acme/sample-repo", "FSH_RELEASE_SIGNING_KEY": str(key),
                 "FSH_RELEASE_SIGNERS": str(signers), "FSH_RELEASE_TODAY": "2026-11-06",
                 "FSH_RELEASE_POLL_SEC": "0", "FSH_RELEASE_WAIT_SEC": "5"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rel, "RELEASE_WAIT_SEC", 5.0)
    monkeypatch.setattr(rel, "POLL_SEC", 0.0)
    return {"work": work, "origin": origin, "state": state}


def _set_ci(repo, mapping):
    s = json.loads(repo["state"].read_text())
    s.update(mapping)
    repo["state"].write_text(json.dumps(s))


def test_release_pushes_everything_in_one_go(repo):
    assert rel.release("1.0.1", repo=repo["work"]) == 0
    o = repo["origin"]
    commit = _git(o, "rev-parse", "v1.0.1^{commit}")
    for ref in ("dev", "master", "release/1.0"):
        assert _git(o, "rev-parse", ref) == commit
    assert _git(o, "show", f"{commit}:VERSION") == "1.0.1"
    assert "## [1.0.1] — 2026-11-06" in _git(o, "show", f"{commit}:CHANGELOG.md")
    assert set(_git(o, "diff", "--name-only", f"{commit}^", commit).split()) <= rel.RELEASE_FILES
    assert _git(o, "log", "-1", "--format=%s", commit) == "chore(release): v1.0.1"
    assert _git(o, "for-each-ref", "--format=%(contents:subject)", "refs/tags/v1.0.1") == "FarShell v1.0.1"
    # 사용자 작업 트리는 그대로다(임시 worktree 에서 만들었다)
    assert (repo["work"] / "VERSION").read_text() == "1.0.0\n"
    assert _git(repo["work"], "worktree", "list").count("\n") == 0


@pytest.mark.parametrize("verdict", ["pending", "failure", "missing"])
def test_release_stops_without_tag_when_ci_not_green(repo, verdict):
    _set_ci(repo, {"ci_default": verdict})
    with pytest.raises(rel.ReleaseError):
        rel.release("1.0.1", repo=repo["work"])
    assert not _git(repo["work"], "tag", "-l", "v1.0.1")
    assert not _git(repo["origin"], "tag", "-l")
    assert _git(repo["origin"], "show", "master:VERSION") == "1.0.0"


def test_dry_run_changes_nothing(repo):
    assert rel.release("1.0.1", repo=repo["work"], dry_run=True) == 0
    assert not _git(repo["origin"], "tag", "-l")
    assert _git(repo["origin"], "show", "dev:VERSION") == "1.0.0"


def test_resume_tags_an_already_pushed_release_commit(repo):
    """릴리스 커밋은 올라갔는데 태그 전에 멈춘 경우(2026-11-06 v2.8.1) — 새 커밋 없이 태그만 이어서 단다.
    그 커밋 자신의 CI 가 없어도(릴리스 커밋은 CI 를 건너뛴다) 부모가 성공이면 된다."""
    w = repo["work"]
    (w / "VERSION").write_text("1.0.1\n")
    (w / "CHANGELOG.md").write_text(rel.bump_changelog(CHANGELOG, "1.0.1", "2026-11-06"))
    _git(w, "commit", "--quiet", "-am", "chore(release): v1.0.1")
    _git(w, "push", "--quiet", "origin", "dev")
    tip = _git(w, "rev-parse", "HEAD")
    _set_ci(repo, {"ci": {tip: "missing"}})
    assert rel.release("1.0.1", repo=w) == 0
    assert _git(repo["origin"], "rev-parse", "v1.0.1^{commit}") == tip


def test_resume_refuses_release_commit_that_touches_code(repo):
    w = repo["work"]
    (w / "VERSION").write_text("1.0.1\n")
    (w / "app.py").write_text("print('sneaky')\n")
    _git(w, "commit", "--quiet", "-am", "chore(release): v1.0.1")
    _git(w, "push", "--quiet", "origin", "dev")
    with pytest.raises(rel.ReleaseError, match="메타 파일 밖"):
        rel.release("1.0.1", repo=w)


def test_refuses_non_increasing_version_and_existing_tag(repo):
    with pytest.raises(rel.ReleaseError, match="커야"):
        rel.release("1.0.0", repo=repo["work"])
    assert rel.release("1.0.1", repo=repo["work"]) == 0
    with pytest.raises(rel.ReleaseError, match="이미"):
        rel.release("1.0.1", repo=repo["work"])


def test_refuses_when_master_is_not_an_ancestor(repo):
    w = repo["work"]
    _git(w, "checkout", "--quiet", "-b", "hotfix", "origin/master")
    (w / "app.py").write_text("print('hotfix only on master')\n")
    _git(w, "commit", "--quiet", "-am", "fix on master")
    _git(w, "push", "--quiet", "origin", "hotfix:master")
    with pytest.raises(rel.ReleaseError, match="조상"):
        rel.release("1.0.1", repo=w)
    assert not _git(repo["origin"], "tag", "-l")
