"""H4(plan-2.6) — 기계 토큰이 curl 인자(= `ps` 에 보이는 argv)나 URL 쿼리에 실리지 않는다. 허용 목록은 없다.

저장소 안 셸 스크립트·`bin/*`·`.claude`·`scripts`·`lib`·`server/hooks` 를 훑는다(시험 파일·문서·node_modules 제외).
- 이어붙인 논리 줄에 `curl` 이 있고 `token=`(`?token=`·`&token=` 포함)이 있으면 실패.
- 훅 스크립트와 lib/fsh_client.sh 에는 `?token=` 이라는 글자 자체가 없어야 한다(주석 포함).
- 토큰 변수($VT_AUTH_TOKEN 등)를 curl 인자(-H "Authorization: Bearer $x")로 직접 넘기는 줄도 실패 — `-K`(설정 파일) 경유만 허용.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"node_modules", ".git", ".venv", "docs", "tests", "dist", "reports", "research_notes", "__pycache__"}
SCAN_ROOTS = ["bin", "lib", "scripts", "server", ".claude", "install.sh", "run_server.sh"]


def script_files():
    out = []
    for root in SCAN_ROOTS:
        base = REPO / root
        if base.is_file():
            out.append(base)
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                p = Path(dp) / fn
                if p.suffix in (".sh", ".ps1") or p.parent.name == "bin":
                    out.append(p)
                elif p.suffix == "" and p.is_file():
                    try:
                        if p.read_bytes()[:2] == b"#!":
                            out.append(p)
                    except OSError:
                        pass
    return sorted(set(out))


def logical_lines(text: str):
    buf, start = "", 0
    for i, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = i
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        yield start, buf + line
        buf = ""
    if buf:
        yield start, buf


CURL = re.compile(r"(?<![\w-])curl(?![\w-])")
TOKEN_QUERY = re.compile(r"(?i)(?<![\w])token=")
TOKEN_VAR_IN_HEADER = re.compile(r"(?i)Authorization:\s*Bearer\s*\$\{?\w*(TOKEN|tok)\b")


QUERY_TOKEN = re.compile(r"(?i)[?&]token=")


def violations(text: str):
    found = []
    uses_curl = bool(CURL.search(text))
    for n, line in logical_lines(text):
        if line.lstrip().startswith("#"):
            continue
        # curl 을 쓰는 파일이면 `?token=` 을 변수에 담아 curl 줄에서 쓰는 우회(예전 TOKEN_Q)도 잡는다.
        if uses_curl and QUERY_TOKEN.search(line):
            found.append((n, "query-string token in a script that runs curl"))
        if not CURL.search(line):
            continue
        if TOKEN_QUERY.search(line):
            found.append((n, "token= in curl command"))
        if TOKEN_VAR_IN_HEADER.search(line):
            found.append((n, "token variable in curl header argv"))
    return found


def test_detector_catches_the_old_pattern():
    assert violations('curl -s "http://h/api?token=$T" -d x')
    assert violations('curl -s -X POST "http://h/a" \\\n  "http://h/b?x=1&token=$T"')
    assert violations('curl -H "Authorization: Bearer $VT_AUTH_TOKEN" http://h/')
    assert violations('curl -H "Authorization: Bearer ${token}" http://h/')
    assert not violations('curl -K <(_fsh_curl_token_cfg) http://h/')
    assert not violations('# curl "http://h/?token=$T"')
    assert not violations('local token="${VT_AUTH_TOKEN}"')
    # 예전 tmux_client_notify.sh 의 모양: 쿼리를 변수에 담았다가 curl 줄에서 쓴다
    assert violations('TOKEN_Q="?token=${T}"\ncurl -s "http://h/api${TOKEN_Q}" -d x')
    assert not violations('echo "?token=$T"')           # curl 을 쓰지 않는 파일의 URL 출력은 이 시험의 대상이 아니다


def test_scanner_sees_the_scripts_it_should():
    names = {str(p.relative_to(REPO)) for p in script_files()}
    for must in ("bin/fsh", "server/hooks/tmux_client_notify.sh", "server/agent_hook.sh", "lib/fsh_client.sh"):
        assert must in names, must


def test_no_script_puts_a_token_in_curl_argv():
    bad = []
    for p in script_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, why in violations(text):
            bad.append(f"{p.relative_to(REPO)}:{n}: {why}")
    assert not bad, "\n".join(bad)


def test_hook_and_client_lib_never_mention_query_token():
    for rel in ("server/hooks/tmux_client_notify.sh", "lib/fsh_client.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "?token=" not in text and "&token=" not in text, rel
        assert not re.search(r"(?i)\btoken_q\b", text), rel


def test_python_clients_do_not_put_token_in_url():
    """파이썬 클라이언트도 URL 에 token= 을 붙이지 않는다(헤더 또는 소켓)."""
    for rel in ("server/fsh_client.py", "server/mcp_farshell.py", "server/agent_hook.sh", "server/clipboard_daemon.py",
                "server/voice/server_client.py", "server/voice_bind.py", "server/tui/helpers.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"[?&]token=", text), rel
