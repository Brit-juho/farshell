#!/usr/bin/env python3
"""I3 — 문서 일관성 검사.

**코드가 단일 진실이고 문서가 그걸 따라간다.** 이 스크립트는 그 방향으로만
검사한다: 서버 라우트·`fsh help` 토픽·`VERSION`이 실제로 존재하는 것이고,
문서가 그것과 어긋나면 문서 쪽을 고친다.

왜 필요한가: 2026-08-20에 실제로 겪은 드리프트가 근거다 — 프로젝트 지침 문서(당시 `CLAUDE.md`,
2026-11-11부터 `AGENTS.md` 하나로 합침)가 엔드포인트 표를 따로 들고 있다가 `API.md`와 어긋났고,
그 뒤로 지침 문서는 카테고리 목록만 남기고 API.md를 단일 진실로 삼았다. 사람이 기억으로 지키는
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
# `api_route`는 여러 메서드를 한 번에 받는 라우트(호스트 전환 중계, 2026-09-28).
# `\w*router`로 접두사를 허용하는 이유: N30/N31에서 승격이 필요한 라우트를
# `APIRouter(dependencies=[Depends(require_elevated)])`로 묶은 별도 라우터
# (`elevated_router`)가 처음 등장했다 — 변수명이 정확히 "router"가 아니면
# 그 라우트가 통째로 안 잡혀서 문서와 어긋난 것처럼 오탐하는 문제가 있었다.
ROUTE_RE = re.compile(
    r'@(?:\w*router|app)\.(get|post|put|patch|delete|websocket|api_route)\(\s*["\']([^"\']+)["\']'
)

# 경로 파라미터는 문서에서 이름이 다를 수 있다(`{id}` vs `{session_id}`).
# 비교는 **모양**으로 한다 — 파라미터 이름 차이로 거짓 경보를 내면 아무도 안 본다.
PARAM_RE = re.compile(r"\{[^}]+\}")
# P2-3 — check_api_contract는 반대로 **이름까지** 비교하므로 캡처 그룹이 필요하다.
PARAM_RE_NAMED = re.compile(r"\{([^}]+)\}")


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
    for name in ("API.md",):
        doc = ROOT / name
        documented = doc_routes(doc)
        missing = sorted(code - documented)
        # 문서에만 있는 경로 = 삭제됐거나 오타. 코드가 진실이므로 이것도 본다.
        extra = sorted(documented - code)
        for p in missing:
            problems.append(f"{name}: 코드에 있는데 문서에 없음 — {p}")
        for p in extra:
            problems.append(f"{name}: 문서에 있는데 코드에 없음 — {p}")


# ── 1b. API.md ↔ FastAPI openapi 스펙 (P2-3, ADR-38) ─────────────────────
#
# 위 check_api_docs는 "경로 집합"만 본다 — 메서드가 바뀌거나(POST→PUT), 경로
# 파라미터 이름이 바뀌거나(norm()이 지운다), 필수 쿼리 파라미터가 새로 생겨도
# 못 잡는다. FastAPI는 이 정보를 이미 `app.openapi()`로 들고 있다 — 안 쓰고
# 있었을 뿐이다.

_METHODS = ("get", "post", "put", "patch", "delete")


def app_openapi() -> dict | None:
    """FastAPI가 만든 스펙을 읽는다. 서버를 띄우지 않는다 — app 객체에서 바로 꺼낸다.

    **임포트 부작용 주의.** `server/main.py`는 모듈 임포트 시점에
    `vt_env.apply_boundary_overrides()`로 **실제 `~/.vt.env`를 읽어
    os.environ에 적용**한다. 이 스크립트가 그걸 그대로 맞으면 개발자의
    실제 인증·네트워크 설정이 프로세스 환경에 새어 들어온다 — 그래서
    `server/tests/conftest.py`가 격리에 쓰는 것과 같은 여섯 변수
    (`VT_PORT`·`VT_TMUX_SOCKET`·`VT_STATE_DIR`·`VT_RUN_DIR`·`VT_CONFIG`·
    `VT_WORKSPACE_PATH`)를 전부 임시 경로로 고정한다. 임포트가 실패하면
    `None` — 문서 검사가 서버 초기화에 인질로 잡히면 안 된다(§2.1).
    """
    import os
    import sys
    import tempfile

    server_dir = str(ROOT / "server")
    added = server_dir not in sys.path
    if added:
        sys.path.insert(0, server_dir)
    try:
        with tempfile.TemporaryDirectory(prefix="fsh-check-docs-") as tmp:
            empty_cfg = str(Path(tmp) / "empty.env")
            open(empty_cfg, "w", encoding="utf-8").close()
            env_overrides = {
                "VT_PORT": "0",
                "VT_TMUX_SOCKET": "fsh-check-docs",
                "VT_STATE_DIR": str(Path(tmp) / "state"),
                "VT_RUN_DIR": tmp,
                "VT_CONFIG": empty_cfg,
                "VT_WORKSPACE_PATH": str(Path(tmp) / "workspace.json"),
            }
            saved = {k: os.environ.get(k) for k in env_overrides}
            os.environ.update(env_overrides)
            try:
                sys.modules.pop("main", None)  # 이미 임포트돼 있으면(같은 프로세스 재호출) 새로 읽는다
                from main import app  # noqa: PLC0415
                return app.openapi()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
    except Exception:
        return None
    finally:
        if added and server_dir in sys.path:
            sys.path.remove(server_dir)


# API.md 표 행에서 (메서드, 원문 경로)를 뽑는다. `WS`는 스펙에 대응이 없어
# (OpenAPI에 웹소켓 오퍼레이션 개념이 없다) 따로 거른다.
_DOC_ROW_RE = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE|WS)\s*\|\s*`([^`]+)`", re.M
)


def _doc_rows() -> list[tuple[str, str]]:
    doc = ROOT / "API.md"
    if not doc.exists():
        return []
    return [(m, p) for m, p in _DOC_ROW_RE.findall(doc.read_text()) if m != "WS"]


def _doc_query_params(raw_path: str) -> tuple[set[str], set[str]]:
    """(항상 보이는 파라미터, `[...]` 안 선택 파라미터).

    이 문서의 관례가 이미 그 구분이다 — `norm()`이 대괄호를 "선택"으로 보고
    지우는 것과 같은 근거(§2.2 "필수 쿼리 파라미터").
    """
    bracketed = re.findall(r"\[([^\]]*)\]", raw_path)
    optional: set[str] = set()
    for b in bracketed:
        optional |= set(re.findall(r"[?&]([a-zA-Z_]\w*)=", b))
    # 분리 후 다시 정규식을 태우지 않는다 — split()이 구분자 `?`를 삼켜버리면
    # 남은 문자열이 `q=X`처럼 시작해 `[?&]` 앵커가 하나도 안 걸린다(실측
    # 오류 — /api/fs/search?q=X의 필수 쿼리를 못 찾았다). 원문 전체에 바로 돌린다.
    stripped = re.sub(r"\[[^\]]*\]", "", raw_path)
    required = set(re.findall(r"[?&]([a-zA-Z_]\w*)=", stripped))
    return required, optional


def _doc_path_params(raw_path: str) -> list[str]:
    """`{name}` 순서 그대로 — 스펙과 **문자까지** 비교하므로 norm()을 안 쓴다."""
    return PARAM_RE_NAMED.findall(raw_path.split("?", 1)[0])


def _spec_routes(spec: dict) -> dict[tuple[str, str], dict]:
    """(METHOD, norm 경로) → {path_params, required_query, has_response_model, raw_path}."""
    out: dict[tuple[str, str], dict] = {}
    for raw_path, methods in (spec.get("paths") or {}).items():
        npath = norm(raw_path)
        for method, op in (methods or {}).items():
            if method not in _METHODS:
                continue
            params = op.get("parameters") or []
            path_params = [p["name"] for p in params if p.get("in") == "path"]
            required_query = {
                p["name"] for p in params if p.get("in") == "query" and p.get("required")
            }
            schema = (
                op.get("responses", {}).get("200", {})
                .get("content", {}).get("application/json", {}).get("schema", {})
            )
            out[(method.upper(), npath)] = {
                "path_params": path_params,
                "required_query": required_query,
                "has_response_model": bool(schema),
                "raw_path": raw_path,
            }
    return out


def check_api_contract(problems: list[str]) -> None:
    spec = app_openapi()
    if spec is None:
        print("⚠ check_api_contract: server/main.py를 임포트하지 못해 건너뛴다 "
              "(스펙 대조는 스킵 — 다른 검사는 계속된다)")
        return

    spec_routes = _spec_routes(spec)
    doc_by_path: dict[str, dict[str, str]] = {}  # norm_path -> {method: raw_doc_path}
    for method, raw in _doc_rows():
        doc_by_path.setdefault(norm(raw), {})[method] = raw

    spec_by_path: dict[str, dict[str, dict]] = {}
    for (method, npath), info in spec_routes.items():
        spec_by_path.setdefault(npath, {})[method] = info

    declared = 0
    total = 0
    for npath, spec_methods in spec_by_path.items():
        doc_methods = doc_by_path.get(npath)
        if doc_methods is None:
            continue  # check_api_docs가 이미 "문서에 없음"으로 잡는다 — 중복 보고 안 함
        if set(spec_methods) != set(doc_methods):
            problems.append(
                f"API.md: {npath} — 메서드가 다르다. 코드 {sorted(spec_methods)} / "
                f"문서 {sorted(doc_methods)}"
            )
        for method in set(spec_methods) & set(doc_methods):
            info = spec_methods[method]
            total += 1
            if info["has_response_model"]:
                declared += 1

            doc_raw = doc_methods[method]
            doc_path_params = _doc_path_params(doc_raw)
            if doc_path_params != info["path_params"]:
                problems.append(
                    f"API.md: {method} {npath} — 경로 파라미터 이름이 다르다. "
                    f"코드 {info['path_params']} / 문서 {doc_path_params}"
                )

            doc_required, _doc_optional = _doc_query_params(doc_raw)
            missing_query = sorted(info["required_query"] - doc_required)
            if missing_query:
                problems.append(
                    f"API.md: {method} {npath} — 필수 쿼리 파라미터가 문서에 없다: "
                    f"{missing_query} (대괄호 없이 적어야 필수로 읽힌다)"
                )

    if total:
        print(f"  (참고) response_model 미선언 라우트: {total - declared}/{total} "
              "— 새 라우트에는 붙일 것")


def check_agents_md(problems: list[str]) -> None:
    """AGENTS.md는 **카테고리 목록만** 유지한다(2026-08-20 드리프트 사고 이후).

    엔드포인트 표를 다시 들이면 API.md와 두 벌이 되므로, 그 표가 생겼는지를
    검사한다 — 개별 경로가 아니라 '표가 다시 생겼는가'가 검사 대상이다.
    """
    for name in ("AGENTS.md",):
        doc = ROOT / name
        if not doc.exists():
            continue
        text = doc.read_text()
        # 카테고리 목록(`| 카테고리 | 대표 경로 |`)은 **허용**이다 — 실제로 지금
        # AGENTS.md가 그 형태다. 금지 대상은 메서드까지 적은 상세 표
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
                f"({len(detailed)}행) — API.md가 단일 진실이다(AGENTS.md는 카테고리 목록만)"
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
    # 영어판 — `fsh help`는 VT_LANG=en 일 때 docs/help/en/<토픽>.md 를 먼저 본다(없으면 한국어로 떨어진다).
    en_files = {p.stem for p in (ROOT / "docs" / "help" / "en").glob("*.md")}
    for topic in sorted(files - en_files):
        problems.append(f"docs/help/{topic}.md: 영어판이 없음 — docs/help/en/{topic}.md")
    for topic in sorted(en_files - files):
        problems.append(f"docs/help/en/{topic}.md: 한국어 원본이 없음 — docs/help/{topic}.md")
    # `fsh help`가 토픽 목록을 docs/help/*.md에서 직접 만들면(2026-10-02~) 문서가 있는데 안내되지
    # 않는 상황이 구조상 생기지 않는다 — 그때는 이 방향의 검사가 필요 없다.
    m = re.search(r"^cmd_help\(\) \{.*?^\}", fsh, re.S | re.M)
    dynamic = bool(m and '"$docs_dir"/*.md' in m.group(0))
    if not dynamic:
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

def _activity_ids(activity_src: str, side_prefs_src: str = "") -> list[str]:
    """활동 막대 위 무리의 id를 순서대로(shell/activity-data.ts의 `ACTIVITIES` 배열).

    리디자인 9단계에 옛 아이콘 레일(index.html `.vt-rail-btn`)을 지우며 `rail-items`를 이것으로 바꿨다.
    개수가 아니라 id 목록을 보는 이유: 항목을 하나 빼고 하나 넣거나 순서를 바꿔도 개수는 그대로라
    문서가 틀린 채로 통과한다. 「세션」은 상수(`SESSIONS_ACTIVITY`, side-prefs.js)라 그 값으로 푼다.
    배열의 항목 줄(`{ id: … }`)만 센다 — 아래 함수 본문의 `a.id` 같은 것은 대상이 아니다.
    """
    m = re.search(r"export const ACTIVITIES\s*:[^=]*=\s*\[(.*?)\n\];", activity_src, re.S)
    if not m:
        return []
    sess = re.search(r"SESSIONS_ACTIVITY\s*=\s*['\"]([\w-]+)['\"]", side_prefs_src)
    out: list[str] = []
    for raw in re.findall(r"^\s*\{\s*id:\s*([^,]+),", m.group(1), re.M):
        raw = raw.strip()
        lit = re.fullmatch(r"['\"]([\w-]+)['\"]", raw)
        if lit:
            out.append(lit.group(1))
        elif raw == "SESSIONS_ACTIVITY" and sess:
            out.append(sess.group(1))
        else:
            return []  # 모르는 식이면 조용히 틀린 목록을 내지 않는다 — 행이 「없다」로 드러난다
    return out


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

    acts = ROOT / "frontend" / "js" / "shell" / "activity-data.ts"
    if acts.exists():
        side = ROOT / "frontend" / "js" / "shell" / "side-prefs.js"
        ids = _activity_ids(acts.read_text(), side.read_text() if side.exists() else "")
        if ids:
            out["activity-items"] = ", ".join(ids)

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


# ── 6. AGENTS.md는 색인이다 (2026-09-18, 2026-11-11 CLAUDE.md와 합침) ──────────────────────────────────

# 496줄·56k자였던 지침 문서를 색인으로 줄이고 상세를 docs/ref/*.md로 갈랐다. 2026-11-11에 CLAUDE.md(색인)와
# AGENTS.md(계약)를 AGENTS.md 하나로 합쳤다(모든 도구가 같은 파일을 읽는다). 그 파일은 **세션마다 자동으로 컨텍스트에 들어간다** — 다시 불어나면 만지지도
# 않는 영역의 설명까지 매번 토큰을 먹는다. 그래서 예산을 검사로 고정한다.
# 합치기 전 예산은 색인 9000자(+ 계약 문서는 무제한)였다. 합친 파일의 현재 크기는 약 15.1k자.
AGENTS_MD_MAX_CHARS = 17000


def check_agents_md_is_an_index(problems: list[str]) -> None:
    doc = ROOT / "AGENTS.md"
    if not doc.exists():
        return
    text = doc.read_text()
    if len(text) > AGENTS_MD_MAX_CHARS:
        problems.append(
            f"AGENTS.md가 {len(text)}자다(예산 {AGENTS_MD_MAX_CHARS}자) — "
            "상세는 docs/ref/*.md로 옮기고 색인만 남긴다. 이 파일은 매 세션 자동 로드된다"
        )
    # 색인이 가리키는 문서가 실제로 있어야 한다. 링크가 깨진 색인은 없느니만 못하다.
    for rel in re.findall(r"\]\(\./((?:docs/ref/)?[A-Za-z0-9_.-]+\.md)\)", text):
        if not (ROOT / rel).exists():
            problems.append(f"AGENTS.md가 없는 문서를 가리킨다: {rel}")


# ── 7. 개인 값이 리포에 들어오지 않았는가 ─────────────────────────────────

# 2026-09-18: 예시라고 적은 ngrok 도메인이 사용자의 **실제 예약 도메인**이었다.
# 개인 설정은 ~/.vt.env(홈, gitignored)와 ~/.claude/CLAUDE.md에 두고, 리포에는
# 일반 예시만 둔다. 패턴은 "이 리포에 있으면 안 되는 형태"만 좁게 잡는다 —
# 넓게 잡으면 오탐이 쌓이고 아무도 안 보게 된다.
PERSONAL_PATTERNS = (
    (r"\b[a-z0-9-]+\.ngrok-free\.(app|dev)\b", "실제 ngrok 도메인으로 보인다"),
    # 2026-09-21: ngrok과 ts.net은 보면서 **정작 기본 제공자인 cloudflare는 안 봤다.**
    # 그래서 `docs/help/tunnel-hook.md`가 실제로 떴던 quick tunnel 주소 두 개를
    # stdin 예시로 들고 공개 리포에 남아 있었다. 익명 터널 주소는 금방 죽어 위험은
    # 낮지만, "언제 떠 있었는지"를 말해주는 값이고 계약 7은 예외를 두지 않는다.
    (r"\b[a-z0-9-]+\.trycloudflare\.com\b", "실제 cloudflare quick tunnel 주소로 보인다"),
    (r"\b[a-z0-9-]+\.ts\.net\b", "Tailscale MagicDNS 이름으로 보인다"),
    (r"/Users/(?!<user>|you\b|USER\b)[a-z][a-z0-9_-]{1,}/", "개인 홈 경로로 보인다"),
    (r"\b(?!127\.0\.0\.1|0\.0\.0\.0|192\.168\.|10\.|100\.64\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
     "공인 IP로 보인다"),
    # 툴체인 절대경로. 2026-09-21에 `.claude/skills/fsh-{start,voice}.md`가
    # `/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python`을 박아둔 채
    # 이 검사를 통과하고 있었다 — 위 패턴들은 홈 경로·IP·도메인만 봤다. 이런
    # 경로는 **사람마다 다르다**(Homebrew 접두사, conda 배포판, 환경 이름 전부).
    # 문서가 시키는 대로 따라 한 사람은 "no such file"을 보게 된다. 올바른 답은
    # 언제나 `~/.vt.env`의 VT_PYTHON이나 `fsh`/`run_server.sh`에 맡기는 것이다.
    (r"(/opt/homebrew|/usr/local/Caskroom|/opt/miniforge|/opt/anaconda|/opt/miniconda)/\S*/bin/\S+",
     "개인 툴체인 절대경로로 보인다 — VT_PYTHON이나 fsh/run_server.sh에 맡길 것"),
)
# 문서에는 모든 패턴을 적용한다 — 예시로 적힌 IP·도메인도 실제 값이면 곤란하다.
PERSONAL_SCAN_GLOBS = ("*.md", "config/*.env", ".claude/skills/**/*.md", "docs/ref/*.md", "docs/help/*.md", "docs/help/en/*.md", "docs/guide/*.md")

# 2026-09-21: 문서만 보다가 **소스에 박힌 개인 경로를 6주 동안 놓쳤다** —
# server/tests/test_notify_fallback.py가 실제 홈 경로(사용자명 + 사내 프로젝트명)를
# 테스트 픽스처로 들고 공개 리포에 올라가 있었다. 다른 테스트들은 전부 `/Users/x`,
# `/Users/example` 자리표시자를 쓰고 있었으니 관례가 없던 게 아니라 검사가 없던 것이다.
#
# **소스에는 경로 패턴만 적용한다.** IP를 같이 보면 오탐이 37건 쏟아진다 —
# 합성 테스트 픽스처(1.1.1.1, 9.9.9.9), RFC 5737 문서용 대역(203.0.113.x),
# network_access.py의 대역 상수(172.16.0.0, 169.254.0.0)가 전부 걸린다. 그건
# 정상적인 코드다. 반면 **실제 홈 경로나 개인 툴체인 경로가 소스에 있을 정당한
# 이유는 없다** — 사람마다 다르므로 그대로는 아무 데서도 동작하지 않는다.
SOURCE_SCAN_GLOBS = (
    "server/**/*.py", "tests/**/*.py", "scripts/**/*.py",
    "frontend/js/**/*.js", "bin/*", "lib/*.sh", "*.sh",
)
SOURCE_PATTERN_REASONS = ("개인 홈 경로로 보인다", "개인 툴체인 절대경로로 보인다")

# 명백한 자리표시자는 통과시킨다. 이게 없으면 "예시를 쓰라"고 해놓고 예시를
# 잡아내는 검사가 되어, 사람들이 검사를 끄는 쪽으로 간다.
PLACEHOLDER_HINTS = ("your-", "example", "my-", "xxx", "USER", "user-", "hostname")
# `<domain>` 같은 꺾쇠 자리표시자만 인정한다. 처음엔 `"<"` 한 글자를 힌트로 뒀다가
# HTML 주석(`<!-- ... -->`)에 박힌 **진짜 도메인이 통과하는** 걸 테스트에서 봤다.
PLACEHOLDER_ANGLE = re.compile(r"<[a-z][a-z0-9_-]*>")


def check_no_personal_values(problems: list[str]) -> None:
    _scan(problems, PERSONAL_SCAN_GLOBS, PERSONAL_PATTERNS)
    # 이 파일 자신은 패턴 문자열을 들고 있을 수밖에 없다 — 스스로를 검사하면
    # 검사기를 고칠 때마다 검사기가 빨개진다.
    source_pats = tuple(pr for pr in PERSONAL_PATTERNS if pr[1].startswith(SOURCE_PATTERN_REASONS))
    _scan(problems, SOURCE_SCAN_GLOBS, source_pats, skip={pathlib.Path(__file__).resolve()})


def _scan(problems: list[str], globs, patterns, skip=frozenset()) -> None:
    for pattern in globs:
        for path in ROOT.glob(pattern):
            if not path.is_file() or path.resolve() in skip:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if any(h in line for h in PLACEHOLDER_HINTS) or PLACEHOLDER_ANGLE.search(line):
                    continue
                for regex, why in patterns:
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


# ── 9. 코드가 읽는 설정 키가 문서화돼 있는가 (2026-09-18) ─────────────────

# 오늘 VT_TUNNEL_PROVIDER·VT_NGROK_DOMAIN을 추가하면서 문서 갱신을 빠뜨렸다.
# 처음엔 "README/CLI에 나와야 한다"로 검사했는데 튜닝 노브 16개가 같이 걸려
# 노이즈가 됐다 — 그런 검사는 사람들이 끄는 쪽으로 간다. 진짜 불변식은 더 좁다:
# **코드가 읽는 키는 config/vt.defaults.env에 (주석으로라도) 설명돼 있어야 한다.**
# 그 파일이 설정의 목록이자 설명이고, 사람이 처음 여는 곳이다.
CONFIG_SCAN = ("server/*.py", "server/routes/*.py", "server/auth/*.py", "bin/fsh", "lib/*.sh")

# 코드에만 살고 설정 파일에 둘 이유가 없는 키 — 런타임이 스스로 만들거나
# 테스트·격리 전용이라 사용자가 손댈 값이 아니다.
CONFIG_KEY_EXEMPT = {
    "VT_CONFIG",          # 어느 설정 파일을 읽을지 고르는 키(그 파일 안에 적을 수 없다)
    "VT_DIR", "VT_BIN", "VT_DEFAULTS", "VT_SERVER",  # fsh가 스스로 채운다
    "VT_TUNNEL_EVENT", "VT_TUNNEL_MAIN_URL",  # 훅에 넘기는 값(입력이 아니라 출력)
    "VT_WORKSPACE_PATH", "VT_ACCESS_SPEC", "VT_E2E",  # 테스트·격리 전용
    # 스크립트 내부 변수 — 설정이 아니다
    "VT_ENV_BOUNDARY_KEYS", "VT_ENV_BOUNDARY_FIXED", "VT_ENV_FILE_CLEARED_KEYS",
    "VT_AUTOOPEN_ERR", "VT_CLAUTH_STATUS", "VT_TMUX_CONF_PATH", "VT_DETECT_DIR",
    "VT_HOTKEY_VOICE", "VT_HOTKEY_VOICE_DISABLED",
    "VT_TIMEOUT",         # routes/ports.py의 모듈 상수(환경변수가 아니다)
}


def check_config_keys_documented(problems: list[str]) -> None:
    defaults = ROOT / "config" / "vt.defaults.env"
    if not defaults.exists():
        return
    documented = set(re.findall(r"\b(VT_[A-Z0-9_]+)\b", defaults.read_text()))
    used: dict[str, str] = {}
    for pattern in CONFIG_SCAN:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for key in re.findall(r"\b(VT_[A-Z0-9_]{3,})\b", text):
                used.setdefault(key, str(path.relative_to(ROOT)))
    missing = sorted(
        k for k in used
        # 끝이 `_`인 건 f-string 접두사 조각이지 키가 아니다(VT_AUTH_, VT_HOTKEY_ …)
        if not k.endswith("_") and k not in documented and k not in CONFIG_KEY_EXEMPT
    )
    if missing:
        problems.append(
            "코드가 읽는 설정 키가 config/vt.defaults.env에 없다: "
            + ", ".join(f"{k}({used[k]})" for k in missing)
        )


# ── 10. 인가 계층 순서 (P2-5, ADR-42) ─────────────────────────────────────
#
# Starlette는 **마지막 등록을 가장 바깥**에 둔다(add_middleware가
# user_middleware.insert(0, …)) — 그래서 실행 순서는 등록의 역순이다. 그
# 뒤집기를 사람이 매번 암산하면 언젠가 틀린다. `docs/ref/auth.md`의 순서
# 표가 `main.py`의 실제 등록과 어긋나면 없느니만 못하다 — 지침 문서가 자체
# 엔드포인트 표를 들었다가 API.md와 갈라진 것(계약 9)과 같은 병이다.
#
# 표의 미들웨어 행에만 `<!-- mw -->` 마커를 단다 — 표에는 미들웨어가 아닌
# 층(기기+OTP, 자원별 검증 등)도 섞여 있어서, 표 전체를 긁으면 그 행들까지
# "미들웨어"로 오인한다.
_AUTH_MW_ROW_RE = re.compile(r"`(\w+Middleware)`\s*<!-- mw")

# CORS는 조건부 등록(`if _cors: app.add_middleware(CORSMiddleware, ...)`)이라
# 코드 쪽 정규식이 무조건 잡아낸다. 문서 표에도 CORS 행이 있지만 "옵트인"이라
# 항상 있는 건 아니므로, 양쪽에서 같은 이름을 제외하고 비교한다(그래야 "CORS를
# 껐다"가 거짓 드리프트로 안 잡힌다).
_AUTH_ORDER_EXEMPT = {"CORSMiddleware"}


def _auth_md_layer_names() -> list[str] | None:
    doc = ROOT / "docs" / "ref" / "auth.md"
    if not doc.exists():
        return None
    return [m for m in _AUTH_MW_ROW_RE.findall(doc.read_text()) if m not in _AUTH_ORDER_EXEMPT]


def check_auth_order(problems: list[str]) -> None:
    """main.py의 미들웨어 등록 순서 ↔ auth.md 순서 표."""
    main_py = ROOT / "server" / "main.py"
    if not main_py.exists():
        return
    registered = [
        m for m in re.findall(r"app\.add_middleware\(\s*(\w+)", main_py.read_text())
        if m not in _AUTH_ORDER_EXEMPT
    ]
    documented = _auth_md_layer_names()
    if documented is None:
        return  # 문서 자체가 없다 — 다른 검사(파일 존재)가 이미 신경 쓸 일
    # 양쪽 다 "찾았는데 0개"와 "찾을 대상이 없다"를 구분하지 않는다 — 빈
    # 집합끼리 비교해 조용히 통과하는 게 최악이다(미들웨어가 다른 파일로
    # 옮겨지면 이 검사가 아예 없는 것처럼 굴면 안 된다).
    if not registered:
        problems.append(
            "main.py에서 app.add_middleware(...) 등록을 하나도 못 찾았다 — "
            "미들웨어가 다른 파일로 옮겨졌다면 docs/ref/auth.md 순서 표도 같이 옮길 것"
        )
        return
    if not documented:
        problems.append(
            "docs/ref/auth.md 「Authorization order」 표에 미들웨어 행(`<!-- mw -->`)이 "
            "하나도 없다"
        )
        return
    execution_order = list(reversed(registered))  # 마지막 등록이 가장 바깥
    if execution_order != documented:
        problems.append(
            "docs/ref/auth.md 인가 순서 표가 main.py 등록 순서와 다르다 — "
            f"코드(실행 순서) {execution_order} / 문서 {documented}"
        )



# ── 영어 문서에 남은 한국어 본문 ─────────────────────────────────────────
# 공개 문서(docs/ref, docs/help/en)는 영어 본문이다. 한국어는 UI 이름 인용
# (`English name (한국어 이름)`, 「…」, 백틱, 따옴표)으로만 남는다. 실패가 아니라
# **리포트만** 한다 — 줄 수가 한도를 넘으면 참고 줄을 찍는다.
# docs/ref/i18n.md 는 한국어 문구 규칙과 용어집(한국어 열)이 본문이라 뺀다.
ENGLISH_DOC_GLOBS = ("docs/ref/*.md", "docs/help/en/*.md")
ENGLISH_DOC_SKIP = frozenset({"docs/ref/i18n.md"})
KOREAN_BODY_LIMIT = 5
_HANGUL = re.compile(r"[\u3131-\u318e\uac00-\ud7a3]")


def korean_outside_labels(line: str) -> bool:
    """백틱·「」·따옴표·한국어가 든 괄호(영어 이름 병기) 밖에 한국어가 있으면 True."""
    line = re.sub(r"`[^`]*`", "", line)
    line = re.sub(r"「[^」]*」", "", line)
    line = re.sub(r"\"[^\"]*\"", "", line)
    line = re.sub(r"\([^()]*\)", "", line)
    return bool(_HANGUL.search(line))


def english_docs_korean_lines() -> list[str]:
    found: list[str] = []
    for pattern in ENGLISH_DOC_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ENGLISH_DOC_SKIP:
                continue
            in_fence = False
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if not in_fence and korean_outside_labels(line):
                    found.append(f"{rel}:{n}")
    return found


def report_english_docs_korean() -> None:
    """경고만 한다(problems에 넣지 않는다) — 한도 초과 시 참고 줄을 찍는다."""
    found = english_docs_korean_lines()
    if len(found) > KOREAN_BODY_LIMIT:
        print(f"  (참고) docs/ref·docs/help/en 에 라벨·코드 밖 한국어 본문 줄 {len(found)}개 "
              f"(기준 {KOREAN_BODY_LIMIT}개 이하) — 영어로 옮기고 UI 이름은 `English (한국어)` 형식으로: "
              + ", ".join(found[:8]) + (" …" if len(found) > 8 else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 일관성 검사 (I3)")
    ap.add_argument("--strict", action="store_true", help="발견 시 종료코드 1")
    args = ap.parse_args()

    problems: list[str] = []
    check_api_docs(problems)
    check_api_contract(problems)
    check_agents_md(problems)
    check_help_topics(problems)
    check_version(problems)
    check_design_md(problems)
    check_agents_md_is_an_index(problems)
    check_no_personal_values(problems)
    check_skill_copy_in_sync(problems)
    check_config_keys_documented(problems)
    check_auth_order(problems)
    report_english_docs_korean()

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
