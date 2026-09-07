#!/usr/bin/env python3
"""I4 — CHANGELOG에서 한 버전의 절만 뽑아 릴리스 노트를 만든다.

왜 스크립트인가: 릴리스 노트를 손으로 다시 쓰면 CHANGELOG와 두 벌이 되고,
둘 중 하나는 반드시 낡는다(이 저장소가 이미 겪은 드리프트 패턴 — I3 참고).
**CHANGELOG가 단일 진실**이고 릴리스 노트는 그 파생물이다.

사용:
    python3 scripts/release_notes.py 1.7.0            # stdout으로
    python3 scripts/release_notes.py 1.7.0 -o OUT.md  # 파일로
    python3 scripts/release_notes.py v1.7.0           # 'v' 접두사도 받는다
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

HEAD_RE = re.compile(r"^##\s*\[([^\]]+)\]\s*[—\-–]?\s*(.*)$")


def extract(version: str, text: str) -> tuple[str, str] | None:
    """(제목줄, 본문). 못 찾으면 None."""
    lines = text.splitlines()
    start = None
    heading = ""
    for i, line in enumerate(lines):
        m = HEAD_RE.match(line)
        if not m:
            continue
        if start is None and m.group(1) == version:
            start, heading = i, line
            continue
        if start is not None:
            # 다음 릴리스 헤딩에서 끊는다
            return heading, "\n".join(lines[start + 1:i]).strip()
    if start is None:
        return None
    return heading, "\n".join(lines[start + 1:]).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="CHANGELOG → 릴리스 노트 (I4)")
    ap.add_argument("version", help="1.7.0 또는 v1.7.0")
    ap.add_argument("-o", "--out", help="출력 파일(기본: stdout)")
    args = ap.parse_args()

    version = args.version.lstrip("vV")
    if not CHANGELOG.exists():
        print(f"CHANGELOG.md를 찾을 수 없습니다: {CHANGELOG}", file=sys.stderr)
        return 1

    found = extract(version, CHANGELOG.read_text())
    if not found:
        # 절이 없으면 **빈 노트를 만들지 않는다** — 릴리스에 "내용 없음"이
        # 붙는 것보다 실패해서 사람이 CHANGELOG를 채우는 게 낫다.
        print(f"CHANGELOG.md에 [{version}] 절이 없습니다", file=sys.stderr)
        return 1

    heading, body = found
    notes = f"{body}\n\n---\n\n전체 변경 이력: [CHANGELOG.md](./CHANGELOG.md)\n"
    if args.out:
        Path(args.out).write_text(notes)
        print(f"✓ {args.out} ({len(body.splitlines())}줄, {heading.strip()})")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
