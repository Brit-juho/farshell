"""원격 호스트에서 들어오는 요청 (N7/N39 1단계) — `/api/peer/*`.

## 왜 별도 네임스페이스인가

기존 API(`/api/tmux`, `/api/files`, `/api/git/*` …)를 peer 토큰으로도 통과시키는
방식은 쓰지 않는다. 그러면 토큰 하나가 새는 순간 **서버 API 전체**가 열린다.
대신 peer가 닿을 수 있는 라우트를 처음부터 여기에만 만든다 — 이 파일에 없는 것은
peer가 아예 호출할 수 없다. `routes/files.py`의 "검사는 한 곳에서만"과 같은 원칙을
경로 설계 수준에서 적용한 것.

## 인증

`main.py`의 TokenAuthMiddleware는 `/api/peer/`를 우회한다(브라우저 세션 쿠키가
있을 리 없으므로). 그 대신 **이 파일이 서명 검증을 직접 한다**:

    X-Peer-Id / X-Peer-Ts / X-Peer-Nonce / X-Peer-Sig
    sig = HMAC(secret, "METHOD\\npath\\nts\\nnonce")

secret은 절대 전송되지 않는다. ts는 60초 창, nonce는 그 창 안에서 1회용(재생 차단).
서명 대상에 method/path가 들어가므로 view용 GET 서명을 control용 POST에 돌려쓸 수 없다.

## capability 관문 (2.1.6)

권한·응답 위생·감사 로그는 `routes/peer_protocol.py`의 `@capability`가 건다.
새 원격 capability를 여기 추가할 때 **`strip`을 반드시 정한다** — B 안에서만
뜻이 있는 식별자(세션 id·경로·PID·워크트리 id)를 A로 넘기면 A가 그걸 자기
것으로 오해한다. 일부러 안 지우는 경우도 `strip=()`로 그 판단을 남긴다.

## 전이(transitive) 금지

peer 응답에는 **내 로컬 것만** 담는다. 내가 등록한 다른 peer의 목록·세션은 절대
중계하지 않는다 — A↔B 상호 페어링에서 A→B→A 무한 재귀가 생기기 때문(hop 0 고정).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import json
import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import agent_msg
import host_store
from authz import hooks as authz_hooks
from authz import msg_svc
from i18n import tr
from routes.peer_protocol import (
    PeerDenied,
    _authenticate_raw,
    _require,
    _require_body,
    _version,
    capability,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# --- 페어링 (서명 없음 — 티켓 자체가 인증) --------------------------------------


@router.post("/api/peer/pair")
async def peer_pair(request: Request):
    """상대가 티켓을 제출하면 그 연결 전용 secret을 발급한다.

    이 엔드포인트만 서명 없이 열린다 — 아직 공유 secret이 없는 시점이기 때문.
    대신 1회용·5분 만료 티켓이 그 역할을 한다(QR 기기 등록과 같은 구조).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    ticket = str(body.get("ticket", ""))
    raw_id = str(body.get("id", ""))
    peer_id = host_store.normalize_id(raw_id)
    if peer_id is None:
        # `local` 등 예약어로 등록하려는 시도를 여기서 막는다 — 통과시키면
        # 로컬 호스트를 가로챈다.
        host_store.audit(raw_id or "?", "pair", False, "잘못되거나 예약된 호스트 id")
        return JSONResponse(
            {"error": "bad_id", "reason": tr("쓸 수 없는 호스트 id입니다: {id}", id=repr(raw_id))},
            status_code=400,
        )
    if host_store.consume_pair_ticket(ticket) is None:
        host_store.audit(peer_id, "pair", False, "티켓 무효/만료")
        return JSONResponse(
            {"error": "ticket_invalid",
             "reason": tr("티켓이 유효하지 않거나 만료됐습니다 — 'fsh host pair'를 다시 실행하세요")},
            status_code=401,
        )

    result = host_store.add_grant(
        peer_id, str(body.get("label", "")), version=str(body.get("version", "")),
    )
    if result is None:
        return JSONResponse({"error": "bad_id"}, status_code=400)
    grant, secret = result
    me = host_store.get_self()
    host_store.audit(peer_id, "pair", True, f"등급 {grant['level']}")
    logger.info(f"[peer] 페어링 완료: {peer_id} ({grant['label']}) 등급={grant['level']}")
    return {
        "ok": True,
        "secret": secret,
        "id": me["id"],
        "label": me["label"],
        "version": _version(),
        "serverTime": time.time(),
        "level": grant["level"],
    }


# --- ping (서명 필요) -------------------------------------------------------------


