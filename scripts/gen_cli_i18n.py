#!/usr/bin/env python3
"""셸 스크립트가 쓰는 영어 사전(lib/locales/en.sh)을 frontend/js/locales/en.js에서 만든다.

bash 3.2(macOS 기본)에는 연관 배열이 없어서 사전을 case 문 함수로 굳힌다. 원본은 en.js 하나다 —
이 파일을 손으로 고치지 않는다(scripts/check_i18n.py가 최신인지 검사한다).

    python3 scripts/gen_cli_i18n.py          # 다시 만든다
    python3 scripts/gen_cli_i18n.py --check  # 최신인지만 본다(종료코드 0/1)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT = ROOT / "frontend" / "js" / "locales" / "en.js"
OUT = ROOT / "lib" / "locales" / "en.sh"
SHELL_FILES = ["bin/fsh", "install.sh", "run_server.sh", "lib/vt_env.sh", "lib/i18n.sh"]

# _t '원문' …  /  _t "원문" …   (따옴표 안은 이스케이프 허용)
CALL = re.compile(r"""(?<![\w$])_t\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""")


def shell_keys() -> dict[str, str]:
    """{키: 처음 쓴 위치}. 큰따옴표 키는 셸이 푸는 이스케이프를 풀어 준다."""
    keys: dict[str, str] = {}
    for rel in SHELL_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in CALL.finditer(line):
                if m.group(1) is not None:
                    key = m.group(1)
                else:
                    key = re.sub(r'\\(["$`\\])', r"\1", m.group(2))
                keys.setdefault(key, f"{rel}:{n}")
    return keys


def load_dict() -> dict:
    text = DICT.read_text(encoding="utf-8")
    start = text.index("window.__VT_I18N")
    return json.loads(text[text.index("{", start): text.rindex("}") + 1])


def sq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def render() -> str:
    d = load_dict()
    rows = []
    for key in sorted(shell_keys()):
        v = d.get(key)
        if isinstance(v, dict):  # 셸에는 복수형이 없다 — other를 쓴다
            v = v.get("other") or v.get("one")
        if isinstance(v, str) and v:
            rows.append(f"    {sq(key)}) _T_OUT={sq(v)} ;;")
    body = "\n".join(rows)
    return (
        "# 자동 생성 — 손대지 말 것. 원본: frontend/js/locales/en.js\n"
        "# 만드는 법: python3 scripts/gen_cli_i18n.py\n"
        "_t_en_lookup() {\n"
        '  case "$1" in\n'
        f"{body}\n"
        "    *) return 1 ;;\n"
        "  esac\n"
        "}\n"
    )


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != text:
            print("lib/locales/en.sh가 최신이 아니다 — python3 scripts/gen_cli_i18n.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
