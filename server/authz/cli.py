"""`fsh authz …` 의 본체 — `python -m authz cli <whoami|grants|requests|revoke|audit|panic>`.

unix socket 로만 서버에 말한다. 서버는 연결의 peer 로 호출자를 안다: pane 밖이면 `local:cli`, pane 안이면 에이전트다.
**CLI 는 보기만 한다** — 승인·회수·비상 정지는 로그인한 사람(웹/폰)의 일이다. `revoke`·`panic` 은 서버를 부르지 않고 그 사실을 알린다
(에이전트가 터미널에서 자기 권한을 스스로 정리하거나 승인하는 길이 없어야 한다).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Optional

import fsh_client
from i18n import tr


def _p(s: str = "") -> None:
    print(s)


def _err(s: str) -> None:
    print(s, file=sys.stderr)


def _get(path: str) -> tuple[int, dict]:
    try:
        status, raw = fsh_client.socket_request("GET", path, timeout=8.0, svc="cli")
    except fsh_client.SocketUnavailable:
        _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
        return 0, {}
    except OSError as e:
        _err("  ✗ " + tr("서버 호출 실패: {why}", why=str(e)))
        return 0, {}
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _fail(status: int, data: dict) -> int:
    if status == 0:
        return 1
    why = data.get("reason") or data.get("error") or f"HTTP {status}"
    _err("  ✗ " + str(why))
    return 1


_SINCE = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")


def parse_since(text: Optional[str], now: Optional[float] = None) -> float:
    """`10m`·`2h`·`1d`(지금부터 그만큼 전) 또는 epoch 초. 비었으면 0(처음부터)."""
    if not text:
        return 0.0
    m = _SINCE.match(text.strip())
    if not m:
        raise ValueError(text)
    n, unit = float(m.group(1)), m.group(2)
    if not unit:
        return n
    return (time.time() if now is None else now) - n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _age(sec) -> str:
    sec = int(sec or 0)
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{sec // 60}m"
    return f"{sec // 3600}h" if sec < 172800 else f"{sec // 86400}d"


def _stamp(ts) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(float(ts or 0)))


def cmd_whoami(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh authz whoami")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    status, data = _get("/api/authz/whoami")
    if status != 200:
        return _fail(status, data)
    if args.json:
        _p(json.dumps(data, ensure_ascii=False))
        return 0
    pr = data.get("principal") or {}
    _p("  " + tr("호출자: {kind} {id} (경로 {via})", kind=pr.get("kind"), id=pr.get("id"), via=pr.get("via")))
    if data.get("scopes") is None:
        _p("  " + tr("유효 권한을 계산하지 못했습니다(권한 저장소 문제)"))
        return 0
    _p("  " + tr("유효 scope: {scopes}", scopes=", ".join(data["scopes"]) or tr("없음")))
    _p("  " + tr("맞는 프리셋: {presets}", presets=", ".join(p["label"] for p in data.get("presets", [])) or tr("없음")))
    return 0


def cmd_grants(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh authz grants")
    ap.add_argument("--kind", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--resource", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("kind", args.kind), ("subject", args.subject), ("resource", args.resource)) if v})
    status, data = _get("/api/authz/grants" + (f"?{q}" if q else ""))
    if status != 200:
        return _fail(status, data)
    if args.json:
        _p(json.dumps(data, ensure_ascii=False))
        return 0
    rows = data.get("grants", [])
    if not rows:
        _p("  " + tr("권한이 없습니다"))
        return 0
    for g in rows:
        pre = (g.get("preset") or {}).get("label") or ",".join(g.get("scopes", [])[:3])
        tail = []
        if g.get("maxUses") is not None:
            tail.append(tr("{n}회 남음", n=max(0, int(g["maxUses"]) - int(g.get("uses") or 0))))
        if g.get("expires"):
            tail.append(tr("만료 {ts}", ts=_stamp(g["expires"])))
        if g.get("lineage"):
            tail.append(tr("위임 {id}", id=g["lineage"]["delegation"]))
        _p(f"  {g['id']:<24} {g['kind']:<8} {(g['subject'].get('name') or g['subject'].get('id') or '-')[:24]:<24} "
           f"{str(g.get('resource') or '-')[:30]:<30} {pre} {' '.join(tail)}".rstrip())
    return 0


def cmd_requests(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh authz requests")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    status, data = _get("/api/authz/approvals")
    if status != 200:
        return _fail(status, data)
    if args.json:
        _p(json.dumps(data, ensure_ascii=False))
        return 0
    rows = data.get("approvals", [])
    if not rows:
        _p("  " + tr("승인 대기 중인 요청이 없습니다"))
        return 0
    for a in rows:
        pre = (a.get("preset") or {}).get("label") or ",".join(a.get("scopes", [])[:3])
        el = " " + tr("[재인증 필요]") if a.get("elevated") else ""
        _p(f"  {a['id']:<20} {a['kind']:<8} {(a['subject'].get('name') or '-')[:20]:<20} → "
           f"{(a['resource'].get('label') or a['resource'].get('id') or '-')[:24]:<24} {pre}  {_age(a.get('age'))}{el}")
    _p("")
    _p("  " + tr("승인·거절은 웹 또는 폰의 승인함에서 합니다 — 터미널에서는 승인하지 않습니다"))
    return 0


def cmd_revoke(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh authz revoke")
    ap.add_argument("id", nargs="?")
    args = ap.parse_args(argv)
    if not args.id:
        _err(tr("사용법: fsh authz revoke <grant id>"))
        return 2
    _p("  " + tr("권한 {id} 의 회수는 로그인한 사람이 웹 또는 폰의 권한 화면에서 합니다 — 터미널에서는 승인도 회수도 하지 않습니다", id=args.id))
    return 3


def cmd_panic(argv: list[str]) -> int:
    _p("  " + tr("비상 정지는 재인증한 사람이 웹 또는 폰의 권한 화면에서 합니다 — 터미널에서는 하지 않습니다"))
    return 3


def cmd_audit(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh authz audit")
    ap.add_argument("--since", default="")
    ap.add_argument("--principal", default="")
    ap.add_argument("--resource", default="")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--follow", "-f", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        since = parse_since(args.since)
    except ValueError:
        _err(tr("--since 는 10m · 2h · 1d 또는 epoch 초입니다"))
        return 2
    from urllib.parse import urlencode

    def fetch(since_v: float, limit: int):
        q = urlencode({k: v for k, v in (("principal", args.principal), ("resource", args.resource),
                                          ("since", since_v or ""), ("limit", limit)) if v != ""})
        return _get("/api/authz/audit?" + q)

    def line(e: dict) -> str:
        scopes = ",".join(e.get("scopes") or [])
        extra = e.get("reason") or e.get("result") or ""
        return (f"  {_stamp(e.get('ts'))} {e.get('source', ''):<7} {e.get('decision') or '-':<7} {e.get('action', '-'):<20} "
                f"{str(e.get('principal') or '-')[:30]:<30} {str(e.get('resource') or '-')[:30]} {scopes} {extra}").rstrip()

    status, data = fetch(since, args.limit)
    if status != 200:
        return _fail(status, data)
    if not args.json:
        _p("  " + (tr("감사 체인: 정상") if data.get("chain") == "ok" else tr("감사 체인: 깨짐 (seq {seq})", seq=data.get("brokenSeq"))))
    seen = set()
    for e in reversed(data.get("entries", [])):
        seen.add((e.get("source"), e.get("seq"), e.get("ts"), e.get("action")))
        _p(json.dumps(e, ensure_ascii=False) if args.json else line(e))
    if not args.follow:
        return 0
    last = max([float(e.get("ts") or 0) for e in data.get("entries", [])] or [since or time.time()])
    try:
        while True:
            time.sleep(2.0)
            status, data = fetch(last, 200)
            if status != 200:
                continue
            for e in reversed(data.get("entries", [])):
                key = (e.get("source"), e.get("seq"), e.get("ts"), e.get("action"))
                if key in seen:
                    continue
                seen.add(key)
                last = max(last, float(e.get("ts") or 0))
                _p(json.dumps(e, ensure_ascii=False) if args.json else line(e))
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 0


COMMANDS = {"whoami": cmd_whoami, "grants": cmd_grants, "requests": cmd_requests, "revoke": cmd_revoke, "audit": cmd_audit,
            "panic": cmd_panic}


def usage() -> str:
    return tr("사용법: fsh authz <whoami|grants|requests|revoke <id>|audit [--follow] [--since 10m]|panic>")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help") or argv[0] not in COMMANDS:
        _p(usage())
        return 0 if argv and argv[0] in ("-h", "--help", "help") else 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
