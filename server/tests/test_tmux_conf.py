"""tmux 설정 층 — 레포 conf 기준 + tmux.local.conf, 옛 ~/.config/vt/tmux.conf 사본 정리.

홈은 늘 tmp_path 이고, 진짜 tmux 를 쓰는 시험은 버리는 소켓 이름만 쓴다(실제 fsh 서버·홈에 닿지 않음).
"""
import datetime
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import tmux_conf
import tmux_runner

TODAY = datetime.date(2026, 11, 7)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VT_TMUX_CONF", raising=False)
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", "fsh-tmuxconf-test-" + os.urandom(6).hex())
    monkeypatch.setattr(tmux_runner, "VT_TMUX_CONF", str(tmux_conf.REPO_CONF))
    return home


def _stale(home: Path, text: bytes) -> Path:
    p = tmux_conf.legacy_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text)
    return p


def _shipped() -> bytes:
    # 4870e14 판과 바이트까지 같을 필요는 없다 — 지금 레포 판도 「배포한 판」으로 친다.
    return tmux_conf.REPO_CONF.read_bytes()


# ── 결정 순서 ──
def test_resolution_ignores_home_copy(isolated):
    _stale(isolated, b"set -g mouse off\n")
    assert tmux_runner._resolve_conf_path() == str(tmux_conf.REPO_CONF)


def test_resolution_env_override_wins(isolated, tmp_path, monkeypatch):
    custom = tmp_path / "custom.conf"
    custom.write_text("set -g status off\n")
    monkeypatch.setenv("VT_TMUX_CONF", str(custom))
    assert tmux_runner._resolve_conf_path() == str(custom)


