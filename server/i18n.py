"""화면 언어(ko/en) — 서버 하나에 값 하나(`VT_LANG`).

결정 순서: ~/.vt.env의 VT_LANG → 프로세스 환경 VT_LANG → OS 로캘($LC_ALL/$LC_MESSAGES/$LANG)
→ 'en'. **설정 파일이 환경보다 먼저다** — 셸·tmux에 남은 낡은 export가 설정 화면에서
바꾼 값을 이기면 "바꿨는데 안 바뀐다"가 된다(boundary 키와 같은 이유).

번역 사전은 프런트엔드(`frontend/js/locales/en.js`)에 있다. 서버는 언어 코드만 정하고,
index.html을 내줄 때 `<html lang>`과 사전 스크립트를 끼워 넣는다 — 그래야 모듈이 평가되는
순간 이미 언어가 정해져 있다(비동기로 받아 오면 첫 화면이 한국어로 그려진 뒤 바뀐다).
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import subprocess
from pathlib import Path

import vt_env

LANGS = ("ko", "en")
DEFAULT = "en"

_ROOT = Path(__file__).resolve().parent.parent
_VT_ENV_SH = _ROOT / "lib" / "vt_env.sh"


def normalize(raw: str | None) -> str | None:
    """'ko', 'ko_KR.UTF-8', 'en-US' → 'ko'/'en'. 모르는 값은 None."""
    if not raw:
        return None
    head = raw.strip().lower().replace("-", "_").split(".")[0].split("_")[0]
    if head in LANGS:
        return head
    return None


def os_lang() -> str:
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(key, "")
        if v and v not in ("C", "POSIX"):
            return "ko" if normalize(v) == "ko" else "en"
    return DEFAULT


def current() -> str:
    file_val = normalize(vt_env.load().get("VT_LANG"))
    if file_val:
        return file_val
    env_val = normalize(os.environ.get("VT_LANG"))
    if env_val:
        return env_val
    return os_lang()


def set_lang(lang: str) -> None:
    """~/.vt.env에 VT_LANG을 쓴다. 쓰기는 lib/vt_env.sh가 단일 writer다(AGENTS.md 계약 5)."""
    if lang not in LANGS:
        raise ValueError(f"unsupported lang: {lang}")
    subprocess.run(
        ["bash", "-c", '. "$1" && vt_env_set VT_LANG "$2"', "_", str(_VT_ENV_SH), lang],
        check=True, capture_output=True, timeout=10,
    )
    # 이 프로세스가 물려받은 낡은 값이 다음 판정을 흐리지 않게 맞춰 둔다.
    os.environ["VT_LANG"] = lang


_MARKER = "<!--vt:i18n-->"
_META = re.compile(r'(<meta [^>]*content=")([^"]*)("[^>]*data-i18n-attr="content"[^>]*>)')


def load_dict(path: Path) -> dict:
    """locales/<lang>.js의 JSON 본문. 못 읽으면 빈 dict(원문으로 나간다)."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text[text.index("{", text.index("window.__VT_I18N")): text.rindex("}") + 1])
    except (OSError, ValueError):
        return {}


def render_index(html: str, lang: str, version: str = "", dictionary: dict | None = None) -> str:
    """index.html에 언어를 박는다. ko는 사전이 필요 없다(원문이 한국어).

    링크 미리보기(og:/twitter:)는 크롤러가 JS를 돌리지 않으므로 서버가 직접 바꾼다.
    """
    html = html.replace('<html lang="ko"', f'<html lang="{lang}"', 1)
    if dictionary:
        def _sub(m: re.Match) -> str:
            v = dictionary.get(m.group(2))
            return m.group(1) + (_html.escape(v, quote=True) if isinstance(v, str) else m.group(2)) + m.group(3)
        html = _META.sub(_sub, html)
    tag = ""
    if lang != "ko":
        q = f"?v={version}" if version else ""
        tag = f'<script src="/static/js/locales/{lang}.js{q}"></script>'
    return html.replace(_MARKER, tag, 1)
