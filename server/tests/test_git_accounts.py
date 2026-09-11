"""N30 — git 계정 저장소 + 바인딩(40-dock-git.md §1) 회귀.

계층을 셋으로 나눈다:
1. `git_account_store` 단위 — 저장·마스킹·삭제 시 바인딩 정리·바인딩 해석 순서
   4케이스·원격 URL 파싱 3형식. 네트워크(`verify_token`)는 여기서 다루지 않는다
   (해석/저장 로직은 네트워크 없이 완전히 테스트 가능해야 한다).
2. `verify_token` — GitHub/GitLab 응답을 흉내낸 stub으로 성공/실패 분기만 확인.
   실제 네트워크 호출은 절대 하지 않는다(urlopen 자체를 monkeypatch).
3. HTTP 라우트 — `routes/git_accounts.py`의 응답 형태(토큰 미노출) + 승격 게이트.
"""

from __future__ import annotations

import json
import os

import pytest
from starlette.testclient import TestClient

import git_account_store as store


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """~/.vt/git-accounts.json 등 실제 사용자 상태를 절대 건드리지 않는다."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))


# ---------------------------------------------------------------------------
# 1a. 계정 저장 + 마스킹
# ---------------------------------------------------------------------------


def test_add_account_generates_id_and_masks_token():
    r = store.add_account("github", "github.com", "neo-work", "ghp_1234567890abcdef")
    assert r["ok"] is True
    account = r["account"]
    assert account["id"] == "gh-neo-work"
    assert account["auth"]["token"] == "ghp_1234567890abcdef"  # 원문은 저장 파일 내부에만

    listed = store.list_accounts()
    assert len(listed) == 1

    public = store.account_public(listed[0])
    assert "token" not in json.dumps(public)  # 원문이 API 응답 형태 어디에도 없어야 한다
    assert public["auth"]["masked"] == "ghp_…cdef"
    assert "masked" in public["auth"] and "token" not in public["auth"]


def test_mask_token_short_token():
    assert store.mask_token("abc") == "…bc"
    assert store.mask_token("") == ""


def test_add_account_dedupes_id_on_collision():
    r1 = store.add_account("github", "github.com", "neo", "ghp_aaaaaaaa")
    r2 = store.add_account("github", "github.com", "neo", "ghp_bbbbbbbb")
    assert r1["account"]["id"] == "gh-neo"
    assert r2["account"]["id"] == "gh-neo-2"


def test_add_account_rejects_invalid_provider():
    r = store.add_account("bitbucket", "bitbucket.org", "x", "tok")
    assert r["ok"] is False
    assert r["error"] == "invalid_provider"


def test_add_account_rejects_missing_fields():
    r = store.add_account("github", "github.com", "", "tok")
    assert r["ok"] is False
    assert r["error"] == "missing_fields"


# ---------------------------------------------------------------------------
# 1b. 삭제 시 바인딩 정리
# ---------------------------------------------------------------------------


def test_remove_account_cleans_up_bindings():
    acc = store.add_account("github", "github.com", "neo-work", "ghp_x")["account"]
    store.set_repo_binding("/repo/a", acc["id"])
    with store._locked(store._bindings_lock_path()):
        data = store._read_bindings_unlocked()
        data["byHostOrg"]["github.com/company-org"] = acc["id"]
        store._write_bindings_unlocked(data)

    result = store.remove_account(acc["id"])
    assert result["ok"] is True

    bindings = store.get_bindings()
    assert acc["id"] not in bindings["byRepo"].values()
    assert acc["id"] not in bindings["byHostOrg"].values()
    assert store.get_account(acc["id"]) is None


def test_remove_account_not_found():
    r = store.remove_account("does-not-exist")
    assert r["ok"] is False
    assert r["error"] == "not_found"


# ---------------------------------------------------------------------------
# 1c. 바인딩 해석 순서 — byRepo → byHostOrg(host/org) → 같은 host 유일 계정 → null
# ---------------------------------------------------------------------------


def _accounts():
    return [
        {"id": "gh-work", "host": "github.com"},
        {"id": "gh-personal", "host": "github.com"},
        {"id": "gl-main", "host": "gitlab.com"},
    ]


def test_resolve_prefers_explicit_repo_binding():
    bindings = {"byRepo": {"/repo/app": "gh-personal"}, "byHostOrg": {"github.com": "gh-work"}}
    got = store.resolve_account_id(
        "/repo/app", "https://github.com/company-org/app.git", bindings, _accounts()
    )
    assert got == "gh-personal"  # byRepo가 byHostOrg보다 항상 우선


def test_resolve_falls_back_to_host_org_binding():
    bindings = {"byRepo": {}, "byHostOrg": {"github.com/company-org": "gh-work"}}
    got = store.resolve_account_id(
        "/repo/other", "https://github.com/company-org/app.git", bindings, _accounts()
    )
    assert got == "gh-work"


def test_resolve_falls_back_to_single_account_on_host():
    # byRepo/byHostOrg 둘 다 없음 → gitlab.com에 계정이 정확히 1개(gl-main)뿐이므로 그것.
    bindings = {"byRepo": {}, "byHostOrg": {}}
    got = store.resolve_account_id(
        "/repo/other", "git@gitlab.com:team/proj.git", bindings, _accounts()
    )
    assert got == "gl-main"


def test_resolve_returns_none_when_ambiguous():
    # github.com에 계정이 2개(gh-work, gh-personal)라 유일성 규칙이 적용 안 됨 → null.
    bindings = {"byRepo": {}, "byHostOrg": {}}
    got = store.resolve_account_id(
        "/repo/other", "https://github.com/someone/app.git", bindings, _accounts()
    )
    assert got is None


def test_resolve_returns_none_without_remote_or_binding():
    got = store.resolve_account_id("/repo/no-remote", None, {"byRepo": {}, "byHostOrg": {}}, _accounts())
    assert got is None


# ---------------------------------------------------------------------------
# 1d. 원격 URL 파싱 — 3형식
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/company-org/app", ("github.com", "company-org", "app")),
    ("https://github.com/company-org/app.git", ("github.com", "company-org", "app")),
    ("https://user@github.com/company-org/app.git", ("github.com", "company-org", "app")),
    ("git@github.com:company-org/app.git", ("github.com", "company-org", "app")),
    ("git@gitlab.com:team/proj", ("gitlab.com", "team", "proj")),
    ("ssh://git@github.com/company-org/app.git", ("github.com", "company-org", "app")),
    ("ssh://git@gitlab.example.com:2222/team/proj.git", ("gitlab.example.com", "team", "proj")),
])
def test_parse_remote_url_three_formats(url, expected):
    assert store.parse_remote_url(url) == expected


def test_parse_remote_url_invalid_returns_none():
    assert store.parse_remote_url("not a url") is None
    assert store.parse_remote_url("") is None
    assert store.parse_remote_url(None) is None


# ---------------------------------------------------------------------------
# 2. verify_token — 네트워크는 항상 stub. 실제 urlopen을 호출하면 이 테스트는 실패해야 한다.
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_verify_token_github_success(monkeypatch):
    def fake_urlopen(req, timeout=10.0):
        assert "api.github.com" in req.full_url
        assert req.headers.get("Authorization") == "Bearer ghp_ok"
        return _FakeResp(json.dumps({"login": "neo-work", "name": "Neo"}).encode())

    monkeypatch.setattr(store, "urlopen", fake_urlopen)
    r = store.verify_token("github", "github.com", "ghp_ok")
    assert r == {"ok": True, "login": "neo-work", "name": "Neo", "email": None}


def test_verify_token_gitlab_success(monkeypatch):
    def fake_urlopen(req, timeout=10.0):
        assert "gitlab.com/api/v4/user" in req.full_url
        assert req.headers.get("Private-token") == "glpat-ok"
        return _FakeResp(json.dumps({"username": "neo"}).encode())

    monkeypatch.setattr(store, "urlopen", fake_urlopen)
    r = store.verify_token("gitlab", "gitlab.com", "glpat-ok")
    assert r["ok"] is True
    assert r["login"] == "neo"


def test_verify_token_invalid_token_401(monkeypatch):
    from urllib.error import HTTPError

    def fake_urlopen(req, timeout=10.0):
        raise HTTPError(req.full_url, 401, "unauthorized", None, None)

    monkeypatch.setattr(store, "urlopen", fake_urlopen)
    r = store.verify_token("github", "github.com", "ghp_bad")
    assert r == {"ok": False, "error": "token_invalid"}


def test_verify_token_network_error(monkeypatch):
    from urllib.error import URLError

    def fake_urlopen(req, timeout=10.0):
        raise URLError("boom")

    monkeypatch.setattr(store, "urlopen", fake_urlopen)
    r = store.verify_token("github", "github.com", "ghp_x")
    assert r["ok"] is False
    assert r["error"] == "network_error"


def test_verify_token_rejects_unknown_provider():
    r = store.verify_token("bitbucket", "bitbucket.org", "tok")
    assert r == {"ok": False, "error": "invalid_provider"}


# ---------------------------------------------------------------------------
# 3. HTTP 라우트
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth as _auth

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    import main
    with TestClient(main.app) as c:
        yield c


def _login(client, password="s3cret-pw"):
    r = client.post("/api/auth", json={"token": password})
    assert r.status_code == 200, r.text


def _login_and_elevate(client, password="s3cret-pw"):
    _login(client, password)
    r = client.post("/api/auth/elevate", json={"password": password})
    assert r.status_code == 200, r.text


def test_get_accounts_never_exposes_token(client, monkeypatch):
    _login(client)  # GET은 승격 불필요 — 로그인만으로 충분해야 한다
    monkeypatch.setattr(store, "verify_token", lambda *a, **k: {"ok": True, "login": "neo"})
    store.add_account("github", "github.com", "neo", "ghp_realtoken12345")

    r = client.get("/api/git/accounts")
    assert r.status_code == 200
    body = r.text
    assert "ghp_realtoken12345" not in body
    assert "masked" in body


def test_post_accounts_without_elevation_401(client):
    _login(client)
    r = client.post("/api/git/accounts", json={"provider": "github", "token": "ghp_x"})
    assert r.status_code == 401
    assert r.json()["error"] == "elevation_required"


def test_post_accounts_verifies_before_saving(client, monkeypatch):
    from urllib.error import HTTPError

    _login_and_elevate(client)

    def fake_urlopen(req, timeout=10.0):
        raise HTTPError(req.full_url, 401, "bad", None, None)

    monkeypatch.setattr(store, "urlopen", fake_urlopen)
    r = client.post("/api/git/accounts", json={"provider": "github", "token": "ghp_bad"})
    assert r.status_code == 401
    assert r.json()["error"] == "token_invalid"
    assert store.list_accounts() == []  # 검증 실패 시 저장하지 않는다


def test_binding_get_resolves_and_put_requires_elevation(client, tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

    monkeypatch.setattr(store, "verify_token", lambda *a, **k: {"ok": True, "login": "neo"})
    acc = store.add_account("github", "github.com", "neo", "ghp_x")["account"]

    _login(client)  # 로그인만, 승격은 안 함

    r = client.put("/api/git/binding", json={"repo": str(repo), "account_id": acc["id"]})
    assert r.status_code == 401  # 승격 없이는 거부
    assert r.json()["error"] == "elevation_required"

    r2 = client.get("/api/git/binding", params={"repo": str(repo)})
    assert r2.status_code == 200
    assert r2.json()["account_id"] is None  # 아직 바인딩 안 됨

    _login_and_elevate(client)
    r3 = client.put("/api/git/binding", json={"repo": str(repo), "account_id": acc["id"]})
    assert r3.status_code == 200, r3.text

    r4 = client.get("/api/git/binding", params={"repo": str(repo)})
    assert r4.json()["account_id"] == acc["id"]
