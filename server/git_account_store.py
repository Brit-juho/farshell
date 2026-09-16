"""git 계정 저장소 + 바인딩 (N30, 40-dock-git.md §1).

공개 터널 너머에서 맥의 git을 "쓰는" 첫 기능의 기반 인프라. 여러 GitHub/GitLab
계정(PAT)을 등록해두고, 저장소별로 어느 계정으로 push/PR 할지 바인딩한다.

파일 I/O 패턴은 새로 만들지 않는다 — `snippet_store.py`의 규칙(0700 디렉토리 +
0600 파일 + atomic replace(os.replace) + flock)을 그대로 따른다. 동시 쓰기(웹
여러 탭·CLI+웹 동시 접근)에도 lost update가 나면 안 된다.

저장·바인딩·URL 파싱은 순수 CRUD + 판정 로직이라 네트워크 없이 단위 테스트한다.
GitHub/GitLab `GET /user` 토큰 검증(`verify_token`)만 예외적으로 이 모듈에 둔다 —
CLI(`fsh git-account add`)가 서버 없이 `python git_account_store.py`로 단독 호출되므로
(auth 패키지와 같은 패턴), 검증 로직이 routes/에만 있으면 CLI 경로에서 중복 구현이
생긴다. 테스트는 `verify_token` 자체를 monkeypatch해 실제 네트워크 호출을 막는다.

토큰 원문은 이 파일이 쓰는 ~/.vt/git-accounts.json에만 존재한다(0600). 클라이언트로
내려가는 모든 경로는 `mask_token`을 거쳐야 한다 — 절대 원문을 API 응답에 담지 않는다.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request as _UrlRequest
from urllib.request import urlopen

PROVIDERS = {"github", "gitlab"}
AUTH_TYPES = {"pat"}


# ---------------------------------------------------------------------------
# 상태 디렉토리 / 파일 경로 (snippet_store.py와 동일한 규칙)
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _accounts_path() -> Path:
    return _state_dir() / "git-accounts.json"


def _accounts_lock_path() -> Path:
    return _state_dir() / "git-accounts.lock"


def _bindings_path() -> Path:
    return _state_dir() / "git-bindings.json"


def _bindings_lock_path() -> Path:
    return _state_dir() / "git-bindings.lock"


@contextmanager
def _locked(lock_path: Path):
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_json_unlocked(path: Path, default):
    if not path.is_file():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json_unlocked(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# 계정 CRUD
# ---------------------------------------------------------------------------

def _read_accounts_unlocked() -> list[dict]:
    data = _read_json_unlocked(_accounts_path(), {"version": 1, "accounts": []})
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return [a for a in (accounts or []) if isinstance(a, dict) and a.get("id")]


def _write_accounts_unlocked(accounts: list[dict]) -> None:
    _write_json_unlocked(_accounts_path(), {"version": 1, "accounts": accounts})


def list_accounts() -> list[dict]:
    """원문 포함 전체 목록(서버 내부용). API 응답 직전엔 반드시 mask_token을 거칠 것."""
    with _locked(_accounts_lock_path()):
        return _read_accounts_unlocked()


def get_account(account_id: str) -> Optional[dict]:
    for a in list_accounts():
        if a.get("id") == account_id:
            return a
    return None


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "acct"


def _gen_id(existing: list[dict], provider: str, login: str) -> str:
    prefix = "gh" if provider == "github" else "gl"
    base = f"{prefix}-{_slugify(login)}"
    ids = {a.get("id") for a in existing}
    if base not in ids:
        return base
    i = 2
    while f"{base}-{i}" in ids:
        i += 1
    return f"{base}-{i}"


def add_account(
    provider: str,
    host: str,
    login: str,
    token: str,
    *,
    name: Optional[str] = None,
    email: Optional[str] = None,
    ssh_key: Optional[str] = None,
    orgs: Optional[list[str]] = None,
    auth_type: str = "pat",
) -> dict:
    """검증(네트워크 호출)이 이미 끝난 값들로 계정을 저장한다.

    routes/git_accounts.py가 GitHub/GitLab `GET /user`로 토큰을 확인하고 그 응답의
    login을 넘겨준 뒤에만 호출돼야 한다 — 이 함수 자체는 토큰이 실제로 유효한지
    검증하지 않는다.
    """
    if provider not in PROVIDERS:
        return {"ok": False, "error": "invalid_provider",
                "reason": f"provider는 {sorted(PROVIDERS)} 중 하나여야 합니다"}
    if auth_type not in AUTH_TYPES:
        return {"ok": False, "error": "invalid_auth_type",
                "reason": "auth.type은 pat만 지원합니다"}
    host = (host or "").strip()
    login = (login or "").strip()
    token = (token or "").strip()
    if not host or not login or not token:
        return {"ok": False, "error": "missing_fields",
                "reason": "host/login/token은 비어있을 수 없습니다"}

    with _locked(_accounts_lock_path()):
        accounts = _read_accounts_unlocked()
        account_id = _gen_id(accounts, provider, login)
        record = {
            "id": account_id,
            "provider": provider,
            "host": host,
            "login": login,
            "name": name,
            "email": email,
            "auth": {"type": auth_type, "token": token},
            "ssh_key": ssh_key,
            "orgs": orgs or [],
            "created": time.time(),
            "lastUsed": 0,
        }
        accounts.append(record)
        _write_accounts_unlocked(accounts)
    return {"ok": True, "account": record}


def remove_account(account_id: str) -> dict:
    """계정 삭제 + 그 계정을 참조하는 모든 바인딩(byRepo/byHostOrg) 제거.

    바인딩 정리를 빼먹으면 삭제된 계정 id가 바인딩에만 남아, 나중에 그 id로 다시
    계정을 만들었을 때 엉뚱한 저장소에 자동으로 다시 바인딩되는 사고가 난다.
    """
    with _locked(_accounts_lock_path()):
        accounts = _read_accounts_unlocked()
        rest = [a for a in accounts if a.get("id") != account_id]
        if len(rest) == len(accounts):
            return {"ok": False, "error": "not_found"}
        _write_accounts_unlocked(rest)

    with _locked(_bindings_lock_path()):
        bindings = _read_bindings_unlocked()
        by_repo = {k: v for k, v in bindings.get("byRepo", {}).items() if v != account_id}
        by_host_org = {k: v for k, v in bindings.get("byHostOrg", {}).items() if v != account_id}
        bindings["byRepo"] = by_repo
        bindings["byHostOrg"] = by_host_org
        _write_bindings_unlocked(bindings)

    return {"ok": True, "removed": account_id}


def mask_token(token: str) -> str:
    """`ghp_…3f2a` 형태로 마스킹. 클라이언트로 내려가는 유일한 형태여야 한다."""
    if not token:
        return ""
    if len(token) <= 8:
        return "…" + token[-2:]
    return f"{token[:4]}…{token[-4:]}"


def account_public(a: dict) -> dict:
    """API 응답용 — 토큰 원문을 제외하고 마스킹만 노출."""
    auth = a.get("auth") or {}
    token = auth.get("token", "")
    return {
        "id": a.get("id"),
        "provider": a.get("provider"),
        "host": a.get("host"),
        "login": a.get("login"),
        "name": a.get("name"),
        "email": a.get("email"),
        "auth": {"type": auth.get("type"), "masked": mask_token(token)},
        "has_ssh_key": bool(a.get("ssh_key")),
        "orgs": a.get("orgs") or [],
        "created": a.get("created"),
        "lastUsed": a.get("lastUsed"),
    }


# ---------------------------------------------------------------------------
# 바인딩
# ---------------------------------------------------------------------------

def _read_bindings_unlocked() -> dict:
    data = _read_json_unlocked(
        _bindings_path(), {"version": 1, "byRepo": {}, "byHostOrg": {}}
    )
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("byRepo", {})
    data.setdefault("byHostOrg", {})
    if not isinstance(data["byRepo"], dict):
        data["byRepo"] = {}
    if not isinstance(data["byHostOrg"], dict):
        data["byHostOrg"] = {}
    return data


def _write_bindings_unlocked(data: dict) -> None:
    _write_json_unlocked(_bindings_path(), data)


def get_bindings() -> dict:
    with _locked(_bindings_lock_path()):
        return _read_bindings_unlocked()


def set_repo_binding(repo_top: str, account_id: str) -> dict:
    """저장소 하나를 특정 계정에 명시적으로 바인딩(byRepo). 해석 순서에서 최우선."""
    if not repo_top:
        return {"ok": False, "error": "missing_repo"}
    if not get_account(account_id):
        return {"ok": False, "error": "account_not_found"}
    with _locked(_bindings_lock_path()):
        data = _read_bindings_unlocked()
        data["byRepo"][repo_top] = account_id
        _write_bindings_unlocked(data)
    return {"ok": True, "repo": repo_top, "account_id": account_id}


# ---------------------------------------------------------------------------
# 원격 URL 파싱 (3형식)
# ---------------------------------------------------------------------------

_RE_HTTPS = re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$")
_RE_SCP = re.compile(r"^[\w.\-]+@([^:/]+):([^/]+)/([^/]+?)(?:\.git)?/?$")
_RE_SSH = re.compile(r"^ssh://(?:[^@/]+@)?([^:/]+)(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$")


def parse_remote_url(url: str) -> Optional[tuple[str, str, str]]:
    """git 원격 URL → (host, owner, name). 지원 형식 3가지(40-dock-git.md §4):

    - `https://host/owner/name(.git)`
    - `git@host:owner/name(.git)`
    - `ssh://git@host/owner/name(.git)`

    파싱 불가면 None — 호출부가 실패로 처리한다(추측하지 않는다).
    """
    if not url:
        return None
    url = url.strip()
    for pattern in (_RE_HTTPS, _RE_SCP, _RE_SSH):
        m = pattern.match(url)
        if m:
            host, owner, name = m.groups()
            return host, owner, name
    return None


# ---------------------------------------------------------------------------
# 바인딩 해석 순서 (40-dock-git.md §1)
# byRepo[repoTop] → 원격 URL의 host/owner가 byHostOrg → 같은 host에 계정이
# 정확히 1개면 그것 → 그 외 null.
# ---------------------------------------------------------------------------

def resolve_account_id(
    repo_top: Optional[str],
    remote_url: Optional[str],
    bindings: dict,
    accounts: list[dict],
) -> Optional[str]:
    """순수 함수 — git 저장소 접근 없이 단위 테스트 가능하도록 값만 받는다."""
    by_repo = bindings.get("byRepo") or {}
    if repo_top and by_repo.get(repo_top):
        return by_repo[repo_top]

    parsed = parse_remote_url(remote_url) if remote_url else None
    by_host_org = bindings.get("byHostOrg") or {}
    if parsed:
        host, owner, _name = parsed
        hit = by_host_org.get(f"{host}/{owner}") or by_host_org.get(host)
        if hit:
            return hit
        matches = [a.get("id") for a in accounts if a.get("host") == host]
        if len(matches) == 1:
            return matches[0]
    return None


def resolve_for_repo(repo_top: Optional[str], remote_url: Optional[str]) -> Optional[str]:
    """실제 저장 파일을 읽어 해석까지 수행하는 편의 함수(라우터에서 사용)."""
    bindings = get_bindings()
    accounts = list_accounts()
    return resolve_account_id(repo_top, remote_url, bindings, accounts)


def git_remote_url(repo_path: str, remote: str = "origin") -> Optional[str]:
    """`git remote get-url <remote>` — 실패(원격 없음 등)하면 None."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", remote],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# ---------------------------------------------------------------------------
