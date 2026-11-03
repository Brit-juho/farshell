"""S6: 런타임 폴더(pid·터널 로그·레지스트리)는 내 소유·심볼릭 링크 아님·0700 이어야 하고, 기본은 ~/.vt/run 이다.

2.7 전 기본값은 공용 /tmp 였다. 같은 맥의 다른 계정이 /tmp/vt-pids 나 /tmp/cloudflared.log 를 먼저 만들어
두면 `mkdir -p`·`: >` 가 그것을 그대로 썼다 — 로그에 넣은 터널 주소로 QR·포트 티켓이 가고, PID 파일로
`fsh stop` 이 엉뚱한 프로세스를 죽였다. bin/fsh 를 통째로 돌리지 않고 lib/run_dir.sh 만 불러 시험한다.
"""
import os
import sys
import subprocess
from pathlib import Path

import pytest

import vt_paths

ROOT = Path(__file__).resolve().parents[2]
LIB = f'_t() {{ printf "%s" "$1"; }}; . "{ROOT}/lib/run_dir.sh"; '


def _sh(script: str, env: dict) -> subprocess.CompletedProcess:
    e = {"PATH": os.environ["PATH"], **env}
    return subprocess.run(["bash", "-c", LIB + script], env=e, capture_output=True, text=True, timeout=30)


# --- 기본 위치 ---------------------------------------------------------------------


def test_python_default_is_state_dir_run(monkeypatch):
    monkeypatch.delenv("VT_RUN_DIR", raising=False)
    monkeypatch.setenv("VT_STATE_DIR", "/srv/x/.vt")
    assert vt_paths.run_dir() == Path("/srv/x/.vt/run")
    assert vt_paths.tunnel_log() == Path("/srv/x/.vt/run/cloudflared.log")


@pytest.mark.parametrize("state, want", [(None, "/h/.vt/run"), ("/s", "/s/run"), ("~/alt", "/h/alt/run")])
def test_shell_default_matches_python(state, want):
    env = {"HOME": "/h"}
    if state is not None:
        env["VT_STATE_DIR"] = state
    assert _sh("_vt_run_default", env).stdout == want


# --- 폴더 판정 ---------------------------------------------------------------------


def test_shell_creates_private_dir_and_tightens_my_loose_one(tmp_path):
    new = tmp_path / "a" / "run"
    assert _sh(f'_vt_private_dir "{new}"', {"HOME": str(tmp_path)}).returncode == 0
    assert new.stat().st_mode & 0o777 == 0o700
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    assert _sh(f'_vt_private_dir "{loose}"', {"HOME": str(tmp_path)}).returncode == 0
    assert loose.stat().st_mode & 0o777 == 0o700