@router.get("/api/peer/sessions")
# 세션 이름은 A가 `remote:<host>:<name>`로 다시 감싸 쓰므로 그대로 내려보낸다.
# cwd도 의도적으로 남긴다(머리말의 등급 근거 참고).
@capability("sessions", strip=())
async def peer_sessions(request: Request, grant: dict):
    """이 호스트의 tmux 세션 + 에이전트 상태 (2단계, `view` 등급).

    **전이 금지(hop 0)**: 내가 등록한 다른 peer의 세션은 절대 섞지 않는다.
    A↔B 상호 페어링에서 A→B→A 무한 재귀가 생기기 때문 — 여기서는 언제나
    `agent_status.LOCAL_HOST` 엔트리만 본다.

    `cwd`를 함께 싣는 이유: 상대 화면이 "무슨 작업 중인 세션인가"를 보여주려면
    필요하다. 다만 이건 **경로 문자열 노출**이라, view 등급이 이미 세션 이름과
    출력까지 볼 수 있는 관계라는 전제 위에서만 정당하다(그보다 더 주지는 않는다).
    """
    import agent_status
    import tmux_runner

    def _collect() -> list[dict]:
        # `@fsh_origin`을 같은 호출에서 읽는다(로컬 `/api/tmux/sessions`와 같은 방식 — 추가
        # tmux 호출 없음). 이 호스트가 기록한 값이라 상대가 아니라 **이 호스트의 사실**이다.
        fmt = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{@fsh_origin}"
        text = tmux_runner.run_text(["list-sessions", "-F", fmt], timeout=2.0)
        if not text:
            return []
        panes = {}
        for p in tmux_runner.get_all_panes():
            panes.setdefault(p.session, p)
        out = []
        for line in text.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            name = parts[0]
            pane = panes.get(name)
            out.append({
                "name": name,
                "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
                "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                "command": pane.command if pane else "",
                "cwd": pane.path if pane else "",
                # 로컬 엔트리만 본다 — 이 값이 상대에게 건너가 그쪽에서 다시
                # host=<나>로 기록된다(A1의 host 차원이 여기서 쓰인다).
                "status": agent_status.status_for_session(name),
                # local|peer|agent|external — 로컬 목록과 같은 값. 기록이 없거나 모르는 값은
                # external(`normalize_origin`). 옛 서버는 이 필드를 안 주고, 받는 쪽이 local로 본다.
                "origin": tmux_runner.normalize_origin(parts[3] if len(parts) > 3 else ""),
            })
        return out

    sessions = await asyncio.to_thread(_collect)
    me = host_store.get_self()
    return {"ok": True, "id": me["id"], "label": me["label"], "sessions": sessions}


_SCREEN_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


def _screen_token(raw: str | None) -> str:
    """상대가 보낸 화면 토큰. 그대로 PTY 세션 id에 들어가므로 문자셋을 좁힌다."""
    raw = (raw or "").strip()
    return raw if _SCREEN_RE.fullmatch(raw) else ""


def _my_tty(grant: dict, screen: str) -> str | None:
    """이 peer의 그 화면(=B에 떠 있는 attach PTY)의 슬레이브 tty.

    **접두사를 여기서 강제한다** — 상대가 남의 화면 id를 주장해도 `peer-<자기
    id>-` 밖으로는 나갈 수 없다. 로컬 경로(`routes/tmux.py`)가 "tty를 클라이언트가
    고르게 하지 않는다"로 지키는 것과 같은 성질을, 원격에서는 이 접두사가 지킨다.
    """
    from routes.tmux import _tty_of_web_session

    if not screen:
        return None
    return _tty_of_web_session(f"peer-{grant['id']}-{screen}")


@router.get("/api/peer/clients")
# tty는 B 로컬 값이지만 A는 되돌려 보여주기만 한다 — 자기 것으로 오해할
# 여지가 없어 지우지 않는다.
@capability("clients", strip=())
async def peer_clients(request: Request, grant: dict):
    """이 호스트의 tmux 세션에 붙은 클라이언트 목록(`view` 등급).

    2.1.2에서 「연결된 화면」은 원격 세션에서 통째로 숨겨져 있었다. `/api/tmux/
    clients`는 **요청을 받은 맥의** tmux를 보므로, 원격 탭에서 그대로 부르면 같은
    이름의 로컬 세션 목록을 보여주고 「이 화면만 남기기」가 자기 자신을 끊었다.
    목록을 PTY 소유 호스트에게 물어보는 이 엔드포인트가 그 구멍을 메운다.

    등급 경계: **목록은 view, 끊기는 control**. 남의 화면을 끊는 건 입력과 같은
    무게의 조작이고, 보는 것은 이미 view가 출력 전체를 보는 것과 같은 무게다.
    """
    from routes.tmux import _CLIENT_SESSION_RE, _client_rows, _label

    session = (request.query_params.get("session") or "").strip()
    if not _CLIENT_SESSION_RE.fullmatch(session):
        return JSONResponse({"error": "invalid session name"}, status_code=400)
    my_tty = _my_tty(grant, _screen_token(request.query_params.get("screen")))
    rows = await asyncio.to_thread(_client_rows, session)
    for r in rows:
        r["is_me"] = bool(my_tty and r["tty"] == my_tty)
        r["label"] = _label(r, my_tty)
    return ({"session": session, "clients": rows, "me_tty": my_tty},
            f"{session} {len(rows)}건")


