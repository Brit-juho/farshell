"""포트 대시보드 — 리스닝 포트 조회 / 종료 / 터널 노출 (P3).

blocking 호출(lsof, ps, kill 대기, vt tunnel)은 전부 asyncio.to_thread로 offload한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import auth
from i18n import tr
import port_proxy
import port_share_store as pshare
import portscan
import tunnel
import tunnel_registry
from routes.port_proxy_app import invalidate_share

logger = logging.getLogger(__name__)

router = APIRouter()

# 노출을 켜고 끄고 자격을 바꾸는 것은 전부 **승격**을 요구한다(plan-2.2 §5).
# 포트를 공개 인터넷에 여는 것은 공유 링크 발급과 같은 등급의 쓰기 액션이고,
# routes/share.py의 elevated_router가 이미 같은 판단을 했다. 라우터 단위로 묶는
# 이유는 핸들러마다 붙이면 하나쯤 빠뜨리기 때문이다(R4).
elevated_router = APIRouter(dependencies=[Depends(auth.require_elevated)])

# fsh tunnel expose 는 cloudflared 기동 + URL 확보까지 최대 30초를 기다린다(bin/fsh:305, _start_extra_tunnel).
VT_TIMEOUT = 45.0


@router.get("/api/ports")
async def list_ports(fresh: bool = Query(False)):
    result = await asyncio.to_thread(portscan.scan, not fresh)
    # L5: 이미 공개 터널이 열려 있는 포트는 행에 tunnel_url을 얹어준다 — ports.js가
    # 이걸로 "공개" 대신 "미리보기" 버튼을 보여준다.
    tunnels = await asyncio.to_thread(tunnel_registry.exposed_ports)
    shares = {s["port"]: s for s in await asyncio.to_thread(pshare.list_shares)}
    # 프록시 리스너는 우리가 띄운 내부 포트다 — 목록에 섞이면 사용자가 자기
    # 공유를 끊는 kill 버튼을 누르게 된다. portscan이 보호도 함께 건다.
    internal = port_proxy.proxy_ports()
    result["ports"] = [r for r in result["ports"] if r["port"] not in internal]
    for row in result["ports"]:
        t = tunnels.get(row["port"])
        if t:
            row["tunnel_url"] = t["url"]
        s = shares.get(row["port"])
        if s:
            row["share"] = pshare.public_view(s)
    return result


@router.delete("/api/ports/{port}")
async def kill_port(port: int, pid: int = Query(None)):
    if port < 1 or port > 65535:
        return JSONResponse({"ok": False, "error": "bad_port"}, status_code=400)
    result = await asyncio.to_thread(portscan.kill_port, port, pid)
    if result.get("ok"):
        return result
    status = {
        "protected": 403,
        "permission": 403,
        "not_found": 404,
        "pid_changed": 409,
    }.get(result.get("error"), 500)
    return JSONResponse(result, status_code=status)


# --- 터널 노출 --------------------------------------------------------------
# 이 엔드포인트는 로컬 개발 서버를 '공개 인터넷'에 연다. P1에서 접근 범위를 tailnet으로
# 좁혀놓고 여기서 다시 뚫으면 의미가 없으므로, 두 겹으로 막는다:
#   - confirm=true 를 본문에 명시해야 실행된다(오탭 방지)
#   - VT_NETWORK_MODE 가 all 이 아니면 아예 거부하고 직접 접근을 안내한다


def _vt_bin() -> Path | None:
    vt_dir = os.environ.get("VT_DIR")
    if not vt_dir:
        return None
    p = Path(vt_dir) / "bin" / "fsh"
    return p if p.is_file() else None


def _run_vt(*args: str) -> tuple[int, str]:
    vt = _vt_bin()
    if vt is None:
        return 127, tr("fsh CLI를 찾을 수 없습니다 (VT_DIR 미설정)")
    try:
        p = subprocess.run(["bash", str(vt), *args], capture_output=True, timeout=VT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, tr("시간 초과")
    except (OSError, ValueError) as e:
        return 127, str(e)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


def _first_url(out: str) -> str:
    for tok in out.split():
        if tok.startswith("https://"):
            return tok
    return ""


@elevated_router.post("/api/ports/{port}/expose")
async def expose_port(port: int, request: Request):
    """노출을 켠다 — 정책 기록 → 프록시 리스너 → cloudflared 순서로.

    2.2부터 cloudflared는 앱이 아니라 **우리 프록시**를 향한다(ADR-30). 그래서
    proxyPort를 먼저 확보하고 리스너를 띄운 뒤에야 터널을 연다. 중간에 실패하면
    **전부 되감는다** — 터널 없는 share나 share 없는 터널이 남으면 그 상태가
    조용히 틀린 채로 굴러간다.
    """
    if port < 1 or port > 65535:
        return JSONResponse({"ok": False, "error": "bad_port"}, status_code=400)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not body.get("confirm"):
        return JSONResponse(
            {"ok": False, "error": "confirm_required",
             "reason": tr("이 포트를 외부에서 접근 가능하게 만듭니다. confirm=true 로 다시 요청하세요.")},
            status_code=428,
        )

    # 공개하면 그 자체가 원격 제어 수단이 되는 포트(port_share_store.danger_reason).
    # 막지 않고 한 번 더 확인받는다 — CLI의 --force와 같은 등급. confirm과 따로 두는
    # 이유: confirm은 "공개 인터넷에 연다"는 일반 확인이고, 이건 "이 포트는 특히
    # 위험하다"는 별도 사실이라 화면이 그 이유를 보여 줘야 한다.
    danger = pshare.danger_reason(port)
    if danger and body.get("force_danger") is not True:
        return JSONResponse(
            {"ok": False, "error": "dangerous_port", "reason": danger,
             "hint": tr("정말 노출하려면 force_danger=true 로 다시 요청하세요.")},
            status_code=409,
        )
    if danger:
        logger.warning(f"[ports] 위험 포트 노출 확인됨: {port} — {danger}")

    net_mode = os.environ.get("VT_NETWORK_MODE", "all")
    if net_mode != "all":
        # 인증이 붙은 모드(session/pin)도 함께 막는다 — "접근 범위를 좁혀놓고
        # 거기서 다시 뚫지 않는다"는 원래 의도를 지키는 쪽(plan §10-3).
        return JSONResponse(
            {"ok": False, "error": "network_mode",
             "reason": tr("접근 범위가 '{mode}'로 제한된 상태입니다. 공개 터널을 여는 대신 같은 망에서 직접 접속하세요.", mode=net_mode)},
            status_code=409,
        )

    # 값이 **없으면** 기본값, 있으면 반드시 검증을 통과해야 한다. `or` 한 방으로
    # 합치면 `ttl: 0`이나 `mode: ""` 같은 명시적 오류가 조용히 기본값이 된다.
    raw_mode = body.get("mode")
    share_mode = "session" if raw_mode is None else str(raw_mode)  # 기본 = 로그인 기기만 (ADR-32)
    if share_mode not in pshare.VALID_MODES:
        return JSONResponse({"ok": False, "error": "bad_mode"}, status_code=400)
    raw_ttl = body.get("ttl")
    try:
        ttl = 3600 if raw_ttl is None else int(raw_ttl)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad_ttl"}, status_code=400)
    if ttl not in pshare.VALID_TTLS:
        return JSONResponse({"ok": False, "error": "bad_ttl"}, status_code=400)

    # 자동 재노출은 사용자가 고르는 값이라 bool만 받는다. "true" 같은 문자열을
    # 참으로 넘기면 의도하지 않은 무기한 노출이 조용히 켜질 수 있다.
    raw_auto = body.get("auto_renew")
    if raw_auto is not None and not isinstance(raw_auto, bool):
        return JSONResponse({"ok": False, "error": "bad_auto_renew"}, status_code=400)
    auto_renew = bool(raw_auto)

    label = str(body.get("label") or f"localhost:{port}")[:64]

    share, pin = await asyncio.to_thread(pshare.create_share, port, label, share_mode, ttl, auto_renew)
    invalidate_share(port)
    if not await port_proxy.start(share):
        await asyncio.to_thread(pshare.remove_share, port)
        return JSONResponse(
            {"ok": False, "error": "proxy_bind_failed",
             "reason": tr("내부 프록시 포트 {port} 를 열 수 없습니다.", port=share['proxyPort'])},
            status_code=500,
        )

    vt_args = ["tunnel", "expose", str(port), label, "--proxy-port", str(share["proxyPort"])]
    rc, out = await asyncio.to_thread(_run_vt, *vt_args)
    if rc != 0:
        await port_proxy.stop(port)
        await asyncio.to_thread(pshare.remove_share, port)
        return JSONResponse({"ok": False, "error": "expose_failed", "reason": out.strip()},
                            status_code=500)

    return {
        "ok": True, "port": port, "url": _first_url(out), "mode": share_mode,
        "share": pshare.public_view(share),
        # PIN 원문이 나가는 **유일한** 응답이다. 저장은 해시뿐이라 이후 어떤
        # 조회로도 다시 꺼낼 수 없다 — 잊으면 재발급이다.
        "pin": pshare.format_pin(pin) if pin else None,
        "output": out.strip(),
    }


@elevated_router.delete("/api/ports/{port}/expose")
async def unexpose_port(port: int):
    await port_proxy.stop(port)
    await asyncio.to_thread(pshare.remove_share, port)
    invalidate_share(port)
    rc, out = await asyncio.to_thread(_run_vt, "tunnel", "unexpose", str(port))
    if rc != 0:
        return JSONResponse({"ok": False, "error": "unexpose_failed", "reason": out.strip()},
                            status_code=500)
    return {"ok": True, "port": port, "output": out.strip()}


@elevated_router.post("/api/ports/{port}/share/pin")
async def reissue_pin(port: int):
    """PIN만 새로 발급 — 터널도 proxyPort도 건드리지 않으므로 **공개 URL이 산다.**"""
    share = await asyncio.to_thread(pshare.get_share, port)
    if share is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if share.get("mode") != "pin":
        return JSONResponse(
            {"ok": False, "error": "not_pin_mode",
             "reason": tr("PIN 모드가 아닌 노출에는 코드가 없습니다.")},
            status_code=409,
        )
    result = await asyncio.to_thread(pshare.regenerate_pin, port)
    if result is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    updated, pin = result
    invalidate_share(port)
    return {"ok": True, "port": port, "pin": pshare.format_pin(pin),
            "share": pshare.public_view(updated)}


@elevated_router.patch("/api/ports/{port}/share")
async def change_share_mode(port: int, request: Request):
    """모드·자동 재노출 변경 — 터널을 재시작하지 않으므로 주소가 죽지 않는다(ADR-30의 이득).

    `mode`와 `auto_renew` 중 하나 이상을 받는다. `auto_renew`만 바꾸면 자격(shareId·PIN)은
    그대로다 — 들어와 있던 사람을 끊을 이유가 없다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    has_mode = body.get("mode") is not None
    raw_auto = body.get("auto_renew")
    if raw_auto is not None and not isinstance(raw_auto, bool):
        return JSONResponse({"ok": False, "error": "bad_auto_renew"}, status_code=400)
    mode = str(body.get("mode") or "")
    if (has_mode or raw_auto is None) and mode not in pshare.VALID_MODES:
        return JSONResponse({"ok": False, "error": "bad_mode"}, status_code=400)

    share = await asyncio.to_thread(pshare.get_share, port)
    if share is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    if not has_mode:
        updated = await asyncio.to_thread(pshare.set_auto_renew, port, raw_auto)
        return {"ok": True, "port": port, "mode": updated.get("mode"), "pin": None,
                "share": pshare.public_view(updated)}

    pin = None
    if mode == "pin" and not share.get("pinHash"):
        # PIN 없이 pin 모드로 바꾸면 아무도 못 들어온다 — 그 상태를 만들지 않는다.
        result = await asyncio.to_thread(pshare.regenerate_pin, port)
        if result is not None:
            _updated, pin = result

    updated = await asyncio.to_thread(pshare.set_mode, port, mode)
    if raw_auto is not None:
        updated = await asyncio.to_thread(pshare.set_auto_renew, port, raw_auto)
    invalidate_share(port)
    return {"ok": True, "port": port, "mode": mode,
            "pin": pshare.format_pin(pin) if pin else None,
            "share": pshare.public_view(updated)}


