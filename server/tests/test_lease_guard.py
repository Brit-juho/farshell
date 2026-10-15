"""pre-commit guard — 설치·이어붙이기·되돌리기와, 진짜 `git commit` 이 서버 응답에 따라 막히거나 통과하는 것.

서버는 시험용 unix socket 서버(가짜)로 흉내 낸다 — 실제 서버와의 왕복은 별도 스모크가 본다.
"""

from __future__ import annotations

import json
import os
import socketserver
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from authz import lease_cli

SERVER_DIR = Path(__file__).resolve().parents[1]


def git(repo, *args, check=True, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
    if check and r.returncode:
        raise AssertionError(r.stderr.decode())
    return r


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@example.com")
    git(d, "config", "user.name", "t")
    return d


@pytest.fixture
def fsh(tmp_path):
    """훅이 부르는 `fsh` 대역 — 진짜 `fsh lease …` 처럼 현재 폴더를 --cwd 로 넘겨 파이썬 모듈을 부른다."""
    p = tmp_path / "bin" / "fsh"
    p.parent.mkdir()
    p.write_text(
        "#!/bin/sh\nshift   # `lease`\n"
        f"cd \"{SERVER_DIR}\" && exec \"{sys.executable}\" -m authz lease --cwd \"$OLDPWD\" \"$@\"\n")
    p.chmod(0o755)
    return p


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """서버 상태 폴더·소켓을 시험 전용으로 — 실제 ~/.vt 에 닿지 않는다."""
    state = tmp_path / "vt"
    monkeypatch.setenv("VT_STATE_DIR", str(state))
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", str(state / "run" / "t.sock"))
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    return state


class FakeServer:
    """`POST /api/leases/check` 에 정해진 답을 하는 unix socket 서버."""

    def __init__(self, path: Path):
        self.path = path
        self.reply = (200, {"ok": True, "checked": 0, "conflicts": []})
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.seen.append({"path": self.path, "body": body, "headers": dict(self.headers)})
                status, data = outer.reply
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        old = os.umask(0o177)
        try:
            self.srv = S(str(path), H)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def server(sandbox):
    s = FakeServer(sandbox / "run" / "t.sock")
    yield s
    s.stop()


def commit(repo, fsh, msg="m", *, files=()):
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text(f)
        git(repo, "add", "--", f)
    env = {**os.environ, "VT_AUTHZ_SOCKET": os.environ.get("VT_AUTHZ_SOCKET", "1")}
    return git(repo, "commit", "-m", msg, check=False, env=env)


# --- 설치 · 이어붙이기 · 되돌리기 ------------------------------------------------------------------


def test_install_fresh_repo_and_uninstall(repo, fsh):
    hook = repo / ".git" / "hooks" / "pre-commit"
    rc, msg = lease_cli.guard_install(str(repo), str(fsh))
    assert rc == 0 and hook.is_file() and stat.S_IMODE(hook.stat().st_mode) == 0o755
    text = hook.read_text()
    assert lease_cli.MARK in text and "lease check --staged" in text
    assert "VT_TOKEN" not in text and "Bearer" not in text and "token" not in text.lower()     # 기계 토큰·비밀을 쓰지 않는다
    assert lease_cli.guard_state(str(repo)) == "installed"
    assert lease_cli.guard_install(str(repo), str(fsh))[0] == 0                                 # 멱등
    assert not (repo / ".git" / "hooks" / lease_cli.CHAINED).exists()
    assert lease_cli.guard_uninstall(str(repo))[0] == 0 and not hook.exists()
    assert lease_cli.guard_uninstall(str(repo))[0] == 0                                         # 두 번째도 오류 없이
    assert lease_cli.guard_state(str(repo)) == "absent"


def test_install_chains_an_existing_hook_and_uninstall_restores_it_exactly(repo, fsh):
    hooks = repo / ".git" / "hooks"
    orig = hooks / "pre-commit"
    body = f"#!/bin/sh\necho ran >> '{repo}/ran.log'\nexit 0\n"
    orig.write_text(body)
    orig.chmod(0o755)
    st = orig.stat()
    rc, _ = lease_cli.guard_install(str(repo), str(fsh))
    assert rc == 0 and (hooks / lease_cli.CHAINED).read_text() == body and lease_cli.MARK in orig.read_text()
    assert lease_cli.guard_state(str(repo)) == "chained"
    lease_cli.guard_install(str(repo), str(fsh))                                                # 다시 설치해도 기존 훅을 덮지 않는다
    assert (hooks / lease_cli.CHAINED).read_text() == body
    lease_cli.guard_uninstall(str(repo))
    assert orig.read_text() == body and stat.S_IMODE(orig.stat().st_mode) == stat.S_IMODE(st.st_mode)
    assert not (hooks / lease_cli.CHAINED).exists()


def test_install_refuses_when_a_chained_slot_is_already_taken(repo, fsh):
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / lease_cli.CHAINED).write_text("#!/bin/sh\nexit 0\n")
    rc, msg = lease_cli.guard_install(str(repo), str(fsh))
    assert rc == 1 and lease_cli.CHAINED in msg
    assert lease_cli.MARK not in (hooks / "pre-commit").read_text()                              # 아무것도 덮지 않았다


