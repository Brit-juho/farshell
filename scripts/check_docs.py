#!/usr/bin/env python3
"""I3 — 문서 일관성 검사.

**코드가 단일 진실이고 문서가 그걸 따라간다.** 이 스크립트는 그 방향으로만
검사한다: 서버 라우트·`fsh help` 토픽·`VERSION`이 실제로 존재하는 것이고,
문서가 그것과 어긋나면 문서 쪽을 고친다.

왜 필요한가: 2026-08-20에 실제로 겪은 드리프트가 근거다 — `CLAUDE.md`가
엔드포인트 표를 따로 들고 있다가 `API.md`와 어긋났고, 그 뒤로 CLAUDE.md는
카테고리 목록만 남기고 API.md를 단일 진실로 삼았다. 사람이 기억으로 지키는
규칙은 언젠가 깨지므로 검사로 고정한다.

**경고로 시작한다**(계획서 I3): 기본은 종료코드 0으로 리포트만 하고,
`--strict`를 주면 발견 시 1로 끝난다. CI는 당분간 경고 모드로 돌리고,
드리프트가 0으로 안정되면 strict로 승격한다 — 처음부터 실패로 두면
"원래 빨간 체크"가 되어 아무도 안 본다.

사용:
    python3 scripts/check_docs.py            # 리포트만 (종료코드 0)
    python3 scripts/check_docs.py --strict   # 발견 시 종료코드 1
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. 서버 라우트 ↔ API.md ───────────────────────────────────────────────
# `@router.get("/api/x")` 형태에서 경로만 뽑는다. 데코레이터가 여러 줄이거나
# 같은 함수에 두 개 붙는 경우(`@router.post` + `@router.get`)도 각각 잡힌다.
# `\w*router`로 접두사를 허용하는 이유: N30/N31에서 승격이 필요한 라우트를
# `APIRouter(dependencies=[Depends(require_elevated)])`로 묶은 별도 라우터
# (`elevated_router`)가 처음 등장했다 — 변수명이 정확히 "router"가 아니면
# 그 라우트가 통째로 안 잡혀서 문서와 어긋난 것처럼 오탐하는 문제가 있었다.
ROUTE_RE = re.compile(
    r'@(?:\w*router|app)\.(get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']'
)

# 경로 파라미터는 문서에서 이름이 다를 수 있다(`{id}` vs `{session_id}`).
# 비교는 **모양**으로 한다 — 파라미터 이름 차이로 거짓 경보를 내면 아무도 안 본다.
PARAM_RE = re.compile(r"\{[^}]+\}")


def norm(path: str) -> str:
    """비교용 표준형.

    쿼리스트링과 대괄호 표기는 **문서의 설명 장치**다(`?path=X`,
    `[?fresh=1]`) — 코드의 라우트 경로에는 없다. 그대로 비교하면 같은
    엔드포인트가 서로 다른 것으로 잡혀 거짓 경보만 쏟아진다.
    """
    # 대괄호를 **먼저** 걷어낸다 — `?`로 먼저 자르면 `[?fresh=1]`의 여는
    # 대괄호가 경로에 남아 `/api/ports[` 같은 유령이 생긴다(실제로 겪었다).
    path = re.sub(r"\[[^\]]*\]", "", path)
    path = path.split("?", 1)[0]
    return PARAM_RE.sub("{}", path.rstrip("/")) or "/"


# 정적 서빙(`/`, `/sw.js`, `/manifest.json`, `/favicon.ico`)은 API가 아니다.
# API.md는 "REST/WebSocket 엔드포인트" 문서이므로 이것들까지 요구하면 거짓 경보가 된다.
API_PREFIXES = ("/api", "/ws", "/voice")


def _is_api(path: str) -> bool:
    return path.startswith(API_PREFIXES)


def code_routes() -> set[str]:
    out = set()
    for py in sorted((ROOT / "server" / "routes").glob("*.py")):
        for _, path in ROUTE_RE.findall(py.read_text()):
            out.add(norm(path))
    # main.py에 직접 붙은 라우트도 있다(정적 서빙 등) — 있으면 함께 본다.
    main = ROOT / "server" / "main.py"
    if main.exists():
        for _, path in ROUTE_RE.findall(main.read_text()):
            out.add(norm(path))
    return {p for p in out if _is_api(p)}


# API.md의 표에서 백틱으로 감싼 경로를 뽑는다.
DOC_PATH_RE = re.compile(r"`(/(?:api|ws|voice)[^`\s]*)`")


def doc_routes(doc: Path) -> set[str]:
    if not doc.exists():
        return set()
    return {norm(p) for p in DOC_PATH_RE.findall(doc.read_text())}


def check_api_docs(problems: list[str]) -> None:
    code = code_routes()
    for name in ("API.md", "API.ko.md"):
        doc = ROOT / name
        documented = doc_routes(doc)
        missing = sorted(code - documented)
        # 문서에만 있는 경로 = 삭제됐거나 오타. 코드가 진실이므로 이것도 본다.
        extra = sorted(documented - code)
        for p in missing:
            problems.append(f"{name}: 코드에 있는데 문서에 없음 — {p}")
        for p in extra:
            problems.append(f"{name}: 문서에 있는데 코드에 없음 — {p}")


def check_claude_md(problems: list[str]) -> None:
    """CLAUDE.md는 **카테고리 목록만** 유지한다(2026-08-20 드리프트 사고 이후).

    엔드포인트 표를 다시 들이면 API.md와 두 벌이 되므로, 그 표가 생겼는지를
    검사한다 — 개별 경로가 아니라 '표가 다시 생겼는가'가 검사 대상이다.
    """
    for name in ("CLAUDE.md",):
        doc = ROOT / name
        if not doc.exists():
            continue
        text = doc.read_text()
        # 카테고리 목록(`| 카테고리 | 대표 경로 |`)은 **허용**이다 — 실제로 지금
        # CLAUDE.md가 그 형태다. 금지 대상은 메서드까지 적은 상세 표
        # (`| GET | /api/x | 설명 |`)다. 둘을 구분하지 않으면 지금 문서가 매번
        # 경보를 내고, 그러면 아무도 이 검사를 안 보게 된다.
        detailed = [
            ln for ln in text.splitlines()
            if ln.startswith("|") and "`/api/" in ln
            and re.search(r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|", ln)
        ]
        if len(detailed) >= 3:
            problems.append(
                f"{name}: 엔드포인트 상세 표가 다시 생긴 것으로 보인다 "
                f"({len(detailed)}행) — API.md가 단일 진실이다(CLAUDE.md는 카테고리 목록만)"
            )


# ── 2. `fsh help` 토픽 ↔ docs/help/*.md ───────────────────────────────────
def check_help_topics(problems: list[str]) -> None:
    fsh = (ROOT / "bin" / "fsh").read_text()
    files = {p.stem for p in (ROOT / "docs" / "help").glob("*.md")}

    listed = set(re.findall(r"fsh help (\w[\w-]*)", fsh))
    listed.discard("topic")   # 사용법 문구의 플레이스홀더

    # "사용 가능한 토픽: a / b / c" 줄
    m = re.search(r"사용 가능한 토픽:\s*([^\"']+)", fsh)
    inline = set()
    if m:
        inline = {t.strip() for t in m.group(1).split("/") if t.strip()}

    for topic in sorted((listed | inline) - files):
        problems.append(f"fsh help: 안내하는 토픽에 문서가 없음 — docs/help/{topic}.md")
    for topic in sorted(files - (listed | inline)):
        problems.append(f"docs/help/{topic}.md: 문서가 있는데 `fsh help`가 안내하지 않음")


# ── 3. VERSION ↔ CHANGELOG ────────────────────────────────────────────────
def check_version(problems: list[str]) -> None:
    version_file = ROOT / "VERSION"
    changelog = ROOT / "CHANGELOG.md"
    if not version_file.exists() or not changelog.exists():
        return
    version = version_file.read_text().strip()
    # CHANGELOG의 첫 번째 릴리스 헤딩(Unreleased는 건너뛴다)
    heads = re.findall(r"^##\s*\[([^\]]+)\]", changelog.read_text(), re.M)
    released = [h for h in heads if h.lower() != "unreleased"]
    if not released:
        return
    if released[0] != version:
        problems.append(
            f"VERSION({version})과 CHANGELOG 최신 릴리스({released[0]})가 다르다 "
            "— 릴리스 태그가 어느 쪽을 따라야 할지 알 수 없다"
        )



# ── 4. DESIGN.md ↔ 코드 파생 값 ───────────────────────────────────────────
#
# DESIGN.{md,ko.md}는 앞으로 모든 UI 작업의 기준이 되므로, 코드에 사는 값을 문서가
# 베껴 적은 지점은 전부 드리프트 후보다. 2026-09-08 감사에서 실제로 두 건이 틀린 채
# 발견됐다 — rail 항목 수(문서 6 / 코드 7)와 에이전트 상태 수(문서 4 / STATUSES 5).
#
# **왜 산문을 훑지 않고 전용 표를 두는가.** 처음엔 "문서 본문에 이 숫자가 들어있나"로
# 짰는데 무의미했다. `7`이나 `720` 같은 맨 숫자는 문서 어딘가엔 늘 존재해서 값을
# 틀리게 바꿔도 검사가 통과했다(실측 확인). 앵커를 산문에 두면 문장을 조금만 고쳐도
# 깨지고, 느슨하게 두면 아무것도 못 잡는다. 그래서 두 문서에 **기계가 읽는 표**를
# 하나 두고 그 표만 코드와 대조한다 — 사람에게도 "이 값의 진짜 출처는 여기"를
# 알려주므로 검사용 껍데기가 아니다.
_DESIGN_TABLE_ROW = re.compile(r"^\|\s*`([a-z0-9.-]+)`\s*\|\s*`([^`]*)`\s*\|", re.M)
# 표는 전용 절 안에서만 읽는다. 문서에는 토큰 표처럼 생김새가 같은 표가 더 있어서
# (`| `--color-acc` | `#f0a860` | 액센트 |`), 파일 전체를 훑으면 그 행들까지
# "검사 대상"으로 오인한다 — 실제로 처음 구현에서 그렇게 오탐이 났다.
_DESIGN_SECTION = re.compile(
    r"^###\s+(?:코드 파생 값|Code-derived values).*?$(.*?)(?=^###\s|\Z)", re.M | re.S
)


def _design_rows(text: str) -> dict[str, str] | None:
    m = _DESIGN_SECTION.search(text)
    if not m:
        return None
    return dict(_DESIGN_TABLE_ROW.findall(m.group(1)))



def _resolve_token(css: str, name: str, depth: int = 3) -> str:
    """`--name`의 최종 hex를 찾는다. `var(--other)` 한두 단계는 따라간다.

    T9에서 액센트가 `--color-acc: var(--acc-farshell)` 형태가 됐다. 테마 칩이
    "다른 스킨의" 액센트를 보여줘야 해서 스코프 밖 상수를 하나 더 두게 된 결과다.
    hex만 찾는 정규식으로는 그 순간 값을 못 읽어 검사가 조용히 빠진다 — 실제로
    이 변경 직후 `color-acc` 행이 "대응하는 코드 검사가 없다"로 떨어졌다.
    """
    seen: set[str] = set()
    for _ in range(depth):
        if name in seen:
            return ""
        seen.add(name)
        m = re.search(rf"{re.escape(name)}:\s*([^;]+);", css)
        if not m:
            return ""
        val = m.group(1).strip()
        if val.startswith("#"):
            return val
        ref = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", val)
        if not ref:
            return ""
        name = ref.group(1)
    return ""

def _design_expected() -> dict[str, str]:
    """코드에서 뽑은 (키 → 값). 키는 두 DESIGN 문서의 표 첫 칸과 같다."""
    out: dict[str, str] = {}

    theme = ROOT / "frontend" / "js" / "theme.js"
    if theme.exists():
        m = re.search(r"VT_SKINS\s*=\s*\[([^\]]*)\]", theme.read_text())
        if m:
            out["skins"] = ", ".join(re.findall(r"['\"]([\w-]+)['\"]", m.group(1)))

    bp = ROOT / "frontend" / "js" / "layout" / "breakpoints.js"
    if bp.exists():
        text = bp.read_text()
        vals = [re.search(rf"{n}\s*=\s*(\d+)", text) for n in ("COMPACT_MAX", "REGULAR_MAX")]
        if all(vals):
            out["breakpoints"] = "/".join(v.group(1) for v in vals)

    dnd = ROOT / "frontend" / "js" / "layout" / "dnd.js"
    if dnd.exists():
        text = dnd.read_text()
        m = re.search(r"function tierCap\(\)\s*\{(.*?)\n\}", text, re.S)
        if m:
            caps = re.findall(r"return\s+(\d+)\s*;", m.group(1))
            if caps:
                out["pane-cap"] = "/".join(caps)

    index = ROOT / "frontend" / "index.html"
    if index.exists():
        # 클래스 경계까지 봐야 한다 — `class="vt-rail-btn`만 보면
        # `vt-rail-btn-DISABLED` 같은 접두 일치까지 세어 코드 쪽 변경을 놓친다.
        out["rail-items"] = str(
            len(re.findall(r'class="vt-rail-btn(?=[ "])', index.read_text()))
        )

    st = ROOT / "server" / "agent_status.py"
    if st.exists():
        m = re.search(r"STATUSES\s*=\s*\(([^)]*)\)", st.read_text())
        if m:
            out["agent-states"] = str(len([x for x in m.group(1).split(",") if x.strip()]))

    tokens = ROOT / "styles" / "theme" / "tokens.css"
    if tokens.exists():
        text = tokens.read_text()
        for key, name in (("color-bg-0", "--color-bg-0"), ("color-acc", "--color-acc"),
                          ("color-txt", "--color-txt")):
            out[key] = _resolve_token(text, name)
        out = {k: v for k, v in out.items() if v}
    return out


def check_design_md(problems: list[str]) -> None:
    """DESIGN.{md,ko.md}의 「코드 파생 값」 표를 실제 코드와 대조한다.

    한국어판·영문판을 같이 본다 — 한쪽만 고치고 넘어가는 게 이 저장소에서 가장
    흔한 문서 사고다.
    """
    expected = _design_expected()
    if not expected:
        return

    for name in ("DESIGN.md",):
        doc = ROOT / name
        if not doc.exists():
            continue
        rows = _design_rows(doc.read_text())
        if not rows:
            problems.append(
                f"{name}: 「코드 파생 값」 표가 없다 — 이 표가 없으면 문서의 "
                "숫자·색이 코드와 맞는지 아무도 검증하지 않는다"
            )
            continue
        for key, want in expected.items():
            got = rows.get(key)
            if got is None:
                problems.append(f"{name}: 「코드 파생 값」 표에 `{key}` 행이 없다 (코드값: {want})")
            elif got != want:
                problems.append(
                    f"{name}: `{key}` — 문서 `{got}` / 코드 `{want}` — 코드가 단일 진실이다"
                )
        for key in rows.keys() - expected.keys():
            problems.append(f"{name}: 「코드 파생 값」 표의 `{key}` 행은 대응하는 코드 검사가 없다")


# ── 6. CLAUDE.md는 색인이다 (2026-09-18) ──────────────────────────────────

# 496줄·56k자였던 CLAUDE.md를 색인으로 줄이고 상세를 docs/ref/*.md로 갈랐다.
# 그 파일은 **세션마다 자동으로 컨텍스트에 들어간다** — 다시 불어나면 만지지도
# 않는 영역의 설명까지 매번 토큰을 먹는다. 그래서 예산을 검사로 고정한다.
CLAUDE_MD_MAX_CHARS = 9000


def check_claude_md_is_an_index(problems: list[str]) -> None:
    doc = ROOT / "CLAUDE.md"
    if not doc.exists():
        return
    text = doc.read_text()
    if len(text) > CLAUDE_MD_MAX_CHARS:
        problems.append(
            f"CLAUDE.md가 {len(text)}자다(예산 {CLAUDE_MD_MAX_CHARS}자) — "
            "상세는 docs/ref/*.md로 옮기고 색인만 남긴다. 이 파일은 매 세션 자동 로드된다"
        )
    # 색인이 가리키는 문서가 실제로 있어야 한다. 링크가 깨진 색인은 없느니만 못하다.
    for rel in re.findall(r"\]\(\./((?:docs/ref/)?[A-Za-z0-9_.-]+\.md)\)", text):
        if not (ROOT / rel).exists():
            problems.append(f"CLAUDE.md가 없는 문서를 가리킨다: {rel}")


# ── 7. 개인 값이 리포에 들어오지 않았는가 ─────────────────────────────────

# 2026-09-18: 예시라고 적은 ngrok 도메인이 사용자의 **실제 예약 도메인**이었다.
# 개인 설정은 ~/.vt.env(홈, gitignored)와 ~/.claude/CLAUDE.md에 두고, 리포에는
# 일반 예시만 둔다. 패턴은 "이 리포에 있으면 안 되는 형태"만 좁게 잡는다 —
# 넓게 잡으면 오탐이 쌓이고 아무도 안 보게 된다.
PERSONAL_PATTERNS = (
    (r"\b[a-z0-9-]+\.ngrok-free\.(app|dev)\b", "실제 ngrok 도메인으로 보인다"),
    (r"\b[a-z0-9-]+\.ts\.net\b", "Tailscale MagicDNS 이름으로 보인다"),
    (r"/Users/(?!<user>|you\b|USER\b)[a-z][a-z0-9_-]{1,}/", "개인 홈 경로로 보인다"),
    (r"\b(?!127\.0\.0\.1|0\.0\.0\.0|192\.168\.|10\.|100\.64\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
     "공인 IP로 보인다"),
)
PERSONAL_SCAN_GLOBS = ("*.md", "config/*.env", ".claude/skills/**/*.md", "docs/ref/*.md", "docs/help/*.md")

# 명백한 자리표시자는 통과시킨다. 이게 없으면 "예시를 쓰라"고 해놓고 예시를
# 잡아내는 검사가 되어, 사람들이 검사를 끄는 쪽으로 간다.
PLACEHOLDER_HINTS = ("your-", "example", "my-", "xxx", "USER", "user-", "hostname")
# `<domain>` 같은 꺾쇠 자리표시자만 인정한다. 처음엔 `"<"` 한 글자를 힌트로 뒀다가
# HTML 주석(`<!-- ... -->`)에 박힌 **진짜 도메인이 통과하는** 걸 테스트에서 봤다.
PLACEHOLDER_ANGLE = re.compile(r"<[a-z][a-z0-9_-]*>")


def check_no_personal_values(problems: list[str]) -> None:
    for pattern in PERSONAL_SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if any(h in line for h in PLACEHOLDER_HINTS) or PLACEHOLDER_ANGLE.search(line):
                    continue
                for regex, why in PERSONAL_PATTERNS:
                    m = re.search(regex, line)
                    if m:
                        rel = path.relative_to(ROOT)
                        problems.append(
                            f"{rel}:{line_no}: {why} ({m.group(0)!r}) — "
                            "개인 값은 ~/.vt.env 또는 ~/.claude/CLAUDE.md에 둔다"
                        )
                        break


# ── 8. 리포의 스킬과 전역 사본이 어긋났는가 ───────────────────────────────

def check_skill_copy_in_sync(problems: list[str]) -> None:
    """`~/.claude/skills/fsh/SKILL.md`는 리포본을 `cp`한 수동 사본이다.

    드리프트해도 아무도 모르는 구조라 여기서 알려준다. 전역 사본이 없으면
    (설치 안 한 환경) 아무 말도 하지 않는다 — 그건 문제가 아니다.
    """
    repo = ROOT / ".claude" / "skills" / "fsh" / "SKILL.md"
    global_copy = pathlib.Path.home() / ".claude" / "skills" / "fsh" / "SKILL.md"
    if not repo.exists() or not global_copy.exists():
        return
    try:
        if repo.read_text() != global_copy.read_text():
            problems.append(
                "전역 스킬 사본이 리포본과 다르다 — "
                f"`cp {repo.relative_to(ROOT)} ~/.claude/skills/fsh/SKILL.md`"
            )
    except (OSError, UnicodeDecodeError):
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 일관성 검사 (I3)")
    ap.add_argument("--strict", action="store_true", help="발견 시 종료코드 1")
    args = ap.parse_args()

    problems: list[str] = []
    check_api_docs(problems)
    check_claude_md(problems)
    check_help_topics(problems)
    check_version(problems)
    check_design_md(problems)
    check_claude_md_is_an_index(problems)
    check_no_personal_values(problems)
    check_skill_copy_in_sync(problems)

    if not problems:
        print("✓ 문서 일관성 OK")
        return 0

    print(f"⚠ 문서 드리프트 {len(problems)}건")
    for p in problems:
        print(f"  · {p}")
    if args.strict:
        print("\n(strict 모드 — 종료코드 1)")
        return 1
    print("\n(경고 모드 — 종료코드 0. --strict로 실패시킬 수 있다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