@router.post("/api/peer/clients/detach")
@capability("clients/detach", level=host_store.LEVEL_CONTROL, strip=())
async def peer_clients_detach(request: Request, grant: dict):
    """클라이언트 하나를 끊는다(`control` 등급). 자기 화면은 끊을 수 없다."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    import tmux_runner

    tty = str(body.get("tty") or "")
    if not tty:
        return JSONResponse({"error": "tty required"}, status_code=400)
    my_tty = _my_tty(grant, _screen_token(body.get("screen")))
    if my_tty and tty == my_tty:
        # 로컬 경로와 같은 규칙 — 지금 보고 있는 화면을 스스로 끊으면 복구 경로가
        # 없다(그리고 원격에서는 "왜 끊겼는지"가 더 안 보인다).
        return JSONResponse(
            {"error": "cannot detach self", "reason": tr("지금 보고 있는 화면은 끊을 수 없습니다")},
            status_code=400,
        ), "자기 화면"
    await asyncio.to_thread(tmux_runner.run, ["detach-client", "-t", tty], 2.0)
    return {"ok": True, "detached": tty}, tty


@router.post("/api/peer/clients/solo")
@capability("clients/solo", level=host_store.LEVEL_CONTROL, strip=())
async def peer_clients_solo(request: Request, grant: dict):
    """「이 화면만 남기기」 원격판(`control` 등급).

    화면 토큰으로 자기 tty를 특정하지 못하면 **아무것도 끊지 않는다** — 로컬
    경로와 같은 규칙이다. 전부 끊고 나면 되돌릴 방법이 없다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    import tmux_runner
    from routes.tmux import _CLIENT_SESSION_RE, _client_rows

    session = str(body.get("session") or "")
    if not _CLIENT_SESSION_RE.fullmatch(session):
        return JSONResponse({"error": "invalid session name"}, status_code=400)
    keep = _my_tty(grant, _screen_token(body.get("screen")))
    if not keep:
        return JSONResponse(
            {"error": "unknown client", "reason": tr("이 화면의 tty를 확인할 수 없습니다")},
            status_code=400,
        ), "자기 화면 미확인"

    def _solo() -> list[str]:
        detached = []
        for row in _client_rows(session):
            if row["tty"] == keep:
                continue
            tmux_runner.run(["detach-client", "-t", row["tty"]], timeout=2.0)
            detached.append(row["tty"])
        tmux_runner.run(["refresh-client", "-t", keep], timeout=2.0)
        return detached

    detached = await asyncio.to_thread(_solo)
    return ({"ok": True, "kept": keep, "detached": detached},
            f"{session} {len(detached)}건")


@router.get("/api/peer/search")
# `session_id`는 **B 안에서만 뜻이 있는 값**이다. 그대로 내려보내면 A가 그걸로
# 로컬 세션을 열려다 엉뚱한 세션을 연다 — 이름만 남기고 지운다.
@capability("search", strip=("session_id",))
async def peer_search(request: Request, grant: dict):
    """이 호스트의 스크롤백 검색(`view` 등급).

    `~` 검색은 로컬 세션만 봤다 — 원격 호스트를 고른 상태에서도 검색창은 이 맥의
    과거 출력만 뒤졌다. 검색은 **출력을 읽는 것**이라 view 등급이 맞다(view는
    이미 라이브 출력 전체를 본다).

    응답 상한은 로컬과 같은 값을 그대로 쓴다(`routes/search`의 상한) — 원격이라고
    더 많이 내려주면 프록시 쪽 팔레트가 감당하지 못한다. 호출 쪽(A)은 타임아웃을
    건다(peer_client의 기본 타임아웃).
    """
    from routes import search as search_routes

    q = (request.query_params.get("q") or "").strip()
    if not q:
        return {"results": [], "truncated": False}, "빈 질의"
    payload = await search_routes.search_scrollback(q=q, sessions="all")
    return payload, f"{len(payload['results'])}건"


