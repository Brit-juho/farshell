"""공유 링크 (N21, 50-files-share.md §2·§3) — device 모드 / PIN 모드.

두 라우터가 이 파일에 함께 있다:
  - `/api/files/{id}/share*` — 발급/취소. **승격 필요**(require_elevated) — 공유
    링크는 누구나 그 파일을 받아갈 수 있게 만드는 행위라 git stage/commit과
    같은 급의 쓰기 액션이다.
  - `/s/{token}` · `/s/{token}/pin` — 공개 다운로드 진입점. main.py의
    TokenAuthMiddleware를 우회하지만(경로 자체가 로그인 세션이 없는 사람에게
    열려야 하니까) **그 대신 이 파일이 토큰 서명·만료·모드별 인가를 전부 직접 한다.**

토큰 형식: `v1.<exp>.<fileId>.<shareId>.<hmac>` — auth.sign_payload(세션 쿠키와 같은 HMAC
키)로 서명한다. 서명이 유효해도 `files.json`의 shares[]에서 취소(제거)됐으면
file_store.get_item_and_share가 None을 돌려주므로 그대로 404가 된다(§3 요구사항).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import auth
from i18n import tr
from authz import preauth as authz_preauth
import file_store
import fsguard
from routes.files import _file_public
from resilience import fsops

logger = logging.getLogger(__name__)

# 발급/취소는 승격 필요 — routes/git_accounts.py의 elevated_router와 같은 패턴.
elevated_router = APIRouter(dependencies=[Depends(auth.require_elevated)])
public_router = APIRouter()  # /s/* — TokenAuthMiddleware 우회 대상(main.py 참고)

VALID_TTLS = {3600, 86400, 604800, 2592000}  # 1h/24h/7d/30d (50-files-share.md §3)
DL_COOKIE_TTL = 60  # PIN 통과 후 실제 다운로드까지 허용하는 1회용 쿠키 수명(초)


def _no_store_headers(resp) -> None:
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"


# --- 발급/취소 (승격) ---------------------------------------------------------


class _ImportError(Exception):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body


def _snapshot_into_store(p: Path) -> dict:
    """열람 경계 안의 파일 하나를 저장소로 **복사**한다(스냅샷). blocking — to_thread로 부른다.

    공유 링크는 id 기반 저장소 파일에만 걸린다(경로를 받는 다운로드 API는 의도적으로 없다).
    그래서 파일 탭의 파일은 복사본을 만들어 그 id로 링크를 발급한다. 같은 원본·크기·mtime이면
    같은 스냅샷을 재사용해 디스크에 두 벌 쌓이지 않는다(내용이 바뀌면 새 스냅샷).
    """
    # resolve() 뒤의 바꿔치기를 막는다 — 마지막 조각만이 아니라 중간 디렉토리를 심링크로 바꿔도 경계 밖이 열리지
    # 않도록 resolve된 경로를 한 조각씩 NOFOLLOW로 따라 연다(fsguard.open_resolved). 판정은 fd로 한다.
    try:
        fd = fsguard.open_resolved(p)
    except FileNotFoundError:
        raise _ImportError(404, {"error": "not_found"})
    except PermissionError:
        raise _ImportError(403, {"error": "denied", "reason": tr("읽기 권한이 없습니다")})
    except OSError:
        # NotRegularFile(디렉토리·FIFO) 또는 판정 뒤에 끼어든 심링크(ELOOP/ENOTDIR)
        raise _ImportError(400, {"error": "not_a_file", "reason": tr("일반 파일만 공유할 수 있습니다")})
    try:
        st = os.fstat(fd)
        max_mb = file_store.MAX_UPLOAD_BYTES // (1024 * 1024)
        if st.st_size > file_store.MAX_UPLOAD_BYTES:
            raise _ImportError(413, {"error": "file too large", "max_mb": max_mb})
        origin = f"fs:{p}:{st.st_size}:{st.st_mtime_ns}"
        existing = file_store.find_by_origin(origin)
        if existing is not None and file_store.real_path_for(existing["id"]) is not None:
            return existing
        file_store.files_dir().mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(file_store.files_dir(), 0o700)
        except OSError:
            pass
        tmp = file_store.files_dir() / f".tmp-{uuid.uuid4().hex}"
        size = 0
        try:
            out_fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(out_fd, "wb") as out, os.fdopen(fd, "rb", closefd=False) as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    # 복사 도중 커지는 파일 — 상한을 스트리밍으로 다시 건다.
                    if size > file_store.MAX_UPLOAD_BYTES:
                        raise _ImportError(413, {"error": "file too large", "max_mb": max_mb})
                    out.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return file_store.add_from_upload(tmp, p.name, size, origin=origin)
    finally:
        os.close(fd)


@elevated_router.post("/api/fs/share-import")
async def share_import(request: Request):
    """파일 탭의 경로를 공유할 수 있게 저장소로 복사하고 그 파일 id를 돌려준다.

    열람 경계는 `/api/fs/file`과 같은 fsguard를 쓴다(루트·거부 목록·숨김 디렉토리).
    승격 필요 — 공유 링크 발급과 같은 급의 동작이다."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    path = body.get("path") if isinstance(body, dict) else None
    if not isinstance(path, str) or not path.strip():
        return JSONResponse({"error": "bad_path"}, status_code=400)
    try:
        p = await fsops.call(fsguard.resolve_under_roots, path, path=path, op="share.import.resolve")
    except fsguard.FsDenied as e:
        return JSONResponse({"error": "denied", "reason": e.reason}, status_code=403)
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)
    try:
        item = await asyncio.to_thread(_snapshot_into_store, p)
    except _ImportError as e:
        return JSONResponse(e.body, status_code=e.status)
    except OSError as e:
        logger.warning(f"share-import 실패: {e}")
        return JSONResponse({"error": "write failed"}, status_code=500)
    return {"ok": True, "item": _file_public(item)}



