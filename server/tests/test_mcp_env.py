"""97번 3단계 — 자격증명을 세션 환경에 넣는 경로(mcp_env).

여기서 못박는 것:
1. **값이 명령줄에 실리지 않는다.** `tmux set-environment`에 값을 직접 넘기면
   `ps`로 머신의 다른 계정에 보인다 — 0600 파일로 좁혀놓은 경계가 넓어진다.
2. **파일은 0600.** 카탈로그와 같은 경계여야 의미가 있다.
3. **셸이 읽는 파일이므로 값의 따옴표 처리가 곧 방어다** — 값에 `$(…)`나 `;`가
   들어 있으면 그대로 실행된다.
4. 꺼진 서버의 키는 안 나간다(노출 최소).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest


@pytest.fixture
def env_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_RUN_DIR", str(tmp_path / "run"))
    import importlib

    import mcp_catalog
    importlib.reload(mcp_catalog)
    import mcp_env
    importlib.reload(mcp_env)
    return mcp_env, mcp_catalog


def test_env_file_is_0600(env_mod):
    m, _ = env_mod
    path = m.write_env_file("dev", {"A": "secret"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_secret_is_in_the_file_not_in_the_command_line(env_mod):
    """명령줄에는 **경로만** 실린다 — 그게 이 모듈의 존재 이유다."""
    m, _ = env_mod
    path = m.write_env_file("dev", {"A": "sk-real-secret"})
    prefix = m.source_prefix(path)
    assert "sk-real-secret" not in prefix
    assert str(path) in prefix
    assert "sk-real-secret" in path.read_text()


def test_values_are_quoted_so_a_shell_cannot_execute_them(env_mod, tmp_path):
    """이 파일은 `.`(source)로 읽힌다. 따옴표가 없으면 값이 명령이 된다."""
    m, _ = env_mod
    marker = tmp_path / "pwned"
    nasty = f"x$(touch {marker}); echo y"
    path = m.write_env_file("dev", {"A": nasty})

    out = subprocess.run(
        ["sh", "-c", f'set -a; . {path}; set +a; printf %s "$A"'],
        capture_output=True, text=True,
    )
    assert not marker.exists(), "값 안의 명령이 실행됐다"
    assert out.stdout == nasty


def test_empty_env_removes_the_file(env_mod):
    """남겨두면 MCP를 껐는데도 키가 계속 주입된다."""
    m, _ = env_mod
    path = m.write_env_file("dev", {"A": "x"})
    assert path.exists()
    assert m.write_env_file("dev", {}) is None
    assert not path.exists()


def test_session_name_cannot_escape_the_directory(env_mod):
    """tmux 세션 이름에는 `/`가 들어갈 수 있다."""
    m, _ = env_mod
    for nasty in ("../../etc/passwd", "a/b/c", "..", "/abs/path"):
        path = m.env_file_for(nasty)
        # 진짜 성질은 "이름에 점이 없다"가 아니라 **디렉터리를 못 벗어난다**이다.
        assert path.resolve().parent == m._run_dir().resolve(), nasty


def test_source_prefix_is_empty_when_there_is_nothing_to_inject(env_mod):
    m, _ = env_mod
    assert m.source_prefix(None) == ""


def test_prefix_survives_a_missing_file(env_mod, tmp_path):
    """파일이 그 사이 지워져도 세션이 안 죽어야 한다 — `[ -f ]` 가드."""
    m, _ = env_mod
    path = m.write_env_file("dev", {"A": "x"})
    path.unlink()
    out = subprocess.run(["sh", "-c", m.source_prefix(path) + "echo alive"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "alive"


def test_only_enabled_servers_contribute_keys(env_mod, monkeypatch):
    """노출 범위를 실제로 쓰는 것으로 한정한다(§2-1)."""
    m, catalog = env_mod
    catalog.set_cred("on-server", "token", "sek-on")
    catalog.set_cred("off-server", "token", "sek-off")
    monkeypatch.setattr("mcp_scan.scan", lambda wt=None: {"servers": [
        {"name": "on-server", "enabled": True, "fingerprint": None},
        {"name": "off-server", "enabled": False, "fingerprint": None},
    ]})
    env = m.env_for_scope(None)
    assert env == {"FSH_MCP_ON_SERVER_TOKEN": "sek-on"}


def test_scan_failure_does_not_break_session_creation(env_mod, monkeypatch):
    """MCP를 못 읽었다고 세션이 안 열리면 안 된다 — 키 없이 연다."""
    m, _ = env_mod

    def boom(wt=None):
        raise RuntimeError("설정 파일이 깨졌다")

    monkeypatch.setattr("mcp_scan.scan", boom)
    assert m.env_for_scope(None) == {}


def test_fingerprint_mismatch_keeps_the_key_out(env_mod, monkeypatch):
    """§2-5 — 같은 이름인데 실행 명령이 바뀐 서버."""
    m, catalog = env_mod
    catalog.set_cred("notion", "token", "sek", fingerprint_="aaaaaaaaaaaaaaaa")
    monkeypatch.setattr("mcp_scan.scan", lambda wt=None: {"servers": [
        {"name": "notion", "enabled": True, "fingerprint": "bbbbbbbbbbbbbbbb"},
    ]})
    assert m.env_for_scope(None) == {}


def test_adapter_and_catalog_compute_the_same_fingerprint():
    """두 벌이 어긋나면 지문이 영원히 안 맞아 키가 절대 주입되지 않는다."""
    import mcp_adapters
    import mcp_catalog
    for defn in (
        {"command": "npx", "args": ["-y", "x"]},
        {"url": "https://example.test/mcp"},
        {},
    ):
        assert mcp_adapters._fingerprint(defn) == mcp_catalog.fingerprint(defn)


# ── 세션 생성 경로에 실제로 물려 있는가 ───────────────────────────────────

def test_open_agent_sources_the_file_and_never_puts_the_value_on_the_line(
        env_mod, monkeypatch, tmp_path):
    """에이전트를 띄우는 명령줄에 **경로만** 실리고 값은 안 실린다.

    이 테스트가 없으면 mcp_env가 아무리 안전해도 호출부가 그냥 값을 넘기는
    회귀를 못 잡는다 — 이 기능의 유일한 보안 성질이 거기 걸려 있다.
    """
    m, catalog = env_mod
    import worktree

    catalog.set_cred("notion", "token", "sk-real-secret")
    monkeypatch.setattr("mcp_scan.scan", lambda wt=None: {"servers": [
        {"name": "notion", "enabled": True, "fingerprint": None},
    ]})
    monkeypatch.setattr(worktree, "list_worktrees", lambda force=False: [])
    monkeypatch.setattr(worktree.tmux_runner, "has_session", lambda n: True)

    calls = []

    def fake_run(args, timeout=None):
        calls.append(args)
        return 0, b"", b""

    monkeypatch.setattr(worktree.tmux_runner, "run", fake_run)
    out = worktree._open_agent(tmp_path, "repo", "main", "claude")
    assert out["ok"]

    flat = " ".join(" ".join(str(x) for x in c) for c in calls)
    assert "sk-real-secret" not in flat, "값이 tmux 명령줄에 실렸다"

    sent = [c for c in calls if c[0] == "send-keys"]
    assert sent and "set -a" in sent[0][3], "환경 파일을 읽는 조각이 안 붙었다"
    assert str(m.env_file_for("wt-repo-main")) in sent[0][3]