# --- 노출 포트 섹션 (N22, 50-files-share.md §5) --------------------------------
#
# 포트 탭 하단에 "지금 열려 있는 것"만 모아 보여주는 요약. 해제는 새 엔드포인트를
# 만들지 않고 위의 기존 `DELETE /api/ports/{port}/expose`를 그대로 쓴다 — 이미
# 같은 일을 하는 라우트가 있는데 이름만 다른 걸 하나 더 만들면 그 자체가
# 유지보수 부담이 되는 레거시라, 목록 조회만 새로 추가한다.


@router.post("/api/ports/_proxy/sync")
async def sync_port_proxy():
    """실행 중인 서버에게 노출 정책 변경을 지금 반영하라고 알린다.

    **승격을 요구하지 않는다.** 이 호출 자체는 누가 접근할 수 있는지를 정하지
    않는다 — 그 결정은 이미 디스크의 `port_shares.json`에 적혀 있고(로컬
    파일시스템 접근은 이미 사용자 신뢰 수준이다), 여기서는 그 상태를 리스너에
    반영할 뿐이다. `fsh tunnel expose/pin/mode`가 `port_share_store` CLI로
    정책을 직접 쓴 뒤(§ CLI 참고) 이 엔드포인트로 서버를 깨운다 — 리스너는
    서버 프로세스 안에서만 뜰 수 있어서다.

    **무효화는 sync 전후 포트의 합집합에 건다.** sync **후** 살아있는 share만
    무효화하면, "지금 막 제거된" 포트(예: `fsh tunnel unexpose` 직후 같은 포트를
    다른 모드로 재노출)는 무효화 대상에서 빠진다. 그 자리에 새 share가 같은
    proxyPort로 재할당되면(포트 번호는 재사용된다), 프록시 앱의 `_shares`
    TTLCache(2초)가 옛 share를 그대로 들고 있어 — 실측(2026-09-22, 로컬
    E2E 스모크): `expose(public)` → `unexpose` → 즉시 `expose(pin)`을 캐시
    TTL 안에 연달아 호출하면 **PIN 게이트를 건너뛰고 그대로 통과**했다. 이건
    HTTP 승격 경로(routes/ports.py의 expose/unexpose 핸들러들)에는 없는
    구멍이다 — 그쪽은 변경한 포트를 그 자리에서 바로 invalidate하기 때문이다.
    CLI 경로(`fsh tunnel ...`)만 이 범용 sync 엔드포인트를 거치므로 여기서
    막아야 한다.
    """
    before = {row["port"] for row in port_proxy.status()}
    result = await port_proxy.sync()
    after = {s["port"] for s in await asyncio.to_thread(pshare.list_shares)}
    for port in before | after:
        invalidate_share(port)
    return {"ok": True, **result}


