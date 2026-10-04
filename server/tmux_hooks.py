"""tmux가 세션 목록 변화를 서버에 직접 알리게 한다(2026-09-30).

왜: 레일·플릿의 세션 목록은 `routes/system.py`의 감시 루프가 tmux를 주기적으로
물어 바뀌었을 때만 push한다. 주기를 기다리지 않게 서버 코드 곳곳에서
`_kick_sessions()`를 손으로 불러 왔는데, 새 경로(화면별 연결·죽은 연결 되살리기)가
생길 때마다 빠뜨렸고, 터미널에서 직접 `tmux new` 한 것처럼 서버를 안 거친 변화는
늘 주기만큼 늦었다. 변화를 가장 정확히 아는 쪽은 tmux 자신이다 — 그래서 tmux
훅이 `POST /api/tmux/kick`을 부르게 하고, 주기는 훅이 없는 변화(실행 중 명령·
현재 폴더)만 맡도록 늘린다.

인증: 기계 토큰(VT_AUTH_TOKEN)은 선택이라(비밀번호만 쓰는 서버에는 없다) 훅이
기댈 수 없다. 대신 **서버 프로세스마다 새로 만드는 비밀값**을 훅 명령에 넣고,
엔드포인트는 일반 인증 대신 이 값만 확인한다(docs/ref/auth.md 예외 표). 값은
디스크에 남지 않고, 같은 사용자만 읽을 수 있는 tmux 소켓의 훅 설정 안에만 있다.
재시작하면 새 값으로 훅을 덮어쓰므로 옛 값은 바로 무효가 된다.

훅은 배열이다 — 사용자가 `set-hook -g session-created ...`로 건 훅([0])을
덮지 않게 우리 몫은 고정 칸([HOOK_INDEX])에만 쓴다.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets

import tmux_runner

logger = logging.getLogger(__name__)

# 세션 목록(이름·창 수·붙은 클라이언트 수)을 바꾸는 사건들. 실행 중 명령·현재
# 폴더 변화에는 훅이 없다 — 그건 감시 루프의 주기와 에이전트 이벤트가 맡는다.
EVENTS = (
    "session-created",
    "session-closed",
    "session-renamed",
    "client-attached",
    "client-detached",
)
HOOK_INDEX = 77

# 터미널 창(탭) 제목을 세션 이름으로 — tmux가 제목을 알려주지 않으면(set-titles off, 기본값)
# Ghostty는 기본 아이콘(👻)만 보여줘서 창이 여러 개일 때 어느 창이 어느 세션인지 구분이 안 된다.
# fsh 전용 서버(-L fsh)에만 걸리고, 사용자의 기본 tmux나 ~/.tmux.conf는 건드리지 않는다.
TITLE_OPTIONS = (("set-titles", "on"), ("set-titles-string", "#S"))
KICK_PATH = "/api/tmux/kick"
HEADER = "X-VT-Kick"

_SECRET = secrets.token_urlsafe(32)


def _port() -> str:
    return (os.environ.get("VT_PORT") or "7777").strip()


def hook_command(port: str | None = None) -> str:
    """훅 한 칸에 넣을 tmux 명령. `-b`로 tmux를 막지 않고, curl은 2초면 포기한다
    (서버가 내려가 있어도 훅이 쌓이지 않는다)."""
    url = f"http://127.0.0.1:{port or _port()}{KICK_PATH}"
    # _SECRET는 [-_A-Za-z0-9]뿐이라 따옴표 안에서 이스케이프가 필요 없다.
    curl = f"curl -s -m 2 -o /dev/null -X POST -H '{HEADER}: {_SECRET}' {url}"
    return f'run-shell -b "{curl}"'


def install() -> bool:
    """우리 칸에 훅을 건다. 여러 번 불러도 결과가 같다. tmux 서버가 아직 없으면
    False — 감시 루프가 시작될 때 다시 부른다."""
    cmd = hook_command()
    ok = True
    for name, value in TITLE_OPTIONS:
        rc, _, err = tmux_runner.run(["set-option", "-g", name, value])
        if rc != 0:
            ok = False
            logger.debug(f"tmux 제목 옵션 설정 실패({name}): {err!r}")
    for event in EVENTS:
        rc, _, err = tmux_runner.run(["set-hook", "-g", f"{event}[{HOOK_INDEX}]", cmd])
        if rc != 0:
            ok = False
            logger.debug(f"tmux 훅 설치 실패({event}): {err!r}")
    return ok


def uninstall() -> None:
    """서버가 내려갈 때 우리 칸만 지운다(남겨 두면 내려간 서버를 계속 부른다).
    그 칸에 **이 프로세스의 비밀값이 있을 때만** 지운다 — 이미 다른 서버가 자기
    값으로 덮어썼다면(같은 소켓에 새 서버가 먼저 떴다) 그 훅을 지우면 안 된다."""
    for event in EVENTS:
        slot = f"{event}[{HOOK_INDEX}]"
        rc, out, _ = tmux_runner.run(["show-hooks", "-g", slot])
        if rc != 0 or _SECRET.encode() not in out:
            continue
        tmux_runner.run(["set-hook", "-gu", slot])


def verify(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, _SECRET)