def test_core_hooks_path_and_linked_worktree_are_respected(repo, fsh, tmp_path):
    shared = tmp_path / "shared-hooks"
    git(repo, "config", "core.hooksPath", str(shared))
    rc, _ = lease_cli.guard_install(str(repo), str(fsh))
    assert rc == 0 and (shared / "pre-commit").is_file() and not (repo / ".git" / "hooks" / "pre-commit").exists()
    git(repo, "config", "--unset", "core.hooksPath")
    (repo / "f").write_text("f")
    git(repo, "add", "f")
    git(repo, "commit", "-qm", "i")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    assert lease_cli.hooks_dir(str(wt)).resolve() == (repo / ".git" / "hooks").resolve()          # 연결된 worktree 도 같은 훅 폴더


def test_not_a_repo(tmp_path, fsh):
    rc, msg = lease_cli.guard_install(str(tmp_path), str(fsh))
    assert rc == 1
    assert lease_cli.guard_state(str(tmp_path)) == "no-repo"


# --- 진짜 git commit ------------------------------------------------------------------------


def test_commit_passes_with_a_warning_and_a_log_line_when_the_server_is_down(repo, fsh, sandbox):
    lease_cli.guard_install(str(repo), str(fsh))
    r = commit(repo, fsh, files=["secret-name.txt", "two.txt"])
    assert r.returncode == 0, r.stderr.decode()                                                    # fail open
    err = r.stderr.decode()
    assert "lease guard" in err and "권고" in err                                                    # 한 줄 경고(권고임을 밝힌다)
    assert len([l for l in err.splitlines() if "lease guard" in l]) == 1
    assert git(repo, "log", "--oneline").stdout.strip()                                            # 커밋이 실제로 만들어졌다
    log = (sandbox / "authz" / lease_cli.SKIP_LOG).read_text()
    assert log.count("\n") == 1 and "reason=server_unreachable" in log and "files=2" in log
    assert "secret-name" not in log and str(repo) not in log                                        # 개수 말고는 내용이 없다
    assert stat.S_IMODE((sandbox / "authz" / lease_cli.SKIP_LOG).stat().st_mode) == 0o600
    assert lease_cli.skipped_24h() == 1


def test_commit_is_blocked_with_holder_and_file_list_on_conflict(repo, fsh, server):
    lease_cli.guard_install(str(repo), str(fsh))
    rows = lambda: [{"path": str(repo / "a.txt"), "holder": "alpha", "expires": 1893456000, "mode": "write", "exclusive": False,
                     "glob": str(repo), "lease": "ls-1"}]
    server.reply = (200, {"ok": False, "checked": 1, "conflicts": rows()})
    r = commit(repo, fsh, files=["a.txt"])
    assert r.returncode == 1
    err = r.stderr.decode()
    assert "alpha" in err and "a.txt" in err and "1" in err and "--no-verify" in err
    assert not git(repo, "log", "--oneline", check=False).stdout.strip()                           # 커밋되지 않았다
    # 요청: 소켓으로만, 토큰·Authorization 없이, 경로는 본문에만
    req = server.seen[-1]
    assert req["path"] == "/api/leases/check" and "authorization" not in {k.lower() for k in req["headers"]}
    assert req["body"] == {"paths": [str(repo / "a.txt")]}
    # 충돌이 풀리면 같은 스테이지로 통과
    server.reply = (200, {"ok": True, "checked": 1, "conflicts": []})
    r = git(repo, "commit", "-m", "m", check=False)
    assert r.returncode == 0 and git(repo, "log", "--oneline").stdout.strip()