def test_resolution_env_override_missing_file_falls_back(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_TMUX_CONF", str(tmp_path / "nope.conf"))
    assert tmux_runner._resolve_conf_path() == str(tmux_conf.REPO_CONF)


def test_bash_resolution_matches(isolated, tmp_path):
    """bin/fsh 의 _resolve_tmux_conf 도 홈 사본을 보지 않는다."""
    _stale(isolated, b"set -g mouse off\n")
    root = tmux_conf.REPO_CONF.parent.parent
    text = (root / "bin" / "fsh").read_text()
    start = text.index("_resolve_tmux_conf() {")
    body = text[start:text.index("\n}\n", start) + 3]
    out = subprocess.run(["bash", "-c", body + '\n_resolve_tmux_conf'],
                         env={"PATH": os.environ["PATH"], "HOME": str(isolated), "VT_DIR": str(root)},
                         capture_output=True, text=True, timeout=10).stdout.strip()
    assert out == str(root / "config" / "vt-tmux.conf")


def test_repo_conf_layers_local_last():
    lines = [ln.strip() for ln in tmux_conf.REPO_CONF.read_text().splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines[-1] == 'source-file -q "$HOME/.config/vt/tmux.local.conf"'
    assert f"set -g {tmux_conf.LAYERED_MARK} 1" in lines


def test_shipped_hashes_cover_current_repo_conf():
    import hashlib
    assert hashlib.sha256(tmux_conf.REPO_CONF.read_bytes()).hexdigest() in tmux_conf.known_hashes()
    assert all(len(h) == 64 for h in tmux_conf.SHIPPED_SHA256)


# ── 옛 사본 정리 ──
def test_migrate_absent_is_noop(isolated):
    r = tmux_conf.migrate_legacy(today=TODAY)
    assert r.action == "absent"
    assert not tmux_conf.conf_dir(isolated).exists()


def test_migrate_known_copy_becomes_backup(isolated):
    src = _stale(isolated, _shipped())
    r = tmux_conf.migrate_legacy(today=TODAY)
    assert r.action == "backup"
    assert not src.exists()
    assert r.dest == src.with_name("tmux.conf.bak-20261107")
    assert r.dest.read_bytes() == _shipped()


def test_migrate_embedded_hash_counts_as_known(isolated, monkeypatch):
    text = b"# an old shipped version\nset -g mouse on\n"
    import hashlib
    monkeypatch.setattr(tmux_conf, "SHIPPED_SHA256", frozenset({hashlib.sha256(text).hexdigest()}))
    _stale(isolated, text)
    assert tmux_conf.migrate_legacy(today=TODAY).action == "backup"


def test_migrate_modified_becomes_legacy_with_notice(isolated):
    src = _stale(isolated, _shipped() + b"set -g mouse off\n")
    r = tmux_conf.migrate_legacy(today=TODAY)
    assert r.action == "legacy"
    assert r.dest == src.with_name("tmux.conf.legacy")
    assert r.dest.read_bytes().endswith(b"set -g mouse off\n")
    lines = tmux_conf.describe_migrate(r)
    assert any("tmux.local.conf" in ln for ln in lines)


@pytest.mark.parametrize("content,name", [(None, "tmux.conf.bak-20261107"), (b"mine\n", "tmux.conf.legacy")])
def test_migrate_never_overwrites_existing_backup(isolated, content, name):
    content = content or _shipped()
    src = _stale(isolated, content)
    first = src.with_name(name)
    first.write_text("older backup\n")
    second = src.with_name(name + ".1")
    second.write_text("even older\n")
    r = tmux_conf.migrate_legacy(today=TODAY)
    assert r.dest == src.with_name(name + ".2")
    assert first.read_text() == "older backup\n" and second.read_text() == "even older\n"
    assert r.dest.read_bytes() == content


def test_migrate_skipped_when_env_override(isolated, tmp_path, monkeypatch):
    custom = tmp_path / "custom.conf"
    custom.write_text("")
    monkeypatch.setenv("VT_TMUX_CONF", str(custom))
    src = _stale(isolated, _shipped())
    assert tmux_conf.migrate_legacy(today=TODAY).action == "override"
    assert src.read_bytes() == _shipped()


def test_migrate_idempotent(isolated):
    _stale(isolated, _shipped())
    assert tmux_conf.migrate_legacy(today=TODAY).action == "backup"
    snapshot = sorted(p.name for p in tmux_conf.conf_dir(isolated).iterdir())
    assert tmux_conf.migrate_legacy(today=TODAY).action == "absent"
    assert sorted(p.name for p in tmux_conf.conf_dir(isolated).iterdir()) == snapshot


def test_migrate_dry_run_touches_nothing(isolated):
    src = _stale(isolated, _shipped())
    r = tmux_conf.migrate_legacy(dry_run=True, today=TODAY)
    assert r.action == "backup" and src.is_file() and not r.dest.exists()


def test_migrate_leaves_local_conf_alone(isolated):
    local = tmux_conf.local_path(isolated)
    local.parent.mkdir(parents=True)
    local.write_text("set -g mouse off\n")
    _stale(isolated, _shipped())
    tmux_conf.migrate_legacy(today=TODAY)
    assert local.read_text() == "set -g mouse off\n"


# ── 다시 읽기 ──
def test_reload_noop_when_server_absent(monkeypatch):
    calls = []

    def run(args, timeout=2.0, input=None):
        calls.append(args)
        return 1, b"", b"no server running"

    monkeypatch.setattr(tmux_runner, "run", run)
    r = tmux_conf.reload()
    assert r.action == "not-running"
    assert calls == [["list-sessions"]]  # source-file 도, 서버를 띄우는 명령도 없다


def test_reload_sources_active_conf(monkeypatch):
    calls = []

    def run(args, timeout=2.0, input=None):
        calls.append(args)
        return 0, b"", b""

    monkeypatch.setattr(tmux_runner, "run", run)
    assert tmux_conf.reload().action == "ok"
    assert calls == [["list-sessions"], ["source-file", str(tmux_conf.REPO_CONF)]]


def test_reload_dry_run_does_not_source(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=2.0, input=None: (calls.append(args) or (0, b"", b"")))
    assert tmux_conf.reload(dry_run=True).action == "would-reload"
    assert calls == [["list-sessions"]]


def test_reload_real_tmux_absent_server():
    if not shutil.which("tmux"):
        pytest.skip("tmux 없음")
    assert tmux_conf.reload().action == "not-running"


# ── doctor 판정 ──
def _status(home, **kw):
    base = dict(running=True, conf=str(tmux_conf.REPO_CONF), override=None, config_files=[],
                layered=True, legacy_location=tmux_conf.legacy_path(home), legacy=None, leftovers=[],
                local=tmux_conf.local_path(home), local_exists=False)
    base.update(kw)
    return tmux_conf.Status(**base)


def test_doctor_ok_when_layered(isolated):
    level, lines = tmux_conf.doctor_lines(_status(isolated, config_files=[str(tmux_conf.REPO_CONF)]))
    assert level == "ok" and len(lines) == 2


def test_doctor_warns_when_running_without_repo_conf(isolated):
    legacy = str(tmux_conf.legacy_path(isolated))
    level, lines = tmux_conf.doctor_lines(_status(isolated, layered=False, config_files=[legacy]))
    assert level == "warn" and legacy in lines[0] and "fsh tmux reload" in lines[0]


def test_doctor_notes_restart_after_reload_from_legacy(isolated):
    legacy = str(tmux_conf.legacy_path(isolated))
    level, lines = tmux_conf.doctor_lines(_status(isolated, config_files=[legacy]))
    assert level == "ok" and len(lines) == 3


def test_doctor_warns_on_leftover_files(isolated):
    leg = tmux_conf.legacy_path(isolated)
    level, lines = tmux_conf.doctor_lines(_status(isolated, running=False, legacy=leg,
                                                  leftovers=[leg.with_name("tmux.conf.legacy")]))
    assert level == "warn" and len(lines) == 4


def test_doctor_cli_against_absent_server(isolated, capsys):
    assert tmux_conf.main(["doctor"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "ok"


def test_cli_reload_migrates_and_skips_absent_server(isolated, capsys):
    if not shutil.which("tmux"):
        pytest.skip("tmux 없음")
    src = _stale(isolated, _shipped())
    assert tmux_conf.main(["reload"]) == 0
    assert not src.exists()
    assert len(list(src.parent.glob("tmux.conf.bak-*"))) == 1


# ── 확장 키(extkeys) 시험 스위치 ───────────────────────────────────────────────
def _local(home: Path) -> Path:
    return tmux_conf.local_path(home)


def test_extkeys_on_creates_block_in_absent_file(isolated):
    assert tmux_conf.extkeys_edit(True, isolated) == "changed"
    text = _local(isolated).read_text()
    assert text == tmux_conf.EXTKEYS_BLOCK
    assert "set -sq extended-keys on" in text and "terminal-features[90]" in text
    assert oct(_local(isolated).stat().st_mode & 0o777) == "0o600"


def test_extkeys_on_is_idempotent_and_keeps_user_lines(isolated):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    p.write_text("set -g mouse off\n# 내 메모\n")
    assert tmux_conf.extkeys_edit(True, isolated) == "changed"
    once = p.read_text()
    assert once.startswith("set -g mouse off\n# 내 메모\n") and once.count(tmux_conf.EXTKEYS_BEGIN) == 1
    assert tmux_conf.extkeys_edit(True, isolated) == "unchanged"
    assert p.read_text() == once


def test_extkeys_on_appends_after_file_without_trailing_newline(isolated):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    p.write_text("set -g mouse off")
    tmux_conf.extkeys_edit(True, isolated)
    assert p.read_text().startswith("set -g mouse off\n")
    assert tmux_conf.extkeys_in_file(isolated)


def test_extkeys_off_removes_only_block_and_restores_original(isolated):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    original = "set -g mouse off\n\nset -g history-limit 9000\n"
    p.write_text(original)
    tmux_conf.extkeys_edit(True, isolated)
    assert tmux_conf.extkeys_edit(False, isolated) == "changed"
    assert p.read_text() == original
    assert tmux_conf.extkeys_edit(False, isolated) == "unchanged"


def test_extkeys_off_keeps_user_lines_around_block(isolated):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    p.write_text(f"set -g a 1\n{tmux_conf.EXTKEYS_BLOCK}set -g b 2\n")
    tmux_conf.extkeys_edit(False, isolated)
    assert p.read_text() == "set -g a 1\nset -g b 2\n"


def test_extkeys_off_without_file_creates_nothing(isolated):
    assert tmux_conf.extkeys_edit(False, isolated) == "unchanged"
    assert not _local(isolated).exists()


def test_extkeys_on_rewrites_edited_block(isolated):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    p.write_text(f"x\n{tmux_conf.EXTKEYS_BEGIN}\nset -sq extended-keys off\n{tmux_conf.EXTKEYS_END}\ny\n")
    assert tmux_conf.extkeys_edit(True, isolated) == "changed"
    assert p.read_text() == f"x\n{tmux_conf.EXTKEYS_BLOCK}y\n"


@pytest.mark.parametrize("body", [
    f"{tmux_conf.EXTKEYS_BEGIN}\nset -g a 1\n",
    f"{tmux_conf.EXTKEYS_END}\n{tmux_conf.EXTKEYS_BEGIN}\n",
    f"{tmux_conf.EXTKEYS_BLOCK}{tmux_conf.EXTKEYS_BLOCK}",
])
def test_extkeys_unbalanced_markers_are_never_edited(isolated, body):
    p = _local(isolated)
    p.parent.mkdir(parents=True)
    p.write_text(body)
    for on in (True, False):
        with pytest.raises(tmux_conf.ExtkeysError):
            tmux_conf.extkeys_edit(on, isolated)
    assert p.read_text() == body


def test_extkeys_dry_run_writes_nothing(isolated):
    assert tmux_conf.extkeys_edit(True, isolated, dry_run=True) == "changed"
    assert not _local(isolated).exists()


def test_extkeys_cli_rejects_mode_on_other_actions(isolated):
    with pytest.raises(SystemExit):
        tmux_conf.main(["reload", "on"])
    with pytest.raises(SystemExit):
        tmux_conf.main(["extkeys", "maybe"])


def test_extkeys_cli_on_off_status_with_absent_server(isolated, capsys, monkeypatch):
    monkeypatch.setattr(tmux_conf, "tmux_version", lambda: (3, 7))
    assert tmux_conf.main(["extkeys", "on"]) == 0
    assert tmux_conf.extkeys_in_file(isolated)
    assert tmux_conf.main(["extkeys", "status"]) == 0
    out = capsys.readouterr().out
    assert "확장 키 구간 있음" in out and "tmux 서버가 꺼져 있습니다" in out
    assert tmux_conf.main(["extkeys", "off"]) == 0
    assert not tmux_conf.extkeys_in_file(isolated)
    assert _local(isolated).read_text() == ""


def test_extkeys_on_refused_below_min_version(isolated, capsys, monkeypatch):
    monkeypatch.setattr(tmux_conf, "tmux_version", lambda: (3, 1))
    assert tmux_conf.main(["extkeys", "on"]) == 1
    assert not _local(isolated).exists()


def test_extkeys_status_warns_when_override_conf(isolated, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("VT_TMUX_CONF", str(tmp_path / "x.conf"))
    monkeypatch.setattr(tmux_conf, "tmux_version", lambda: (3, 7))
    tmux_conf.extkeys_edit(True, isolated)
    assert tmux_conf.main(["extkeys", "status"]) == 0
    assert "VT_TMUX_CONF" in capsys.readouterr().out


def test_extkeys_real_tmux_on_off_roundtrip(isolated, capsys):
    """격리 소켓(fixture 가 임의 이름을 줌)에서 켜기→끄기. 끝나면 서버를 죽이고 소켓 파일을 지운다."""
    if not shutil.which("tmux") or (tmux_conf.tmux_version() or (0, 0)) < tmux_conf.EXTKEYS_MIN_TMUX:
        pytest.skip("tmux 3.2+ 없음")
    sock = tmux_runner.VT_TMUX_SOCKET
    try:
        rc, _, err = tmux_runner.run(["new-session", "-d", "-s", "t"], timeout=5.0)
        assert rc == 0, err
        assert tmux_runner.run_text(["show-options", "-sv", "extended-keys"]).strip() == "off"
        assert tmux_conf.main(["extkeys", "on"]) == 0
        assert tmux_runner.run_text(["show-options", "-sv", "extended-keys"]).strip() == "on"
        for _ in range(2):  # reload 를 거듭해도 한 줄
            assert tmux_conf.main(["extkeys", "on"]) == 0
            tmux_conf.reload()
        feats = tmux_runner.run_text(["show-options", "-sv", "terminal-features"])
        assert feats.count("xterm*:extkeys") == 1
        st = tmux_conf.extkeys_status(isolated)
        assert st.server == "on" and st.feature and st.in_file
        assert tmux_conf.main(["extkeys", "off"]) == 0
        assert tmux_runner.run_text(["show-options", "-sv", "extended-keys"]).strip() == "off"
        assert "extkeys" not in tmux_runner.run_text(["show-options", "-sv", "terminal-features"])
        assert not tmux_conf.extkeys_in_file(isolated)
    finally:
        tmux_runner.run(["kill-server"], timeout=5.0)
        for d in Path("/tmp").glob("tmux-*"):
            (d / sock).unlink(missing_ok=True)
