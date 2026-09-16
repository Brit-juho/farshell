"""peer capability 관문(`routes/peer_protocol.capability`).

## 왜 이 파일이 필요한가

원격 capability 핸들러는 전부 같은 네 단계를 밟는다 — 권한 검사 → 로컬 구현
재사용 → **응답 위생** → 감사 로그. 지금까지 그 넷은 핸들러마다 손으로
쓰여 있었고, 10개 전부 지켜지고 있었다. 문제는 2.2에 원격 기능이 넷 더
들어온다는 것이다(원격 파일 목록·호스트별 터널·원격 포트 대시보드·원격
워크트리 생성).

**위생을 빠뜨려도 아무 일도 안 일어난다.** 빠뜨린 채로 동작하고, 틀린 건
A쪽에서 한참 뒤에 드러난다 — B 안에서만 뜻이 있는 식별자를 A가 자기 것으로
오해하는 형태로. 그래서 관문을 두고, 여기서 **관문이 실제로 강제하는지**를
본다.

마지막 테스트는 소스 수준 검사다: 새 capability가 관문을 우회해도 그 모듈에
대한 테스트를 안 쓰면 행동 검사는 아무것도 못 잡기 때문이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

import host_store
from routes import peer_protocol


@pytest.fixture
def app(monkeypatch, tmp_path):
    """서명 검증을 통과시킨 상태에서 **관문 자체**만 본다.
    서명·nonce·등급은 test_routes_peer.py가 왕복으로 이미 검증한다."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    grants = {"peer-a": {"id": "peer-a", "level": host_store.LEVEL_CONTROL}}
    monkeypatch.setattr(peer_protocol, "_authenticate_raw",
                        lambda method, path, headers, body_hash="": grants["peer-a"])

    audits: list[tuple] = []
    monkeypatch.setattr(host_store, "audit",
                        lambda pid, action, ok, detail="": audits.append((pid, action, ok, detail)))

    api = FastAPI()

    @api.get("/t/strip")
    @peer_protocol.capability("t.strip", strip=("session_id",))
    async def strip_handler(request: Request, grant: dict):
        return {"results": [{"name": "a", "session_id": "B-local"}], "session_id": "top"}

    @api.get("/t/detail")
    @peer_protocol.capability("t.detail", strip=())
    async def detail_handler(request: Request, grant: dict):
        return {"ok": True}, "12건"

    @api.get("/t/refuse")
    @peer_protocol.capability("t.refuse", strip=())
    async def refuse_handler(request: Request, grant: dict):
        return JSONResponse({"error": "nope"}, status_code=400), "사람이 읽는 이유"

    @api.get("/t/refuse-bare")
    @peer_protocol.capability("t.refuse_bare", strip=())
    async def refuse_bare_handler(request: Request, grant: dict):
        return JSONResponse({"error": "nope"}, status_code=404)

    api.state.audits = audits
    return api


def test_strip_removes_b_local_ids_at_every_depth(app):
    """최상위만 지우면 정작 위험한 건(목록 안의 항목) 그대로 남는다."""
    with TestClient(app) as c:
        body = c.get("/t/strip").json()
    assert body == {"results": [{"name": "a"}]}


def test_handler_detail_reaches_the_audit_log(app):
    """관문이 detail을 일반화해 버리면 "12건" 같은 사람 말이 경로로 뭉개진다."""
    with TestClient(app) as c:
        c.get("/t/detail")
    assert app.state.audits[-1] == ("peer-a", "t.detail", True, "12건")


def test_refusal_is_audited_as_failure_with_its_reason(app):
    with TestClient(app) as c:
        r = c.get("/t/refuse")
    assert r.status_code == 400
    assert app.state.audits[-1] == ("peer-a", "t.refuse", False, "사람이 읽는 이유")


def test_refusal_without_a_reason_still_gets_audited(app):
    """이유를 안 적어도 **감사는 남는다** — 빠뜨려서 기록이 비는 일이 없게."""
    with TestClient(app) as c:
        c.get("/t/refuse-bare")
    pid, action, ok, detail = app.state.audits[-1]
    assert (pid, action, ok) == ("peer-a", "t.refuse_bare", False)
    assert "404" in detail


