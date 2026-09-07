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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. 서버 라우트 ↔ API.md ───────────────────────────────────────────────
# `@router.get("/api/x")` 형태에서 경로만 뽑는다. 데코레이터가 여러 줄이거나
# 같은 함수에 두 개 붙는 경우(`@router.post` + `@router.get`)도 각각 잡힌다.
ROUTE_RE = re.compile(
    r'@(?:router|app)\.(get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']'
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
    for name in ("CLAUDE.md", "CLAUDE.ko.md"):
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


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 일관성 검사 (I3)")
    ap.add_argument("--strict", action="store_true", help="발견 시 종료코드 1")
    args = ap.parse_args()

    problems: list[str] = []
    check_api_docs(problems)
    check_claude_md(problems)
    check_help_topics(problems)
    check_version(problems)

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