def test_shell_refuses_symlink_and_foreign_owner(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    r = _sh(f'_vt_private_dir "{link}"', {"HOME": str(tmp_path)})
    assert r.returncode == 1 and "심볼릭 링크" in r.stderr
    r = _sh('_vt_private_dir /', {"HOME": str(tmp_path)})  # root 소유 — 남의 폴더
    assert r.returncode == 1 and "소유자" in r.stderr


def test_python_check_private_dir(tmp_path):
    vt_paths.check_private_dir(tmp_path / "missing")  # 없으면 통과(만드는 쪽이 0700으로 만든다)
    d = tmp_path / "run"
    d.mkdir()
    d.chmod(0o755)
    vt_paths.check_private_dir(d)
    assert d.stat().st_mode & 0o777 == 0o700
    link = tmp_path / "link"
    link.symlink_to(d)
    with pytest.raises(vt_paths.UnsafeRunDir):
        vt_paths.check_private_dir(link)
    with pytest.raises(vt_paths.UnsafeRunDir):
        vt_paths.check_private_dir(Path("/"))


# --- 2.7 업그레이드 이전 -------------------------------------------------------------


def _legacy(tmp_path):
    old = tmp_path / "oldtmp"
    (old / "vt-pids").mkdir(parents=True)
    (old / "vt-pids" / "server.pid").write_text("123\n")
    (old / "vt-pids" / "tunnels.tsv").write_text("3000\thttps://x\tapp\n")
    (old / "cloudflared.log").write_text("https://sample.trycloudflare.com\n")
    (old / "cloudflared-3000.log").write_text("x\n")
    new = tmp_path / "home" / ".vt" / "run"
    (new / "vt-pids").mkdir(parents=True)
    return old, new


def _migrate(old, new, extra=""):
    return _sh(f'_VT_LEGACY_RUN="{old}"; VT_RUN_DIR="{new}"; PID_DIR="{new}/vt-pids"; {extra}_vt_migrate_legacy_run',
               {"HOME": str(new.parent.parent)})


def test_migration_moves_my_legacy_files(tmp_path):
    old, new = _legacy(tmp_path)
    r = _migrate(old, new)
    assert r.returncode == 0, r.stderr
    assert (new / "vt-pids" / "server.pid").read_text() == "123\n"
    assert (new / "vt-pids" / "tunnels.tsv").exists()
    assert (new / "cloudflared.log").exists() and (new / "cloudflared-3000.log").exists()
    assert not (old / "vt-pids").exists() and not (old / "cloudflared.log").exists()


def test_migration_skips_when_new_dir_is_in_use_or_run_dir_is_explicit(tmp_path):
    old, new = _legacy(tmp_path)
    (new / "vt-pids" / "server.pid").write_text("999\n")
    _migrate(old, new)
    assert (new / "vt-pids" / "server.pid").read_text() == "999\n"  # 새 위치의 것을 덮지 않는다
    assert (old / "vt-pids" / "server.pid").exists()
    (new / "vt-pids" / "server.pid").unlink()
    _migrate(old, new, extra="_VT_RUN_DIR_EXPLICIT=1; ")
    assert (old / "vt-pids" / "server.pid").exists()  # VT_RUN_DIR 를 직접 지정했으면 건드리지 않는다
    _migrate(old, new, extra=f'VT_STATE_DIR="{new.parent}"; ')
    assert (old / "vt-pids" / "server.pid").exists()  # 격리 인스턴스는 기본 인스턴스의 기록을 가져가지 않는다


def test_migration_ignores_symlinked_legacy_entries(tmp_path):
    old, new = _legacy(tmp_path)
    target = tmp_path / "victim.txt"
    target.write_text("secret\n")
    (old / "cloudflared.log").unlink()
    (old / "cloudflared.log").symlink_to(target)
    _migrate(old, new)
    assert not (new / "cloudflared.log").exists()
    assert target.read_text() == "secret\n"


# --- 옛 export(VT_RUN_DIR=/tmp) ------------------------------------------------------
# 2.7 전 bin/fsh 가 늘 export 하던 값이라 그때 띄운 tmux pane·데몬의 환경에 남아 있다. 그 pane 에서 친
# `fsh restart` 가 거부되면 업그레이드 경로가 막힌다.


@pytest.mark.parametrize("legacy", ["/tmp", "/tmp/", "/private/tmp"])
def test_python_treats_legacy_tmp_export_as_unset(monkeypatch, legacy):
    monkeypatch.setenv("VT_RUN_DIR", legacy)
    monkeypatch.setenv("VT_STATE_DIR", "/srv/x/.vt")
    assert vt_paths.run_dir() == Path("/srv/x/.vt/run")
    monkeypatch.setenv("VT_RUN_DIR", "/tmp/iso-run")  # /tmp 아래의 내 폴더는 그대로 존중한다
    assert vt_paths.run_dir() == Path("/tmp/iso-run")


def test_fsh_in_a_pre_upgrade_pane_uses_the_new_default(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".vt.env").write_text("")
    r = subprocess.run([str(ROOT / "bin" / "fsh"), "help"], capture_output=True, text=True, timeout=60,
                       env={"PATH": os.environ["PATH"], "HOME": str(home), "VT_RUN_DIR": "/tmp", "VT_LANG": "en"})
    assert r.returncode == 0, r.stderr
    assert "symbolic link" not in r.stderr
    assert (home / ".vt" / "run" / "vt-pids").is_dir()


# --- 이전은 서버를 내리고 올리는 명령 안에서만 ---------------------------------------------
# 2026-11-03: `fsh update trust` 가 이 Mac 의 **돌고 있는 옛 서버** 곁에서 /tmp 파일을 옮겨 버렸다(서버·터널·워치독은 /tmp 를
# 보고 있다). 이전은 cmd_stop(restart·update --restart 포함)과, 서버가 안 떠 있을 때의 cmd_start 에서만 일어난다.


def test_migration_is_only_called_from_stop_and_start_not_from_the_dispatcher():
    import re
    text = (ROOT / "bin" / "fsh").read_text()
    calls = [m.start() for m in re.finditer(r"^\s*(?:_port_in_use \"\$VT_PORT\" \|\| )?_vt_migrate_legacy_run\s*$", text, re.M)]
    assert len(calls) == 2, "cmd_start 와 cmd_stop 두 곳뿐이어야 한다"
    for pos in calls:
        head = text[:pos].rsplit("\n", 40)[0]
        owner = re.findall(r"^(cmd_\w+)\(\) \{", text[:pos], re.M)[-1]
        assert owner in ("cmd_start", "cmd_stop"), owner
    assert "start|stop|restart|voice|clip|mobile|update|tunnel) _vt_migrate_legacy_run" not in text


def test_non_lifecycle_commands_leave_a_running_legacy_installs_files_alone(tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "vt-pids").mkdir(parents=True)
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        (legacy / "vt-pids" / "server.pid").write_text(f"{sleeper.pid}\n")
        (legacy / "cloudflared.log").write_text("x\n")
        home = tmp_path / "home"
        home.mkdir()
        (home / ".vt.env").write_text("")
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "VT_LANG": "en", "_VT_LEGACY_RUN": str(legacy), "VT_PORT": "7899"}
        for args in (["status"], ["tunnel", "list"], ["help"]):
            r = subprocess.run([str(ROOT / "bin" / "fsh"), *args], capture_output=True, text=True, timeout=60, env=env)
            assert (legacy / "vt-pids" / "server.pid").exists(), (args, r.stderr[-300:])
            assert (legacy / "cloudflared.log").exists(), args
            assert not list((home / ".vt" / "run" / "vt-pids").iterdir()), args
        r = subprocess.run([str(ROOT / "bin" / "fsh"), "status"], capture_output=True, text=True, timeout=60, env=env)
        assert "old location" in r.stdout, "옛 위치에서 서버가 돌면 status 가 알려야 한다"
    finally:
        sleeper.kill()
        sleeper.wait()


def test_migration_ends_a_legacy_watchdog_but_never_an_unrelated_process(tmp_path):
    old, new = _legacy(tmp_path)
    watchdog = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "tunnel_watchdog.py"])
    try:
        (new / "vt-pids" / "tunnel-watchdog.pid").unlink(missing_ok=True)
        (old / "vt-pids" / "tunnel-watchdog.pid").write_text(f"{watchdog.pid}\n")
        _migrate(old, new)
        assert watchdog.wait(timeout=10) is not None            # 끝났다
        assert not (new / "vt-pids" / "tunnel-watchdog.pid").exists()
    finally:
        watchdog.kill()
    old, new = _legacy(tmp_path / "second")
    bystander = subprocess.Popen(["sleep", "60"])                # pid 번호가 재사용된 무관한 프로세스
    try:
        (old / "vt-pids" / "tunnel-watchdog.pid").write_text(f"{bystander.pid}\n")
        _migrate(old, new)
        assert bystander.poll() is None, "워치독이 아닌 프로세스를 끝내면 안 된다"
    finally:
        bystander.kill()
