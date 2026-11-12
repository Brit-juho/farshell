"""훅이 온 CLI 프로세스의 실행 경로 기록(routes/agents.py `_remember_cli`, 세션 로직 점검 단계 3).

- 읽은 뒤 그 pid가 아직 그 pane 아래 프로세스인지 다시 확인한다(pid 재사용·세션 교체).
- 세션은 이름이 아니라 pane이 속한 세션의 정체성 키로 찾는다.
- 런타임 행이 아직 없으면 다음 훅에서 다시 시도한다(최대 횟수까지).
"""

import asyncio

import pytest

import cli_resolve
import hook_identity
import routes.agents as agents
import tmux_runner
from session_catalog import db as catalog_db


@pytest.fixture
def fakes(monkeypatch):
    state = {"found": ("/bin/claude", "/usr/bin"), "verdict": (hook_identity.OK, 42), "key": "$1:100", "rows": True, "records": []}
    monkeypatch.setattr(cli_resolve, "record_from_process", lambda pid, tool: state["found"])
    monkeypatch.setattr(hook_identity, "classify", lambda pid, pane: state["verdict"])
    monkeypatch.setattr(tmux_runner, "run_text", lambda args, timeout=None: state["key"])

    def record(key, path, env):
        state["records"].append((key, path, env))
        return state["rows"]
    monkeypatch.setattr(catalog_db, "record_cli", record)
    agents._cli_recorded.clear()
    yield state
    agents._cli_recorded.clear()


def test_records_by_the_pane_sessions_identity(fakes):
    assert agents._record_cli_now(42, "claude", "%3") == "done"
    assert fakes["records"] == [("$1:100", "/bin/claude", "/usr/bin")]


@pytest.mark.parametrize("verdict", [(hook_identity.FOREIGN, 42), (hook_identity.UNKNOWN, None), (hook_identity.OK, 77)])
def test_a_pid_that_is_no_longer_that_panes_process_is_not_recorded(fakes, verdict):
    fakes["verdict"] = verdict
    assert agents._record_cli_now(42, "claude", "%3") == "stop"
    assert fakes["records"] == []


def test_missing_runtime_row_or_unreadable_process_retries(fakes):
    fakes["rows"] = False
    assert agents._record_cli_now(42, "claude", "%3") == "retry"
    fakes["found"] = None
    assert agents._record_cli_now(42, "claude", "%3") == "retry"


def test_remember_retries_on_later_hooks_up_to_a_limit(fakes):
    fakes["rows"] = False

    async def hooks(n):
        for _ in range(n):
            agents._remember_cli(42, "claude", "%3")
            await asyncio.sleep(0.05)
    asyncio.run(hooks(8))
    assert len(fakes["records"]) == agents._CLI_RECORD_TRIES
    fakes["rows"] = True
    agents._cli_recorded.clear()
    asyncio.run(hooks(3))
    assert len(fakes["records"]) == agents._CLI_RECORD_TRIES + 1, "기록한 뒤에는 같은 프로세스를 다시 읽지 않는다"


def test_bad_pane_values_are_ignored(fakes):
    async def go():
        agents._remember_cli(42, "claude", "=app:")
        agents._remember_cli(42, "claude", None)
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert fakes["records"] == []


def test_record_cli_counts_an_unchanged_row_as_recorded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db, inventory
    inventory.reconcile({"complete": True, "epoch": "e", "sessions": [
        {"physical_key": "$1:1", "tmux_id": "$1", "name": "w", "origin": "local", "cwd": "", "panes": []}]})
    assert db.record_cli("$1:1", "/bin/claude", "/usr/bin") is True
    assert db.record_cli("$1:1", "/bin/claude", "/usr/bin") is True
    assert db.record_cli("$9:9", "/bin/claude", "/usr/bin") is False
