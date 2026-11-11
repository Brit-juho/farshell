"""I3 — 문서 일관성 검사기(scripts/check_docs.py) 자체의 회귀 테스트.

검사기가 조용히 고장 나면(정규식 하나가 안 맞아 아무것도 못 찾는 식) **CI는
계속 초록인데 문서는 썩는다.** 그래서 "드리프트를 실제로 잡아내는가"를 고정한다.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(SPEC)
sys.modules["check_docs"] = check_docs
SPEC.loader.exec_module(check_docs)


def test_repo_is_currently_consistent():
    """지금 저장소가 통과 상태여야 한다 — 안 그러면 CI를 strict로 못 올린다."""
    problems = []
    check_docs.check_api_docs(problems)
    check_docs.check_api_contract(problems)
    check_docs.check_agents_md(problems)
    check_docs.check_help_topics(problems)
    check_docs.check_version(problems)
    check_docs.check_design_md(problems)
    check_docs.check_auth_order(problems)
    assert problems == [], "문서 드리프트:\n" + "\n".join(problems)


# ── check_api_contract (P2-3, ADR-38) ───────────────────────────────────
#
# check_api_docs(위)는 경로 **집합**만 본다. 여기서 확인하는 건 그게 못 잡는
# 세 가지 — 메서드 변경·경로 파라미터 개명·필수 쿼리 파라미터 누락 — 를
# openapi 스펙 대조가 실제로 잡아내는가다.


def _fake_spec(paths: dict) -> dict:
    return {"paths": paths}


def _op(*, path_params=(), required_query=()) -> dict:
    params = [{"name": n, "in": "path", "required": True} for n in path_params]
    params += [{"name": n, "in": "query", "required": True} for n in required_query]
    return {"parameters": params, "responses": {"200": {"description": "ok"}}}


def test_method_mismatch_is_detected(monkeypatch):
    spec = _fake_spec({"/api/x/{id}": {"put": _op(path_params=["id"])}})
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(
        check_docs, "_doc_rows", lambda: [("POST", "/api/x/{id}")],
    )
    problems = []
    check_docs.check_api_contract(problems)
    assert any("메서드가 다르다" in p and "PUT" in p and "POST" in p for p in problems)


def test_path_param_rename_is_not_caught_by_the_old_check_but_is_caught_here(monkeypatch):
    """계획서 §4-2의 증명 — 이름만 바뀐 경로는 check_api_docs(집합 비교)는
    통과시키지만 이 검사는 잡아야 한다."""
    spec = _fake_spec({"/api/x/{session_id}": {"get": _op(path_params=["session_id"])}})
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(check_docs, "_doc_rows", lambda: [("GET", "/api/x/{id}")])

    old_check_problems = []
    monkeypatch.setattr(check_docs, "code_routes", lambda: {"/api/x/{}"})
    monkeypatch.setattr(check_docs, "doc_routes", lambda doc: {"/api/x/{}"})
    check_docs.check_api_docs(old_check_problems)
    assert old_check_problems == [], "이건 현행 통과 조건의 전제일 뿐 — 이 항목의 증명이 아니다"

    problems = []
    check_docs.check_api_contract(problems)
    assert any("경로 파라미터 이름이 다르다" in p for p in problems)
    assert any("session_id" in p and "id" in p for p in problems)


def test_missing_required_query_param_is_detected(monkeypatch):
    spec = _fake_spec({"/api/x": {"get": _op(required_query=["q"])}})
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(check_docs, "_doc_rows", lambda: [("GET", "/api/x")])
    problems = []
    check_docs.check_api_contract(problems)
    assert any("필수 쿼리 파라미터가 문서에 없다" in p and "q" in p for p in problems)


def test_bracketed_required_query_param_still_counts_as_missing(monkeypatch):
    """대괄호는 이 문서 관례상 "선택"이다 — 실제로 필수인 파라미터를 대괄호
    안에 적으면 여전히 드리프트여야 한다."""
    spec = _fake_spec({"/api/x": {"get": _op(required_query=["q"])}})
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(check_docs, "_doc_rows", lambda: [("GET", "/api/x[?q=X]")])
    problems = []
    check_docs.check_api_contract(problems)
    assert any("필수 쿼리 파라미터가 문서에 없다" in p for p in problems)


def test_matching_route_produces_no_findings(monkeypatch):
    spec = _fake_spec({
        "/api/x/{item_id}": {"delete": _op(path_params=["item_id"], required_query=["scope"])},
    })
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(
        check_docs, "_doc_rows", lambda: [("DELETE", "/api/x/{item_id}?scope=X")],
    )
    problems = []
    check_docs.check_api_contract(problems)
    assert problems == []


def test_undocumented_route_is_skipped_here_not_double_reported(monkeypatch):
    """check_api_docs가 이미 "문서에 없음"을 보고한다 — 여기서 또 보고하면
    같은 드리프트가 두 번 찍힌다."""
    spec = _fake_spec({"/api/brand-new": {"get": _op()}})
    monkeypatch.setattr(check_docs, "app_openapi", lambda: spec)
    monkeypatch.setattr(check_docs, "_doc_rows", lambda: [])
    problems = []
    check_docs.check_api_contract(problems)
    assert problems == []


def test_websocket_doc_rows_are_excluded():
    """OpenAPI엔 웹소켓 오퍼레이션 개념이 없다 — WS 행을 스펙과 대조하려 하면
    안 된다(오탐만 낸다)."""
    rows = check_docs._DOC_ROW_RE.findall(
        "| WS | `/ws/{id}` | x |\n| GET | `/api/y` | z |\n"
    )
    assert ("WS", "/ws/{id}") in rows  # 정규식 자체는 WS도 뽑는다
    doc_rows = [(m, p) for m, p in rows if m != "WS"]
    assert doc_rows == [("GET", "/api/y")]  # _doc_rows()가 여기서 거른다


def test_app_openapi_returns_none_on_unexpected_failure(monkeypatch):
    """무엇이 됐든(임포트 실패 포함) 예외가 나면 None으로 떨어진다 — 문서
    검사가 서버 초기화에 인질로 잡히면 안 된다(§2.1).

    `sys.path` 조작으로 임포트 실패를 흉내 내면 conftest.py가 이미 진짜
    `server/`를 경로에 꽂아둔 상태라 그쪽으로 폴백해 통과해버린다 — 그래서
    함수 안에서 실제로 도는 호출(`tempfile.TemporaryDirectory`)을 직접
    터뜨린다.
    """
    import tempfile

    def _boom(*a, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _boom)
    assert check_docs.app_openapi() is None


def test_app_openapi_restores_the_real_environment_afterwards(monkeypatch):
    """스펙을 꺼내려고 VT_WORKSPACE_PATH 등을 임시로 바꾸는데, 끝나면 실제
    개발자 환경(이 프로세스의 env)을 원래대로 되돌려야 한다 — 안 그러면 이
    스크립트를 부른 다음 코드가 격리된 값을 계속 보게 된다."""
    import os

    before = os.environ.get("VT_WORKSPACE_PATH")
    check_docs.app_openapi()
    assert os.environ.get("VT_WORKSPACE_PATH") == before


def test_app_openapi_real_call_returns_a_spec_with_paths():
    """진짜 main.py를 임포트해도 동작해야 한다 — 여기서만 실물로 확인한다."""
    spec = check_docs.app_openapi()
    assert spec is not None, "server/main.py를 못 읽었다 — 이 환경엔 fastapi가 있어야 한다"
    assert "/api/sessions" in spec.get("paths", {})


# ── check_design_md (T5) ─────────────────────────────────────────────────
#
# 이 검사기는 두 번 조용히 고장 났다가 고쳐졌다. 둘 다 "통과하는데 아무것도 안
# 잡는" 형태였고, 그게 이 파일이 존재하는 이유 그대로다.
#   1) 산문에 숫자가 있는지만 봤다 → `7`이나 `720`은 문서 어딘가엔 늘 있어서
#      값을 틀리게 바꿔도 통과했다. 전용 표를 검사 대상으로 바꿔 해결.
#   2) 표를 파일 전체에서 찾았다 → 생김새가 같은 토큰 표까지 읽어 오탐이 났다.
#      「코드 파생 값」 절 안으로 스코프를 좁혀 해결.
#   3) rail 개수를 `class="vt-rail-btn` 접두로 셌다 → `vt-rail-btn-DISABLED`도
#      세어 코드 쪽 변경을 못 잡았다. 클래스 경계까지 보게 해서 해결.
#      (리디자인 9단계에 옛 레일을 지우며 `rail-items`는 활동 막대의 id 목록 `activity-items`가 됐다.)


def _design_doc(rows: dict) -> str:
    body = "\n".join(f"| `{k}` | `{v}` | src |" for k, v in rows.items())
    return f"# D\n\n### 코드 파생 값 (기계 검증)\n\n| 키 | 값 | 소스 |\n|---|---|---|\n{body}\n\n### 다음 절\n\n본문\n"


def test_design_rows_are_read_from_the_dedicated_section_only(tmp_path, monkeypatch):
    """생김새가 같은 다른 표(토큰 표)를 검사 대상으로 오인하면 안 된다."""
    doc = _design_doc({"activity-items": "7"}).replace(
        "### 다음 절\n\n본문\n",
        "### 색 토큰\n\n| 토큰 | 값 | 용도 |\n|---|---|---|\n| `--color-acc` | `#f0a860` | 액센트 |\n",
    )
    rows = check_docs._design_rows(doc)
    assert rows == {"activity-items": "7"}


def test_design_missing_section_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "DESIGN.md").write_text("# D\n\n표가 없다\n")
    monkeypatch.setattr(check_docs, "_design_expected", lambda: {"activity-items": "7"})
    problems = []
    check_docs.check_design_md(problems)
    assert len(problems) == 1 and "표가 없다" in problems[0]


def test_design_value_mismatch_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "DESIGN.md").write_text(_design_doc({"activity-items": "6"}))
    monkeypatch.setattr(check_docs, "_design_expected", lambda: {"activity-items": "7"})
    problems = []
    check_docs.check_design_md(problems)
    assert len(problems) == 1
    assert "문서 `6`" in problems[0] and "코드 `7`" in problems[0]


def test_design_missing_row_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "DESIGN.md").write_text(_design_doc({"activity-items": "7"}))
    monkeypatch.setattr(check_docs, "_design_expected",
                        lambda: {"activity-items": "7", "skins": "a, b"})
    problems = []
    check_docs.check_design_md(problems)
    assert len(problems) == 1 and "`skins` 행이 없다" in problems[0]


def test_design_orphan_row_is_flagged(tmp_path, monkeypatch):
    """표에만 있고 코드 검사가 없는 행은 아무도 검증하지 않는 죽은 약속이다."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "DESIGN.md").write_text(_design_doc({"activity-items": "7", "made-up": "1"}))
    monkeypatch.setattr(check_docs, "_design_expected", lambda: {"activity-items": "7"})
    problems = []
    check_docs.check_design_md(problems)
    assert len(problems) == 1 and "made-up" in problems[0]


