"""시스템 상태 — capabilities, tunnel, safe-mode, workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import os
import shutil
import socket
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import fsguard
import i18n
import network_access
import notify
import platform_utils
import push
import safe_mode
import tailscale
import tmux_hooks
import tunnel
import device_settings
import usage
import stt_service
import voice_handler
import workspace
from deps import pty_mgr

logger = logging.getLogger(__name__)

router = APIRouter()

# N34(HUD) — 상태바 우측 끝에 버전을 띄운다. VERSION 파일은 릴리스마다만
# 바뀌므로 프로세스 수명 동안 한 번만 읽는다. 파일이 없거나 못 읽으면 빈
# 문자열 → 프런트가 "값이 없는 칩은 숨긴다" 규칙으로 알아서 안 그린다.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSION: str | None = None


def _version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            _VERSION = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        except Exception:
            _VERSION = ""
    return _VERSION


# N37(헤더 §4) — 워크스페이스 칩의 "/ mac-studio" 자리. tailscale MagicDNS
# 이름(tailscale.get_hostname())은 tailscale이 안 돌면 없으므로, 항상 있는
# OS 호스트명을 쓴다 — "이 서버가 물리적으로 어느 기기인가"만 알면 되고,
# 네트워크 신원까지는 필요 없다. 재부팅 전엔 안 바뀌므로 한 번만 계산한다.
_HOSTNAME: str | None = None


def _hostname() -> str:
    global _HOSTNAME
    if _HOSTNAME is None:
        try:
            _HOSTNAME = socket.gethostname().split(".")[0]
        except Exception:
            _HOSTNAME = ""
    return _HOSTNAME


# N45(모바일 헤더 환경 칩) — "macOS · MacBook Pro M1 Pro"의 뒷부분. hostname과
# 달리 사용자가 짓지 않는, 기기 자체의 정체다. macOS만 system_profiler로 읽는다
# (Linux/WSL2는 상응하는 표준 조회가 없어 빈 문자열 — hostname/version과 같은
# "값 없으면 프런트가 칩을 안 그린다" 규칙에 맡긴다).
_MACHINE: str | None = None


def _machine() -> str:
    """실측(2026-09-23, M1 Pro): `system_profiler SPHardwareDataType` 170ms.
    tailscale/tunnel 프로브만큼 무겁진 않지만 매 `/api/capabilities` 호출마다
    돌릴 이유가 없다 — 재부팅 전엔 안 바뀌므로 `_hostname()`/`_version()`과
    같은 프로세스-수명 캐시로 한 번만 계산한다."""
    global _MACHINE
    if _MACHINE is None:
        _MACHINE = ""
        if platform_utils.IS_MACOS:
            try:
                out = subprocess.run(
                    ["system_profiler", "SPHardwareDataType"],
                    capture_output=True, text=True, timeout=3,
                ).stdout
                name = chip = ""
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("Model Name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("Chip:"):
                        chip = line.split(":", 1)[1].strip()
                _MACHINE = " ".join(p for p in (name, chip.replace("Apple ", "")) if p)
            except Exception:
                _MACHINE = ""
    return _MACHINE


def _etag_response(payload, request: Request, stable_for_etag=None) -> Response:
    """Phase 9 #9: ETag/304 — 변화 적은 GET에 대해 If-None-Match 처리.

    payload를 정렬-직렬화한 후 sha1 16자리를 ETag로 사용.
    `stable_for_etag`가 주어지면 그것으로 hash를 계산 — payload 안에 timestamp 같은
    매번 변하는 필드가 있어도 ETag는 안정적으로 유지된다.
    """
    hash_src = stable_for_etag if stable_for_etag is not None else payload
    body = _json.dumps(hash_src, sort_keys=True, separators=(",", ":")).encode()
    etag = hashlib.sha1(body).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})

# WS 클라이언트 — 워크스페이스 변경 브로드캐스트
_workspace_clients: set[WebSocket] = set()


def _capabilities_payload() -> dict:
    """프로브를 전부 도는 **동기** 함수 — 반드시 to_thread로 부른다.

    실측(2026-09-16): 합계 87ms 중 `tailscale.get_status_dict()` 53.6ms +
    `tunnel.get_tunnel_status()` 32.9ms가 99%다(나머지 11개 프로브 합계 0.26ms).
    둘 다 서브프로세스라, 이벤트 루프에서 직접 돌면 그 87ms 동안 서버 전체가
    멈춘다 — 터미널 타이핑에서 감지되는 크기다. 두 모듈에 TTL 캐시를 넣어
    호출 빈도도 함께 줄였다(tailscale 30초 · tunnel 10초).
    """
    # ⚠️ 모델을 로드하지 않고 「이 서버에서 STT 엔진을 쓸 수 있는가」만 확인한다. 예전엔 페이지 로드마다
    # (capabilities는 grid.js가 시작 시 호출) 모델 ~400MB를 서버에 올렸다 → 터미널만 쓰는 사용자도 물었다.
    stt_ok = voice_handler.stt_available()
    tts_ok = voice_handler.tts_available()
    spec = network_access.get_current_spec()
    payload = {
        "voice": stt_ok or tts_ok,
        # 로드돼 있으면 실제 엔진명, 아니면 설치만 됨(available)/미설치(none)
        "stt": (stt_service.loaded_engine_id() if voice_handler.stt_loaded()
                else ("available" if stt_ok else "none")),
        "stt_loaded": voice_handler.stt_loaded(),
        "tts": "available" if tts_ok else "none",
        "network_mode": os.environ.get("VT_NETWORK_MODE", "all"),
        "bound_host": network_access.resolve_bind_host(spec),
        "lan_ip": network_access.get_lan_ip(),
        "tunnel": tunnel.get_tunnel_status(),
        "tailscale": tailscale.get_status_dict(),
        # P2: 시작할 수 있는 루트가 하나도 없으면(설정 오류 등) 코드 뷰어 UI를 숨긴다.
        "fs": bool(fsguard.get_start_roots()),
        # P3: lsof 없이는 포트 스캔이 불가능하다(최소 리눅스 컨테이너 등).
        "ports": shutil.which("lsof") is not None,
        # P5: pywebpush 설치 여부. 실제 구독 가능 여부는 secure context 도 필요하므로
        # 프론트가 isSecureContext 를 함께 본다.
        "push": push.available(),
        # U1: 사용량 소스(clauth 피드 등)가 없으면 rail 항목·우측 레일 섹션을
        # 통째로 숨긴다 — 기존 fs/ports/push 게이팅과 같은 메커니즘이라
        # 프런트에 새 개념이 생기지 않는다.
        "usage": usage.capability(),
        # N41: 누적형(로컬 LLM 등) 소스 — 한도형(clauth)과 독립적으로 게이팅한다.
        # 어느 한쪽만 있어도 dock 사용량 탭은 떠야 하므로, 프런트는 이 둘을
        # OR로 묶어 `.needs-usage`를 판정한다(agent/status.js).
        "usage_counter": usage.counter_capability(),
        # 96번 계획서 — Codex도 한도형 독립 소스다. clauth와 마찬가지로
        # OR로 묶어 `.needs-usage`를 판정한다(agent/status.js).
        "usage_codex": usage.codex_capability(),
        # N34: HUD 우측 끝 버전 칩. 빈 문자열이면 프런트가 칩을 안 그린다.
        "version": _version(),
        # N37: 헤더 워크스페이스 칩. 빈 문자열이면 "/ 호스트" 부분을 안 그린다.
        "hostname": _hostname(),
        # N45: 모바일 헤더 환경 칩("macOS · MacBook Pro M1 Pro"). machine이
        # 빈 문자열이면(비-macOS) 프런트가 os만 보여준다.
        "os": platform_utils.PLATFORM_NAME,
        "machine": _machine(),
        # P3-3(60-observability.md §1) — 세션별 broadcast 드롭 누적을 합친
        # 값. `fsh doctor`가 0이 아니면 경고한다(60-observability.md).
        "dropped_subscribers": sum(s.dropped_subscribers for s in pty_mgr.sessions.values()),
    }
    return payload


@router.get("/api/capabilities")
async def capabilities(request: Request):
    payload = await asyncio.to_thread(_capabilities_payload)
    # ETag는 결정적 부분(tunnel.checked_at 같은 timestamp 제외)으로만 계산.
    stable = {k: v for k, v in payload.items() if k != "tunnel"}
    tun = dict(payload.get("tunnel") or {})
    tun.pop("checked_at", None)
    stable["tunnel"] = tun
    return _etag_response(payload, request, stable_for_etag=stable)


@router.get("/api/usage")
async def usage_get(request: Request):
    """U1 — 정규화된 사용량 스냅샷.

    미설치/비활성이면 `{available: false, reason}`을 돌려준다(404가 아니다) —
    프런트는 이 응답 하나로 "기능이 없다"와 "일시적 실패"를 구분할 수 있어야
    한다. ETag는 `generated_at`을 뺀 값으로 계산한다: 피드가 90초마다 새
    타임스탬프를 찍는데 그걸 포함하면 내용이 같아도 매번 새 ETag가 나온다
    (기존 `tunnel.checked_at` 처리와 같은 이유).
    """
    # 96번 계획서 — 이 엔드포인트가 실제로 화면에 그릴 것(clauth+codex를
    # 합친 프로필 목록)을 결정한다. 설정 →「정보」의 clauth 전용 진단은
    # `usage.capability()`(merged 아님)를 따로 쓴다 — 섞으면 그 진단이
    # codex 상태에 가려 틀린 말을 하게 된다.
    # 둘 다 동기 I/O다(clauth 파일 읽기, codex는 캐시가 비면 외부 HTTPS — 최대
    # TIMEOUT_SEC). 이벤트 루프에서 부르면 그동안 터미널 입출력까지 전부 멈춘다
    # (2026-09-29 실측: 요청당 ~390ms 정지). _capabilities_payload와 같은 관용구.
    cap = await asyncio.to_thread(usage.merged_capability)
    if not cap.get("available"):
        return _etag_response({"available": False, **cap}, request)
    snap = await asyncio.to_thread(usage.merged_snapshot)
    if snap is None:
        return _etag_response({"available": False, "provider": cap.get("provider"), "reason": "read-failed"}, request)
    payload = {"available": True, **snap}
    stable = {k: v for k, v in payload.items() if k != "generated_at"}
    return _etag_response(payload, request, stable_for_etag=stable)


@router.get("/api/usage/counter")
async def usage_counter_get(request: Request, since: float = 0):
    """N41 — 누적형 사용량 스냅샷. `/api/usage`(한도형)와 별개 엔드포인트다 —

    둘 다 없을 수도, 하나만 있을 수도, 둘 다 있을 수도 있어서(clauth 없이
    로컬 LLM만 쓰는 사용자도 있다) 응답을 합치면 "무엇이 없는지"가
    모호해진다. `since`(epoch초, 기본 0=전체 누적)로 집계 창을 고를 수 있다 —
    7일 스파크라인(`samples`)은 이와 무관하게 항상 최근 7일 고정이다.
    """
    cap = usage.counter_capability()
    if not cap.get("available"):
        return _etag_response({"available": False, **cap}, request)
    snap = usage.counter_snapshot(since)
    if snap is None:
        return _etag_response({"available": False, "provider": cap.get("provider"), "reason": "read-failed"}, request)
    running = set(usage.counter_running_models())
    counters = [{**c, "running": c["label"] in running} for c in snap.get("counters", [])]
    payload = {"available": True, **snap, "counters": counters}
    stable = {k: v for k, v in payload.items() if k != "generated_at"}
    return _etag_response(payload, request, stable_for_etag=stable)


@router.post("/api/usage/counter")
async def usage_counter_post(request: Request):
    """N41 — 누적형 사용량 기록. `fsh usage add`의 서버 경유 경로.

    호출 주체는 FarShell이 아니다 — 로컬 LLM을 실행한 사용자 스크립트/훅이
    "방금 이만큼 썼다"를 알려주는 것뿐이다. 그래서 model/tokens/seconds
    세 필드만 받고, 입력 검증은 `counter_jsonl.add()`(범위·타입)가 전부다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    r = usage.counter_add(body.get("model"), body.get("tokens"), body.get("seconds"))
    if not r.get("ok"):
        status = 403 if r.get("error") == "disabled" else 400
        return JSONResponse(r, status_code=status)
    return r


