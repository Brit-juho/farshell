"""T2 — 정의되지 않은 CSS 변수 검사기(scripts/check_css_vars.py) 자체의 회귀 테스트.

이 검사기는 2026-09-08에 실제로 난 사고에서 나왔다: `var(--acc-dim)`이 6스킨
어디에도 정의돼 있지 않아 `.vt-card.open-tab`의 `box-shadow`가 통째로 무시됐는데,
`npm run build`도 node 테스트 261개도 전부 통과했다. 검사기가 조용히 고장 나면
(정규식 하나가 안 맞아 아무것도 못 찾는 식) **똑같은 사고가 다시 초록불로 지나간다.**
그래서 "그 사고를 실제로 잡아내는가"와 "정상 패턴을 오탐하지 않는가"를 둘 다 고정한다.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location(
    "check_css_vars", ROOT / "scripts" / "check_css_vars.py"
)
check_css_vars = importlib.util.module_from_spec(SPEC)
sys.modules["check_css_vars"] = check_css_vars
SPEC.loader.exec_module(check_css_vars)

find = check_css_vars.find_problems


def _names(problems):
    return [name for name, _line in problems]


# ── 실제 사고 재현 ────────────────────────────────────────────────────────

def test_catches_the_2026_09_08_regression():
    """정의되지 않은 이름을 fallback 없이 쓰면 잡는다 — 이 사고의 원형."""
    css = """
    :root { --color-acc-dim: #b3803f; }
    .vt-card.open-tab { box-shadow: inset 3px 0 0 var(--acc-dim); }
    """
    assert _names(find(css, set())) == ["--acc-dim"]


def test_passes_when_the_real_token_name_is_used():
    """T1이 적용한 형태(--color-* 직접 참조)는 통과해야 한다."""
    css = """
    :root { --color-acc-dim: #b3803f; }
    .vt-card.open-tab { box-shadow: inset 3px 0 0 var(--color-acc-dim); }
    """
    assert find(css, set()) == []


def test_alias_chain_is_resolved():
    """별칭이 실제로 선언돼 있으면(--acc: var(--color-acc)) 통과한다."""
    css = """
    :root { --color-acc: #f0a860; }
    html[data-skin="macos"] { --acc: var(--color-acc); }
    .x { color: var(--acc); }
    """
    assert find(css, set()) == []


def test_reports_first_use_line():
    css = "\n".join([":root { --a: 1px; }", ".x { top: var(--a); }", ".y { left: var(--nope); }"])
    problems = find(css, set())
    assert problems == [("--nope", 3)]


def test_multiple_undefined_names_all_reported():
    css = ".x { color: var(--one); background: var(--two); border-color: var(--one); }"
    assert _names(find(css, set())) == ["--one", "--two"]


# ── 오탐 방지 ─────────────────────────────────────────────────────────────

def test_fallback_makes_it_valid():
    """var(--x, 기본값)은 이름이 없어도 선언이 안 죽는다 — 유효한 패턴."""
    css = ".vt-vw-dock { width: var(--vt-dock-w, 420px); }"
    assert find(css, set()) == []


def test_comments_are_not_references():
    """주석 안의 설명 텍스트를 참조로 세면 안 된다.

    legacy.css:38의 `--trad - var(--r-2xs)`는 반경 정리 과정을 설명하는 주석
    본문이다. 실제 선언이 아니므로 잡히면 오탐이다.
    """
    css = "/* 예전엔 var(--r-2xs)를 썼다 */\n.x { color: red; }"
    assert find(css, set()) == []


def test_comment_stripping_does_not_join_tokens():
    """주석을 빈 문자열이 아니라 공백으로 치환해야 양옆 토큰이 안 붙는다."""
    css = ":root{--a:1px;}\n.x{top:var(--a)/* c */;}"
    assert find(css, set()) == []


def test_runtime_names_from_js_are_accepted():
    """JS가 style.setProperty로 심는 변수는 CSS에 선언이 없는 게 정상이다."""
    css = ".vt-vw-trow { padding-left: calc(var(--d) * 4px); }"
    assert _names(find(css, set())) == ["--d"]          # 인정 안 하면 잡힌다
    assert find(css, {"--d"}) == []                      # 인정하면 통과


def test_fallback_containing_colon_is_not_a_declaration():
    """var(--a, url(x:y))의 콜론을 선언으로 오인하면 --a가 정의된 걸로 착각한다."""
    css = ".x { background: var(--a, url(http://e/i.png)); border-color: var(--a); }"
    assert _names(find(css, set())) == ["--a"]


def test_nested_var_fallback():
    """var(--a, var(--b))에서 --a는 fallback 보유, --b는 미정의로 잡혀야 한다."""
    css = ".x { color: var(--a, var(--b)); }"
    assert _names(find(css, set())) == ["--b"]


def test_whitespace_variants_are_matched():
    css = ".x { color: var( --nope ); }"
    assert _names(find(css, set())) == ["--nope"]


# ── 저장소 현재 상태 ──────────────────────────────────────────────────────

def test_built_css_is_currently_clean():
    """지금 빌드 산출물이 통과 상태여야 CI를 게이트로 올릴 수 있다.

    산출물이 없으면(빌드 전) 이 테스트는 의미가 없으므로 건너뛴다 — CI는
    npm run build 뒤에 돌린다.
    """
    built = check_css_vars.BUILT_CSS
    if not built.exists():
        import pytest
        pytest.skip("frontend/dist/app.css 없음 — `npm run build` 후에만 유효")
    problems = find(built.read_text(encoding="utf-8"), check_css_vars._runtime_names())
    assert problems == [], "정의되지 않은 CSS 변수:\n" + "\n".join(
        f"  {n} (첫 사용 {l}행)" for n, l in problems
    )
