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
    rec = tmp_path / "rec"
    rec.mkdir()
    assert cli_resolve.launch_path(str(tmp_path / "claude"), str(rec)) == str(rec)
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


# --- 교차 검토(Codex)에서 나온 보강 ---------------------------------------------------------------

def test_group_writable_files_and_folders_are_refused_unless_the_group_is_trusted(tmp_path):
    exe = _exe(tmp_path / "bin")
    gid = exe.stat().st_gid
    if cli_resolve._trusted_group(gid):
        pytest.skip("이 환경의 기본 그룹은 믿는 그룹(사용자 전용 그룹)이다")
    os.chmod(exe, 0o775)
    assert not cli_resolve.trusted(str(exe), "claude")
    os.chmod(exe, 0o755)
    os.chmod(exe.parent, 0o775)
    assert not cli_resolve.trusted(str(exe), "claude")


def test_every_link_on_the_way_is_checked(tmp_path):
    real = _exe(tmp_path / "versions", "2.1.0")
    hop = tmp_path / "open"
    hop.mkdir()
    (hop / "middle").symlink_to(real)
    os.chmod(hop, 0o777)          # 중간 링크가 아무나 쓸 수 있는 폴더에 있다 → 바꿔 끼울 수 있다
    first = tmp_path / "bin"
    first.mkdir()
    (first / "claude").symlink_to(hop / "middle")
    assert not cli_resolve.trusted(str(first / "claude"), "claude")
    os.chmod(hop, 0o755)
    assert cli_resolve.trusted(str(first / "claude"), "claude")


def test_a_link_whose_upper_folder_others_can_write_is_refused(tmp_path):
    real = _exe(tmp_path / "versions", "2.1.0")
    upper = tmp_path / "shared"
    (upper / "bin").mkdir(parents=True)
    (upper / "bin" / "claude").symlink_to(real)
    os.chmod(upper, 0o777)        # 위 폴더가 열려 있으면 bin 폴더째 바꿔 끼울 수 있다
    assert not cli_resolve.trusted(str(upper / "bin" / "claude"), "claude")


def test_recorded_path_keeps_only_folders_others_cannot_write(tmp_path, monkeypatch):
    good = tmp_path / "good"
    good.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    recorded = os.pathsep.join([".", "", "rel/bin", str(shared), str(good), str(tmp_path / "missing"), str(good)])
    assert cli_resolve.safe_path(recorded) == str(good)
    monkeypatch.setattr(cli_resolve, "known_dirs", lambda: [])
    monkeypatch.setenv("PATH", "/usr/bin")
    assert cli_resolve.launch_path(str(tmp_path / "x" / "claude"), recorded) == str(good)
    # 남는 게 없으면 서버 PATH 쪽으로 간다.
    assert cli_resolve.launch_path(str(good / "claude"), f".{os.pathsep}{shared}") == f"/usr/bin{os.pathsep}{good}"
    # 기록한 PATH에서 이름으로 찾을 때도 남이 쓸 수 있는 폴더는 보지 않는다.
    _exe(shared)
    os.chmod(shared, 0o777)
    assert cli_resolve.resolve("claude", None, str(shared)) is None