def test_design_checks_both_languages(tmp_path, monkeypatch):
    """한쪽만 고치고 넘어가는 게 이 저장소에서 가장 흔한 문서 사고다."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "DESIGN.md").write_text(_design_doc({"activity-items": "7"}))
    (tmp_path / "DESIGN.md").write_text(_design_doc({"activity-items": "6"}))
    monkeypatch.setattr(check_docs, "_design_expected", lambda: {"activity-items": "7"})
    problems = []
    check_docs.check_design_md(problems)
    assert len(problems) == 1 and problems[0].startswith("DESIGN.md")


def test_activity_ids_read_the_array_in_order():
    """개수가 아니라 id 목록이다 — 하나 빼고 하나 넣거나 순서를 바꿔도 잡힌다. 상수 id는 side-prefs에서 푼다."""
    src = """import x;
export const ACTIVITIES: Activity[] = [
  { id: SESSIONS_ACTIVITY, label: t('세션'), icon: 'list' },
  { id: 'scm', label: t('변경'), icon: 'git-branch' },
  { id: "dir", label: t('파일') },
];

export function activityById(id: string) { return ACTIVITIES.find((a) => a.id === id); }
"""
    side = "export const SESSIONS_ACTIVITY = 'sessions';"
    assert check_docs._activity_ids(src, side) == ["sessions", "scm", "dir"]


def test_activity_ids_refuse_unknown_expressions():
    """모르는 식(다른 상수 등)이면 틀린 목록 대신 빈 목록 — 표의 행이 「코드 검사가 없다」로 드러난다."""
    src = "export const ACTIVITIES: Activity[] = [\n  { id: OTHER_CONST, label: 'x' },\n];\n"
    assert check_docs._activity_ids(src, "") == []


def test_design_expected_reads_real_code():
    """추출기가 조용히 빈 dict를 돌려주면 검사 전체가 무의미해진다."""
    exp = check_docs._design_expected()
    for key in ("skins", "breakpoints", "pane-cap", "activity-items", "agent-states"):
        assert key in exp, f"{key}를 코드에서 못 뽑았다 — 추출기가 고장 났다"
    assert exp["breakpoints"] == "720/1280"
    assert exp["pane-cap"] == "1/4/6"


def test_norm_strips_doc_only_decorations():
    """문서의 설명 장치(쿼리·대괄호)가 경로 비교를 방해하면 안 된다."""
    n = check_docs.norm
    assert n("/api/ports[?fresh=1]") == "/api/ports"
    assert n("/api/download?path=X") == "/api/download"
    assert n("/api/sessions/{id}") == n("/api/sessions/{session_id}")
    # 대괄호를 `?`보다 먼저 걷어내지 않으면 `/api/ports[` 같은 유령이 생긴다
    assert "[" not in n("/api/ports/{port}[?pid=N]")


def test_static_routes_are_not_required_in_api_docs():
    """`/`·`/sw.js` 같은 정적 서빙까지 요구하면 거짓 경보가 된다."""
    assert not check_docs._is_api("/sw.js")
    assert not check_docs._is_api("/favicon.ico")
    assert check_docs._is_api("/api/usage")
    assert check_docs._is_api("/ws-agent")


def test_detects_undocumented_route(tmp_path, monkeypatch):
    """새 라우트를 만들고 문서에 안 적으면 잡아낸다 — 이 검사의 존재 이유다."""
    monkeypatch.setattr(check_docs, "code_routes", lambda: {"/api/brand-new"})
    monkeypatch.setattr(check_docs, "doc_routes", lambda doc: set())
    problems = []
    check_docs.check_api_docs(problems)
    assert any("/api/brand-new" in p for p in problems)


def test_detects_route_removed_from_code(monkeypatch):
    """문서에만 남은 경로도 잡는다(삭제됐거나 오타)."""
    monkeypatch.setattr(check_docs, "code_routes", lambda: set())
    monkeypatch.setattr(check_docs, "doc_routes", lambda doc: {"/api/ghost"})
    problems = []
    check_docs.check_api_docs(problems)
    assert any("/api/ghost" in p for p in problems)


def test_category_table_in_agents_md_is_allowed(tmp_path, monkeypatch):
    """AGENTS.md의 카테고리 목록은 허용 — 금지 대상은 메서드까지 적은 상세 표다."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "| Category | Paths |\n|---|---|\n"
        "| Sessions | `/api/sessions`, `/ws/{id}`, `/api/watch/{id}` |\n"
        "| tmux | `/api/tmux/sessions`, `/api/tmux/attach`, `/api/tmux/create` |\n"
        "| Voice | `/voice/input`, `/api/x`, `/api/y` |\n"
    )
    problems = []
    check_docs.check_agents_md(problems)
    assert problems == []


