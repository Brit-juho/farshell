#!/usr/bin/env python3
"""FarShell 릴리스 — 한 번 실행으로 끝낸다(목표 5분 이내).

    python3 scripts/release.py 2.8.2            # 릴리스
    python3 scripts/release.py 2.8.2 --dry-run  # 무엇을 할지만 보여 주고 아무것도 올리지 않는다

왜 이렇게 하는가(2026-11-06): 예전 절차는 릴리스 커밋을 dev 에 올리고, CI(약 8분)를 기다리고, master 로 옮겨
CI 를 **한 번 더** 기다린 뒤 태그를 찍었다. 같은 코드를 두 번 검증하느라 16분 넘게 걸렸고, 두 번째 CI 에서
흔들리는 테스트가 빨개지면 릴리스가 멈췄다. 이제는

1. 기준은 항상 `origin/dev` 끝 커밋이다. 그 커밋의 CI(`ci.yml`)가 **이미 성공**했어야 한다 — 새로 기다리지 않는다.
   진행 중이거나 실패면 바로 멈춘다(그건 릴리스가 아니라 CI 를 고칠 일이다).
2. 릴리스 커밋은 임시 worktree 에서 만든다(사용자의 작업 트리·로컬 브랜치를 건드리지 않는다). 바꾸는 파일은
   VERSION·README 배지 둘·CHANGELOG 뿐이고, 정말 그것만 바뀌었는지 검사한다 — 코드가 같으니 부모의 CI 결과가 그대로
   유효하다. 기준 커밋이 이미 그 버전의 릴리스 커밋이면(이전 시도가 태그 전에 멈춘 경우) 새 커밋 없이 이어 간다.
3. 서명 태그(`FarShell vX.Y.Z`, SSH)를 찍고 `fsh update` 와 같은 방법(`~/.vt/update-signers`)으로 검증한다.
4. dev·master·태그·release/X.Y 를 **원자적 push 한 번**으로 올린다(전부 되거나 전부 안 된다).
5. 태그로 도는 release.yml(약 40초)이 끝나고 GitHub Release 에 tarball·sha256 이 붙었는지 확인한다.

릴리스 커밋만 올라간 push 는 ci.yml 이 건너뛰고, 대신 release.yml 이 "부모 커밋 CI 성공 + 메타 파일만 변경"을
다시 확인한다(누가 손으로 태그를 찍어도 검증 안 된 코드가 나가지 않게).

테스트·대역: `FSH_RELEASE_GH`(gh 대신 쓸 실행 파일), `FSH_RELEASE_SIGNERS`(신뢰 키 파일),
`FSH_RELEASE_SIGNING_KEY`(서명 공개 키 경로), `FSH_RELEASE_TODAY`(날짜 고정). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RELEASE_FILES = frozenset({"VERSION", "README.md", "README.ko.md", "CHANGELOG.md"})
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
BADGE_RE = re.compile(r"(badge/version-)(\d+\.\d+\.\d+)(-blue\.svg)")
CI_WORKFLOW = "ci.yml"
RELEASE_WORKFLOW = "release.yml"
RELEASE_WAIT_SEC = float(os.environ.get("FSH_RELEASE_WAIT_SEC", "240"))
POLL_SEC = float(os.environ.get("FSH_RELEASE_POLL_SEC", "5"))
CMD_TIMEOUT = 120


class ReleaseError(RuntimeError):
    pass


# --- 순수 함수(테스트 대상) ----------------------------------------------------------------

def parse_version(text: str) -> tuple[int, int, int]:
    m = VERSION_RE.match(text.strip().removeprefix("v"))
    if not m:
        raise ReleaseError(f"버전 형식이 X.Y.Z 가 아닙니다: {text!r}")
    return int(m[1]), int(m[2]), int(m[3])


def release_subject(version: str) -> str:
    return f"chore(release): v{version}"


def bump_readme(text: str, new: str) -> str:
    out, n = BADGE_RE.subn(lambda m: f"{m[1]}{new}{m[3]}", text)
    if n != 1:
        raise ReleaseError(f"README 의 버전 배지를 정확히 하나 찾지 못했습니다(찾은 수 {n})")
    return out


def bump_changelog(text: str, version: str, date: str) -> str:
    """`## [Unreleased]` 의 내용을 `## [X.Y.Z] — 날짜` 절로 옮기고 빈 `[Unreleased]` 를 남긴다.
    절은 `\\n---\\n` 로 나뉜다(이 저장소의 형식). 옮길 내용이 없으면 거부한다."""
    head = "## [Unreleased]"
    i = text.find(head)
    if i < 0:
        raise ReleaseError("CHANGELOG 에 '## [Unreleased]' 절이 없습니다")
    body_start = i + len(head)
    sep = text.find("\n---\n", body_start)
    if sep < 0:
        raise ReleaseError("CHANGELOG 의 [Unreleased] 절 끝(---)을 찾지 못했습니다")
    body = text[body_start:sep].strip()
    if not body:
        raise ReleaseError("CHANGELOG [Unreleased] 가 비어 있습니다 — 릴리스할 변경 내용을 먼저 적으세요")
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
        raise ReleaseError(f"CHANGELOG 에 이미 [{version}] 절이 있습니다")
    return (text[:i] + f"{head}\n\n---\n\n## [{version}] — {date}\n\n{body}\n" + text[sep:])


def ci_verdict(runs: list[dict]) -> str:
    """한 커밋의 ci.yml 실행 목록 → success / pending / failure / missing.
    같은 트리가 여러 번(dev push·master push·재실행) 돌 수 있다 — 하나라도 끝까지 성공했으면 검증된 것이다."""
    if not runs:
        return "missing"
    if any(r.get("status") == "completed" and r.get("conclusion") == "success" for r in runs):
        return "success"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    return "failure"


def repo_slug(remote_url: str) -> str:
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", remote_url.strip())
    if not m:
        raise ReleaseError(f"GitHub 원격 주소가 아닙니다: {remote_url}")
    return m[1]


# --- 외부 명령 -------------------------------------------------------------------------------

def run(args: list[str], cwd: Path, *, check: bool = True, timeout: float = CMD_TIMEOUT,
        env: dict | None = None) -> str:
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        raise ReleaseError(f"명령이 {timeout:g}초 안에 끝나지 않았습니다: {' '.join(args[:4])}") from None
    if check and p.returncode != 0:
        raise ReleaseError(f"명령 실패({p.returncode}): {' '.join(args[:6])}\n{(p.stderr or p.stdout).strip()}")
    return p.stdout


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd, check=check).strip()


def gh_bin() -> str:
    return os.environ.get("FSH_RELEASE_GH") or shutil.which("gh") or "gh"


def gh_api(cwd: Path, path: str) -> dict:
    return json.loads(run([gh_bin(), "api", path], cwd) or "{}")


def ci_runs(cwd: Path, slug: str, sha: str) -> list[dict]:
    data = gh_api(cwd, f"repos/{slug}/actions/workflows/{CI_WORKFLOW}/runs?head_sha={sha}&per_page=30")
    return list(data.get("workflow_runs") or [])


def signers_file() -> Path:
    env = os.environ.get("FSH_RELEASE_SIGNERS")
    return Path(env) if env else Path(os.environ.get("VT_STATE_DIR") or Path.home() / ".vt") / "update-signers"


def signing_key(cwd: Path) -> str:
    """서명에 쓸 공개 키 경로. 지정이 없으면 ~/.ssh/*.pub 중 신뢰 키 파일(update-signers)에 있는 것을 고른다 —
    저장소에 개인 키 이름을 적지 않기 위해서다(계약 7)."""
    env = os.environ.get("FSH_RELEASE_SIGNING_KEY") or git(cwd, "config", "--get", "user.signingkey", check=False)
    if env:
        return env
    try:
        trusted = {ln.split()[-1] for ln in signers_file().read_text().splitlines() if ln.strip()}
    except OSError:
        raise ReleaseError(f"신뢰 키 파일이 없습니다: {signers_file()} — 'fsh update trust' 로 먼저 만드세요") from None
    for pub in sorted((Path.home() / ".ssh").glob("*.pub")):
        try:
            parts = pub.read_text().split()
        except OSError:
            continue
        if len(parts) >= 2 and parts[1] in trusted:
            return str(pub)
    raise ReleaseError("신뢰 키(update-signers)와 맞는 ~/.ssh/*.pub 가 없습니다 — FSH_RELEASE_SIGNING_KEY 로 지정하세요")


# --- 본 흐름 --------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t0 = time.monotonic()
        self.last = self.t0

    def step(self, name: str) -> None:
        now = time.monotonic()
        print(f"  ✓ {name}  ({now - self.last:.1f}s, 누적 {now - self.t0:.1f}s)", flush=True)
        self.last = now

    def total(self) -> float:
        return time.monotonic() - self.t0


def wait_release(cwd: Path, slug: str, tag: str, sha: str, clock: Clock) -> str:
    deadline = time.monotonic() + RELEASE_WAIT_SEC
    run_info = None
    while time.monotonic() < deadline:
        data = gh_api(cwd, f"repos/{slug}/actions/workflows/{RELEASE_WORKFLOW}/runs?event=push&per_page=10")
        mine = [r for r in data.get("workflow_runs") or [] if r.get("head_sha") == sha and r.get("head_branch") == tag]
        if mine:
            run_info = mine[0]
            if run_info.get("status") == "completed":
                break
        time.sleep(POLL_SEC)
    if not run_info:
        raise ReleaseError(f"{RELEASE_WAIT_SEC:g}초 안에 release.yml 실행이 보이지 않습니다 — Actions 탭을 확인하세요")
    if run_info.get("status") != "completed":
        raise ReleaseError(f"release.yml 이 {RELEASE_WAIT_SEC:g}초 안에 끝나지 않았습니다: {run_info.get('html_url')}")
    if run_info.get("conclusion") != "success":
        raise ReleaseError(f"release.yml 실패({run_info.get('conclusion')}): {run_info.get('html_url')}")
    clock.step("release.yml 완료")
    rel = gh_api(cwd, f"repos/{slug}/releases/tags/{tag}")
    names = {a.get("name") for a in rel.get("assets") or []}
    version = tag.removeprefix("v")
    need = {f"farshell-{version}.tar.gz", f"farshell-{version}.tar.gz.sha256"}
    if not need <= names:
        raise ReleaseError(f"GitHub Release 에 자산이 빠졌습니다: {sorted(need - names)}")
    clock.step("GitHub Release 자산 확인")
    return rel.get("html_url", "")


def release(version: str, *, remote: str = "origin", dry_run: bool = False, repo: Path | None = None) -> int:
    clock = Clock()
    parse_version(version)
    tag = f"v{version}"
    minor = ".".join(version.split(".")[:2])
    root = Path(repo or git(Path.cwd(), "rev-parse", "--show-toplevel"))
    slug = os.environ.get("FSH_RELEASE_REPO") or repo_slug(git(root, "remote", "get-url", remote))
    print(f"▸ FarShell {tag} 릴리스 ({slug}){' — dry-run' if dry_run else ''}")

    git(root, "fetch", "--quiet", "--tags", remote)
    base = git(root, "rev-parse", f"{remote}/dev")
    if git(root, "ls-remote", "--tags", remote, f"refs/tags/{tag}"):
        raise ReleaseError(f"{tag} 태그가 이미 원격에 있습니다")
    if git(root, "tag", "-l", tag):
        raise ReleaseError(f"{tag} 태그가 로컬에 이미 있습니다 — 이전 시도의 흔적이면 'git tag -d {tag}' 후 다시 실행하세요")
    clock.step(f"원격 확인 (기준 {remote}/dev = {base[:7]})")

    base_version = git(root, "show", f"{base}:VERSION")
    resume = (base_version == version and git(root, "log", "-1", "--format=%s", base) == release_subject(version))
    if not resume and parse_version(version) <= parse_version(base_version):
        raise ReleaseError(f"새 버전 {version} 이 현재 {base_version} 보다 커야 합니다")

    # CI 확인 — 새로 기다리지 않는다.
    gate = base
    if resume:
        changed = set(git(root, "diff", "--name-only", f"{base}^", base).split())
        if not changed <= RELEASE_FILES:
            raise ReleaseError(f"기준 릴리스 커밋이 메타 파일 밖을 바꿨습니다: {sorted(changed - RELEASE_FILES)}")
        verdicts = {s: ci_verdict(ci_runs(root, slug, s)) for s in (base, git(root, "rev-parse", f"{base}^"))}
        verdict = "success" if "success" in verdicts.values() else max(verdicts.values(), key=["missing", "failure", "pending"].index)
        gate = next((s for s, v in verdicts.items() if v == "success"), base)
    else:
        verdict = ci_verdict(ci_runs(root, slug, base))
    if verdict != "success":
        hint = {"pending": "CI 가 아직 도는 중입니다 — 끝난 뒤 다시 실행하세요",
                "failure": "CI 가 실패했습니다 — 고친 뒤 dev 에 올리고 다시 실행하세요",
                "missing": "이 커밋의 CI 실행이 없습니다 — dev 에 push 해 CI 를 먼저 돌리세요"}[verdict]
        raise ReleaseError(f"{gate[:7]}: {hint}")
    clock.step(f"CI 성공 확인 ({gate[:7]})")

    # 빠른 병합 가능 여부(원자적 push 가 거절되기 전에 이유를 알려 준다)
    for branch in ("master", f"release/{minor}"):
        ref = f"{remote}/{branch}"
        if git(root, "rev-parse", "--verify", "--quiet", ref, check=False):
            if subprocess.run(["git", "merge-base", "--is-ancestor", ref, base], cwd=root).returncode != 0:
                raise ReleaseError(f"{ref} 가 {remote}/dev 의 조상이 아닙니다 — 빠른 병합이 안 됩니다(긴급 수정이 그 브랜치에만 있는지 확인)")

    work = Path(tempfile.mkdtemp(prefix="fsh-release-"))
    tag_made = False
    try:
        git(root, "worktree", "add", "--quiet", "--detach", str(work), base)
        if resume:
            commit = base
            print(f"  ⓘ {base[:7]} 이 이미 {tag} 릴리스 커밋이다 — 새 커밋 없이 이어 간다")
        else:
            today = os.environ.get("FSH_RELEASE_TODAY") or time.strftime("%Y-%m-%d")
            (work / "VERSION").write_text(version + "\n")
            for name in ("README.md", "README.ko.md"):
                p = work / name
                p.write_text(bump_readme(p.read_text(encoding="utf-8"), version), encoding="utf-8")
            cl = work / "CHANGELOG.md"
            cl.write_text(bump_changelog(cl.read_text(encoding="utf-8"), version, today), encoding="utf-8")
            git(work, "add", *sorted(RELEASE_FILES))
            git(work, "commit", "--quiet", "-m", release_subject(version), "-m",
                f"CHANGELOG [{version}] 절이 릴리스 노트의 정본이다. scripts/release.py 로 만들었다.")
            commit = git(work, "rev-parse", "HEAD")
            changed = set(git(work, "diff", "--name-only", base, commit).split())
            if not changed <= RELEASE_FILES:
                raise ReleaseError(f"릴리스 커밋이 메타 파일 밖을 바꿨습니다: {sorted(changed - RELEASE_FILES)}")
        notes = run([sys.executable, "scripts/release_notes.py", version], work)
        if not notes.strip():
            raise ReleaseError("CHANGELOG 에서 릴리스 노트를 뽑지 못했습니다")
        clock.step(f"릴리스 커밋 준비 ({commit[:7]})")

        if dry_run:
            print(f"  ⓘ dry-run — 여기서 멈춘다. 올릴 것: dev·master·release/{minor} → {commit[:7]}, 태그 {tag}")
            return 0

        key = signing_key(root)
        run(["git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", tag, "-m",
             f"FarShell {tag}", commit], root)
        tag_made = True
        run(["git", "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers_file()}",
             "verify-tag", tag], root)
        clock.step(f"서명 태그 {tag} 생성·검증")

        git(root, "push", "--atomic", "--quiet", remote,
            f"{commit}:refs/heads/dev", f"{commit}:refs/heads/master",
            f"{commit}:refs/heads/release/{minor}", f"refs/tags/{tag}")
        clock.step("원자적 push (dev·master·release·태그)")

        url = wait_release(root, slug, tag, commit, clock)
        git(root, "fetch", "--quiet", remote)
        total = clock.total()
        print(f"✓ {tag} 릴리스 완료 — {total:.0f}초 ({total / 60:.1f}분)  {url}")
        if total > 300:
            print("  ⚠ 5분을 넘겼다 — 위 단계별 시간에서 늦은 곳을 확인하세요")
        local = git(root, "rev-parse", "--verify", "--quiet", "refs/heads/dev", check=False)
        if local and local != commit:
            print(f"  ⓘ 로컬 dev 는 {local[:7]} 그대로다 — 필요하면 'git pull --ff-only {remote} dev'")
        return 0
    except BaseException:
        if tag_made and not git(root, "ls-remote", "--tags", remote, f"refs/tags/{tag}", check=False):
            git(root, "tag", "-d", tag, check=False)       # 안 올라간 태그는 지워 다시 실행할 수 있게 한다
        raise
    finally:
        git(root, "worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FarShell 릴리스 (CI 가 이미 성공한 origin/dev 를 릴리스한다)")
    ap.add_argument("version", help="X.Y.Z")
    ap.add_argument("--dry-run", action="store_true", help="아무것도 올리지 않고 확인만 한다")
    ap.add_argument("--remote", default="origin")
    args = ap.parse_args(argv)
    try:
        return release(args.version, remote=args.remote, dry_run=args.dry_run)
    except ReleaseError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