@router.get("/api/peer/ping")
@capability("ping", strip=())
async def peer_ping(request: Request, grant: dict):
    """연결 확인 + 시계 동기 + 버전 교환. 1단계에서 유일한 인증 엔드포인트다.

    `serverTime`으로 상대가 시계 오차를 계산해 저장한다 — 그래야 다음 요청의
    서명이 60초 창 안에 들어온다.
    """
    me = host_store.get_self()
    return {
        "ok": True,
        "id": me["id"],
        "label": me["label"],
        "version": _version(),
        "serverTime": time.time(),
        "level": grant["level"],
    }


# --- 3단계: 입력(control) · 출력 스트림 -------------------------------------------
#
# 여기부터가 "원격 pane을 실제로 조작"하는 부분이다. 두 가지 원칙을 지킨다:
#
# 1. **입력은 control 등급에서만.** 서명이 method+path에 묶여 있으므로 view용
#    GET 서명을 이 POST/WS에 돌려쓸 수 없다 — 등급 검사와 서명 검사가 서로를
#    보강한다.
# 2. **PTY를 소유한 호스트만 알린다.** 이 PTY는 여기(B)에 있으므로 출력 감시·
#    푸시 알림·스크롤백 영속화는 전부 B의 설정을 따른다. 프록시 쪽(A)은
#    아무것도 기록하지 않는다 — 그래야 알림이 두 번 가지 않는다.
#    다만 **이 연결은 output_watcher에 등록하지 않는다**: 같은 tmux 세션을
#    B의 사용자가 이미 자기 탭으로 보고 있으면 그쪽이 이미 감시 중이고,
#    여기서 또 등록하면 완료 알림이 두 번 울린다.