def test_detailed_table_in_agents_md_is_flagged(tmp_path, monkeypatch):
    """2026-08-20 드리프트 사고의 재발 방지 — 상세 표가 부활하면 경고."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "| Method | Path | Desc |\n|---|---|---|\n"
        "| GET | `/api/a` | x |\n| POST | `/api/b` | y |\n| GET | `/api/c` | z |\n"
    )
    problems = []
    check_docs.check_agents_md(problems)
    assert len(problems) == 1 and "단일 진실" in problems[0]


def test_version_mismatch_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("2.0.0\n")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.7.0] - 2026-08-20\n")
    problems = []
    check_docs.check_version(problems)
    assert len(problems) == 1 and "2.0.0" in problems[0]


def test_version_ignores_unreleased(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("1.7.0\n")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.7.0] - 2026-08-20\n")
    problems = []
    check_docs.check_version(problems)
    assert problems == []


# ── check_auth_order (P2-5, ADR-42) ─────────────────────────────────────

def test_current_repo_auth_order_matches():
    problems = []
    check_docs.check_auth_order(problems)
    assert problems == [], "\n".join(problems)


def test_auth_order_mismatch_is_detected(tmp_path, monkeypatch):
    """등록 순서를 일부러 바꾸면 잡아야 한다."""
    server = tmp_path / "server"
    server.mkdir()
    (server / "main.py").write_text(
        "app.add_middleware(NetworkAccessMiddleware)\n"
        "app.add_middleware(TokenAuthMiddleware)\n"
        "app.add_middleware(OriginGuardMiddleware)\n"
    )
    docs_ref = tmp_path / "docs" / "ref"
    docs_ref.mkdir(parents=True)
    (docs_ref / "auth.md").write_text(
        "| 1 | X `TokenAuthMiddleware` <!-- mw --> | ... |\n"
        "| 2 | Y `OriginGuardMiddleware` <!-- mw --> | ... |\n"
        "| 3 | Z `NetworkAccessMiddleware` <!-- mw --> | ... |\n"
    )
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    problems = []
    check_docs.check_auth_order(problems)
    assert len(problems) == 1
    assert "다르다" in problems[0]


def test_auth_order_ignores_cors_on_both_sides(tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    (server / "main.py").write_text(
        "app.add_middleware(NetworkAccessMiddleware)\n"
        "app.add_middleware(TokenAuthMiddleware)\n"
        "if _cors:\n"
        "    app.add_middleware(CORSMiddleware, allow_origins=x)\n"
        "app.add_middleware(OriginGuardMiddleware)\n"
    )
    docs_ref = tmp_path / "docs" / "ref"
    docs_ref.mkdir(parents=True)
    (docs_ref / "auth.md").write_text(
        "| 1 | Origin `OriginGuardMiddleware` <!-- mw --> | ... |\n"
        "| 2 | CORS `CORSMiddleware` <!-- mw:cors --> | ... |\n"
        "| 3 | Token `TokenAuthMiddleware` <!-- mw --> | ... |\n"
        "| 4 | Net `NetworkAccessMiddleware` <!-- mw --> | ... |\n"
    )
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    problems = []
    check_docs.check_auth_order(problems)
    assert problems == [], "\n".join(problems)


def test_auth_order_warns_instead_of_silently_passing_when_no_middleware_found(
    tmp_path, monkeypatch,
):
    """미들웨어가 다른 파일로 옮겨져 이 파일에서 하나도 안 잡히면, 빈 집합끼리
    비교해 조용히 통과하면 안 된다 — 검사 자체가 무의미해진 것이므로 경고해야 한다."""
    server = tmp_path / "server"
    server.mkdir()
    (server / "main.py").write_text("# 미들웨어 없음\n")
    docs_ref = tmp_path / "docs" / "ref"
    docs_ref.mkdir(parents=True)
    (docs_ref / "auth.md").write_text("표 없음\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    problems = []
    check_docs.check_auth_order(problems)
    assert len(problems) == 1
    assert "하나도 못 찾았다" in problems[0]


def test_auth_order_warns_when_doc_has_no_middleware_rows(tmp_path, monkeypatch):
    server = tmp_path / "server"
    server.mkdir()
    (server / "main.py").write_text("app.add_middleware(TokenAuthMiddleware)\n")
    docs_ref = tmp_path / "docs" / "ref"
    docs_ref.mkdir(parents=True)
    (docs_ref / "auth.md").write_text("표가 아직 없다\n")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    problems = []
    check_docs.check_auth_order(problems)
    assert len(problems) == 1
    assert "미들웨어 행" in problems[0]


# ── 영어 문서에 남은 한국어 본문(경고만) ────────────────────────────────────


@pytest.mark.parametrize("line,expected", [
    ("Settings → Appearance (모양) opens it", False),          # 영어 이름 (한국어) 병기
    ("the button 「저장」 and `저장됨` and \"저장\"", False),     # 라벨·코드·따옴표 안
    ("Settings → 모양 opens it", True),                         # 라벨 밖 한국어
    ("이 문장은 한국어 본문이다", True),
    ("plain English only", False),
])
def test_korean_outside_labels(line, expected):
    assert check_docs.korean_outside_labels(line) is expected


def test_english_docs_korean_lines_within_limit(capsys):
    """영어 문서(docs/ref·docs/help/en)에 라벨 밖 한국어 본문 줄이 한도를 넘지 않는다."""
    found = check_docs.english_docs_korean_lines()
    assert len(found) <= check_docs.KOREAN_BODY_LIMIT, found


def test_every_ref_doc_must_be_in_the_agents_md_index(tmp_path, monkeypatch):
    """docs/ref 의 문서가 AGENTS.md 색인에서 빠지면 경고 — 색인에 없는 문서는 읽힐 일이 없다."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "docs" / "ref").mkdir(parents=True)
    (tmp_path / "docs" / "ref" / "listed.md").write_text("# a\n")
    (tmp_path / "docs" / "ref" / "orphan.md").write_text("# b\n")
    (tmp_path / "AGENTS.md").write_text("| a | [`docs/ref/listed.md`](./docs/ref/listed.md) |\n")
    problems = []
    check_docs.check_agents_md_is_an_index(problems)
    assert len(problems) == 1 and "orphan.md" in problems[0]
