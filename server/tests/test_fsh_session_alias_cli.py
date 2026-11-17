"""`bin/fsh` 의 세션 별칭 도우미 — 별칭 또는 ID 를 받아 ID 로 풀고, 목록에는 `별칭 (ID)` 를 보인다.

실제 bin/fsh 에서 그 구간만 떼어 bash 로 돌린다. 서버(`_authed_curl`)와 tmux 는 셸 함수로 흉내 낸다.
별칭은 사람이 친 글이다 — 셸 명령으로 실행되거나 제어 문자가 터미널에 닿으면 안 된다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASH = "/bin/bash" if os.path.exists("/bin/bash") else "bash"

SESSIONS_JSON = ('[{"name": "s-3f9a2c1d", "alias": "버그 수정"}, {"name": "dev", "alias": "dev"},'
                 ' {"name": "s-0000beef", "alias": "$(touch PWNED)\\u001b[31m\\tx"}]')


def _block() -> str:
    text = (REPO / "bin" / "fsh").read_text()
    start = text.index("# ── 세션 별칭(보이는 이름) ↔ ID")
    end = text.index("_ttl_word_to_seconds() {", start)
    return text[start:end]


def run(script: str, tmp_path: Path, *, resolve_rc: int = 0, resolve_json: str = "") -> subprocess.CompletedProcess:
    stub = f'''
VT_PORT=7777
CURL_LOG="{tmp_path}/curl.log"
_t() {{ local o="$1" kv; shift; for kv in "$@"; do o="${{o//\\{{${{kv%%=*}}\\}}/${{kv#*=}}}}"; done; printf '%s' "$o"; }}
faketmux() {{
  case "$1" in
    has-session) case "$3" in =dev|=s-3f9a2c1d|=s-0000beef) return 0 ;; *) return 1 ;; esac ;;
    list-sessions) printf '%s\\n' s-3f9a2c1d dev s-0000beef ;;
  esac
}}
TMUX_BASE=(faketmux)
_authed_curl() {{
  local url="${{@: -1}}"
  printf '%s\\n' "$*" >> "$CURL_LOG"
  case "$url" in
    */api/tmux/sessions) printf '%s' '{SESSIONS_JSON}' ;;
    */api/tmux/resolve) [ {resolve_rc} -eq 0 ] && printf '%s' '{resolve_json}'; return {resolve_rc} ;;
  esac
}}
'''
    return subprocess.run([BASH, "-c", stub + _block() + "\n" + script], cwd=tmp_path, capture_output=True,
                          text=True, timeout=30)


def test_list_shows_alias_first_and_never_runs_or_prints_control_chars(tmp_path):
    r = run('_print_session_bullets; printf "[%s]\\n" "$(printf "%s\\n" dev s-3f9a2c1d | _session_labels_line "$(_session_alias_map)")"',
            tmp_path)
    assert r.returncode == 0, r.stderr
    assert "    • 버그 수정 (s-3f9a2c1d)" in r.stdout
    assert "    • dev\n" in r.stdout                   # 별칭이 ID 와 같으면 ID 만 — dev 는 그대로
    assert "$(touch PWNED) [31m x (s-0000beef)" in r.stdout and "\x1b" not in r.stdout
    assert "[dev 버그 수정 (s-3f9a2c1d) ]" in r.stdout
    assert not (tmp_path / "PWNED").exists()


def test_exact_id_wins_without_asking_the_server(tmp_path):
    r = run('_session_arg dev; echo "rc=$?"', tmp_path)
    assert r.stdout.split() == ["dev", "rc=0"]
    assert not (tmp_path / "curl.log").exists()


def test_alias_resolves_through_the_server(tmp_path):
    r = run('_session_arg "버그 수정"; echo "rc=$?"', tmp_path, resolve_json='{"name": "s-3f9a2c1d", "alias": "버그 수정"}')
    assert r.stdout.split() == ["s-3f9a2c1d", "rc=0"]
    log = (tmp_path / "curl.log").read_text()
    assert "--data-urlencode q=버그 수정" in log and "/api/tmux/resolve" in log


def test_unknown_alias_and_unreachable_server_are_told_apart(tmp_path):
    r = run('_session_arg "없는 이름"; echo "rc=$?"', tmp_path, resolve_rc=22)
    assert r.stdout.splitlines() == ["없는 이름", "rc=1"]
    r = run('_session_arg "$(echo x)y"; echo "rc=$?"', tmp_path, resolve_rc=7)
    assert r.stdout.splitlines() == ["xy", "rc=2"]          # 서버에 못 물으면 받은 값 그대로(옛 동작)


def test_tmux_target_syntax_is_not_used_for_the_exact_check(tmp_path):
    """`dev:1` 은 tmux 에서 dev 세션의 1번 창이다 — 정확한 ID 검사로 넘기지 않고 서버에 묻는다."""
    r = run('_session_arg "dev:1"; echo "rc=$?"', tmp_path, resolve_rc=22)
    assert r.stdout.splitlines() == ["dev:1", "rc=1"]


def test_not_found_message_strips_control_chars(tmp_path):
    r = run('_session_not_found "$(printf "a\\033[2Jb")"', tmp_path)
    assert "a[2Jb" in r.stdout and "\x1b" not in r.stdout
