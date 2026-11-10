#!/usr/bin/env python3
"""9스킨 × 색 토큰 명도 대비 검사 (20-design-system.md 수용 기준).

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

2026-11(리디자인 3단계)에 더한 검사: 텍스트 램프를 떠 있는 면(bg-raised)에서도,
크롬 막대(상태·제목·활동), 터미널 색(term-*, 반투명 diff·선택·검색 강조 면 포함),
틴트 행(waiting·선택 — 올라간 글자의 기준), done과 강조색의 색 거리(CIE76 ΔE).
반투명 값(rgb(… / a), color-mix(…, transparent))은 아래 배경과 합성해서 본다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "styles" / "theme" / "tokens.css"
SKINS = ROOT / "styles" / "theme" / "skins.css"

SKIN_NAMES = ["farshell", "macos", "catppuccin", "windows", "vscode", "notepad", "warp", "linear", "vercel"]

# (전경 토큰, 배경 토큰들, 최소 대비, 설명)
# 텍스트 램프는 bg-0/1/2에서만 본다(위 docstring의 bg-3 예외).
TEXT_BGS = ["--color-bg-0", "--color-bg-1", "--color-bg-2"]
# 2026-11 리디자인 3단계 — 메뉴·대화상자·떠 있는 면(--color-bg-raised)에도 같은
# 텍스트 램프가 얹힌다. 시안 점검에서 faint #67655f가 바탕 셋은 통과하고 raised
# 위에서만 2.73으로 미달했던 게 이 배경을 따로 보는 이유다.
RAMP_BGS = TEXT_BGS + ["--color-bg-raised"]
RULES = [
    # 8.5인 이유: WCAG AAA가 7:1이고, catppuccin(#cdd6f4)·vscode(#cccccc)의
    # 본문색은 그 팔레트의 공식 전경색이라 정체성이다 — 그 둘을 우리 취향으로
    # 바꾸느니 AAA를 이미 크게 넘는 선에서 하한을 잡는다.
    ("--color-txt", RAMP_BGS, 8.5, "본문"),
    ("--color-sub", RAMP_BGS, 6.0, "보조"),
    ("--color-muted", RAMP_BGS, 4.2, "약한 보조(읽혀야 하는 하한)"),
    ("--color-faint", RAMP_BGS, 3.0, "장식·비활성(본문 금지)"),
    ("--color-acc", TEXT_BGS, 3.0, "액센트(테두리·dot·마커)"),
    ("--color-acc-dim", TEXT_BGS, 3.0, "액센트 테두리"),
    ("--color-acc-ink", ["--color-acc-surface"], 4.5, "채움 버튼의 글자"),
    ("--color-st-working", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-waiting", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-done", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-error", TEXT_BGS, 3.0, "상태 dot"),
    ("--color-st-idle", TEXT_BGS, 3.0, "상태 dot"),
    # 접힌 세션 띠 호버 카드(71-rail-hover-card.css)는 bg-raised 위에 상태 기호(링 아이콘)를 상태색으로 그린다 — 기호는 3:1.
    ("--color-st-working", ["--color-bg-raised"], 3.0, "호버 카드 상태 기호"),
    ("--color-st-waiting", ["--color-bg-raised"], 3.0, "호버 카드 상태 기호"),
    ("--color-st-done", ["--color-bg-raised"], 3.0, "호버 카드 상태 기호"),
    ("--color-st-error", ["--color-bg-raised"], 3.0, "호버 카드 상태 기호(실패)"),
    ("--color-st-idle", ["--color-bg-raised"], 3.0, "호버 카드 상태 기호"),
    # 크롬 막대. 상태 막대 글자는 12px 본문이라 AA. 제목 막대·활동 막대는 본문
    # 글자(txt)와 보조(sub)가 그대로 얹히므로 램프 기준을 그대로 쓴다. 반투명
    # 막대(macos 제목 막대)는 그 아래 bg-0과 합성해서 본다.
    ("--color-statusbar-fg", ["--color-statusbar-bg"], 4.5, "상태 막대 글자"),
    ("--color-txt", ["--color-titlebar-bg", "--color-activity-bg"], 8.5, "막대 위 본문"),
    ("--color-sub", ["--color-titlebar-bg", "--color-activity-bg"], 6.0, "막대 위 보조"),
    # 터미널 색(--color-term-*) — 미리보기·뷰어·작업 기록이 터미널 내용을 그릴 때
    # 쓰는 색. 값은 theme.js VT_XTERM_THEMES에서 온다(theme.test.js가 일치를 본다).
    ("--color-term-fg", ["--color-term-bg"], 4.5, "터미널 글자"),
    ("--color-term-bright", ["--color-term-bg"], 4.5, "터미널 강조 글자"),
    ("--color-term-red", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    ("--color-term-green", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    ("--color-term-amber", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    ("--color-term-blue", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    ("--color-term-mag", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    ("--color-term-cyan", ["--color-term-bg"], 4.5, "터미널 ANSI"),
    # 주석·흐린 출력은 일부러 낮춘 값이라 3:1만 본다.
    ("--color-term-dim", ["--color-term-bg"], 3.0, "터미널 흐린 글자"),
]

# 반투명 면 위의 글자 — (전경, 면 토큰, 면 아래 깔리는 배경, 최소, 설명).
# 면은 rgb(r g b / a) 또는 rgba()이고 아래 배경과 합성해서 대비를 계산한다.
#
# diff 줄: 줄 글자는 term-fg(4.5)이고, 초록·빨강은 +/- 표시와 바뀐 낱말 표시(3.0)다.
# 초록·빨강 글자로 줄 전체를 칠하면 notepad(종이 위 #22863a가 맨바탕에서 이미
# 4.59)·vscode(#f14c4c 4.67)에서는 어떤 틴트를 깔아도 4.5 아래로 내려간다.
OVERLAY_RULES = [
    ("--color-term-fg", "--color-term-add-bg", "--color-term-bg", 4.5, "diff 추가 줄 글자"),
    ("--color-term-fg", "--color-term-del-bg", "--color-term-bg", 4.5, "diff 삭제 줄 글자"),
    ("--color-term-green", "--color-term-add-bg", "--color-term-bg", 3.0, "diff + 표시·바뀐 낱말"),
    ("--color-term-red", "--color-term-del-bg", "--color-term-bg", 3.0, "diff - 표시·바뀐 낱말"),
    ("--color-term-fg", "--color-term-sel", "--color-term-bg", 4.5, "터미널 선택 영역"),
    ("--color-term-bright", "--color-term-hl", "--color-term-bg", 4.5, "검색 일치 강조"),
]

# 틴트 행(개입 필요 = waiting 틴트, 선택 = acc-soft) — 틴트 위에서는 보조 글자가
# 한 단계 올라간다(muted 자리에 sub, sub 자리에 txt). 그래서 각 토큰은 **올라간
# 자리의 기준**을 틴트 위에서 넘어야 한다: txt는 sub의 6.0, sub는 AA 4.5(옛 규칙
# 그대로이고 muted 기준 4.2보다 높다), muted는 faint의 3.0.
TINTS = [
    ("--color-st-waiting", "--waiting-tint-pct", "개입 필요 틴트"),
    ("--color-acc", "--acc-soft-pct", "선택 틴트(acc-soft)"),
]
TINT_RULES = [("--color-txt", 6.0), ("--color-sub", 4.5), ("--color-muted", 3.0)]

# done 점과 강조색이 같은 색으로 보이면 「끝난 세션」 점이 선택 표시·주 버튼과
# 헷갈린다(시안 점검: #8b9dff vs #7aa2ff = ΔE 6.7). CIE76 ΔE ≥ 15는 "나란히 두면
# 누구나 다른 색이라 말하는" 정도의 거리다.
DISTINCT = [("--color-st-done", "--color-acc", 15.0, "done 점 vs 강조")]

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


def to_rgba(value: str) -> tuple[float, float, float, float] | None:
    """hex 또는 rgb(r g b / a)·rgba(r,g,b,a) — 알파가 없으면 1."""
    value = value.strip()
    solid = to_rgb(value)
    if solid is not None:
        return (*solid, 1.0)
    m = re.fullmatch(r"rgba?\(\s*([0-9.]+)[\s,]+([0-9.]+)[\s,]+([0-9.]+)\s*(?:[/,]\s*([0-9.]+)(%?))?\s*\)", value)
    if not m:
        return None
    a = 1.0
    if m.group(4) is not None:
        a = float(m.group(4)) / (100 if m.group(5) else 1)
    return (float(m.group(1)) / 255, float(m.group(2)) / 255, float(m.group(3)) / 255, a)


def over(top: tuple[float, float, float, float], base: tuple[float, float, float]) -> tuple[float, float, float]:
    a = top[3]
    return tuple(t * a + b * (1 - a) for t, b in zip(top[:3], base))  # type: ignore[return-value]


def mix_pct(tokens: dict[str, str], name: str, base_hex_token: str, pct_token: str) -> tuple[float, float, float, float] | None:
    """color-mix(in srgb, X p%, transparent) 형태의 틴트를 (r,g,b,a)로."""
    m = re.fullmatch(r"([0-9.]+)%", tokens.get(pct_token, ""))
    c = to_rgb(resolve(tokens, base_hex_token) or "")
    if not m or c is None:
        return None
    return (*c, float(m.group(1)) / 100)


def surface(tokens: dict[str, str], name: str, under: str = "--color-bg-0") -> tuple[float, float, float] | None:
    """면 토큰의 실제 색 — 반투명이면 under 위에 합성한다. color-mix 틴트도 푼다."""
    raw = resolve(tokens, name) or ""
    rgba = to_rgba(raw)
    if rgba is None:
        m = re.fullmatch(
            r"color-mix\(in srgb,\s*var\((--[a-z0-9-]+)\)\s+(?:var\((--[a-z0-9-]+)\)|([0-9.]+)%),\s*transparent\)",
            raw,
        )
        if not m:
            return None
        if m.group(2):
            rgba = mix_pct(tokens, name, m.group(1), m.group(2))
        else:
            c = to_rgb(resolve(tokens, m.group(1)) or "")
            rgba = (*c, float(m.group(3)) / 100) if c else None
        if rgba is None:
            return None
    if rgba[3] >= 1:
        return rgba[:3]
    base = surface(tokens, under) if under != name else None
    if base is None:
        return None
    return over(rgba, base)


def lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) / 1.0
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = [t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116 for t in (x, y, z)]
    return (116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2]))


def delta_e(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = lab(a), lab(b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


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
    bg = surface(tokens, bg_name)
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

        # 반투명 면(diff 줄·선택·검색 강조) 위 글자
        for fg_name, sf_name, under, minimum, label in OVERLAY_RULES:
            fg = to_rgb(resolve(tokens, fg_name) or "")
            sf = surface(tokens, sf_name, under)
            if fg is None or sf is None:
                failures.append(f"{skin}: {fg_name} 또는 {sf_name} 값을 못 읽었다")
                continue
            r = contrast(fg, sf)
            if table:
                print(f"  {'ok ' if r >= minimum else 'FAIL'} {fg_name:<20} on {sf_name:<14} {r:5.2f} (>= {minimum}) {label}")
            if r < minimum:
                failures.append(f"{skin}: {fg_name} on {sf_name} = {r:.2f}:1 < {minimum}:1 ({label})")

        # 틴트 행 — 개입 필요(waiting)·선택(acc-soft). 틴트는 투명도라 bg와 합성한다.
        for base_name, pct_name, label in TINTS:
            tint = mix_pct(tokens, "", base_name, pct_name)
            if tint is None:
                failures.append(f"{skin}: {pct_name} 또는 {base_name} 을 못 읽었다")
                continue
            for bg_name in TEXT_BGS:
                bg = to_rgb(resolve(tokens, bg_name) or "")
                comp = over(tint, bg)
                for fg_name, minimum in TINT_RULES:
                    fg = to_rgb(resolve(tokens, fg_name) or "")
                    r = contrast(fg, comp)
                    if table:
                        print(f"  {'ok ' if r >= minimum else 'FAIL'} {fg_name:<20} on {label}({bg_name}) {r:5.2f} (>= {minimum})")
                    if r < minimum:
                        failures.append(f"{skin}: {fg_name} on {label}({bg_name}) = {r:.2f}:1 < {minimum}:1")

        # 서로 달라 보여야 하는 색 쌍
        for a_name, b_name, minimum, label in DISTINCT:
            a = to_rgb(resolve(tokens, a_name) or "")
            b = to_rgb(resolve(tokens, b_name) or "")
            if a is None or b is None:
                failures.append(f"{skin}: {a_name} 또는 {b_name} 값을 못 읽었다")
                continue
            d = delta_e(a, b)
            if table:
                print(f"  {'ok ' if d >= minimum else 'FAIL'} ΔE {a_name} vs {b_name}: {d:.1f} (>= {minimum}) {label}")
            if d < minimum:
                failures.append(f"{skin}: {a_name} vs {b_name} ΔE {d:.1f} < {minimum} ({label})")

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
    print(f"대비 검사 통과 — 스킨 {len(skins)}개 × 규칙 {len(RULES) + len(OVERLAY_RULES)}개 + 틴트 {len(TINTS)}종 + 색 거리 + 램프 간격")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
