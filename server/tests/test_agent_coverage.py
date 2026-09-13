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
    # N9 — 커버리지가 자기보고 기록(~/.vt/agent-report-seen.json)을 읽는다.
    # 격리하지 않으면 **개발자 실제 홈의 파일**이 결과를 바꾼다.
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
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


# ── N9(2.1.4) — 자기보고를 커버리지에 반영 ────────────────────────────────
# 2.1.3까지 trust:high는 `path_kind == "hook"`일 때만 나왔고 그 값은 claude일
# 때만 세워졌다 — codex/aider/gemini는 패턴을 아무리 넣어도 영원히 mid였다.
# 자기보고(`fsh pane report`)는 훅과 같은 1차 신호라 같은 등급을 준다.

def test_self_reported_cli_is_report_path_and_high_trust(client, monkeypatch, tmp_path):
    import report_seen

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    report_seen.mark("codex")

    rows = {row["cli"]: row for row in client.get("/api/agents/coverage").json()}
    assert rows["codex"]["path"] == "report"
    assert rows["codex"]["trust"] == "high"
    # 자기보고는 네 상태를 전부 직접 말할 수 있다 — PTY 패턴(waiting 하나)보다 넓다.
    assert rows["codex"]["states"] == ["idle", "working", "waiting", "done"]
    assert rows["codex"]["lastReport"]
    # 보고한 적 없는 CLI는 그대로다.
    assert rows["aider"]["path"] == "none"
    assert rows["aider"]["trust"] == "low"


def test_report_route_records_the_agent_name(client, monkeypatch, tmp_path):
    """실제 경로 전체 — POST /api/agent/report 한 번이 커버리지를 올린다."""
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    before = {row["cli"]: row for row in client.get("/api/agents/coverage").json()}
    assert before["aider"]["trust"] == "low"

    r = client.post("/api/agent/report", json={"state": "waiting", "agent": "aider", "cwd": "/tmp"})
    assert r.status_code == 200

    after = {row["cli"]: row for row in client.get("/api/agents/coverage").json()}
    assert after["aider"]["path"] == "report"
    assert after["aider"]["trust"] == "high"


def test_report_without_agent_name_records_nothing(client, monkeypatch, tmp_path):
    """어느 CLI인지 모르면 기록하지 않는다 — 추측으로 등급을 올리지 않는다."""
    import report_seen

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    client.post("/api/agent/report", json={"state": "working", "cwd": "/tmp"})
    assert report_seen.load() == {}


def test_hook_still_wins_over_report_for_claude(client, monkeypatch, tmp_path):
    """훅이 걸려 있으면 claude는 계속 "훅"으로 보인다(정본이 둘이면 헷갈린다)."""
    import claude_hooks
    import report_seen

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    _write_toml(detect_dir / "claude.toml", "claude", enter=["Do you want to proceed?"], exit_=["esc"])
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)
    monkeypatch.setattr(claude_hooks, "plan", lambda s: {"PreToolUse": ("ok", "")})

    report_seen.mark("claude")
    rows = {row["cli"]: row for row in client.get("/api/agents/coverage").json()}
    assert rows["claude"]["path"] == "hook"
    assert rows["claude"]["trust"] == "high"


def test_unknown_cli_that_reported_shows_up_in_the_table(client, monkeypatch, tmp_path):
    """KNOWN_CLIS에도 toml에도 없는 CLI라도, 보고한 적이 있으면 표에 나온다."""
    import report_seen

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    report_seen.mark("mycli")
    rows = {row["cli"]: row for row in client.get("/api/agents/coverage").json()}
    assert rows["mycli"]["path"] == "report"
    assert rows["mycli"]["patternLines"] == 0


def test_corrupt_report_file_is_treated_as_empty(client, monkeypatch, tmp_path):
    """커버리지 표는 어떤 경우에도 떠야 한다 — 깨진 파일로 500이 나면 안 된다."""
    import report_seen

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    monkeypatch.setattr(detect, "DETECT_DIR", detect_dir)

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / report_seen.FILENAME).write_text("{not json")

    r = client.get("/api/agents/coverage")
    assert r.status_code == 200
    rows = {row["cli"]: row for row in r.json()}
    assert rows["codex"]["path"] == "none"
