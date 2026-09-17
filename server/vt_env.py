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


# ---------------------------------------------------------------------------
# 경계값 — 설정 파일이 환경변수를 이긴다 (2026-09-17)
# ---------------------------------------------------------------------------
#
# 이 파일의 기본 규칙은 "환경변수 > 파일"이고, 포트·경로·인스턴스 격리에는 그게
# 맞다. 하지만 **보안 경계를 정하는 값**에 그 규칙을 쓰면 낡은 사본이 조용히
# 경계를 넓힌다. 실제로 두 번 겪었다(2026-09-17):
#
#   - ~/.vt.env의 VT_BROWSE_ROOTS를 ~/GitHub 로 좁히고 서버를 재시작했는데,
#     fsh를 실행한 셸이 예전 값(=홈 전체)을 export하고 있어 새 서버가 그걸
#     그대로 물려받았다. 코드 뷰어가 공개 터널 너머로 홈을 계속 내보냈다.
#   - VT_AUTH_TOKEN을 재발급했는데 옛 값을 든 에이전트 세션들의 훅이 전부
#     401이 됐다(490건). 아무도 알려주지 않았다.
#
# 그래서 경계값만 우선순위를 뒤집는다. 호출부가 69곳이라 전부 고치는 대신
# **부팅 때 한 번 os.environ을 정규화한다**(apply_boundary_overrides).
#
# VT_CONFIG는 여기 없다 — "어느 파일을 읽을지" 고르는 키라 env가 이겨야 한다.
# 격리 테스트 서버가 그 성질에 의존한다(AGENTS.md).
BOUNDARY_KEYS = (
    "VT_BROWSE_ROOTS",        # 코드 뷰어 열람 경계
    "VT_NETWORK_MODE",        # IP 필터
    "VT_ALLOWED_ORIGINS",     # 교차 출처 허용
    "VT_TRUST_PROXY",         # 프록시 헤더 신뢰 여부
    "VT_AUTH_TOKEN",          # 기계 토큰
    "VT_AUTH_PASSWORD_HASH",  # 웹 로그인 비밀번호 해시
    "VT_AUTH_SESSION_KEY",    # 세션 쿠키 서명키
    "VT_SAFE_MODE",           # 위험 도구 차단
    "VT_DISALLOWED_TOOLS",
    "VT_STATE_DIR",           # 기기 목록·OTP가 사는 곳
    # 레거시 이름도 같은 경계값이다 — 하나만 막으면 옛 이름으로 우회된다.
    "VT_TOKEN",
    "VT_PASSWORD_HASH",
    "VT_SECRET_KEY",
)


def boundary_diff(file_env: Optional[Mapping[str, str]] = None) -> list:
    """환경변수와 파일이 다른 경계값 목록 — [(key, "env")].

    값 자체는 돌려주지 않는다. 토큰·해시가 섞여 있어서 로그·화면에 나가면 안 된다.
    """
    src = file_env if file_env is not None else load()
    out = []
    for key in BOUNDARY_KEYS:
        if key not in src:
            continue
        env_val = os.environ.get(key)
        if env_val is not None and env_val != src[key]:
            out.append((key, "env"))
    return out


def apply_boundary_overrides(file_env: Optional[Mapping[str, str]] = None) -> list:
    """파일에 정의된 경계값으로 os.environ을 덮어쓴다. 바뀐 키 목록을 돌려준다.

    **파일에 없는 키는 건드리지 않는다** — 그 경우엔 기존대로 환경변수가 이긴다
    (일회성 실험을 통째로 막지 않기 위해서다. 파일이 정한 경계만 지킨다).

    ⚠ **호출 시점이 계약이다.** auth 등 일부 모듈은 import 시점에 값을 읽으므로,
    이 함수는 그 import들보다 **먼저** 불려야 한다. server/main.py 상단 참조.
    """
    src = file_env if file_env is not None else load()
    changed = []
    for key in BOUNDARY_KEYS:
        if key not in src:
            continue
        if os.environ.get(key) != src[key]:
            os.environ[key] = src[key]
            changed.append(key)
    return changed


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