@elevated_router.post("/api/files/{file_id}/share")
async def create_share(file_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = body.get("mode")
    ttl = body.get("ttl")
    once = bool(body.get("once", False))
    pin = body.get("pin")
    if mode not in file_store.VALID_SHARE_MODES:
        return JSONResponse({"error": "bad_mode"}, status_code=400)
    if ttl not in VALID_TTLS:
        return JSONResponse({"error": "bad_ttl"}, status_code=400)
    if mode == "pin" and not (isinstance(pin, str) and file_store.MIN_PIN_LEN <= len(pin) <= file_store.MAX_PIN_LEN):
        return JSONResponse({"error": "bad_pin"}, status_code=400)
    share = file_store.add_share(file_id, mode, ttl, once, pin=pin if mode == "pin" else None)
    if share is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    token = file_store.build_share_token(file_id, share)
    public = {k: v for k, v in share.items() if k not in ("pinHash", "pinSalt", "dl_cookie_hash", "dl_cookie_exp")}
    return {"ok": True, "share": public, "token": token, "url": f"/s/{token}"}


@elevated_router.delete("/api/files/{file_id}/share/{share_id}")
async def delete_share(file_id: str, share_id: str):
    ok = file_store.remove_share(file_id, share_id)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"ok": True}


# --- 토큰 파싱/검증 ------------------------------------------------------------


def _parse_token(token: str) -> Optional[tuple[str, str, int]]:
    """`v1.<exp>.<fileId>.<shareId>.<hmac>` → (fileId, shareId, exp). 무효면 None."""
    parts = token.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        return None
    _, exp_s, file_id, share_id, sig = parts
    payload = f"v1.{exp_s}.{file_id}.{share_id}"
    if not hmac.compare_digest(auth.sign_payload(payload), sig):
        return None
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp <= int(time.time()):
        return None
    return file_id, share_id, exp


def _resolve(token: str):
    """토큰 → (item, share) 또는 (None, None). 서명·만료·취소·파일존재를 전부 검사."""
    parsed = _parse_token(token)
    if parsed is None:
        return None, None
    file_id, share_id, _ = parsed
    item, share = file_store.get_item_and_share(file_id, share_id)
    if item is None or share is None:
        return None, None
    if share.get("exp", 0) <= time.time():
        return None, None
    if file_store.real_path_for(file_id) is None:
        return None, None
    return item, share


# --- PIN 페이지(정적, 크롬 없음) -----------------------------------------------

def _pin_page(token: str, error: str = "") -> str:
    err_html = f'<p class="err">{error}</p>' if error else ""
    title = tr("FarShell — 공유 파일")
    heading = tr("PIN을 입력하세요")
    submit = tr("확인")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/static/icon-192.png">
<title>{title}</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#111;color:#eee;display:flex;
  align-items:center;justify-content:center;min-height:100vh;margin:0}}
