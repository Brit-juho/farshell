"""CLI 실행 파일 찾기(server/cli_resolve.py, 세션 로직 점검 단계 3).

재부팅 뒤 서버 PATH에 `~/.local/bin`이 빠져 이어서 시작이 「CLI 실행 파일을 찾을 수 없습니다」로 실패했다.
- 찾는 순서: 기록한 경로 → 기록한 PATH → 서버 PATH → 흔한 설치 위치.
- 남이 바꿔 끼울 수 있는 파일(아무나 쓸 수 있는 폴더·파일, 이름이 다른 파일)은 쓰지 않는다.
- 서버를 띄울 때 흔한 설치 위치를 PATH 끝에 붙인다(앞이 아니라 뒤).
"""

import os

import pytest

import cli_resolve


def _exe(folder, name="claude", mode=0o755):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(mode)
    return path


@pytest.fixture
def no_known_dirs(monkeypatch):
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [])
    monkeypatch.setenv("PATH", "/nonexistent-fsh-path")


def test_trusts_an_own_executable_named_after_the_cli(tmp_path):
    assert cli_resolve.trusted(str(_exe(tmp_path / "bin")), "claude")


@pytest.mark.parametrize("case", ["relative", "wrong_name", "not_executable", "world_writable_file", "world_writable_dir", "directory"])
def test_refuses_files_others_could_swap(tmp_path, case):
    if case == "relative":
        path = "claude"
    elif case == "wrong_name":
        path = str(_exe(tmp_path / "bin", "claude-evil"))
    elif case == "not_executable":
        path = str(_exe(tmp_path / "bin", mode=0o644))
    elif case == "world_writable_file":
        path = str(_exe(tmp_path / "bin", mode=0o757))
    elif case == "world_writable_dir":
        path = str(_exe(tmp_path / "open"))
        os.chmod(tmp_path / "open", 0o777)
    else:
        (tmp_path / "claude").mkdir()
        path = str(tmp_path / "claude")
    assert not cli_resolve.trusted(path, "claude")


def test_a_symlink_keeps_its_own_name_and_is_checked_through(tmp_path):
    real = _exe(tmp_path / "versions", "2.1.0")
    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    (link_dir / "claude").symlink_to(real)
    assert cli_resolve.trusted(str(link_dir / "claude"), "claude")
    # 링크가 사는 폴더가 아무나 쓸 수 있으면 링크를 바꿔 끼울 수 있다.
    os.chmod(link_dir, 0o777)
    assert not cli_resolve.trusted(str(link_dir / "claude"), "claude")


def test_resolution_order(tmp_path, monkeypatch, no_known_dirs):
    recorded = _exe(tmp_path / "recorded")
    env_bin = _exe(tmp_path / "envpath")
    server_bin = _exe(tmp_path / "server")
    known = _exe(tmp_path / "known")
    monkeypatch.setenv("PATH", str(server_bin.parent))
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [str(known.parent)])
    assert cli_resolve.resolve("claude", str(recorded), str(env_bin.parent)) == str(recorded)
    assert cli_resolve.resolve("claude", str(tmp_path / "gone" / "claude"), str(env_bin.parent)) == str(env_bin)
    assert cli_resolve.resolve("claude", None, None) == str(server_bin)
    monkeypatch.setenv("PATH", "/nonexistent-fsh-path")
    assert cli_resolve.resolve("claude") == str(known), "서버 PATH에 없어도(재부팅 뒤 자동 시작) 흔한 위치에서 찾는다"


def test_unknown_tool_or_nothing_found(tmp_path, no_known_dirs):
    assert cli_resolve.resolve("bash") is None
    assert cli_resolve.resolve("claude", str(tmp_path / "missing" / "claude")) is None


def test_record_from_process_reads_the_process_table_only(tmp_path, monkeypatch):
    import agent_config_context
    exe = _exe(tmp_path / "bin")
    monkeypatch.setattr(agent_config_context, "process_args", lambda pid: ([str(exe), "--resume", "x"], {"PATH": "/a:/b"}))
    assert cli_resolve.record_from_process(1, "claude") == (str(exe), "/a:/b")
    # npm 설치(`node …/cli.js`)는 실행 파일 이름이 node — 그 PATH에서 이름으로 찾는다.
    monkeypatch.setattr(agent_config_context, "process_args", lambda pid: (["node", "/x/cli.js"], {"PATH": str(exe.parent)}))
    assert cli_resolve.record_from_process(1, "claude") == (str(exe), str(exe.parent))
    # 찾지 못해도 PATH는 남긴다(나중에 그 PATH로 찾는다).
    monkeypatch.setattr(agent_config_context, "process_args", lambda pid: (["node"], {"PATH": "/nowhere"}))
    assert cli_resolve.record_from_process(1, "claude") == (None, "/nowhere")
    monkeypatch.setattr(agent_config_context, "process_args", lambda pid: (["node"], {}))
    assert cli_resolve.record_from_process(1, "claude") is None

    def gone(pid):
        raise ProcessLookupError(pid)
    monkeypatch.setattr(agent_config_context, "process_args", gone)
    assert cli_resolve.record_from_process(1, "claude") is None
    assert cli_resolve.record_from_process(1, "bash") is None


def test_server_path_appends_existing_safe_dirs_at_the_end(tmp_path, monkeypatch):
    good = tmp_path / "good"
    good.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [str(good), str(shared), str(tmp_path / "missing"), "/usr/bin"])
    assert cli_resolve.server_path("/usr/bin:/bin") == f"/usr/bin:/bin:{good}"
    assert cli_resolve.server_path("") == f"{good}:/usr/bin"


def test_launch_path_prefers_the_recorded_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [])
    monkeypatch.setenv("PATH", "/usr/bin")
    assert cli_resolve.launch_path(str(tmp_path / "claude"), "/rec") == "/rec"
    assert cli_resolve.launch_path(str(tmp_path / "claude")) == f"/usr/bin:{tmp_path}"


def test_profile_launch_and_codex_lookup_use_the_same_resolution(tmp_path, monkeypatch, no_known_dirs):
    import agent_profile_runtime
    import codex_cli
    exe = _exe(tmp_path / "bin", "codex")
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [str(exe.parent)])
    monkeypatch.delenv("VT_CODEX_BIN", raising=False)
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    assert agent_profile_runtime.binary("codex") == str(exe)
    assert codex_cli.executable() == str(exe)
