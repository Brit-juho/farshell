"""~/.vt.env 계약 테스트 — bash writer / bash source / Python 파서 3자 일치.

이 파일이 검증하는 것은 파서 하나의 동작이 아니라 **셋이 같은 값을 읽는가**이다.
과거 버그가 전부 이 지점에서 났다:
  - 큰따옴표로 쓰던 setter 때문에 `scrypt$16384$8$1$...` 해시가 source 시 `scrypt6384`로 파괴
  - Python 최소 파서가 `'\\''` 이스케이프를 이해하지 못해 bash와 다른 값을 읽음
  - tmp 파일을 umask(644)로 만들어 mv → 시크릿 파일 권한이 600에서 강등
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vt_env  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LIB = os.path.join(_REPO, "lib", "vt_env.sh")

# 과거에 실제로 깨졌거나 깨질 수 있는 값들
TRICKY = [
    ("plain", "7777"),
    ("scrypt_hash", "scrypt$16384$8$1$c2FsdA==$aGFzaA=="),   # $ 확장 → 값 파괴
    ("single_quote", "it's ok"),                              # '\'' 이스케이프
    ("spaces_parens", "터미널 (VT)"),                          # source syntax error
    ("double_quote", 'say "hi"'),
    ("backslash", r"C:\path\to\x"),
    ("backtick", "echo `whoami`"),                            # 명령 치환
    ("dollar_paren", "$(rm -rf /tmp/nope)"),                  # 명령 치환
    ("shell_redirect", "cat > ~/vt-urls.txt"),
    ("hash", "value # not-a-comment"),
    ("empty", ""),
    ("equals_in_value", "https://x.example/p?a=1&b=2"),
    ("tab_like", "a\tb"),
    ("unicode", "한글 값 🌐"),
]


def _bash(script: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )


def _write(path: str, key: str, value: str) -> subprocess.CompletedProcess:
    """lib/vt_env.sh의 vt_env_set으로 기록. 값은 인자로 넘겨 셸 인용을 타지 않게 한다."""
    return subprocess.run(
        ["/bin/bash", "-c",
         f'set -uo pipefail; . "{_LIB}"; vt_env_set "$1" "$2"',
         "bash", key, value],
        capture_output=True, text=True,
        env={**os.environ, "VT_CONFIG": path},
    )


def _read_via_source(path: str, key: str) -> str:
    """bin/vt와 동일하게 bash가 source해서 읽은 값."""
    r = _bash(
        f'set -a; . "{path}"; set +a; printf "%s" "${{{key}-}}"',
        env={"VT_CONFIG": path},
    )
    assert r.returncode == 0, f"source 실패: {r.stderr}"
    return r.stdout


@pytest.fixture()
def envfile(tmp_path):
    return str(tmp_path / ".vt.env")


class TestThreeWayAgreement:
    @pytest.mark.parametrize("name,value", TRICKY, ids=[t[0] for t in TRICKY])
    def test_bash_and_python_read_the_same_value(self, envfile, name, value):
        key = "VT_TEST_VALUE"
        w = _write(envfile, key, value)
        assert w.returncode == 0, f"vt_env_set 실패: {w.stderr}"

        from_bash = _read_via_source(envfile, key)
        from_python = vt_env.load(envfile)[key]

        assert from_bash == value, f"bash source가 값을 바꿨다: {from_bash!r} != {value!r}"
        assert from_python == value, f"Python 파서가 값을 바꿨다: {from_python!r} != {value!r}"

    def test_command_substitution_does_not_execute(self, envfile, tmp_path):
        """값에 $( ) 가 있어도 source 시 실행되면 안 된다 (홑따옴표 보장)."""
        canary = tmp_path / "canary"
        _write(envfile, "VT_TEST_VALUE", f"$(touch {canary})")
        _read_via_source(envfile, "VT_TEST_VALUE")
        assert not canary.exists(), "값 안의 명령이 실행됐다 — 인용이 깨짐"


class TestPermissions:
    def test_set_forces_0600(self, envfile):
        _write(envfile, "VT_A", "1")
        assert oct(os.stat(envfile).st_mode & 0o777) == "0o600"

    def test_set_does_not_downgrade_existing_0600(self, envfile):
        """과거 버그: tmp가 umask(644)로 생겨 mv 후 600 → 644로 강등됐다."""
        _write(envfile, "VT_AUTH_PASSWORD_HASH", "scrypt$1$old")
        os.chmod(envfile, 0o600)
        _write(envfile, "VT_AUTH_PASSWORD_HASH", "scrypt$1$new")
        assert oct(os.stat(envfile).st_mode & 0o777) == "0o600"

    def test_unset_keeps_0600(self, envfile):
        _write(envfile, "VT_A", "1")
        _write(envfile, "VT_B", "2")
        r = _bash(f'. "{_LIB}"; vt_env_unset VT_A', env={"VT_CONFIG": envfile})
        assert r.returncode == 0
        assert oct(os.stat(envfile).st_mode & 0o777) == "0o600"


class TestIdempotenceAndIsolation:
    def test_repeated_set_does_not_duplicate(self, envfile):
        for i in range(3):
            _write(envfile, "VT_A", f"v{i}")
        body = open(envfile, encoding="utf-8").read()
        assert body.count("VT_A=") == 1
        assert vt_env.load(envfile)["VT_A"] == "v2"

    def test_set_preserves_other_keys(self, envfile):
        _write(envfile, "VT_KEEP", "keep me")
        _write(envfile, "VT_OTHER", "x")
        loaded = vt_env.load(envfile)
        assert loaded["VT_KEEP"] == "keep me"
        assert loaded["VT_OTHER"] == "x"

    def test_unset_removes_only_target(self, envfile):
        _write(envfile, "VT_A", "1")
        _write(envfile, "VT_AB", "2")
        _bash(f'. "{_LIB}"; vt_env_unset VT_A', env={"VT_CONFIG": envfile})
        loaded = vt_env.load(envfile)
        assert "VT_A" not in loaded
        assert loaded.get("VT_AB") == "2", "접두가 같은 다른 키까지 지워졌다"

    def test_invalid_key_rejected(self, envfile):
        r = _bash(f'. "{_LIB}"; vt_env_set "BAD KEY" v', env={"VT_CONFIG": envfile})
        assert r.returncode == 2
        assert not os.path.exists(envfile), "잘못된 키인데 파일을 건드렸다"

    def test_get_matches_python(self, envfile):
        _write(envfile, "VT_A", "터미널 (VT)")
        r = _bash(f'. "{_LIB}"; vt_env_get VT_A', env={"VT_CONFIG": envfile})
        assert r.stdout == "터미널 (VT)"
        assert vt_env.load(envfile)["VT_A"] == "터미널 (VT)"


def _load_via_bash(path: str, key: str, extra_env=None) -> str:
    """bin/vt와 동일하게 vt_env_load(파서)로 읽은 값. source가 아니다."""
    r = subprocess.run(
        ["/bin/bash", "-c",
         f'set -uo pipefail; . "{_LIB}"; vt_env_load "$1"; '
         f'printf "%s" "${{{key}-}}"',
         "bash", path],
        capture_output=True, text=True,
        env={**os.environ, **(extra_env or {})},
    )
    assert r.returncode == 0, f"vt_env_load 실패: {r.stderr}"
    return r.stdout


# 확장 규칙 — bash 파서와 Python 파서가 반드시 같은 값을 내야 한다
EXPANSION_CASES = [
    # (파일 내용, 확인할 키, 기대값)
    ("VT_DIR=/opt/vt\nVT_PYTHON=${VT_DIR}/.venv/bin/python\n",
     "VT_PYTHON", "/opt/vt/.venv/bin/python"),                 # install.sh가 만드는 형태
    ("VT_A=/opt\nVT_B=$VT_A/x\n", "VT_B", "/opt/x"),           # 중괄호 없는 참조
    ("VT_A=/opt\nVT_B='$VT_A/x'\n", "VT_B", "$VT_A/x"),        # 홑따옴표 = 확장 없음
    ('VT_A=/opt\nVT_B="$VT_A/x"\n', "VT_B", "/opt/x"),         # 큰따옴표 = 확장
    ("VT_B=${VT_NOPE_UNDEFINED}/x\n", "VT_B", "/x"),           # 미정의 → 빈 문자열
    ('VT_B="a\\$b"\n', "VT_B", "a$b"),                         # \$ 이스케이프
    ("VT_B='scrypt$16384$8$1$abc'\n",
     "VT_B", "scrypt$16384$8$1$abc"),                          # 해시는 홑따옴표로 보존
]


class TestExpansionParity:
    @pytest.mark.parametrize("content,key,expected", EXPANSION_CASES,
                             ids=[c[1] + str(i) for i, c in enumerate(EXPANSION_CASES)])
    def test_bash_and_python_expand_identically(self, tmp_path, content, key, expected):
        p = tmp_path / ".vt.env"
        p.write_text(content, encoding="utf-8")
        from_bash = _load_via_bash(str(p), key)
        from_python = vt_env.load(str(p))[key]
        assert from_bash == expected, f"bash: {from_bash!r} != {expected!r}"
        assert from_python == expected, f"python: {from_python!r} != {expected!r}"

    def test_env_falls_back_when_key_not_in_file(self, tmp_path):
        p = tmp_path / ".vt.env"
        p.write_text("VT_B=${VT_FROM_ENV}/x\n", encoding="utf-8")
        assert _load_via_bash(str(p), "VT_B",
                              {"VT_FROM_ENV": "/env"}) == "/env/x"
        assert vt_env.load(str(p), )["VT_B"] == "/x"  # 환경에 없으면 빈 값


class TestNoExecution:
    """설정 파일은 데이터다 — 어떤 구문도 실행되면 안 된다."""

    def test_command_substitution_not_executed(self, tmp_path):
        canary = tmp_path / "canary"
        p = tmp_path / ".vt.env"
        p.write_text(f'VT_X="$(touch {canary})"\n', encoding="utf-8")
        _load_via_bash(str(p), "VT_X")
        vt_env.load(str(p))
        assert not canary.exists(), "명령 치환이 실행됐다"

    def test_backtick_not_executed(self, tmp_path):
        canary = tmp_path / "canary2"
        p = tmp_path / ".vt.env"
        p.write_text(f'VT_X="`touch {canary}`"\n', encoding="utf-8")
        _load_via_bash(str(p), "VT_X")
        assert not canary.exists(), "백틱이 실행됐다"

    def test_arbitrary_command_line_not_executed(self, tmp_path):
        canary = tmp_path / "canary3"
        p = tmp_path / ".vt.env"
        p.write_text(f"touch {canary}\nVT_X='ok'\n", encoding="utf-8")
        assert _load_via_bash(str(p), "VT_X") == "ok"
        assert not canary.exists(), "설정 파일의 명령이 실행됐다"

    def test_malformed_line_does_not_kill_loader(self, tmp_path):
        """과거: set -u + source 조합에서 "...$8..." 한 줄이 vt 전체를 죽였다."""
        p = tmp_path / ".vt.env"
        p.write_text('VT_H="scrypt$16384$8$1$abc"\nVT_OK=\'fine\'\n', encoding="utf-8")
        assert _load_via_bash(str(p), "VT_OK") == "fine"
        assert vt_env.load(str(p))["VT_OK"] == "fine"


class TestPrecedence:
    def test_env_beats_file(self, tmp_path):
        p = tmp_path / ".vt.env"
        p.write_text("VT_PORT='7777'\n", encoding="utf-8")
        r = subprocess.run(
            ["/bin/bash", "-c",
             f'set -uo pipefail; . "{_LIB}"; '
             f'_VT_ENV_PRESET_NAMES=" VT_PORT "; vt_env_load "$1"; printf "%s" "$VT_PORT"',
             "bash", str(p)],
            capture_output=True, text=True,
            env={**os.environ, "VT_PORT": "9999"},
        )
        assert r.stdout == "9999", "환경변수가 파일보다 우선해야 한다"

    def test_config_beats_defaults(self, tmp_path):
        defaults = tmp_path / "defaults.env"
        config = tmp_path / ".vt.env"
        defaults.write_text("VT_PORT='7777'\n", encoding="utf-8")
        config.write_text("VT_PORT='8888'\n", encoding="utf-8")
        r = subprocess.run(
            ["/bin/bash", "-c",
             f'set -uo pipefail; . "{_LIB}"; vt_env_load "$1"; vt_env_load "$2"; '
             f'printf "%s" "$VT_PORT"',
             "bash", str(defaults), str(config)],
            capture_output=True, text=True, env={**os.environ},
        )
        assert r.stdout == "8888", "~/.vt.env가 defaults보다 우선해야 한다"


class TestLint:
    def test_flags_undefined_variable(self, envfile):
        """옛 setter가 만들던 형태 — $abc가 확장돼 값 일부가 사라진다."""
        open(envfile, "w", encoding="utf-8").write('VT_H="scrypt$16384$8$1$abc"\n')
        r = _bash(f'. "{_LIB}"; vt_env_lint "{envfile}"')
        assert r.returncode == 1
        assert "VT_H" in r.stdout and "abc" in r.stdout

    def test_flags_execution_syntax(self, envfile):
        open(envfile, "w", encoding="utf-8").write('VT_X="$(whoami)"\nVT_Y=`date`\n')
        r = _bash(f'. "{_LIB}"; vt_env_lint "{envfile}"')
        assert r.returncode == 1
        assert "VT_X" in r.stdout and "VT_Y" in r.stdout

    def test_accepts_canonical_file(self, envfile):
        _write(envfile, "VT_A", "scrypt$16384$8$1$abc")
        _write(envfile, "VT_B", "터미널 (VT)")
        r = _bash(f'. "{_LIB}"; vt_env_lint "{envfile}"')
        assert r.returncode == 0, f"정규 형식인데 걸렸다: {r.stdout}"

    def test_accepts_intentional_expansion(self, envfile):
        """install.sh가 만드는 형태 — 앞줄에서 정의된 참조는 문제가 아니다."""
        open(envfile, "w", encoding="utf-8").write(
            "VT_DIR=/opt/vt\nVT_PYTHON=${VT_DIR}/.venv/bin/python\n")
        r = _bash(f'. "{_LIB}"; vt_env_lint "{envfile}"')
        assert r.returncode == 0, f"의도한 확장인데 걸렸다: {r.stdout}"


class TestPythonParser:
    def test_legacy_forms_still_read(self):
        parsed = vt_env.parse(
            'VT_A=7777\n'
            'export VT_B="hello"\n'
            "VT_C='world'\n"
            '# comment\n'
            '\n'
            'VT_D=\n'
        )
        assert parsed == {"VT_A": "7777", "VT_B": "hello",
                          "VT_C": "world", "VT_D": ""}

    def test_last_definition_wins(self):
        assert vt_env.parse("VT_A='1'\nVT_A='2'\n")["VT_A"] == "2"

    def test_env_takes_precedence(self, monkeypatch, envfile):
        _write(envfile, "VT_A", "from-file")
        monkeypatch.setenv("VT_A", "from-env")
        assert vt_env.getenv("VT_A", file_env=vt_env.load(envfile)) == "from-env"

    def test_missing_file_is_empty(self, tmp_path):
        assert vt_env.load(str(tmp_path / "nope")) == {}