@router.post("/api/peer/input")
@capability("input", level=host_store.LEVEL_CONTROL, strip=())
async def peer_input(request: Request, grant: dict):
    """원격 세션에 텍스트를 넣는다(control 등급).

    기본은 **Enter 없이 타이핑**이다(`POST /api/files/{id}/insert`와 같은 계약) —
    아직 완성 안 된 명령을 대신 실행시키지 않기 위해서다. `enter: true`면 Enter까지
    누른다: 큐 투입(A3)은 "실행돼야 하는 지시"라 Enter가 없으면 프롬프트에 영원히
    떠 있게 된다. 어느 쪽인지 **호출부가 명시**하게 하고 서버가 추측하지 않는다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    session = str(body.get("session", "")).strip()
    data = body.get("data")
    enter = bool(body.get("enter", False))
    if not session or not isinstance(data, str) or not data:
        return JSONResponse({"error": "bad_request", "reason": tr("session/data가 필요합니다")}, status_code=400)

    import tmux_target

    pane = tmux_target.session_pane(session)
    if not pane:
        return JSONResponse({"error": "session_not_found"}, status_code=404), f"세션 없음: {session}"
    send = tmux_target.send_to_tmux if enter else tmux_target.type_to_tmux
    ok = await asyncio.to_thread(send, pane, data)
    detail = f"{session} {len(data)}자{' +Enter' if enter else ''}"
    if not ok:
        return JSONResponse({"error": "input_failed"}, status_code=500), detail
    return {"ok": True}, detail


# --- 세션 생성 · 종료 · 이름 바꾸기 (control) -------------------------------------
#
# 2.2까지 원격 호스트는 "이미 떠 있는 세션을 보고 조작"하는 데서 멈췄다 — 레일의
# 「+ 새 세션」은 원격에서 꺼져 있었고, 원격 행에는 메뉴가 없었다. 셋 다 **control
# 등급**이다: control은 이미 셸 입력 전부를 할 수 있으므로(`/api/peer/input`) 셸
# 하나를 더 띄우거나 끄는 것이 새 권한을 만들지 않는다. 반대로 view에게 열면
# "보기만 허락한 상대가 내 작업 세션을 끈다"가 된다.
#
# **시작 폴더·실행 명령은 받지 않는다.** 세션은 이 호스트의 기본 폴더에서 기본
# 셸로만 시작한다 — 경로는 이 맥 안에서만 뜻이 있는 값이고(머리말의 strip 원칙과
# 같은 이유), 명령을 받으면 "세션 만들기"가 "임의 명령 실행"의 다른 입구가 된다.

# 첫 글자에 `-`를 막는다 — `rename-session -t =a -x`처럼 새 이름이 위치 인자로
# 들어가는 명령에서 tmux가 그걸 옵션으로 읽는다.
_TMUX_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-]{0,31}")


def _tmux_name_or_none(raw) -> str | None:
    """로컬 rename(`routes/pty.py`)과 같은 문자셋 + 길이 상한. tmux 인자로 바로
    들어가므로 여기서 걸러야 한다(`-t` 대상 문법의 `:`·`.` 등을 막는다)."""
    name = str(raw or "").strip()
    return name if _TMUX_NAME_RE.fullmatch(name) else None


async def _has_exact(name: str) -> bool:
    """정확히 이 이름의 세션이 있는가. `tmux_runner.has_session`은 `-t name`이라
    정확한 이름이 없으면 **접두사로도** 맞춘다(`dev`가 `dev-2`에 걸린다) — 종료·
    이름 바꾸기 대상이 엉뚱한 세션이 되지 않게 `=name`(정확 매칭)으로 묻는다."""
    import tmux_runner

    rc, _, _ = await tmux_runner.run_async(["has-session", "-t", f"={name}"], timeout=1.0)
    return rc == 0


def _bad_name(raw) -> tuple[JSONResponse, str]:
    return JSONResponse(
        {"error": "bad_name", "reason": tr("세션 이름은 영숫자·dash·underscore 1~32자만 가능합니다(첫 글자 dash 불가)")},
        status_code=400,
    ), f"잘못된 이름: {str(raw)[:40]!r}"


@router.post("/api/peer/sessions/create")
@capability("sessions/create", level=host_store.LEVEL_CONTROL, strip=())
async def peer_sessions_create(request: Request, grant: dict):
    """이 호스트에 tmux 세션을 만든다(`control` 등급). 이름이 비면 로컬 생성과
    같은 기본 이름 규칙(`routes/tmux.py`)을 쓴다 — 같은 맥에서 만든 세션이 어디서
    만들었느냐에 따라 이름 모양이 달라지지 않게."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    import platform_utils
    import tmux_runner
    from routes.tmux import _generate_default_session_name

    raw = body.get("name")
    if raw is None or str(raw).strip() == "":
        name = await asyncio.to_thread(_generate_default_session_name)
    else:
        name = _tmux_name_or_none(raw)
        if name is None:
            return _bad_name(raw)
        if await _has_exact(name):
            return JSONResponse(
                {"error": "exists", "reason": tr("이미 있는 이름입니다: {name}", name=name)}, status_code=409,
            ), f"중복: {name}"
    rc, _, err = await tmux_runner.run_async(
        ["new-session", "-d", "-s", name, "-c", platform_utils.default_start_dir()],
        timeout=5.0,
    )
    if rc != 0:
        return JSONResponse(
            {"error": "create_failed", "reason": err.decode("utf-8", errors="ignore")[:200]},
            status_code=500,
        ), f"생성 실패: {name}"
    # 만든 곳 기록 — 서명 검증을 통과한 peer 경로이므로 서버가 "peer"로 정한다(본문 값은 안 본다).
    await tmux_runner.set_origin_async(name, "peer")
    return {"ok": True, "name": name}, name


@router.post("/api/peer/sessions/kill")
@capability("sessions/kill", level=host_store.LEVEL_CONTROL, strip=())
async def peer_sessions_kill(request: Request, grant: dict):
    """이 호스트의 tmux 세션을 끝낸다(`control` 등급). 이 맥의 웹 탭이 같은 세션을
    열고 있으면 그 PTY·상태도 함께 정리한다 — 로컬 종료(`DELETE /api/tmux/kill`)를
    그대로 부르는 이유다(정리 순서를 두 군데서 따로 관리하지 않는다)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    from routes.tmux import kill_tmux_session

    name = _tmux_name_or_none(body.get("name"))
    if name is None:
        return _bad_name(body.get("name"))
    if not await _has_exact(name):
        return JSONResponse({"error": "session_not_found"}, status_code=404), f"세션 없음: {name}"
    result = await kill_tmux_session(name)
    if isinstance(result, JSONResponse):
        return result, f"종료 실패: {name}"
    return {"ok": True, "name": name}, name


@router.post("/api/peer/sessions/rename")
@capability("sessions/rename", level=host_store.LEVEL_CONTROL, strip=())
async def peer_sessions_rename(request: Request, grant: dict):
    """이 호스트의 tmux 세션 이름을 바꾼다(`control` 등급). tmux 세션 이름이 이름의
    단일 진실이라(`routes/pty.py` rename 주석) 이 맥의 웹 탭 레코드도 새 이름으로
    맞춘다 — 안 그러면 이 맥의 화면이 옛 이름으로 다시 붙으려다 실패한다."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    import tmux_runner
    from deps import session_store

    old = _tmux_name_or_none(body.get("name"))
    if old is None:
        return _bad_name(body.get("name"))
    new = _tmux_name_or_none(body.get("new"))
    if new is None:
        return _bad_name(body.get("new"))
    if old == new:
        return {"ok": True, "name": new}, "변경 없음"
    if not await _has_exact(old):
        return JSONResponse({"error": "session_not_found"}, status_code=404), f"세션 없음: {old}"
    if await _has_exact(new):
        return JSONResponse(
            {"error": "exists", "reason": tr("이미 있는 이름입니다: {name}", name=new)}, status_code=409,
        ), f"중복: {new}"
    rc, _, err = await tmux_runner.run_async(["rename-session", "-t", f"={old}", new], timeout=2.0)
    if rc != 0:
        return JSONResponse(
            {"error": "rename_failed", "reason": err.decode("utf-8", errors="ignore")[:200]},
            status_code=500,
        ), f"변경 실패: {old}"
    for local in session_store.find_all_by_tmux_name(old):
        session_store.update_tmux_name(local.session_id, new)
    return {"ok": True, "name": new}, f"{old} → {new}"


