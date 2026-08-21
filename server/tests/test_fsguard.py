"""P2 회귀: 코드 뷰어가 허용 루트 밖 파일이나 시크릿을 절대 내보내지 않아야 한다.

읽기 전용 API라도 공개 터널 너머로 열리므로, 경로 탈출 하나가 곧 유출이다.
특히 아래 3가지는 실제로 뚫린 적이 있거나(startswith) 실존하는 위험이다:
  - 문자열 접두사 비교로 형제 디렉토리 통과 (routes/pty.py:210-219의 교훈)
  - 루트 안에 있지만 밖을 가리키는 심링크
  - 루트 안의 .env / .ssh (~/GitHub/services/*/.env 는 실제로 존재한다)
"""

import os
from pathlib import Path

import pytest

import fsguard


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """tmp_path 안에 루트를 만들고 VT_BROWSE_ROOTS 를 거기로 고정."""
    root = tmp_path / "repos"
    root.mkdir()
    (root / "proj").mkdir()
    (root / "proj" / "main.py").write_text("print('hi')\n")
    (root / "proj" / ".env").write_text("SECRET=1\n")
    (root / "proj" / "key.pem").write_text("-----BEGIN-----\n")
    (root / "proj" / ".ssh").mkdir()
    (root / "proj" / ".ssh" / "config").write_text("Host *\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not leak\n")

    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    return {"root": root, "outside": outside}


def test_normal_file_is_allowed(sandbox):
    p = fsguard.resolve_under_roots(str(sandbox["root"] / "proj" / "main.py"))
    assert p.is_file()


def test_root_itself_is_allowed(sandbox):
    p = fsguard.resolve_under_roots(str(sandbox["root"]))
    assert p == sandbox["root"].resolve()


def test_absolute_escape_is_denied(sandbox):
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots("/etc/passwd")


def test_dotdot_escape_is_denied(sandbox):
    escape = str(sandbox["root"] / "proj" / ".." / ".." / "outside" / "secret.txt")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(escape)


def test_sibling_prefix_is_denied(sandbox, monkeypatch, tmp_path):
    """startswith 회귀: '<root>-evil' 은 '<root>' 로 시작하지만 루트 밖이다."""
    evil = tmp_path / "repos-evil"
    evil.mkdir()
    (evil / "x.txt").write_text("nope\n")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(evil / "x.txt"))


def test_symlink_escaping_root_is_denied(sandbox):
    """루트 안에 있지만 밖을 가리키는 심링크 — resolve()가 펼쳐서 걸러야 한다."""
    link = sandbox["root"] / "proj" / "escape"
    link.symlink_to(sandbox["outside"])
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(link / "secret.txt"))


def test_dotenv_is_denied(sandbox):
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(sandbox["root"] / "proj" / ".env"))


def test_dotenv_variants_are_denied(sandbox):
    (sandbox["root"] / "proj" / ".env.production").write_text("X=1\n")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(sandbox["root"] / "proj" / ".env.production"))


def test_pem_is_denied(sandbox):
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(sandbox["root"] / "proj" / "key.pem"))


def test_ssh_dir_component_is_denied(sandbox):
    """.ssh 는 중간 디렉토리다 — 마지막 이름만 보면 통과해버린다."""
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(sandbox["root"] / "proj" / ".ssh" / "config"))


def test_empty_path_is_denied(sandbox):
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots("")


def test_no_roots_configured_denies_everything(monkeypatch, tmp_path):
    """루트가 비면 fail-closed 여야 한다 — 전체 허용으로 열리면 안 된다."""
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path / "does-not-exist"))
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots("/etc/passwd")


def test_default_boundary_is_github_not_home(monkeypatch):
    """기본 경계(get_roots)는 이제 ~/GitHub 이다 — $HOME 전체를 기본으로 열면
    .ssh/.aws/셸 히스토리 등 거부 목록이 모르는 임의 시크릿까지 사정권에 든다.
    CLAUDE.md에 문서화된 기본값(~/GitHub)과도 일치해야 한다."""
    monkeypatch.delenv("VT_BROWSE_ROOTS", raising=False)
    expected = Path.home() / "GitHub"
    if expected.is_dir():
        assert fsguard.get_roots() == [expected]
    else:
        # ~/GitHub 이 없는 환경에서는 완전 잠금을 피해 홈으로 폴백한다.
        assert fsguard.get_roots() == [Path.home()]


def test_default_start_root_is_not_home(monkeypatch):
    """시작 화면은 경계보다 좁게 유지한다 — 첫 화면이 곧 $HOME 전체로 열리면 안 된다."""
    monkeypatch.delenv("VT_BROWSE_ROOTS", raising=False)
    starts = fsguard.get_start_roots()
    assert Path.home() not in starts


def test_default_boundary_still_denies_home_secrets(monkeypatch):
    """경계가 홈으로 넓어져도 거부 목록은 경로 존재 여부와 무관하게 그대로 막아야 한다."""
    monkeypatch.delenv("VT_BROWSE_ROOTS", raising=False)
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(Path.home() / ".ssh" / "config"))
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(Path.home() / ".aws" / "credentials"))


def test_default_boundary_does_not_allow_navigating_above_start_root(monkeypatch):
    """기본 경계는 이제 ~/GitHub 자체다 — 위(홈)로 이동은 opt-in(VT_BROWSE_ROOTS 명시)
    으로만 가능해야 한다. ~/GitHub 이 실제로 존재하는 환경에서만 검증한다."""
    monkeypatch.delenv("VT_BROWSE_ROOTS", raising=False)
    if not (Path.home() / "GitHub").is_dir():
        pytest.skip("이 환경에는 ~/GitHub 이 없어 기본 경계가 홈으로 폴백한다")
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(Path.home()))


def test_widened_boundary_keeps_github_as_start_root(monkeypatch):
    """VT_BROWSE_ROOTS=$HOME 처럼 경계를 홈까지 넓혀도, ~/GitHub 이 그 경계 안에
    있으면 시작 화면은 여전히 ~/GitHub 로 좁게 유지돼야 한다 — "..” 로 위까지
    올라갈 수 있는 것과 매번 여는 화면이 좁은 것은 별개다."""
    github = Path.home() / "GitHub"
    if not github.is_dir():
        pytest.skip("이 환경에는 ~/GitHub 이 없다")
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(Path.home()))
    assert fsguard.get_start_roots() == [github]
    # 경계 자체는 넓어졌으므로 홈까지는 더 이상 막히지 않는다.
    fsguard.resolve_under_roots(str(Path.home()))


def test_custom_roots_are_not_widened_to_home(sandbox):
    """VT_BROWSE_ROOTS 를 명시했으면 사용자가 고른 경계 그대로다 — 자동으로 넓히지 않는다."""
    assert fsguard.get_roots() == [sandbox["root"].resolve()]
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(Path.home()))


def test_looks_binary():
    assert fsguard.looks_binary(b"\x89PNG\x00\x1a")
    assert not fsguard.looks_binary(b"print('hello')\n")
