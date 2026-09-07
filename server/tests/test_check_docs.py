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
    check_docs.check_claude_md(problems)
    check_docs.check_help_topics(problems)
    check_docs.check_version(problems)
    assert problems == [], "문서 드리프트:\n" + "\n".join(problems)


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


def test_category_table_in_claude_md_is_allowed(tmp_path, monkeypatch):
    """CLAUDE.md의 카테고리 목록은 허용 — 금지 대상은 메서드까지 적은 상세 표다."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "| Category | Paths |\n|---|---|\n"
        "| Sessions | `/api/sessions`, `/ws/{id}`, `/api/watch/{id}` |\n"
        "| tmux | `/api/tmux/sessions`, `/api/tmux/attach`, `/api/tmux/create` |\n"
        "| Voice | `/voice/input`, `/api/x`, `/api/y` |\n"
    )
    problems = []
    check_docs.check_claude_md(problems)
    assert problems == []


def test_detailed_table_in_claude_md_is_flagged(tmp_path, monkeypatch):
    """2026-08-20 드리프트 사고의 재발 방지 — 상세 표가 부활하면 경고."""
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "| Method | Path | Desc |\n|---|---|---|\n"
        "| GET | `/api/a` | x |\n| POST | `/api/b` | y |\n| GET | `/api/c` | z |\n"
    )
    problems = []
    check_docs.check_claude_md(problems)
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
