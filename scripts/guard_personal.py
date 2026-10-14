#!/usr/bin/env python3
"""개인 값·비밀이 커밋/푸시에 섞이지 않게 막는 가드 (pre-commit · pre-push 훅이 부른다).

이 저장소에서 GitHub 로 공유하는 것은 "누가 클론해도 같은" 전역 설정뿐이다. `.gitignore` 가 1차
방어선이고, 이 스크립트는 그것을 우회하는 경우(`git add -f`, 새로 생긴 도구 디렉터리, 복사해 온
설정 파일)를 2차로 막는다.

검사 대상은 **이번에 올라가는 변경**뿐이다(추가된 줄과 새로 들어온 파일 이름) — 이미 추적 중인
파일의 옛 내용은 `scripts/check_docs.py` 의 개인값 검사가 맡는다. 네 가지를 본다.

1. 경로 — 도구 상태 디렉터리(`.codex/`, `.agents/` …), 인증·키·DB·`.env*` 파일, 그리고
   로컬 전용이어야 할 기록 문서(조사 노트·보고서·계획서, 허용 폴더 밖의 `docs/` 파일).
2. 비밀 — 토큰·키 모양(`ntn_…`, `ghp_…`, `sk-…`, scrypt 해시, 개인키 헤더)과
   `VT_AUTH_TOKEN=<실제 값>` 같은 대입.
3. 내 홈 — 지금 이 머신의 **실제** 사용자 이름·홈 경로. 저장소에는 값을 적지 않고 실행 시점에
   `getpass.getuser()` / `$HOME` 에서 구한다(그래서 이 파일 자체에 개인 값이 없다).
4. 내 단어 — 조직·프로필·기기 이름처럼 저장소에 적을 수 없는 값. 저장소 밖의 로컬 파일
   (`~/.vt-guard-words`, 환경변수 `VT_GUARD_WORDS` 로 경로 변경)에서 줄마다 하나씩 읽는다.
   `#` 로 시작하는 줄은 주석이다. 대소문자를 구분하고 단어 경계로만 맞춘다(`acme` 는 `acmeco` 에
   걸리지 않는다). 출력에는 단어를 다시 싣지 않는다.

사용법:
    guard_personal.py staged            # pre-commit: 인덱스(staged)를 본다
    guard_personal.py push              # pre-push: stdin 의 `<local ref> <local sha> <remote ref> <remote sha>` 를 본다
    guard_personal.py range A..B        # 임의 범위(수동 점검)
    guard_personal.py history [REV]     # 지금까지의 커밋 전체(기본 HEAD)를 훑는다 — 보고용

비상 우회: `FSH_GUARD_SKIP=1`. 이 값을 쓰면 stderr 에 경고를 남긴다. 에이전트는 쓰지 않는다.
종료 코드: 0 통과, 1 차단, 2 사용법/환경 오류.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from typing import Iterable

# ── 1. 경로 ──────────────────────────────────────────────────────────────────

# (정규식, 이유). 정규식은 저장소 루트 기준 경로 전체에 `search` 한다.
FORBIDDEN_PATHS: tuple[tuple[str, str], ...] = (
    (r"(^|/)\.(codex|agents|gemini|cursor|windsurf|continue|opencode|zed|kiro|roo|cline|sourcegraph)(/|$)",
     "에이전트·에디터 도구가 만든 개인 설정 디렉터리"),
    (r"(^|/)\.aider(?!\.conf\.yml$)[^/]*$", "aider 대화·입력·태그 캐시(개인 것)"),
    (r"(^|/)(AGENTS|CLAUDE)\.local\.md$|\.local\.md$", "에이전트 지침의 개인 덮어쓰기본"),
    (r"(^|/)settings\.local\.json$", "도구의 로컬 설정"),
    (r"(^|/)\.claude/(projects|hooks)(/|$)|(^|/)\.claude/settings\.json$", "Claude Code 개인 설정/상태"),
    (r"(^|/)auth\.json$", "인증 정보"),
    (r"(^|/)\.vt\.env$|(^|/)\.env(\.(?!example$)[^/]*)?$", "환경 파일(토큰·비밀번호 해시가 들어간다)"),
    (r"\.(pem|key|p12|pfx|keystore)$|(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$", "개인키·인증서"),
    (r"\.(sqlite3?|db)(-shm|-wal)?$", "로컬 데이터베이스(대화·상태가 들어간다)"),
    (r"(^|/)\.vt(-[^/]*)?(/|$)", "FarShell 런타임 상태"),
    # 기록 문서는 로컬 전용이다(2026-10-14). docs/ref/ 는 폴더 단위로 허용돼 있어 .gitignore 만으로는
    # 거기 둔 파일이 종류와 무관하게 추적된다 — 그래서 이름으로 한 번 더 막는다.
    (r"^(research_notes|reports)/", "조사 노트·보고서는 로컬 전용이다 (docs/study/ 등 무시되는 곳에 둔다)"),
    (r"^docs/(?!(help|ref|guide)/)", "docs/ 에서 추적하는 것은 help/ref/guide 뿐이다"),
    (r"^docs/ref/[^/]*-plan\.md$", "계획서는 로컬 전용이다 (docs/ref/ 에는 현재 동작만 둔다)"),
    (r"^docs/ref/(references|[^/]*reference-logic)\.md$", "다른 제품을 읽은 조사 기록은 로컬 전용이다"),
    (r"^[A-Z0-9]+(_[A-Z0-9]+)*_DESIGN\.md$", "설계 초안은 로컬 전용이다"),
)

# ── 2. 비밀 ──────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(
    r"^(?:<[^>]*>|\$\{?[A-Za-z_]+\}?|x{3,}|\*{3,}|\.{3,}|changeme|change-me|example|your[-_a-z]*|"
    r"secret|token|password|hash|test[-_a-z0-9]*|dummy[-_a-z0-9]*|fake[-_a-z0-9]*|placeholder|redacted)$",
    re.I,
)
_VT_ASSIGN = re.compile(
    r"\b(VT_AUTH_TOKEN|VT_AUTH_SESSION_KEY|VT_AUTH_PASSWORD_HASH|VT_NOTION_TOKEN|VT_TOKEN|VT_VAPID_PRIVATE_KEY)"
    r"\s*[=:]\s*(['\"]?)([^\s'\"#]{12,})\2"
)
# 대입의 오른쪽이 "비밀 리터럴"처럼 보이는가 — 셸 변수·명령 치환·함수 호출·식별자는 코드이지 값이 아니다.
_CODE_CHARS = re.compile(r"[$(){}<>\[\]`\\]")


def _looks_like_secret_literal(v: str) -> bool:
    if _CODE_CHARS.search(v) or _PLACEHOLDER.match(v):
        return False
    # 소문자·밑줄·점만으로 된 이름(os.environ.get, self.token 등)은 식별자다.
    if re.fullmatch(r"[a-z_][a-z0-9_.]*", v) and not re.search(r"\d{4,}", v):
        return False
    return bool(re.search(r"\d", v) or re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) or re.search(r"[@%+/=]", v))

_FAKE_WORDS = re.compile(r"test|fake|dummy|example|sample|placeholder|xxxx", re.I)

SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bntn_[A-Za-z0-9]{20,}"), "Notion 토큰"),
    (re.compile(r"\bgh[opsur]_[A-Za-z0-9]{30,}"), "GitHub 토큰"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"), "GitHub 토큰"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}"), "Slack 토큰"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}"), "API 키(sk-…)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS 액세스 키"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API 키"),
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "개인키"),
    (re.compile(r"scrypt\$\d+\$\d+\$\d+\$[0-9a-f]{16,}\$[0-9a-f]{32,}"), "비밀번호 해시(scrypt)"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
)


def secret_hits(line: str) -> list[str]:
    """한 줄에서 비밀로 보이는 것의 이유 목록. 값 자체는 돌려주지 않는다(출력에 다시 찍히면 안 된다)."""
    reasons = []
    for pat, why in SECRET_PATTERNS:
        for m in pat.finditer(line):
            # 테스트 픽스처의 가짜 값(sk-test…, ghp_fake…)은 비밀이 아니다.
            if _FAKE_WORDS.search(m.group(0)):
                continue
            reasons.append(why)
            break
    for m in _VT_ASSIGN.finditer(line):
        if _looks_like_secret_literal(m.group(3)):
            reasons.append(f"{m.group(1)} 에 실제 값으로 보이는 문자열을 대입")
    return reasons


# ── 3. 내 홈 ─────────────────────────────────────────────────────────────────

def personal_markers(user: str | None = None, home: str | None = None) -> list[str]:
    """이 머신 사용자의 홈 경로 표지. 너무 흔한 이름(root·user 등)은 오탐이 많아 뺀다."""
    out: list[str] = []
    user = user if user is not None else _safe_user()
    home = home if home is not None else os.path.expanduser("~")
    if home and home not in ("/", "/root") and len(home) > 5:
        out.append(home.rstrip("/") + "/")
    if user and user.lower() not in {"root", "user", "me", "x", "you", "example", "admin", "runner", "ubuntu", "vagrant"} and len(user) >= 3:
        out.append(f"/Users/{user}/")
        out.append(f"/home/{user}/")
    seen: set[str] = set()
    return [m for m in out if not (m in seen or seen.add(m))]


def words_path() -> str:
    return os.environ.get("VT_GUARD_WORDS") or os.path.expanduser("~/.vt-guard-words")


def personal_words(path: str | None = None) -> list[str]:
    """저장소에 적을 수 없는 이 사람의 단어(조직·프로필·기기 이름 …). 파일이 없으면 빈 목록이다."""
    p = path if path is not None else words_path()
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read().splitlines()
    except OSError:
        return []
    out: list[str] = []
    for ln in raw:
        w = ln.strip()
        if w and not w.startswith("#") and len(w) >= 3:
            out.append(w)
    return list(dict.fromkeys(out))


def _word_regex(words: list[str]) -> "re.Pattern | None":
    if not words:
        return None
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + alt + r")(?![A-Za-z0-9_])")


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


# ── git 접근 ─────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()[:200]}")
    return r.stdout


def _added_paths_and_lines(diff_args: list[str]) -> tuple[list[str], list[tuple[str, int, str]]]:
    """(새로 들어오거나 바뀐 파일 경로, [(경로, 줄번호, 추가된 줄)])."""
    names = _git("diff", "--name-only", "--diff-filter=ACMR", *diff_args).splitlines()
    patch = _git("diff", "-U0", "--no-color", "--diff-filter=ACMR", *diff_args)
    lines: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
            continue
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@", raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if path:
                lines.append((path, lineno, raw[1:]))
            lineno += 1
    return names, lines


# ── 검사 ─────────────────────────────────────────────────────────────────────

def scan(names: Iterable[str], lines: Iterable[tuple[str, int, str]],
         markers: list[str] | None = None, words: list[str] | None = None) -> list[str]:
    """문제 목록(사람이 읽는 문장). 비밀 값과 개인 단어는 싣지 않는다."""
    markers = personal_markers() if markers is None else markers
    word_rx = _word_regex(personal_words() if words is None else words)
    problems: list[str] = []
    for n in names:
        for rx, why in FORBIDDEN_PATHS:
            if re.search(rx, n):
                problems.append(f"{n}: 올릴 수 없는 경로 — {why}")
                break
        else:
            if word_rx and word_rx.search(n):
                shown = word_rx.sub(lambda m: m.group(0)[0] + "…", n)   # 이름이 곧 단어라 그대로 찍으면 다시 새 나간다
                problems.append(f"{shown}: 파일 이름에 개인 단어가 들어 있다 (~/.vt-guard-words)")
    for path, lineno, text in lines:
        for why in secret_hits(text):
            problems.append(f"{path}:{lineno}: 비밀로 보이는 값 — {why}")
        for mk in markers:
            if mk in text:
                problems.append(f"{path}:{lineno}: 이 머신의 개인 경로({_mask(mk)})가 들어 있다")
                break
        if word_rx and word_rx.search(text):
            problems.append(f"{path}:{lineno}: 개인 단어가 들어 있다 (~/.vt-guard-words) — 중립 값(alice·acme)으로 바꾼다")
    return problems


def _mask(marker: str) -> str:
    # `/Users/<user>/` -> `/Users/<u…>/` — 출력에 이름을 다시 싣지 않는다.
    m = re.match(r"^(/(?:Users|home)/)(.)(.*)(/)$", marker)
    return f"{m.group(1)}{m.group(2)}…{m.group(4)}" if m else marker[:6] + "…"


# ── 모드 ─────────────────────────────────────────────────────────────────────

_ZERO = "0" * 40


def _push_ranges(stdin_text: str) -> list[str]:
    ranges: list[str] = []
    for ln in stdin_text.splitlines():
        parts = ln.split()
        if len(parts) < 4:
            continue
        _lref, lsha, _rref, rsha = parts[:4]
        if lsha == _ZERO:        # 브랜치 삭제
            continue
        if rsha == _ZERO:        # 새 브랜치 — 기본 브랜치 대비
            base = ""
            for cand in ("origin/master", "origin/main"):
                if subprocess.run(["git", "rev-parse", "--verify", "-q", cand], capture_output=True).returncode == 0:
                    base = cand
                    break
            ranges.append(f"{base}..{lsha}" if base else lsha)
        else:
            ranges.append(f"{rsha}..{lsha}")
    return ranges


def run(mode: str, arg: str = "", stdin_text: str = "") -> int:
    if os.environ.get("FSH_GUARD_SKIP") == "1":
        print("⚠ guard_personal: FSH_GUARD_SKIP=1 — 개인 값 검사를 건너뜁니다.", file=sys.stderr)
        return 0
    try:
        if mode == "staged":
            names, lines = _added_paths_and_lines(["--cached"])
            problems = scan(names, lines)
        elif mode == "range":
            names, lines = _added_paths_and_lines([arg])
            problems = scan(names, lines)
        elif mode == "push":
            problems = []
            for rg in _push_ranges(stdin_text):
                if ".." in rg:
                    names, lines = _added_paths_and_lines([rg])
                else:       # 기준 브랜치가 없는 첫 푸시: 전체 커밋을 훑는다
                    names, lines = _history(rg)
                problems += scan(names, lines)
        elif mode == "history":
            names, lines = _history(arg or "HEAD")
            problems = scan(names, lines)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except RuntimeError as e:
        print(f"guard_personal: {e}", file=sys.stderr)
        return 2
    if not problems:
        return 0
    uniq = list(dict.fromkeys(problems))
    print("✗ 개인 값/비밀이 섞여 있어 막았습니다:", file=sys.stderr)
    for p in uniq[:40]:
        print(f"  - {p}", file=sys.stderr)
    if len(uniq) > 40:
        print(f"  … 그 밖에 {len(uniq) - 40}건", file=sys.stderr)
    print("  고치는 법: 그 파일/줄을 빼고(.gitignore 대상이면 `git rm --cached`), 자리표시자(/Users/x, <token>)로 바꾼다.",
          file=sys.stderr)
    print("  GitHub 에는 누가 클론해도 같은 전역 설정만 올린다 — 개인 값은 ~/.vt.env, ~/.claude/CLAUDE.md 에 둔다.",
          file=sys.stderr)
    return 1


def _history(rev: str) -> tuple[list[str], list[tuple[str, int, str]]]:
    """커밋 전체를 훑는다(각 커밋의 추가분). 큰 저장소에서는 느리다 — 보고용."""
    names: list[str] = []
    lines: list[tuple[str, int, str]] = []
    for sha in _git("rev-list", rev).split():
        p = f"{sha}^!"
        try:
            n, l = _added_paths_and_lines([p])
        except RuntimeError:
            continue
        names += n
        lines += l
    return names, lines


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    mode = argv[1]
    arg = argv[2] if len(argv) > 2 else ""
    stdin_text = sys.stdin.read() if mode == "push" else ""
    return run(mode, arg, stdin_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
