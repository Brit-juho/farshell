"""`fsh update` / `fsh sync` — 채널별 자기 업데이트와 훅·스킬 동기화.

채널
  stable   서명 검증을 통과한 최신 `vX.Y.Z` 태그(분리된 HEAD로 체크아웃)
  dev      `origin/dev` 최신(빨리 감기만)
  devmode  자동 업데이트 없음 — 직접 pull 하는 개발용. `--check`만 동작한다

설계에서 지키는 것
1. 로컬 변경이 있으면 시작하지 않는다(추적 파일 기준). 빌드가 실패하면 원래 커밋으로 돌아간다.
2. stable 은 **태그 서명**을 본다. 신뢰할 서명 키는 리포 밖(`~/.vt/update-signers`)에만 둔다 —
   리포가 침해돼도 공격자가 신뢰 키를 바꾸지 못하게 하려는 것이다. 처음 한 번 `fsh update trust`로
   지문을 사람이 확인해 고정한다(TOFU). 서명된 태그 객체는 이름도 같이 서명하므로
   「태그 이름 == 서명된 이름」을 확인해, 옛 서명 태그를 새 이름에 붙여 되돌리는 공격을 막는다.
3. 훅·스킬·tmux 설정은 새 코드로 옮긴 **뒤** 맞춘다(`sync`). 등록기 쪽 원칙(백업·멱등·남의 훅 보존)은
   `claude_hooks.py`/`codex_hooks.py`를 그대로 부른다 — 여기서 설정 파일을 직접 쓰지 않는다.
4. 서버는 자동으로 재시작하지 않는다(열린 세션이 끊긴다). `--restart`를 줘야 한다.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import config_io
from i18n import tr

ROOT = Path(__file__).resolve().parent.parent
CHANNELS = ("stable", "dev", "devmode")
PRINCIPAL = "farshell-release"
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_SIG_RE = re.compile(r"-----BEGIN SSH SIGNATURE-----\n(.*?)\n-----END SSH SIGNATURE-----", re.S)

# 코드를 옮긴 뒤 프런트엔드를 다시 빌드해야 하는 경로
_FRONTEND_PATHS = ("frontend/", "styles/", "package.json", "package-lock.json",
                   "vite.config.js", "tsconfig.json")


class UpdateError(Exception):
    """사람에게 그대로 보여 줄 실패. 메시지는 이미 번역돼 있다."""


# ── 상태 파일 ─────────────────────────────────────────────────────────────
def state_dir() -> Path:
    d = (os.environ.get("VT_STATE_DIR") or "").strip()
    if d:
        return Path(os.path.expanduser(d))
    return Path(os.environ.get("HOME") or os.path.expanduser("~")) / ".vt"


def _channel_file() -> Path:
    return state_dir() / "update-channel"


def _prev_file() -> Path:
    return state_dir() / "update-prev.json"


def signers_file() -> Path:
    return state_dir() / "update-signers"


def read_channel() -> str | None:
    try:
        v = _channel_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if v in CHANNELS else None


def write_channel(channel: str) -> None:
    config_io.atomic_write(_channel_file(), channel + "\n")


# ── git ───────────────────────────────────────────────────────────────────
def _git_env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # 자격 증명을 묻느라 멈추지 않게
    env["LC_ALL"] = "C"
    return env


def git(root: Path, *args: str, check: bool = True, timeout: int = 120,
        config: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(root)]
    for c in config:
        cmd += ["-c", c]
    cmd += list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_git_env())
    except FileNotFoundError:
        raise UpdateError(tr("git 을 찾을 수 없습니다."))
    except subprocess.TimeoutExpired:
        raise UpdateError(tr("git {cmd} 이(가) {sec}초 안에 끝나지 않았습니다.", cmd=args[0], sec=timeout))
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout).strip().splitlines()
        raise UpdateError(tr("git {cmd} 실패: {detail}", cmd=args[0], detail=detail[-1] if detail else p.returncode))
    return p


def rev(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}").stdout.strip()


def current_branch(root: Path) -> str | None:
    p = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def version_of(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def dirty_files(root: Path) -> list[str]:
    out = git(root, "status", "--porcelain", "--untracked-files=no").stdout
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


def release_tags(root: Path) -> list[str]:
    names = git(root, "tag", "--list", "v*").stdout.split()
    tags = [n for n in names if _TAG_RE.match(n)]
    return sorted(tags, key=lambda n: tuple(int(x) for x in _TAG_RE.match(n).groups()))


# ── 태그 서명 ─────────────────────────────────────────────────────────────
def _read_string(buf: bytes, pos: int) -> tuple[bytes, int]:
    (n,) = struct.unpack_from(">I", buf, pos)
    pos += 4
    if pos + n > len(buf):
        raise ValueError("truncated")
    return buf[pos:pos + n], pos + n


def fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def signer_of_tag(root: Path, tag: str) -> tuple[str, str, str]:
    """태그 객체의 SSH 서명에 들어 있는 공개 키 → (키 종류, base64 blob, 지문)."""
    p = git(root, "cat-file", "tag", tag, check=False)
    if p.returncode != 0:
        raise UpdateError(tr("{tag} 은(는) 주석 태그가 아닙니다 — 서명할 수 없는 태그입니다.", tag=tag))
    m = _SIG_RE.search(p.stdout)
    if not m:
        raise UpdateError(tr("{tag} 에 서명이 없습니다.", tag=tag))
    try:
        raw = base64.b64decode("".join(m.group(1).split()), validate=True)
        if raw[:6] != b"SSHSIG":
            raise ValueError("magic")
        blob, _ = _read_string(raw, 6 + 4)  # magic(6) + version(4) 다음이 공개 키
        ktype, _ = _read_string(blob, 0)
    except (ValueError, struct.error):
        raise UpdateError(tr("{tag} 의 서명 형식을 읽을 수 없습니다 (SSH 서명이 아닙니다).", tag=tag))
    return ktype.decode("ascii", "replace"), base64.b64encode(blob).decode(), fingerprint(blob)


def _signed_name(root: Path, tag: str) -> str:
    """서명된 태그 객체 본문의 `tag <이름>` 줄."""
    out = git(root, "cat-file", "tag", tag).stdout
    for line in out.splitlines():
        if line.startswith("tag "):
            return line[4:].strip()
        if not line:
            break
    return ""


def trusted_keys() -> list[tuple[str, str]]:
    """신뢰 파일의 (키 종류, base64) 목록."""
    try:
        text = signers_file().read_text(encoding="utf-8")
    except OSError:
        return []
    keys = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == PRINCIPAL:
            keys.append((parts[-2], parts[-1]))
    return keys


def verify_tag(root: Path, tag: str) -> str:
    """서명이 신뢰 키와 맞고 태그 이름이 서명 안의 이름과 같을 때만 통과. 지문을 돌려준다."""
    if not trusted_keys():
        raise UpdateError(tr("신뢰하는 서명 키가 없습니다 — 먼저 'fsh update trust'로 릴리스 서명 키를 고정하세요."))
    if not shutil.which("ssh-keygen"):
        raise UpdateError(tr("ssh-keygen 이 필요합니다 — 서명 검증에 씁니다."))
    _, _, fp = signer_of_tag(root, tag)
    cfg = ("gpg.format=ssh", f"gpg.ssh.allowedSignersFile={signers_file()}")
    p = git(root, "verify-tag", tag, check=False, config=cfg)
    if p.returncode != 0:
        raise UpdateError(tr("{tag} 의 서명이 신뢰하는 키와 맞지 않습니다 ({fp}) — 업데이트를 거부합니다.", tag=tag, fp=fp))
    if _signed_name(root, tag) != tag:
        raise UpdateError(tr("{tag} 이(가) 다른 이름으로 서명된 태그를 가리킵니다 — 되돌리기 공격일 수 있어 거부합니다.", tag=tag))
    return fp


def _write_signers(keys: list[tuple[str, str]]) -> None:
    lines = [f'{PRINCIPAL} namespaces="git" {kt} {b64}' for kt, b64 in keys]
    config_io.atomic_write(signers_file(), "\n".join(lines) + "\n")


def _parse_pubkey_file(path: Path) -> tuple[str, str, str]:
    try:
        parts = path.read_text(encoding="utf-8").split()
        ktype, b64 = parts[0], parts[1]
        blob = base64.b64decode(b64, validate=True)
        name, _ = _read_string(blob, 0)
        if name.decode("ascii", "replace") != ktype:
            raise ValueError("type")
    except (OSError, IndexError, ValueError, struct.error):
        raise UpdateError(tr("{path} 은(는) SSH 공개 키(.pub) 파일이 아닙니다.", path=path))
    return ktype, b64, fingerprint(blob)


def cmd_trust(root: Path, key: str | None, replace: bool, yes: bool) -> int:
    if key:
        ktype, b64, fp = _parse_pubkey_file(Path(key).expanduser())
        origin = tr("지정한 공개 키 파일")
    else:
        fetch(root)
        tags = release_tags(root)
        signed = []
        for t in reversed(tags):
            try:
                signed.append((t, *signer_of_tag(root, t)))
                break
            except UpdateError:
                continue
        if not signed:
            raise UpdateError(tr("서명된 릴리스 태그가 아직 없습니다. 릴리스 서명 공개 키 파일을 --key 로 지정하세요."))
        tag, ktype, b64, fp = signed[0]
        origin = tr("{tag} 태그의 서명", tag=tag)
    existing = trusted_keys()
    if existing and (ktype, b64) in existing:
        print(tr("  ✓ 이미 신뢰하는 키입니다 ({fp})", fp=fp))
        return 0
    if existing and not replace:
        raise UpdateError(tr("이미 다른 키를 신뢰하고 있습니다. 교체하려면 --replace 를 주세요."))
    print(tr("  서명 키 ({origin})", origin=origin))
    print(f"    {ktype}  {fp}")
    print(tr("  ⓘ 이 지문이 릴리스에 서명하는 본인 키의 지문(ssh-keygen -lf <키>.pub)과 같은지 확인하세요."))
    if not yes:
        if not sys.stdin.isatty():
            raise UpdateError(tr("확인이 필요합니다 — 터미널에서 실행하거나 지문을 확인한 뒤 --yes 를 주세요."))
        if input(tr("  이 키를 릴리스 서명 키로 신뢰할까요? [y/N] ")).strip().lower() != "y":
            print(tr("  취소했습니다."))
            return 1
    _write_signers([(ktype, b64)])
    print(tr("  ✓ 신뢰 키를 저장했습니다 ({path})", path=signers_file()))
    return 0


# ── 대상 정하기 ───────────────────────────────────────────────────────────
def fetch(root: Path) -> None:
    if git(root, "remote", "get-url", "origin", check=False).returncode != 0:
        raise UpdateError(tr("origin 원격이 없습니다 — git 으로 받은 설치만 업데이트할 수 있습니다."))
    git(root, "fetch", "--quiet", "--tags", "origin", timeout=180)


def resolve_target(root: Path, channel: str, to_tag: str | None) -> tuple[str, str]:
    """(사람이 읽는 이름, 커밋 sha). stable/--to 는 서명 검증까지 끝낸다."""
    if to_tag or channel == "stable":
        if to_tag:
            if not _TAG_RE.match(to_tag):
                raise UpdateError(tr("--to 는 vX.Y.Z 형식의 태그여야 합니다."))
            tag = to_tag
        else:
            tags = release_tags(root)
            if not tags:
                raise UpdateError(tr("릴리스 태그(vX.Y.Z)가 없습니다."))
            tag = tags[-1]
        if git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode != 0:
            raise UpdateError(tr("{tag} 태그가 없습니다.", tag=tag))
        verify_tag(root, tag)
        sha = rev(root, f"refs/tags/{tag}")
        master = git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/master", check=False)
        if master.returncode == 0 and git(root, "merge-base", "--is-ancestor", sha, master.stdout.strip(),
                                          check=False).returncode != 0:
            raise UpdateError(tr("{tag} 이(가) origin/master 의 이력에 없습니다 — 릴리스가 아니어서 거부합니다.", tag=tag))
        return tag, sha
    # dev
    p = git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/dev^{commit}", check=False)
    if p.returncode != 0:
        raise UpdateError(tr("origin/dev 가 없습니다."))
    sha = p.stdout.strip()
    local = git(root, "rev-parse", "--verify", "--quiet", "refs/heads/dev^{commit}", check=False)
    if local.returncode == 0 and git(root, "merge-base", "--is-ancestor", local.stdout.strip(), sha,
                                     check=False).returncode != 0:
        raise UpdateError(tr("로컬 dev 가 origin/dev 와 갈라졌습니다 — 빨리 감기로 올릴 수 없어 중단합니다."))
    return "dev", sha


# ── 코드를 옮긴 뒤의 단계 ──────────────────────────────────────────────────
def _python(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _run(cmd: list[str], cwd: Path, step: str) -> None:
    print(f"  · {step}")
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise UpdateError(tr("{step}: {cmd} 을(를) 찾을 수 없습니다.", step=step, cmd=cmd[0]))
    except subprocess.TimeoutExpired:
        raise UpdateError(tr("{step}: 시간 초과", step=step))
    if p.returncode != 0:
        tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-6:])
        raise UpdateError(tr("{step} 실패:", step=step) + "\n" + tail)


def post_checkout(root: Path, old: str, new: str) -> None:
    """바뀐 파일을 보고 필요한 의존성 설치·빌드만 한다."""
    changed = set(git(root, "diff", "--name-only", old, new).stdout.split())
    py = _python(root)
    # 파이썬 의존성은 해시 잠금 파일(locks/)로만 설치한다(`--require-hashes`). requirements-*.txt 는 잠금의 입력일 뿐이다.
    voice_installed = subprocess.run([py, "-m", "pip", "show", "edge-tts"], capture_output=True).returncode == 0
    if "locks/core.txt" in changed or (voice_installed and "locks/voice.txt" in changed):
        # 음성이 깔려 있으면 core 와 voice 를 한 번에 맞춘다 — 같은 패키지가 서로 다른 버전이 되지 않게.
        files = ["locks/core.txt"] + (["locks/voice.txt"] if voice_installed else [])
        cmd = [py, "-m", "pip", "install", "--quiet", "--require-hashes"]
        for f in files:
            cmd += ["-r", f]
        _run(cmd, root, tr("파이썬 의존성 설치") + (" + " + tr("음성 의존성 설치") if voice_installed else ""))
    need_build = (not (root / "frontend" / "dist").exists()) or any(
        c == p or (p.endswith("/") and c.startswith(p)) for c in changed for p in _FRONTEND_PATHS)
    if need_build:
        if "package-lock.json" in changed or not (root / "node_modules").exists():
            _run(["npm", "ci", "--silent"], root, tr("프런트엔드 의존성 설치"))
        _run(["npm", "run", "build", "--silent"], root, tr("프런트엔드 빌드"))


def _checkout(root: Path, ref: str, detach: bool = True) -> None:
    git(root, "checkout", "-q", *(["--detach"] if detach else []), ref)


def _save_prev(root: Path) -> dict:
    prev = {"sha": rev(root, "HEAD"), "branch": current_branch(root), "version": version_of(root)}
    config_io.atomic_write(_prev_file(), json.dumps(prev) + "\n")
    return prev


def _restore(root: Path, prev: dict) -> None:
    try:
        if prev.get("branch"):
            _checkout(root, prev["branch"], detach=False)
        else:
            _checkout(root, prev["sha"])
    except UpdateError as e:
        print(tr("  ✗ 원래 위치로 복구하지 못했습니다: {err}", err=e))
        print(tr("    직접 복구: git checkout {sha}", sha=prev["sha"]))


# ── 훅·스킬 동기화 ─────────────────────────────────────────────────────────
def sync(root: Path, dry_run: bool = False) -> int:
    """Claude Code·Codex 훅을 현재 코드에 맞추고 전역 `fsh` 스킬 링크·tmux 설정을 확인한다."""
    py = _python(root)
    rc = 0
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    codex_dir = Path(os.environ.get("VT_CODEX_HOME") or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    action = "status" if dry_run else "install"
    for label, cfg, script in (("Claude Code", claude_dir, "claude_hooks.py"),
                               ("Codex", codex_dir, "codex_hooks.py")):
        if not cfg.is_dir():
            print(tr("  ⓘ {label}: 설정 폴더가 없어 건너뜁니다", label=label))
            continue
        p = subprocess.run([py, str(root / "server" / script), action], capture_output=True, text=True)
        out = p.stdout.strip()
        if dry_run:
            pending = [ln for ln in out.splitlines() if "\t" in ln and ln.split("\t")[1] in ("add", "update")]
            print(f"  {'⚠' if pending else '✓'} {label}: " +
                  (tr("{n}개 훅 갱신 필요", n=len(pending)) if pending else tr("훅 최신")))
        elif p.returncode != 0:
            rc = 1
            print(f"  ✗ {label}: " + tr("훅 갱신 실패 — 'fsh hooks install' 로 다시 시도하세요"))
            if out:
                print("    " + out.replace("\n", "\n    "))
        else:
            print(f"  ✓ {label}: " + tr("훅 최신"))
    return rc | _sync_skill(root, claude_dir, dry_run) | _sync_tmux_conf(root, py, dry_run)


def _sync_tmux_conf(root: Path, py: str, dry_run: bool) -> int:
    """옛 ~/.config/vt/tmux.conf 사본을 치우고 떠 있는 tmux 에 레포 설정을 다시 읽힌다.

    새로 받은 코드의 tmux_conf.py 를 별도 프로세스로 부른다(훅 등록기와 같은 이유 — 이 프로세스는
    옛 코드를 들고 있다). dry_run 이면 할 일만 보고한다.
    """
    script = root / "server" / "tmux_conf.py"
    if not script.is_file():
        return 0
    cmd = [py, str(script), "reload"] + (["--dry-run"] if dry_run else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("  ✗ " + tr("tmux 설정 정리 실패 — 'fsh tmux reload' 로 다시 시도하세요") + f" ({e})")
        return 1
    out = (p.stdout + p.stderr).rstrip()
    if out:
        print(out)
    return 1 if p.returncode != 0 else 0


def _sync_skill(root: Path, claude_dir: Path, dry_run: bool) -> int:
    src = root / ".claude" / "skills" / "fsh"
    if not src.is_dir() or not claude_dir.is_dir():
        return 0
    dst = claude_dir / "skills" / "fsh"
    if dst.is_symlink():
        if Path(os.path.realpath(dst)) == src.resolve():
            print(tr("  ✓ fsh 스킬: 링크 최신"))
            return 0
        print(tr("  ⚠ fsh 스킬: 다른 곳을 가리키는 링크입니다 ({dst}) — 건드리지 않았습니다", dst=os.readlink(dst)))
        return 0
    if dst.exists():
        print(tr("  ⚠ fsh 스킬: 링크가 아닌 사본입니다 — 덮어쓰지 않았습니다. 최신화: rm -r {dst} && fsh sync", dst=dst))
        return 0
    if dry_run:
        print(tr("  ⚠ fsh 스킬: 링크가 없습니다 (fsh sync 가 만듭니다)"))
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.symlink_to(src)
    print(tr("  ✓ fsh 스킬: 링크를 만들었습니다"))
    return 0


# ── 명령 ──────────────────────────────────────────────────────────────────
def cmd_update(root: Path, channel_arg: str | None, to_tag: str | None, check: bool,
               restart: bool, do_sync: bool = True) -> int:
    stored = read_channel()
    channel = channel_arg or stored
    if channel is None:
        br = current_branch(root)
        if br in (None, "master"):
            channel = "stable"
        else:
            raise UpdateError(tr("채널이 정해지지 않았고 지금 브랜치({br})는 릴리스가 아닙니다 — "
                                 "--channel stable|dev|devmode 로 정하세요.", br=br))
    if channel not in CHANNELS:
        raise UpdateError(tr("채널은 stable, dev, devmode 중 하나입니다."))
    cur = rev(root, "HEAD")
    print(tr("  채널: {channel}   현재: {ver} ({sha})", channel=channel, ver=version_of(root), sha=cur[:8]))

    if channel == "devmode" and not to_tag:
        if channel_arg and channel_arg != stored and not check:
            write_channel(channel)
        if not check:
            print(tr("  ⓘ devmode 는 자동 업데이트를 하지 않습니다 — 직접 git pull 하세요 (훅·스킬은 'fsh sync')."))
            return 0
        fetch(root)
        up = git(root, "rev-list", "--count", "HEAD..@{upstream}", check=False)
        if up.returncode != 0:
            print(tr("  이 브랜치에는 upstream 이 없어 비교하지 않았습니다."))
        else:
            print(tr("  upstream 에 새 커밋 {n}개", n=up.stdout.strip()))
        return 0

    if not check:
        dirty = dirty_files(root)
        if dirty:
            raise UpdateError(tr("로컬 변경이 있어 중단합니다: {files}", files=", ".join(dirty[:5])))
    fetch(root)
    label, sha = resolve_target(root, channel, to_tag)
    print(tr("  대상: {label} ({sha})", label=label, sha=sha[:8]))
    if sha == cur:
        print(tr("  ✓ 이미 최신입니다"))
        if channel_arg and channel_arg != stored and not check:
            write_channel(channel)
        return 0
    if check:
        print(tr("  → 업데이트가 있습니다: fsh update"))
        return 0

    prev = _save_prev(root)
    try:
        _checkout(root, sha)
        post_checkout(root, cur, sha)
        if channel == "dev" and not to_tag:
            git(root, "checkout", "-q", "-B", "dev", sha)
    except UpdateError as e:
        print(f"  ✗ {e}")
        print(tr("  원래 커밋({sha})으로 되돌립니다 (이미 설치된 파이썬/노드 패키지는 되돌리지 않습니다).", sha=cur[:8]))
        _restore(root, prev)
        return 1
    if channel_arg and (channel_arg != stored) and not to_tag:
        write_channel(channel)
    print(tr("  ✓ {old} → {new}", old=prev["version"], new=version_of(root)))
    rc = sync(root) if do_sync else 0
    if restart:
        rc |= subprocess.run([str(root / "bin" / "fsh"), "restart"]).returncode
    else:
        print(tr("  ⓘ 서버가 떠 있다면 'fsh restart' 로 반영하세요 (자동으로 재시작하지 않습니다)."))
    print(tr("  ⓘ 되돌리기: fsh update --rollback"))
    return rc


def cmd_rollback(root: Path) -> int:
    try:
        prev = json.loads(_prev_file().read_text(encoding="utf-8"))
        sha = prev["sha"]
    except (OSError, ValueError, KeyError):
        raise UpdateError(tr("되돌릴 기록이 없습니다."))
    if dirty_files(root):
        raise UpdateError(tr("로컬 변경이 있어 중단합니다."))
    cur = rev(root, "HEAD")
    if git(root, "cat-file", "-e", sha + "^{commit}", check=False).returncode != 0:
        raise UpdateError(tr("기록된 커밋({sha})이 이 리포에 없습니다.", sha=sha[:8]))
    config_io.atomic_write(_prev_file(), json.dumps({"sha": cur, "branch": current_branch(root),
                                                      "version": version_of(root)}) + "\n")
    _checkout(root, sha)
    try:
        post_checkout(root, cur, sha)
    except UpdateError as e:
        print(f"  ✗ {e}")
        _checkout(root, cur)
        return 1
    print(tr("  ✓ {ver} ({sha}) 로 되돌렸습니다 — 분리된 HEAD 입니다.", ver=version_of(root), sha=sha[:8]))
    print(tr("  ⓘ 다음 'fsh update' 는 채널({ch}) 기준으로 다시 올라갑니다. 고정하려면 devmode 로 바꾸세요.",
             ch=read_channel() or "stable"))
    return sync(root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fsh update", description=tr("FarShell 채널별 업데이트"))
    sub = ap.add_subparsers(dest="action")
    ap.add_argument("--channel", choices=CHANNELS)
    ap.add_argument("--to", metavar="vX.Y.Z")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    tr_ = sub.add_parser("trust")
    tr_.add_argument("--key", metavar="PUBKEY")
    tr_.add_argument("--replace", action="store_true")
    tr_.add_argument("--yes", action="store_true")
    sy = sub.add_parser("sync")
    sy.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = Path(os.environ.get("VT_DIR") or ROOT)
    try:
        if args.action == "trust":
            return cmd_trust(root, args.key, args.replace, args.yes)
        if args.action == "sync":
            return sync(root, args.dry_run)
        if args.rollback:
            return cmd_rollback(root)
        return cmd_update(root, args.channel, args.to, args.check, args.restart)
    except UpdateError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
