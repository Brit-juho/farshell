#!/usr/bin/env python3
"""파이썬 해시 잠금 파일(locks/*.txt)이 requirements-*.txt 와 맞는지 본다. 네트워크를 쓰지 않는다.

    python3 scripts/check_python_lock.py          # 문제가 있으면 목록을 찍고 종료코드 1

보는 것:
  1. 잠금 파일의 모든 줄이 `이름==버전 [; 마커]` 이고 sha256 해시가 하나 이상 붙어 있다
     (해시 없는 줄·URL·`-e`·범위 지정이 하나라도 있으면 `pip --require-hashes` 가 설치를 거부한다).
  2. requirements-<이름>.txt 의 모든 요구가 locks/<이름>.txt 에 있고, 잠긴 버전이 그 범위를 만족한다
     (requirements 를 고치고 scripts/lock_python.sh 를 안 돌렸으면 여기서 걸린다).
  3. 같은 패키지가 파일마다 다른 버전으로 잠기지 않았다(core 와 voice 를 한 환경에 같이 설치하기 때문).
  4. locks/ 에 requirements 로 설명되지 않는 파일이 없다.

잠금을 다시 만드는 쪽은 scripts/lock_python.sh 다(uv 필요). 이 검사는 순수 파이썬 + `packaging` 만 쓴다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ("core", "voice", "dev")

try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - 환경 문제를 알기 쉽게
    print("check_python_lock: `packaging` 이 없습니다 — requirements-core.txt 를 설치한 환경(pytest 가 끌고 옴)에서 실행하세요.",
          file=sys.stderr)
    raise SystemExit(2)

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<ver>[^\s;\\]+)\s*(?:;\s*(?P<marker>[^\\]*?))?\s*\\?$")
_HASH = re.compile(r"^--hash=sha256:[0-9a-f]{64}\s*\\?$")


def parse_lock(path: Path) -> tuple[list[dict], list[str]]:
    """[{name, version, marker, hashes}], [문제]."""
    entries: list[dict] = []
    problems: list[str] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash="):
            if not entries:
                problems.append(f"{path.name}:{n}: 해시가 어느 패키지에도 속하지 않는다")
            elif _HASH.match(line):
                entries[-1]["hashes"].append(line.split("=", 1)[1].rstrip(" \\"))
            else:
                problems.append(f"{path.name}:{n}: sha256 해시 형식이 아니다: {line[:60]}")
            continue
        m = _PIN.match(line)
        if not m:
            problems.append(f"{path.name}:{n}: `이름==버전` 고정이 아닌 줄(범위·URL·옵션은 허용하지 않는다): {line[:60]}")
            continue
        try:
            Version(m["ver"])
        except InvalidVersion:
            problems.append(f"{path.name}:{n}: 버전이 올바르지 않다: {m['ver']}")
        entries.append({"name": canonicalize_name(m["name"]), "version": m["ver"],
                        "marker": (m["marker"] or "").strip(), "hashes": [], "line": n})
    for e in entries:
        if not e["hashes"]:
            problems.append(f"{path.name}:{e['line']}: {e['name']}=={e['version']} 에 해시가 없다")
    return entries, problems


def parse_requirements(path: Path) -> tuple[list[Requirement], list[str]]:
    reqs: list[Requirement] = []
    problems: list[str] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(" #", 1)[0].strip() if " #" in raw else raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            reqs.append(Requirement(line))
        except InvalidRequirement:
            problems.append(f"{path.name}:{n}: 읽을 수 없는 요구: {line[:60]}")
    return reqs, problems


def check(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    locked: dict[str, list[dict]] = {}
    for prof in PROFILES:
        req_path = root / f"requirements-{prof}.txt"
        lock_path = root / "locks" / f"{prof}.txt"
        if not req_path.exists():
            problems.append(f"requirements-{prof}.txt 가 없다")
            continue
        if not lock_path.exists():
            problems.append(f"locks/{prof}.txt 가 없다 — scripts/lock_python.sh 를 실행하고 결과를 커밋하세요")
            continue
        entries, p = parse_lock(lock_path)
        problems += p
        locked[prof] = entries
        reqs, p = parse_requirements(req_path)
        problems += p
        by_name: dict[str, list[dict]] = {}
        for e in entries:
            by_name.setdefault(e["name"], []).append(e)
        for r in reqs:
            cands = by_name.get(canonicalize_name(r.name))
            if not cands:
                problems.append(f"locks/{prof}.txt 에 {r.name} 이 없다 (requirements-{prof}.txt 에 있음) — scripts/lock_python.sh 를 다시 돌리세요")
            elif not any(r.specifier.contains(Version(c["version"]), prereleases=True) for c in cands):
                have = ", ".join(sorted({c["version"] for c in cands}))
                problems.append(f"locks/{prof}.txt 의 {r.name} {have} 이 requirements-{prof}.txt 의 `{r.specifier}` 를 만족하지 않는다 — scripts/lock_python.sh 를 다시 돌리세요")
    # 같은 (이름, 마커)가 파일마다 다른 버전이면 core+voice 를 한 환경에 설치할 때 충돌한다.
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for prof, entries in locked.items():
        for e in entries:
            key = (e["name"], e["marker"])
            if key in seen and seen[key][1] != e["version"]:
                problems.append(f"{e['name']} 이 locks/{seen[key][0]}.txt 에서는 {seen[key][1]}, locks/{prof}.txt 에서는 {e['version']} 이다")
            seen.setdefault(key, (prof, e["version"]))
    lock_dir = root / "locks"
    if lock_dir.is_dir():
        for f in sorted(lock_dir.iterdir()):
            if f.suffix != ".txt" or f.stem not in PROFILES:
                problems.append(f"locks/{f.name}: requirements-*.txt 로 설명되지 않는 파일")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("해시 잠금 점검 실패:")
        for p in problems:
            print("  -", p)
        print("\n고치는 법: scripts/lock_python.sh 를 실행하고 바뀐 locks/ 를 커밋하세요(docs/ref/dev.md 「Python dependency lock」).")
        return 1
    n = sum(len(parse_lock(ROOT / "locks" / f"{p}.txt")[0]) for p in PROFILES)
    print(f"해시 잠금 점검 통과 ({len(PROFILES)}개 파일, {n}개 고정)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
