"""파일 열람 가드 — 루트 확정 + 경로 검증 + 거부 목록의 단일 진실의 원천.

P2(코드 뷰어)는 읽기 전용이지만, 공개 터널 너머로 열리는 API라서 "읽기만 하니 안전"이
성립하지 않는다. 루트 밖 파일 하나가 유출되면 그걸로 끝이다. 그래서 방어를 3중으로 둔다.

  1. 경계 확정   VT_BROWSE_ROOTS 안의 경로만 (미설정 시 기본 경계는 ~/GitHub 자체 —
                   위로 더 넓게 열려면 VT_BROWSE_ROOTS를 명시하는 opt-in이 필요하다.
                   그렇게 경계를 홈까지 넓혀도 시작 화면은 get_start_roots()가 여전히
                   ~/GitHub 으로 좁게 잡고, 3번 거부 목록이 ~/.ssh·~/.aws 등을 막는다)
  2. 경로 검증   Path.resolve() + is_relative_to
                 ⚠ startswith 금지. routes/pty.py:210-219에 같은 교훈이 남아 있다 —
                   문자열 접두사 비교는 /tmp/vt-uploads-evil/ 을 통과시켰다.
                 ⚠ resolve()가 심링크를 따라가므로, 루트 안에 있지만 밖을 가리키는
                   심링크는 이 단계에서 자동으로 걸러진다.
  3. 거부 목록   루트 안이어도 시크릿은 거부 (.env / id_rsa / *.pem / .ssh/ ...)
                 경로의 '모든' 구성요소를 검사한다 — ~/GitHub/foo/.ssh/config 같은 형태.

일반 열람 경로는 판정만 담당한다. 네이티브 자식 기록은 전용 검증·안전한 파일 열기를 제공한다.
"""

from __future__ import annotations

import logging
import contextlib
import os
import re
from pathlib import Path
from typing import Optional
from i18n import tr

logger = logging.getLogger(__name__)

# 열람 가능한 파일 최대 크기. 넘으면 앞부분만 잘라 보낸다(묵시적 실패가 아니라 명시적 절단).
MAX_BYTES = int(os.environ.get("VT_FS_MAX_BYTES", str(512 * 1024)))

# 바이너리 판정에 쓸 선두 바이트 수.
SNIFF_BYTES = 8192

# 트리 조회 시 통째로 건너뛸 디렉토리. node_modules 하나가 수만 개 항목을 뿜는다.
EXCLUDE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".gradle",
    ".idea", ".DS_Store", "Pods", ".terraform",
}

# 트리 한 번에 반환할 최대 항목 수. 초과분은 잘라내되 truncated 플래그로 알린다.
MAX_ENTRIES = 1000

# --- 거부 목록 ---------------------------------------------------------------
# 루트 안에 있어도 절대 내보내지 않는다. 실제로 ~/GitHub/services/*/.env 가 존재한다.

DENY_NAMES = {
    ".farshell-provider.json", ".farshell-hooks-disabled.json", ".farshell-token.json", ".credentials.json",
    ".env", ".vt.env", ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "secrets.json", "serviceaccount.json", ".git-credentials",
    # --- 에이전트 CLI·개발 도구가 홈에 두는 자격증명 파일 ---------------------
    # 2026-09-17 점검에서 실제로 읽혔다: VT_BROWSE_ROOTS를 홈으로 넓혀 둔 설정에서
    # `GET /api/fs/file`이 아래 파일들을 전부 200으로 내보냈다(공개 터널 너머로).
    # 경계를 다시 ~/GitHub 로 좁히는 것이 1차 방어지만, 거부 목록이 이 이름들을
    # 모른다는 사실 자체가 결함이다 — 경계를 넓혀 쓰는 선택은 언제든 다시 나온다.
    "auth.json",            # ~/.codex/auth.json (OAuth 토큰), 그 외 다수 CLI 공통 이름
    ".claude.json",         # Claude Code 계정·MCP 설정
    "hosts.yml", "hosts.yaml",  # ~/.config/gh/hosts.yml (GitHub PAT)
    ".authinfo", ".authinfo.gpg",
    "token.json", "tokens.json", "credentials.json",
    # --- FarShell 자신의 런타임 상태 ------------------------------------------
    # ~/.vt 전체는 아래 DENY_DIR_PARTS가 막는다. 여기 이름으로 한 번 더 두는 것은
    # 그 파일들이 ~/.vt 밖으로 복사됐을 때(백업·디버깅용 사본)를 위한 것이다:
    # git-accounts.json은 PAT, vapid.json은 푸시 서명키다.
    "git-accounts.json", "vapid.json",
}