@router.websocket("/api/peer/ws/{tmux_name}")
async def peer_ws(ws: WebSocket, tmux_name: str):
    """원격 pane의 출력 스트림(+ control이면 입력). 상대 서버(A)의 프록시가 연결한다.

    브라우저가 직접 여는 소켓이 아니다 — 서명 헤더를 붙일 수 있는 건 서버뿐이다
    (브라우저 WebSocket API는 커스텀 헤더를 못 보낸다). 그게 이 구조가 서버-서버인
    이유이기도 하다.

    수명: 이 연결 전용 PTY를 만들고 끊길 때 정리한다. B의 사용자가 자기 탭으로
    보고 있는 PTY를 공유하지 않는 이유는 크기(resize) 때문이다 — 두 화면의 크기가
    다르면 한쪽이 계속 찌그러진다. tmux가 같은 세션에 여러 클라이언트를 붙이는
    것을 이미 지원하므로 PTY를 따로 두는 게 맞다.
    """
    try:
        grant = _authenticate_raw("GET", ws.url.path, ws.headers)
    except PeerDenied as e:
        # accept 전에 닫으면 상대는 HTTP 403을 받는다 — 이유를 실을 수 없어
        # 일단 받아들인 뒤 코드와 함께 닫는다.
        await ws.accept()
        await ws.close(code=4401, reason=e.reason[:120])
        return

    import tmux_runner
    from routes.tmux import TMUX_SOCKET
    from deps import pty_mgr
    import platform_utils

    if not await tmux_runner.has_session_async(tmux_name):
        await ws.accept()
        host_store.audit(grant["id"], "ws", False, f"세션 없음: {tmux_name}")
        await ws.close(code=4404, reason="tmux session not found")
        return

    await ws.accept()
    can_control = grant.get("level") == host_store.LEVEL_CONTROL
    authz_hooks.peer_ws_control(grant, tmux_name, can_control)   # shadow(plan-2.6 1단계)
    # 「연결된 화면」이 원격에서도 동작하려면 상대가 **자기 화면이 어느 것인지**
    # 지목할 수 있어야 한다(안 그러면 「이 화면만 남기기」가 자기 자신을 끊는다).
    # 그래서 화면 토큰을 상대가 정해 보내고, PTY 세션 id에 그대로 박는다:
    #   peer-<peer id>-<screen>
    # 토큰은 서명 대상(method+path)에 안 들어가지만 문제되지 않는다 — 접두사에
    # peer id가 박혀 있고 아래 clients 엔드포인트가 그 접두사를 강제하므로,
    # 어떤 토큰을 주장하든 **자기 화면 집합 안에서만** 고를 수 있다.
    screen = _screen_token(ws.query_params.get("screen"))
    session_id = f"peer-{grant['id']}-{screen or time.time_ns()}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2048)

    def _on_data(data: bytes) -> None:
        # PTY 리더 스레드에서 불린다 — 큐에 넣기만 하고 전송은 아래 태스크가 한다.
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", tmux_name],
        cols=80, rows=24,
    )
    pty_mgr.subscribe(session_id, _on_data)
    host_store.audit(grant["id"], "ws", True, f"{tmux_name} 등급 {grant.get('level')}")

    async def _pump_out():
        while True:
            data = await queue.get()
            await ws.send_bytes(data)
            # 2026-09-28 — attach PTY가 끝났는가. pty_manager는 EOF 때 `[process
            # exited]`를 흘린 **직후** PTY를 목록에서 뺀다. 예전엔 여기서 아무것도 안
            # 봐서, 세션이 사라져도 이 소켓은 열린 채 죽은 화면만 남았다(재연결 때에야
            # 4404). 세션 자체가 없어졌을 때만 닫는다 — 세션은 살아 있는데 이 화면만
            # 끊긴 경우(「이 화면만 남기기」 등)에 닫으면 브라우저가 다시 붙어 그
            # 기능을 무력화한다.
            if queue.empty() and session_id not in pty_mgr.sessions:
                if not await _has_exact(tmux_name):
                    await ws.close(code=4404, reason="tmux session gone")
                    return

    out_task = asyncio.create_task(_pump_out())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if can_control:
                    pty_mgr.write(session_id, msg["bytes"])
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if payload.get("type") == "resize":
                # 크기는 등급과 무관하게 받는다 — 보기만 하는 화면도 자기 크기에
                # 맞게 그려져야 한다(입력이 아니다).
                pty_mgr.resize(session_id, int(payload.get("cols", 80)), int(payload.get("rows", 24)))
            elif payload.get("type") == "input" and can_control:
                pty_mgr.write(session_id, str(payload.get("data", "")).encode())
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 — 어떤 이유로든 연결이 끝나면 정리가 우선
        logger.debug(f"[peer ws] {e}")
    finally:
        out_task.cancel()
        pty_mgr.unsubscribe(session_id, _on_data)
        pty_mgr.destroy_session(session_id)
        host_store.audit(grant["id"], "ws", True, f"{tmux_name} 종료")


