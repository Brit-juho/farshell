"""N25(2.1.5 3/n) — tmux 세션의 붙여넣기는 tmux 자신에게 위임한다.

1/n 실측: tmux는 attach하는 순간 바깥쪽 클라이언트에게 자기 쪽 DECSET 2004를
한 번 켜고 계속 켜둔 채, **안쪽 pane의 진짜 bracketed 상태는 tmux가 따로
관리한다.** 그래서 우리가 밖에서 "이 tmux 세션이 지금 bracket을 원하는가"를
알 방법이 없다 — 대신 tmux `paste-buffer -p`에 넘기면 tmux가 정확히 안다.

근거(95번 계획서 §1-1, tmux `cmd-paste-buffer.c` 소스 직독):
    if (bracket && (wp->screen->mode & MODE_BRACKETPASTE)) { ... }
`-p`를 **무조건** 준다 — 마커를 붙일지는 tmux가 그 pane의 실제 화면 모드로
판단하므로, 우리가 따로 감쌀지 말지 정할 필요가 없다(그래서 여기서는
paste_prepare의 개행 정규화·제어문자 제거만 쓰고 ESC 무력화·마커 씌우기는
쓰지 않는다 — 그건 tmux 쪽 일이다).

tmux 3.7부터 `paste-buffer`가 기본으로 `vis(3)`를 태워 내용을 이스케이프한다
(CHANGES: "버퍼에 bracket end 시퀀스가 들어 있으면 문제가 된다"가 근거) —
그런데 우리는 이미 위험한 제어문자를 직접 제거했으므로 이중으로 손댈 필요가
없고, vis(3)는 UTF-8을 모르는 구현이라 한글 등 비ASCII를 오염시킬 수 있다.
그래서 **3.7 이상이면 `-S`로 그 이스케이프를 끈다.** 3.6 이하는 애초에
`-S` 옵션 자체가 없고(붙이면 명령이 실패한다) vis(3)도 안 태우므로 그냥
`-p`만 쓴다.
"""

from __future__ import annotations

import logging
import re
import uuid

import paste_prepare
import tmux_runner

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")

# vis(3) 이스케이프가 들어온 버전. CHANGES FROM 3.6b TO 3.7 확인(95번 계획서).
_VIS_SINCE = (3, 7)


def tmux_version() -> tuple[int, int] | None:
    """(major, minor). 파싱 실패·tmux 없음이면 None — 그때는 `-S`를 안 쓴다
    (모르면 구버전으로 취급 — `-S`를 잘못 붙여 명령 자체가 실패하는 것보다
    vis(3)를 한 번 더 태우는 쪽이 안전하다: 최악의 경우도 "내용이 조금
    이스케이프됨"이지 "붙여넣기가 아예 실패함"이 아니다)."""
    rc, out, _ = tmux_runner.run(["-V"], timeout=1.5)
    if rc != 0:
        return None
    m = _VERSION_RE.search(out.decode("utf-8", "replace"))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def supports_paste_sanitize_off(version: tuple[int, int] | None) -> bool:
    return version is not None and version >= _VIS_SINCE


def paste_via_tmux(tmux_name: str, text: str) -> bool:
    """tmux 세션(`tmux_name`)의 활성 pane에 텍스트를 붙여넣는다.

    버퍼는 매번 새 이름으로 만들고 `-d`로 붙여넣은 직후 지운다 — 사용자가
    tmux 안에서 직접 쓰던 버퍼 스택(prefix+])을 오염시키지 않는다.
    성공하면 True, 실패(세션 없음·tmux 자체 문제)면 False — 호출자가
    "조용히 삼키지 않고" 알린다(계획서 4-4 위험 3의 요구).
    """
    if not text:
        return True
    body = paste_prepare.strip_unsafe_control_chars(text)
    body = paste_prepare.normalize_newlines(body)
    payload = body.encode("utf-8", "surrogatepass")

    buf_name = f"vt-paste-{uuid.uuid4().hex[:12]}"
    rc, _, err = tmux_runner.run(["load-buffer", "-b", buf_name, "-"], input=payload)
    if rc != 0:
        logger.warning(f"tmux load-buffer 실패: {err!r}")
        return False

    args = ["paste-buffer", "-p", "-d", "-b", buf_name, "-t", tmux_name]
    if supports_paste_sanitize_off(tmux_version()):
        args.insert(1, "-S")  # paste-buffer -p -S -d ...
    rc, _, err = tmux_runner.run(args)
    if rc != 0:
        logger.warning(f"tmux paste-buffer 실패({tmux_name}): {err!r}")
        # load-buffer는 성공했지만 paste-buffer가 실패한 경우 -d가 못 지운
        # 버퍼가 남는다 — 조용히 시도만 하고 실패해도 무시(사용자 버퍼
        # 스택엔 없는 이름이라 눈에 띄지 않고, 다음 GC(tmux 자체 버퍼 상한)가
        # 정리한다).
        tmux_runner.run(["delete-buffer", "-b", buf_name])
        return False
    return True