# 토큰 검증 (GitHub/GitLab GET /user) — 저장 전 반드시 거친다
# ---------------------------------------------------------------------------

def _github_api_base(host: str) -> str:
    return "https://api.github.com" if host in ("", "github.com") else f"https://{host}/api/v3"


def verify_token(provider: str, host: str, token: str, timeout: float = 10.0) -> dict:
    """GitHub/GitLab `GET /user`로 토큰을 검증하고 login을 확인한다.

    {"ok": True, "login": ..., "name": ..., "email": ...} 또는
    {"ok": False, "error": "token_invalid"|"network_error"|...}.
    테스트는 실제 네트워크를 타지 않도록 이 함수를 monkeypatch한다.
    """
    if provider not in PROVIDERS:
        return {"ok": False, "error": "invalid_provider"}
    host = (host or "").strip() or ("github.com" if provider == "github" else "gitlab.com")
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "empty_token"}

    if provider == "github":
        url = f"{_github_api_base(host)}/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "farshell",
        }
        login_key = "login"
    else:  # gitlab
        url = f"https://{host}/api/v4/user"
        headers = {"PRIVATE-TOKEN": token}
        login_key = "username"

    req = _UrlRequest(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "error": "token_invalid"}
        return {"ok": False, "error": f"http_{e.code}"}
    except URLError as e:
        return {"ok": False, "error": "network_error", "reason": str(e.reason)}
    except Exception as e:  # noqa: BLE001 — 검증 실패는 전부 안전하게 거부로 처리
        return {"ok": False, "error": "verify_failed", "reason": str(e)}

    login = data.get(login_key) if isinstance(data, dict) else None
    if not login:
        return {"ok": False, "error": "no_login"}
    return {
        "ok": True,
        "login": login,
        "name": data.get("name") if isinstance(data, dict) else None,
        "email": data.get("email") if isinstance(data, dict) else None,
    }


