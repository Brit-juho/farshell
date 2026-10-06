"""~/.codex/hooks.json 에 복제된 FarShell 훅(Codex 앱 가져오기) 감지·제거."""

import json
import stat

import pytest

import codex_hooks as ch

S = "/x/server/agent_hook.sh"


def _data():
    return {
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": f"{S} pre claude"},
                {"type": "command", "command": "/usr/bin/own-hook"},
            ]}],
            "Stop": [{"hooks": [{"type": "command", "command": f"{S} stop claude"}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": f"{S} session_end"}]}],
            "Other": [{"hooks": [{"type": "command", "command": "/usr/bin/keep"}]}],
        }
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def _write(home, data, indent=2, nl=True):
    p = home / "hooks.json"
    p.write_text(json.dumps(data, indent=indent) + ("\n" if nl else ""))
    return p


def test_find_reports_event_args_tag(home):
    _write(home, _data())
    found = ch.find_foreign_farshell_hooks()
    assert sorted(found, key=lambda x: x[0]) == [
        ("PreToolUse", "pre claude", "claude"),
        ("SessionEnd", "session_end", None),
        ("Stop", "stop claude", "claude"),
    ]


def test_find_missing_and_broken_return_empty(home):
    assert ch.find_foreign_farshell_hooks() == []
    (home / "hooks.json").write_text("{nope")
    assert ch.find_foreign_farshell_hooks() == []
    (home / "hooks.json").write_text("[1]")
    assert ch.find_foreign_farshell_hooks() == []
    _write(home, {"hooks": {"Stop": "x", "Pre": [None, {"hooks": "bad"}, {"hooks": [1, {"command": 3}]}]}})
    assert ch.find_foreign_farshell_hooks() == []


def test_dedupe_removes_only_ours_and_prunes(home, capsys):
    p = _write(home, _data(), indent=4)
    assert ch._cmd_dedupe(False) == 0
    out = json.loads(p.read_text())
    assert out["hooks"] == {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "/usr/bin/own-hook"}]}],
        "Other": [{"hooks": [{"type": "command", "command": "/usr/bin/keep"}]}],
    }
    assert ch.find_foreign_farshell_hooks() == []
    text = p.read_text()
    assert text.endswith("\n") and '\n    "hooks"' in text  # 들여쓰기 4칸 보존
    assert json.loads((home / "hooks.json.vtbak").read_text()) == _data()
    assert "3개 제거" in capsys.readouterr().out


def test_dedupe_idempotent(home, capsys):
    p = _write(home, _data())
    ch._cmd_dedupe(False)
    first = p.read_text()
    (home / "hooks.json.vtbak").unlink()
    assert ch._cmd_dedupe(False) == 0
    assert p.read_text() == first
    assert not (home / "hooks.json.vtbak").exists()
    assert "중복 없음" in capsys.readouterr().out


def test_dedupe_dry_run_touches_nothing(home, capsys):
    p = _write(home, _data())
    before = p.read_text()
    assert ch._cmd_dedupe(True) == 0
    assert p.read_text() == before
    assert not (home / "hooks.json.vtbak").exists()
    assert "제거 예정" in capsys.readouterr().out


def test_dedupe_tab_indent_and_no_trailing_newline(home):
    p = home / "hooks.json"
    p.write_text(json.dumps(_data(), indent="\t"))
    ch._cmd_dedupe(False)
    text = p.read_text()
    assert '\n\t"hooks"' in text and not text.endswith("\n")


def test_dedupe_preserves_mode(home):
    p = _write(home, _data())
    p.chmod(0o600)
    ch._cmd_dedupe(False)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_dedupe_broken_json_untouched(home, capsys):
    p = home / "hooks.json"
    p.write_text("{nope")
    assert ch._cmd_dedupe(False) == 2
    assert p.read_text() == "{nope"
    assert not (home / "hooks.json.vtbak").exists()


def test_dedupe_missing_file(home, capsys):
    assert ch._cmd_dedupe(False) == 0
    assert not (home / "hooks.json").exists()
    assert "없습니다" in capsys.readouterr().out


def test_install_never_writes_hooks_json(home):
    ch._cmd_install()
    assert (home / "config.toml").exists()
    assert not (home / "hooks.json").exists()


def test_main_dedupe_args(home):
    _write(home, _data())
    assert ch.main(["x", "dedupe", "--bogus"]) == 64
    assert ch.main(["x", "dedupe", "--dry-run"]) == 0
    assert len(ch.find_foreign_farshell_hooks()) == 3