form{{background:#1c1c1c;padding:24px;border-radius:12px;width:min(320px,90vw)}}
h1{{font-size:15px;margin:0 0 16px}}
input{{width:100%;box-sizing:border-box;font-size:16px;padding:10px;border-radius:8px;
  border:1px solid #444;background:#000;color:#eee;letter-spacing:2px;text-align:center}}
button{{width:100%;margin-top:12px;padding:10px;border-radius:8px;border:none;
  background:#4a8;color:#000;font-weight:600}}
.err{{color:#f66;font-size:13px;margin:0 0 8px}}
</style></head><body>
<form method="post" action="/s/{token}/pin">
<h1>{heading}</h1>
{err_html}
<input name="pin" inputmode="numeric" autocomplete="off" autofocus maxlength="20">
<button type="submit">{submit}</button>
</form></body></html>"""


# --- 공개 다운로드 진입점 -------------------------------------------------------


async def _serve(item: dict, share: dict, request: Request) -> FileResponse:
    fp = file_store.real_path_for(item["id"])
    file_store.update_share(item["id"], share["shareId"], views=share.get("views", 0) + 1,
                             lastAccess=time.time())
    resp = FileResponse(str(fp), filename=item["name"], media_type=item.get("mime"))
    _no_store_headers(resp)
    if share.get("once"):
        file_store.remove_share(item["id"], share["shareId"])
    return resp


@public_router.get("/s/{token}")
async def share_download(token: str, request: Request):
    item, share = _resolve(token)
    if item is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if share["mode"] == "device":
        device = auth.verify_device(request.cookies.get("vt_device", ""))
        session_ok = auth.verify_session(request.cookies.get("vt_session", ""))
        if not (device and session_ok):
            return RedirectResponse(url=f"/?next=/s/{token}", status_code=302)
        return await _serve(item, share, request)

    # pin 모드 — PIN 통과 후 발급된 1회용 쿠키가 있으면 바로 다운로드.
    dl_cookie = request.cookies.get(f"vt_share_dl_{share['shareId']}", "")
    if dl_cookie and share.get("dl_cookie_hash") and share.get("dl_cookie_exp", 0) > time.time():
        if hmac.compare_digest(hashlib.sha256(dl_cookie.encode()).hexdigest(), share["dl_cookie_hash"]):
            resp = await _serve(item, share, request)
            resp.delete_cookie(f"vt_share_dl_{share['shareId']}", path=f"/s/{token}")
            return resp
    return HTMLResponse(_pin_page(token))


_PIN_FORM_MAX = 4096


async def _read_pin(request: Request) -> str | None:
    """`application/x-www-form-urlencoded` 본문에서 pin 만 읽는다. 4KB 를 넘으면 None.

    `request.form()` 은 본문 전체를 메모리에 올린다 — 로그인 전 공개 경로라 아무나 큰 본문을 보낼 수 있었다.
    PIN 은 20자 이하이니 4KB 면 충분하다(노출 포트 PIN 폼과 같은 상한)."""
    if int(request.headers.get("content-length") or 0) > _PIN_FORM_MAX:
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _PIN_FORM_MAX:
            return None
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
        return ""
    from urllib.parse import parse_qs
    values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True).get("pin", [""])
    return values[0]


@public_router.post("/s/{token}/pin")
async def share_pin_submit(token: str, request: Request):
    item, share = _resolve(token)
    if item is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if share["mode"] != "pin":
        return JSONResponse({"error": "not_pin_mode"}, status_code=400)

    source = authz_preauth.source_of(request)
    pin = await _read_pin(request)
    if pin is None:
        return JSONResponse({"error": "payload_too_large"}, status_code=413)

    # 시도를 **검증 전에, 락 안에서** 예약한다 — 동시에 몰려 와도 한도(5회)를 넘겨 검증되지 않는다(file_store).
    state, share = file_store.reserve_pin_attempt(item["id"], share["shareId"])
    if state != "ok":
        if state == "exhausted":
            logger.warning(f"[share] PIN 시도 초과로 공유 취소: file={item['id']}")
        return JSONResponse({"error": "not_found"}, status_code=404)

    expected = file_store.hash_pin(pin, share.get("pinSalt", ""))
    if not hmac.compare_digest(expected, share.get("pinHash", "")):
        authz_preauth.fail(source, "share_pin")   # 출처별 잠금(10회/60초 → 5분)에도 센다
        if share.get("attempts", 0) >= file_store.MAX_PIN_ATTEMPTS:
            file_store.remove_share(item["id"], share["shareId"])
            logger.warning(f"[share] PIN {file_store.MAX_PIN_ATTEMPTS}회 실패로 공유 취소: file={item['id']} share={share['shareId']}")
            return JSONResponse({"error": "not_found"}, status_code=404)
        return HTMLResponse(_pin_page(token, tr("PIN이 올바르지 않습니다")), status_code=401)

    secret = secrets.token_urlsafe(24)
    file_store.update_share(
        item["id"], share["shareId"],
        attempts=0,
        dl_cookie_hash=hashlib.sha256(secret.encode()).hexdigest(),
        dl_cookie_exp=time.time() + DL_COOKIE_TTL,
    )
    resp = RedirectResponse(url=f"/s/{token}", status_code=303)
    resp.set_cookie(
        f"vt_share_dl_{share['shareId']}", secret,
        httponly=True, samesite="strict", secure=auth.is_https(request),
        max_age=DL_COOKIE_TTL, path=f"/s/{token}",
    )
    return resp
