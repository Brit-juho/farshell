#!/usr/bin/env python3
"""styles/ 의 캐스케이드 계약이 깨지지 않았는지 검사한다.

왜 필요한가: 2.1.6에서 legacy.css(2927줄)를 styles/screens/ 아홉 개로 쪼갰다.
**순서가 곧 우선순위**라 import 줄 순서가 바뀌면 화면이 바뀌는데, 그건 파일을
열어봐도 안 보이고 리뷰에서도 잘 안 걸린다. 그리고 레이어 안에 `@layer`를 또
쓰면(예: screens 파일 안의 `@layer base`) 그게 top-level base가 아니라
**legacy.base**가 되어, 전역 리셋이 다시 components를 이기는 회귀가 조용히
살아난다 — 실제로 한 번 당한 함정이라 검사로 못 박는다.

    python3 scripts/check_css_order.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "styles" / "main.css"
SCREENS = ROOT / "styles" / "screens"

# main.css의 @layer 선언에 반드시 이 순서로 들어 있어야 한다.
EXPECTED_LAYERS = ["theme", "base", "components", "legacy", "utilities"]


def main() -> int:
    fails: list[str] = []
    css = MAIN.read_text(encoding="utf-8")

    # 1) 레이어 순서
    m = re.search(r"@layer\s+([a-z, \n]+);", css)
    if not m:
        fails.append("main.css에 @layer 순서 선언이 없다")
    else:
        got = [s.strip() for s in m.group(1).replace("\n", " ").split(",") if s.strip()]
        if got != EXPECTED_LAYERS:
            fails.append(f"레이어 순서가 다르다: {got} != {EXPECTED_LAYERS}")

    # 2) screens/*.css가 전부, 파일명 번호 순서대로, layer(legacy)로 import되는가
    on_disk = sorted(p.name for p in SCREENS.glob("*.css"))
    imported = re.findall(r'@import\s+"\./screens/([^"]+)"\s+layer\(legacy\)', css)
    if imported != sorted(imported):
        fails.append(f"screens import가 번호 순서가 아니다: {imported}")
    missing = set(on_disk) - set(imported)
    if missing:
        fails.append(f"main.css가 import하지 않는 screens 파일: {sorted(missing)} — 통째로 죽은 CSS가 된다")
    extra = set(imported) - set(on_disk)
    if extra:
        fails.append(f"없는 파일을 import한다: {sorted(extra)}")

    # 3) screens 파일 안에 @layer를 쓰지 않았는가 (중첩 레이어 함정)
    for p in sorted(SCREENS.glob("*.css")):
        body = re.sub(r"/\*.*?\*/", "", p.read_text(encoding="utf-8"), flags=re.S)
        if re.search(r"@layer\b", body):
            fails.append(
                f"{p.name}에 @layer가 있다 — layer(legacy)로 import되므로 legacy의 "
                f"하위 레이어가 되어 의도한 순서와 달라진다"
            )

    # 4) 리셋은 base 레이어에 단 한 번만
    reset_files = [
        p.name for p in (ROOT / "styles").rglob("*.css")
        if re.search(r"\*\s*\{[^}]*padding\s*:\s*0", p.read_text(encoding="utf-8"))
    ]
    if reset_files != ["reset.css"]:
        fails.append(f"전역 리셋이 reset.css 밖에도 있다: {reset_files}")

    if fails:
        print("CSS 순서 검사 실패:", file=sys.stderr)
        for f in fails:
            print("  -", f, file=sys.stderr)
        return 1
    print(f"CSS 순서 검사 통과 — 레이어 {len(EXPECTED_LAYERS)}단, screens {len(on_disk)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
