"""재사용한 메인 터널의 PID를 tunnel.pid에 다시 기록한다(2026-11-04, bin/fsh `_adopt_main_tunnel_pid`).

재사용 분기가 pid 파일을 다시 쓰지 않아, 한 번 잃은 tunnel.pid가 영영 비어 있었다(실측: 15일 된
cloudflared가 pid 파일 없이 돌고 있었다). bin/fsh를 통째로 돌리면 이 트리의 실제 데몬을 건드리므로
함수 하나만 떼어 가짜 pgrep으로 시험한다.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FSH = (ROOT / "bin" / "fsh").read_text(encoding="utf-8")


def _func(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", FSH, re.M | re.S)
    assert m, f"{name} not found in bin/fsh"
    return m.group(0)


ADOPT = _func("_adopt_main_tunnel_pid")

# 가짜 pgrep: -f 패턴을 FAKE_PS의 명령줄들에 grep -E로 맞춰 PID를 낸다(실제 pgrep -f와 같은 판정).
FAKE_PGREP = r"""#!/bin/bash
pat="$2"
found=1
while IFS=$'\t' read -r pid cmd; do
  [ -n "$pid" ] || continue
  if printf '%s\n' "$cmd" | grep -Eq -- "$pat"; then echo "$pid"; found=0; fi
done < "$FAKE_PS"
exit $found
"""


def _run(tmp_path, procs, *, provider="cloudflare", env_extra=None, pidfile=None, main_pids=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    pg = bindir / "pgrep"
    pg.write_text(FAKE_PGREP)
    pg.chmod(pg.stat().st_mode | stat.S_IEXEC)
    ps = tmp_path / "ps.tsv"
    ps.write_text("".join(f"{pid}\t{cmd}\n" for pid, cmd in procs))
    pid_dir = tmp_path / "vt-pids"
    pid_dir.mkdir(exist_ok=True)
    if pidfile is not None:
        (pid_dir / "tunnel.pid").write_text(pidfile)
    script = (
        "set -uo pipefail\n"
        '_t() { local s="$1"; shift; local kv; for kv in "$@"; do s="${s//\\{${kv%%=*}\\}/${kv#*=}}"; done; printf "%s" "$s"; }\n'
        '_is_running() { [ -f "$PID_DIR/$1.pid" ] && kill -0 "$(cat "$PID_DIR/$1.pid")" 2>/dev/null; }\n'
        f'_tunnel_provider() {{ echo {provider}; }}\n'
        f'_main_tunnel_pids() {{ echo "{main_pids}"; }}\n'
        + ADOPT
        + "_adopt_main_tunnel_pid\n"
    )
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_PS": str(ps),
        "PID_DIR": str(pid_dir),
        "VT_PORT": "7777",
        **(env_extra or {}),
    }
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    f = pid_dir / "tunnel.pid"
    return r.stdout, (f.read_text().strip() if f.exists() else None)


ANON = "cloudflared tunnel --url http://localhost:7777"


def test_records_the_single_anonymous_tunnel(tmp_path):
    out, pid = _run(tmp_path, [(12345, ANON)])
    assert pid == "12345"
    assert "12345" in out


def test_mail_gate_args_and_full_path_still_match(tmp_path):
    _, pid = _run(tmp_path, [(5, "/opt/homebrew/bin/cloudflared tunnel --allowed-mail a@b.c --url http://localhost:7777")])
    assert pid == "5"


@pytest.mark.parametrize("cmd", [
    "cloudflared tunnel --url http://localhost:7800",    # 포트 공유 프록시 터널
    "cloudflared tunnel --url http://localhost:77770",   # 포트 접두사만 같은 것
    "cloudflared tunnel --url http://localhost:3000",    # expose한 다른 앱
    "python -c cloudflared tunnel --url http://localhost:7777",  # cloudflared가 아닌 프로세스
])
def test_ignores_tunnels_that_are_not_the_main_one(tmp_path, cmd):
    out, pid = _run(tmp_path, [(9, cmd)])
    assert pid is None
    assert out == ""


def test_keeps_a_live_pidfile_untouched(tmp_path):
    live = str(os.getpid())  # 살아 있는 PID
    _, pid = _run(tmp_path, [(12345, ANON)], pidfile=live)
    assert pid == live


def test_replaces_a_stale_pidfile(tmp_path):
    _, pid = _run(tmp_path, [(12345, ANON)], pidfile="999999")
    assert pid == "12345"


def test_does_not_guess_between_several_candidates(tmp_path):
    out, pid = _run(tmp_path, [(1, ANON), (2, ANON)])
    assert pid is None
    assert "1 2" in out


def test_unnamed_or_foreign_named_tunnel_is_not_adopted(tmp_path):
    procs = [(7, "cloudflared tunnel run other")]
    assert _run(tmp_path, procs)[1] is None
    assert _run(tmp_path, procs, env_extra={"VT_TUNNEL_NAME": "mine"})[1] is None


def test_named_tunnel_with_my_name_is_adopted(tmp_path):
    procs = [(7, "cloudflared tunnel run my.tun"), (8, "cloudflared tunnel run myXtun")]
    _, pid = _run(tmp_path, procs, env_extra={"VT_TUNNEL_NAME": "my.tun"})
    assert pid == "7"   # 이름의 '.'은 글자 그대로 — myXtun은 맞지 않는다


def test_named_tunnel_name_with_regex_chars_is_skipped(tmp_path):
    _, pid = _run(tmp_path, [(7, "cloudflared tunnel run a|b")], env_extra={"VT_TUNNEL_NAME": "a|b"})
    assert pid is None


def test_ngrok_uses_main_tunnel_pids(tmp_path):
    _, pid = _run(tmp_path, [], provider="ngrok", main_pids="4242")
    assert pid == "4242"


def test_both_reuse_branches_call_it():
    """cloudflare·ngrok 재사용 분기 둘 다 return 0 직전에 기록한다."""
    for marker in ("터널 이미 실행 중 (PID{pids}): {url}", "ngrok 이미 실행 중: {url}"):
        i = FSH.index(marker)
        tail = FSH[i:i + 300]
        assert tail.index("_adopt_main_tunnel_pid") < tail.index("return 0"), marker
