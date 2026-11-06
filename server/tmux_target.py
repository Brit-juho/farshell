"""tmux pane 결정 + 텍스트 주입 — 타깃 결정 규칙의 단일 진실의 원천.

원래 server/voice/tmux_target.py 에 있었으나 여기로 옮겼다. 이유:
voice/config.py 가 `from pynput import keyboard` 를 하기 때문에, voice 패키지를
거치면 **음성 미설치 환경에서 import 자체가 실패한다.** 프롬프트 큐(P4)처럼
음성과 무관한 기능도 같은 타깃 규칙을 써야 하므로 의존성 없는 위치로 끌어냈다.
server/voice/tmux_target.py 는 이 모듈을 재export하는 얇은 껍데기로 남아 있다.

타깃 결정 순서 (음성·큐가 공유한다 — 규칙을 새로 만들지 말 것):
  1. ~/.vt/voice_target 에 lock된 세션의 active pane
  2. lock이 없거나 그 세션이 사라졌으면 → 현재 활성 pane
  3. 그것도 없으면 → 아무 pane
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid

import tmux_runner
import vt_env
from shell_quote import has_control_chars, shell_quote_path  # noqa: F401 — 파일 경로 입력에 쓴다(type_path_to_tmux)

logger = logging.getLogger(__name__)

TMUX_SOCKET = vt_env.getenv("VT_TMUX_SOCKET", "fsh")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]

VOICE_TARGET_LOCK = "~/.vt/voice_target"


def get_active_tmux_pane() -> str | None:
    """현재 활성 tmux pane ID."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def get_any_tmux_pane() -> str | None:
    """아무 tmux 세션의 첫 번째 pane."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        panes = result.stdout.strip().split("\n")
        return panes[0] if panes and panes[0] else None
    except Exception:
        return None


def read_voice_target_lock() -> str | None:
    """~/.vt/voice_target에 lock된 세션 이름."""
    try:
        path = os.path.expanduser(VOICE_TARGET_LOCK)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            target = f.read().strip()
        return target if target else None
    except Exception:
        return None


def pane_label(pane_id: str) -> str | None:
    """pane_id(`%12` 같은 내부 ID) → 사람이 읽는 라벨("dev:0.0").

    V3-G: 녹음이 어디로 갈지 발화 전에 안내할 때 pane_id 그대로는 의미가 없어 필요해졌다.
    """
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "-t", pane_id,
                         "#{session_name}:#{window_index}.#{pane_index}"],
            capture_output=True, text=True, timeout=2,
        )
        label = result.stdout.strip()
        # 존재하지 않는 pane_id를 -t로 넘겨도 tmux는 exit 0 + 빈 변수만 채운 형식
        # 문자열(예: ":.")을 그대로 뱉는다(실측 확인) — session_name이 비어 있으면
        # 애초에 대상이 없었다는 뜻이라 None으로 취급한다.
        return label if label and not label.startswith(":") else None
    except Exception:
        return None


def get_locked_session_pane(session_name: str) -> str | None:
    """lock된 세션의 active pane id."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "-t", session_name, "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def resolve_voice_target_pane() -> tuple[str | None, str]:
    """보낼 pane 결정. (pane_id, mode) 반환.
    mode: "lock:<name>" / "auto" / "none"
    """
    locked = read_voice_target_lock()
    if locked:
        pane = get_locked_session_pane(locked)
        if pane:
            return pane, f"lock:{locked}"
        logger.warning(f"타깃 lock된 세션 '{locked}' 없음 → AUTO 폴백")
    pane = get_active_tmux_pane() or get_any_tmux_pane()
    return pane, ("auto" if pane else "none")


def session_pane(session_name: str) -> str | None:
    """이름으로 지정된 tmux 세션의 pane. 큐 항목이 타깃을 명시할 때 쓴다."""
    return get_locked_session_pane(session_name)


def shell_only_sessions() -> set[str]:
    """모든 pane의 포그라운드가 빈 셸인 tmux 세션 이름. 에이전트가 떠 있지 않은 일반 터미널이다.

    화면 감지(승인 대기)는 이런 세션에서는 의미가 없다 — 셸에 찍힌 다른 CLI의 문구(종료된 claude의
    trust 화면 등)를 질문으로 오인한 채 해제 신호 없이 굳는다."""
    import hook_identity

    shells: set[str] = set()
    busy: set[str] = set()
    for p in tmux_runner.get_all_panes():
        (shells if hook_identity.is_shell(p.command or "") else busy).add(p.session)
    return shells - busy