def test_chained_hook_runs_first_and_its_failure_stops_before_the_lease_check(repo, fsh, server):
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text(f"#!/bin/sh\necho first >> '{repo}/order.log'\nexit 7\n")
    (hooks / "pre-commit").chmod(0o755)
    lease_cli.guard_install(str(repo), str(fsh))
    r = commit(repo, fsh, files=["a.txt"])
    assert r.returncode != 0 and (repo / "order.log").read_text() == "first\n"
    assert server.seen == []                                                                        # 앞 훅이 실패하면 서버에 묻지도 않는다
    (hooks / lease_cli.CHAINED).write_text(f"#!/bin/sh\necho first >> '{repo}/order.log'\nexit 0\n")
    r = git(repo, "commit", "-m", "m", check=False)
    assert r.returncode == 0 and len(server.seen) == 1 and (repo / "order.log").read_text() == "first\nfirst\n"


def test_names_with_spaces_newlines_unicode_and_renames_are_sent_exactly(repo, fsh, server):
    names = ["with space.txt", "줄\n바꿈.txt", "ünï.txt", "-dash.txt", "a b/c d.txt"]
    for n in names:
        (repo / n).parent.mkdir(parents=True, exist_ok=True)
        (repo / n).write_text(n)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init", env={**os.environ, "GIT_CONFIG_COUNT": "0"})
    git(repo, "mv", "with space.txt", "renamed 이름.txt")
    (repo / "줄\n바꿈.txt").write_text("changed")
    git(repo, "add", "-A")
    lease_cli.guard_install(str(repo), str(fsh))
    r = git(repo, "commit", "-m", "again", check=False)
    assert r.returncode == 0, r.stderr.decode()
    sent = set(server.seen[-1]["body"]["paths"])
    top = lease_cli.repo_top(str(repo))
    assert sent == {os.path.join(top, "with space.txt"), os.path.join(top, "renamed 이름.txt"), os.path.join(top, "줄\n바꿈.txt")}


def test_no_staged_files_or_not_a_repo_never_asks_the_server(repo, fsh, server, tmp_path):
    assert lease_cli.cmd_check(["--staged"], str(repo)) == 0 and server.seen == []
    assert lease_cli.cmd_check(["--staged"], str(tmp_path)) == 0 and server.seen == []


@pytest.mark.parametrize("status", [404, 403, 500])
def test_unexpected_server_answers_fail_open_with_a_logged_reason(repo, fsh, server, sandbox, status):
    server.reply = (status, {"error": "x"})
    lease_cli.guard_install(str(repo), str(fsh))
    r = commit(repo, fsh, files=["a.txt"])
    assert r.returncode == 0 and "lease guard" in r.stderr.decode()
    assert f"reason=http_{status}" in (sandbox / "authz" / lease_cli.SKIP_LOG).read_text()


def test_missing_fsh_binary_skips_without_blocking(repo, tmp_path):
    lease_cli.guard_install(str(repo), str(tmp_path / "nope" / "fsh"))
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    r = git(repo, "commit", "-m", "e", check=False, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "fsh not found" in r.stderr.decode()                              # fsh 를 못 찾아도 커밋은 통과


def test_manual_check_reports_and_is_strict_about_a_dead_server(repo, fsh, sandbox, capsys):
    (repo / "a.txt").write_text("a")
    assert lease_cli.cmd_check(["a.txt"], str(repo)) == 2                                           # 수동 확인은 서버가 없으면 오류
    assert "unix socket" in capsys.readouterr().err
    assert not (sandbox / "authz" / lease_cli.SKIP_LOG).exists()


def test_parse_ttl():
    assert lease_cli.parse_ttl("90") == 90 and lease_cli.parse_ttl("30m") == 1800 and lease_cli.parse_ttl("2h") == 7200
    assert lease_cli.parse_ttl(None) is None
    for bad in ("x", "1d", "-5", "1.5h"):
        with pytest.raises(ValueError):
            lease_cli.parse_ttl(bad)