def test_every_handler_is_audited_exactly_once(app):
    """핸들러가 직접 audit을 부르고 관문도 부르면 같은 사건이 두 번 남는다."""
    with TestClient(app) as c:
        c.get("/t/detail")
    assert len(app.state.audits) == 1


def test_denied_request_never_reaches_the_handler(app, monkeypatch):
    monkeypatch.setattr(
        peer_protocol, "_authenticate_raw",
        lambda *a, **k: (_ for _ in ()).throw(peer_protocol.PeerDenied("no")))
    with TestClient(app) as c:
        r = c.get("/t/strip")
    assert r.status_code == 401 and r.json()["error"] == "peer_denied"


# ---------------------------------------------------------------------------
# 소스 검사 — 새 capability가 관문을 우회하는 것을 막는다
# ---------------------------------------------------------------------------

_PEER_PY = Path(__file__).resolve().parent.parent / "routes" / "peer.py"

# 관문을 못 쓰는 정당한 예외 둘.
#  - pair : 서명 **전**에 도는 부트스트랩이다(티켓 자체가 인증).
#  - ws   : WebSocket에는 Request가 없고 수명도 요청 하나가 아니다
#           (연결·종료를 따로 감사한다).
_EXEMPT = {"peer_pair", "peer_ws"}


def _http_handlers() -> list[tuple[str, str]]:
    src = _PEER_PY.read_text(encoding="utf-8")
    out = []
    for block in re.split(r"\n(?=@router\.)", src)[1:]:
        m = re.search(r"async def (\w+)", block)
        if not m:
            continue
        out.append((m.group(1), block))
    return out


def test_every_http_capability_goes_through_the_gate():
    offenders = [
        name for name, block in _http_handlers()
        if name not in _EXEMPT
        and not block.startswith("@router.websocket")
        and "@capability(" not in block
    ]
    assert offenders == [], (
        "관문을 거치지 않는 peer capability가 있다 — @capability를 붙이고 "
        f"strip을 정할 것(B 로컬 식별자가 A로 새는 것을 막는다): {offenders}")


def test_handlers_do_not_audit_by_hand():
    """관문이 감사를 책임진다. 핸들러가 또 부르면 같은 사건이 두 번 남고,
    어느 쪽이 정본인지 알 수 없게 된다."""
    offenders = [
        name for name, block in _http_handlers()
        if name not in _EXEMPT and "host_store.audit(" in block
    ]
    assert offenders == [], (
        f"핸들러가 직접 감사 로그를 남긴다 — 관문에 맡길 것: {offenders}")


def test_the_exemptions_are_still_exempt_for_the_stated_reason():
    """예외 목록이 조용히 늘어나는 것을 막는다 — 늘리려면 이 테스트를 고치면서
    왜 관문을 못 쓰는지 적어야 한다."""
    names = {name for name, _ in _http_handlers()}
    assert _EXEMPT <= names, "예외 목록에 이제 없는 핸들러가 남아 있다"
    assert len(_EXEMPT) == 2


def test_peer_file_strips_b_local_identifiers():
    """`peer_file`의 strip 선언이 사라지지 않게 한다.

    `id`(B의 저장소 키)와 `path`(B의 절대 경로)는 **모양이 A의 것과 똑같다.**
    화면이 그 `id`로 `/api/files/{id}/…`를 부르면 A의 저장소에서 엉뚱한 파일을
    찾는데, 아무 오류도 안 난다 — 그냥 다른 파일이 열린다.

    나중에 원격 파일 목록(2.2)에서 이 파일을 다시 가리켜야 하면 **지우는 대신
    B 것임이 이름에 드러나는 형태**로 바꿀 것(세션의 `remote:<host>:<name>`과
    같은 방식). 로컬 것과 구별 안 되는 `id`를 그냥 내려보내는 길로 돌아가면
    이 테스트가 막는다.
    """
    src = _PEER_PY.read_text(encoding="utf-8")
    block = next(b for _, b in _http_handlers() if "peer_file" in b)
    assert 'strip=("id", "path")' in block, (
        "peer_file이 B 로컬 식별자를 다시 내려보내려 한다")
    # A쪽도 그걸 되돌려 흘리면 안 된다.
    files_py = (_PEER_PY.parent / "files.py").read_text(encoding="utf-8")
    assert '("id", "path", "reused", "typed")' not in files_py, (
        "routes/files.py가 B의 id·path를 화면으로 다시 흘린다")