@router.post("/api/peer/agent/message")
# 응답의 `id`는 보낸 쪽이 정한 메시지 id를 그대로 돌려주는 것이라 양쪽에서 뜻이
# 같다(답장이 이 id로 대화를 잇는다) — 지우지 않는다.
@capability("agent/message", level=host_store.LEVEL_CONTROL, strip=(), body=True)
async def peer_agent_message(request: Request, grant: dict, data: bytes):
    """다른 맥의 에이전트가 보낸 메시지를 받아 이 맥 세션의 큐에 넣는다(agent_msg.py).

    control 등급 **과** `fsh host allow-agent` 스위치 둘 다 있어야 받는다 — 에이전트에게
    일을 시키는 길이라 셸 입력 허용만으로 저절로 열리면 안 된다. 본문 해시까지
    서명한다(메시지 내용이 곧 받는 에이전트가 실행할 지시다).
    """
    ticket = None
    allowed = bool(grant.get("agentMessages"))
    authz_hooks.peer_agent_messages(grant, allowed)   # shadow(plan-2.6 1단계) — 켜짐에서도 옛 검사와의 차이를 센다
    if msg_svc.enforced():
        # 3c — 엔진이 판정한다(hosts.json 의 agentMessages 를 그 자리에서 번역한 grant). 결과는 옛 검사와 같다.
        try:
            v = await asyncio.to_thread(msg_svc.authorize_peer, grant)
        except agent_msg.MessageError as e:
            return JSONResponse({"error": e.code, "reason": e.reason}, status_code=e.status), f"거절: {e.code}"
        allowed, ticket = v.allow, v.ticket
    if not allowed:
        return JSONResponse(
            {"error": "agent_messages_off", "reason":
             "상대 맥이 에이전트 메시지를 받지 않습니다 — 그 맥에서 "
             "'fsh host allow-agent <이 맥 id>'를 실행해야 합니다"},
            status_code=403,
        ), "에이전트 메시지 수신 꺼짐"
    try:
        body = json.loads(data.decode("utf-8") or "{}")
    except ValueError:
        return JSONResponse({"error": "bad_body"}, status_code=400), "본문이 JSON이 아님"
    to_session = str(body.get("to") or "")
    if not agent_msg.SESSION_RE.match(to_session):
        return JSONResponse({"error": "bad_target", "reason": "받는 세션 이름이 올바르지 않습니다"},
                            status_code=400), f"잘못된 대상: {to_session[:40]!r}"
    if not await _has_exact(to_session):
        return JSONResponse({"error": "session_not_found",
                             "reason": f"이 맥에 '{to_session}' 세션이 없습니다"},
                            status_code=404), f"세션 없음: {to_session}"
    try:
        msg = await asyncio.to_thread(
            agent_msg.receive,
            from_host=grant["id"], from_label=grant.get("label") or grant["id"],
            from_session=str(body.get("from") or ""), to_session=to_session,
            text=str(body.get("text") or ""), reply_to=body.get("replyTo") or None,
            claimed_hop=int(body.get("hop") or 0), msg_id=str(body.get("id") or ""),
            conv=str(body.get("conv") or ""), authz=ticket,
        )
    except agent_msg.MessageError as e:
        return JSONResponse({"error": e.code, "reason": e.reason},
                            status_code=e.status), f"거절: {e.code}"
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad_body"}, status_code=400), "잘못된 필드"
    immediate = agent_msg.maybe_drain(to_session)
    return ({"ok": True, "id": msg["id"], "conv": msg["conv"], "hop": msg["hop"],
             "status": msg["status"], "immediate": immediate},
            f"{msg['from']['session'] or '?'} → {to_session} #{msg['id']}")