DENY_PREFIXES = (".env",)          # .env.local, .env.production ...
DENY_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk",
    # 셸 히스토리는 파일명이 제각각(.zsh_history/.bash_history/.python_history)이라
    # 접미사로 잡는다. 토큰을 명령줄에 한 번이라도 쳤으면 여기 그대로 남아 있다.
    "_history",
)

# 경로 중간에 이 디렉토리가 끼어 있으면 거부.
DENY_DIR_PARTS = {
    ".ssh", ".aws", ".gnupg", ".config/gcloud", ".kube", ".docker",
    # 자격증명·세션 상태를 통째로 담는 디렉토리. 개별 파일명을 쫓는 것보다
    # 디렉토리를 닫는 쪽이 새 파일이 추가돼도 깨지지 않는다.
    ".vt", ".claude", ".codex", ".clauth", ".config/gh",
    ".password-store", ".cert", ".chef", ".azure",
}

# 열람 루트 아래의 점(.) 디렉토리는 기본 거부한다 — 도구마다 자격증명·세션 상태를
# 자기 점 디렉토리에 두므로(`.sometool/.auth.json`) 이름을 하나씩 쫓지 않고 일반 규칙으로 닫는다.
# 소스 트리의 일부인 점 디렉토리만 허용 목록으로 연다. 루트 경로 자체와 그 위쪽 구성요소는
# 검사하지 않는다(사용자가 고른 루트이므로. 예: ~/.worktrees 루트).
ALLOWED_DOT_DIRS = {
    ".github", ".gitlab", ".vscode", ".devcontainer", ".husky", ".storybook",
    ".circleci", ".changeset", ".worktrees",
}


def is_hidden_state_dir_name(name: str) -> bool:
    """디렉토리 이름만 보는 순수 판정 — 트리 목록·검색이 같이 쓴다."""
    return name.startswith(".") and name not in ALLOWED_DOT_DIRS


