"""파일 열람 가드 — 루트 확정 + 경로 검증 + 거부 목록의 단일 진실의 원천.

P2(코드 뷰어)는 읽기 전용이지만, 공개 터널 너머로 열리는 API라서 "읽기만 하니 안전"이
성립하지 않는다. 루트 밖 파일 하나가 유출되면 그걸로 끝이다. 그래서 방어를 3중으로 둔다.

  1. 루트 확정   VT_BROWSE_ROOTS 안의 경로만 (기본 ~/GitHub — $HOME 전체는 절대 금지)
  2. 경로 검증   Path.resolve() + is_relative_to
                 ⚠ startswith 금지. routes/pty.py:210-219에 같은 교훈이 남아 있다 —
                   문자열 접두사 비교는 /tmp/vt-uploads-evil/ 을 통과시켰다.
                 ⚠ resolve()가 심링크를 따라가므로, 루트 안에 있지만 밖을 가리키는
                   심링크는 이 단계에서 자동으로 걸러진다.
  3. 거부 목록   루트 안이어도 시크릿은 거부 (.env / id_rsa / *.pem / .ssh/ ...)
                 경로의 '모든' 구성요소를 검사한다 — ~/GitHub/foo/.ssh/config 같은 형태.

이 모듈은 I/O를 하지 않는다(존재 확인 제외). 순수 판정만 담당해 테스트가 쉽도록 유지한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

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
    ".env", ".vt.env", ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "secrets.json", "serviceaccount.json", ".git-credentials",
}

DENY_PREFIXES = (".env",)          # .env.local, .env.production ...
DENY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk")

# 경로 중간에 이 디렉토리가 끼어 있으면 거부.
DENY_DIR_PARTS = {".ssh", ".aws", ".gnupg", ".config/gcloud", ".kube", ".docker"}


class FsDenied(Exception):
    """경로 검증 실패. reason은 사용자에게 그대로 보여줘도 되는 짧은 사유."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def get_roots() -> list[Path]:
    """열람 허용 루트 목록.

    VT_BROWSE_ROOTS 는 ':' 구분. 기본값을 $HOME 이 아니라 ~/GitHub 으로 두는 것이
    이 기능의 안전성 대부분을 결정한다 — $HOME 을 열면 ~/.ssh, ~/.aws 가 사정권에 들어온다.
    """
    raw = os.environ.get("VT_BROWSE_ROOTS", "").strip()
    if not raw:
        raw = str(Path.home() / "GitHub")
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
    return roots


def _is_denied_name(name: str) -> Optional[str]:
    lowered = name.lower()
    if lowered in DENY_NAMES:
        return f"거부된 파일명: {name}"
    if lowered.startswith(DENY_PREFIXES):
        return f"거부된 파일명: {name}"
    if lowered.endswith(DENY_SUFFIXES):
        return f"거부된 확장자: {name}"
    return None


def _check_denylist(p: Path) -> Optional[str]:
    """경로의 모든 구성요소를 검사한다.

    마지막 이름만 보면 ~/GitHub/x/.ssh/config 가 통과한다. .ssh 는 중간 디렉토리다.
    """
    parts = p.parts
    for i, part in enumerate(parts):
        if part in DENY_DIR_PARTS:
            return f"거부된 디렉토리: {part}"
        # ".config/gcloud" 같은 2단 항목 처리
        if i + 1 < len(parts) and f"{part}/{parts[i + 1]}" in DENY_DIR_PARTS:
            return f"거부된 디렉토리: {part}/{parts[i + 1]}"
    return _is_denied_name(p.name)


def resolve_under_roots(raw_path: str) -> Path:
    """사용자가 준 경로를 검증해 안전한 절대경로로 바꾼다.

    실패하면 FsDenied 를 던진다. 호출부는 이 예외를 403으로 매핑하면 된다.
    """
    if not raw_path or not raw_path.strip():
        raise FsDenied("경로가 비었습니다")

    roots = get_roots()
    if not roots:
        raise FsDenied("열람 가능한 루트가 설정되지 않았습니다 (VT_BROWSE_ROOTS)")

    try:
        # strict=False — 존재하지 않아도 정규화는 하고, 존재 확인은 호출부가 한다.
        # 심링크는 여기서 전부 펼쳐지므로 루트 밖을 가리키면 아래 검사에서 걸린다.
        p = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        raise FsDenied(f"경로 해석 실패: {e}") from e

    if not any(_is_within(p, root) for root in roots):
        raise FsDenied("허용된 루트 밖의 경로입니다")

    denied = _check_denylist(p)
    if denied:
        raise FsDenied(denied)

    return p


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


def looks_binary(head: bytes) -> bool:
    """선두 바이트에 NUL 이 있으면 바이너리로 본다.

    완벽한 판정은 아니지만(UTF-16 텍스트는 오탐) 코드 열람 용도로는 충분하고,
    오탐 방향이 '안전한 쪽'(내용을 안 보냄)이라 허용 가능하다.
    """
    return b"\x00" in head
