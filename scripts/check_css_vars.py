#!/usr/bin/env python3
"""빌드된 CSS에서 '정의되지 않은 커스텀 프로퍼티 참조'를 찾는다.

## 왜 이게 필요한가 (2026-09-08)

`styles/layers/legacy.css`의 스킨 블록은 `--acc`/`--acc-ink`는 별칭으로 깔지만
`--acc-dim`/`--muted`는 깔지 않는다. `styles/theme/tokens.css`가 정의하는 이름은
`--color-acc-dim`이라는 **다른 이름**이다. 그래서

    .vt-card.open-tab { box-shadow:inset 3px 0 0 var(--acc-dim); }

는 6스킨 전부에서 invalid at computed-value time이 되어 `box-shadow`가 통째로
`none`으로 계산된다. CSS는 이걸 에러로 만들지 않는다 — 조용히 그 선언만 버린다.

실제로 이 상태로 `npm run build`도 `npm test`(261개)도 전부 통과했다. 브라우저에서
6스킨을 순회하며 `getComputedStyle`을 찍어보고서야 발견했다. 사람 눈이 못 잡는
종류라서 기계가 잡아야 한다.

## 검사 대상이 왜 소스가 아니라 빌드 산출물인가

`frontend/dist/app.css`는 `@import`가 이미 해소된 단일 파일이라, "이 이름이 어딘가엔
정의돼 있다"를 파일 하나로 판정할 수 있다. 소스를 훑으면 `@import` 순서와 Tailwind
`@theme static` 확장(`--color-*`가 실제 `:root` 프로퍼티로 컴파일되는 것)을 직접
재현해야 하는데, 그건 빌드를 두 번 구현하는 셈이다.

## 오탐을 막는 세 가지

1. **주석 제거.** 주석 안의 `var(--r-2xs)` 같은 설명 텍스트를 참조로 세지 않는다.
2. **fallback 보유분 통과.** `var(--vt-dock-w, 420px)`처럼 기본값이 있으면 이름이
   없어도 선언이 죽지 않는다 — 유효한 패턴이므로 통과시킨다.
3. **JS가 심는 변수 인정.** `el.style.setProperty('--d', n)`처럼 런타임에 요소
   단위로 설정되는 변수는 CSS 어디에도 선언이 없는 게 정상이다. 임의의 허용
   목록을 손으로 관리하는 대신 `frontend/js/`에서 `setProperty('--x'`를 실제로
   찾아 인정한다 — 코드가 지워지면 인정도 같이 사라진다.

사용:
    python3 scripts/check_css_vars.py            # 리포트만 (종료코드 0)
    python3 scripts/check_css_vars.py --strict   # 발견 시 종료코드 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT_CSS = ROOT / "frontend" / "dist" / "app.css"
JS_DIR = ROOT / "frontend" / "js"

# `/* ... */` — CSS에는 줄 주석이 없다.
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# `var(--name` 뒤에 콤마가 오면 fallback이 있는 것.
_VAR_USE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?")
# 선언부: `--name:` (var() 안에서는 이 형태가 나올 수 없다 — 아래에서 var()를
# 먼저 지우고 찾으므로 중첩 fallback의 콜론에도 안 걸린다).
_VAR_DEF = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
# JS 런타임 주입: el.style.setProperty('--d', ...)
_JS_SET = re.compile(r"""setProperty\(\s*['"](--[A-Za-z0-9_-]+)['"]""")


def _strip_comments(css: str) -> str:
    return _COMMENT.sub(" ", css)


def _defined_names(css: str) -> set[str]:
    """선언된 커스텀 프로퍼티 이름.

    `var(...)` 표현식을 통째로 지운 뒤에 찾는다 — `var(--a, url(x:y))`처럼
    fallback 안에 콜론이 들어간 경우를 선언으로 오인하지 않기 위해서다.
    """
    without_var = re.sub(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)", " ", css)
    return set(_VAR_DEF.findall(without_var))


def _runtime_names() -> set[str]:
    """JS가 `style.setProperty`로 심는 변수 — CSS에 선언이 없는 게 정상이다."""
    names: set[str] = set()
    if not JS_DIR.is_dir():
        return names
    for path in JS_DIR.rglob("*.js"):
        names.update(_JS_SET.findall(path.read_text(encoding="utf-8")))
    return names


def find_problems(css: str, runtime: set[str]) -> list[tuple[str, int]]:
    """(이름, 그 이름이 fallback 없이 쓰인 첫 줄) 목록."""
    stripped = _strip_comments(css)
    defined = _defined_names(stripped)
    known = defined | runtime

    first_line: dict[str, int] = {}
    for m in _VAR_USE.finditer(stripped):
        name, has_fallback = m.group(1), bool(m.group(2))
        if has_fallback or name in known:
            continue
        first_line.setdefault(name, stripped.count("\n", 0, m.start()) + 1)
    return sorted(first_line.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="발견 시 종료코드 1")
    args = ap.parse_args()

    if not BUILT_CSS.exists():
        print(f"✗ 빌드 산출물이 없다: {BUILT_CSS.relative_to(ROOT)}", file=sys.stderr)
        print("  먼저 `npm run build`를 실행한다.", file=sys.stderr)
        return 1

    problems = find_problems(BUILT_CSS.read_text(encoding="utf-8"), _runtime_names())
    if not problems:
        print("✓ 정의되지 않은 CSS 변수 참조 없음")
        return 0

    print(f"✗ 정의되지 않은 CSS 변수 참조 {len(problems)}건 "
          f"(fallback도 없어 그 선언이 통째로 무시된다):")
    for name, line in problems:
        print(f"  {BUILT_CSS.relative_to(ROOT)}:{line}  var({name})")
    print()
    print("  고치는 법: 토큰의 진짜 이름(--color-*)을 쓰거나, 의도한 값이 없을 수도")
    print("  있는 변수라면 var(--name, 기본값) 형태로 fallback을 준다.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