# --- 만료된 노출 정리 (2026-10) ---------------------------------------------------
#
# 만료된 share는 프록시가 404로 닫지만, 예전엔 cloudflared와 리스너가 계속 떠 있었다
# (URL이 살아 있어 "닫혔는지"를 바깥에서 알 수 없다). 이제 주기적으로 터널까지 내린다.

SWEEP_INTERVAL_SEC = 60.0


async def sweep_expired_shares() -> list[int]:
    """만료된 노출을 터널·리스너·정책까지 내린다. 내린 포트 목록을 돌려준다.

    자동 재노출이 켜진 정책은 내리지 않고 만료만 `ttl`만큼 연장한다(자격 유지).
    """
    done: list[int] = []
    for stale in await asyncio.to_thread(pshare.expired_shares):
        port = stale["port"]
        # 그사이 같은 포트가 새로 노출됐으면(새 shareId·새 만료) 건드리지 않는다.
        current = next((s for s in await asyncio.to_thread(pshare.list_shares, True)
                        if s["port"] == port), None)
        if current is None or current.get("shareId") != stale.get("shareId") \
                or not pshare.is_expired(current):
            continue
        if current.get("autoRenew"):
            if await asyncio.to_thread(pshare.renew_share, port, current["shareId"]):
                invalidate_share(port)
                logger.info(f"[ports] 자동 재노출: 포트 {port} 만료 연장")
                continue
        await port_proxy.stop(port)
        invalidate_share(port)
        rc, out = await asyncio.to_thread(_run_vt, "tunnel", "unexpose", str(port))
        if rc != 0:
            logger.warning(f"[ports] 만료된 노출 {port}의 터널 종료 실패: {out.strip()[:200]}")
        # unexpose가 정책도 지우지만, CLI를 못 찾은 경우에도 정책은 남기지 않는다.
        await asyncio.to_thread(pshare.remove_share, port)
        logger.info(f"[ports] 만료된 노출 정리: 포트 {port}")
        done.append(port)
    return done