@router.get("/api/tailscale/status")
async def tailscale_status(request: Request):
    return _etag_response(await asyncio.to_thread(tailscale.get_status_dict), request)


@router.post("/api/notify/client-event")
async def notify_client_event(request: Request):
    """D9: tmux client-attached/client-detached 훅이 호출하는 엔드포인트.

    SSH(+Tailscale)로 순수 텍스트 접속하는 클라이언트는 web/voice 경로와 달리
    서버가 자연히 알 방법이 없다. `bin/vt`의 `_maybe_register_client_hooks`가
    `VT_NOTIFY_CLIENT_EVENTS=1`일 때만 tmux 훅을 등록하고,
    `server/hooks/tmux_client_notify.sh`가 attach/detach 시 이 엔드포인트로
    POST해서 기존 ntfy/Telegram 브릿지(notify.py)로 "누가 언제 접속했는지"를 알린다.

    항상 127.0.0.1(tmux 서버가 도는 로컬 머신)에서만 호출되므로 원격 노출 없음.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    event = str(body.get("event", "attached"))[:16]
    session = str(body.get("session", "?"))[:64]
    remote_host = str(body.get("remote_host", "") or "").strip()[:128]

    if event == "detached":
        title = "tmux 세션 연결 해제"
        message = f"세션 '{session}'에서 클라이언트 연결이 끊어졌습니다"
        tags = "lock"
    else:
        title = "🔐 tmux 세션 접속"
        source = f"SSH ({remote_host})" if remote_host else "로컬 클라이언트"
        message = f"세션 '{session}'에 새 클라이언트 연결 — {source}"
        tags = "unlock,warning"

    sent = await notify.send(title, message, priority="default", tags=tags)
    return {"ok": True, "notified": sent, "configured": notify.is_configured()}


@router.get("/api/safe-mode")
async def safe_mode_status(request: Request):
    return _etag_response({"enabled": safe_mode.is_enabled()}, request)


@router.get("/api/hooks/status")
async def hooks_status():
    """A0/S4 — Claude Code와 Codex 훅 등록 상태.

    설정 화면 「정보」가 읽는다. 훅이 없으면 상태 배지·프롬프트 큐 자동 투입·
    TTS 요약이 전부 **조용히** 동작하지 않으므로("왜 아무 일도 안 일어나지"의
    1번 원인), 사용자가 화면에서 확인할 수 있어야 한다.
    """
    import claude_hooks
    import codex_hooks

    try:
        settings = claude_hooks.load_settings(claude_hooks.settings_path())
    except ValueError as e:
        return {"ok": False, "error": "unreadable", "reason": str(e), "events": {}}
    claude_plan = claude_hooks.plan(settings)
    try:
        codex_plan = codex_hooks.plan_text(codex_hooks.load_text(codex_hooks.settings_path()))
        codex_error = None
    except ValueError as e:
        codex_plan = {}
        codex_error = str(e)
    tools = {
        "claude": {
            "ok": all(state == "ok" for state, _ in claude_plan.values()),
            "events": {event: state for event, (state, _) in claude_plan.items()},
        },
        "codex": {
            "ok": bool(codex_plan) and all(state == "ok" for state, _ in codex_plan.values()),
            "events": {event: state for event, (state, _) in codex_plan.items()},
        },
    }
    if codex_error:
        tools["codex"]["error"] = codex_error
    # hooks.json 에 복제된 FarShell 훅(Codex 앱 가져오기). 경고일 뿐 ok 에는 영향 없다.
    dup_events = [event for event, _args, _tag in codex_hooks.find_foreign_farshell_hooks()]
    tools["codex"]["duplicates"] = {"count": len(dup_events), "events": dup_events}
    return {
        "ok": all(tool["ok"] for tool in tools.values()),
        # 기존 클라이언트 호환: 최상위 events는 계속 Claude 이벤트다.
        "events": tools["claude"]["events"],
        "tools": tools,
    }


# N3(60-settings-palette.md §1) — 기기 스코프 설정. `/api/workspace`(전역)와
# 저장소가 완전히 분리된다 — device_settings.py 모듈 주석 참고. GET은
# 인증 없이도 device_id를 매길 수 있어야 부팅 초기(설정 로드가 세션 확인보다
# 먼저 끝날 수 있다)에도 막히지 않는다 — 다른 상태 없는 GET들(capabilities 등)
# 과 같은 수준의 노출이라 별도 승격이 필요 없다(D16의 git 쓰기와는 다르다 —
# 이건 그 기기 자신의 UI 취향일 뿐, 다른 기기·계정에 영향이 없다).
@router.get("/api/device-settings")
async def device_settings_get(request: Request):
    did = device_settings.device_id_for(request.cookies.get("vt_device", ""))
    profile = request.query_params.get("profile")
    if profile is not None:
        if profile not in device_settings.PROFILES:
            return JSONResponse({"error": "unknown device profile"}, status_code=400)
        settings = device_settings.load_profile(profile, did)
        return {"device_id": device_settings.profile_id(profile), "profile": profile, "settings": settings}
    return {"device_id": did, "settings": device_settings.load(did)}


@router.put("/api/device-settings")
async def device_settings_put(request: Request):
    did = device_settings.device_id_for(request.cookies.get("vt_device", ""))
    body = await request.json()
    values = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(values, dict):
        return JSONResponse({"error": "settings must be an object"}, status_code=400)
    profile = request.query_params.get("profile")
    if profile is not None:
        if profile not in device_settings.PROFILES:
            return JSONResponse({"error": "unknown device profile"}, status_code=400)
        device_settings.load_profile(profile, did)
        did = device_settings.profile_id(profile)
    merged = device_settings.save(did, values)
    return {"ok": True, "device_id": did, "settings": merged}


# 화면 언어 — 서버 전역 하나(server/i18n.py). 읽기는 로그인 전에도 필요해서
# /api/auth/status와 index.html이 대신 싣는다. 여기는 바꾸기만(인증 필요).
@router.put("/api/lang")
async def lang_put(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    lang = body.get("lang") if isinstance(body, dict) else None
    if lang not in i18n.LANGS:
        return JSONResponse({"error": "lang must be one of: " + ", ".join(i18n.LANGS)}, status_code=400)
    try:
        await asyncio.to_thread(i18n.set_lang, lang)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("[i18n] VT_LANG write failed: %s", e)
        return JSONResponse({"error": "failed to save language"}, status_code=500)
    return {"ok": True, "lang": i18n.current()}


async def broadcast_workspace_event(msg: dict) -> None:
    """`/ws-workspace`에 붙은 모든 클라이언트로 push. workspace_put과 N8(워크트리
    변경) 양쪽이 공유하는 단일 브로드캐스트 경로 — 각자 클라이언트 집합을 순회하는
    코드를 복제하지 않는다."""
    dead = set()
    for ws in list(_workspace_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _workspace_clients.difference_update(dead)


@router.get("/api/workspace")
async def workspace_get():
    return workspace.load()


@router.put("/api/workspace")
async def workspace_put(request: Request):
    # L4에서 발견한 버그 수정: `request`에 타입 힌트가 없어서 FastAPI가 이걸
    # (Request 객체가 아니라) 필수 쿼리 파라미터 "request"로 해석했다 —
    # 그래서 body로 JSON을 보내면 매번 422가 났다. `/api/workspace`를 실제로
    # 쓰는 프런트 코드가 이전엔 하나도 없어서(이 라우트를 만든 뒤로 아무도
    # PUT을 안 불러봤다) 이 라우트가 생긴 이후 계속 고장나 있었을 가능성이
    # 높다 — rail.js(L4)가 처음으로 실제 호출하면서 실브라우저 검증 중 발견.
    # 파일 상단에 이미 `from fastapi import ... Request ...`가 있어 여기 있던
    # 함수 내부의 중복 import도 함께 정리했다.
    data = await request.json()
    merged = workspace.update(data)

    await broadcast_workspace_event({"type": "workspace_updated", "data": merged})
    return {"ok": True, "data": merged}


# ── tmux 세션 목록 push (2026-09-30) ─────────────────────────────────────────
# 레일·플릿이 5초마다, agent/status.js가 20초마다 GET /api/tmux/sessions를
# 부르고 있었다(86분에 952회 — 호스트 전환 중이면 중계까지 두 번). 서버 쪽
# 감시 하나로 모아 **바뀌었을 때만** `tmux_sessions`를 쏜다. 서버를 거친 변경
# (생성·종료·이름 변경·붙기)은 그 자리에서 kick_sessions()로 바로 확인하고,
# (2026-09-30 이후 이 역할은 tmux 훅이 맡고, 손으로 부르는 곳은 훅이 못 보는
#  서버 메모리 변화 — 워크트리 표시·웹 세션 등록 — 뒤에만 남았다),
# 터미널에서 직접 `tmux new` 한 것처럼 서버를 안 거친 변경은 주기가 잡는다.
# 2026-09-30 — 이제 tmux 훅(tmux_hooks.py)이 생성·종료·이름 변경·붙기·떨어지기를
# 모든 경로에서 바로 알린다(서버를 안 거친 `tmux new`까지).
# 현재 cwd·브랜치·변경량도 같은 snapshot으로 밀어 준다(2026-10-07).
# 외부 cd/checkout은 tmux 훅이 없으므로 기본 5초 안전망으로 본다. Git은 cwd별
# 10초 캐시·동시 조회 합치기를 쓰고, 숨은 화면은 기존처럼 쉰다.
_SESSIONS_PUSH_INTERVAL = float(os.environ.get("VT_SESSIONS_PUSH_INTERVAL", "5.0"))
_hooks_installed = False
_sessions_task: asyncio.Task | None = None
_sessions_kick: asyncio.Event | None = None
_sessions_last: list | None = None
# 화면이 백그라운드라고 알린 연결. 예전 폴링은 `document.hidden`이면 쉬었는데,
# 서버 감시는 연결만 보면 계속 돌아 백그라운드 탭에서 오히려 일이 늘었다(실측
# 120초 서버 CPU 0.10s → 0.88s). 보이는 화면이 하나도 없으면 감시도 쉰다.
# 알리지 않는 옛 화면은 보이는 것으로 친다(예전 동작 그대로).
_workspace_hidden: set[WebSocket] = set()


def _any_visible(clients: set, hidden: set) -> bool:
    return any(ws not in hidden for ws in clients)


def handle_visibility(ws, hidden_set: set, raw: str, on_visible) -> None:
    """`{"type":"visibility","hidden":bool}` 메시지를 반영한다. 다시 보이게 되면
    on_visible()로 쉬는 동안의 변화를 바로 따라잡게 한다. 다른 메시지는 무시."""
    try:
        msg = _json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(msg, dict) or msg.get("type") != "visibility":
        return
    if msg.get("hidden") is True:
        hidden_set.add(ws)
    else:
        was_hidden = ws in hidden_set
        hidden_set.discard(ws)
        if was_hidden:
            on_visible()


def kick_sessions() -> None:
    if _sessions_kick is not None:
        _sessions_kick.set()


async def _install_tmux_hooks() -> None:
    # 서버 시작 때 tmux 서버가 아직 없었으면 설치가 실패한다 — 화면이 붙어 감시가
    # 시작될 때마다 한 번 더 시도하고, 성공하면 그만한다.
    global _hooks_installed
    try:
        _hooks_installed = await asyncio.to_thread(tmux_hooks.install)
    except Exception as e:  # noqa: BLE001 — 훅이 없어도 주기가 메운다
        logger.debug(f"tmux 훅 설치 실패: {e}")


def _ensure_sessions_loop() -> None:
    global _sessions_task, _sessions_kick
    if not _hooks_installed:
        asyncio.create_task(_install_tmux_hooks())
    if _sessions_task is not None and not _sessions_task.done():
        return
    # Event는 처음 기다리는 루프에 묶인다 — 새 태스크마다 새로 만든다.
    _sessions_kick = asyncio.Event()
    _sessions_task = asyncio.create_task(_sessions_loop(_sessions_kick))


async def _sessions_loop(kick: asyncio.Event) -> None:
    global _sessions_last
    from routes.tmux import collect_tmux_sessions
    while _workspace_clients:
        try:
            await asyncio.wait_for(kick.wait(), _SESSIONS_PUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        kick.clear()
        if not _workspace_clients:
            break
        if not _any_visible(_workspace_clients, _workspace_hidden):
            continue
        try:
            cur = await collect_tmux_sessions()
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시 본다
            logger.debug(f"tmux 세션 목록 실패: {e}")
            continue
        if cur == _sessions_last:
            continue
        _sessions_last = cur
        await broadcast_workspace_event({"type": "tmux_sessions", "sessions": cur})


@router.post("/api/tmux/kick", status_code=204)   # = tmux_hooks.KICK_PATH (문서 검사가 문자열로 찾는다)
async def tmux_kick(request: Request):
    """tmux 훅 전용 — 세션 목록이 바뀌었으니 바로 확인하라는 신호. 일반 인증 대신
    서버가 시작할 때 만든 비밀값을 확인한다(main.py 예외 목록, docs/ref/auth.md).
    신호는 합쳐지므로 여러 번 와도 확인은 한 번이다."""
    if not tmux_hooks.verify(request.headers.get(tmux_hooks.HEADER)):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    kick_sessions()
    return Response(status_code=204)


@router.websocket("/ws-workspace")
async def ws_workspace(websocket: WebSocket):
    # codex review fix: VT_TOKEN 보호
    from routes.pty import _ws_auth_token
    import auth
    token = _ws_auth_token(websocket)
    if token is None:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _workspace_clients.add(websocket)
    _ensure_sessions_loop()
    session_watchdog = auth.spawn_session_watchdog(websocket, token)
    try:
        await websocket.send_json({"type": "workspace_snapshot", "data": workspace.load()})
        # 재연결이면 끊긴 동안의 변경을 놓쳤을 수 있다 — 지금 목록을 이 연결에만 준다.
        # (_sessions_last는 건드리지 않는다: 다른 연결들은 마지막 브로드캐스트 값을 들고 있다.)
        from routes.tmux import collect_tmux_sessions
        await websocket.send_json({"type": "tmux_sessions", "sessions": await collect_tmux_sessions()})
        while True:
            handle_visibility(websocket, _workspace_hidden, await websocket.receive_text(), kick_sessions)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session_watchdog.cancel()
        _workspace_hidden.discard(websocket)
        _workspace_clients.discard(websocket)


# ---------------------------------------------------------------------------
# 노출 포트 session 모드 핸드셰이크 (2.2, plan-2.2/00-port-access.md §3)
# ---------------------------------------------------------------------------
#
# 노출 포트는 메인 터널과 **다른 호스트**라 `vt_session` 쿠키가 거기로 전송되지
# 않는다(host-only). 파일 공유의 device 모드는 같은 origin(`/s/{token}`)이라
# 이 문제가 없었다. 그래서 여기서 로그인·기기를 확인하고, 그 결과를 1회용
# 티켓으로 바꿔 노출 호스트로 넘긴다.
#
# **오픈 리다이렉트를 구조로 막는다.** 이 라우트는 목적지 파라미터를 받지
# 않는다 — `share`(= shareId)만 받고, 실제 주소는 서버가 레지스트리에서 읽는다.
# 클라이언트가 목적지에 영향을 줄 수단이 없으면 검증 로직도 필요 없다.

PORT_TICKET_TTL = 60  # 기기 등록 티켓(5분)과 별개 — URL에 실리므로 창을 좁힌다


@router.get("/p/authorize")
async def port_share_authorize(request: Request):
    from urllib.parse import quote

    import auth
    import port_share_store as pshare
    import tunnel_registry
    from fastapi.responses import RedirectResponse
    from routes.port_proxy_app import safe_next

    share_id = request.query_params.get("share", "")
    next_path = safe_next(request.query_params.get("next"))

    share = await asyncio.to_thread(pshare.find_by_share_id, share_id)
    if share is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if share.get("mode") != "session":
        return JSONResponse({"error": "not_session_mode"}, status_code=400)

    # 미로그인/미등록 기기면 기존 로그인 게이트로 보낸다. 돌아올 주소는 지금
    # 이 요청 그대로 — frontend/js/gate.js가 `?next=`를 따라 재진입시킨다.
    device = auth.verify_device(request.cookies.get("vt_device", ""))
    session_cookie = request.cookies.get("vt_session", "")
    if not (device and auth.verify_session(session_cookie)):
        back = f"/p/authorize?share={quote(share_id, safe='')}&next={quote(next_path, safe='')}"
        return RedirectResponse(f"/?next={quote(back, safe='')}", status_code=302)

    # 목적지는 **레지스트리에서** 읽는다. 클라이언트 입력이 아니다.
    tunnels = await asyncio.to_thread(tunnel_registry.exposed_ports)
    entry = tunnels.get(share["port"])
    if not entry or not entry.get("url"):
        return JSONResponse(
            {"error": "tunnel_not_found",
             "reason": "이 포트의 공개 주소를 찾을 수 없습니다. 노출이 내려갔을 수 있습니다."},
            status_code=404,
        )

    # 티켓을 share와 기기에 묶는다 — 다른 share로는 못 쓰고, 기기를 폐기하면
    # 그 티켓으로 받은 쿠키까지 함께 죽는다(port_proxy_app.verify_cookie).
    device_id = device.get("id") or "-"
    ticket = await asyncio.to_thread(
        auth.issue_ticket, "port-share", PORT_TICKET_TTL,
        share=share["shareId"], device=device_id,
    )
    target = (
        f"{entry['url'].rstrip('/')}/__fsh/cb"
        f"?t={quote(ticket, safe='')}&next={quote(next_path, safe='')}"
    )
    return RedirectResponse(target, status_code=302)
