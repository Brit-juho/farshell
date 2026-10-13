#!/usr/bin/env python3
"""영어 사전(frontend/js/locales/en.js)과 코드의 번역 호출이 맞는지 본다.

키가 한국어 원문이라(gettext 방식) 원문을 고치면 사전 키가 조용히 어긋난다. 화면은
깨지지 않고 그 문구만 한국어로 돌아간다 — 사람 눈에 늦게 띄는 종류라 기계가 잡는다.

검사:
  1. missing  — 코드의 t('…') / vtT('…') / data-i18n 원문이 사전에 없다.
  2. unused   — 사전에 있지만 코드 어디에서도 쓰지 않는 키.
  3. vars     — 원문과 번역의 {자리표시자} 이름이 다르다.
  4. untagged — t()로 감싸지 않은 한글 문자열 리터럴(리포트만, --strict에서도 실패 아님).
               --untagged 를 주면 목록을 출력한다.

사전 본문은 JSON이어야 한다(`window.__VT_I18N = {…};`). 이 스크립트가 json으로 읽는다.

사용:
    python3 scripts/check_i18n.py            # 리포트만 (종료코드 0)
    python3 scripts/check_i18n.py --strict   # 1~3 발견 시 종료코드 1
"""
from __future__ import annotations

import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"
DICT = FRONT / "js" / "locales" / "en.js"
SKIP_DIRS = {"locales", "vendor", "dist", "dist-test", "tests", "node_modules"}

_STR = r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\$]|\\.|\$(?!\{))*)`"""
CALL = re.compile(r"(?<![\w.$])(?:t|T|vtT|window\.vtT)\(\s*(?:" + _STR + r")")
CALL_FULL = re.compile(r"(?<![\w.$])(?:t|T|vtT|window\.vtT)\(\s*(?:" + _STR + r")")
HANGUL_LIT = re.compile(r"""'[^'\n]*[가-힣][^'\n]*'|"[^"\n]*[가-힣][^"\n]*"|`[^`]*[가-힣][^`]*`""")
VAR = re.compile(r"\{(\w+)\}")


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _HtmlKeys(HTMLParser):
    """i18n-boot.js의 apply()와 같은 규칙으로 정적 HTML의 원문 키를 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys: list[str] = []
        self.stack: list[bool] = []  # 각 열린 요소가 data-i18n인가

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in (a.get("data-i18n-attr") or "").split(","):
            v = a.get(name.strip())
            if name.strip() and v:
                self.keys.append(v)
        if "data-i18n" in a and a["data-i18n"]:
            self.keys.append(a["data-i18n"])
        if tag not in VOID:
            self.stack.append("data-i18n" in a and not a["data-i18n"])

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1] and data.strip():
            self.keys.append(data.strip())


def _unescape(s: str) -> str:
    try:
        return json.loads('"' + s.replace('"', '\\"').replace("\\'", "'").replace("\\`", "`") + '"')
    except json.JSONDecodeError:
        return s


def load_dict() -> dict:
    text = DICT.read_text(encoding="utf-8")
    start = text.index("window.__VT_I18N")
    body = text[text.index("{", start): text.rindex("}") + 1]
    return json.loads(body)


def source_files():
    for p in sorted(FRONT.rglob("*")):
        if p.suffix not in (".js", ".ts", ".tsx", ".html") or not p.is_file():
            continue
        if SKIP_DIRS & set(p.relative_to(FRONT).parts):
            continue
        yield p


def _strip_comments(text: str, suffix: str) -> str:
    if suffix == ".html":
        return re.sub(r"<!--.*?-->", "", text, flags=re.S)
    # 줄 주석만 뺀다. 문자열 안의 //(URL)를 지우지 않도록 줄 맨 앞 주석만 본다.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join("" if ln.lstrip().startswith("//") else ln for ln in text.splitlines())


SERVER = ROOT / "server"
_LOG_NAMES = {"print", "debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def collect_server(used: dict, untagged: list) -> None:
    """server/*.py의 tr('원문') 호출을 키로 모으고, 감싸지 않은 한글 문자열 상수를 센다."""
    for p in sorted(SERVER.rglob("*.py")):
        rel = p.relative_to(SERVER).parts
        if "tests" in rel or "__pycache__" in rel or ".venv" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        skip: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                    skip.add(id(body[0].value))
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name == "tr" and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    used.setdefault(node.args[0].value, f"server/{'/'.join(rel)}")
                    skip.add(id(node.args[0]))
                elif name in _LOG_NAMES:  # 로그는 번역하지 않는다
                    for a in ast.walk(node):
                        skip.add(id(a))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip \
                    and re.search(r"[가-힣]", node.value):
                untagged.append(f"server/{'/'.join(rel)}:{node.lineno}: {node.value[:80]!r}")


def collect():
    used: dict[str, str] = {}
    untagged: list[str] = []
    collect_server(used, untagged)
    for p in source_files():
        rel = str(p.relative_to(ROOT))
        text = _strip_comments(p.read_text(encoding="utf-8"), p.suffix)
        for m in CALL.finditer(text):
            raw = next(g for g in m.groups() if g is not None)
            used.setdefault(_unescape(raw), rel)
        if p.suffix == ".html":
            hp = _HtmlKeys()
            hp.feed(text)
            for k in hp.keys:
                used.setdefault(k, rel)
            continue
        for ln_no, ln in enumerate(text.splitlines(), 1):
            # 이미 감싼 호출은 지우고 본다 — 템플릿 안의 ${t('…')}를 미처리로 세지 않게.
            ln = CALL_FULL.sub("", ln)
            for m in HANGUL_LIT.finditer(ln):
                before = ln[: m.start()]
                if re.search(r"(?:\b[tT]|vtT)\(\s*$", before):
                    continue
                untagged.append(f"{rel}:{ln_no}: {m.group(0)[:80]}")
    return used, untagged


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    d = load_dict()
    used, untagged = collect()
    missing = sorted(k for k in used if k not in d)
    unused = sorted(k for k in d if k not in used)
    bad_vars = []
    for k, v in d.items():
        vals = [v] if isinstance(v, str) else [x for x in (v.get("one"), v.get("other")) if x]
        want = set(VAR.findall(k))
        for x in vals:
            got = set(VAR.findall(x))
            # {n}은 복수형 one 문구에서 숫자를 풀어 쓸 수 있어 빠져도 된다.
            if got - want or (want - got - {"n"}):
                bad_vars.append(f"{k!r} -> {x!r}")
    for k in missing:
        print(f"missing: {k!r}  ({used[k]})")
    for k in unused:
        print(f"unused:  {k!r}")
    for b in bad_vars:
        print(f"vars:    {b}")
    if "--untagged" in argv:
        for u in untagged:
            print(f"untagged: {u}")
    print(f"i18n: {len(used)} used, {len(d)} in en, {len(missing)} missing, "
          f"{len(unused)} unused, {len(bad_vars)} var mismatches, {len(untagged)} untagged literals")
    return 1 if strict and (missing or unused or bad_vars) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