async def restore_auto_renew_tunnels() -> list[int]:
    """자동 재노출 정책인데 터널이 없는 포트를 다시 연다. 다시 연 포트 목록을 돌려준다.

    재부팅·`fsh stop`·cloudflared 종료로 터널만 사라지고 정책은 남는다. 리스너는
    `port_proxy.sync()`가 정책을 보고 되살리므로, 여기서는 웹 노출과 같은 경로
    (`fsh tunnel expose --proxy-port`)로 터널만 다시 띄운다. 익명 터널이라 주소는
    새로 받고, 새 주소는 URL 변경 훅(`expose` 이벤트)으로 알린다.
    """
    if os.environ.get("VT_NETWORK_MODE", "all") != "all":
        return []  # 접근 범위를 좁힌 상태에서 공개 터널을 다시 뚫지 않는다(expose와 같은 규칙)
    shares = await asyncio.to_thread(pshare.auto_renew_shares)
    if not shares:
        return []
    alive = await asyncio.to_thread(tunnel_registry.exposed_ports)
    restored: list[int] = []
    for share in shares:
        port = share["port"]
        if port in alive:
            continue
        if not await port_proxy.start(share):
            logger.warning(f"[ports] 자동 재노출 {port}: 프록시 리스너를 열 수 없음")
            continue
        rc, out = await asyncio.to_thread(
            _run_vt, "tunnel", "expose", str(port), share.get("label") or f"localhost:{port}",
            "--proxy-port", str(share["proxyPort"]))
        if rc != 0:
            logger.warning(f"[ports] 자동 재노출 {port}: 터널 재시작 실패: {out.strip()[:200]}")
            continue
        logger.info(f"[ports] 자동 재노출: 포트 {port} 터널 복구 {_first_url(out)}")
        restored.append(port)
    return restored


