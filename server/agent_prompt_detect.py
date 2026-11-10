"""A3 — PTY 출력에서 "승인/입력 대기(waiting)"를 감지한다.

구조는 `auto_responder`를 그대로 빌려온다(세션별 슬라이딩 윈도우 + cooldown).
다른 점은 **히트했을 때 하는 일**이다: auto_responder는 답을 대신 써 넣어
waiting을 *없애고*, 이쪽은 아무것도 쓰지 않고 상태만 `waiting`으로 *알린다*.

    auto_responder : VT_AUTO_TRUST=1 옵트인, 기본 OFF, 프롬프트를 삼킨다
    이 모듈        : 항상 ON, 아무것도 안 쓴다

**상호배제**: 둘은 `routes/pty.py`에서 같은 바이트 스트림을 나란히 먹는 형제라
순서 보장이 없다. `VT_AUTO_TRUST=1`이면 auto_responder가 프롬프트를 먼저
삼켜버려 waiting이 뜰 새가 없는데, 그 짧은 창에 waiting을 띄우면 화면이
깜빡이고 (더 나쁘게) 큐가 그 pane을 막힌 것으로 오해한다. 그래서 auto_responder가
응답한 세션은 **같은 cooldown 창 동안 waiting 판정을 억제**한다.

**해제(exit) 판정** — 이게 A3에서 새로 만드는 핵심이다. 넷 중 아무거나:
    1. exit 패턴이 윈도우에 등장         (`feed`)
    2. 그 pane에 사용자 입력이 들어감     (`on_user_input` — routes/pty.py의 WS 입력 경로)
    3. `pre`/`stop` 훅 수신               (agent_status가 상태 전이로 이미 처리)
    4. TTL 120초                          (agent_status.sweep의 waiting TTL)

오탐이 화면에 영구히 남지 않게 하는 것이 4번의 존재 이유다. 감지가 틀렸어도
2분이면 스스로 풀린다.

패턴은 `server/detect/<agent>.toml`로 외부화한다 — 에이전트 CLI가 업데이트되면
문구가 바뀌므로, 파이썬을 고치고 서버를 재시작하는 대신 TOML을 고친다.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 이하
    tomllib = None  # type: ignore

logger = logging.getLogger(__name__)


def _on_event_loop() -> bool:
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False

DETECT_DIR = Path(os.environ.get("VT_DETECT_DIR", Path(__file__).parent / "detect"))

# Codex ratatui는 승인 overlay를 그릴 때 전체 화면을 다시 그린다. 144×38 실측에서
# 질문과 마지막 선택지 사이 제어 출력이 2KB를 넘어, 예전 창에서는 질문이 먼저
# 밀려난 뒤 y/p/esc를 추출할 수 없었다. 한 화면+ANSI 여유를 담되 오래된 프롬프트가
# 과도하게 남지 않도록 16KiB로 제한한다.
WINDOW_SIZE = 16 * 1024
# 같은 세션에서 상태를 연속으로 뒤집지 않기 위한 최소 간격.
FLAP_GUARD_SEC = 1.0
# `interrupt` 문구가 청크 경계에 걸리거나 단어마다 색상 코드가 끼는 것을 감안한 추가 조회 바이트.
_INTERRUPT_LOOKBACK = 256
# 청크 끝에서 잘린 이스케이프(`\x1b[38;5`, `\x1b]0;title`, `\x1b(`) — 다음 청크와 이어 벗긴다.
_INCOMPLETE_ESC_RE = re.compile(rb"\x1b(?:\[[0-9;?]*[ -/]*|\][^\x07\x1b]*|[()]?)$")
# CLI 판별(프로세스 스캔) 결과를 세션별로 이만큼 재사용한다.
AGENT_CACHE_SEC = 5.0
# 현재 화면이 프롬프트가 아니라고 답한 같은 문구 위치를 다시 화면에 묻기까지의 최소 간격.
# 스트리밍 출력이 계속 오는 동안 같은 인용 문구 때문에 tmux를 반복해 부르지 않는다.
SCREEN_RECHECK_SEC = 2.0

_patterns: Optional[dict] = None

# waiting 유형. 로더가 트리거 우선순위를 만들 때도 쓴다.
KIND_ORDER = ("plan", "approval", "choice", "question")

# 실기기 검증(2.1.0)에서 발견 — 실제 CLI 출력은 커서 이동·색상·줄지우기 코드가
# 옵션 줄 사이사이에 낀다(예: `\xe2\x9d\xaf\r\x1b[3S\x1b[46;2H 1. Yes`). 이걸 안
# 벗기면 캐리지리턴이 줄바꿈으로 오분류돼 숫자 앞에 이스케이프가 남아 옵션
# 정규식이 실패하거나(1번 선택지 누락), `\x1b[K`가 라벨 끝에 그대로 붙는다.
# CSI(`\x1b[...문자`) · OSC(`\x1b]...BEL`) · 문자셋 지정(`\x1b(B` 등) · 기타
# 2바이트 이스케이프를 텍스트에서 제거한 뒤에만 줄 파싱·정규식을 돌린다.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"         # CSI: ESC [ 파라미터(0x30-0x3F: 숫자 ; < = > ?)... 최종바이트
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC: ESC ] ... BEL 또는 ST
    r"|\x1b[()][0-9A-Za-z]"            # 문자셋 지정: ESC ( B / ESC ) 0 등
    r"|\x1b[@-_]"                      # 기타 2바이트 이스케이프
)


# 줄 머리로 가는 절대 커서 이동(`\x1b[19;1H`, `\x1b[H`). tmux가 바뀐 줄만 다시 그릴 때 줄바꿈
# 대신 이걸 쓴다(Claude Code 2.1.289 실측: 선택지 2번과 3번 사이가 `\r\n`이 아니라
# `\x1b[19;1H`) — 그냥 지우면 두 줄이 한 줄로 붙어 옵션 라벨이 `... 3. Yes ... 4. No`까지 먹는다.
_LINE_START_RE = re.compile(r"\x1b\[(?:\d+)?(?:;1)?H")


# 오른쪽으로 n칸 이동(`\x1b[13C`). 칸을 비워 둔 채 건너뛴 자리라 화면에서는 공백이다 —
# 그냥 지우면 두 열이 붙는다(Codex 0.160.0 계획 확인 overlay 3번: `... Plan mode` 와
# `Continue planning ...` 사이가 `\x1b[13X\x1b[13C`로 이어져 `Plan modeContinue`가 됐다).
_CURSOR_FORWARD_RE = re.compile(r"\x1b\[(\d*)C")


def _strip_ansi(text: str) -> str:
    """ANSI 제거 후 줄바꿈을 `\\n` 하나로 통일한다.

    옵션 정규식은 `re.MULTILINE`의 `^`/`$`(오직 `\\n` 경계만 본다)로 줄을
    가르는데, 실제 터미널 출력은 화면 갱신 중 `\\r` 단독(캐리지리턴만, 다음
    줄로 안 넘어가고 같은 줄 덮어쓰기)도 섞어 쓴다. 앞뒤 문맥 없이 통일하지
    않으면 질문 추출(`splitlines()`는 `\\r` 단독도 경계로 본다)과 선택지
    추출(`\\n`만 경계로 봄)이 서로 다른 줄 개수를 세게 된다.
    """
    text = _LINE_START_RE.sub("\n", text)
    text = _CURSOR_FORWARD_RE.sub(lambda m: " " * min(int(m.group(1) or 1), 200), text)
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def load_patterns(force: bool = False) -> dict:
    """`detect/*.toml`을 읽어 {agent: {"enter": [...], "exit": [...]}}.

    파싱 실패한 파일은 **건너뛴다** — 잘못된 패턴 하나가 서버를 죽이면 안 된다.
    결과는 캐시한다(매 출력 청크마다 디스크를 읽을 수는 없다). 파일을 고친 뒤엔
    force=True 또는 서버 재시작.
    """
    global _patterns
    if _patterns is not None and not force:
        return _patterns
    out: dict[str, dict] = {}
    if tomllib is None:
        logger.warning("tomllib 없음(Python 3.11+ 필요) — waiting 감지 비활성")
        _patterns = out
        return out
    if DETECT_DIR.is_dir():
        for path in sorted(DETECT_DIR.glob("*.toml")):
            try:
                data = tomllib.loads(path.read_text())
            except Exception as e:
                logger.warning(f"detect 패턴 파싱 실패 — 건너뜀: {path.name}: {e}")
                continue
            name = str(data.get("name") or path.stem)
            enter = [str(p).encode() for p in (data.get("enter") or []) if str(p)]
            exit_ = [str(p).encode() for p in (data.get("exit") or []) if str(p)]
            # N38(70-mobile.md §2) — 번호 선택지 캡처 정규식. 단일 문자열 또는
            # 문자열 리스트(에이전트마다 형태가 여럿일 수 있어) 둘 다 받는다.
            # 컴파일 실패(정규식 오타)는 이 필드만 무시 — enter/exit는 살아있다.
            raw_options = data.get("options")
            if isinstance(raw_options, str):
                raw_options = [raw_options]
            options: list[re.Pattern] = []
            for pat in (raw_options or []):
                try:
                    options.append(re.compile(str(pat), re.MULTILINE))
                except re.error as e:
                    logger.warning(f"detect options 정규식 무효 — 건너뜀: {path.name}: {e}")
            # `[kinds]` — waiting 유형(kind)별 리터럴. enter와 같은 '리터럴 바이트
            # 문자열' 규칙이고, 여기 적힌 문구도 waiting 진입 신호다(enter 목록에
            # 또 적을 필요 없다). enter 히트는 kind=approval.
            kinds_raw = data.get("kinds") if isinstance(data.get("kinds"), dict) else {}
            kinds: dict[str, list[bytes]] = {}
            for k in KIND_ORDER:
                lits = [str(p).encode() for p in (kinds_raw.get(k) or []) if str(p)]
                if lits:
                    kinds[k] = lits
            for k in kinds_raw:
                if k not in KIND_ORDER:
                    logger.warning(f"detect [kinds] 알 수 없는 kind — 무시: {path.name}: {k}")
            # `[badge]` — 대기로 올리지 않고 done 엔트리에 보조 표시만 붙이는 문구.
            badge_raw = data.get("badge") if isinstance(data.get("badge"), dict) else {}
            badges = {
                str(b): [str(p).encode() for p in (v or []) if str(p)]
                for b, v in badge_raw.items() if isinstance(v, list)
            }
            badges = {b: v for b, v in badges.items() if v}
            # 이 kind가 히트한 동안은 exit를 무시한다(예: Codex 질문 화면은 상태줄에
            # `esc to interrupt`를 계속 그린다).
            ignore_exit_on = {str(k) for k in (data.get("ignore_exit_on") or []) if str(k) in KIND_ORDER}
            prefer_latest_kind = data.get("prefer_latest_kind") is True
            # `interrupt` — 사용자가 승인을 거절하거나 ESC로 작업을 끊으면 CLI는 훅(Stop)을
            # 내지 않고 이 문구만 화면에 남긴다. 훅이 만든 waiting/working 엔트리를 풀 유일한
            # 신호라 별도 키로 둔다(waiting 진입 신호가 아니다).
            interrupt = [str(p).encode() for p in (data.get("interrupt") or []) if str(p)]
            # `interrupt_stale_after` — 그 문구 **뒤**에 이 정규식이 맞으면 새 중단이 아니라 이미
            # 지나간 화면의 재그림이다. Codex는 중단 줄이 스크롤백에 남은 채 다음 턴이 시작되고,
            # tmux 부분 재그림이 그 줄을 `Working … esc to interrupt` 없이 다시 보내므로(2026-10-06
            # 0.160.0 실측) 문구만으로는 작업 중 엔트리를 done으로 잘못 끝낸다. 중단 직후 화면은
            # 문구 뒤에 빈 입력줄밖에 없고, 지나간 화면은 그 뒤에 새 사용자 입력/응답 줄이 있다.
            stale_raw = data.get("interrupt_stale_after")
            interrupt_stale = None
            if isinstance(stale_raw, str) and stale_raw:
                try:
                    interrupt_stale = re.compile(stale_raw, re.MULTILINE)
                except re.error as e:
                    logger.warning(f"detect interrupt_stale_after 정규식 무효 — 건너뜀: {path.name}: {e}")
            # 트리거 순서 = 우선순위(앞이 우선, 같으면 최신 위치). 구체적인 것 먼저:
            # plan -> approval -> enter(=approval) -> choice -> question. choice의
            # 하단 안내줄(`Enter to select`)은 다른 화면에도 있을 수 있어 뒤에 둔다.
            triggers: list[tuple[bytes, str]] = []
            for k in ("plan", "approval"):
                triggers += [(lit, k) for lit in kinds.get(k, [])]
            triggers += [(lit, "approval") for lit in enter]
            for k in ("choice", "question"):
                triggers += [(lit, k) for lit in kinds.get(k, [])]
            if triggers or exit_ or badges or interrupt:
                out[name] = {
                    "interrupt": interrupt, "interrupt_stale": interrupt_stale,
                    "enter": [t[0] for t in triggers],
                    "enter_kinds": [t[1] for t in triggers],
                    "exit": exit_, "options": options,
                    "kinds": kinds, "badge": badges, "ignore_exit_on": ignore_exit_on,
                    "prefer_latest_kind": prefer_latest_kind,
                }
    _patterns = out
    return out


def _locate_prompt(pats: dict, joined: bytes) -> tuple[Optional[dict], Optional[bytes], int]:
    """가장 구체적인 최신 enter 패턴의 spec·리터럴·위치를 반환한다."""
    spec, pattern, pos, _ = _locate_prompt_kind(pats, joined)
    return spec, pattern, pos


def _locate_prompt_kind(pats: dict, joined: bytes
                        ) -> tuple[Optional[dict], Optional[bytes], int, Optional[str]]:
    """`_locate_prompt` + 히트한 패턴의 kind. `pats`가 한 CLI의 spec만 담으면
    다른 CLI 문구는 애초에 비교 대상이 아니다(kind 판별은 그 CLI의 spec으로만)."""
    prompt_spec: Optional[dict] = None
    prompt_pos = -1
    prompt_priority = 1_000_000
    prompt_pattern: Optional[bytes] = None
    prompt_kind: Optional[str] = None
    hits: list[tuple[int, int, dict, bytes, str]] = []
    for spec in pats.values():
        kinds = spec.get("enter_kinds") or []
        for priority, pattern in enumerate(spec["enter"]):
            pos = joined.rfind(pattern)
            if pos >= 0:
                hits.append((pos, priority, spec, pattern,
                             kinds[priority] if priority < len(kinds) else "approval"))
            if pos >= 0 and (priority < prompt_priority
                             or (priority == prompt_priority and pos > prompt_pos)):
                prompt_priority = priority
                prompt_pos = pos
                prompt_pattern = pattern
                prompt_spec = spec
                prompt_kind = kinds[priority] if priority < len(kinds) else "approval"
    # Codex 화면의 종류별 제목은 서로 겹치지 않는다. 외부 tmux 입력은 이력 창을
    # 비우지 않으므로 이전 plan/approval보다 새 choice의 위치를 먼저 본다. 같은
    # 종류 안에서는 구체적인 제목을 하단 안내 문구보다 우선한다.
    if hits and prompt_spec and prompt_spec.get("prefer_latest_kind"):
        latest_kind = max(hits, key=lambda hit: hit[0])[4]
        relevant = [hit for hit in hits if hit[4] == latest_kind]
        pos, _, prompt_spec, prompt_pattern, prompt_kind = min(
            relevant, key=lambda hit: (hit[1], -hit[0]))
        prompt_pos = pos
    return prompt_spec, prompt_pattern, prompt_pos, prompt_kind


# 푸터형 문구(choice: `Enter to select ·` 안내줄)는 선택지 **아래**에 그려진다. 그 줄 위쪽에서
# 마지막 선택지 묶음까지 허용하는 최대 줄 간격(선택지 설명줄·구분선·빈 줄 몫).
_FOOTER_GAP_LINES = 10


def _option_matches(regex: "re.Pattern", lines: list[str]) -> list[tuple[int, str, str]]:
    """줄마다 옵션 정규식을 돌려 (줄 번호, 키, 라벨)을 순서대로 모은다.

    기존 계약 그대로 — 단축키 이름 그룹(`key`/`label`)이 있으면 그것, 없으면 (번호, 라벨) 2튜플.
    그룹이 둘 미만인 정규식은 선택지로 보지 않는다.
    """
    if regex.groups < 2:
        return []
    named = "key" in regex.groupindex and "label" in regex.groupindex
    out: list[tuple[int, str, str]] = []
    for idx, line in enumerate(lines):
        m = regex.search(line)
        if m is None:
            continue
        key, label = (m.group("key"), m.group("label")) if named else (m.group(1), m.group(2))
        out.append((idx, str(key).strip(), str(label).strip()))
    return out


def _numbered_runs(matches: list[tuple[int, str, str]]) -> list[list[tuple[int, str, str]]]:
    """번호가 1씩 늘어나는 묶음으로 가른다(`1,2,3` / `1..6` / `1,2` 는 각각 다른 묶음).

    출력 이력에는 선택지 아닌 번호 목록(앞선 도구 출력·6단계 계획)도 섞여 있다. 같은 대화상자는
    번호가 이어서 올라가고, 새 목록은 1부터 다시 시작하거나 번호가 끊긴다.
    """
    runs: list[list[tuple[int, str, str]]] = []
    for m in matches:
        if runs and int(m[1]) == int(runs[-1][-1][1]) + 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


def _same_dialog(chosen: dict[str, str], run: list[tuple[int, str, str]]) -> bool:
    """`run`이 `chosen`과 같은 대화상자의 앞선 그림인가 — 겹치는 번호의 라벨이 모두 같다.

    tmux는 바뀐 줄만 다시 보내므로(방향키로 ❯가 옮겨 가면 1·2번 줄만 다시 온다) 가장 뒤 묶음이
    `1,2`뿐일 수 있다. 앞선 `1,2,3`이 같은 라벨이면 한 대화상자라 3번을 되살린다. 앞선 번호 목록
    (계획 4·5·6번)은 라벨이 달라 여기서 끊긴다.
    """
    overlap = [m for m in run if m[1] in chosen]
    return bool(overlap) and all(chosen[m[1]] == m[2] for m in overlap)


def _pick_numbered(runs: list[list[tuple[int, str, str]]], hit_line: int, footer: bool
                   ) -> list[tuple[int, str, str]]:
    """프롬프트 줄 기준으로 진짜 대화상자의 선택지만 고른다.

    - 프롬프트 줄(제목)이나 그 **아래**에 있는 마지막 묶음이 선택지다. 제목 위의 번호 목록은 앞선 출력이다.
    - 푸터형(choice) 문구는 선택지 아래에 있으므로, 아래에 묶음이 없으면 푸터 바로 위 묶음을 쓴다.
    - 선택한 묶음 앞에 같은 대화상자의 부분 재그림 앞 그림이 있으면 거기서 빠진 번호를 채운다.
    """
    below = [i for i, r in enumerate(runs) if r[-1][0] >= hit_line]
    if below:
        pick = below[-1]
    elif footer:
        above = [i for i, r in enumerate(runs) if hit_line - r[-1][0] <= _FOOTER_GAP_LINES]
        if not above:
            return []
        pick = above[-1]
    else:
        return []
    chosen = {m[1]: m for m in runs[pick]}
    labels = {k: v[2] for k, v in chosen.items()}
    for run in reversed(runs[:pick]):
        if not _same_dialog(labels, run):
            break
        for m in run:
            if m[1] not in chosen:
                chosen[m[1]] = m
                labels[m[1]] = m[2]
    return sorted(chosen.values(), key=lambda m: int(m[1]))


def _extract_prompt(pats: dict, joined: bytes
                    ) -> tuple[Optional[str], Optional[list], Optional[str]]:
    """enter 패턴이 히트한 윈도우에서 질문 1줄과 번호 선택지를 뽑는다.

    질문: 히트한 enter 문자열을 **마지막으로** 담은 실제 줄(더 구체적인 문구를 위해 리터럴이
    아니라 그 줄 전체를 쓴다) — 못 찾으면 리터럴 그대로.
    선택지: `options` 정규식을 줄마다 돌려 (번호, 라벨)을 모은 뒤, 프롬프트 줄 기준으로 진짜
    대화상자의 묶음만 쓴다(`_pick_numbered`). 윈도우는 화면이 아니라 출력 이력이라 앞선 번호 목록
    (도구 출력·계획 단계)이 함께 들어 있다 — 전부 모으면 그것이 가짜 버튼이 된다.
    kind: 히트한 패턴의 waiting 유형(`approval|choice|plan|question`).
    디코딩 실패(바이너리 잡음 등)는 조용히 빈 결과로 — waiting 판정 자체는
    이미 끝났으므로 여기서 실패해도 상태 전이는 막지 않는다.
    """
    try:
        text = _strip_ansi(joined.decode("utf-8", errors="ignore"))
    except Exception:
        return None, None, None

    question: Optional[str] = None
    # 각 detect 파일은 구체적인 질문을 먼저, 짧은 폴백 문구를 뒤에 둔다.
    # Codex 선택지의 "don't ask again"이 Claude의 폴백 enter와 겹치므로,
    # 더 앞에 선언된 패턴을 우선하고 같은 우선순위에서만 최신 위치를 고른다.
    encoded = text.encode("utf-8")
    prompt_spec, prompt_pattern, prompt_pos, kind = _locate_prompt_kind(pats, encoded)
    latest_only = bool(prompt_spec and prompt_spec.get("prefer_latest_kind") and prompt_pos >= 0)
    if latest_only:
        # 이전 화면의 선택지가 다음 화면으로 넘어오지 않게 현재 제목 줄부터 읽는다.
        line_start = encoded.rfind(b"\n", 0, prompt_pos) + 1
        text = encoded[line_start:].decode("utf-8", errors="ignore")
        hit_line = 0
    else:
        hit_line = encoded.count(b"\n", 0, max(prompt_pos, 0)) if prompt_pos >= 0 else 0
    lines = text.split("\n")
    if prompt_pattern is not None:
        needle = prompt_pattern.decode("utf-8", errors="ignore")
        if 0 <= hit_line < len(lines) and needle in lines[hit_line]:
            question = lines[hit_line].strip()
        else:
            question = needle

    options: Optional[list] = None
    if prompt_spec:
        for regex in prompt_spec.get("options") or []:
            matches = _option_matches(regex, lines)
            if not matches:
                continue
            if all(m[1].isdigit() for m in matches):
                picked = _pick_numbered(_numbered_runs(matches), hit_line,
                                        footer=kind == "choice" and not latest_only)
            else:
                # 단축키 키(`(y)`/`(p)`/`esc`)는 번호 순서가 없다 — 프롬프트 줄 이후만 쓴다.
                picked = [m for m in matches if m[0] >= hit_line]
            if not picked:
                continue
            options = []
            seen_keys: dict[str, int] = {}
            for _, key, label in picked:
                entry = {"key": key, "label": label}
                # 전체화면 TUI는 같은 대화상자를 다시 그린다 — 같은 키는 처음 자리에 두고
                # 가장 최근 라벨로 덮는다.
                if key in seen_keys:
                    options[seen_keys[key]] = entry
                else:
                    seen_keys[key] = len(options)
                    options.append(entry)
            options = options[:6]  # 화면에 6개 넘게 그릴 일은 없다 — 방어적 상한
            break

    return question, options, kind


def _spec_for_agent(pats: dict, agent: Optional[str]) -> dict:
    """detector가 판별한 CLI의 spec만 담은 dict. 모르는 CLI(None)면 전체 그대로
    (옛 동작) — 판별된 CLI에 toml이 없으면 빈 dict(다른 CLI 문구로 오탐 금지)."""
    if not agent:
        return pats
    return {agent: pats[agent]} if agent in pats else {}


# 화면 확인에서 읽는 마지막 줄 수. Claude의 선택지 UI(질문 + 선택지 4개 + 안내줄)는 이 안에 들어온다.
SCREEN_TAIL_LINES = 14


def screen_state(agent: Optional[str], text: str) -> dict:
    """지금 **화면 한 장**(`tmux capture-pane`의 보이는 영역)이 무엇을 보여 주는가.

    훅이 알린 상태가 아직 맞는지 다시 확인하는 데 쓴다(agent_status.sweep의 만료 직전).
    PTY 스트림 감지(`PromptDetector`)는 브라우저가 그 세션을 열고 있을 때만 돌지만, 이건
    tmux 화면을 직접 읽으므로 아무도 보고 있지 않아도 된다. 같은 `detect/*.toml`을 쓴다.

    반환: {"prompt": 사람의 답을 기다리는 화면이 떠 있나, "kind": 그 종류 또는 None,
          "busy": 작업 중 표시(exit 패턴, 예: `esc to interrupt`)가 보이나}.
    프롬프트 문구 뒤에 작업 중 표시가 나오면 그 프롬프트는 이미 지나간 것이라 prompt=False다.
    """
    # 질문·승인 UI는 화면 **맨 아래**에 붙는다. 위쪽 대화 내용이 그 안내 문구를 인용하고 있을 수 있어
    # (이 코드를 고치는 대화처럼) 마지막 몇 줄만 본다 — 아니면 지나간 인용을 질문으로 오인한다.
    lines = _strip_ansi(text or "").rstrip().split("\n")
    joined = "\n".join(lines[-SCREEN_TAIL_LINES:]).encode("utf-8", errors="replace")
    spec = _spec_for_agent(load_patterns(), agent)
    if not spec:
        return {"prompt": False, "kind": None, "busy": False}
    prompt_spec, _, pos, kind = _locate_prompt_kind(spec, joined)
    exit_pos = -1
    for sp in spec.values():
        for pattern in sp.get("exit") or []:
            exit_pos = max(exit_pos, joined.rfind(pattern))
    # Codex 질문 화면은 맨 아래 안내줄 한 줄에 `enter to submit answer | esc to interrupt`를 같이 그린다
    # (`ignore_exit_on`). 작업 중 표시가 **프롬프트 문구와 같은 줄**에 있을 때만 지나간 프롬프트의 증거로
    # 쓰지 않는다 — 인용된 문구 아래의 `Working (… esc to interrupt)` 줄은 여전히 작업 중이라는 뜻이다.
    ignore = False
    if pos >= 0 and exit_pos > pos and prompt_spec is not None \
            and kind in prompt_spec.get("ignore_exit_on", ()):
        ls = joined.rfind(b"\n", 0, exit_pos) + 1
        le = joined.find(b"\n", exit_pos)
        line = joined[ls:le if le >= 0 else len(joined)]
        ignore = any(lit in line for lit in prompt_spec.get("kinds", {}).get(kind, ()))
    prompt = pos >= 0 and (ignore or exit_pos <= pos)
    return {"prompt": prompt, "kind": kind if prompt else None, "busy": exit_pos >= 0}


class PromptDetector:
    """세션별 출력 윈도우 + enter/exit 패턴 매처.

    on_change(session_id, waiting: bool)로 상태 변화를 알린다. 이미 waiting이어도
    질문/선택지가 뒤 청크에서 새로 완성되면 한 번 더 알린다. 실제 TUI는 질문과
    옵션을 여러 write로 나눠 그리므로, 이 갱신이 없으면 훅이 먼저 만든 waiting
    엔트리의 `options`가 영원히 None으로 남는다. 같은 메타데이터의 단순 재렌더는
    알리지 않아 WS 브로드캐스트 폭주를 막는다.
    """

    def __init__(self, on_change: Callable[[str, bool], None],
                 agent_for: Optional[Callable[[str], Optional[str]]] = None,
                 on_badge: Optional[Callable[[str, Optional[str]], None]] = None,
                 on_interrupt: Optional[Callable[[str], None]] = None,
                 screen_for: Optional[Callable[[str, Optional[str]], Optional[dict]]] = None):
        self._on_change = on_change
        # (session_id, agent) -> `screen_state` 결과 또는 None(읽지 못함). 있으면 waiting으로 **올리기 전에**
        # 지금 화면 마지막 줄들이 정말 프롬프트인지 묻는다(`_screen_ok`). 없으면 출력 이력만 본다.
        self._screen_for = screen_for
        self._total: dict[str, int] = {}
        self._screen_checks: dict[str, tuple[int, Optional[bool], float]] = {}
        self._screen_pending: set[str] = set()
        self._on_interrupt = on_interrupt
        # session_id -> 그 pane에서 도는 CLI 이름(없으면 None). kind 패턴은 이 CLI의
        # spec으로만 매칭한다 — 서로 다른 CLI가 같은 문구(`Do you want to proceed?`)를
        # 쓰기 때문이다. None을 돌려주거나 resolver가 없으면 옛 동작(전 spec 매칭).
        self._agent_for = agent_for
        self._on_badge = on_badge
        self._agent_cache: dict[str, tuple[Optional[str], float]] = {}
        self._kinds: dict[str, Optional[str]] = {}
        self._badges: dict[str, str] = {}
        # 실기기 검증(2.1.0)에서 발견 — 예전엔 `deque(maxlen=4)`로 **청크 개수**를
        # 제한했다. 실제 터미널은 커서 깜빡임·tmux 상태줄 자동 갱신 같은 잡음이
        # 초 단위로 끼어들어 한 프롬프트 렌더가 4개 넘는 write로 쪼개지는 일이
        # 흔하다 — 그러면 청크 개수 상한이 옵션 1번이 담긴 앞쪽 청크를 (바이트
        # 창 안에 여전히 들어가는데도) 밀어내 버려서, 선택지가 레이스 컨디션으로
        # 들쭉날쭉 사라졌다. bytearray로 바꿔 **바이트 수**만으로 자른다.
        self._windows: dict[str, bytearray] = {}
        # 청크 경계 처리 상태(잘린 이스케이프·UTF-8) — 윈도우 자체는 `_windows`다.
        self._pending: dict[str, bytes] = {}
        self._decoders: dict[str, object] = {}
        self._waiting: dict[str, bool] = {}
        self._changed_at: dict[str, float] = {}
        # 승인 질문을 실제로 낸 CLI의 패턴 spec. 서로 다른 CLI가 같은 문구를
        # 쓸 수 있으므로(예: 한 CLI의 상태줄 문구가 다른 CLI의 exit와 같음), 활성 spec의
        # exit만 적용해야 한다.
        self._active_specs: dict[str, dict] = {}
        # N38 — 마지막으로 감지된 질문/선택지. waiting=False가 되면 지운다
        # (더 이상 답할 대상이 없다).
        self._prompts: dict[str, tuple[Optional[str], Optional[list]]] = {}

    def _agent(self, session_id: str) -> Optional[str]:
        """resolver 결과를 짧게 캐시한다 — 프롬프트가 떠 있는 동안 청크마다 부르면
        tmux/ps를 매번 돌리게 된다. 실패는 None(=전 spec 매칭 폴백)."""
        if self._agent_for is None:
            return None
        now = time.monotonic()
        hit = self._agent_cache.get(session_id)
        if hit is not None and now - hit[1] < AGENT_CACHE_SEC:
            return hit[0]
        # PTY 출력 콜백은 이벤트 루프 위에서 돈다. 판별은 tmux list-panes + ps(최대 3초)라 여기서 직접 부르면
        # 그동안 서버 전체가 멈춘다 — 직전 값(없으면 None = 전 spec 폴백)으로 답하고 `proc` 풀에서 갱신한다.
        if _on_event_loop():
            self._refresh_async(session_id)
            return hit[0] if hit is not None else None
        try:
            agent = self._agent_for(session_id)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[waiting] CLI 판별 실패: {e}")
            agent = None
        self._agent_cache[session_id] = (agent, now)
        return agent

    def _refresh_async(self, session_id: str) -> None:
        pending = self.__dict__.setdefault("_agent_pending", set())
        if session_id in pending:
            return
        import asyncio
        from resilience import pools

        async def _run():
            try:
                agent = await pools.run("proc", self._agent_for, session_id, op="agent_for_pty",
                                        key=session_id, timeout=5)
            except Exception as e:  # noqa: BLE001 — 판별 실패는 None(전 spec 폴백)
                logger.debug(f"[waiting] CLI 판별 실패: {e}")
                agent = None
            finally:
                pending.discard(session_id)
            self._agent_cache[session_id] = (agent, time.monotonic())
        try:
            asyncio.get_running_loop().create_task(_run())
            pending.add(session_id)
        except RuntimeError:
            pass

    def _scoped(self, session_id: str, pats: dict) -> dict:
        if self._agent_for is None:
            return pats
        return _spec_for_agent(pats, self._agent(session_id))

    def _check_badge(self, session_id: str, pats: dict, joined: bytes) -> None:
        """대기로 올리지 않는 보조 표시(`[badge]`). 그 CLI의 spec으로만 본다."""
        if self._on_badge is None:
            return
        found: Optional[str] = None
        for spec in pats.values():
            for badge, lits in (spec.get("badge") or {}).items():
                if any(lit in joined for lit in lits):
                    found = badge
        if found is not None and self._agent_for is not None:
            scoped = self._scoped(session_id, pats)
            if not any(found in (spec.get("badge") or {}) and
                       any(lit in joined for lit in spec["badge"][found])
                       for spec in scoped.values()):
                found = None
        if found is not None and self._badges.get(session_id) != found:
            self._badges[session_id] = found
            self._notify_badge(session_id, found)

    def _notify_badge(self, session_id: str, badge: Optional[str]) -> None:
        try:
            if self._on_badge:
                self._on_badge(session_id, badge)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[badge] 통지 실패: {e}")

    def _check_interrupt(self, session_id: str, pats: dict, clean: bytes, fresh: int) -> bool:
        """이번 청크 끝에서 `interrupt` 문구가 가장 마지막 신호로 그려졌는가.

        `clean`은 ANSI를 벗긴 윈도우다(아래 `feed` 참고). 조건: (1) 이번 청크가 더한 꼬리
        구간(경계에 걸친 문구를 잡을 만큼 `_INTERRUPT_LOOKBACK` 바이트 포함)에 있다 — 윈도우에
        남은 옛 문구를 되풀이해 세지 않는다. (2) 그 뒤에 프롬프트 문구(enter)나 작업 중
        상태줄(`esc to interrupt`)이 다시 그려지지 않았다 — 그랬다면 이미 지나간 화면이다.
        """
        if self._on_interrupt is None:
            return False
        specs = [sp for sp in self._scoped(session_id, pats).values() if sp.get("interrupt")]
        if not specs:
            return False
        start = max(0, len(clean) - fresh - _INTERRUPT_LOOKBACK)
        for spec in specs:
            for lit in spec["interrupt"]:
                at = clean.rfind(lit, start)
                if at < 0:
                    continue
                after = clean[at:]
                if any(x and x in after for x in list(spec.get("exit", [])) + list(spec.get("enter", []))):
                    continue
                stale = spec.get("interrupt_stale")
                if stale is not None and stale.search(
                        after[len(lit):].decode("utf-8", errors="ignore")):
                    continue
                try:
                    self._on_interrupt(session_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[interrupt] 통지 실패: {e}")
                return True
        return False

    def _append_clean(self, session_id: str, data: bytes) -> int:
        """ANSI를 벗긴 윈도우에 `data`를 더하고 이번에 더해진 바이트 수를 돌려준다.

        실제 TUI는 단어 사이마다 색상 코드(`\\x1b[38;5;246m`)를 끼우고 `⎿` 뒤에 NBSP를 쓰며
        줄 사이를 `\\r\\n`이 아니라 절대 커서 이동으로 잇는다 — 바이트 리터럴 매칭은 그 화면을
        놓친다(agy 1.2.14 선택 화면 안내줄, Claude Code 2.1.289 `Interrupted ·` 줄 실측).
        청크 끝에 잘린 이스케이프와 UTF-8 문자는 다음 청크로 넘겨 반쪽이 남지 않게 한다.
        """
        pend = self._pending.get(session_id, b"") + data
        m = _INCOMPLETE_ESC_RE.search(pend)
        if m and len(pend) - m.start() <= 64:
            self._pending[session_id] = pend[m.start():]
            pend = pend[:m.start()]
        else:
            self._pending.pop(session_id, None)
        dec = self._decoders.get(session_id)
        if dec is None:
            dec = self._decoders[session_id] = codecs.getincrementaldecoder("utf-8")("ignore")
        text = _strip_ansi(dec.decode(pend)).replace("\u00a0", " ")
        add = text.encode("utf-8")
        win = self._windows.get(session_id)
        if win is None:
            win = self._windows[session_id] = bytearray()
        win.extend(add)
        self._total[session_id] = self._total.get(session_id, 0) + len(add)
        if len(win) > WINDOW_SIZE:
            del win[: len(win) - WINDOW_SIZE]
        return len(add)

    def _drop_window(self, session_id: str) -> None:
        for store in (self._windows, self._pending, self._decoders, self._total,
                      self._screen_checks):
            store.pop(session_id, None)

    # ── 입력 ─────────────────────────────────────────────────────────────
    def feed(self, session_id: str, data: bytes) -> None:
        pats = load_patterns()
        if not pats:
            return
        # 윈도우는 ANSI를 벗긴 텍스트다(`_append_clean`). 줄 구조는 `\n`으로 남아 질문·선택지
        # 추출도 같은 윈도우를 쓴다 — 원본 바이트 윈도우를 따로 두면 이스케이프가 많아 먼저
        # 밀려나 감지(clean)와 추출(raw)의 결과가 갈렸다(agy 선택 화면이 question으로 바뀜).
        fresh = self._append_clean(session_id, data)
        joined = bytes(self._windows[session_id])

        # PTY 윈도우는 현재 화면이 아니라 **출력 이력**이다. Codex는 작업 상태줄
        # (`esc to interrupt`)을 찍은 뒤 같은 2KB 안에서 승인 질문을 그리므로,
        # exit이 한 번이라도 있으면 무조건 우선하던 옛 규칙은 실제 승인 화면을
        # working으로 남겼다. 가장 마지막에 출력된 신호가 현재 상태다.
        scoped, enter_at, exit_at = self._resolve(session_id, pats, joined)

        self._check_badge(session_id, pats, joined)
        interrupted = self._check_interrupt(session_id, pats, joined, fresh)

        if interrupted:
            # 거절/중단 — 화면에 남은 이전 프롬프트 문구로 waiting을 되살리지 않는다.
            self._set(session_id, False)
            self._drop_window(session_id)
        elif exit_at >= 0 and exit_at > enter_at:
            self._set(session_id, False)
        elif enter_at >= 0:
            self._enter(session_id, scoped, joined, enter_at)

    def _resolve(self, session_id: str, pats: dict, joined: bytes) -> tuple[dict, int, int]:
        """윈도우에서 (이 CLI의 spec들, 가장 구체적인 최신 enter 위치, 마지막 exit 위치)."""
        scoped = pats
        prompt_spec, _, enter_at, kind = _locate_prompt_kind(pats, joined)
        if prompt_spec is not None and self._agent_for is not None:
            # 어느 CLI의 문구인지는 detector가 판별한 CLI로만 인정한다.
            scoped = self._scoped(session_id, pats)
            if scoped is not pats:
                prompt_spec, _, enter_at, kind = _locate_prompt_kind(scoped, joined)
        if prompt_spec is not None:
            self._active_specs[session_id] = prompt_spec
        active_spec = prompt_spec or self._active_specs.get(session_id)
        exit_at = max(
            (joined.rfind(p) for p in (active_spec or {}).get("exit", [])),
            default=-1,
        )
        # 그 kind가 떠 있는 동안 exit를 무시하는 CLI(Codex 질문 화면은 상태줄에
        # `esc to interrupt`를 같이 그린다) — 같은 윈도우에서 enter가 우선한다.
        if (enter_at >= 0 and prompt_spec is not None
                and kind in prompt_spec.get("ignore_exit_on", ())):
            exit_at = -1
        return scoped, enter_at, exit_at

    def _enter(self, session_id: str, scoped: dict, joined: bytes, enter_at: int) -> None:
        if _auto_trust_suppressed(session_id):
            logger.debug(f"[waiting] sid={session_id} auto_responder cooldown — 억제")
            return
        if not self._screen_ok(session_id, enter_at):
            return
        question, options, hit_kind = _extract_prompt(scoped, joined)
        prompt = (question, options)
        previous_prompt = self._prompts.get(session_id)
        kind_changed = (previous_prompt is not None
                        and self._kinds.get(session_id) != hit_kind)
        self._kinds[session_id] = hit_kind
        # TUI가 같은 질문 줄을 지우고 다시 그리면 윈도우에는 문구가 두 번
        # 남아 질문 문자열만 달라질 수 있다. 그 재렌더는 통지하지 않고,
        # 인라인 승인에 실제로 필요한 선택지가 바뀔 때만 갱신한다.
        options_changed = (
            previous_prompt is not None and previous_prompt[1] != prompt[1]
        ) or kind_changed
        self._prompts[session_id] = prompt
        self._set(session_id, True, refresh=options_changed)

    # ── 현재 화면 확인 ────────────────────────────────────────────────────
    # 출력 이력에 프롬프트 문구가 있다고 지금 사람의 답을 기다리는 것은 아니다. 작업 중인 에이전트가
    # 문서·설정·테스트를 읽으면 `Enter to select · ↑/↓ to navigate`, `Do you want to proceed?`가 일반
    # 출력으로 그대로 찍힌다. 진짜 프롬프트는 화면 맨 아래에 붙으므로, waiting으로 올리기 **전에**
    # 같은 `screen_state`(tmux 화면의 마지막 SCREEN_TAIL_LINES줄, 뒤에 작업 중 표시가 있으면 지나간 것)
    # 로 한 번 확인한다. 이미 waiting이면 묻지 않는다(선택지 보강·재그림이 매 청크 tmux를 부르지 않게).
    def _screen_ok(self, session_id: str, enter_at: int) -> bool:
        """지금 waiting으로 올려도 되는가. False면 올리지 않는다(비동기 확인 중이거나 화면이 아니라고 답함)."""
        if self._screen_for is None or self._waiting.get(session_id):
            return True
        win = self._windows.get(session_id)
        if win is None:
            return True
        # 이 히트의 스트림 절대 위치 — 새로 그려진 프롬프트(더 뒤 위치)만 새로 확인한다.
        at = self._total.get(session_id, 0) - len(win) + enter_at
        now = time.monotonic()
        seen = self._screen_checks.get(session_id)
        if seen is not None and seen[0] == at:
            if seen[1] is not False:
                return True
            if now - seen[2] < SCREEN_RECHECK_SEC:
                return False
        if session_id in self._screen_pending:
            return False
        agent = self._agent(session_id)
        if _on_event_loop():
            # 화면 읽기는 tmux capture-pane이라 이벤트 루프 위에서 직접 부르지 않는다.
            self._check_screen_async(session_id, at, agent)
            return False
        ok = self._read_screen(session_id, agent)
        self._screen_checks[session_id] = (at, ok, now)
        return ok is not False

    def _read_screen(self, session_id: str, agent: Optional[str]) -> Optional[bool]:
        """True=화면이 프롬프트, False=아님, None=읽지 못함(옛 동작처럼 이력 판정을 따른다)."""
        try:
            shot = self._screen_for(session_id, agent) if self._screen_for else None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[waiting] 화면 확인 실패: {e}")
            return None
        if shot is None:
            return None
        return bool(shot.get("prompt"))

    def _check_screen_async(self, session_id: str, at: int, agent: Optional[str]) -> None:
        import asyncio
        from resilience import pools

        async def _run():
            ok: Optional[bool]
            try:
                ok = await pools.run("proc", self._read_screen, session_id, agent,
                                     op="screen_for_pty", key=session_id, timeout=3)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[waiting] 화면 확인 실패: {e}")
                ok = None
            finally:
                self._screen_pending.discard(session_id)
            if session_id not in self._windows:
                return  # 확인하는 사이 입력/삭제로 윈도우가 비워졌다
            self._screen_checks[session_id] = (at, ok, time.monotonic())
            if ok is not False:
                self._reevaluate(session_id)
        try:
            asyncio.get_running_loop().create_task(_run())
            self._screen_pending.add(session_id)
        except RuntimeError:
            pass

    def _reevaluate(self, session_id: str) -> None:
        """화면 확인이 끝난 뒤 지금 윈도우로 waiting 판정을 이어 간다(그 사이 exit가 왔으면 따른다)."""
        pats = load_patterns()
        win = self._windows.get(session_id)
        if not pats or win is None:
            return
        joined = bytes(win)
        scoped, enter_at, exit_at = self._resolve(session_id, pats, joined)
        if enter_at >= 0 and not (exit_at >= 0 and exit_at > enter_at):
            self._enter(session_id, scoped, joined, enter_at)

    def on_user_input(self, session_id: str) -> None:
        """그 pane에 사용자가 뭔가 입력했다 → 승인 대기는 끝난 것으로 본다.

        해제 판정 4종 중 가장 확실한 신호다(사람이 실제로 답했다). 윈도우도
        비워서 이미 지나간 프롬프트 문자열이 다시 히트하지 않게 한다.
        """
        if self._badges.pop(session_id, None) is not None:
            self._drop_window(session_id)
            self._notify_badge(session_id, None)
        if self._waiting.get(session_id):
            self._drop_window(session_id)
            self._set(session_id, False)

    # ── 상태 ─────────────────────────────────────────────────────────────
    def is_waiting(self, session_id: str) -> bool:
        return bool(self._waiting.get(session_id))

    def get_prompt(self, session_id: str) -> tuple[Optional[str], Optional[list]]:
        """N38 — 마지막으로 감지된 질문·선택지. waiting이 아니면 (None, None)."""
        if not self._waiting.get(session_id):
            return None, None
        return self._prompts.get(session_id, (None, None))

    def get_kind(self, session_id: str) -> Optional[str]:
        """마지막으로 감지된 waiting 유형. waiting이 아니면 None."""
        if not self._waiting.get(session_id):
            return None
        return self._kinds.get(session_id)

    def _set(self, session_id: str, waiting: bool, *, refresh: bool = False) -> None:
        same_state = self._waiting.get(session_id, False) == waiting
        if same_state and not (waiting and refresh):
            return
        now = time.monotonic()
        # 질문/선택지 보강은 상태 flap이 아니다. 질문 청크 직후 옵션 청크가
        # 1초 안에 오는 것이 정상이라 이 경로를 guard로 막으면 다시 유실된다.
        if not same_state and now - self._changed_at.get(session_id, 0.0) < FLAP_GUARD_SEC:
            return
        self._waiting[session_id] = waiting
        if not same_state:
            self._changed_at[session_id] = now
        if not waiting:
            self._prompts.pop(session_id, None)
            self._kinds.pop(session_id, None)
            self._active_specs.pop(session_id, None)
        try:
            self._on_change(session_id, waiting)
        except Exception as e:  # 감지가 서버를 죽이지 않는다
            logger.warning(f"[waiting] 상태 통지 실패: {e}")

    def remove(self, session_id: str) -> None:
        self._drop_window(session_id)
        self._waiting.pop(session_id, None)
        self._changed_at.pop(session_id, None)
        self._prompts.pop(session_id, None)
        self._kinds.pop(session_id, None)
        self._badges.pop(session_id, None)
        self._agent_cache.pop(session_id, None)
        self._active_specs.pop(session_id, None)
        self._screen_pending.discard(session_id)


def _auto_trust_suppressed(session_id: str) -> bool:
    """auto_responder가 방금 이 세션에 답을 써 넣었으면 waiting을 억제한다."""
    import auto_responder

    if not auto_responder.is_enabled():
        return False
    responder = auto_responder._global_responder
    if responder is None:
        return False
    last = responder._last_response_time.get(session_id)
    if last is None:
        return False
    return (time.monotonic() - last) < auto_responder.COOLDOWN_SECONDS


_global_detector: Optional[PromptDetector] = None


def get_global_detector(on_change: Callable[[str, bool], None],
                        agent_for: Optional[Callable[[str], Optional[str]]] = None,
                        on_badge: Optional[Callable[[str, Optional[str]], None]] = None,
                        on_interrupt: Optional[Callable[[str], None]] = None,
                        screen_for: Optional[Callable[[str, Optional[str]], Optional[dict]]] = None
                        ) -> PromptDetector:
    global _global_detector
    if _global_detector is None:
        _global_detector = PromptDetector(on_change, agent_for, on_badge, on_interrupt, screen_for)
    return _global_detector
