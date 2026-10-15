"""scripts/guard_personal.py — 개인 값·비밀이 커밋/푸시에 섞이지 않게 막는 가드.

비밀처럼 보이는 문자열은 이 파일에도 그대로 적지 않는다(가드가 이 파일을 막으면 안 되므로 조각을 이어 만든다).
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("guard_personal", ROOT / "scripts" / "guard_personal.py")
guard = importlib.util.module_from_spec(_spec)
sys.modules["guard_personal"] = guard
_spec.loader.exec_module(guard)


def _tok(prefix: str, n: int = 36) -> str:
    return prefix + "A1b2C3d4E5f6G7h8I9j0" * 4  # 조각을 이어 만든 가짜 값


# ── 경로 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    ".codex/hooks.json", ".agents/skills/fsh/SKILL.md", "a/b/.gemini/settings.json", ".cursor/rules/x.mdc",
    ".aider.chat.history.md", ".aider.input.history", "CLAUDE.local.md", "docs/x.local.md",
    ".claude/settings.json", ".claude/projects/x/y.jsonl", ".claude/hooks/pre.sh",
    "auth.json", "home/auth.json", ".vt.env", ".env", ".env.local", "certs/server.pem", "k/id_rsa", "k/id_ed25519.pub",
    "data/state.sqlite", "x.db", "x.sqlite-wal", ".vt/agent_messages.json",
])
def test_forbidden_paths_are_blocked(path):
    probs = guard.scan([path], [], markers=[])
    assert probs and "올릴 수 없는 경로" in probs[0]


@pytest.mark.parametrize("path", [
    ".aider.conf.yml", ".env.example", ".claude/skills/fsh/SKILL.md", "config/vt.defaults.env",
    "server/auth.py", "docs/ref/auth.md", "frontend/js/core/auth.js", ".github/workflows/ci.yml", "AGENTS.md",
    "server/tests/test_auth_json.py",
])
def test_shared_paths_pass(path):
    assert guard.scan([path], [], markers=[]) == []


# ── 비밀 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "token = '" + _tok("ntn_") + "'",
    "GH=" + _tok("ghp_"),
    "k = " + _tok("github_pat_"),
    "x = '" + _tok("sk-") + "'",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "VT_AUTH_PASSWORD_HASH=scrypt$16384$8$1$" + "ab" * 16 + "$" + "cd" * 32,
    "VT_AUTH_TOKEN=" + "aB3dE5gH7jK9mN1p",
    "export VT_NOTION_TOKEN='" + _tok("ntn_")[:30] + "'",
])
def test_secret_literals_are_blocked_and_value_is_not_echoed(line):
    probs = guard.scan([], [("f.txt", 3, line)], markers=[])
    assert probs and "f.txt:3" in probs[0]
    for fragment in ("A1b2C3d4E5", "aB3dE5gH7j", "abababab", "cdcdcdcd"):
        assert fragment not in " ".join(probs)


@pytest.mark.parametrize("line", [
    'VT_AUTH_TOKEN="$VT_AUTH_TOKEN"',
    "VT_AUTH_TOKEN=$(cat ~/.vt/token)",
    "VT_TOKEN = os.environ.get('VT_TOKEN')",
    "# VT_AUTH_TOKEN=<your-token>",
    "VT_AUTH_TOKEN=",
    "VT_AUTH_TOKEN=changeme",
    "VT_NOTION_TOKEN=xxxxxxxxxxxxxxxx",
    "secret = 'sk-" + "testsecret" + "x" * 20 + "'",       # 테스트 픽스처의 가짜 값
    "t = 'ghp_" + "fake" + "A1" * 20 + "'",
    "Authorization: Bearer ${VT_AUTH_TOKEN}",
])
def test_code_placeholders_and_fakes_pass(line):
    assert guard.scan([], [("f", 1, line)], markers=[]) == []


# ── 내 홈 ────────────────────────────────────────────────────────────────────

def test_personal_markers_come_from_the_running_machine_not_the_repo():
    ms = guard.personal_markers(user="someone", home="/" + "Users" + "/someone")
    assert ("/" + "Users/someone/") in ms and "/home/someone/" in ms
    assert guard.scan([], [("f", 1, "path = '/" + "Users/someone/proj'")], markers=ms)
    # 이름은 출력에 다시 싣지 않는다
    assert "someone" not in " ".join(guard.scan([], [("f", 1, "/" + "Users/someone/x")], markers=ms))


@pytest.mark.parametrize("user", ["root", "user", "me", "x", "ab", "runner", "ubuntu"])
def test_generic_user_names_do_not_cause_false_positives(user):
    ms = guard.personal_markers(user=user, home="/")
    assert guard.scan([], [("f", 1, f"/Users/{user}/repo")], markers=ms) == []


def test_placeholder_paths_pass_for_a_real_user():
    ms = guard.personal_markers(user="realperson", home="/Users/realperson")
    for ph in ("/Users/x/GitHub", "/Users/example/proj", "/Users/me/repo", "/Users/<user>/p"):
        assert guard.scan([], [("f", 1, ph)], markers=ms) == []


# ── git 연동(임시 저장소) ─────────────────────────────────────────────────────

def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "ok.txt").write_text("hello\n")
    _git(tmp_path, "add", "ok.txt")
    _git(tmp_path, "commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FSH_GUARD_SKIP", raising=False)
    return tmp_path


def test_staged_mode_blocks_a_forced_tool_dir_and_a_secret(repo, capsys):
    (repo / ".codex").mkdir()
    (repo / ".codex" / "hooks.json").write_text("{}")
    (repo / "notes.md").write_text("token " + _tok("ntn_") + "\n")
    _git(repo, "add", "-f", ".codex/hooks.json", "notes.md")
    assert guard.run("staged") == 1
    err = capsys.readouterr().err
    assert ".codex/hooks.json" in err and "notes.md:1" in err and "A1b2C3d4E5" not in err


def test_staged_mode_passes_clean_changes(repo):
    (repo / "ok.txt").write_text("hello\nworld\n")
    _git(repo, "add", "ok.txt")
    assert guard.run("staged") == 0


def test_only_added_lines_are_scanned_not_old_content(repo):
    (repo / "legacy.txt").write_text("VT_AUTH_TOKEN=aB3dE5gH7jK9mN1p\n")
    _git(repo, "add", "legacy.txt")
    _git(repo, "commit", "-qm", "legacy", "--no-verify")
    (repo / "legacy.txt").write_text("VT_AUTH_TOKEN=aB3dE5gH7jK9mN1p\nplain line\n")
    _git(repo, "add", "legacy.txt")
    assert guard.run("staged") == 0     # 옛 줄은 이번 변경이 아니다


def test_push_mode_checks_the_pushed_range(repo, capsys):
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "leak.md").write_text("k=" + _tok("ghp_") + "\n")
    _git(repo, "add", "leak.md")
    _git(repo, "commit", "-qm", "leak", "--no-verify")
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert guard.run("push", stdin_text=f"refs/heads/master {head} refs/heads/master {base}\n") == 1
    assert "leak.md:1" in capsys.readouterr().err
    assert guard.run("push", stdin_text=f"refs/heads/master {head} refs/heads/master {head}\n") == 0


def test_skip_env_is_loud_not_silent(repo, monkeypatch, capsys):
    (repo / ".env").write_text("A=1\n")
    _git(repo, "add", "-f", ".env")
    monkeypatch.setenv("FSH_GUARD_SKIP", "1")
    assert guard.run("staged") == 0
    assert "FSH_GUARD_SKIP" in capsys.readouterr().err


# ── 기록 문서 경로 (2026-10-14) ──────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "research_notes/x/y.md", "reports/audit.md", "docs/plan-2.5/10-a.md", "docs/archive/v2.2/x.md", "docs/TODOS.md",
    "docs/ref/keyboard-input-plan.md", "docs/ref/references.md", "docs/ref/mcp-reference-logic.md",
    "MCP_MANAGEMENT_DESIGN.md", "SESSION_VIEW_DESIGN.md",
    "docs/local/plans/plan-x/10-a.md", "docs/local/research/notes.md", "docs/local/notes/memo.md",
])
def test_record_documents_are_blocked(path):
    probs = guard.scan([path], [], markers=[], words=[])
    assert probs and "올릴 수 없는 경로" in probs[0]


@pytest.mark.parametrize("path", [
    "DESIGN.md", "docs/ref/terminal.md", "docs/ref/session-ui.md", "docs/help/ssh.md", "docs/help/en/ssh.md",
    "docs/guide/platforms.md", "README.md", "scripts/stt_bench_engines/README.md", "frontend/js/reports/x.js",
])
def test_reference_and_product_documents_pass(path):
    assert guard.scan([path], [], markers=[], words=[]) == []


# ── 내 단어 ──────────────────────────────────────────────────────────────────

def test_personal_words_block_whole_words_only_and_are_not_echoed():
    words = ["acmecorp", "profile-x"]
    lines = [
        ("a.py", 1, "owner = 'acmecorp'"),
        ("a.py", 2, "name: profile-x"),
        ("a.py", 3, "acmecorporation"),          # 단어 경계 — 걸리지 않는다
        ("a.py", 4, "x_acmecorp_y"),             # 밑줄은 단어 문자다
    ]
    probs = guard.scan([], lines, markers=[], words=words)
    assert len(probs) == 2 and probs[0].startswith("a.py:1:") and probs[1].startswith("a.py:2:")
    assert all("acmecorp" not in p and "profile-x" not in p for p in probs)


def test_personal_words_are_case_sensitive():
    # 대문자로 시작하는 저장소 슬러그가 소문자 프로필 이름과 섞이지 않게 한다.
    probs = guard.scan([], [("README.md", 1, "github.com/Acmecorp-Labs/farshell")], markers=[], words=["acmecorp"])
    assert probs == []
    assert guard.scan([], [("a.py", 1, "profile acmecorp")], markers=[], words=["acmecorp"])


def test_personal_words_also_check_new_file_names():
    probs = guard.scan(["docs/help/acmecorp-notes.md"], [], markers=[], words=["acmecorp"])
    assert probs and "acmecorp" not in probs[0]


def test_words_file_parsing(tmp_path, monkeypatch):
    f = tmp_path / "words"
    f.write_text("# 내 단어\n\nacmecorp\n  profile-x  \nab\nacmecorp\n", encoding="utf-8")
    assert guard.personal_words(str(f)) == ["acmecorp", "profile-x"]      # 주석·빈 줄·짧은 단어·중복 제외
    assert guard.personal_words(str(tmp_path / "missing")) == []          # 파일이 없어도 가드는 돈다
    monkeypatch.setenv("VT_GUARD_WORDS", str(f))
    assert guard.words_path() == str(f)
    assert guard.scan([], [("a.py", 1, "owner acmecorp")], markers=[])    # words=None → 환경변수 파일을 읽는다


def test_staged_mode_uses_the_words_file(tmp_path, monkeypatch):
    words = tmp_path / "words"
    words.write_text("zzcorp\n", encoding="utf-8")
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**os.environ, "VT_GUARD_WORDS": str(words), "HOME": str(tmp_path)}
    run = lambda *a: subprocess.run(a, cwd=repo, env=env, capture_output=True, text=True)
    run("git", "init", "-q")
    (repo / "a.txt").write_text("owner zzcorp\n", encoding="utf-8")
    run("git", "add", "a.txt")
    r = run(sys.executable, str(ROOT / "scripts" / "guard_personal.py"), "staged")
    assert r.returncode == 1 and "개인 단어" in r.stderr and "zzcorp" not in r.stderr
