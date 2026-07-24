"""~/.vt.env 파서 — bash(bin/vt의 source)와 동일하게 읽는 단일 구현.

이전에는 voice/config.py와 clipboard_daemon.py가 각자 최소 파서를 중복 구현했고,
bash와도 해석이 달라 같은 파일을 셋이 다르게 읽었다. 예를 들어

    VT_TUNNEL_MAIN_LABEL='it'\\''s ok'

를 bash는 ``it's ok``로, 옛 최소 파서는 ``it'\\''s ok``로 읽었다.

형식은 lib/vt_env.sh가 정의한다 — 홑따옴표는 리터럴, 큰따옴표/무따옴표는 ``${VAR}``
확장, 명령 치환 등 실행 구문은 미지원. 여기서는 같은 규칙을 그대로 구현한다.

``${VAR}`` 확장을 여기서도 해야 하는 이유: install.sh가 만드는 설정 파일에
``VT_PYTHON=${VT_DIR}/.venv/bin/python`` 이 들어간다. 예전엔 bash만 이걸 확장하고
Python은 리터럴로 읽어, 같은 파일을 둘이 다르게 봤다.

의존성 없음 — voice 패키지(pynput)를 끌어오지 않아야 clipboard_daemon도 쓸 수 있다.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

DEFAULT_PATH = "~/.vt.env"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT_RE = re.compile(r"^export\s+")


def _lookup(name: str, defined: Mapping[str, str],
            environ: Mapping[str, str]) -> str:
    """확장 참조 순서: 파일에서 앞서 정의된 키 → 프로세스 환경변수 → 빈 문자열."""
    if name in defined:
        return defined[name]
    return environ.get(name, "")


def _expand_at(raw: str, i: int, out: list[str],
               defined: Mapping[str, str], environ: Mapping[str, str]) -> int:
    """raw[i] == '$' 지점에서 확장. 다음 인덱스를 반환."""
    two = raw[i:i + 2]

    # 실행 구문은 형식에 없다 — '$'를 리터럴로 두고 넘어간다
    if two in ("$(", "$["):
        out.append("$")
        return i + 1

    if two == "${":
        j = raw.find("}", i + 2)
        name = raw[i + 2:j] if j != -1 else ""
        if j == -1 or not _KEY_RE.match(name):
            out.append("$")
            return i + 1
        out.append(_lookup(name, defined, environ))
        return j + 1

    j = i + 1
    while j < len(raw) and (raw[j].isalnum() or raw[j] == "_"):
        j += 1
    name = raw[i + 1:j]
    if not _KEY_RE.match(name):
        out.append("$")
        return i + 1
    out.append(_lookup(name, defined, environ))
    return j


def parse_value(raw: str, defined: Optional[Mapping[str, str]] = None,
                environ: Optional[Mapping[str, str]] = None) -> str:
    """값 부분을 lib/vt_env.sh와 동일한 규칙으로 해석."""
    defined = {} if defined is None else defined
    environ = os.environ if environ is None else environ
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "'":
            i += 1
            while i < n and raw[i] != "'":
                out.append(raw[i])
                i += 1
            i += 1
        elif c == '"':
            i += 1
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < n and raw[i + 1] in '"\\$`':
                    out.append(raw[i + 1])
                    i += 2
                    continue
                if raw[i] == "$":
                    i = _expand_at(raw, i, out, defined, environ)
                    continue
                out.append(raw[i])
                i += 1
            i += 1
        elif c == "\\":
            i += 1
            if i < n:
                out.append(raw[i])
                i += 1
        elif c == "$":
            i = _expand_at(raw, i, out, defined, environ)
        elif c in " \t":
            break  # 따옴표 없는 값은 공백에서 끝난다
        else:
            out.append(c)
            i += 1
    return "".join(out)


# 하위 호환 별칭 — 값 하나만 풀고 싶을 때
def unquote(value: str) -> str:
    return parse_value(value)


def split_line(line: str) -> Optional[tuple[str, str]]:
    """한 줄에서 (KEY, RAW값). 형식에 안 맞으면 None."""
    s = line.lstrip()
    if not s or s.startswith("#"):
        return None
    s = _EXPORT_RE.sub("", s)
    if "=" not in s:
        return None
    key, raw = s.split("=", 1)
    key = key.rstrip()
    if not _KEY_RE.match(key):
        return None
    return key, raw


def parse(text: str, environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """파일 내용을 dict로. 같은 키가 여러 번이면 마지막이 이긴다."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parsed = split_line(line)
        if parsed is None:
            continue
        key, raw = parsed
        out[key] = parse_value(raw, out, environ)
    return out


def load(path: Optional[str] = None) -> dict[str, str]:
    """~/.vt.env(또는 VT_CONFIG)를 읽어 dict로. 없거나 못 읽으면 빈 dict."""
    p = path or os.environ.get("VT_CONFIG") or DEFAULT_PATH
    p = os.path.expanduser(p)
    try:
        with open(p, encoding="utf-8") as f:
            return parse(f.read())
    except OSError:
        return {}
    except Exception:
        return {}


def getenv(key: str, default: str = "",
           file_env: Optional[Mapping[str, str]] = None) -> str:
    """환경변수 → ~/.vt.env → default 우선순위.

    bin/vt가 문서화한 우선순위와 같다. file_env를 주면 파일을 다시 읽지 않는다.
    """
    val = os.environ.get(key)
    if val:
        return val
    src = file_env if file_env is not None else load()
    return src.get(key, default)