def session_for_cwd(cwd: str | None) -> str | None:
    """cwd → tmux 세션 이름. Claude Code stop 훅이 어느 세션에서 왔는지 특정할 때 쓴다.

    그리드 뷰가 hook payload의 cwd를 pane_current_path와 매칭해 카드를 특정하는
    것과 동일한 방식(agent_status.py 참고)이다. 같은 cwd로 여러 세션이 떠 있으면
    (둘 다 $HOME 등) 어느 쪽인지 알 수 없으므로, 틀린 세션을 확신 있게 고르는
    대신 None을 돌려준다 — 큐 드레인 쪽에서는 이 경우 세션 미지정 항목만 흘려보낸다.
    """
    if not cwd:
        return None
    matches = {p.session for p in tmux_runner.get_all_panes() if p.path == cwd}
    if len(matches) == 1:
        return next(iter(matches))
    return None


def pane_exists(pane_id: str) -> bool:
    try:
        result = subprocess.run(
            TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        return pane_id in result.stdout.split()
    except Exception:
        return False


def _enter_gap_sec() -> float:
    """붙여넣기와 Enter 사이 간격.

    붙여넣기 묶음과 Enter가 한 입력으로 합쳐지면 Claude Code는 Enter를 줄바꿈으로
    받는다(아래 send_to_tmux 주석). paste-buffer → 대기 → Enter 순서에서
    기본 대기는 200ms다. 환경변수로 조정한다.
    """
    try:
        ms = float(os.environ.get("VT_PASTE_ENTER_GAP_MS", "200"))
    except ValueError:
        ms = 200.0
    return max(0.0, ms) / 1000.0


def _paste_to_pane(pane_id: str, text: str) -> bool:
    """pane에 텍스트를 붙여넣는다(Enter 없음). 줄바꿈을 보존한다.

    `send-keys -l`은 텍스트 속 `\n`을 키 입력으로 넘겨, 여러 줄 지시가 첫 줄에서
    제출돼버렸다. tmux 버퍼를 거쳐 `paste-buffer -p -r`로 넣으면:
      - `-p`: pane이 bracketed paste를 켰으면 마커로 감싼다(Claude Code·Codex·
        bash 5.1+). 마커 안의 줄바꿈은 제출이 아니라 입력창 줄바꿈이다.
      - `-r`: LF를 CR로 바꾸지 않는다(기본 동작은 바꿔서 줄마다 Enter가 된다).
      - `-d`: 붙인 뒤 버퍼를 지운다. 버퍼 이름은 매번 새로 — 동시 투입끼리,
        그리고 사용자의 tmux 버퍼 스택(prefix+])과 섞이지 않는다.
    대상은 세션 이름이 아니라 pane id(`%N`)다 — 그 사이 활성 pane이 바뀌어도
    엉뚱한 칸에 들어가지 않는다. 정리·버전 규칙은 tmux_paste와 같다.
    """
    import paste_prepare
    import tmux_paste

    body = paste_prepare.strip_unsafe_control_chars(text)
    body = paste_prepare.normalize_newlines(body)
    if not body:
        return True
    buf_name = f"fsh-send-{uuid.uuid4().hex[:12]}"
    rc, _, err = tmux_runner.run(["load-buffer", "-b", buf_name, "-"],
                                 input=body.encode("utf-8", "surrogatepass"))
    if rc != 0:
        logger.error(f"tmux load-buffer 실패: {err!r}")
        return False
    args = ["paste-buffer", "-p", "-r", "-d", "-b", buf_name, "-t", pane_id]
    if tmux_paste.supports_paste_sanitize_off(tmux_paste.tmux_version()):
        args.insert(1, "-S")
    rc, _, err = tmux_runner.run(args)
    if rc != 0:
        logger.error(f"tmux paste-buffer 실패({pane_id}): {err!r}")
        tmux_runner.run(["delete-buffer", "-b", buf_name])
        return False
    return True


class SendVetoed(Exception):
    """`send_to_tmux(guard=...)` 의 가드가 입력 직전에 막았다 — 아무것도 입력하지 않았다. `reason` 은 안정적인 사유 코드."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def send_to_tmux(pane_id: str, text: str, preface: str | None = None, guard=None) -> bool:
    """tmux pane에 텍스트를 넣고 Enter를 누른다. 여러 줄도 한 번에 제출된다.

    2026-09-28(실측) — 예전엔 `send-keys -- text Enter` **한 번의 호출**이었다.
    Claude Code는 한꺼번에 몰려 들어온 입력을 붙여넣기로 받고 그 안의 Enter를
    줄바꿈으로 처리해서, 큐·에이전트 메시지가 입력창에 들어가기만 하고 제출되지
    않았다(M1↔M5 실제 Claude 테스트, 같은 조건 5회 중 0회 제출). 그래서 텍스트와
    Enter를 **따로 호출**한다 — 이 원칙은 그대로다.

    2026-10-13 — 텍스트 쪽을 `send-keys -l`에서 붙여넣기(_paste_to_pane)로 바꿨다.
    `-l`은 텍스트 속 줄바꿈을 키 입력으로 보내 여러 줄 지시가 첫 줄에서 잘렸다.
    pane 락(pane_input) 안에서 "붙여넣기 → 대기 → Enter"를 한 덩어리로 보낸다 —
    다른 자동 입력이 사이에 끼면 그쪽 Enter가 이 텍스트를 반쯤 제출한다.

    `preface`(한 줄)가 있으면 붙여넣기 전에 **타이핑**으로 넣는다. Claude Code는 bracketed paste를
    `<pasted_content>`로 감싸 "사용자가 직접 쓴 말이 아님"으로 다루고, 그 안의 지시를 따르지 않을 수
    있다(2026-10-13 위임 E2E: 3회 중 1회 거부). 위임 지시처럼 받는 쪽이 **따라야 하는** 텍스트는
    사용자 턴의 머리말이 "아래 붙여넣은 작업을 하라"고 말하게 한다. 결과 보고처럼 데이터로 읽혀야
    하는 텍스트는 머리말 없이 붙여넣기만 한다.

    `guard`(인자 없는 함수)가 있으면 pane 락을 잡은 **직후, 첫 키를 보내기 전에** 한 번 부른다. 사유 문자열을 돌려주면
    아무것도 입력하지 않고 `SendVetoed(사유)` 를 올린다 — 권한을 입력 직전에 다시 확인하는 자리다(승인~입력 사이의 회수 경합).
    가드가 예외를 내면 입력하지 않는다(닫힘).
    """
    import pane_input

    with pane_input.lock(pane_id):
        if guard is not None:
            veto = guard()
            if veto:
                raise SendVetoed(str(veto))
        if preface:
            line = " ".join(preface.split())
            rc, _, err = tmux_runner.run(["send-keys", "-t", pane_id, "-l", "--", line + " "])
            if rc != 0:
                logger.error(f"tmux send-keys(머리말) 실패({pane_id}): {err!r}")
                return False
            time.sleep(_enter_gap_sec())
        if not _paste_to_pane(pane_id, text):
            return False
        time.sleep(_enter_gap_sec())
        rc, _, err = tmux_runner.run(["send-keys", "-t", pane_id, "Enter"])
        if rc != 0:
            logger.error(f"tmux send-keys Enter 실패({pane_id}): {err!r}")
            return False
        return True


def session_of_pane(pane_id: str) -> str | None:
    """pane id → tmux 세션 이름. 사람 입력 확인(pane_input)이 세션 단위라 필요하다."""
    rc, out, _ = tmux_runner.run(["display-message", "-p", "-t", pane_id, "#{session_name}"])
    if rc != 0:
        return None
    name = out.decode("utf-8", "replace").strip()
    return name or None


def type_path_to_tmux(pane_id: str, path: str) -> bool:
    """`type_to_tmux`로 파일 경로를 입력한다 — 셸 인용을 거친다(`shell_quote_path`).

    제어문자(C0·DEL)가 든 경로는 **입력하지 않고** False를 돌려준다(`has_control_chars`). `send-keys -l`은
    바이트를 그대로 pty에 넣으므로 `^U`·`\r` 같은 바이트는 인용과 상관없이 줄 편집기가 먼저 처리한다 —
    사용자가 Enter를 누르기 전에 명령이 실행될 수 있다. 호출부는 이 결과를 실패로 다루고, 사용자에게 이유를
    보여 줄 곳(라우트)은 먼저 `has_control_chars`로 따로 거른다.
    """
    if has_control_chars(path):
        logger.warning(f"제어문자가 든 경로라 입력하지 않음({pane_id}): {path!r}")
        return False
    return type_to_tmux(pane_id, shell_quote_path(path))


def type_to_tmux(pane_id: str, text: str) -> bool:
    """tmux pane에 텍스트만 타이핑(Enter 없음) — 스니펫 paste 모드(N6)와 같은 규칙.

    파일 경로 삽입(N19)처럼 사용자가 명령 일부로 조합해 넣을 값에 쓴다. Enter를
    자동으로 누르면 아직 완성 안 된 명령을 그대로 실행시켜버릴 수 있어 위험하다.
    pane 락을 잡는다 — 큐 투입의 "붙여넣기 → Enter" 사이에 끼어들면 그 Enter가
    이 텍스트까지 실행시킨다.
    """
    import pane_input

    try:
        with pane_input.lock(pane_id):
            subprocess.run(
                # `-l` — 텍스트 전체가 키 이름(`Enter` 등)과 같으면 tmux가 그 키를 눌러
                # "Enter 없이 타이핑"이라는 약속이 깨진다(send_to_tmux 주석).
                TMUX_BASE + ["send-keys", "-t", pane_id, "-l", "--", text],
                timeout=2, check=True,
            )
        return True
    except Exception as e:
        logger.error(f"tmux send-keys(타이핑) 실패: {e}")
        return False
