"""FarShell.app 실행 껍데기(macos/farshell-launcher.c, lib/macos_app.sh) — macOS 권한 책임자를 FarShell 로 고정.

권한(TCC) 자체는 시험하지 않는다 — 보호 위치에 닿으면 사람이 눌러야 하는 창이 뜬다. 책임자가 FarShell 로 바뀌는
것은 2026-11-06 실측으로 확인했다(tccd: `responsible=com.farshell.server-test … accessing=com.apple.ls`).
여기서는 그 효과가 유지되는 조건과 껍데기의 동작을 고정한다:
- 껍데기는 소스·Info.plist 가 바뀔 때만 다시 빌드한다(다시 빌드하면 서명이 바뀌어 macOS 가 다시 묻는다).
- 종료 코드·인자를 그대로 넘기고, 실제 명령의 pid 를 FSH_APP_PIDFILE 에 쓰고, 시그널을 넘긴다(무시 중인 것은 그대로).
- 프로세스 구조: 바깥 FarShell → 안쪽 FarShell(책임자) → 명령.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("cc") or not shutil.which("codesign"),
    reason="macOS + cc + codesign 필요",
)


def _sh(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "$VT_DIR/lib/macos_app.sh"; {script}'], capture_output=True,
                          text=True, timeout=120, env={**os.environ, **env})


@pytest.fixture
def app(tmp_path):
    env = {"VT_DIR": str(ROOT), "VT_STATE_DIR": str(tmp_path / "state")}
    env.pop("VT_MACOS_APP", None)
    r = _sh("_vt_app_prefix", env)
    assert r.returncode == 0, r.stderr
    exe = r.stdout.strip()
    assert exe.endswith("FarShell.app/Contents/MacOS/FarShell") and os.access(exe, os.X_OK)
    return {"exe": exe, "env": env, "tmp": tmp_path}


def test_app_is_signed_as_farshell_and_built_once(app):
    info = subprocess.run(["codesign", "-dv", str(Path(app["exe"]).parents[2])], capture_output=True, text=True)
    assert "Identifier=com.farshell.server" in info.stderr
    before = os.stat(app["exe"]).st_mtime_ns
    assert _sh("_vt_app_state", app["env"]).stdout.strip() == "ready"
    _sh("_vt_app_ensure", app["env"])
    assert os.stat(app["exe"]).st_mtime_ns == before, "소스가 그대로인데 다시 빌드했다(macOS 가 새 앱으로 보고 다시 묻는다)"


def test_app_rebuilds_only_when_source_changes(tmp_path):
    src = tmp_path / "repo"
    (src / "lib").mkdir(parents=True)
    (src / "macos").mkdir()
    shutil.copy(ROOT / "lib" / "macos_app.sh", src / "lib" / "macos_app.sh")
    for f in ("farshell-launcher.c", "Info.plist", "AppIcon.icns"):
        shutil.copy(ROOT / "macos" / f, src / "macos" / f)
    env = {"VT_DIR": str(src), "VT_STATE_DIR": str(tmp_path / "state")}
    exe = _sh("_vt_app_prefix", env).stdout.strip()
    first = os.stat(exe).st_mtime_ns
    with open(src / "macos" / "farshell-launcher.c", "a") as f:
        f.write("\n/* changed */\n")
    assert _sh("_vt_app_state", env).stdout.strip() == "stale"
    time.sleep(0.01)
    assert _sh("_vt_app_prefix", env).stdout.strip() == exe
    assert os.stat(exe).st_mtime_ns != first


def _repo_copy(tmp_path, files):
    src = tmp_path / "repo"
    (src / "lib").mkdir(parents=True)
    (src / "macos").mkdir()
    shutil.copy(ROOT / "lib" / "macos_app.sh", src / "lib" / "macos_app.sh")
    for f in files:
        shutil.copy(ROOT / "macos" / f, src / "macos" / f)
    return src, {"VT_DIR": str(src), "VT_STATE_DIR": str(tmp_path / "state")}


def test_app_has_icon_and_it_is_part_of_the_hash(tmp_path):
    src, env = _repo_copy(tmp_path, ("farshell-launcher.c", "Info.plist", "AppIcon.icns"))
    bundle = Path(_sh("_vt_app_prefix", env).stdout.strip()).parents[2]
    assert (bundle / "Contents" / "Resources" / "AppIcon.icns").is_file()
    assert subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], capture_output=True).returncode == 0
    plist = subprocess.run(["plutil", "-extract", "CFBundleIconFile", "raw", str(bundle / "Contents" / "Info.plist")],
                           capture_output=True, text=True)
    assert plist.stdout.strip() == "AppIcon"
    assert _sh("_vt_app_state", env).stdout.strip() == "ready"
    with open(src / "macos" / "AppIcon.icns", "ab") as f:
        f.write(b"\0")
    assert _sh("_vt_app_state", env).stdout.strip() == "stale"


def test_app_builds_without_icon_file(tmp_path):
    _, env = _repo_copy(tmp_path, ("farshell-launcher.c", "Info.plist"))
    exe = _sh("_vt_app_prefix", env).stdout.strip()
    assert exe and os.access(exe, os.X_OK)
    assert not (Path(exe).parents[1] / "Resources").exists()
    assert _sh("_vt_app_state", env).stdout.strip() == "ready"


def test_disabled_by_env(tmp_path):
    env = {"VT_DIR": str(ROOT), "VT_STATE_DIR": str(tmp_path / "state"), "VT_MACOS_APP": "0"}
    r = _sh("_vt_app_prefix; echo rc=$?", env)
    assert r.stdout.strip() == "rc=0" or r.stdout.strip().endswith("rc=0")
    assert "FarShell" not in r.stdout
    assert _sh("_vt_app_state", env).stdout.strip() == "off"
    assert not (tmp_path / "state" / "app").exists()


def test_exit_code_and_argv_pass_through(app):
    r = subprocess.run([app["exe"], "/bin/sh", "-c", "exit 7"], timeout=30)
    assert r.returncode == 7
    out = subprocess.run([app["exe"], "/usr/bin/printf", "%s|", "a b", "c"], capture_output=True, text=True, timeout=30)
    assert out.stdout == "a b|c|"
    assert subprocess.run([app["exe"], "/nonexistent-cmd"], capture_output=True, timeout=30).returncode == 127


def test_pidfile_holds_real_command_pid_and_is_not_inherited(app):
    pidfile = app["tmp"] / "child.pid"
    seen = app["tmp"] / "seen"
    script = f'echo $$ > "{seen}"; env | grep -c FSH_APP_ >> "{seen}" || true'
    r = subprocess.run([app["exe"], "/bin/sh", "-c", script], env={**os.environ, "FSH_APP_PIDFILE": str(pidfile)},
                       timeout=30)
    assert r.returncode == 0
    real_pid, leaked = seen.read_text().split()
    assert pidfile.read_text().strip() == real_pid
    assert leaked == "0", "FSH_APP_* 가 실제 명령의 환경에 남았다"


def _parent(pid: int) -> tuple[int, str]:
    """(부모 pid, 부모 실행 파일 이름)."""
    ppid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout)
    comm = subprocess.run(["ps", "-o", "comm=", "-p", str(ppid)], capture_output=True, text=True).stdout.strip()
    return ppid, comm


def test_process_tree_and_signal_forwarding(app):
    pidfile = app["tmp"] / "child.pid"
    outer = subprocess.Popen([app["exe"], "/bin/sleep", "60"], env={**os.environ, "FSH_APP_PIDFILE": str(pidfile)})
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        child = int(pidfile.read_text())
        inner, inner_comm = _parent(child)
        assert inner_comm.endswith("FarShell")
        assert _parent(inner)[0] == outer.pid and _parent(inner)[1].endswith("FarShell")
        outer.send_signal(signal.SIGTERM)                    # 바깥에 보낸 SIGTERM 이 실제 명령까지 간다
        assert outer.wait(timeout=10) == 128 + signal.SIGTERM
        time.sleep(0.2)
        assert subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode != 0
    finally:
        if outer.poll() is None:
            outer.kill()


def test_ignored_sighup_stays_ignored(app):
    """nohup 으로 띄운 서버가 터미널이 닫힐 때(SIGHUP) 같이 죽지 않아야 한다 — 무시 중이던 시그널은 넘기지 않는다."""
    pidfile = app["tmp"] / "child.pid"
    outer = subprocess.Popen([app["exe"], "/bin/sleep", "60"], env={**os.environ, "FSH_APP_PIDFILE": str(pidfile)},
                             preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        child = int(pidfile.read_text())
        outer.send_signal(signal.SIGHUP)
        time.sleep(0.3)
        assert outer.poll() is None
        assert subprocess.run(["kill", "-0", str(child)], capture_output=True).returncode == 0
    finally:
        outer.terminate()
        outer.wait(timeout=10)