# ---------------------------------------------------------------------------
# CLI — `fsh git-account`가 서버 없이 직접 호출한다 (python git_account_store.py <cmd>)
# ---------------------------------------------------------------------------

def _parse_flags(args: list[str]) -> dict:
    opts: dict = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                opts[key] = args[i + 1]
                i += 2
            else:
                opts[key] = True
                i += 1
        else:
            i += 1
    return opts


def _cli(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"

    if cmd == "list":
        print(json.dumps([account_public(a) for a in list_accounts()], ensure_ascii=False))
        return 0

    if cmd == "add":
        opts = _parse_flags(argv[1:])
        provider = str(opts.get("provider", "") or "")
        if provider not in PROVIDERS:
            print(json.dumps({"ok": False, "error": "invalid_provider"}, ensure_ascii=False))
            return 1
        if "token-stdin" not in opts:
            print(json.dumps(
                {"ok": False, "error": "token_stdin_required",
                 "reason": "--token-stdin만 지원합니다 (argv에 토큰 노출 금지)"},
                ensure_ascii=False,
            ))
            return 1
        host = str(opts.get("host", "") or "")
        token = sys.stdin.readline().strip()
        if not token:
            print(json.dumps({"ok": False, "error": "empty_token"}, ensure_ascii=False))
            return 1
        v = verify_token(provider, host, token)
        if not v.get("ok"):
            print(json.dumps(v, ensure_ascii=False))
            return 1
        r = add_account(
            provider, host or ("github.com" if provider == "github" else "gitlab.com"),
            v["login"], token,
            name=opts.get("name") or v.get("name"),
            email=opts.get("email") or v.get("email"),
            ssh_key=opts.get("ssh-key"),
        )
        if r.get("ok"):
            print(json.dumps({"ok": True, "account": account_public(r["account"])}, ensure_ascii=False))
            return 0
        print(json.dumps(r, ensure_ascii=False))
        return 1

    if cmd == "rm":
        account_id = argv[1] if len(argv) > 1 else ""
        r = remove_account(account_id)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    if cmd == "bind":
        if len(argv) < 3:
            print("usage: git_account_store.py bind <repo> <account_id>", file=sys.stderr)
            return 2
        r = set_repo_binding(argv[1], argv[2])
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
