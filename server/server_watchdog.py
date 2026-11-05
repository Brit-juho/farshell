#!/usr/bin/env python3
"""서버 워치독 — 서버가 일을 못 받으면 진단을 모으고 다시 띄운다.

2026-11-05 M5: 서버 프로세스는 살아 있고 포트도 열려 있었지만 기본 스레드 풀이 전부 막혀 인증된 API 가
몇 시간 동안 응답하지 않았다. `fsh status`는 포트만 봐서 정상이라 했고, 다시 띄우는 장치도 없었다.

동작(`CHECK_SEC`마다)
1. `/api/health/ready`를 부른다(unix 소켓 우선, 없으면 기계 토큰으로 TCP). 200 이면 정상(degraded 도
   정상으로 본다 — 일은 받고 있다). 503(unready)·기한 초과·연결 실패는 실패.
2. 서버 pid 파일이 없으면 사람이 일부러 내린 것이다 — 아무것도 하지 않는다.
   pid 파일은 있는데 프로세스가 없으면(죽음) 실패로 센다.
3. 실패가 `FAILS`번 이어지면
   a. 진단 묶음을 `$VT_RUN_DIR/incidents/<시각>/`에 남긴다: SIGUSR1 로 받은 전 스레드 스택, 마지막 ready
      응답, 스레드 상태(ps), 마운트·NFS 상태, 서버 로그 꼬리. 다음에 같은 일이 생기면 원인이 여기 있다.
   b. 재시작 예산(`RESTART_MAX`회/시간) 안이면 `fsh restart`를 별도 세션으로 띄운다(접속 주소 유지).
      예산을 넘기면 다시 띄우지 않고 기록만 한다 — 원인이 그대로면 재시작해도 또 막히기 때문이다.
4. 서버가 새로 뜬 직후(`GRACE_SEC`)는 판정하지 않는다.

상태는 `$VT_RUN_DIR/server-watchdog.json`에 남는다(`fsh status`·`fsh doctor`가 읽는다).
끄기: `VT_SERVER_WATCHDOG=0`. 실행: `fsh start`가 띄운다(macOS 는 `fsh watchdog agent install`로 launchd 감독도 가능).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import fsh_client  # noqa: E402
import vt_env  # noqa: E402
import vt_paths  # noqa: E402

logger = logging.getLogger("server-watchdog")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [server-watchdog] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

_VT_ENV = vt_env.load()


def _get(key: str, default: str = "") -> str:
    return vt_env.getenv(key, default, file_env=_VT_ENV)


CHECK_SEC = float(_get("VT_WATCHDOG_CHECK_SEC", "15"))
PROBE_TIMEOUT = float(_get("VT_WATCHDOG_PROBE_TIMEOUT", "8"))
FAILS = int(_get("VT_WATCHDOG_FAILS", "3"))
GRACE_SEC = float(_get("VT_WATCHDOG_GRACE_SEC", "60"))
RESTART_MAX = int(_get("VT_WATCHDOG_RESTART_MAX", "3"))
RESTART_WINDOW = 3600.0
KEEP_INCIDENTS = 20
FSH_BIN = _HERE.parent / "bin" / "fsh"


def _port() -> str:
    return str(_get("VT_PORT", os.environ.get("PORT") or fsh_client.DEFAULT_PORT)).strip()


def _run_dir() -> Path:
    return vt_paths.run_dir()


def _pid_file() -> Path:
    return _run_dir() / "vt-pids" / "server.pid"


def _status_file() -> Path:
    return _run_dir() / "server-watchdog.json"


def _restarts_file() -> Path:
    return _run_dir() / "server-watchdog-restarts.json"


def _server_pid() -> int | None:
    try:
        return int(_pid_file().read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# --- 판정 -----------------------------------------------------------------------------------
def probe() -> tuple[str, dict | None, str]:
    """('ok'|'unready'|'down', ready JSON, 설명)."""
    path = "/api/health/ready"
    try:
        status, body = fsh_client.socket_request("GET", path, timeout=PROBE_TIMEOUT, port=_port())
        return _classify(status, body, "socket")
    except fsh_client.SocketUnavailable:
        pass
    except OSError as e:          # 붙은 뒤 기한 초과·끊김 — 소켓 쪽도 응답하지 않는다
        return "down", None, f"socket: {e}"
    req = urllib.request.Request(f"http://127.0.0.1:{_port()}{path}")
    token = _get("VT_AUTH_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            return _classify(r.status, r.read(), "tcp")
    except urllib.error.HTTPError as e:
        return _classify(e.code, e.read() or b"", "tcp")
    except (OSError, ValueError) as e:
        return "down", None, f"tcp: {e}"


def _classify(status: int, body: bytes, via: str) -> tuple[str, dict | None, str]:
    try:
        data = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        data = None
    if status == 200:
        return "ok", data, f"{via} 200"
    if status == 503 and isinstance(data, dict) and "problems" in data:
        return "unready", data, f"{via} 503 {','.join(data.get('problems') or [])}"
    if status in (401, 403):
        # 판정을 못 받는다(자격 문제) — 서버는 답했다. 재시작 사유가 아니다.
        return "ok", None, f"{via} {status} (자격 문제로 ready 를 못 읽음)"
    return "unready", data, f"{via} {status}"


# --- 진단 묶음 ------------------------------------------------------------------------------
def _cmd(argv: list[str], timeout: float = 10) -> str:
    if not shutil.which(argv[0]):
        return f"({argv[0]} 없음)\n"
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, text=True, errors="replace",
                           start_new_session=True)
        return (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return f"({' '.join(argv)}: {timeout:g}s 초과)\n"
    except OSError as e:
        return f"({' '.join(argv)}: {e})\n"


def _tail(path: Path, lines: int = 300) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", "replace")
        return "\n".join(data.splitlines()[-lines:]) + "\n"
    except OSError as e:
        return f"({path}: {e})\n"


def collect_incident(reason: str, last_ready: dict | None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = _run_dir() / "incidents"
    d = base / stamp
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    pid = _server_pid()

    def w(name: str, text: str) -> None:
        p = d / name
        p.write_text(text, encoding="utf-8")
        os.chmod(p, 0o600)

    w("reason.txt", f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{reason}\nserver pid={pid}\n")
    if last_ready is not None:
        w("ready.json", json.dumps(last_ready, ensure_ascii=False, indent=1))
    stacks = _run_dir() / "stacks.log"
    before = stacks.stat().st_size if stacks.exists() else 0
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGUSR1)       # resilience/watch.py 의 faulthandler 가 전 스레드 스택을 덧붙인다
            time.sleep(1.5)
        except OSError:
            pass
    try:
        with open(stacks, "rb") as f:
            f.seek(max(0, before - 64 * 1024))   # 덤프 + 그 직전 watch 기록(오래 도는 작업 스택)
            w("stacks.txt", f.read().decode("utf-8", "replace"))
    except OSError as e:
        w("stacks.txt", f"(stacks.log 없음: {e})\n")
    if pid:
        if sys.platform == "darwin":
            w("ps.txt", _cmd(["ps", "-M", "-p", str(pid)]) + _cmd(["ps", "-o", "pid,ppid,stat,etime,command", "-p", str(pid)]))
        else:
            w("ps.txt", _cmd(["ps", "-L", "-o", "pid,tid,stat,wchan:32,etime,comm", "-p", str(pid)]))
        if shutil.which("lsof"):
            w("lsof.txt", _cmd(["lsof", "-nP", "-p", str(pid)], timeout=15))
    w("mount.txt", _cmd(["mount"]))
    if sys.platform == "darwin":
        w("nfsstat.txt", _cmd(["nfsstat", "-m"]))
    w("server-log-tail.txt", _tail(_run_dir() / "vt-server.log"))
    _prune_incidents(base)
    logger.warning(f"진단 묶음 저장: {d}")
    return d


def _prune_incidents(base: Path) -> None:
    try:
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return
    for old in dirs[:-KEEP_INCIDENTS]:
        shutil.rmtree(old, ignore_errors=True)


# --- 재시작 ---------------------------------------------------------------------------------
def _restarts() -> list[float]:
    try:
        data = json.loads(_restarts_file().read_text())
        return [float(t) for t in data if time.time() - float(t) < RESTART_WINDOW]
    except (OSError, ValueError, TypeError):
        return []


def _save_restarts(ts: list[float]) -> None:
    try:
        _restarts_file().write_text(json.dumps(ts))
        os.chmod(_restarts_file(), 0o600)
    except OSError:
        pass


def restart(reason: str) -> bool:
    recent = _restarts()
    if len(recent) >= RESTART_MAX:
        logger.error(f"최근 1시간에 {len(recent)}번 재시작했다 — 더 띄우지 않는다(원인 확인 필요): {reason}")
        return False
    recent.append(time.time())
    _save_restarts(recent)
    log = open(_run_dir() / "vt-server-watchdog-restart.log", "ab")
    os.chmod(_run_dir() / "vt-server-watchdog-restart.log", 0o600)
    # 별도 세션 — `fsh restart`의 stop 단계가 이 워치독을 내려도 재시작은 끝까지 간다.
    subprocess.Popen([str(FSH_BIN), "restart"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True)
    logger.warning(f"fsh restart 실행 ({len(recent)}/{RESTART_MAX}회/시간): {reason}")
    return True


# --- 상태 기록 ------------------------------------------------------------------------------
def _write_status(**info) -> None:
    try:
        tmp = _status_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), **info}, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _status_file())
    except OSError:
        pass


def _single_instance():
    """이미 다른 워치독이 돌고 있으면 None. 잠금 파일은 열린 채로 둔다(프로세스가 끝나면 풀린다)."""
    f = open(_run_dir() / "server-watchdog.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def summary() -> str:
    """`fsh status`·`fsh doctor`가 그대로 찍는 줄들. 첫 줄 `READY <ok|degraded|unready|down|stopped>`."""
    lines = []
    pid = _server_pid()
    if pid is None or not _alive(pid):
        lines.append("READY stopped")
    else:
        state, data, why = probe()
        status = (data or {}).get("status") if state == "ok" else state
        lines.append(f"READY {status or state}")
        if data:
            for prob in data.get("problems") or []:
                lines.append("PROBLEM " + prob)
            for op in (data.get("slow_ops") or [])[:5]:
                lines.append(f"SLOW {op['pool']}:{op['op']} {op['age']:.0f}s {op.get('key') or ''}".rstrip())
            for lk in data.get("locks") or []:
                h = lk.get("holder") or {}
                if h.get("held_for"):
                    lines.append(f"LOCK {lk['name']} {h['held_for']:.0f}s {h.get('op') or h.get('thread') or ''}".rstrip())
            for q in data.get("quarantine") or []:
                lines.append(f"QUARANTINE {q['path']} ({q['reason']})")
        elif state != "ok":
            lines.append("WHY " + why)
    try:
        st = json.loads(_status_file().read_text())
    except (OSError, ValueError):
        st = None
    if st and _alive(st.get("pid")):
        lines.append(f"WATCHDOG on {st.get('mode', 'fsh')} {st.get('state', '?')} restarts={st.get('restarts', 0)}")
    else:
        lines.append("WATCHDOG off")
    inc = _run_dir() / "incidents"
    try:
        dirs = sorted(p.name for p in inc.iterdir() if p.is_dir())
    except OSError:
        dirs = []
    if dirs:
        lines.append(f"INCIDENTS {len(dirs)} {inc / dirs[-1]}")
    return "\n".join(lines)


def write_plist(path: str) -> None:
    import plistlib
    data = {
        "Label": "com.farshell.server-watchdog",
        "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "--mode", "launchd"],
        "RunAtLoad": True,
        # 비정상 종료면 다시 띄운다. VT_SERVER_WATCHDOG=0 으로 스스로 끝낸 경우(종료 코드 0)는 그대로 둔다.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(_run_dir() / "vt-server-watchdog-agent.log"),
        "StandardErrorPath": str(_run_dir() / "vt-server-watchdog-agent.log"),
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="fsh", choices=["fsh", "launchd"])
    ap.add_argument("--once", action="store_true", help="한 번만 판정하고 결과를 출력한다(진단용)")
    ap.add_argument("--summary", action="store_true", help="fsh status/doctor 용 사람이 읽는 요약(여러 줄)")
    ap.add_argument("--quarantine", action="store_true", help="탐색 격리 목록을 출력한다")
    ap.add_argument("--unquarantine", action="store_true", help="탐색 격리 목록을 비우고 푼 개수를 출력한다")
    ap.add_argument("--write-plist", metavar="PATH", help="launchd 감독용 plist 를 쓴다(fsh watchdog agent install)")
    args = ap.parse_args(argv)

    if args.once:
        state, data, why = probe()
        print(json.dumps({"state": state, "why": why, "ready": data}, ensure_ascii=False, indent=1))
        return 0 if state == "ok" else 1
    if args.summary:
        print(summary())
        return 0
    if args.quarantine or args.unquarantine:
        from resilience import quarantine
        if args.unquarantine:
            print(quarantine.clear())
        else:
            for e in quarantine.entries():
                print(f"QUARANTINE {e['path']} ({e['reason']}, {e['until_in']}s, {e['count']}회)")
        return 0
    if args.write_plist:
        write_plist(args.write_plist)
        return 0

    if _get("VT_SERVER_WATCHDOG", "1").strip().lower() in ("0", "false", "no", "off"):
        logger.info("VT_SERVER_WATCHDOG=0 — 감시하지 않는다")
        return 0
    lock = _single_instance()
    if lock is None and args.mode == "fsh":
        logger.info("다른 서버 워치독이 이미 돌고 있다 — 끝낸다")
        return 0
    # launchd 감독판은 fsh 판이 돌고 있으면 기다렸다가, 그 판이 내려가면(fsh stop·재시작 중) 이어받는다.
    while lock is None:
        time.sleep(30)
        lock = _single_instance()

    logger.info(f"감시 시작 — {CHECK_SEC:g}s 간격, 연속 {FAILS}회 실패면 진단 저장 후 재시작"
                f"(시간당 최대 {RESTART_MAX}회), 기동 직후 {GRACE_SEC:g}s 유예, mode={args.mode}")
    fails = 0
    last_pid = None
    pid_seen_at = time.monotonic()
    last_ready = None
    restarting_until = 0.0
    while True:
        time.sleep(CHECK_SEC)
        now = time.monotonic()
        pid = _server_pid()
        if pid != last_pid:
            last_pid, pid_seen_at, fails = pid, now, 0
        if pid is None:
            fails = 0
            _write_status(mode=args.mode, state="stopped", checked=time.time(), fails=0)
            continue                                # 사람이 내렸다
        if now < restarting_until or now - pid_seen_at < GRACE_SEC:
            _write_status(mode=args.mode, state="grace", checked=time.time(), fails=fails, server_pid=pid)
            continue
        if not _alive(pid):
            state, data, why = "down", None, f"server pid {pid} 없음"
        else:
            state, data, why = probe()
        if data is not None:
            last_ready = data
        if state == "ok":
            if fails:
                logger.info(f"회복: {why}")
            fails = 0
        else:
            fails += 1
            logger.warning(f"판정 실패 {fails}/{FAILS}: {why}")
        _write_status(mode=args.mode, state=state, why=why, checked=time.time(), fails=fails,
                      server_pid=pid, restarts=len(_restarts()))
        if fails >= FAILS:
            reason = f"연속 {fails}회 실패 — {why}"
            try:
                collect_incident(reason, last_ready)
            except Exception as e:  # noqa: BLE001 — 진단 실패가 복구를 막으면 안 된다
                logger.error(f"진단 묶음 저장 실패: {e}")
            if restart(reason):
                restarting_until = time.monotonic() + 180
            fails = 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
