"""`fsh pair …` 의 본체 — `python -m authz pair <start|code|join|status|ls|handoff|accept|end|revoke|doctor> …`.

서버와는 **unix socket 으로만** 말한다(연결의 peer 가 곧 신원). 기계 토큰을 읽지도, 명령 인자에 비밀을 싣지도 않는다. 코드는 서버가 돌려준 값을 화면에 한 번 보일 뿐
(명령 인자로 넘기는 `join <code>` 는 5분짜리 1회용 값이고, 합류의 결과는 코드가 아니라 호출한 세션의 신원으로 안다).

  start [--session S]… [--dir D]… [--role driver|navigator] [--lease] [--elevated A,B] [--with S]   pair 를 만들고 코드를 발급한다
  code <id>                    코드를 다시 보기(대기 중인 코드는 그대로 — 멱등)
  join <code>                  코드로 합류
  status [<id>]                내 pair 목록 또는 한 건(합류자는 이것으로 승인 결과를 안다)
  handoff <id> · accept <id>   driver 의 제안 · navigator 의 수락
  end <id>                     끝내기
  revoke <id> [--verify]       끝내기를 요청하고 --verify 면 정리가 끝났는지(grant·lease·큐·메시지) 확인한다. pair 에 속하지 않은 터미널은 --verify 만 의미가 있다
승인은 이 명령이 아니라 웹/폰의 승인함에서 사람이 한다. 같은 uid 의 프로세스는 `~/.vt` 를 직접 고칠 수 있다 — pair 는 그것을 막는 장치가 아니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import fsh_client
from i18n import tr


def _p(s: str = "") -> None:
    print(s)


def _err(s: str) -> None:
    print(s, file=sys.stderr)


class Unreachable(Exception):
    pass


def _call(method: str, path: str, body: Optional[dict] = None, timeout: float = 10.0) -> tuple[int, dict]:
    try:
        raw_body = json.dumps(body).encode("utf-8") if body is not None else None
        status, raw = fsh_client.socket_request(method, path, body=raw_body, timeout=timeout)
    except fsh_client.SocketUnavailable as e:
        raise Unreachable(str(e)) from e
    except OSError as e:
        raise Unreachable(type(e).__name__) from e
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _fail(status: int, data: dict) -> int:
    if data.get("error") == "pair_disabled":
        _err("  ✗ " + tr("이 서버는 pair 기능이 꺼져 있습니다 — 서버 설정 VT_AUTHZ_ENFORCE 에 pair 를 더하세요"))
        return 1
    _err("  ✗ " + str(data.get("reason") or data.get("error") or f"HTTP {status}"))
    need = data.get("need")
    if need:
        _err("    " + tr("필요한 권한: {scopes} on {res} — request_access 로 사람에게 요청하세요",
                         scopes=",".join(need.get("scopes") or ()), res=need.get("resource")))
    return 1


def _unreachable() -> int:
    _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
    return 1


def _line(v: dict) -> str:
    mem = ", ".join(f"{m.get('name') or m.get('kind')}={m['role']}{'*' if m.get('you') else ''}" for m in v.get("members", []))
    return f"  {v['id']:<14} {v['state']:<16} gen {v.get('generation', 0):<3} {mem}"


def _request(method: str, path: str, body: Optional[dict] = None):
    try:
        return _call(method, path, body)
    except Unreachable:
        return None


def cmd_start(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh pair start")
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--dir", action="append", default=[])
    ap.add_argument("--role", choices=("driver", "navigator"), default="driver")
    ap.add_argument("--lease", action="store_true")
    ap.add_argument("--elevated", default="")
    ap.add_argument("--with", dest="with_", default=None)
    args = ap.parse_args(argv)
    import os
    body = {"sessions": args.session, "dirs": [os.path.abspath(os.path.expanduser(d)) for d in args.dir], "role": args.role,
            "leases": args.lease, "elevated": [x for x in args.elevated.split(",") if x]}
    if args.with_:
        body["with"] = args.with_
    r = _request("POST", "/api/pairs", body)
    if r is None:
        return _unreachable()
    if r[0] not in (200, 201):
        return _fail(*r)
    pid = r[1]["pair"]["id"]
    r = _request("POST", f"/api/pairs/{pid}/code")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    return _show_code(pid, r[1])


def _show_code(pid: str, c: dict) -> int:
    _p(tr("pair {id}", id=pid))
    _p(tr("  코드: {code}   확인 문구: {phrase}", code=c["code"], phrase=c["phrase"]))
    _p(tr("  5분 안에 한 번만 쓸 수 있습니다. 상대가 'fsh pair join <코드>' 를 하면 사람이 승인함에서 확인 문구를 보고 승인합니다."))
    return 0


def cmd_code(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh pair code")
    ap.add_argument("id")
    args = ap.parse_args(argv)
    r = _request("POST", f"/api/pairs/{args.id}/code")
    if r is None:
        return _unreachable()
    return _show_code(args.id, r[1]) if r[0] == 200 else _fail(*r)


def cmd_join(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh pair join")
    ap.add_argument("code")
    args = ap.parse_args(argv)
    r = _request("POST", "/api/pairs/join", {"code": args.code})
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    v = r[1]["pair"]
    _p(tr("합류했습니다: {id} — 내 역할 {role}", id=v["id"], role=v.get("role")))
    _p(tr("  확인 문구: {phrase} (사람이 승인할 때 이 문구를 봅니다)", phrase=v.get("phrase")))
    _p(tr("  승인을 기다리는 중 — 'fsh pair status {id}' 로 결과를 확인하세요", id=v["id"]))
    return 0


def cmd_status(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh pair status")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = _request("GET", f"/api/pairs/{args.id}/status" if args.id else "/api/pairs")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    if args.json:
        _p(json.dumps(r[1], ensure_ascii=False))
        return 0
    rows = [r[1]["pair"]] if args.id else (r[1].get("pairs") or [])
    if not rows:
        _p(tr("pair 가 없습니다"))
        return 0
    for v in rows:
        _p(_line(v))
        if args.id:
            _p("    " + tr("승인 {state} · 내 역할 {role}{drv}", state=(v.get("approval") or {}).get("state") or "-", role=v.get("role") or "-",
                           drv=tr(" (운전 중)") if v.get("isDriver") else ""))
            if v.get("endReason"):
                _p("    " + tr("끝난 이유: {r}", r=v["endReason"]))
    return 0


def _simple(sub: str, argv: list[str], method: str, tail: str, done: str) -> int:
    ap = argparse.ArgumentParser(prog=f"fsh pair {sub}")
    ap.add_argument("id")
    args = ap.parse_args(argv)
    r = _request(method, f"/api/pairs/{args.id}{tail}")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    _p(done)
    _p(_line(r[1]["pair"]))
    return 0


def cmd_revoke(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh pair revoke")
    ap.add_argument("id")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    r = _request("DELETE", f"/api/pairs/{args.id}")
    if r is None:
        return _unreachable()
    ended = r[0] == 200
    if not ended and not (args.verify and r[0] == 404):
        return _fail(*r)
    if ended:
        _p(tr("pair {id} 을 끝냈습니다", id=args.id))
    elif r[0] == 404:
        _p(tr("이 호출자는 pair {id} 의 구성원이 아닙니다 — 끝내기·회수는 구성원 세션이나 웹/폰의 사람이 합니다. 정리 상태만 확인합니다", id=args.id))
    if not args.verify:
        return 0
    return _verify(args.id)


def _verify(pid: str) -> int:
    """정리가 끝났는지 — 끝난 pair 이고, 회수 saga 가 확인을 마쳤고, pair grant·lease 가 남지 않았다. 잠시 기다리며 몇 번 다시 본다."""
    last = None
    for _ in range(10):
        r = _request("GET", "/api/pairs")
        if r is None:
            return _unreachable()
        if r[0] != 200:
            return _fail(*r)
        v = next((x for x in r[1].get("pairs", []) if x.get("id") == pid), None)
        if v is None:
            _err("  ✗ " + tr("pair {id} 이 목록에 없습니다(구성원이 아니거나 이미 정리돼 사라짐)", id=pid))
            return 1
        last = v
        c = v.get("cleanup") or {}
        if v["state"] in ("ended", "expired", "revoked") and c.get("done"):
            _p(tr("확인 완료: 끝남({state}) · 남은 grant {g} · 남은 lease {l} · 회수 정리 끝", state=v["state"], g=c.get("grants", 0), l=c.get("leases", 0)))
            return 0
        time.sleep(0.5)
    c = (last or {}).get("cleanup") or {}
    _err("  ✗ " + tr("아직 정리가 끝나지 않았습니다 — 상태 {state} · 남은 grant {g} · 남은 lease {l} (서버가 이어서 처리합니다; 잠시 뒤 다시 확인하세요)",
                     state=(last or {}).get("state"), g=c.get("grants", "?"), l=c.get("leases", "?")))
    return 1


def cmd_doctor(argv: list[str]) -> int:
    """`fsh doctor` 가 쓴다 — `enabled|active|waiting`. 항상 0(진단이지 판정이 아니다)."""
    from . import flags, pairs
    try:
        d, _bad = pairs.store._read("pairs")
        active = sum(1 for x in d["items"] if x.get("state") in pairs.RUNNING_STATES)
        waiting = sum(1 for x in d["items"] if x.get("state") == pairs.JOINED)
    except Exception:  # noqa: BLE001
        active = waiting = -1
    _p(f"{int(flags.enforced('pair'))}|{active}|{waiting}")
    return 0


def main(argv: list[str]) -> int:
    sub, rest = (argv[0] if argv else "status"), argv[1:]
    if sub == "start":
        return cmd_start(rest)
    if sub == "code":
        return cmd_code(rest)
    if sub == "join":
        return cmd_join(rest)
    if sub in ("status", "ls", "list"):
        return cmd_status(rest)
    if sub == "handoff":
        return _simple("handoff", rest, "POST", "/handoff", tr("handoff 를 제안했습니다 — 상대가 'fsh pair accept' 하면 바뀝니다(2분 안에)"))
    if sub == "accept":
        return _simple("accept", rest, "POST", "/handoff/accept", tr("수락했습니다 — 이제 내가 driver 입니다"))
    if sub == "end":
        return _simple("end", rest, "DELETE", "", tr("pair 를 끝냈습니다"))
    if sub == "revoke":
        return cmd_revoke(rest)
    if sub == "doctor":
        return cmd_doctor(rest)
    _err(tr("사용법: fsh pair <start [--session S] [--dir D] [--role driver|navigator] [--lease] [--elevated a,b]|code <id>|join <code>|status [<id>]|handoff <id>|accept <id>|end <id>|revoke <id> [--verify]>"))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
