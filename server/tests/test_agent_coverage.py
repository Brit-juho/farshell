"""N9/N45(80-multihost-agents.md §2) — GET /api/agents/coverage.

핵심 수용 기준: "coverage API가 toml 변경을 반영(줄 수)" — detect/*.toml을
갈아치우면 patternLines가 그 즉시(서버 재시작 없이) 바뀌어야 한다. 그래서
DETECT_DIR을 tmp_path로 몽키패치해 실제 파일을 건드리지 않고 검증한다.

codex/gemini/aider의 실제 프롬프트 문구는 이 테스트에서도 추측해 채우지
않는다 — 빈 toml(스텁)과 채워진 toml(claude 흉내) 두 가지만 다룬다.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import agent_prompt_detect as detect
import main
from routes import agents as agents_route


@pytest.fixture
def client(monkeypatch, tmp_path):
    # 훅 등록 안 된 상태를 기본으로 — 없는 경로를 가리켜 "unreadable"이 나게 한다.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-claude-config"))
    detect._patterns = None
    with TestClient(main.app) as c:
        yield c
    detect._patterns = None


def _write_toml(path, name, enter=None, exit_=None, extra_lines=0):
    lines = [f'name = "{name}"', "", f"enter = {enter or []}", f"exit = {exit_ or []}"]
    lines += ["# padding"] * extra_lines
    path.write_text("\n".join(lines) + "\n")


def test_lists_all_known_clis_even_without_toml(client, monkeypatch, tmp_path):
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    _write_toml(detect_dir / "claude.toml", "claude", enter=["Do you want to proceed?"], exit_=["esc to interrupt"])
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    r = client.get("/api/agents/coverage")
    assert r.status_code == 200
    rows = {row["cli"]: row for row in r.json()}

    # 알려진 4개 CLI가 전부 나온다 — toml이 없는 것도 "none"으로.
    assert set(rows) >= {"claude", "codex", "aider", "gemini"}
    for cli in ("codex", "aider", "gemini"):
        assert rows[cli]["path"] == "none"
        assert rows[cli]["patternLines"] == 0
        assert rows[cli]["states"] == []
        assert rows[cli]["trust"] == "low"


def test_claude_without_hook_is_pty_not_hook(client, monkeypatch, tmp_path):
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    _write_toml(detect_dir / "claude.toml", "claude", enter=["Do you want to proceed?"], exit_=["esc to interrupt"])
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    r = client.get("/api/agents/coverage")
    rows = {row["cli"]: row for row in r.json()}
    assert rows["claude"]["path"] == "pty"          # 훅 미등록이므로 hook이 아니다
    assert rows["claude"]["states"] == ["waiting"]
    assert rows["claude"]["trust"] == "mid"


def test_empty_toml_stub_is_none(client, monkeypatch, tmp_path):
    """codex/aider/gemini 스텁(enter=exit=[])은 파일이 있어도 실효 패턴이 없다."""
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    _write_toml(detect_dir / "codex.toml", "codex", enter=[], exit_=[])
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    r = client.get("/api/agents/coverage")
    rows = {row["cli"]: row for row in r.json()}
    assert rows["codex"]["path"] == "none"
    assert rows["codex"]["states"] == []
    assert rows["codex"]["trust"] == "low"
    # 파일 자체는 있으니 줄 수는 0이 아니어야 한다(내용이 있다).
    assert rows["codex"]["patternLines"] > 0


def test_pattern_lines_reflects_toml_edit_without_restart(client, monkeypatch, tmp_path):
    """핵심 수용 기준 — toml을 고치면(줄 수가 바뀌면) 다음 호출에 바로 반영."""
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    toml_path = detect_dir / "gemini.toml"
    _write_toml(toml_path, "gemini", enter=["question?"], exit_=["done"])
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    r1 = client.get("/api/agents/coverage")
    before = next(row for row in r1.json() if row["cli"] == "gemini")["patternLines"]

    # 줄을 5줄 더 늘려 다시 쓴다 — "서버를 고치는 대신 toml만 고친다"는
    # 원칙(agent_prompt_detect.py 모듈 docstring)이 이 엔드포인트에도 지켜지는지.
    _write_toml(toml_path, "gemini", enter=["question?"], exit_=["done"], extra_lines=5)

    r2 = client.get("/api/agents/coverage")
    after = next(row for row in r2.json() if row["cli"] == "gemini")["patternLines"]

    assert after == before + 5


def test_route_order_coverage_not_swallowed_by_tmux_name(client, monkeypatch, tmp_path):
    """`/api/agents/{tmux_name}` 라우트가 "coverage"를 tmux 이름으로 삼키면
    안 된다 — 커버리지 라우트가 그 앞에 등록돼 있어야 한다."""
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    r = client.get("/api/agents/coverage")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_trust_thresholds_documented_function(monkeypatch):
    """등급 함수 자체의 임계값 계약을 고정한다(회귀 방지)."""
    assert agents_route._coverage_trust("hook", ["waiting"]) == "high"
    assert agents_route._coverage_trust("pty", ["waiting"]) == "mid"
    assert agents_route._coverage_trust("hook", []) == "low"  # hook이어도 패턴이 없으면 low
    assert agents_route._coverage_trust("none", []) == "low"
