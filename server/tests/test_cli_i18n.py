"""CLI(bash) 번역 — lib/i18n.sh 의 언어 결정·_t, install.sh --lang, fsh help 의 영어 문서.

bash 3.2(macOS 기본)에서도 돌아야 해서 /bin/bash 로 실행한다. 모든 호출은 env -i 로 시작해
개발자 셸의 VT_LANG·LANG·VT_CONFIG 에 영향받지 않는다.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = "/bin/bash" if os.path.exists("/bin/bash") else "bash"


def _run(script: str, tmp_path: Path, env: dict | None = None, args=()) -> subprocess.CompletedProcess:
    e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "dumb"}
    e.update(env or {})
    return subprocess.run([BASH, "-c", script, "_", *args], env=e, capture_output=True,
                          text=True, timeout=60, cwd=str(ROOT), stdin=subprocess.DEVNULL)


def _cfg(tmp_path: Path, content: str = "") -> Path:
    p = tmp_path / "vt.env"
    p.write_text(content, encoding="utf-8")
    p.chmod(0o600)
    return p


SETUP = f'. "{ROOT}/lib/vt_env.sh"; . "{ROOT}/lib/i18n.sh"; '


@pytest.mark.parametrize("env,file,want", [
    ({"LANG": "ko_KR.UTF-8"}, "", "ko"),
    ({"LANG": "en_US.UTF-8"}, "", "en"),
    ({"LANG": "fr_FR.UTF-8"}, "", "en"),
    ({}, "", "en"),
    ({"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}, "", "ko"),   # C/POSIX 는 건너뛴다
    ({"VT_LANG": "ko", "LANG": "en_US.UTF-8"}, "", "ko"),
    ({"VT_LANG": "ko", "LANG": "ko_KR.UTF-8"}, "VT_LANG='en'\n", "en"),  # 파일이 낡은 export 를 이긴다
    ({"LANG": "en_US.UTF-8"}, "VT_LANG='ko'\n", "ko"),
])
def test_lang_resolution_order(tmp_path, env, file, want):
    cfg = _cfg(tmp_path, file)
    r = _run(SETUP + "vt_lang", tmp_path, {**env, "VT_CONFIG": str(cfg)})
    assert r.stdout == want, r.stderr


def test_t_translates_and_falls_back(tmp_path):
    cfg = _cfg(tmp_path, "VT_LANG='en'\n")
    script = SETUP + (
        'printf "%s|" "$(_t "알 수 없는 명령: {cmd}" cmd=frob)"; '
        'printf "%s|" "$(_t "웹 화면 전용 문구 {n}" n=3)"; '
        'printf "%s|" "$(_t "사전에 없는 문구 {x}" x=7)"; '
        'printf "%s" "$(_t "주소 {url}" url="http://h/?a=1&b=2")"'
    )
    r = _run(script, tmp_path, {"VT_CONFIG": str(cfg)})
    parts = r.stdout.split("|")
    assert parts[0] == "Unknown command: frob"
    assert parts[1] == "웹 화면 전용 문구 3"          # 셸 사전에 없는 키는 원문
    assert parts[2] == "사전에 없는 문구 7"          # 사전에 없으면 한국어 원문
    assert parts[3] == "주소 http://h/?a=1&b=2"      # & 가 깨지지 않는다(bash 5.2 patsub)


def test_t_korean_is_identity(tmp_path):
    cfg = _cfg(tmp_path, "VT_LANG='ko'\n")
    r = _run(SETUP + '_t "알 수 없는 명령: {cmd}" cmd=x', tmp_path, {"VT_CONFIG": str(cfg)})
    assert r.stdout == "알 수 없는 명령: x"


def test_generated_dictionary_is_current():
    r = subprocess.run(["python3", str(ROOT / "scripts" / "gen_cli_i18n.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_every_shell_key_is_in_dictionary():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import gen_cli_i18n
    d = gen_cli_i18n.load_dict()
    missing = [k for k in gen_cli_i18n.shell_keys() if k not in d]
    assert not missing, missing[:10]


def test_help_topics_have_english_pair():
    ko = {p.stem for p in (ROOT / "docs" / "help").glob("*.md")}
    en = {p.stem for p in (ROOT / "docs" / "help" / "en").glob("*.md")}
    assert ko == en, (ko ^ en)
    for p in (ROOT / "docs" / "help" / "en").glob("*.md"):
        assert not re.search(r"[가-힣]", re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)), p.name


# ── install.sh 의 언어 선택(_inst_pick_lang) ─────────────────────────────

def _install_pick() -> str:
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    start = text.index("_inst_os_lang() {")
    end = text.index("_inst_pick_lang\n", text.index("_inst_pick_lang() {"))
    return text[start:end]


def _pick(tmp_path, opt, existing, locale):
    home = tmp_path / "home"
    home.mkdir()
    if existing is not None:
        f = home / ".vt.env"
        f.write_text(existing, encoding="utf-8")
        f.chmod(0o600)
    script = (SETUP + f"LANG_OPT='{opt}'; LANG_PENDING=''; " + _install_pick() +
              '_inst_pick_lang; printf "%s|%s" "$(vt_lang)" "$LANG_PENDING"')
    r = _run(script, home, {"LANG": locale})
    f = home / ".vt.env"
    return r.stdout, (f.read_text(encoding="utf-8") if f.exists() else ""), (f.stat().st_mode & 0o777 if f.exists() else None)


def test_install_lang_option_writes_value(tmp_path):
    out, text, mode = _pick(tmp_path, "en", "FOO=1\n", "ko_KR.UTF-8")
    assert out == "en|" and "VT_LANG='en'" in text and "FOO=1" in text and mode == 0o600


def test_install_keeps_existing_value_without_option(tmp_path):
    out, text, _ = _pick(tmp_path, "", "VT_LANG='en'\n", "ko_KR.UTF-8")
    assert out == "en|" and text.count("VT_LANG=") == 1


def test_install_option_overrides_existing_value(tmp_path):
    out, text, _ = _pick(tmp_path, "ko", "VT_LANG='en'\n", "en_US.UTF-8")
    assert out == "ko|" and "VT_LANG='ko'" in text and text.count("VT_LANG=") == 1


@pytest.mark.parametrize("locale,want", [("ko_KR.UTF-8", "ko"), ("en_US.UTF-8", "en")])
def test_install_noninteractive_follows_os_locale(tmp_path, locale, want):
    out, text, _ = _pick(tmp_path, "", "FOO=1\n", locale)
    assert out == f"{want}|" and f"VT_LANG='{want}'" in text


def test_install_without_env_file_defers_write(tmp_path):
    out, text, _ = _pick(tmp_path, "en", None, "ko_KR.UTF-8")
    assert out == "en|en" and text == ""   # ~/.vt.env 템플릿을 만든 뒤 기록한다(5단계 뒤)


@pytest.mark.parametrize("args,needle", [(["--lang", "fr"], "Invalid --lang"), (["--lang"], "requires a value"),
                                         (["--frob"], "Unknown option")])
def test_install_rejects_bad_options(tmp_path, args, needle):
    r = subprocess.run([BASH, str(ROOT / "install.sh"), *args],
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "en_US.UTF-8"},
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and needle in (r.stdout + r.stderr)
    assert not (tmp_path / ".vt.env").exists()


# ── fsh help 가 영어 문서를 고른다 ────────────────────────────────────────

def _fsh(tmp_path, lang, *args):
    cfg = _cfg(tmp_path)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "dumb", "LANG": "C",
           "VT_CONFIG": str(cfg), "VT_STATE_DIR": str(tmp_path / "s"), "VT_RUN_DIR": str(tmp_path / "r"),
           "VT_TMUX_SOCKET": "fsh-cli-i18n-test", "VT_TUNNEL_PROVIDER": "none", "VT_LANG": lang}
    return subprocess.run([BASH, str(ROOT / "bin" / "fsh"), *args], env=env, capture_output=True,
                          text=True, timeout=60, stdin=subprocess.DEVNULL)


def test_fsh_help_topic_is_english_in_en(tmp_path):
    r = _fsh(tmp_path, "en", "help", "concepts")
    assert r.returncode == 0 and "Core concepts" in r.stdout and "핵심 개념" not in r.stdout


def test_fsh_help_topic_is_korean_in_ko(tmp_path):
    r = _fsh(tmp_path, "ko", "help", "concepts")
    assert r.returncode == 0 and "핵심 개념" in r.stdout


def test_fsh_unknown_command_translated(tmp_path):
    assert "Unknown command: frobnicate" in _fsh(tmp_path, "en", "frobnicate").stdout
    assert "알 수 없는 명령: frobnicate" in _fsh(tmp_path, "ko", "frobnicate").stdout
