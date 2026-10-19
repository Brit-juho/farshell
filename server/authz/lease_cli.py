"""`fsh lease …` 의 본체 — `python -m authz lease [--cwd DIR] <ls|acquire|renew|release|check|guard|doctor> …`.

서버와는 **unix socket 으로만** 말한다(연결의 peer 가 곧 신원). 기계 토큰을 읽지도, 명령 인자에 비밀을 싣지도 않는다.

## pre-commit guard (`fsh lease guard install|uninstall|status`)
현재 저장소의 `pre-commit` 훅이 `fsh lease check --staged` 를 부른다. 이미 있는 훅은 덮지 않고 `pre-commit.fsh-chained` 로 옮겨 **먼저 실행**한다
(훅 이름만 바꾸므로 실행 권한·내용이 그대로다. 지울 때 되돌린다). `core.hooksPath` 가 있으면 그 폴더를 쓴다.
스테이지된 경로는 `git diff --cached --name-only -z --no-renames` 로 읽는다 — NUL 로 나눠 받아 이름에 공백·개행이 있어도 안전하고,
이름 바꾸기도 옛 이름·새 이름 둘 다 보인다. 경로는 서버로 가는 본문에만 실리고 명령 인자에는 실리지 않는다.

- 다른 세션의 lease 가 걸린 파일이 있으면 보유자 닉네임과 파일 목록을 보이고 종료 코드 1(커밋 중단).
- 서버가 안 닿거나(소켓 없음·연결 실패·서버가 lease API 를 모름) 응답이 이상하면 **경고 한 줄만 내고 커밋을 통과**시킨다(fail open):
  lease 는 권고이고 guard 는 선택 설치다. 건너뛴 사실은 `<상태 폴더>/authz/guard-skipped.log` 에 시각·사유·파일 개수만 한 줄 남긴다(경로 없음).
- guard 는 같은 계정의 셸이 `git commit --no-verify` 로 건너뛸 수 있다 — 막는 장치가 아니라 알리는 장치다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import fsh_client
from i18n import tr

MARK = "# fsh-lease-guard v1"
CHAINED = "pre-commit.fsh-chained"
SKIP_LOG = "guard-skipped.log"
GIT_TIMEOUT = 15.0


def _p(s: str = "") -> None:
    print(s)


def _err(s: str) -> None:
    print(s, file=sys.stderr)


# --- 서버 호출 --------------------------------------------------------------------------------


class Unreachable(Exception):
    pass


def _call(method: str, path: str, body: Optional[dict] = None, timeout: float = 8.0) -> tuple[int, dict]:
    try:
        raw_body = json.dumps(body).encode("utf-8") if body is not None else None
        status, raw = fsh_client.socket_request(method, path, body=raw_body, timeout=timeout)
    except fsh_client.SocketUnavailable as e:
        raise Unreachable(str(e)) from e
    except OSError as e:
        raise Unreachable(f"{type(e).__name__}") from e
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _fail(status: int, data: dict) -> int:
    why = data.get("reason") or data.get("error") or f"HTTP {status}"
    _err("  ✗ " + str(why))
    need = data.get("need")
    if need:
        _err("    " + tr("필요한 권한: {scopes} on {res} — request_access 로 사람에게 요청하세요",
                         scopes=",".join(need.get("scopes") or ()), res=need.get("resource")))
    if data.get("holder"):
        _err("    " + tr("보유자 {nick}, 만료 {when}", nick=data["holder"], when=_stamp(data.get("expires"))))
    return 1


def _stamp(ts) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "-"


_TTL = re.compile(r"^(\d+)([smh]?)$")


def parse_ttl(text: Optional[str]) -> Optional[int]:
    """`90`·`30m`·`2h` → 초. 비었으면 None(서버 기본 30분)."""
    if text in (None, ""):
        return None
    m = _TTL.match(str(text).strip())
    if not m:
        raise ValueError(text)
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


# --- ls · acquire · renew · release -------------------------------------------------------------


def cmd_ls(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh lease ls")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        status, data = _call("GET", "/api/leases")
    except Unreachable:
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 1
    if status != 200:
        return _fail(status, data)
    rows = data.get("leases") or []
    if args.json:
        _p(json.dumps(data, ensure_ascii=False))
        return 0
    if not rows:
        _p(tr("lease 가 없습니다"))
        return 0
    _p(f"  {'ID':<14} {'HOLDER':<14} {'MODE':<6} {'EXCL':<5} {'LEFT':>6}  GLOB")
    for l in rows:
        left = int(l.get("remaining") or 0)
        _p(f"  {l['id']:<14} {l['holder'][:14]:<14} {l['mode']:<6} {'yes' if l.get('exclusive') else '-':<5} "
           f"{(str(left // 60) + 'm') if left >= 60 else (str(left) + 's'):>6}  {l['glob']}" + (f"  ({l['reason']})" if l.get("reason") else ""))
    if not data.get("enforced"):
        _p("  " + tr("(권고 모드 — 서버가 쓰기를 막지 않습니다: VT_AUTHZ_ENFORCE 에 lease 가 없음)"))
    return 0


def cmd_acquire(argv: list[str], cwd: Optional[str] = None) -> int:
    ap = argparse.ArgumentParser(prog="fsh lease acquire")
    ap.add_argument("glob")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--read", action="store_true")
    ap.add_argument("--exclusive", action="store_true")
    ap.add_argument("--ttl", default=None)
    ap.add_argument("--reason", default="")
    args = ap.parse_args(argv)
    try:
        ttl = parse_ttl(args.ttl)
    except ValueError:
        _err("  ✗ " + tr("--ttl 은 90, 30m, 2h 같은 값이어야 합니다"))
        return 2
    # 상대 경로는 fsh 를 부른 폴더(--cwd) 기준이다 — 이 프로세스는 server/ 에서 돈다(bin/fsh 가 cd 한다).
    glob = os.path.expanduser(args.glob)
    if not os.path.isabs(glob):
        glob = os.path.join(cwd or os.getcwd(), glob)
    if not any(c in glob for c in "*?["):
        glob = os.path.abspath(glob)
    body = {"glob": glob, "mode": "read" if args.read else "write", "exclusive": args.exclusive, "reason": args.reason}
    if ttl is not None:
        body["ttl"] = ttl
    try:
        status, data = _call("POST", "/api/leases", body)
    except Unreachable:
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 1
    if status != 201:
        return _fail(status, data)
    l = data["lease"]
    _p(tr("lease {id} — {glob} ({mode}{ex}) {min}분", id=l["id"], glob=l["glob"], mode=l["mode"],
          ex=", exclusive" if l.get("exclusive") else "", min=int(l.get("remaining", 0)) // 60))
    return 0


def cmd_renew(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh lease renew")
    ap.add_argument("id")
    ap.add_argument("--ttl", default=None)
    args = ap.parse_args(argv)
    try:
        ttl = parse_ttl(args.ttl)
    except ValueError:
        _err("  ✗ " + tr("--ttl 은 90, 30m, 2h 같은 값이어야 합니다"))
        return 2
    try:
        status, data = _call("POST", f"/api/leases/{args.id}/renew", {"ttl": ttl} if ttl is not None else {})
    except Unreachable:
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 1
    if status != 200:
        return _fail(status, data)
    l = data["lease"]
    _p(tr("lease {id} 갱신 — 남은 {min}분{cap}", id=l["id"], min=int(l.get("remaining", 0)) // 60,
          cap=tr(" (최대 시간에 닿음)") if l.get("capped") else ""))
    return 0


def cmd_release(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh lease release")
    ap.add_argument("id")
    args = ap.parse_args(argv)
    try:
        status, data = _call("DELETE", f"/api/leases/{args.id}")
    except Unreachable:
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 1
    if status != 200:
        return _fail(status, data)
    _p(tr("lease {id} 을 놓았습니다", id=args.id))
    return 0


# --- check · guard -----------------------------------------------------------------------------


def _git(cwd: str, *args: str) -> Optional[bytes]:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_top(cwd: str) -> Optional[str]:
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return out.decode("utf-8", "surrogateescape").strip() if out else None


def staged_paths(top: str) -> list[str]:
    """스테이지된 파일의 절대 경로 — NUL 구분. 이름 바꾸기는 옛 이름·새 이름 둘 다(`--no-renames`)."""
    out = _git(top, "diff", "--cached", "--name-only", "-z", "--no-renames")
    if not out:
        return []
    return [os.path.join(top, n) for n in out.decode("utf-8", "surrogateescape").split("\0") if n]


def state_dir() -> Path:
    return Path(fsh_client.state_dir())


def log_skip(reason: str, files: int) -> None:
    """건너뛴 사실 한 줄 — 시각·사유·파일 개수뿐(경로·저장소 이름 없음). 실패해도 커밋을 막지 않는다."""
    try:
        d = state_dir() / "authz"
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        p = d / SKIP_LOG
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} skipped reason={reason} files={files}\n")
    except OSError:
        pass


def skipped_24h() -> int:
    p = state_dir() / "authz" / SKIP_LOG
    try:
        n, cutoff = 0, time.time() - 86400
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                if time.mktime(time.strptime(line.split(" ", 1)[0][:19], "%Y-%m-%dT%H:%M:%S")) >= cutoff:
                    n += 1
            except (ValueError, OverflowError):
                continue
        return n
    except OSError:
        return 0


def _warn_skipped(reason: str, files: int) -> int:
    _err(tr("fsh: lease guard 건너뜀({reason}) — lease 는 권고라 커밋은 계속합니다", reason=reason))
    log_skip(reason, files)
    return 0


def cmd_check(argv: list[str], cwd: str) -> int:
    ap = argparse.ArgumentParser(prog="fsh lease check")
    ap.add_argument("--staged", action="store_true", help="스테이지된 파일을 확인(pre-commit guard)")
    ap.add_argument("--holder", default="")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)
    top = repo_top(cwd)
    if args.staged:
        if top is None:
            return 0                                   # git 저장소가 아니면 확인할 것이 없다
        paths = staged_paths(top)
        if not paths:
            return 0
    else:
        paths = [os.path.abspath(os.path.join(cwd, os.path.expanduser(x))) for x in args.paths]
        if not paths:
            _err("  ✗ " + tr("확인할 경로를 주세요 (또는 --staged)"))
            return 2
    body = {"paths": paths}
    if args.holder and not args.staged:
        body["holder"] = args.holder
    try:
        status, data = _call("POST", "/api/leases/check", body, timeout=5.0)
    except Unreachable:
        if args.staged:
            return _warn_skipped("server_unreachable", len(paths))
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 2
    if status != 200:
        if args.staged:
            return _warn_skipped(f"http_{status}", len(paths))
        return _fail(status, data)
    rows = data.get("conflicts") or []
    if not rows:
        if not args.staged:
            _p(tr("충돌 없음 ({n}개 확인)", n=data.get("checked", len(paths))))
        return 0
    base = top or cwd
    by: dict = {}
    for r in rows:
        by.setdefault(r.get("holder") or "?", []).append(r)
    _err("  ✗ " + tr("다른 세션이 lease 를 잡은 파일이 {n}개 있습니다", n=len(rows)))
    for nick, rs in by.items():
        _err("    " + tr("{nick} (만료 {when}):", nick=nick, when=_stamp(rs[0].get("expires"))))
        for r in rs[:20]:
            try:
                shown = os.path.relpath(r["path"], base)
            except ValueError:
                shown = r["path"]
            _err(f"      {shown}")
        if len(rs) > 20:
            _err("      " + tr("… 외 {n}개", n=len(rs) - 20))
    if args.staged:
        _err("    " + tr("lease 가 끝나길 기다리거나 보유자와 정리하세요. 이 guard 는 알리는 장치입니다 — git commit --no-verify 로 건너뛸 수 있습니다."))
    return 1


# --- guard 설치 ----------------------------------------------------------------------------------


def hooks_dir(cwd: str) -> Optional[Path]:
    out = _git(cwd, "rev-parse", "--git-path", "hooks")
    if not out:
        return None
    p = Path(out.decode("utf-8", "surrogateescape").strip())
    return p if p.is_absolute() else Path(cwd) / p


def hook_text(fsh_bin: str) -> str:
    q = "'" + fsh_bin.replace("'", "'\\''") + "'"
    return f"""#!/bin/sh
{MARK} — installed by `fsh lease guard install`; remove with `fsh lease guard uninstall`.
# 1) runs the pre-commit hook that was here before (kept as {CHAINED}),
# 2) asks FarShell whether another session holds a lease on a staged file.
# Leases are advisory: if the server cannot be reached this only prints a warning and the commit goes on.
hook_dir=$(dirname "$0")
if [ -x "$hook_dir/{CHAINED}" ]; then
  "$hook_dir/{CHAINED}" "$@" || exit $?