@router.post("/api/peer/file")
# `id`는 **B의 파일 저장소 키**이고 `path`는 **B의 절대 경로**다. 둘 다
# A에서는 아무 뜻이 없는데 모양은 A의 것과 똑같아서, 화면이 이 `id`로
# `/api/files/{id}/…`를 부르면 A의 저장소에서 엉뚱한 파일을 찾는다. 지금은
# 화면이 `typed`/`reused`만 쓰지만 **모양이 같다는 것 자체가 함정이다** —
# 나중에 누가 쓰기 시작하는 걸 코드로 막는다.
#
# 나중에 원격 파일 목록(2.2)에서 이 파일을 다시 가리켜야 하면, 지우는 대신
# **B 것임이 이름에 드러나는 형태**로 바꿀 것 — 세션이 `remote:<host>:<name>`을
# 쓰는 것과 같은 방식이다. 로컬 것과 구별 안 되는 `id`를 그냥 내려보내는
# 길로는 돌아가지 않는다.
@capability("file", level=host_store.LEVEL_CONTROL, strip=("id", "path"), body=True)
async def peer_file(request: Request, grant: dict, data: bytes):
    """파일 바이트를 받아 이 호스트의 저장소에 넣는다(A2, control 등급).

    ## 왜 control인가
    파일을 남의 디스크에 쓰는 행위이고, `session`이 함께 오면 그 경로를 pane에
    타이핑까지 한다 — 입력과 같은 등급이 맞다. `view` 상대는 남의 맥에 파일을
    떨어뜨릴 수 없어야 한다.

    ## 왜 본문 해시를 서명하나
    다른 엔드포인트는 본문이 작은 JSON이라 method+path 서명으로 충분했다. 파일은
    터널을 지나는 큰 덩어리라 "서명은 맞는데 바이트가 다른" 경우를 구분할 수
    있어야 한다(`X-Peer-Body`).

    ## 중복 전송
    같은 파일을 다시 보내면 저장하지 않고 이미 있는 항목을 그대로 쓴다 —
    `origin`(보낸 쪽 호스트 id + 그쪽 파일 id)으로 판정한다. 파일 내용 해시가
    아니라 origin을 쓰는 이유: 내용이 같아도 **다른 사람이 보낸 파일**은 다른
    파일로 다뤄야 하고, 무엇보다 200MB를 매번 해싱해 대조할 이유가 없다.
    """
    import file_store
    import tmux_target

    name = request.headers.get("x-peer-file-name", "") or "file"
    src_id = request.headers.get("x-peer-file-id", "")
    session = request.headers.get("x-peer-file-session", "").strip()

    if len(data) > file_store.MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": "too_large",
             "reason": tr("파일이 이 호스트의 상한을 넘습니다 (최대 {mb}MB)", mb=file_store.MAX_UPLOAD_BYTES // (1024*1024))},
            status_code=413,
        ), f"{name} 크기 초과"

    origin = f"{grant['id']}:{src_id}" if src_id else ""
    item = await asyncio.to_thread(file_store.find_by_origin, origin) if origin else None
    reused = item is not None
    if item is None:
        item = await asyncio.to_thread(_store_peer_file, data, name, origin)

    path = str(file_store.real_path_for(item["id"]))
    typed = False
    if session:
        pane = tmux_target.session_pane(session)
        if pane:
            # 파일 경로는 **Enter 없이** 타이핑한다 — 로컬 파일 삽입과 같은 계약.
            typed = await asyncio.to_thread(tmux_target.type_to_tmux, pane, path)
    return ({"ok": True, "id": item["id"], "path": path, "reused": reused, "typed": typed},
            f"{name} {len(data)}B{' (재사용)' if reused else ''}{' → ' + session if typed else ''}")


def _store_peer_file(data: bytes, name: str, origin: str) -> dict:
    """받은 바이트를 임시 파일로 쓰고 저장소에 편입한다(복사 없이 rename)."""
    import tempfile

    import file_store

    tmp_dir = file_store.files_dir()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(tmp_dir), prefix=".peer-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        return file_store.add_from_upload(Path(tmp), name, len(data), origin=origin)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
