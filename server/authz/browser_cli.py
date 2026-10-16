"""`fsh browser …` 의 본체 — `python -m authz browser <adapters|enable|tabs|close> …`.

서버와는 **unix socket 으로만** 말한다(연결의 peer 가 곧 신원). 어댑터를 켜고 끄는 것·모든 탭을 보고 닫는 것은 사람(웹)과 터미널의 fsh 의 일이다 — 에이전트는 못 한다.
서버의 브라우저 스위치(`VT_AUTHZ_ENFORCE` 의 `browser`)가 꺼져 있으면 라우트가 404 라서 «꺼져 있다»고 알려 준다.

  adapters                 어댑터 목록(켜짐 · 꺼짐 · 사용 불가와 이유)
  enable <name> [--off] [--yes]   어댑터 켜기(--off 로 끄기). 처음 켤 때는 동의가 필요하다(--yes 또는 대화형 확인)
  tabs [--json]            열린 탭(소유자·상태)
  close <id>               탭 닫기
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


def _call(method: str, path: str, body: Optional[dict] = None, timeout: float = 30.0) -> tuple[int, dict]:
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


def _request(method: str, path: str, body: Optional[dict] = None):
    try:
        return _call(method, path, body)
    except Unreachable:
        return None


def _unreachable() -> int:
    _err("  ✗ " + tr("서버의 unix socket 에 연결하지 못했습니다 — 'fsh status' 로 서버를 확인하세요"))
    return 1


def _fail(status: int, data: dict) -> int:
    if status == 404 and data.get("detail") == "Not Found":
        _err("  ✗ " + tr("이 서버는 브라우저 기능이 꺼져 있습니다 — 서버 설정 VT_AUTHZ_ENFORCE 에 browser 를 더하세요"))
        return 1
    _err("  ✗ " + str(data.get("reason") or data.get("error") or f"HTTP {status}"))
    return 1


def cmd_adapters(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh browser adapters")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = _request("GET", "/api/browser/adapters")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    if args.json:
        _p(json.dumps(r[1], ensure_ascii=False))
        return 0
    for a in r[1].get("adapters", []):
        state = tr("켜짐") if a.get("enabled") else (tr("꺼짐") if a.get("available") else tr("사용 불가 ({reason})", reason=a.get("reason")))
        _p(f"  {a['name']:<22} {a.get('profile', 'isolated'):<9} {state}")
    if r[1].get("configError"):
        _err("  ! " + tr("설정 파일을 읽을 수 없어 모든 어댑터를 꺼짐으로 봅니다"))
    return 0


def _ask(text: str) -> bool:
    if not sys.stdin.isatty():
        return False
    _p(text)
    try:
        return input(tr("계속할까요? [y/N] ")).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_enable(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh browser enable")
    ap.add_argument("name")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--yes", action="store_true", help="첫 사용 동의를 명시한다")
    args = ap.parse_args(argv)
    body = {"enabled": not args.off, "consent": bool(args.yes)}
    r = _request("POST", f"/api/browser/adapters/{args.name}/enable", body)
    if r is None:
        return _unreachable()
    if r[0] == 409 and r[1].get("error") == "consent_required":
        if not _ask(str(r[1].get("consentText") or r[1].get("reason") or "")):
            _err("  ✗ " + tr("동의가 필요합니다 — 내용을 확인한 뒤 --yes 를 붙여 다시 실행하세요"))
            return 1
        r = _request("POST", f"/api/browser/adapters/{args.name}/enable", {**body, "consent": True})
        if r is None:
            return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    _p(tr("어댑터 {name} 을(를) {state}", name=args.name, state=tr("껐습니다") if args.off else tr("켰습니다")))
    return 0


def cmd_tabs(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh browser tabs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = _request("GET", "/api/browser/tabs")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    if args.json:
        _p(json.dumps(r[1], ensure_ascii=False))
        return 0
    rows = r[1].get("tabs") or []
    if not rows:
        _p(tr("열린 탭이 없습니다"))
        return 0
    for t in rows:
        who = t.get("ownerName") or t.get("owner") or ("-" if not t.get("humanTaken") else tr("사람"))
        age = int(max(0, time.time() - float(t.get("lastUsed") or 0)))
        _p(f"  {t['id']:<14} {t['state']:<9} {t['adapter']:<12} {who}  ({age}s)")
    return 0


def cmd_close(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="fsh browser close")
    ap.add_argument("id")
    args = ap.parse_args(argv)
    r = _request("DELETE", f"/api/browser/tabs/{args.id}")
    if r is None:
        return _unreachable()
    if r[0] != 200:
        return _fail(*r)
    _p(tr("탭 {id} 을(를) 닫았습니다", id=args.id))
    return 0


def main(argv: list[str]) -> int:
    sub, rest = (argv[0] if argv else "adapters"), argv[1:]
    return {"adapters": cmd_adapters, "enable": cmd_enable, "tabs": cmd_tabs, "close": cmd_close}.get(sub, lambda _a: _usage())(rest)


def _usage() -> int:
    _err(tr("사용법: fsh browser <adapters|enable <이름> [--off] [--yes]|tabs|close <id>>"))
    return 2
