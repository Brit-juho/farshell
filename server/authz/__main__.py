"""`python -m authz migrate-auth …` — `fsh migrate-auth`의 본체(bin/fsh 가 server/ 에서 부른다).

    migrate-auth [--dry-run]          계획만 출력(기본). 아무것도 쓰지 않는다
    migrate-auth --apply              백업 → 이전(멱등) → 감사 기록
    migrate-auth --verify             (1) 저장소 = 지금 옛 저장소의 번역인가(원본 id 기준)
                                      (2) 개발 체크아웃이면 authz_char/matrix.json 을 이전된 저장소로 재생
    migrate-auth --backup             백업만
    migrate-auth --restore <ts>       백업 검증 후 복원(복원 전 현재 상태를 다시 백업)
    migrate-auth --status             이전 여부·백업 목록·shadow 불일치 수·감사 체인
    공통: --json (기계용 출력)

`--finalize`(옛 저장소 정리)는 8단계 기능이라 이 단계에는 없다.

## --verify 의 matrix 재생을 어떻게 하는가
matrix.json 은 HTTP 결과(상태 코드)의 golden 이고, 엔진 입력(주체·scope·리소스)이 아니다. 그래서
"엔진만 따로 재생"하려면 사례마다 판정 지점 규칙을 한 번 더 손으로 적어야 하고, 그 사본이 실제
판정 지점과 갈라지면 재생은 아무것도 증명하지 못한다. 대신 **0단계 harness 를 그대로 돌린다**:
사례마다 가짜 세계를 만들고 `migrate.apply`로 그 세계의 옛 저장소를 이전한 뒤, shadow 를 `store`
소스로 켜고 실제 요청을 보낸다. 실제 판정 지점의 shadow 호출이 이전된 저장소만 보고 판정하므로
"이전 규칙 + 엔진"이 기존 판정과 같은지가 사례 단위로 드러난다(test_authz_shadow.py 의 store 소스).
사용자의 실제 ~/.vt 는 (1)에서만 읽고, (2)는 tmp 세계만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _p(s: str = "") -> None:
    print(s)


def _fmt_stats(name: str, st: dict) -> str:
    return f"  {name:<10} +{st['added']} ~{st['updated']} -{st['removed']} ={st['same']}"


def _dry_run(as_json: bool) -> int:
    from . import migrate, store
    p = migrate.plan()
    d = migrate.diff(p)
    if as_json:
        out = {k: v for k, v in p.items() if k not in ("peers",)}
        out["peers"] = [{k: v for k, v in x.items() if k != "secret_sha256"} for x in p["peers"]]
        out["diff"] = d
        _p(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    _p(f"fsh migrate-auth --dry-run  (저장소 {store.root()}, 쓰지 않음)")
    _p(f"  tmux 읽기: {'됨 — 세션 ' + str(p['tmux_sessions']) + '개' if p['tmux_readable'] else '안 됨(F3 — 죽은 세션 정리 안 함)'}")
    _p("이전 대상:")
    kinds: dict[str, int] = {}
    for g in p["grants"]:
        k = g["origin"].split(":")[1]
        kinds[k] = kinds.get(k, 0) + 1
    for k, n in sorted(kinds.items()):
        _p(f"  grant ← {k:<14} {n}")
    _p(f"  approval(대기·거절 쿨다운)  {len(p['approvals'])}")
    _p(f"  공유 정책(shares)          {len(p['shares'])}")
    _p(f"  peer 등록부                {len(p['peers'])}  (secret 은 sha256 만 — 1단계)")
    _p(f"  가져올 옛 로그             {', '.join(p['logs']) or '없음'}")
    dr = p["dropped"]
    _p(f"버림: 죽은 세션 {dr['dead_session']}, 만료 요청 {dr['expired']}, 빈 grant {dr['empty']}")
    _p("저장소에 적용하면(+추가 ~갱신 -제거 =같음):")
    for k, st in d.items():
        _p(_fmt_stats(k, st))
    for g in p["grants"]:
        _p(f"    {g['origin']:<40} {g['subject']} → {g['resource']}  [{', '.join(g['scopes'])}]")
    if p["warnings"]:
        _p("경고:")
        for w in p["warnings"]:
            _p(f"  ! {w}")
    _p("적용: fsh migrate-auth --apply   (먼저 ~/.vt/authz/backup/<ts>.tgz 백업)")
    return 0


def _apply(as_json: bool) -> int:
    from . import migrate
    r = migrate.apply()
    if as_json:
        _p(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    _p(f"이전 완료 — 백업 {r['backup']}")
    for k in ("grants", "approvals", "shares", "peers"):
        _p(_fmt_stats(k, r[k]))
    _p(f"  옛 로그 복사 {r['logs']['copied']}, 이미 가져옴 {r['logs']['skipped']}")
    for w in r["warnings"]:
        _p(f"  ! {w}")
    return 0


def _repo_tests() -> Path | None:
    t = Path(__file__).resolve().parents[1] / "tests" / "test_authz_shadow.py"
    return t if t.is_file() and (t.parent / "authz_char" / "matrix.json").is_file() else None


def _replay_matrix() -> tuple[str, dict | None]:
    """(상태, 차이) — 상태: ok | diff | skipped:<이유> | error:<요약>."""
    t = _repo_tests()
    if t is None:
        return "skipped:개발 체크아웃이 아님(server/tests/authz_char 없음)", None
    try:
        import pytest  # noqa: F401
    except ImportError:
        return "skipped:pytest 없음(.venv 의 dev 의존성)", None
    with tempfile.TemporaryDirectory(prefix="fsh-authz-verify-") as td:
        report = Path(td) / "report.json"
        env = {**os.environ, "VT_AUTHZ_VERIFY_ONLY": "store", "VT_AUTHZ_VERIFY_REPORT": str(report),
               "HOME": td}   # 재생은 tmp 세계만 — 그래도 HOME 까지 막아 둔다
        for k in ("VT_STATE_DIR", "VT_WORKSPACE_PATH"):
            env.pop(k, None)
        cmd = [sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:randomly", "-o", "addopts=",
               "-k", "test_shadow_matches_current_decision"]
        r = subprocess.run(cmd, cwd=str(t.parents[2]), env=env, capture_output=True, text=True)
        found = json.loads(report.read_text(encoding="utf-8")).get("store", {}) if report.is_file() else None
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode == 0:
            return f"ok:{tail}", found
        return (f"diff:{tail}" if found else f"error:{tail}"), found


def _verify(as_json: bool) -> int:
    from . import migrate
    s = migrate.verify_store()
    status, found = _replay_matrix()
    if as_json:
        _p(json.dumps({"store": s, "matrix": {"status": status, "diffs": found}}, ensure_ascii=False, indent=1,
                      default=str))
    else:
        _p(f"(1) 저장소 ↔ 옛 저장소 번역: {'일치' if s['clean'] else '차이 있음'}")
        for k, st in s["diff"].items():
            _p(_fmt_stats(k, st))
        _p(f"(2) matrix.json 재생(이전된 저장소, store 소스): {status}")
        for cid, rows in (found or {}).items():
            for r in rows:
                _p(f"    {cid}: {r.get('site')} {r.get('scope')} {r.get('resource')} "
                   f"old={r.get('old')} new={r.get('new')} ({r.get('reason')})")
    ok = s["clean"] and (status.startswith("ok") or status.startswith("skipped"))
    return 0 if ok else 1


def _status(as_json: bool) -> int:
    from . import migrate
    st = migrate.status()
    if as_json:
        _p(json.dumps(st, ensure_ascii=False, indent=1, default=str))
        return 0
    m = st["meta"]
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["applied_at"])) if st["migrated"] else "-"
    _p(f"이전: {'됨 ' + when if st['migrated'] else '안 됨'}  {m.get('counts') or ''}")
    _p(f"shadow 불일치 기록: {st['shadow_mismatches']}줄")
    a = st["audit"]
    _p(f"감사 체인: {'온전' if a['ok'] else '깨짐 seq=' + str(a['broken_seq'])} ({a['lines']}줄)")
    _p("백업:")
    for b in st["backups"] or [{"ts": "없음"}]:
        _p(f"  {b.get('ts')}  {b.get('error') or str(b.get('files', '')) + '개 파일'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fsh migrate-auth", description="옛 권한 저장소 → authz 이전(plan-2.6 1단계)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--backup", action="store_true")
    g.add_argument("--restore", metavar="TS")
    g.add_argument("--status", action="store_true")
    g.add_argument("--finalize", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from . import migrate, store
    try:
        if args.finalize:
            _p("--finalize 는 8단계(옛 저장소 제거)에 생깁니다 — 지금은 쓸 수 없습니다.")
            return 2
        if args.apply:
            return _apply(args.json)
        if args.verify:
            return _verify(args.json)
        if args.backup:
            m = migrate.backup()
            _p(json.dumps(m, indent=1) if args.json else f"백업 {m['ts']} ({len(m['files'])}개 파일) → {migrate.backup_dir()}")
            return 0
        if args.restore:
            r = migrate.restore(args.restore)
            if args.json:
                _p(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                _p(f"복원 완료 — {r['from']} ({len(r['restored'])}개 복원, {len(r['removed'])}개 제거)")
                _p(f"  복원 전 상태는 {r['pre_restore_backup']} 로 백업됨. 서버를 재시작하세요(fsh restart).")
            return 0
        if args.status:
            return _status(args.json)
        return _dry_run(args.json)
    except (migrate.MigrateError, store.StoreError) as e:
        _p(f"✗ {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "migrate-auth":
        sys.exit(main(sys.argv[2:]))
    print("사용법: python -m authz migrate-auth [--dry-run|--apply|--verify|--backup|--restore <ts>|--status] [--json]")
    sys.exit(2)