fi
FSH_BIN={q}
[ -x "$FSH_BIN" ] || FSH_BIN=$(command -v fsh 2>/dev/null)
if [ -z "$FSH_BIN" ]; then
  echo "fsh: lease guard skipped (fsh not found) - leases are advisory, commit continues" >&2
  exit 0
fi
exec "$FSH_BIN" lease check --staged
"""


def _is_ours(p: Path) -> bool:
    try:
        return MARK in p.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return False


def guard_state(cwd: str) -> str:
    """`installed` | `chained`(기존 훅을 이어서 실행) | `absent` | `foreign`(다른 pre-commit 훅이 있음) | `no-repo`."""
    d = hooks_dir(cwd)
    if d is None:
        return "no-repo"
    h = d / "pre-commit"
    if h.is_file() and _is_ours(h):
        return "chained" if (d / CHAINED).exists() else "installed"
    return "foreign" if h.exists() else "absent"


def guard_install(cwd: str, fsh_bin: str) -> tuple[int, str]:
    d = hooks_dir(cwd)
    if d is None:
        return 1, tr("git 저장소가 아닙니다")
    d.mkdir(parents=True, exist_ok=True)
    h, chained = d / "pre-commit", d / CHAINED
    note = ""
    if h.exists() or h.is_symlink():
        if _is_ours(h):
            note = tr("(이미 설치됨 — 내용만 새로 씁니다)")
        else:
            if chained.exists() or chained.is_symlink():
                return 1, tr("{name} 가 이미 있어 기존 pre-commit 훅을 옮길 수 없습니다 — 직접 정리한 뒤 다시 하세요", name=CHAINED)
            os.rename(h, chained)                      # 이름만 바꾼다 — 내용·실행 권한 그대로
            note = tr("(기존 pre-commit 훅은 {name} 로 옮겨 먼저 실행합니다)", name=CHAINED)
    tmp = d / f".pre-commit.fsh-tmp-{os.getpid()}"
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(hook_text(fsh_bin))
        os.chmod(tmp, 0o755)
        os.replace(tmp, h)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return 1, tr("훅을 쓰지 못했습니다: {why}", why=str(e))
    return 0, f"{h} {note}".strip()


def guard_uninstall(cwd: str) -> tuple[int, str]:
    d = hooks_dir(cwd)
    if d is None:
        return 1, tr("git 저장소가 아닙니다")
    h, chained = d / "pre-commit", d / CHAINED
    if h.exists() and _is_ours(h):
        h.unlink()
        if chained.exists() or chained.is_symlink():
            os.rename(chained, h)
            return 0, tr("guard 를 지우고 기존 pre-commit 훅을 되돌렸습니다")
        return 0, tr("guard 를 지웠습니다")
    if not h.exists() and chained.exists():
        os.rename(chained, h)                          # 설치 도중 끊긴 흔적 — 기존 훅을 되돌린다
        return 0, tr("기존 pre-commit 훅을 되돌렸습니다")
    return 0, tr("설치된 guard 가 없습니다")


def cmd_guard(argv: list[str], cwd: str, fsh_bin: str) -> int:
    sub = argv[0] if argv else ""
    if sub == "install":
        rc, msg = guard_install(cwd, fsh_bin)
    elif sub == "uninstall":
        rc, msg = guard_uninstall(cwd)
    elif sub == "status":
        st = guard_state(cwd)
        _p({"installed": tr("설치됨"), "chained": tr("설치됨(기존 훅과 이어짐)"), "absent": tr("설치 안 됨"),
            "foreign": tr("설치 안 됨(다른 pre-commit 훅이 있음)"), "no-repo": tr("git 저장소가 아님")}[st])
        return 0
    else:
        _err(tr("사용법: fsh lease guard <install|uninstall|status>"))
        return 2
    (_p if rc == 0 else _err)(("  " if rc == 0 else "  ✗ ") + msg)
    if rc == 0 and sub == "install":
        _p("  " + tr("lease 는 권고입니다 — 서버가 안 닿으면 경고만 하고 커밋은 통과합니다. 같은 계정의 셸은 --no-verify 로 건너뛸 수 있습니다."))
    return rc


def cmd_doctor(argv: list[str], cwd: str) -> int:
    """`fsh doctor` 가 쓴다 — `lease_count|guard_state|skipped_24h|enforced|sagas_pending`. 항상 0(진단이지 판정이 아니다)."""
    from . import flags, leases, saga
    try:
        n = len(leases.active_now())
    except Exception:  # noqa: BLE001
        n = -1
    try:
        pend = len(saga.pending())
    except Exception:  # noqa: BLE001
        pend = -1
    _p(f"{n}|{guard_state(cwd)}|{skipped_24h()}|{int(flags.enforced('lease'))}|{pend}")
    return 0


def main(argv: list[str]) -> int:
    cwd = os.getcwd()
    fsh_bin = ""
    while argv and argv[0] in ("--cwd", "--fsh"):
        if len(argv) < 2:
            return 2
        if argv[0] == "--cwd":
            cwd = argv[1]
        else:
            fsh_bin = argv[1]
        argv = argv[2:]
    sub, rest = (argv[0] if argv else "ls"), argv[1:]
    if sub in ("ls", "list"):
        return cmd_ls(rest)
    if sub == "acquire":
        return cmd_acquire(rest, cwd)
    if sub == "renew":
        return cmd_renew(rest)
    if sub in ("release", "rm"):
        return cmd_release(rest)
    if sub == "check":
        return cmd_check(rest, cwd)
    if sub == "guard":
        return cmd_guard(rest, cwd, fsh_bin or "fsh")
    if sub == "doctor":
        return cmd_doctor(rest, cwd)
    _err(tr("사용법: fsh lease <ls|acquire <glob> [--write|--read] [--exclusive] [--ttl 30m]|renew <id>|release <id>|check [--staged]|guard install|uninstall|status>"))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