async def expiry_sweep_loop() -> None:
    """main.py lifespan이 띄운다. 예외로 루프가 죽지 않게 매 회 감싼다.

    만료 정리 다음에 자동 재노출 복구를 돈다 — 기동 직후 한 번, 이후 1분마다.
    """
    while True:
        for step, name in ((sweep_expired_shares, "만료 노출 정리"),
                           (restore_auto_renew_tunnels, "자동 재노출 복구")):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[ports] {name} 중 오류: {e}")
        await asyncio.sleep(SWEEP_INTERVAL_SEC)


@router.get("/api/tunnel/list")
async def list_tunnels():
    main_status = await asyncio.to_thread(tunnel.get_tunnel_status)
    extra = await asyncio.to_thread(tunnel_registry.exposed_ports)
    main = None
    if main_status["running"] and main_status["url"]:
        main = {
            "port": int(os.environ.get("VT_PORT", "7777")),
            "label": tr("터미널 (VT)"),
            "url": main_status["url"],
            "named": main_status["mode"] == "named",
        }
    shares = {s["port"]: s for s in await asyncio.to_thread(pshare.list_shares)}
    extra_rows = []
    for port, info in sorted(extra.items()):
        row = {"port": port, "label": info["label"], "url": info["url"], "named": False}
        s = shares.get(port)
        if s:
            # mode·broken은 UI가 "누가 열 수 있나"와 "지금 고장났나"를 말하는 근거다.
            # public_view가 pinHash를 뺀다 — 이 라우트는 승격을 요구하지 않는다.
            row["share"] = pshare.public_view(s)
            row["mode"] = s.get("mode")
            row["broken"] = bool(s.get("broken"))
        extra_rows.append(row)
    return {"main": main, "extra": extra_rows, "count": (1 if main else 0) + len(extra_rows)}