class FsDenied(Exception):
    """경로 검증 실패. reason은 사용자에게 그대로 보여줘도 되는 짧은 사유."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def get_roots() -> list[Path]:
    """열람 허용 경계 — resolve_under_roots 가 이 안에 있는지만 검사한다.

    VT_BROWSE_ROOTS 를 명시하면 그 경계를 그대로 쓴다(사용자가 직접 고른 정책이므로
    자동으로 넓히지 않는다). 비워두면(기본값) 경계는 ~/GitHub 이다 — $HOME 전체를
    기본으로 열면 .ssh/.aws/셸 히스토리/거부 목록이 모르는 임의 시크릿 파일명까지
    사정권에 들어온다(CLAUDE.md에 문서화된 기본값과도 일치). 홈 전체·상위 디렉토리로
    범위를 넓히는 건 VT_BROWSE_ROOTS를 명시하는 opt-in으로만 가능하다.
    ~/GitHub 이 없는 환경(신규 설치 등)에서는 완전 잠금을 피하려 $HOME 으로 폴백한다 —
    이 경우에도 ~/.ssh·~/.aws 등은 거부 목록(DENY_NAMES/DENY_DIR_PARTS)이 여전히 막는다.
    """
    raw = os.environ.get("VT_BROWSE_ROOTS", "").strip()
    if not raw:
        default = Path.home() / "GitHub"
        roots = [default] if default.is_dir() else [Path.home()]
        return _with_worktrees_root(roots)
    roots = []
    for chunk in raw.split(":"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            p = Path(chunk).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if p.is_dir():
            roots.append(p)
    return _with_worktrees_root(roots)


def _with_worktrees_root(roots: list[Path]) -> list[Path]:
    """N8(30-worktree.md §2) — `~/.worktrees`는 디자인상 정해진 워크트리 저장 위치라
    VT_BROWSE_ROOTS에 없어도 항상 열람 경계 안에 있어야 한다. 이미 어떤 루트가 그
    경로를 포함하고 있으면 중복 추가하지 않는다."""
    wt_root = Path.home() / ".worktrees"
    if not wt_root.is_dir():
        return roots
    if any(_is_within(wt_root, root) for root in roots):
        return roots
    logger.info(f"VT_BROWSE_ROOTS에 없어 자동 추가: {wt_root}")
    return roots + [wt_root]


def get_start_roots() -> list[Path]:
    """UI가 처음 여는 지점 — get_roots()(경계)보다 좁아도 된다.

    ~/GitHub 가 있고 그 경계(get_roots()) 안에 포함돼 있으면 VT_BROWSE_ROOTS 를
    명시했더라도 항상 ~/GitHub 에서 시작한다 — "..” 로 위로 이동해서 경계를 넓게 쓰더라도
    매번 여는 화면은 좁게 유지하고 싶다는 의도(opt-in으로 넓힌 경계는 탐색 허용 범위일 뿐,
    시작점을 바꾸는 게 아니다). ~/GitHub 가 경계 밖이면(사용자가 다른 루트만 지정한 경우)
    get_roots() 그대로 시작한다.
    """
    default = Path.home() / "GitHub"
    if default.is_dir():
        roots = get_roots()
        if any(_is_within(default, root) for root in roots):
            return [default]
    return get_roots()


def _is_denied_name(name: str) -> Optional[str]:
    lowered = name.lower()
    if lowered in DENY_NAMES:
        return tr("거부된 파일명: {name}", name=name)
    if lowered.startswith(DENY_PREFIXES):
        return tr("거부된 파일명: {name}", name=name)
    if lowered.endswith(DENY_SUFFIXES):
        return tr("거부된 확장자: {name}", name=name)
    return None


def is_managed_profile_path(path: Path) -> bool:
    """Manager backups/payloads stay private even with a visible state override."""
    import vt_paths
    try:
        return any(Path(path).resolve().is_relative_to((vt_paths.state_dir() / name).resolve())
                   for name in ('agent-profiles', 'session-catalog'))
    except (OSError, RuntimeError, ValueError):
        return True


def _check_denylist(p: Path) -> Optional[str]:
    """경로의 모든 구성요소를 검사한다.

    마지막 이름만 보면 ~/GitHub/x/.ssh/config 가 통과한다. .ssh 는 중간 디렉토리다.
    """
    if is_managed_profile_path(p):
        return tr("관리 프로필의 비공개 저장소는 열람할 수 없습니다")
    parts = p.parts
    for i, part in enumerate(parts):
        if part in DENY_DIR_PARTS:
            return tr("거부된 디렉토리: {name}", name=part)
        # ".config/gcloud" 같은 2단 항목 처리
        if i + 1 < len(parts) and f"{part}/{parts[i + 1]}" in DENY_DIR_PARTS:
            return tr("거부된 디렉토리: {name}", name=f"{part}/{parts[i + 1]}")
    return _is_denied_name(p.name)


def private_profile_directory(path: Path, *, create: bool = True) -> Path:
    """Private manager state; reject swapped links and foreign directories.

    Ancestors are checked too. This grants no file-browser permission.
    """
    import stat
    path = Path(path).absolute()
    # /tmp and /var may themselves be platform-owned symlinks. Resolve only
    # their existing platform prefix, never a manager-controlled component.
    parts = path.parts
    current = Path(parts[0])
    for component in parts[1:]:
        current /= component
        if current.is_symlink():
            if current in (Path('/tmp'), Path('/var')):
                current = current.resolve()
                continue
            raise FsDenied(tr("프로필 경로에 심볼릭 링크가 있습니다"))
        if not current.exists():
            if not create:
                raise FsDenied(tr("프로필 디렉터리가 없습니다"))
            current.mkdir(mode=0o700)
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise FsDenied(tr("프로필 경로가 디렉터리가 아닙니다"))
    info = current.stat()
    if info.st_uid != os.getuid():
        raise FsDenied(tr("프로필 소유자가 일치하지 않습니다"))
    os.chmod(current, 0o700)
    return current


def profile_file(root: Path, relative: str) -> Path:
    """Only an approved native-relative path, without link traversal."""
    import stat
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('.', '..') for p in rel.parts):
        raise FsDenied(tr("프로필 파일 경로가 올바르지 않습니다"))
    base = Path(root)
    if base.is_symlink() or not base.is_dir():
        raise FsDenied(tr("프로필 디렉터리를 확인하지 못했습니다"))
    target = base
    for component in rel.parts:
        target /= component
        if target.is_symlink():
            raise FsDenied(tr("프로필 파일에 심볼릭 링크가 있습니다"))
        if target.exists():
            info = target.stat()
            if info.st_uid != os.getuid() or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise FsDenied(tr("프로필 파일 소유권을 확인하지 못했습니다"))
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise FsDenied(tr("프로필 특수 파일은 지원하지 않습니다"))
    if not target.resolve().is_relative_to(base.resolve()):
        raise FsDenied(tr("프로필 파일이 허용된 경로 밖에 있습니다"))
    return target


@contextlib.contextmanager
def _profile_stream(root: Path, relative: str, limit: int):
    """Open one profile file with NOFOLLOW at every component; yield (stream, size)."""
    import stat
    profile_file(root, relative)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > limit:
                raise FsDenied(tr("프로필 파일을 안전하게 읽지 못했습니다"))
            yield stream, info.st_size
    finally:
        os.close(fd)


def read_profile_bytes(root: Path, relative: str, limit: int = 8 * 1024 * 1024) -> bytes:
    """Open every component relative to a directory fd with NOFOLLOW."""
    with _profile_stream(root, relative, limit) as (stream, _size):
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise FsDenied(tr("프로필 파일이 너무 큽니다"))
        return data


def read_profile_head(root: Path, relative: str, head: int = 1024 * 1024) -> tuple[bytes, int]:
    """First `head` bytes and the full size, without loading the whole file."""
    with _profile_stream(root, relative, 1 << 62) as (stream, size):
        return stream.read(head), size


class FsChanged(FsDenied):
    """A profile file no longer matches the digest that was reviewed."""


def hash_profile_file(root: Path, relative: str, limit: int, prefix: Optional[int] = None) -> tuple:
    """Streaming SHA-256 -> (full digest, size, digest of the first `prefix` bytes or None)."""
    import hashlib
    full = hashlib.sha256()
    head = hashlib.sha256() if prefix is not None else None
    with _profile_stream(root, relative, limit) as (stream, _size):
        total = 0
        while chunk := stream.read(1024 * 1024):
            if head is not None and total < prefix:
                head.update(chunk[:prefix - total])
            total += len(chunk)
            if total > limit:
                raise FsDenied(tr("프로필 파일이 너무 큽니다"))
            full.update(chunk)
    return full.hexdigest(), total, (head.hexdigest() if head is not None and total >= prefix else None)


def copy_profile_file(root: Path, relative: str, destination: Path, expected_sha256: str, limit: int, mode: int = 0o600) -> None:
    """Stream a profile file to `destination`, verifying its digest before it becomes visible."""
    import hashlib
    import tempfile
    digest = hashlib.sha256()
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, _profile_stream(root, relative, limit) as (stream, _size):
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise FsDenied(tr("프로필 파일이 너무 큽니다"))
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != expected_sha256:
            raise FsChanged(tr("프로필 파일이 검토한 내용과 다릅니다"))
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def profile_tree(root: Path, *, limit: int = 2000):
    """Bound traversal including directories; never descend links or Git data."""
    import stat
    root = Path(root)
    stack = [root]
    count = 0
    while stack:
        current = stack.pop()
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise FsDenied(tr("프로필 원본 디렉터리를 안전하게 읽지 못했습니다"))
        entries = []
        with os.scandir(current) as scan:
            for entry in scan:
                if entry.name == '.git':
                    continue
                count += 1
                if count > limit:
                    raise FsDenied(tr("프로필 원본의 항목 수가 한도를 넘었습니다"))
                entries.append(entry)
        for entry in sorted(entries, key=lambda e: e.name):
            path = Path(entry.path)
            profile_file(root, str(path.relative_to(root)))
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            else:
                yield path


def skill_import_source(path: Path, home: Path) -> Path:
    """Resolve a discovered skill link to code, without widening file browsing."""
    target = path.resolve(strict=True)
    if not target.is_relative_to(home.resolve()) or not target.is_dir() or not (target / 'SKILL.md').is_file():
        raise FsDenied(tr("스킬 원본이 사용자 홈의 유효한 스킬 디렉터리가 아닙니다"))
    sensitive = DENY_DIR_PARTS - {'.claude', '.codex', '.clauth', '.vt'}
    if any(part in sensitive for part in target.parts):
        raise FsDenied(tr("스킬 원본에 자격증명 디렉터리가 포함되어 있습니다"))
    return target


def resolve_cli_profile(raw_path: str, home: Path) -> Path:
    """A server-discovered CLI settings root, confined to its owner's home.

    This does not authorize arbitrary file browsing. Callers only use it for
    native settings filenames beneath a profile found on a same-user process.
    """
    p = Path(raw_path).expanduser()
    if not p.is_absolute() or not p.resolve().is_relative_to(home.resolve()):
        raise FsDenied(tr("프로필 경로가 사용자 홈 밖에 있습니다"))
    return p.resolve()


def resolve_under_roots(raw_path: str, *, check_hidden: bool = True) -> Path:
    """사용자가 준 경로를 검증해 안전한 절대경로로 바꾼다.

    실패하면 FsDenied 를 던진다. 호출부는 이 예외를 403으로 매핑하면 된다.
    check_hidden=False 는 점 디렉토리 규칙만 끈다(경계·거부 목록은 그대로). 내용을 내보내지
    않고 `.git` 디렉토리의 경계 소속만 확인하는 내부 호출(session_context)용이다.
    """
    if not raw_path or not raw_path.strip():
        raise FsDenied(tr("경로가 비었습니다"))

    roots = get_roots()
    if not roots:
        raise FsDenied(tr("열람 가능한 루트가 설정되지 않았습니다 (VT_BROWSE_ROOTS)"))

    try:
        # strict=False — 존재하지 않아도 정규화는 하고, 존재 확인은 호출부가 한다.
        # 심링크는 여기서 전부 펼쳐지므로 루트 밖을 가리키면 아래 검사에서 걸린다.
        p = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        raise FsDenied(tr("경로 해석 실패: {error}", error=e)) from e

    if not any(_is_within(p, root) for root in roots):
        raise FsDenied(tr("허용된 루트 밖의 경로입니다"))

    denied = _check_denylist(p)
    if denied:
        raise FsDenied(denied)

    if check_hidden:
        denied = _check_hidden_state_dir(p, _matching_root(p, roots))
        if denied:
            raise FsDenied(denied)

    return p


def _matching_root(p: Path, roots: list[Path]) -> Path:
    """p 를 품는 루트 중 가장 깊은 것 — 사용자가 명시적으로 고른 가장 구체적인 경계."""
    return max((r for r in roots if _is_within(p, r)), key=lambda r: len(r.parts))


def _check_hidden_state_dir(p: Path, root: Path) -> Optional[str]:
    """root 아래 구성요소 중 허용 목록에 없는 점 디렉토리가 있으면 거부 사유를 돌려준다.

    root 자신과 그 위쪽은 검사하지 않는다. 마지막 구성요소는 디렉토리일 때만 본다
    (`.gitignore`·`.eslintrc` 같은 점 파일은 열람 대상). _check_denylist 와 달리
    루트 상대 판정이라 resolve_under_roots 에서만 부른다.
    """
    try:
        rel = p.relative_to(root).parts
    except ValueError:
        return None
    for i, part in enumerate(rel):
        if not is_hidden_state_dir_name(part):
            continue
        if i == len(rel) - 1 and not p.is_dir():
            continue
        return tr("거부된 디렉토리: {name}", name=part)
    return None


def _is_within(p: Path, root: Path) -> bool:
    """p 가 root 안에 있는지. root 자기 자신도 허용한다."""
    if p == root:
        return True
    try:
        return p.is_relative_to(root)
    except (AttributeError, ValueError):
        # Python 3.8 이하 폴백 — 이 프로젝트는 3.13이지만 방어적으로 둔다.
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False


# [T3] 인라인 이미지 미리보기가 **원본 바이트로 내보내도 되는** 타입.
#
# 화이트리스트인 이유가 핵심이다. 이 서버는 공개 터널로 나가고, 여기서 내보낸
# 바이트는 브라우저가 **이 오리진의 리소스로** 해석한다. 그래서:
#   - SVG는 뺀다. SVG는 스크립트를 품을 수 있어 같은 오리진에서 열면 XSS다
#     (SVG는 텍스트라 뷰어가 어차피 소스로 보여준다 — 잃는 게 없다).
#   - HTML·PDF도 뺀다. 같은 이유(스크립트/임베드).
#   - 확장자만 믿지 않고 매직 바이트로 한 번 더 확인한다 — `.png`로 이름만 바꾼
#     HTML을 image/png로 내보내면 브라우저가 스니핑으로 되살릴 여지를 준다.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}

# 이미지 미리보기 상한. 코드 열람 도구지 사진첩이 아니다 — 원격(터널 너머)에서
# 20MB짜리를 통째로 끌어오면 화면이 멈춘 것처럼 보인다.
MAX_IMAGE_BYTES = int(os.environ.get("VT_FS_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)


def image_type(path, head: bytes) -> str | None:
    """확장자와 매직 바이트가 **둘 다** 같은 타입을 가리킬 때만 그 타입을 준다."""
    from pathlib import Path as _P

    declared = IMAGE_TYPES.get(_P(str(path)).suffix.lower())
    if not declared:
        return None
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return declared if mime == declared else None
    # webp는 RIFF 컨테이너라 4바이트 건너뛴 자리를 본다.
    if declared == "image/webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return declared
    return None


def looks_binary(head: bytes) -> bool:
    """선두 바이트에 NUL 이 있으면 바이너리로 본다.

    완벽한 판정은 아니지만(UTF-16 텍스트는 오탐) 코드 열람 용도로는 충분하고,
    오탐 방향이 '안전한 쪽'(내용을 안 보냄)이라 허용 가능하다.
    """
    return b"\x00" in head


def voice_transcript_path(tool: str, value: str):
    """음성 훅 기록은 등록 도구의 로컬 기록 디렉터리 안에서만 읽는다."""
    from pathlib import Path
    if tool == 'claude':
        roots = [Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects']
    elif tool == 'codex':
        roots = [Path(os.environ.get('VT_CODEX_HOME') or os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions']
    else:
        return None
    try:
        path = Path(value).expanduser().resolve()
        if path.suffix == '.jsonl' and path.is_file() and any(path.is_relative_to(r.resolve()) for r in roots):
            return path
    except (OSError, ValueError, TypeError):
        pass
    return None


def subagent_transcript_path(cli: str, parent_id: str, agent_id: str,
                             raw_path: str | None, parent_transcript: str | None = None) -> Path:
    """Narrow exception for one verified CLI child; never alters browse roots."""
    import claude_hooks
    import codex_hooks
    import subagents
    if not subagents.valid_id(parent_id) or not subagents.valid_id(agent_id):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    try:
        if cli == "claude":
            root = (claude_hooks.settings_path().parent / "projects").resolve()
            if not isinstance(parent_transcript, str):
                raise FsDenied(tr("기록에 접근할 수 없습니다"))
            parent = Path(parent_transcript).expanduser().resolve()
            if not parent.is_relative_to(root) or parent.name != f"{parent_id}.jsonl":
                raise FsDenied(tr("기록에 접근할 수 없습니다"))
            expected = parent.parent / parent_id / "subagents" / f"agent-{agent_id}.jsonl"
            # Do not resolve the expected suffix: a link into another parent's file is not this child.
            path = Path(raw_path).expanduser().resolve() if raw_path else expected.resolve()
            if path != expected or not path.is_relative_to(root):
                raise FsDenied(tr("기록에 접근할 수 없습니다"))
        elif cli == "codex":
            root = (codex_hooks.settings_path().parent / "sessions").resolve()
            if not isinstance(raw_path, str):
                raise FileNotFoundError
            path = Path(raw_path).expanduser().resolve()
            if not path.is_relative_to(root):
                raise FsDenied(tr("기록에 접근할 수 없습니다"))
        else:
            raise FsDenied(tr("지원하지 않는 기록 형식"))
        if path.suffix != ".jsonl":
            raise FsDenied(tr("기록에 접근할 수 없습니다"))
        return path
    except (RuntimeError, ValueError):
        raise FsDenied(tr("기록에 접근할 수 없습니다")) from None


def open_subagent_file(path: Path):
    """Open resolved absolute components without following subsequently swapped links."""
    import stat
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        result = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(result).st_mode):
            os.close(result)
            raise FsDenied(tr("기록에 접근할 수 없습니다"))
        return os.fdopen(result, "rb")
    finally:
        os.close(fd)


def history_source_root(raw: str) -> Path:
    """Explicitly approved CLI home, not a general filesystem exemption."""
    import vt_paths
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    original = Path(raw).expanduser()
    home = vt_paths.user_home().resolve()
    if not original.is_absolute() or original.is_symlink():
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    root = original.resolve(strict=True)
    blocked = {'.ssh', '.aws', '.gnupg', '.kube', '.azure'}
    if root == home or not root.is_relative_to(home) or any(p in blocked for p in root.parts):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    # A manager profile is valid, but catalog backups may never recursively ingest themselves.
    if root.is_relative_to((vt_paths.state_dir() / 'session-catalog').resolve()) or not root.is_dir():
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    return root


def history_capture_kind(relative: str, provider: str) -> str | None:
    if not isinstance(relative,str) or len(relative)>4096:
        return None
    rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        return None
    allowed={'claude':{'projects'},'codex':{'sessions','archived_sessions'}}
    if rel.parts[0] not in allowed.get(provider,set()):
        return None
    if relative.endswith('.jsonl') or relative.endswith('.jsonl.zst'):
        return 'transcript'
    if provider=='claude':
        if (len(rel.parts)==5 and rel.parts[3]=='subagents'
                and re.fullmatch(r'agent-[A-Za-z0-9_-]{1,160}\.meta\.json',rel.name)):
            return 'child_metadata'
        if (len(rel.parts)==5 and rel.parts[3]=='tool-results'
                and rel.suffix in ('.txt','.json')):
            return 'tool_attachment'
    return None


def history_source_path(root: Path, relative: str, provider: str) -> Path:
    if not isinstance(relative, str) or len(relative) > 4096:
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    rel = Path(relative)
    allowed = {'claude': {'projects'}, 'codex': {'sessions', 'archived_sessions'}}
    if (rel.is_absolute() or '..' in rel.parts or not rel.parts
            or rel.parts[0] not in allowed.get(provider, set())
            or history_capture_kind(relative,provider) is None):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    path = root / rel
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(root):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    return resolved


def history_restore_target(raw_root: str, relative: str, provider: str) -> Path:
    """Create only declared transcript directories under an approved original CLI home."""
    import vt_paths
    import stat
    root = Path(raw_root)
    home = vt_paths.user_home().resolve()
    if (not root.is_absolute() or not root.resolve().is_relative_to(home) or root.resolve() == home
            or any(p in {'.ssh', '.aws', '.gnupg', '.kube', '.azure'} for p in root.parts)):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    rel = Path(relative)
    allowed = {'claude': {'projects'}, 'codex': {'sessions', 'archived_sessions'}}
    if (rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in allowed.get(provider, set())
            or history_capture_kind(relative,provider) is None):
        raise FsDenied(tr("기록에 접근할 수 없습니다"))
    target = root / rel
    # Walk each component; do not let recursive mkdir follow a substituted link.
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in target.parent.relative_to(home).parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
    finally:
        os.close(directory)
    return target
