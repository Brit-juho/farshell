#!/usr/bin/env python3
"""6스킨 × 색 토큰 명도 대비 검사 (20-design-system.md 수용 기준).

왜 필요한가: 2.1.5까지 스킨 색은 사람이 눈으로 고른 값이었고, 그래서 notepad의
sub(5.62:1)와 muted(5.03:1)처럼 **이름만 다른 같은 색**이 실제로 배포돼 있었다.
네 단계 램프가 두 단계로 붕괴하면 위계가 글자 크기 하나에만 걸린다. 값을 목표
대비에서 역산하기로 한 이상, 그 역산이 계속 맞는지 검사하는 것도 코드여야 한다.

읽는 방식: styles/theme/{tokens.css,skins.css}를 **파싱**한다(CSS를 실행하지
않는다). tokens.css의 :root/@theme 블록이 기본값(= farshell 스킨)이고,
skins.css의 html[data-skin="X"] 블록이 그 위에 얹힌다 — 브라우저 캐스케이드와
같은 순서다.

    python3 scripts/check_contrast.py          # 검사(실패 시 exit 1)
    python3 scripts/check_contrast.py --table  # 전 스킨 대비표 출력

bg-3 예외: bg-3는 hover·활성 행 배경이라 그 위에 얹히는 글자는 한순간만 보인다.
텍스트 램프의 하한은 bg-0·bg-1·bg-2에서 본다. faint는 애초에 "못 읽어도 일이
막히지 않는" 단계라 bg-3 결과를 참고로만 출력한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "styles" / "theme" / "tokens.css"
SKINS = ROOT / "styles" / "theme" / "skins.css"

SKIN_NAMES = ["farshell", "macos", "catppuccin", "windows", "vscode", "notepad"]

# (전경 토큰, 배경 토큰들, 최소 대비, 설명)
# 텍스트 램프는 bg-0/1/2에서만 본다(위 docstring의 bg-3 예외).
TEXT_BGS = ["--color-bg-0", "--color-bg-1", "--color-bg-2"]
RULES = [
    # 8.5인 이유: WCAG AAA가 7:1이고, catppuccin(#cdd6f4)·vscode(#cccccc)의
    # 본문색은 그 팔레트의 공식 전경색이라 정체성이다 — 그 둘을 우리 취향으로
    # 바꾸느니 AAA를 이미 크게 넘는 선에서 하한을 잡는다.
    ("--color-txt", TEXT_BGS, 8.5, "본문"),
    ("--color-sub", TEXT_BGS, 6.0, "보조"),
    ("--color-muted", TEXT_BGS, 4.2, "약한 보조(읽혀야 하는 하한)"),
    ("--color-faint", TEXT_BGS, 3.0, "장식·비활성(본문 금지)"),
    ("--color-acc", TEXT_BGS, 3.0, "액센트(테두리·dot·마커)"),
    ("--color-acc-dim", TEXT_BGS, 3.0, "액센트 테두리"),
    ("--color-acc-ink", ["--color-acc-surface"], 4.5, "채움 버튼의 글자"),
    ("--color-st-working", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-waiting", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-done", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-error", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-idle", TEXT_BGS, 3.0, "상태 dot"),
]

# 램프가 "네 단계로 보이는가" — 인접 단계의 대비비가 이 배수 이상 벌어져야
# 눈으로 한 단계 차이가 난다. notepad의 붕괴(5.62 vs 5.03 = 1.12배)를 잡는 검사다.
RAMP = ["--color-txt", "--color-sub", "--color-muted", "--color-faint"]
RAMP_MIN_STEP = 1.25


def parse_block(text: str) -> dict[str, str]:
    out = {}
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text):
        out[name] = value.strip()
    return out


def load() -> dict[str, dict[str, str]]:
    base = parse_block(TOKENS.read_text(encoding="utf-8"))
    skins_css = SKINS.read_text(encoding="utf-8")
    skins = {"farshell": dict(base)}
    for name in SKIN_NAMES:
        if name == "farshell":
            continue
        m = re.search(r'html\[data-skin="%s"\]\s*\{(.*?)\n  \}' % name, skins_css, re.S)
        if not m:
            raise SystemExit(f"skins.css에서 {name} 블록을 못 찾았다")
        merged = dict(base)
        merged.update(parse_block(m.group(1)))
        skins[name] = merged
    return skins


def resolve(tokens: dict[str, str], name: str, depth: int = 0) -> str | None:
    """var(--x) 한 겹을 따라간다(토큰끼리의 별칭만 쓰므로 한 겹이면 충분하다)."""
    if depth > 8:
        return None
    value = tokens.get(name)
    if value is None:
        return None
    m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    if m:
        return resolve(tokens, m.group(1), depth + 1)
    return value


def to_rgb(value: str) -> tuple[float, float, float] | None:
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    # rgb(255 255 255 / .07) 같은 반투명 선 색은 배경과 합성해야 의미가 있어
    # 이 검사 대상이 아니다(선은 텍스트가 아니다).
    return None


def luminance(rgb: tuple[float, float, float]) -> float:
    a = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]


def contrast(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def ratio(tokens: dict[str, str], fg_name: str, bg_name: str) -> float | None:
    fg = to_rgb(resolve(tokens, fg_name) or "")
    bg = to_rgb(resolve(tokens, bg_name) or "")
    if fg is None or bg is None:
        return None
    return contrast(fg, bg)


def main() -> int:
    skins = load()
    failures: list[str] = []
    table = "--table" in sys.argv

    for skin, tokens in skins.items():
        if table:
            print(f"\n[{skin}]")
        for fg_name, bg_names, minimum, label in RULES:
            for bg_name in bg_names:
                r = ratio(tokens, fg_name, bg_name)
                if r is None:
                    failures.append(f"{skin}: {fg_name} 또는 {bg_name} 값을 못 읽었다")
                    continue
                mark = "ok " if r >= minimum else "FAIL"
                if table:
                    print(f"  {mark} {fg_name:<20} on {bg_name:<14} {r:5.2f} (>= {minimum}) {label}")
                if r < minimum:
                    failures.append(
                        f"{skin}: {fg_name} on {bg_name} = {r:.2f}:1 < {minimum}:1 ({label})"
                    )

        # 램프가 네 단계로 보이는지 — bg-0 기준 인접 단계 간격
        steps = [(n, ratio(tokens, n, "--color-bg-0")) for n in RAMP]
        for (n1, r1), (n2, r2) in zip(steps, steps[1:]):
            if r1 is None or r2 is None:
                continue
            gap = r1 / r2
            if table:
                print(f"  {'ok ' if gap >= RAMP_MIN_STEP else 'FAIL'} 램프 간격 {n1} → {n2}: {gap:.2f}배 (>= {RAMP_MIN_STEP})")
            if gap < RAMP_MIN_STEP:
                failures.append(
                    f"{skin}: 램프가 붙었다 {n1}({r1:.2f}) → {n2}({r2:.2f}) = {gap:.2f}배 < {RAMP_MIN_STEP}배"
                )

    if failures:
        print("\n대비 검사 실패:", file=sys.stderr)
        for f in failures:
            print("  -", f, file=sys.stderr)
        return 1
    print(f"대비 검사 통과 — 스킨 {len(skins)}개 × 규칙 {len(RULES)}개 + 램프 간격")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
