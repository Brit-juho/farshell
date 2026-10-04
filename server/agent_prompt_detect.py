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
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"       # CSI: ESC [ 파라미터... 최종바이트
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC: ESC ] ... BEL 또는 ST
    r"|\x1b[()][0-9A-Za-z]"            # 문자셋 지정: ESC ( B / ESC ) 0 등
    r"|\x1b[@-_]"                      # 기타 2바이트 이스케이프
)


# 줄 머리로 가는 절대 커서 이동(`\x1b[19;1H`, `\x1b[H`). tmux가 바뀐 줄만 다시 그릴 때 줄바꿈
# 대신 이걸 쓴다(Claude Code 2.1.289 실측: 선택지 2번과 3번 사이가 `\r\n`이 아니라
# `\x1b[19;1H`) — 그냥 지우면 두 줄이 한 줄로 붙어 옵션 라벨이 `... 3. Yes ... 4. No`까지 먹는다.
_LINE_START_RE = re.compile(r"\x1b\[(?:\d+)?(?:;1)?H")


def _strip_ansi(text: str) -> str:
    """ANSI 제거 후 줄바꿈을 `\\n` 하나로 통일한다.

    옵션 정규식은 `re.MULTILINE`의 `^`/`$`(오직 `\\n` 경계만 본다)로 줄을
    가르는데, 실제 터미널 출력은 화면 갱신 중 `\\r` 단독(캐리지리턴만, 다음
    줄로 안 넘어가고 같은 줄 덮어쓰기)도 섞어 쓴다. 앞뒤 문맥 없이 통일하지
    않으면 질문 추출(`splitlines()`는 `\\r` 단독도 경계로 본다)과 선택지
    추출(`\\n`만 경계로 봄)이 서로 다른 줄 개수를 세게 된다.
    """
    text = _LINE_START_RE.sub("\n", text)
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
            # `interrupt` — 사용자가 승인을 거절하거나 ESC로 작업을 끊으면 CLI는 훅(Stop)을
            # 내지 않고 이 문구만 화면에 남긴다. 훅이 만든 waiting/working 엔트리를 풀 유일한
            # 신호라 별도 키로 둔다(waiting 진입 신호가 아니다).
            interrupt = [str(p).encode() for p in (data.get("interrupt") or []) if str(p)]
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
                    "interrupt": interrupt,
                    "enter": [t[0] for t in triggers],
                    "enter_kinds": [t[1] for t in triggers],
                    "exit": exit_, "options": options,
                    "kinds": kinds, "badge": badges, "ignore_exit_on": ignore_exit_on,
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
    for spec in pats.values():
        kinds = spec.get("enter_kinds") or []
        for priority, pattern in enumerate(spec["enter"]):
            pos = joined.rfind(pattern)
            if pos >= 0 and (priority < prompt_priority
                             or (priority == prompt_priority and pos > prompt_pos)):
                prompt_priority = priority
                prompt_pos = pos
                prompt_pattern = pattern
                prompt_spec = spec
                prompt_kind = kinds[priority] if priority < len(kinds) else "approval"
    return prompt_spec, prompt_pattern, prompt_pos, prompt_kind


def _extract_prompt(pats: dict, joined: bytes
                    ) -> tuple[Optional[str], Optional[list], Optional[str]]:
    """enter 패턴이 히트한 윈도우에서 질문 1줄과 번호 선택지를 뽑는다.

    질문: 히트한 enter 문자열을 담은 실제 줄(더 구체적인 문구를 위해 리터럴이
    아니라 그 줄 전체를 쓴다) — 못 찾으면 리터럴 그대로.
    선택지: `options` 정규식을 텍스트에 돌려 (번호, 라벨) 쌍을 순서대로 모은다.
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
    prompt_spec, prompt_pattern, _, kind = _locate_prompt_kind(pats, text.encode("utf-8"))
    if prompt_pattern is not None:
        needle = prompt_pattern.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if needle in line:
                question = line.strip()
                break
        if question is None:
            question = needle

    options: Optional[list] = None
    if prompt_spec:
        for regex in prompt_spec.get("options") or []:
            found = regex.findall(text)
            if found:
                options = []
                seen_keys: dict[str, int] = {}
                for match in found:
                    # 기존 패턴은 (번호, 라벨), Codex는 (라벨, 실제 단축키)다.
                    # named group이 있으면 우선하고, 없으면 기존 2튜플 계약을 유지한다.
                    if isinstance(match, tuple) and len(match) >= 2:
                        if "key" in regex.groupindex and "label" in regex.groupindex:
                            key = match[regex.groupindex["key"] - 1]
                            label = match[regex.groupindex["label"] - 1]
                        else:
                            key, label = match[0], match[1]
                        key = str(key).strip()
                        entry = {"key": key, "label": str(label).strip()}
                        # 전체화면 TUI는 같은 대화상자를 다시 그린다 — 윈도우에 같은 선택지가
                        # 여러 벌 들어 있으면 버튼이 2~3벌로 불어난다(폰 실측 2026-10-05).
                        # 같은 키는 처음 자리에 두고 가장 최근 라벨로 덮는다.
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
                 on_interrupt: Optional[Callable[[str], None]] = None):
        self._on_change = on_change
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
        try:
            agent = self._agent_for(session_id)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[waiting] CLI 판별 실패: {e}")
            agent = None
        self._agent_cache[session_id] = (agent, now)
        return agent

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
        if len(win) > WINDOW_SIZE:
            del win[: len(win) - WINDOW_SIZE]
        return len(add)

    def _drop_window(self, session_id: str) -> None:
        for store in (self._windows, self._pending, self._decoders):
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

        self._check_badge(session_id, pats, joined)
        interrupted = self._check_interrupt(session_id, pats, joined, fresh)

        if interrupted:
            # 거절/중단 — 화면에 남은 이전 프롬프트 문구로 waiting을 되살리지 않는다.
            self._set(session_id, False)
            self._drop_window(session_id)
        elif exit_at >= 0 and exit_at > enter_at:
            self._set(session_id, False)
        elif enter_at >= 0:
            if _auto_trust_suppressed(session_id):
                logger.debug(f"[waiting] sid={session_id} auto_responder cooldown — 억제")
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
                        on_interrupt: Optional[Callable[[str], None]] = None
                        ) -> PromptDetector:
    global _global_detector
    if _global_detector is None:
        _global_detector = PromptDetector(on_change, agent_for, on_badge, on_interrupt)
    return _global_detector
