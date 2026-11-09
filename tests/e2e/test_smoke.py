"""E1 — 실브라우저 스모크. 정적 검사가 원리상 못 보는 것만 본다.

## 왜 이 파일이 다시 생겼나

2026-09-08에 `test_e2e.py`를 통째로 지웠다. 13개 중 7개가 ADR-7로 폐지된 그리드
뷰(`#grid-toggle`)를 검사하고 있었고, playwright 미설치 + 실서버 필요로 어디서도
안 돌아서 아무도 그 사실을 몰랐다. "테스트가 있다"는 거짓 안심감만 주고 있었다.

그런데 브라우저 없이 못 잡는 버그가 실재한다. 같은 날 rail 세션 카드의
`.card-preview`가 카드 밖으로 **1008px** 삐져나와 패널과 터미널 위를 덮고 있는 걸
발견했다 — `.vt-card`(flex-direction:column)와 `.vt-rail-session-row`
(align-items:center)를 동시에 갖는 바람에 자식이 stretch되지 않고 max-content 폭을
가진 것이다. 이건 **캐스케이드가 실제로 합성된 결과**라서, 토큰 값을 계산하는
`frontend/tests/tokens-contrast.test.js`로는 원리상 못 본다.

## 그래서 여기서 보는 것 / 안 보는 것

본다: 레이아웃 봉쇄(자식이 부모 밖으로 나가는가), 반응형 구간별 표시/숨김,
스킨 전환이 실제 계산값에 반영되는가, 색 단독이 아닌 상태 표시가 실제로 그려지는가.

안 본다: 색 대비(숫자 계산이 더 빠르고 정확하다 — tokens-contrast.test.js),
비즈니스 로직(pytest), 모듈 배선(jsdom 테스트). **중복은 일부러 피한다** — 느린
테스트가 빠른 테스트로 되는 걸 또 하면 유지비만 는다.

## 실행

    pip install -r requirements-dev.txt && playwright install chromium
    pytest tests/e2e -v

playwright나 브라우저가 없으면 **전체 skip**한다. 옛 파일이 "안 도는데 있는 척"
했던 게 문제였지 skip 자체가 문제는 아니다 — 다만 CI에서는 실제로 설치해 돌린다
(.github/workflows/ci.yml의 e2e 잡).

**`pytest`를 그냥 치면 이 파일은 안 돈다** — pytest.ini의 testpaths가
`server/tests`라서다. 일부러 그렇게 뒀다: playwright 동기 API를 같은 프로세스에
올리면 asyncio를 쓰는 기존 테스트 15건이 RuntimeError로 깨진다(실측). 자세한 건
같은 디렉토리의 conftest.py 주석 참고.

`VT_E2E_URL`을 주면 이미 떠 있는 서버에 붙는다(그때는 `VT_E2E_TOKEN`(기계 토큰)과 `VT_E2E_PASSWORD`(로그인 비밀번호)도 같이).
안 주면 이 파일이 **격리된 서버를 직접 띄운다** — 실 사용자의 워크스페이스·tmux
소켓·인증 토큰을 절대 건드리지 않기 위해서다(아래 `_isolated_env` 주석 참고).
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import json
import subprocess
import urllib.request
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright 미설치 — pip install -r requirements-dev.txt")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# 격리 서버가 쓴 tmux 소켓을 뒷정리하려고 server/ 의 공용 헬퍼를 쓴다.
# pytest.ini의 `pythonpath = server` 로 이미 경로에 있지만, 이 파일은 경로를
# 명시해서 돌리는 일이 많아(`pytest tests/e2e`) 여기서 한 번 더 보장한다.
if str(ROOT / "server") not in sys.path:
    sys.path.insert(0, str(ROOT / "server"))
import tmux_sockets  # noqa: E402

# 반응형 3구간 — frontend/js/layout/breakpoints.js와 같은 경계값.
COMPACT = {"width": 390, "height": 780}
REGULAR = {"width": 900, "height": 800}
WIDE = {"width": 1440, "height": 900}

SKINS = ["farshell", "macos", "catppuccin", "windows", "vscode", "notepad", "warp", "linear", "vercel"]  # frontend/js/theme.js VT_SKINS와 같다


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# 브라우저는 사람처럼 **비밀번호로 로그인**해서 세션 쿠키를 받는다. 기계 토큰(VT_AUTH_TOKEN)은
# 웹앱에서 더 이상 통하지 않는다(`?token=`은 주소창에서 지워지고 서버도 거절한다 — docs/ref/auth.md).
# 토큰은 테스트 코드가 서버를 직접 부를 때(Authorization 헤더)만 쓴다. 격리 서버 전용 값이다.
E2E_PASSWORD = "e2e-smoke-password"


def _password_hash() -> str:
    from auth.password import hash_password
    return hash_password(E2E_PASSWORD)


def _login(ctx, base: str) -> None:
    """컨텍스트에 세션 쿠키를 심는다 — `ctx.request`는 컨텍스트의 쿠키 저장소를 같이 쓴다.
    외부 서버(VT_E2E_URL)는 VT_E2E_PASSWORD로 로그인한다."""
    password = os.environ.get("VT_E2E_PASSWORD", E2E_PASSWORD)
    r = ctx.request.post(f"{base}/api/auth", data={"token": password})
    assert r.ok, f"e2e 로그인 실패: {r.status} {r.text()[:200]}"


def _isolated_env(port: int, token: str, tmp: Path) -> dict:
    """실 사용자 상태와 완전히 분리된 서버 환경.

    세 개를 **같이** 줘야 한다. 하나라도 빠지면 이 테스트가 사용자의 실제 상태를
    건드린다:

      VT_AUTH_TOKEN     안 주면 서버가 ~/.vt.env의 토큰을 상속해, 테스트가 모르는
                        값으로 인증이 걸려 401이 난다.
      VT_WORKSPACE_PATH 안 주면 사용자의 실제 workspace.json에 테스트가 만든
                        레이아웃·설정이 저장된다.
      VT_TMUX_SOCKET    안 주면 사용자의 `-L fsh` 소켓을 그대로 본다 — 진짜 작업
                        세션이 목록에 뜨고, 조작하면 실제로 영향을 준다.
      VT_STATE_DIR      안 주면 **`~/.vt/` 그 자체**에 쓴다. 서버 모듈 20여 개가
                        이 값을 읽는다(기기 설정·기기 목록·스니펫·큐·파일·푸시
                        구독·스크롤백·MCP). 2026-09-19에 실제로 겪었다: 이
                        스위트가 `~/.vt/device-settings/local.json`을 만들고
                        레일 폭·dock 탭을 거기 써 넣었다. 같은 실수가
                        `~/.vt/groups.json`에서 한 번 더 있었다(2026-09-18).

    VT_NETWORK_MODE=localhost로 두는 건 테스트 서버가 LAN에 노출되지 않게 하기
    위해서다. 짧게 뜨는 서버라도 인증 토큰이 프로세스 목록에 보이는 창은 안 만든다.
    """
    env = dict(os.environ)
    # 상속을 확실히 끊는다 — ~/.vt.env가 아니라 여기서 준 값만 쓰게 한다.
    for k in list(env):
        if k.startswith("VT_"):
            del env[k]
    env.update(
        VT_PORT=str(port),
        VT_AUTH_TOKEN=token,
        VT_AUTH_PASSWORD_HASH=_password_hash(),
        # 서명키가 없으면 로그인 쿠키가 기계 토큰 그대로가 되어 「사람 세션」으로 인정되지 않는다
        # (승인함 같은 사람 전용 조회가 403). 실제 설치처럼 서명키를 둔다.
        VT_AUTH_SESSION_KEY=secrets.token_hex(32),   # 서버 하나의 수명 동안만 쓰는 임시 값
        # 화면 언어는 서버가 OS 로캘로 정한다(docs/ref/i18n.md). 이 스위트의 단언은 한국어 화면을 전제하는데
        # CI 러너는 LANG=C.UTF-8이라 영어로 떴고, 탭·라벨 선택자가 전부 빗나갔다. 로캘과 무관하게 고정한다.
        VT_LANG="ko",
        VT_WORKSPACE_PATH=str(tmp / "workspace.json"),
        VT_TMUX_SOCKET=_tmux_socket(port),
        VT_NETWORK_MODE="localhost",
        VT_STATE_DIR=str(tmp / "state"),      # ~/.vt/ 대신 여기에 쓰게
        VT_CONFIG=str(tmp / "no-such.env"),   # ~/.vt.env를 읽지 않게
        # dock 소스컨트롤 탭이 볼 저장소를 이 저장소 자신으로 고정한다. 기본값
        # (~/GitHub)은 CI에 없고 로컬에선 사람마다 달라 결과가 흔들린다. 열람은
        # 읽기 전용이고 경계도 여기로 좁아진다.
        # 둘째 경계는 뷰어 시험 파일(큰 파일·CP949·긴 한 줄 — `_make_viewer_files`)을 두는 임시 폴더다.
        VT_BROWSE_ROOTS=f"{ROOT}:{tmp / 'browse'}",
        PYTHONUNBUFFERED="1",
    )
    return env


def _tmux_socket(port: int) -> str:
    """이 격리 서버가 쓸 전용 tmux 소켓 이름 — 이름을 만드는 유일한 곳."""
    return f"fsh-e2e-{port}"


def _read_log(path, tail: int = 4000) -> str:
    """서버 로그 꼬리 — 실패 메시지에 붙인다(파이프를 안 쓰므로 파일에서 읽는다)."""
    try:
        return path.read_text(errors="replace")[-tail:]
    except OSError:
        return "(로그 없음)"


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """(base_url, token). VT_E2E_URL이 있으면 그걸 쓰고, 없으면 직접 띄운다."""
    external = os.environ.get("VT_E2E_URL")
    if external:
        yield external.rstrip("/"), os.environ.get("VT_E2E_TOKEN", "")
        return

    global _LOCAL_STATE_DIR
    tmp = tmp_path_factory.mktemp("e2e")
    _LOCAL_STATE_DIR = tmp / "state"
    global _BROWSE_DIR
    _BROWSE_DIR = _make_viewer_files(tmp / "browse")
    port = _free_port()
    token = "e2e-smoke-token"
    # ⚠ 서버 로그는 **파일로** 받는다. 예전엔 stdout=PIPE였는데 그 파이프를 읽는
    # 쪽이 아무도 없었다 — 액세스 로그가 쌓여 파이프 버퍼(64KB)가 차는 순간
    # 서버의 write(2)가 영원히 블록되고, 그게 asyncio 이벤트 루프를 통째로
    # 멈춰 세운다. 그때부터 **모든 요청이 응답 없이 타임아웃**이라 17번째쯤부터
    # 스위트 전체가 무너졌다(2026-09-14, 멈춘 서버의 스택을 떠서 확인:
    # uvloop → task_step → _io_FileIO_write → write). 테스트가 오래 돌수록
    # 로그가 쌓이니 "왜 뒤쪽만 깨지나"도 이걸로 설명된다.
    log_path = tmp / "server.log"
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT / "server"),
        env=_isolated_env(port, token, tmp),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"서버가 뜨기 전에 죽었다:\n{_read_log(log_path)}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("서버 기동 30초 타임아웃")
        yield base, token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        # 격리 서버가 띄운 tmux 서버는 **서버 프로세스를 죽여도 남는다** — 별도
        # 프로세스라서 부모를 따라 내려가지 않는다. 여기서 죽이지 않으면 tmux
        # 서버가 계속 돌고, 죽이더라도 소켓 파일은 /tmp/tmux-<uid>/ 에 쌓인다.
        tmux_sockets.kill_server(_tmux_socket(port))


@pytest.fixture(scope="session")
def _pw():
    """playwright 드라이버 하나(세션 스코프) — `browser`·`isolated_page`가
    같이 쓴다. `sync_playwright()`를 두 번 중첩해 열면(각 픽스처가 따로
    `with sync_playwright()`를 부르면) 드라이버 하나로 처리하도록 되어 있는
    동기 API가 충돌한다(실측: isolated_page 픽스처를 `with sync_playwright()`
    로 따로 열었더니 그 즉시 setup 단계에서 에러가 났다) — 그래서 드라이버는
    세션에 하나만 두고, 그 위에서 브라우저만 따로 띄운다."""
    with sync_playwright() as p:
        yield p


# 공유 Chromium 프로세스는 컨텍스트를 오래 순환하면 열화된다(`isolated_page`
# 픽스처 주석의 그 현상). 일정 개수마다 프로세스를 새로 띄워 **메모리 증가를
# 묶어둔다** — 기동이 1초 미만이라 스위트 전체로도 값싸다.
#
# ⚠ 정직하게 적어둔다: 이걸로 **17번째 테스트부터 스위트가 멈추는 문제는 안
# 고쳐졌다**(2026-09-13 실측 — 브라우저를 새로 띄워도, 좀비 프로세스를 전부
# 정리한 깨끗한 머신에서도 같은 자리에서 멈춘다). 개별·소그룹 실행은 28건
# 전부 통과하므로 원인은 "누적된 무언가"이고, 그게 서버인지 드라이버인지는
# 아직 못 갈랐다. 이 상수는 그 조사 중에 넣은 것이고, 되돌릴 이유가 없어
# 남긴다 — 다음 조사자가 여기서부터 이어가면 된다.
CONTEXTS_PER_BROWSER = 10


class _BrowserPool:
    """N개 컨텍스트마다 프로세스를 갈아 끼우는 얇은 래퍼."""

    def __init__(self, pw):
        self._pw = pw
        self._b = None
        self._used = 0

    def new_context(self, **kw):
        if self._b is None or self._used >= CONTEXTS_PER_BROWSER:
            self._close()
            self._b = self._pw.chromium.launch()
            self._used = 0
        self._used += 1
        return self._b.new_context(**kw)

    def _close(self):
        if self._b is not None:
            try:
                self._b.close()
            except Exception:  # 이미 죽었을 수 있다 — 정리 실패가 결과를 덮으면 안 된다
                pass
            self._b = None


@pytest.fixture(scope="session")
def browser(_pw):
    try:
        _pw.chromium.launch().close()   # 설치 여부만 먼저 확인한다
    except PlaywrightError as e:
        pytest.skip(f"chromium 없음 — `playwright install chromium` 필요 ({e})")
    pool = _BrowserPool(_pw)
    yield pool
    pool._close()


# 레이아웃 실패는 로그만으로 재구성하기 어려워서 실패 시 화면을 남긴다 —
# CI가 /tmp/e2e-*.png를 아티팩트로 올린다. `stash_failed`를 세우는 훅은
# conftest.py에 있다(테스트 모듈 안의 훅은 pytest가 수집하지 않는다).
# 2026-09-21 — **저장된 배치를 매 테스트 앞에서 지운다.**
#
# `f09535f`(세션 0개에서도 레이아웃 복원)부터 이 스위트의 배치가 실제로
# 저장된다. 그런데 서버는 세션 스코프로 한 벌뿐이라, 앞 테스트가 만든
# 패널·뷰어 pane이 **다음 테스트의 시작 화면**으로 복원된다. 그러면
# `layout/panes.js`가 `body.vt-has-viewer-pane`을 켜 온보딩을 숨기고,
# 온보딩을 전제하는 테스트들이 줄줄이 무너진다(실측: 6건).
#
# 브라우저 저장소(컨텍스트마다 새로 생긴다)만으로는 부족하다 — 정본은
# 서버의 workspace.json이다. 둘 다 비운다.
#
# 원래 `page` 픽스처 안의 지역 클로저였다 — `isolated_page`도 **같은 서버**를
# 공유하면서 이 초기화를 빼먹고 있었다. 그쪽 테스트(splitActivePane +
# createSession으로 그룹을 만드는 것들)가 만드는 split은 `removeSession()`
# 정리로 세션만 지워질 뿐 트리 구조(빈 leaf 2개)는 그대로 남아 **다음
# isolated_page 테스트가 그 유령 split 위에서 시작**했다(실측: 새 세션 2개만
# 만들었는데 pane이 3개였다). 모듈 최상위로 뽑아 두 픽스처가 함께 쓴다.
# 이 스위트가 직접 띄운 격리 서버의 상태 폴더(`VT_STATE_DIR`). 외부 서버(VT_E2E_URL)를 쓰면 None — 거기는 파일을 못 건드린다.
_LOCAL_STATE_DIR: Path | None = None
# 뷰어 시험 파일 폴더(격리 서버의 둘째 열람 경계). 외부 서버면 None — 그 시험들은 건너뛴다.
_BROWSE_DIR: Path | None = None


def _make_viewer_files(d: Path) -> Path:
    """파일 개편 단계 3·4의 뷰어 시험 파일. 내용은 합성이다(계약 7 — 실제 코드·경로를 쓰지 않는다)."""
    d.mkdir(parents=True, exist_ok=True)
    # 6MB, 64바이트 줄 — 앞 4MB만 열리고 「더 보기」가 붙는다.
    row = "{:08d} " + "x" * 54 + "\n"
    (d / "big.log").write_text("".join(row.format(i) for i in range(6 * 16384)))
    (d / "rows.txt").write_text("".join(f"row {i:04d}\n" for i in range(1, 1001)))
    (d / "k949.txt").write_bytes(("안녕하세요 뷰어 시험\n" * 20).encode("cp949"))
    (d / "mixed.txt").write_bytes(b"a\r\nb\nc\r\n")
    # 1.5MB 한 줄(minified 모양) — 첫 구간이 line_truncated, 다음 구간이 같은 줄을 잇는다.
    (d / "long.min.js").write_text("".join(f"var a{i}=function(b){{return b+{i % 97}}};" for i in range(60000))[: 1536 * 1024])
    return d


def _viewer_file(name: str) -> str:
    if _BROWSE_DIR is None:
        pytest.skip("외부 서버(VT_E2E_URL)에서는 뷰어 시험 파일을 만들 수 없다")
    return str(_BROWSE_DIR / name)


def _api(base: str, token: str, method: str, path: str, payload=None):
    # 브라우저 컨텍스트가 **닫힌 뒤에도** 불러야 하므로(page 픽스처의 teardown)
    # `ctx.request`를 쓰지 않는다 — 컨텍스트와 수명을 같이하기 때문이다.
    req = urllib.request.Request(
        f"{base}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"null")


def _reset_saved_layout(base: str, token: str) -> None:
    """앞 테스트가 서버에 남긴 **화면 상태**를 비운다 — 테스트 순서에 결과가 달라지지 않게.

    남는 곳이 셋이다. 예전엔 첫째만 지워서 전체 스위트가 5건씩 **늘 같은 자리에서** 깨졌다(2026-11-03 확인 —
    단독으로 돌리면 통과하고 전체로 돌리면 실패하는 순서 의존이었다):
      1. `/api/workspace` 의 `ui.layout`   — 옛 단일 배치 키.
      2. `/api/workspace` 의 `ui.layouts`  — **화면별** 배치(프런트가 지금 쓰는 키). 이게 안 지워지면 다음 테스트가
         앞 테스트가 만든 패널 칸을 복원해 온보딩·레일 시트를 가린다. 키를 `None`으로 보내야 지워진다.
      3. 기기 설정(`state/device-settings/*.json`) — dock 탭·접힘·폭, 레일 폭 등. 이 값이 남으면 「dock은 wide에서
         접힘이 기본」이 깨지고, `_open_dock`은 이미 열린 탭을 다시 눌러 닫는다. 서버가 파일에서 읽으므로 컨텍스트를
         닫은 뒤 지우면 된다(API에는 삭제가 없다).
    """
    try:
        try:
            cur = _api(base, token, "GET", "/api/workspace") or {}
            ui = (cur.get("data") or cur).get("ui") or {}
            stale = {k: None for k in (ui.get("layouts") or {})}
        except Exception:
            stale = {}
        _api(base, token, "PUT", "/api/workspace", {"ui": {"layout": None, **({"layouts": stale} if stale else {})}})
    except Exception:
        pass   # 정리 실패가 테스트 결과를 덮으면 안 된다
    if _LOCAL_STATE_DIR is not None:
        for f in (_LOCAL_STATE_DIR / "device-settings").glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass


@pytest.fixture
def page(browser, server, request):
    base, token = server
    ctx = browser.new_context(viewport=WIDE)
    _login(ctx, base)
    _reset_saved_layout(base, token)
    # ⚠ **첫 문서에서만** 지운다. init 스크립트는 `page.reload()`에서도 다시
    # 도는데, 배치 복원을 검사하는 테스트는 localStorage에 스냅샷을 심고
    # 새로고침하는 방식이다 — 매번 지우면 그 전제를 지워 버린다(실측으로 그
    # 테스트 하나가 깨졌다). sessionStorage는 같은 탭의 새로고침을 건너뛰고
    # 컨텍스트가 바뀌면 함께 비므로 "테스트마다 한 번"이 정확히 된다.
    ctx.add_init_script(
        "try {"
        "  if (!sessionStorage.getItem('__vtE2eLayoutCleared')) {"
        "    sessionStorage.setItem('__vtE2eLayoutCleared', '1');"
        "    localStorage.removeItem('vt-layout-v1');"
        "  }"
        "} catch (e) {}"
    )
    pg = ctx.new_page()
    errors: list[str] = []
    bad_responses: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    # 콘솔의 "Failed to load resource: … 401"에는 **URL이 없다.** 어떤 요청이
    # 실패했는지 모르면 그 줄을 무시할지 말지 판단할 수 없어서(2026-09-19에
    # 실제로 겪었다) 응답을 따로 모은다.
    pg.on(
        "response",
        lambda r: bad_responses.append(f"{r.request.method} {r.url} → {r.status}")
        if r.status >= 400 else None,
    )
    _boot(pg, f"{base}/")
    pg.console_errors = errors  # 개별 테스트가 필요하면 본다
    pg.bad_responses = bad_responses
    yield pg
    if getattr(request.node, "stash_failed", False):
        safe = "".join(c if c.isalnum() else "_" for c in request.node.name)[:80]
        try:
            pg.screenshot(path=f"/tmp/e2e-{safe}.png", full_page=True)
        except Exception:  # 스크린샷 실패가 테스트 결과를 덮으면 안 된다
            pass
    # ⚠ **컨텍스트를 닫은 뒤 한 번 더 지운다.** 레이아웃 저장은 400ms 디바운스
    # 뒤 fire-and-forget PUT이라, 앞 테스트의 저장이 **다음 테스트의 초기화보다
    # 늦게** 서버에 닿을 수 있다(실측: 같은 스위트를 연달아 돌리면 5건이
    # 들쭉날쭉 깨졌다 — 온보딩을 기다리는데 복원된 패널 칸이 그걸 숨긴다).
    # 컨텍스트를 먼저 닫아 더 이상 저장이 나가지 않게 한 뒤 지우면 그 창이 닫힌다.
    ctx.close()
    _reset_saved_layout(base, token)


# 이 파일의 나머지 테스트는 전부 세션 스코프 `browser`(Chromium 프로세스 하나)를
# 공유한다 — 컨텍스트만 테스트마다 새로 열고 닫는다. 실측으로 확인한 문제: 실제
# PTY를 만들어 xterm을 **진짜로 렌더링**하는 테스트(WebGL 사용)가 그 공유
# 프로세스 안에서 20여 개 컨텍스트를 거친 뒤에 돌면, 서버는 멀쩡한데(격리된
# 스크립트로 같은 순서를 재현해도 매번 즉시 끝난다) 브라우저 쪽 응답만 느려져
# `wait_for_selector`가 타임아웃되고, 그 여파로 파일 뒤쪽 테스트까지 연쇄로
# 깨졌다 — headless Chromium의 소프트웨어 WebGL 렌더러가 컨텍스트를 오래
# 순환하며 누적 열화되는 것으로 보인다(콘솔에 "GPU stall due to ReadPixels"
# 경고가 반복 찍힌다). 그래서 실제 터미널을 렌더링하는 테스트만 **자기 전용
# 브라우저 프로세스**를 새로 띄운다 — 공유 프로세스의 컨텍스트 수와 무관해진다.
@pytest.fixture
def isolated_page(_pw, server):
    base, token = server
    try:
        b = _pw.chromium.launch()
    except PlaywrightError as e:
        pytest.skip(f"chromium 없음 — `playwright install chromium` 필요 ({e})")
    # `page` 픽스처와 같은 이유로 같은 초기화가 필요하다 — 이 스위트의 서버는
    # 세션 스코프로 한 벌이라, 이 테스트들이 만드는 세션·split도 같은
    # workspace.json을 공유한다. `removeSession()`(테스트 cleanup)은 세션만
    # 지우고 split 구조(빈 leaf들)는 남기므로, 리셋 없이는 다음 isolated_page
    # 테스트가 유령 split 위에서 시작한다.
    _reset_saved_layout(base, token)
    ctx = b.new_context(viewport=WIDE)
    _login(ctx, base)
    pg = ctx.new_page()
    _boot(pg, f"{base}/")
    yield pg
    ctx.close()
    b.close()
    _reset_saved_layout(base, token)


def _boot(pg, url: str) -> None:
    """부팅이 **끝날 때까지** 기다린다.

    `#vt-rail`이 보이는 걸 기다리면 안 된다 — index.html의 정적 마크업이라
    요소는 있고 리스너는 아직 없는 창이 있다. 실제로 그 창에 클릭이 들어가
    아무 일도 안 일어났다. sleep으로 덮으면 CI에서 플레이키해지므로
    `main.js`가 세우는 완료 마커를 본다.
    """
    pg.goto(url, wait_until="load")
    pg.wait_for_function(
        "() => document.documentElement.dataset.appBooted === 'true'"
        " || document.documentElement.dataset.appBootFailed === 'true'",
        timeout=20000,
    )
    failed = pg.evaluate("() => document.documentElement.dataset.appBootFailed === 'true'")
    assert not failed, "앱 부팅이 실패했다 (main.js의 bootApp 예외)"
    # `appBooted`는 배선 끝이고 bootApp()의 fetch는 아직 돌고 있을 수 있다. 그 사이에
    # 테스트가 새로고침하면 진행 중인 부팅이 끊긴다(ADR-45가 찾은 경쟁) — 끝까지 기다린다.
    pg.wait_for_function("() => document.documentElement.dataset.appReady === 'true'", timeout=20000)


def _dock_tab(pg, label: str):
    """활동 막대의 활동 버튼 하나(2026-11 작업대 — 옛 dock 탭 줄이 활동 막대로 옮겨 갔다).
    버튼은 **아이콘만** 그려서 텍스트가 없다 — 이름은 aria-label·툴팁에 있다."""
    return pg.locator(f'#vt-activitybar [role="tab"][aria-label^="{label}"]').first


def _open_dock(pg, label: str) -> None:
    """도구 활동을 사이드바에 연다 — **이미 그 활동이 펼쳐져 있으면 클릭하지 않는다.**
    펼친 활동 재클릭은 접기라서, 앞 테스트가 남긴 상태(사이드바는 활동·접힘을
    기기 설정에 저장한다) 위에서 무조건 클릭하면 닫아버린다. 활성 표시(.active)는
    「지금 활동이고 사이드바가 보인다」일 때만 붙는다.

    선택자를 `:has-text()`로 쓰지 않는 이유: 그건 playwright 전용 문법이라
    `page.evaluate` 안의 querySelector에서는 SyntaxError가 난다(실측). 클릭은
    locator로, 상태 확인은 textContent 비교로 나눠 쓴다.

    **탭이 active가 된 것과 패널이 뜬 것은 다르다.** 탭 활성은 동기(시그널)지만
    패널 본체는 지연 청크(panels.js)라 네트워크를 한 번 갔다 온다. 2.1.6 전에는
    app.js가 panels.js를 정적 import해서(=지연이 지연이 아니었다) 클릭 직후
    항상 이미 로드돼 있었고, 그래서 이 함수가 패널을 안 기다려도 통과했다.
    청크가 진짜로 지연되자 그 의존이 드러났다 — 여기서 같이 기다린다.
    """
    pg.wait_for_selector('#vt-activitybar [role="tab"]', timeout=10000)
    is_active = (
        "(label) => [...document.querySelectorAll('#vt-activitybar [role=tab]')]"
        ".some((t) => (t.getAttribute('aria-label') || '').startsWith(label) && t.classList.contains('active'))"
    )
    if not pg.evaluate(is_active, label):
        _dock_tab(pg, label).click()
    pg.wait_for_function(is_active, arg=label, timeout=5000)
    # 지연 청크가 도착해 패널이 dock 본문에 붙을 때까지. 무엇이 붙는지는
    # 호출한 테스트가 검사한다 — 여기서는 "뭔가 하나 떴다"까지만 본다.
    pg.wait_for_function(
        "() => !!document.querySelector('#vt-dock .vt-dock-body > *')", timeout=10000
    )


# ── 부팅 ──────────────────────────────────────────────────────────────────

# 예전에는 레거시 `?token=` 링크의 쿠키 교환(`POST /api/auth` 401)이 매번 한 번 찍혀 그것만 면제했다.
# 이제 스위트는 비밀번호로 로그인해 들어가므로(`_login`) 면제할 응답이 없다 — 4xx/5xx는 전부 실패다.


def test_페이지가_JS_에러_없이_뜬다(page):
    page.wait_for_timeout(1200)
    # 서버가 인증을 요구하는 401/403은 없어야 하고, 모듈 평가 에러도 없어야 한다.
    unexpected = list(page.bad_responses)
    assert unexpected == [], "예상 밖 실패 응답:\n  " + "\n  ".join(unexpected)
    fatal = [
        e for e in page.console_errors
        if "favicon" not in e.lower()
        # URL이 없는 줄이라 문구로 거른다 — 어떤 요청이 실패했는지는 바로 위
        # bad_responses 단언이 이미 확인했다.
        and "Failed to load resource" not in e
    ]
    assert fatal == [], "콘솔 에러:\n  " + "\n  ".join(fatal)


def test_세션이_없으면_온보딩이_보인다(page):
    # CI에는 tmux 세션이 없다. 그 상태에서 빈 화면이 아니라 안내가 떠야 한다.
    page.wait_for_selector(".vt-onboarding", timeout=10000)
    assert page.locator(".vt-onboarding").is_visible()


def test_세션이_없어도_패널_전용_배치를_복원하고_다음_변경을_저장한다(page):
    """N4 후속 — 세션 복원 분기를 하나도 타지 않아도 layout 복원은 끝나야 한다.

    이 호출이 빠지면 패널 칸은 새로고침과 함께 사라지고, persist의 저장 가드도
    영원히 닫힌다. 실제 Chromium에서 부팅 순서·지연 패널 마운트·400ms 저장
    디바운스까지 한 번에 확인한다.
    """
    page.evaluate(
        """() => localStorage.setItem('vt-layout-v1', JSON.stringify({
          v: 4, savedAt: Date.now(), activeTab: 'panel-tab',
          active: 'panel-pane',
          tree: { t:'leaf', id:'panel-pane', session:null, kind:'panel', panel:'queue' },
          tabs: [{
            id:'panel-tab', worktreeId:null, hostId:'local', label:null,
            active:'panel-pane',
            tree:{ t:'leaf', id:'panel-pane', session:null, kind:'panel', panel:'queue' }
          }]
        }))"""
    )
    page.reload(wait_until="load")
    page.wait_for_function("() => document.documentElement.dataset.appBooted === 'true'")
    page.wait_for_selector(".vt-pane-panel-root", state="visible", timeout=15000)

    assert page.locator(".vt-onboarding").is_hidden()
    assert page.evaluate("() => document.body.classList.contains('vt-has-viewer-pane')")

    page.evaluate("() => window.openPanelPane('files')")
    page.wait_for_function(
        """() => {
          const s = JSON.parse(localStorage.getItem('vt-layout-v1') || 'null');
          return s?.tree?.kind === 'panel' && s.tree.panel === 'files';
        }""",
        timeout=5000,
    )


# ── 레이아웃 봉쇄 — 2026-09-08 버그의 클래스 ──────────────────────────────
#
# 그날의 버그: rail 세션 카드의 `.card-preview`가 카드 밖으로 1008px 삐져나왔다.
# `.vt-card`(flex-direction:column)와 `.vt-rail-session-row`(align-items:center)를
# 한 요소가 동시에 가져, 컬럼 flex의 자식이 stretch되지 않고 max-content 폭을
# 가진 것이다.
#
# 이걸 그대로 재현하려면 **웹 세션이 있어야 한다** — rail은 `allSessions()`(웹
# 세션 스토어)를 `web_session_id`로 tmux 목록과 조인해서 카드를 만들기 때문에,
# `/api/tmux/sessions`만 스텁해도 "열려 있는 세션이 없습니다"가 뜬다. CI에는
# tmux도 세션도 없다.
#
# 그래서 둘로 나눈다:
#   1) 그 클래스 조합을 실제 패널 안에 넣고 봉쇄를 확인 (아래)
#   2) CI에서 실제로 열리는 표면들에 대한 일반 봉쇄 검사 (그 아래)
# 1은 정밀하지만 마크업을 테스트가 들고 있고, 2는 데이터 없이도 같은 **종류**의
# 버그를 잡는다. 둘 다 있어야 한다.

# rail.js가 buildSessionCard 결과에 얹는 클래스 조합. 이게 바뀌면 아래 테스트가
# 엉뚱한 걸 검사하게 되므로 번들에서 실제로 확인한다.
# shell/Rail.tsx가 행에 얹는 클래스 조합(N36). 이게 바뀌면 아래 테스트가
# 엉뚱한 걸 검사하게 되므로 번들에서 실제로 확인한다 — 레일은 지연 청크
# (shell.js)에 있으므로 app.js가 아니라 그 파일을 본다.
_CARD_CLASSES = ["vt-wgrail-row", "vt-wgrail-name", "vt-wgrail-row-sub"]


def test_레일_행_클래스_조합이_아직_코드에_있다(page, server):
    """아래 봉쇄 테스트가 검사하는 마크업이 실제 코드와 안 어긋나는지 본다.

    테스트가 마크업을 들고 있으면 코드가 바뀔 때 조용히 무의미해진다 — 이 저장소가
    이미 겪은 실패다(폐지된 #grid-toggle을 검사하던 옛 E2E). 최소한의 결합 확인.
    """
    base, _ = server
    bundle = page.request.get(f"{base}/static/dist/shell.js").text()
    missing = [c for c in _CARD_CLASSES if c not in bundle]
    assert missing == [], f"번들에 없는 클래스: {missing} — 테스트 마크업이 낡았다"


def test_레일_행_레이아웃이_행_안에_갇힌다(page):
    """긴 브랜치명·긴 상태 문장이 레일 행 밖으로 나가지 않는가.

    N36이 48px 아이콘 레일을 252px 워크트리 레일로 대체하면서, 봉쇄가 깨질 수
    있는 자리도 세션 카드에서 이 행으로 옮겨갔다(이름 + 상태 문장 두 줄 모두
    ellipsis에 의존한다). CI에는 세션이 없으므로 같은 클래스 조합을 직접 넣어
    캐스케이드가 합성된 결과만 본다 — 옛 세션 카드 테스트와 같은 방식이다.
    """
    page.wait_for_selector("#vt-wgrail .vt-wgrail-body", timeout=10000)
    overflow = page.evaluate(
        """() => {
          const list = document.querySelector('#vt-wgrail .vt-wgrail-body');
          const row = document.createElement('div');
          // 2026-09-19 — 실제 행은 `vt-srow vt-wgrail-row` 두 클래스를 함께
          // 단다(RailRow.tsx). 레이아웃(display:flex 등)은 `vt-srow` 쪽이
          // 들고 있어서, 옛 이름만 붙이면 flex가 아닌 블록을 재게 된다.
          // 2026-11(리디자인 4단계) — 아이콘 열 + 최대 4줄 + 답 버튼 줄. 개입 필요 행(틴트)이 가장 많이 싣는다.
          row.className = 'vt-srow vt-wgrail-row is-waiting';
          row.innerHTML = `
            <span class="vt-srow-state"><svg width="16" height="16"></svg></span>
            <div class="vt-srow-main vt-wgrail-row-main">
              <div class="vt-srow-top vt-wgrail-row-top">
                <span class="vt-srow-agent vt-wgrail-agent"><svg width="14" height="14"></svg></span>
                <span class="vt-srow-name vt-wgrail-name">feat/very-long-branch-name-that-never-wraps-anywhere-at-all</span>
                <button type="button" class="vt-srow-kids"><span>위임 보고 12/20</span></button>
                <span class="vt-srow-elapsed">59분</span>
                <button type="button" class="vt-icon-btn sm vt-srow-menu"><svg width="16" height="16"></svg></button>
              </div>
              <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line2">
                <span class="vt-srow-workspace-kind">WT</span>
                <span class="vt-srow-directory">/Users/x/GitHub/work/proj/very/long/path/that/never/wraps</span>
                <span class="vt-srow-branch with-ico"><span class="vt-srow-branch-ico"><svg width="12" height="12"></svg></span><span class="vt-srow-branch-name">feat/another-very-long-branch-name</span></span>
                <span class="vt-srow-meta vt-wgrail-diff"><span class="vt-diff-add">+14200</span> <span class="vt-diff-del">−3800</span></span>
              </div>
              <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line3">
                <span class="vt-srow-kw" data-state="waiting">승인 요청</span>
                <span class="vt-srow-tool">도구: mcp__very_long_server_name__very_long_tool_name</span>
                <span class="vt-srow-badge">계획 있음</span>
              </div>
              <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line4"><span class="vt-srow-screens">다른 화면 12</span></div>
              <div class="vt-srow-answers">
                <button type="button" class="vt-btn sm vt-srow-answer"><span class="vt-srow-answer-label">1 · Yes, and don&#39;t ask again for this command in this project directory</span></button>
                <button type="button" class="vt-btn sm">3 · 아니오</button>
                <button type="button" class="vt-btn sm quiet">터미널로</button>
              </div>
            </div>`;
          list.appendChild(row);
          const c = row.getBoundingClientRect();
          const bad = [];
          for (const child of row.querySelectorAll('*')) {
            const r = child.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            if (r.left < c.left - 1 || r.right > c.right + 1) {
              bad.push(`${child.className}: ${Math.round(r.left)}~${Math.round(r.right)} (행 ${Math.round(c.left)}~${Math.round(c.right)})`);
            }
          }
          row.remove();
          return bad;
        }"""
    )
    assert overflow == [], "행 밖으로 넘친 자식:\n  " + "\n  ".join(overflow)


def test_열린_표면의_내용이_그_표면을_넘지_않는다(page):
    """CI에서 실제로 열리는 표면들에 대한 일반 봉쇄 검사.

    데이터가 없어도 되고, 새 UI가 생겨도 선택자만 늘리면 된다. 세션 카드처럼
    특정 조합을 못 집는 대신 같은 **종류**의 실패를 넓게 잡는다.
    """
    _open_dock(page, "큐")
    page.wait_for_timeout(300)
    bad = page.evaluate(
        """() => {
          const surfaces = ['#vt-workbench', '#vt-activitybar', '#vt-side', '#vt-dock', '.vt-onboarding', '#topbar'];
          const out = [];
          for (const sel of surfaces) {
            const host = document.querySelector(sel);
            if (!host || host.offsetParent === null) continue;
            const h = host.getBoundingClientRect();
            for (const child of host.querySelectorAll('*')) {
              const r = child.getBoundingClientRect();
              if (r.width === 0 && r.height === 0) continue;
              const cs = getComputedStyle(child);
              if (cs.position === 'fixed' || cs.position === 'absolute') continue;  // 의도적 탈출
              if (r.right > h.right + 1 || r.left < h.left - 1) {
                out.push(`${sel} > ${child.className || child.tagName}: ${Math.round(r.left)}~${Math.round(r.right)} (표면 ${Math.round(h.left)}~${Math.round(h.right)})`);
              }
            }
          }
          return out;
        }"""
    )
    assert bad == [], "표면 밖으로 넘친 요소:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("viewport", [COMPACT, REGULAR, WIDE], ids=["compact", "regular", "wide"])
def test_가로_스크롤이_생기지_않는다(page, viewport):
    """body가 가로로 스크롤되면 모바일에서 화면이 밀린다 — 반응형이 깨진 신호."""
    page.set_viewport_size(viewport)
    page.wait_for_timeout(400)
    over = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert over <= 1, f"가로 넘침 {over}px (뷰포트 {viewport['width']}px)"


# ── 반응형 구간 ───────────────────────────────────────────────────────────

def test_작업대는_compact에서_숨고_그_위에서_보인다(page):
    """티어 표(DESIGN.md §3): compact는 작업대(활동 막대·사이드바)가 없다(하단 내비가
    자리를 쓴다), regular부터 활동 막대가 보이고 세션 활동이면 레일(펼침 또는 48px 띠)이 보인다."""
    page.set_viewport_size(COMPACT)
    page.wait_for_timeout(300)
    assert not page.locator("#vt-workbench").is_visible(), "compact에서는 하단 내비가 대신한다"
    assert not page.locator("#vt-wgrail").is_visible(), "compact에서는 팔레트·바텀시트가 대신한다"

    page.set_viewport_size(REGULAR)
    page.wait_for_timeout(300)
    assert page.locator("#vt-activitybar").is_visible()
    assert page.locator("#vt-wgrail").is_visible()
    assert not page.locator("#vt-dock").is_visible(), "세션 활동에서 dock 본문은 hidden이다"


# ── 스킨 ──────────────────────────────────────────────────────────────────

def test_스킨이_실제_계산값에_반영된다(page):
    """data-skin만 바뀌고 색은 안 바뀌는 상태(스타일시트 누락 등)를 잡는다."""
    bars = {}
    for skin in SKINS:
        page.evaluate("(s) => document.documentElement.setAttribute('data-skin', s)", skin)
        bars[skin] = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--bar').trim()"
        )
    assert all(bars.values()), f"--bar가 비었다: {bars}"
    assert len(set(bars.values())) == len(SKINS), f"스킨별 --bar가 안 갈린다: {bars}"


def test_테마_칩이_서로_다른_색을_보인다(page):
    """T9 회귀 방지 — 칩 색을 인라인 style에서 토큰 상수로 옮겼다.

    `--color-acc`는 현재 스킨 것만 알려주므로, 칩이 그걸 쓰면 전부 같은
    색이 된다. 정적 검사(인라인 style이 없는가)만으로는 이 실패를 못 본다.
    """
    # 2026-09-19 — 칩은 레일 플라이아웃에서 **설정 패널 「모양」**으로 이식됐다
    # (플라이아웃 자체가 없어지면서 네 기능이 도달 불가능해졌던 그 이송).
    # 열지 않으면 DOM에 아예 없으므로 먼저 연다.
    # 액션(`settings.show`)은 지연 청크(panels/settings-lazy.js)가 등록하므로
    # 로드 전에는 `getAction`이 undefined다 — 실제 버튼을 눌러 그 청크까지
    # 받아오게 한다(제목 막대의 FarShell 메뉴 → 설정, `_open_dock`과 같은 이유).
    page.wait_for_selector(".vt-mb-menu", timeout=10000)
    page.locator(".vt-mb-menu").click()
    # 메뉴 항목의 접근 이름에는 단축키 글자(`Ctrl+,`)가 붙는다 — exact 일치가 아니라 앞부분으로 고른다.
    # 「상태 막대 설정」은 `^설정`에 걸리지 않는다.
    page.get_by_role("menuitem", name=re.compile(r"^설정")).click()
    # 설정은 섹션 하나만 그린다(기본 「터미널」) — 「모양」으로 옮겨야 칩이 생긴다.
    page.wait_for_selector("#vt-settings .vt-set-navitem", timeout=15000)
    page.locator("#vt-settings .vt-set-navitem", has_text="모양").first.click()
    page.wait_for_selector("#vt-settings .theme-chip .dot", timeout=10000)
    colors = page.eval_on_selector_all(
        "#vt-settings .theme-chip .dot",
        "els => els.map(e => getComputedStyle(e).backgroundColor)",
    )
    assert len(colors) == len(SKINS), f"칩 개수가 {len(colors)}개"
    assert len(set(colors)) == len(SKINS), f"칩 색이 안 갈린다: {colors}"


# ── 상태 표시가 색 단독이 아닌가 ──────────────────────────────────────────

# 옛 `test_레일_상태막대가_색_단독이_아니고_실제로_그려진다`는 지웠다 — 행 왼쪽 3px 상태 막대(`.vt-srow-mark`,
# `.vt-wgrail-bar`)가 상태 아이콘(`.vt-srow-state`, 색+모양, 읽지 않음은 둘레 링)으로 대체돼 검사할 대상이
# 없다(RailRow.tsx 머리말). 색 단독이 아닌지는 아이콘 모양 쪽이라 jsdom 시험(frontend/tests)이 본다.


def test_레일에서_연_시트가_온보딩_위에_뜬다(page):
    """2026-09-20 — 같은 회귀의 네 번째 자리, 이번엔 **쌓임 맥락** 쪽이다.

    `.vt-sidepanel`은 `position:fixed; z-index:70`이라 자기 안에 쌓임 맥락을
    만든다. 저장소 시트가 레일 JSX 안에 있던 동안에는 그 백드롭의
    `z-index:600`이 레일 밖으로 나가지 못해, 루트 맥락의 `.vt-onboarding`
    (z:500)이 시트를 통째로 덮었다 — 시트는 **보이는데** 클릭이 온보딩으로
    갔다(실측: 카드 중앙의 최상위 요소가 `DIV#onboarding`). z-index를 올려도
    안 풀린다. `Portal`로 body에 붙이는 것이 해법이고, 이 테스트는 그 상태를
    숫자가 아니라 **히트 테스트**로 고정한다 — CSS 값만 보면 600 > 500이라
    통과해 버린다.
    """
    page.wait_for_function(
        "() => document.documentElement.dataset.appBooted === 'true'", timeout=20000)
    page.wait_for_selector(".vt-onboarding", state="visible", timeout=10000)
    # 로그인 게이트의 스피너는 z-index 700이라 **온보딩보다도 위**다. 그게 아직
    # 떠 있는 동안 히트 테스트를 하면 이 테스트가 재려는 것(시트 vs 온보딩)이
    # 아니라 스피너를 잰다 — 실제로 한 번 그렇게 깨졌다. 걷힐 때까지 기다린다.
    page.wait_for_function(
        "() => { const s = document.getElementById('login-spinner');"
        "        return !s || s.offsetParent === null; }", timeout=15000)
    # 브리지는 레일 컴포넌트가 마운트될 때 만들어진다(Rail.tsx) — 레일은 지연 청크라 온보딩이 보인 뒤에야 올 수 있다.
    # 이걸 기다리지 않으면 단독으로는 통과하고 브라우저가 오래 돈 전체 실행에서만 깨진다(2026-11-03 확인).
    page.wait_for_function("() => typeof window.vtOpenWorktreeDialog === 'function'", timeout=15000)
    page.evaluate("() => window.vtOpenWorktreeDialog()")
    page.wait_for_selector(".vt-wtd-backdrop .vt-viewer-card", timeout=10000)
    got = page.evaluate(
        """() => {
          const bd = document.querySelector('.vt-wtd-backdrop');
          const card = bd.querySelector('.vt-viewer-card');
          const r = card.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return {
            inRail: document.getElementById('vt-wgrail').contains(bd),
            hitInSheet: !!(hit && bd.contains(hit)),
            hit: hit ? (hit.tagName + '.' + (hit.className || '')) : null,
          };
        }"""
    )
    assert got["inRail"] is False, "시트가 레일 안에 있으면 쌓임 맥락에 갇힌다"
    assert got["hitInSheet"], f"시트 중앙이 다른 것에 덮였다: {got}"


def test_온보딩이_레일과_dock을_덮지_않는다(page):
    """L4에서 한 번 잡았던 회귀의 재발 방지. `.vt-onboarding`은 position:fixed +
    z-index:500이라 body의 padding(레일·dock 자리)을 안 따라간다 — 세션이 0개인
    흔한 상태(부팅 직후·마지막 탭을 닫은 직후)에서 두 표면이 통째로 온보딩 밑에
    깔려 **클릭이 안 먹는다**. 렌더 여부가 아니라 실제 히트 테스트로 본다."""
    page.set_viewport_size(WIDE)
    page.wait_for_selector(".vt-onboarding", timeout=10000)
    page.wait_for_timeout(300)
    hit = page.evaluate(
        """() => {
          const top = (x, y) => document.elementFromPoint(x, y)?.closest('#vt-wgrail, #vt-activitybar, .vt-onboarding')?.id || 'onboarding';
          const rail = document.getElementById('vt-wgrail').getBoundingClientRect();
          const bar = document.getElementById('vt-activitybar').getBoundingClientRect();
          return {
            rail: top(rail.left + rail.width / 2, 400),
            bar: top(bar.left + bar.width / 2, 100),
          };
        }"""
    )
    assert hit["rail"] == "vt-wgrail", f"온보딩이 레일을 덮었다: {hit}"
    assert hit["bar"] == "vt-activitybar", f"온보딩이 활동 막대를 덮었다: {hit}"


def test_온보딩이_모바일_하단_내비를_덮지_않는다(page):
    """N38 — 데스크톱 레일·dock에서 두 번 났던 것과 같은 회귀가 모바일 하단
    내비 52px에서도 그대로 재현됐다(z-index 100 vs onboarding의 500). 부팅
    직후(세션 0개)에 하단 내비 버튼을 실제로 누를 수 있는지 본다.

    2026-09-20 — **상단 바(`#vt-mnav-top`)가 없어졌다.** 모바일 탐색 개편
    (`71cf429`)이 그 24px 상태 요약 줄을 걷어내고 「대기 N」 배지를 하단 내비의
    「작업」 버튼 안으로 옮겼다. 이 테스트는 그 뒤로 없는 요소를 잡으려다
    `getBoundingClientRect` of null로 죽고 있었다 — 화면이 바뀌었는데 같이
    안 고쳐진 것이지, 회귀가 아니다. 남은 불변식(하단 내비)만 본다.
    """
    page.set_viewport_size(COMPACT)
    # compact의 기본 탭은 **작업(플릿 홈)**이고, 그게 떠 있는 동안에는 온보딩을
    # 숨긴다(60-mobile-hud.css: z-index 500이 플릿 슬롯 90을 덮어 호스트
    # 스위처가 클릭을 못 받던 결함). 이 테스트가 보려는 상태 ―「온보딩이 떠
    # 있고, 그게 내비를 덮지 않는가」― 는 **터미널 탭**에서 재현된다.
    page.click('#vt-mnav-bottom .vt-mnav-btn[data-mnav="terminal"]')
    page.wait_for_selector(".vt-onboarding", state="visible", timeout=10000)
    page.wait_for_timeout(300)
    hit = page.evaluate(
        """() => {
          const bottom = document.getElementById('vt-mnav-bottom').getBoundingClientRect();
          const at = (x, y) => document.elementFromPoint(x, y)?.closest('#vt-mnav-bottom, .vt-onboarding')?.id || 'onboarding';
          const ob = document.querySelector('.vt-onboarding').getBoundingClientRect();
          return {
            obBottom: Math.round(ob.bottom),
            navTop: Math.round(bottom.top),
            bottom: at(bottom.left + bottom.width / 2, bottom.top + bottom.height / 2),
          };
        }"""
    )
    assert hit["bottom"] == "vt-mnav-bottom", f"온보딩이 하단 내비를 덮었다: {hit}"


def test_파일이_모달이_아니라_페인으로_열린다(page):
    """N35 §6 — 모달 코드 뷰어는 제거됐다. 파일은 pane 트리의 leaf(kind:viewer)로
    열리고, 그 내용이 pane 상자 안에 갇혀 있어야 한다(밖으로 새면 표면 레이어의
    터미널 위를 덮는다)."""
    target = str(ROOT / "VERSION")
    page.evaluate("(p) => window.openFileInPane(p)", target)
    page.wait_for_selector(".vt-pane-viewer .cm-line", timeout=15000)
    got = page.evaluate(
        """() => {
          const body = document.querySelector('.vt-pane-viewer');
          const pane = body.closest('.vt-pane');
          const pr = pane.getBoundingClientRect();
          const overflow = [...body.querySelectorAll('*')].filter((el) => {
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return false;
            return r.right > pr.right + 1 || r.left < pr.left - 1;
          }).length;
          return {
            modal: !!document.getElementById('vt-viewer'),
            lines: body.querySelectorAll('.cm-line').length,
            // 온보딩은 남아 있어도 되지만 **덮으면 안 된다** — 실제 히트 테스트로 본다.
            covered: document.elementFromPoint(pr.left + pr.width / 2, pr.top + pr.height / 2)
                       ?.closest('.vt-onboarding') != null,
            overflow,
          };
        }"""
    )
    assert not got["modal"], "모달 코드 뷰어가 다시 살아났다"
    assert got["lines"] > 0, f"파일 내용이 안 그려졌다: {got}"
    assert not got["covered"], "온보딩이 뷰어 페인 위를 덮고 있다"
    assert got["overflow"] == 0, f"뷰어 내용이 pane 밖으로 넘쳤다: {got}"


def _term_size(page, sid):
    return page.evaluate("(id) => { const t = window.getSession(id).term; return [t.cols, t.rows]; }", sid)


def test_파일_오버레이는_터미널_칸수를_바꾸지_않는다(isolated_page):
    """ADR-44 — 이 작업의 성공 기준 그 자체. 파일을 열고 닫아도 xterm의 cols×rows가
    그대로여야 한다(fit도 SIGWINCH도 없다). 대조군으로 `pane` 표면에서는 칸 수가
    **바뀌어야** 한다 — 그래야 이 테스트가 실제로 무언가를 재고 있다는 게 증명된다.
    실제 터미널을 렌더링하므로 전용 브라우저(isolated_page)를 쓴다."""
    page = isolated_page
    target = str(ROOT / "VERSION")
    page.evaluate("() => window.createSession && window.createSession()")
    page.wait_for_selector(".vt-pane .vt-pane-body .xterm", timeout=15000)
    page.wait_for_timeout(600)
    sid = page.evaluate("() => Object.keys(window.allSessions())[0]")
    try:
        before = _term_size(page, sid)

        page.evaluate("(p) => window.openFile(p)", target)
        page.wait_for_selector("#vt-file .cm-line", timeout=15000)
        page.wait_for_timeout(500)
        opened = _term_size(page, sid)
        geo = page.evaluate(
            """() => {
              const c = document.getElementById('terminal-container').getBoundingClientRect();
              const k = document.querySelector('#vt-file .vt-viewer-card').getBoundingClientRect();
              const below = document.elementFromPoint(c.left + c.width / 2, c.bottom - 8);
              return {
                inside: k.left >= c.left - 1 && k.right <= c.right + 1 && k.top >= c.top - 1 && k.bottom <= c.bottom + 1,
                parent: document.getElementById('vt-file').parentElement.id,
                belowIsOverlay: !!below?.closest('#vt-file'),
              };
            }"""
        )
        page.click("#vt-file .vt-vw-x")
        page.wait_for_selector("#vt-file", state="detached", timeout=5000)
        page.wait_for_timeout(500)
        closed = _term_size(page, sid)

        assert opened == before, f"오버레이를 열었더니 터미널 크기가 바뀌었다: {before} → {opened}"
        assert closed == before, f"오버레이를 닫았더니 터미널 크기가 바뀌었다: {before} → {closed}"
        assert geo["parent"] == "terminal-container", geo
        assert geo["inside"], f"오버레이가 터미널 영역 밖(레일·dock·topbar)까지 덮었다: {geo}"
        assert not geo["belowIsOverlay"], f"시트 아래(프롬프트 자리)가 가려졌다: {geo}"

        # 대조군 — pane 표면은 터미널 옆을 쪼개므로 칸 수가 줄어야 한다.
        page.evaluate("() => window.vtSettingsSet('viewer.surface', 'pane')")
        page.evaluate("(p) => window.openFile(p)", target)
        page.wait_for_selector(".vt-pane-viewer .cm-line", timeout=15000)
        page.wait_for_timeout(600)
        as_pane = _term_size(page, sid)
        assert as_pane[0] < before[0], f"대조군: pane으로 열면 cols가 줄어야 한다: {before} → {as_pane}"
    finally:
        page.evaluate("() => window.vtSettingsSet('viewer.surface', 'sheet')")
        page.wait_for_timeout(300)
        page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)


def test_떠_있는_창은_헤더로_옮겨지고_화면_밖으로_안_나간다(page):
    """ADR-44 §6 — float 표면. 헤더를 끌면 창이 움직이고, 컨테이너 밖으로 크게
    끌어도 헤더를 다시 잡을 수 있는 만큼(48×28)은 안에 남는다."""
    page.evaluate("() => window.vtSettingsSet('viewer.surface', 'float')")
    page.evaluate("() => window.vtSettingsSet('viewer.float.rect', '{}')")
    page.wait_for_timeout(300)
    try:
        page.evaluate("(p) => window.openFile(p)", str(ROOT / "VERSION"))
        page.wait_for_selector("#vt-file.surface-float .cm-line", timeout=15000)
        card = page.locator("#vt-file .vt-viewer-card")
        start = card.bounding_box()
        head = page.locator("#vt-file .vt-file-title").bounding_box()
        hx, hy = head["x"] + 20, head["y"] + head["height"] / 2

        page.mouse.move(hx, hy)
        page.mouse.down()
        page.mouse.move(hx - 120, hy + 60, steps=6)
        page.mouse.up()
        moved = card.bounding_box()
        assert round(moved["x"] - start["x"]) == -120, (start, moved)
        assert round(moved["y"] - start["y"]) == 60, (start, moved)

        page.mouse.move(moved["x"] + 20, moved["y"] + 10)
        page.mouse.down()
        page.mouse.move(moved["x"] + 5000, moved["y"] + 5000, steps=6)
        page.mouse.up()
        far = card.bounding_box()
        c = page.locator("#terminal-container").bounding_box()
        assert far["x"] <= c["x"] + c["width"] - 48 + 1, (far, c)
        assert far["y"] <= c["y"] + c["height"] - 28 + 1, (far, c)

        saved = page.evaluate("() => JSON.parse(window.vtSettingsGet('viewer.float.rect'))")
        assert round(saved["x"]) == round(far["x"] - c["x"]), (saved, far, c)
    finally:
        page.evaluate("() => { const el = document.getElementById('vt-file'); if (el) window.closePanel(el); }")
        page.evaluate("() => window.vtSettingsSet('viewer.surface', 'sheet')")
        page.evaluate("() => window.vtSettingsSet('viewer.float.rect', '{}')")
        page.wait_for_timeout(300)


def test_이미지가_페인_안에_인라인으로_그려진다(page):
    """[T3] 예전엔 이미지도 "바이너리 파일 — 미리보기를 지원하지 않습니다"였다.
    스크린샷·다이어그램은 원격에서 코드만큼 자주 열어보는 파일이다.

    실브라우저로만 볼 수 있는 것 둘: 이미지가 **실제로 디코드됐는가**
    (naturalWidth) 와, 그 그림이 **페인 상자 안에 갇혀 있는가**(밖으로 새면
    표면 레이어의 터미널 위를 덮는다 — 이 파일이 반복해서 잡아 온 회귀다)."""
    target = str(ROOT / "frontend" / "icon-192.png")
    page.evaluate("(p) => window.openFileInPane(p)", target)
    page.wait_for_selector(".vt-pane-viewer .vt-vw-image img", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('.vt-pane-viewer .vt-vw-image img')?.naturalWidth > 0",
        timeout=10000,
    )
    fit = page.evaluate(
        """() => {
          const img = document.querySelector('.vt-pane-viewer .vt-vw-image img');
          const pane = img.closest('.vt-pane');
          const i = img.getBoundingClientRect(), p = pane.getBoundingClientRect();
          return { overflowRight: Math.round(i.right - p.right), w: img.naturalWidth };
        }"""
    )
    assert fit["w"] == 192, fit
    assert fit["overflowRight"] <= 1, f"이미지가 페인 밖으로 나갔다: {fit}"


def _open_overlay(page, path, opts=None):
    page.evaluate("([p, o]) => window.openFile(p, o || {})", [path, opts])
    page.wait_for_selector("#vt-file .cm-line", timeout=15000)


def _close_overlay(page):
    page.evaluate("() => { const el = document.getElementById('vt-file'); if (el) window.closePanel(el); }")


_DOC = "document.querySelector('#vt-file .vt-vw-code-pane')._vtEditor.view.state.doc"


def test_큰_파일은_앞_4MB를_열고_더_보기로_이어_받는다(page):
    """파일 개편 단계 3·4 — 4MB를 넘는 파일은 1MB 구간을 이어 받아 앞 4MB까지 열고, 「더 보기」로 나머지를
    붙인다. 받은 범위에서만 찾기·줄 이동이 된다는 안내가 함께 보인다."""
    target = _viewer_file("big.log")
    size = os.path.getsize(target)
    try:
        _open_overlay(page, target)
        page.wait_for_selector("#vt-file .vt-vw-trunc", timeout=15000)
        note = page.locator("#vt-file .vt-vw-trunc").inner_text()
        assert "앞 4.0MB만 불러왔습니다" in note and "6.0MB" in note, note
        first = page.evaluate(f"() => {_DOC}.length")
        assert 4 * 1024 * 1024 <= first < size, first
        page.locator("#vt-file .vt-vw-trunc button", has_text="더 보기").click()
        page.wait_for_selector("#vt-file .vt-vw-trunc", state="detached", timeout=15000)
        assert page.evaluate(f"() => {_DOC}.length") == size
        assert page.evaluate(f"() => {_DOC}.lines") == 6 * 16384 + 1
    finally:
        _close_overlay(page)


def test_끝부분_보기는_끝에서_센_줄_번호를_보인다(page):
    target = _viewer_file("big.log")
    try:
        _open_overlay(page, target)
        page.wait_for_selector("#vt-file .vt-vw-trunc", timeout=15000)
        page.locator("#vt-file .vt-vw-trunc button", has_text="끝부분 보기").click()
        page.wait_for_selector("#vt-file .vt-vw-bar", timeout=15000)
        assert "끝부분만 보는 중" in page.locator("#vt-file .vt-vw-bar").inner_text()
        page.wait_for_function(
            "() => [...document.querySelectorAll('#vt-file .cm-lineNumbers .cm-gutterElement')]"
            ".some((e) => e.textContent === '-1')",
            timeout=10000,
        )
        last = page.evaluate(f"() => {{ const d = {_DOC}; return d.line(d.lines - 1).text; }}")
        assert last.startswith(f"{6 * 16384 - 1:08d} "), last
        page.locator("#vt-file .vt-vw-bar button", has_text="처음부터 보기").click()
        page.wait_for_selector("#vt-file .vt-vw-trunc", timeout=15000)
    finally:
        _close_overlay(page)


def test_인코딩을_모르면_이유를_띄우고_고르면_다시_연다(page):
    """D9 — 인코딩을 모르는 파일은 읽기 전용 이유를 보이고, 인코딩을 골라 다시 열 수 있다."""
    target = _viewer_file("k949.txt")
    try:
        _open_overlay(page, target)
        bar = page.locator("#vt-file .vt-vw-bar")
        bar.wait_for(timeout=10000)
        assert "읽기 전용" in bar.inner_text() and "UTF-8이 아닌" in bar.inner_text(), bar.inner_text()
        # 앱이 <select>를 자체 드롭다운으로 감싸 원본은 숨어 있다(ui/select) — 원본 값을 바꾸고 change를 보낸다.
        page.evaluate(
            "() => { const s = document.querySelector('#vt-file .vt-vw-bar select'); s.value = 'cp949';"
            " s.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
        page.wait_for_function(
            "() => document.querySelector('#vt-file .vt-vw-code-pane')?._vtEditor?.view.state.doc.line(1).text"
            " === '안녕하세요 뷰어 시험'",
            timeout=10000,
        )
        assert page.input_value("#vt-file .vt-vw-bar select") == "cp949"

        _open_overlay(page, _viewer_file("mixed.txt"))
        page.wait_for_selector("#vt-file .vt-vw-bar", timeout=10000)
        assert "줄 끝(LF·CRLF)이 섞여" in page.locator("#vt-file .vt-vw-bar").inner_text()
    finally:
        _close_overlay(page)


def test_줄_이동은_그_줄로_옮기고_표시한다(page):
    target = _viewer_file("rows.txt")
    try:
        _open_overlay(page, target, {"line": 700, "col": 3})
        page.wait_for_selector("#vt-file .cm-vt-target", timeout=10000)
        assert page.locator("#vt-file .cm-vt-target").inner_text() == "row 0700"
        # 같은 파일을 다른 줄로 다시 열면 다시 그리지 않고 옮기기만 한다.
        page.evaluate("(p) => window.openFile(p, { line: 12 })", target)
        page.wait_for_function(
            "() => document.querySelector('#vt-file .cm-vt-target')?.textContent === 'row 0012'", timeout=10000,
        )
    finally:
        _close_overlay(page)


def test_새로고침은_조건부로_묻고_304면_다시_그리지_않는다(page):
    target = _viewer_file("rows.txt")
    statuses = []
    page.on("response", lambda r: statuses.append(r.status) if "/api/fs/file" in r.url else None)
    try:
        _open_overlay(page, target)
        page.wait_for_timeout(300)
        before = page.evaluate("() => document.querySelector('#vt-file .cm-content').innerHTML")
        statuses.clear()
        page.click("#vt-file .vt-file-refresh")
        page.wait_for_timeout(800)
        assert statuses == [304], statuses
        assert page.evaluate("() => document.querySelector('#vt-file .cm-content').innerHTML") == before
    finally:
        _close_overlay(page)


def test_긴_한_줄은_구간을_이어_한_줄로_보인다(page):
    target = _viewer_file("long.min.js")
    try:
        _open_overlay(page, target)
        page.wait_for_function(f"() => {_DOC}.length === {1536 * 1024}", timeout=15000)
        assert page.evaluate(f"() => {_DOC}.lines") == 1
        assert page.locator("#vt-file .vt-vw-trunc").count() == 0
    finally:
        _close_overlay(page)


def test_터미널의_경로_줄_링크는_그_줄로_연다(isolated_page):
    """term/links.js — `path:줄:열`을 누르면 그 파일을 열고 그 줄로 간다."""
    page = isolated_page
    target = _viewer_file("rows.txt")
    page.evaluate("() => window.createSession && window.createSession()")
    page.wait_for_selector(".vt-pane .vt-pane-body .xterm", timeout=15000)
    page.wait_for_timeout(800)
    sid = page.evaluate("() => Object.keys(window.allSessions())[0]")
    try:
        link = f"{target}:321:2"
        # 셸 출력과 섞이지 않게 화면을 비우고 첫 줄에 링크를 쓴다(xterm 버퍼에 직접 — PTY를 거치지 않는다).
        page.evaluate(
            "([id, s]) => new Promise((r) => window.getSession(id).term.write('\\x1b[2J\\x1b[H' + s + '\\r\\n', r))",
            [sid, link],
        )
        page.wait_for_timeout(300)
        box = page.evaluate(
            """(id) => {
              const t = window.getSession(id).term;
              const screen = t.element.querySelector('.xterm-screen').getBoundingClientRect();
              const cell = t._core._renderService.dimensions.css.cell;
              return { x: screen.left + cell.width * 5.5, y: screen.top + cell.height * 0.5 };
            }""",
            sid,
        )
        page.mouse.move(box["x"], box["y"])
        page.wait_for_timeout(300)
        page.mouse.move(box["x"] + 2, box["y"])
        page.wait_for_timeout(300)
        page.mouse.click(box["x"] + 2, box["y"])
        page.wait_for_selector("#vt-file .cm-vt-target", timeout=15000)
        assert page.locator("#vt-file .cm-vt-target").inner_text() == "row 0321"
    finally:
        _close_overlay(page)
        page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)


def test_레일_파일_버튼은_사라지고_팔레트가_그_자리다(page):
    """§6 — 「파일」 진입점은 팔레트로 옮겨갔다. 옛 rail 버튼이 남아 있으면
    누를 대상이 없는 버튼이 된다."""
    assert page.evaluate("() => !document.getElementById('vt-rail-file')")
    page.evaluate("() => window.getAction('viewer.show')()")
    page.wait_for_selector("#vt-qo-input", timeout=5000)
    page.keyboard.press("Escape")


# ── N3(60-settings-palette.md §1) 기기 스코프 설정 ─────────────────────────

def test_사이드바_폭이_기기_설정으로_저장되고_새로고침에도_유지된다(page, server):
    """사이드바 폭(`ui.side.width`, 활동 전체가 공유)이 /api/device-settings에 저장되고
    실제 새로고침 뒤에도 돌아오는가(재부팅 없이는 캐시만으로도 통과하는 버그를 놓친다)."""
    base, token = server
    page.evaluate("() => { window.vtSettingsSet('ui.side.collapsed', false); window.vtSettingsSet('ui.side.width', 300); }")
    page.wait_for_timeout(400)
    # 저장 위치는 OS별 프로필(mac/windows/linux/mobile)이라 호스트마다 다르다 — 어느 프로필이든
    # 이 값이 서버에 있으면 된다. 프로필 없는 조회는 다른 저장소(기기 id)를 본다.
    saved = [
        page.request.get(f"{base}/api/device-settings", params={"profile": prof}).json()["settings"].get("ui.side.width")
        for prof in ("mac", "windows", "linux", "mobile")
    ]
    assert 300 in saved, saved

    page.reload()
    page.wait_for_function(
        "() => document.documentElement.dataset.appBooted === 'true'", timeout=20000
    )
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'open'", timeout=10000)
    page.wait_for_timeout(300)
    # `--vt-side-w`는 식(min(저장 폭, 40vw))이라 px로 내려오지 않는다. 저장값 변수와 실제로
    # 그려진 폭을 본다. 뷰포트는 WIDE(1440)라 40vw=576px, 즉 min은 300px이다.
    var = page.evaluate(
        "() => document.documentElement.style.getPropertyValue('--vt-side-stored')"
    )
    assert var == "300px", f"저장값이 CSS 변수에 안 들어왔다: {var}"
    got = page.evaluate(
        "() => Math.round(document.getElementById('vt-side').getBoundingClientRect().width)"
    )
    assert abs(got - 300) <= 1, f"새로고침 후 사이드바 폭이 안 돌아왔다: {got}px (변수 {var})"


# ── N43 §8 리사이즈 오버레이 ────────────────────────────────────────────────

def test_리사이즈_오버레이가_분할선_드래그_중에만_보인다(isolated_page):
    """§8 — 드래그 중 픽셀 크기 + 칸 수가 뜨고, 놓으면 200ms 뒤 사라진다.
    CI에는 tmux 세션이 없으므로 일반 터미널 세션을 직접 만든다.

    **전용 브라우저**(isolated_page)를 쓴다 — `isolated_page` 픽스처 주석 참고.

    이 테스트가 만드는 세션·탭은 **서버 쪽 상태**(session_store + 공유
    workspace.json)라 브라우저 컨텍스트를 닫아도 안 없어진다. 처음엔
    `DELETE /api/sessions/{id}`로만 정리했는데 그건 session_store에서만
    지운다 — 탭을 만들 때 클라이언트가 이미 workspace.json에 저장해 둔 항목은
    그대로 남아, 뒤 테스트가 새로고침할 때마다 **죽은 세션을 복원하려는
    부팅 경로**를 타면서 그 뒤로 이어지는 여러 테스트가 통째로 깨졌다(실제
    재현 — dock 소스컨트롤 테스트 4개가 연쇄로 실패/에러). `window.
    removeSession()`(닫기 버튼과 같은 경로 — session_store 삭제 + workspace
    재저장을 한 번에 한다)을 써야 한다.
    """
    page = isolated_page
    page.evaluate("() => window.createSession && window.createSession()")
    page.wait_for_selector(".vt-pane .vt-pane-body", timeout=15000)
    page.wait_for_timeout(500)   # PTY 연결 + 첫 fit
    page.evaluate("() => window.splitActivePane && window.splitActivePane('row')")
    page.wait_for_selector(".vt-split-resizer", timeout=10000)
    session_ids = page.evaluate("() => Object.keys(window.allSessions())")
    try:
        # 방금 분할로 생긴 새 pane은 비어 있다 — 세션이 있는 쪽을 활성으로 되돌린다.
        page.evaluate(
            "() => { const w = window; const sid = Object.keys(w.allSessions())[0]; w.switchTo(sid); }"
        )
        page.wait_for_timeout(200)

        assert page.evaluate("() => !document.getElementById('vt-resize-overlay')"), \
            "드래그 전에는 오버레이가 아예 없어야 한다"

        box = page.locator(".vt-split-resizer").bounding_box()
        page.mouse.move(box["x"] + 3, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 80, box["y"] + box["height"] / 2, steps=5)
        page.wait_for_timeout(150)
        during = page.evaluate(
            """() => {
              const el = document.getElementById('vt-resize-overlay');
              return el && !el.hidden ? {
                dims: el.querySelector('.vt-ro-dims').textContent,
                cellsShown: !el.querySelector('.vt-ro-cells').hidden,
              } : null;
            }"""
        )
        page.mouse.up()
        assert during, "드래그 중 오버레이가 안 보였다"
        assert re.match(r"^\d+ × \d+$", during["dims"]), f"픽셀 캡션 형식이 다르다: {during}"
        assert during["cellsShown"], "터미널 pane인데 칸 수(cols×rows)가 안 보였다"

        page.wait_for_timeout(60)
        just_after = page.evaluate("() => document.getElementById('vt-resize-overlay').hidden")
        assert not just_after, "놓자마자 사라지면 안 된다(200ms 유예)"

        page.wait_for_timeout(300)
        assert page.evaluate("() => document.getElementById('vt-resize-overlay').hidden"), \
            "200ms 뒤에는 사라져야 한다"
    finally:
        for sid in session_ids:
            page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)
        page.wait_for_timeout(300)   # saveWorkspace()가 서버에 반영될 시간


def test_연결된_화면이_실제_tmux_클라이언트를_표시한다(isolated_page):
    page = isolated_page
    try:
        before = set(page.evaluate("() => Object.keys(window.allSessions())"))
        page.evaluate("() => window.createTmuxSession()")
        page.wait_for_function("(n) => Object.keys(window.allSessions()).length > n", arg=len(before), timeout=15000)
        page.evaluate("() => window.vtShowClients()")
        page.wait_for_selector("#vt-connected-screens .vt-clients-row.me", timeout=15000)
        assert page.locator("#vt-connected-screens .vt-clients-badge").inner_text() == "나"
        assert page.locator("#vt-connected-screens .vt-clients-head button").is_disabled()
        assert page.locator("#vt-connected-screens .vt-clients-title").is_hidden()
    finally:
        page.evaluate("() => window.vtShowClients && document.getElementById('vt-connected-screens') && window.vtShowClients()")
        for sid in set(page.evaluate("() => Object.keys(window.allSessions())")) - before:
            page.evaluate("(id) => window.removeSession(id)", sid)


def test_두_웹_화면을_따로_세고_상대만_끊는다(isolated_page):
    first = isolated_page
    second_context = first.context.browser.new_context(viewport=WIDE)
    _login(second_context, first.url.split("/", 3)[0] + "//" + first.url.split("/", 3)[2])
    second = second_context.new_page()
    try:
        before = set(first.evaluate("() => Object.keys(window.allSessions())"))
        first.evaluate("() => window.createTmuxSession()")
        first.wait_for_function("(n) => Object.keys(window.allSessions()).length > n", arg=len(before), timeout=15000)
        new_id = next(iter(set(first.evaluate("() => Object.keys(window.allSessions())")) - before))
        name = first.evaluate("(id) => window.allSessions()[id].tmuxName", new_id)
        _boot(second, first.url)
        second.evaluate("(name) => window.attachTmux(name)", name)
        second.wait_for_function("(name) => Object.values(window.allSessions()).some(s => s.tmuxName === name)", arg=name)
        first.evaluate("() => window.vtShowClients()")
        first.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 2")
        assert first.locator("#vt-connected-screens .vt-clients-row.me").count() == 1
        second.evaluate("() => window.vtShowClients()")
        second.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 2")
        assert second.locator("#vt-connected-screens .vt-clients-row.me").count() == 1

        # 같은 탭 새로고침은 attach를 늘리지 않고 자기 화면을 복구한다.
        _boot(second, second.url)
        second.evaluate("(name) => window.vtShowClients(Object.entries(window.allSessions()).find(([, s]) => s.tmuxName === name)?.[0])", name)
        second.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 2")
        assert second.locator("#vt-connected-screens .vt-clients-row.me").count() == 1

        first.locator("#vt-connected-screens .vt-clients-detach").click()
        second.wait_for_selector("#conn-overlay", timeout=15000)
        second.wait_for_function("() => document.querySelector('#conn-overlay')?.textContent.includes('다른 화면에서 이 화면을 끊음')", timeout=15000)
        assert "다른 화면에서 이 화면을 끊음" in second.locator("#conn-overlay").inner_text()
        first.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 1", timeout=15000)
        assert first.locator("#vt-connected-screens .vt-clients-row.me").count() == 1
    finally:
        second_context.close()
        for sid in set(first.evaluate("() => Object.keys(window.allSessions())")) - before:
            first.evaluate("(id) => window.removeSession(id)", sid)


def test_브라우저_탭을_닫으면_그_화면만_목록에서_빠진다(isolated_page):
    first = isolated_page
    second_context = first.context.browser.new_context(viewport=WIDE)
    _login(second_context, first.url.split("/", 3)[0] + "//" + first.url.split("/", 3)[2])
    second = second_context.new_page()
    before = set(first.evaluate("() => Object.keys(window.allSessions())"))
    try:
        first.evaluate("() => window.createTmuxSession()")
        first.wait_for_function("(n) => Object.keys(window.allSessions()).length > n", arg=len(before), timeout=15000)
        new_id = next(iter(set(first.evaluate("() => Object.keys(window.allSessions())")) - before))
        name = first.evaluate("(id) => window.allSessions()[id].tmuxName", new_id)
        _boot(second, first.url)
        second.evaluate("(name) => window.attachTmux(name)", name)
        first.evaluate("() => window.vtShowClients()")
        first.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 2")
        second_context.close()
        first.wait_for_function("() => document.querySelectorAll('#vt-connected-screens .vt-clients-row').length === 1", timeout=15000)
        assert first.locator("#vt-connected-screens .vt-clients-row.me").count() == 1
    finally:
        if not second.is_closed():
            second_context.close()
        for sid in set(first.evaluate("() => Object.keys(window.allSessions())")) - before:
            first.evaluate("(id) => window.removeSession(id)", sid)


def test_그룹의_세션을_클릭하면_짝을_밀어내지_않고_그_칸으로만_이동한다(isolated_page):
    """2026-09-21(사용자 지적) — 레일에서 그룹화된 세션을 클릭하면 "바로가기"가
    아니라 그 세션이 활성 pane에 배정돼(옛 `switchTo` 경로) 짝 세션을 그 칸에서
    밀어냈다: 화면은 분할된 채로 남는데 짝은 트리에서 사라지고 클릭한 세션만
    보였다. `window.revealSession`(term/session.js)이 그 대체다.

    재현에 두 번째 탭은 필요 없다 — **같은 탭 안에서 활성 pane이 클릭한
    세션의 칸이 아닌 짝의 칸일 때**(방금 짝 쪽에 포커스가 있었을 때, 즉 rail을
    누르기 직전의 가장 흔한 상태) 옛 코드의 `setSession`이 두 칸을 동시에
    건드렸다: 클릭한 세션이 이미 있던 칸을 지우고, 활성 pane(짝의 칸)을
    덮어썼다.
    """
    page = isolated_page
    page.evaluate("() => window.createSession && window.createSession()")
    page.wait_for_selector(".vt-pane .vt-pane-body", timeout=15000)
    page.wait_for_timeout(500)
    page.evaluate("() => window.splitActivePane && window.splitActivePane('row')")
    page.wait_for_selector(".vt-split-resizer", timeout=10000)
    # 방금 분할한 새 pane이 활성이다 — 거기에 두 번째 세션을 만들면 그 칸에
    # 배정된다(addSession의 마지막 줄이 switchTo). 이제 A는 첫 칸에, B는
    # 활성 칸(둘째)에 있다 — 그룹이 만들어진 상태이자, "짝(A) 쪽이 아니라
    # B 쪽이 활성"이라는 버그 재현에 필요한 조건이다.
    # place:'pane' — 기본은 새 단독 뷰(탭)라서 분할한 칸이 아니라 새 탭으로 간다(term/session.js, 2026-10-03).
    page.evaluate("() => window.createSession && window.createSession({ place: 'pane' })")
    page.wait_for_timeout(500)
    session_ids = page.evaluate("() => Object.keys(window.allSessions())")
    assert len(session_ids) == 2, f"두 세션이 만들어져야 한다: {session_ids}"
    sid_a, sid_b = session_ids
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('.vt-pane .xterm').length === 2", timeout=15000
        )
        before = page.evaluate(
            """([a, b]) => ({
              panes: document.querySelectorAll('.vt-pane').length,
              aSession: !!Object.values(window.allSessions())[0],
            })""",
            session_ids,
        )
        assert before["panes"] == 2

        # 레일 클릭을 흉내낸다 — A(첫 칸, 지금은 비활성)를 연다.
        page.evaluate("(id) => window.revealSession && window.revealSession(id)", sid_a)
        page.wait_for_timeout(200)

        got = page.evaluate(
            """([a, b]) => {
              const paneOf = (sid) => {
                const w = window.allSessions()[sid];
                return w && w.wrapper ? w.wrapper.closest('.vt-pane')?.dataset.paneId : null;
              };
              return {
                panes: document.querySelectorAll('.vt-pane').length,
                paneA: paneOf(a),
                paneB: paneOf(b),
                activePaneHasA: document.querySelector('.vt-pane.active')
                  ?.contains(window.allSessions()[a]?.wrapper) || false,
              };
            }""",
            session_ids,
        )
        assert got["panes"] == 2, f"분할 자체가 없어지면 안 된다: {got}"
        assert got["paneA"], f"A가 여전히 어느 칸엔가 있어야 한다: {got}"
        assert got["paneB"], f"짝(B)이 트리에서 사라지면 안 된다(이게 버그였다): {got}"
        assert got["paneA"] != got["paneB"], f"둘이 같은 칸에 겹치면 안 된다: {got}"
        assert got["activePaneHasA"], f"A를 열었으니 A가 있는 칸이 활성이어야 한다: {got}"
    finally:
        for sid in session_ids:
            page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)
        page.wait_for_timeout(300)


def test_파일을_터미널_칸에_끌어다_놓으면_경로가_꽂힌다(isolated_page):
    """R6 — 터미널 칸 드롭은 **올리고 나서 그 칸의 세션에 경로를 붙여넣는다**.

    파일 패널 드롭(위)과 규칙이 갈리는 지점이라 따로 본다. 살아 있는 PTY가
    있어야 붙여넣기가 관찰되므로 전용 브라우저 픽스처를 쓴다 — 실제 터미널을
    렌더링하는 테스트가 공유 Chromium에서 열화되는 문제(픽스처 주석) 때문이다.
    """
    page = isolated_page
    page.evaluate("() => window.createSession && window.createSession()")
    # ⚠ `.vt-pane-body`를 기다리는 것으로는 부족하다 — **빈 pane에도 있다.**
    # 세션이 아직 그 칸에 안 들어왔는데 드롭하면 `_paneSessionId()`가 null이라
    # 업로드만 되고 경로는 안 꽂힌다(CI에서 실제로 그렇게 깨졌다, 로컬은
    # 800ms 대기로 우연히 가려져 있었다). 터미널이 그 칸 안에 실제로 붙었고
    # WS가 열렸는지를 직접 본다.
    page.wait_for_selector(".vt-pane .vt-pane-body .xterm", timeout=20000)
    page.wait_for_function(
        """() => {
          const s = Object.values(window.allSessions())[0];
          return !!(s && s.ws && s.ws.readyState === 1 && s.wrapper);
        }""",
        timeout=20000,
    )
    page.wait_for_timeout(500)   # 셸 프롬프트가 한 번 찍힐 여유
    session_ids = page.evaluate("() => Object.keys(window.allSessions())")
    try:
        name = f"e2e-pane-{int(time.time())}.txt"
        page.evaluate(
            """([name]) => {
              const dt = new DataTransfer();
              dt.items.add(new File(['x'], name, { type: 'text/plain' }));
              const pane = document.querySelector('.vt-pane');
              for (const type of ['dragover', 'drop']) {
                pane.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
              }
            }""",
            [name],
        )
        # 단계를 나눠 본다: 먼저 **올라갔는지**. 한 번에 버퍼만 기다리면 실패했을
        # 때 "업로드가 안 된 것"과 "경로가 안 꽂힌 것"을 구분할 수 없다.
        page.wait_for_function(
            """async (n) => {
              const r = await fetch('/api/files', { credentials: 'same-origin' });
              const j = await r.json().catch(() => null);
              return !!(j && (j.files || j).some?.((f) => (f.name || '').includes(n)));
            }""",
            arg=name, timeout=15000,
        )

        # 그다음 **경로가 꽂혔는지**. DOM이 아니라 xterm 버퍼를 읽는다 —
        # 렌더러가 WebGL이라 화면은 캔버스고 `.xterm-screen`의 textContent는 비어
        # 있다(실측으로 한 번 헛다리를 짚었다).
        # 줄바꿈으로 접힌 줄(isWrapped)은 이어 붙인다 — 경로가 칸 폭보다 길면 파일 이름이 두 줄에
        # 걸쳐 그려지고, 줄마다 '\n'을 끼우면 이름이 끊겨 못 찾는다(칸 폭에 따라 결과가 갈렸다).
        read_buf = """() => {
          const s = Object.values(window.allSessions())[0];
          const b = s && s.term && s.term.buffer && s.term.buffer.active;
          if (!b) return '';
          let out = '';
          for (let i = 0; i < b.length; i++) {
            const line = b.getLine(i);
            if (!line) continue;
            out += (line.isWrapped ? '' : '\\n') + line.translateToString(true);
          }
          return out;
        }"""
        page.wait_for_function(
            "(n) => { const f = " + read_buf + "; return f().includes(n); }",
            arg=name, timeout=20000,
        )
        screen = page.evaluate(read_buf)
        assert name in screen, screen[-300:]
        # 붙여넣기지 실행이 아니다 — 경로만 들어가고 명령은 안 돌아야 한다.
        assert "command not found" not in screen, f"경로가 실행돼 버렸다: {screen[-300:]}"
    finally:
        for sid in session_ids:
            page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)
        page.wait_for_timeout(300)


# ── N35 §6 dock ───────────────────────────────────────────────────────────

def test_활동_막대가_문서대로_그려진다(page):
    """세션 | 변경 | 파일 | 큐 | 첨부 | 스니펫 | (포트) | (사용량) | 시스템 — capability가 없는 활동은
    아예 없다. 세로 탭 목록이고, 아래 무리(MCP · 음성 · 설정)는 탭이 아니다."""
    page.wait_for_selector('#vt-activitybar [role="tab"]', timeout=10000)
    shape = page.evaluate(
        """() => {
          const list = document.querySelector('#vt-activitybar [role="tablist"]');
          const bar = document.getElementById('vt-activitybar').getBoundingClientRect();
          const tabs = [...list.querySelectorAll('[role="tab"]')];
          return {
            orient: list.getAttribute('aria-orientation'),
            labels: tabs.map((t) => t.getAttribute('aria-label')),
            barW: Math.round(bar.width),
            itemH: Math.round(tabs[0].getBoundingClientRect().height),
            tabsInBottom: document.querySelectorAll('.vt-act-bottom [role="tab"]').length,
            settings: !!document.querySelector('.vt-act-bottom .vt-act-settings'),
          };
        }"""
    )
    assert shape["orient"] == "vertical", shape
    assert shape["labels"][:4] == ["세션", "변경", "파일", "큐"], shape
    assert shape["labels"][-1] == "시스템", shape
    assert shape["barW"] == 48 and shape["itemH"] == 44, shape
    assert shape["tabsInBottom"] == 0 and shape["settings"], shape


def test_작업대_기본_폭과_본문_패딩이_티어와_접힘을_따라간다(page):
    """§5에서 실제로 났던 버그의 작업대 판: 접거나 폭이 바뀌었는데 body padding이 옛 폭 그대로면
    화면에 빈 띠가 생기거나 칸이 사이드바 밑으로 들어간다. 저장값이 없는 기기에서
    wide(1440)는 펼침 360, 접으면 세션 48px 띠, regular(900)는 min(저장 폭, 260, 40vw)."""
    page.set_viewport_size(WIDE)
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'open'", timeout=10000)
    page.wait_for_timeout(200)
    measure = """() => ({
        mode: document.documentElement.dataset.vtSide,
        side: Math.round(document.getElementById('vt-side').getBoundingClientRect().width),
        wb: Math.round(document.getElementById('vt-workbench').getBoundingClientRect().width),
        padL: getComputedStyle(document.body).paddingLeft,
        padR: getComputedStyle(document.body).paddingRight,
      })"""
    got = page.evaluate(measure)
    assert got["wb"] == 48 + 360, got
    assert got["padL"] == "408px", f"본문 패딩이 사이드바 폭을 안 따라간다: {got}"
    assert got["padR"] == "0px", f"오른쪽 dock 자리가 남았다: {got}"

    _dock_tab(page, "세션").click()   # 펼친 세션 재클릭 = 48px 띠
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'strip'", timeout=5000)
    got = page.evaluate(measure)
    assert got["wb"] == 96 and got["padL"] == "96px", got

    _dock_tab(page, "세션").click()
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'open'", timeout=5000)
    page.set_viewport_size(REGULAR)
    page.wait_for_timeout(300)
    got = page.evaluate(measure)
    assert got["wb"] == 48 + 260 and got["padL"] == "308px", f"regular 상한 260이 안 걸렸다: {got}"


def test_regular는_저장값이_없으면_사이드바가_접힌_채로_시작한다(page):
    page.set_viewport_size(REGULAR)
    page.reload()
    page.wait_for_function("() => !!document.documentElement.dataset.vtSide", timeout=20000)
    page.wait_for_timeout(200)
    mode = page.evaluate("() => document.documentElement.dataset.vtSide")
    assert mode == "strip", f"regular는 저장값이 없으면 접힘(세션 띠)으로 시작해야 한다: {mode}"


def test_dock_탭을_누르면_그_패널이_dock_안에_마운트된다(page):
    """모달 backdrop이 아니라 사이드바의 dock 본문에 들어가는가(패널 렌더러 재사용의 핵심).
    같은 활동을 다시 누르면 접힌다(도구는 0) — 진입점을 이중화하지 않기 위한 규칙."""
    _open_dock(page, "큐")
    got = page.evaluate(
        """() => {
             const q = document.getElementById('vt-queue');
             const dockBody = document.querySelector('#vt-dock .vt-dock-body');
             return { exists: !!q, inDock: !!(q && dockBody && dockBody.contains(q)),
                      backdrop: !!(q && q.classList.contains('vt-viewer-backdrop')),
                      inside: !!(q && q.getBoundingClientRect().right <= document.getElementById('vt-dock').getBoundingClientRect().right + 1) };
           }"""
    )
    assert got["exists"], "큐 탭을 눌렀는데 패널이 안 생겼다"
    assert got["inDock"], f"패널이 dock 본문 밖에 있다: {got}"
    assert not got["backdrop"], "dock 안인데 모달 backdrop 클래스가 붙었다"
    assert got["inside"], f"패널이 dock 폭을 넘는다: {got}"

    # 같은 활동 재클릭 = 접기(도구는 0)
    _dock_tab(page, "큐").click()
    page.wait_for_function(
        "() => document.documentElement.dataset.vtSide === 'closed'", timeout=5000
    )
    assert page.evaluate("() => !document.getElementById('vt-queue')"), "접었는데 패널이 남았다"


def test_dock_탭을_페인으로_끌어내면_그_칸이_패널_칸이_되고_dock은_접힌다(page):
    """N4 3단계 — dock 탭 → pane 드래그.

    브라우저 없이 못 보는 것이 둘이다. 첫째, **네이티브 HTML5 DnD가 실제로
    성립하는가** — `draggable` 속성, `dataTransfer` mime, 드롭 타겟의
    `preventDefault`가 하나라도 어긋나면 jsdom에서는 티가 안 나고 실브라우저에서만
    조용히 아무 일도 안 일어난다. 둘째, 5구역 판정이 **실제 좌표**로 돌아가는가
    (`computeDropZone`의 단위 테스트는 가짜 rect를 쓴다).

    시작 상태로 뷰어 칸을 먼저 연다. 세션이 0개면 온보딩이 화면 전체를 덮어
    (position:fixed·z-index:500) pane이 드롭을 받을 수 없다 — 뷰어/패널 칸이
    하나라도 생기면 `body.vt-has-viewer-pane`이 그걸 눌러준다(panes.js).
    PTY를 띄우지 않고 그 상태를 만드는 가장 싼 방법이다."""
    page.evaluate("(p) => window.openFileInPane(p)", str(ROOT / "VERSION"))
    page.wait_for_selector(".vt-pane-viewer", timeout=15000)

    _open_dock(page, "큐")
    tab = _dock_tab(page, "큐")
    pane = page.locator(".vt-pane").first
    box = pane.bounding_box()

    # 오른쪽 가장자리(폭의 10% 지점) = 그 방향으로 분할. 가운데였다면 교체다.
    tab.drag_to(pane, target_position={"x": box["width"] * 0.9, "y": box["height"] / 2})

    page.wait_for_selector(".vt-pane-body .vt-pane-panel-root", timeout=15000)
    got = page.evaluate(
        """() => {
          const root = document.querySelector('.vt-pane-body .vt-pane-panel-root');
          const pane = root.closest('.vt-pane');
          const panes = [...document.querySelectorAll('.vt-pane')];
          const pr = pane.getBoundingClientRect();
          return {
            id: root.id,
            panes: panes.length,
            // 분할이면 패널 칸이 **오른쪽**에 와야 한다.
            isRightmost: panes.every((p) => p.getBoundingClientRect().left <= pr.left + 1),
            head: pane.querySelector('.vt-pane-name')?.textContent.trim(),
            dockCollapsed: document.documentElement.dataset.vtSide === 'closed',
            stillInDock: !!document.querySelector('#vt-dock .vt-dock-body #vt-queue'),
            overflow: [...root.querySelectorAll('*')].filter((el) => {
              const r = el.getBoundingClientRect();
              if (!r.width && !r.height) return false;
              return r.right > pr.right + 1 || r.left < pr.left - 1;
            }).length,
          };
        }"""
    )
    assert got["id"].startswith("vt-queue--pane-"), f"pane 인스턴스 id 규칙이 깨졌다: {got}"
    assert got["panes"] == 2, f"오른쪽 가장자리 드롭인데 분할이 안 됐다: {got}"
    assert got["isRightmost"], f"새 칸이 오른쪽에 안 왔다: {got}"
    assert got["head"] == "큐", f"pane 헤더 이름이 패널 라벨이 아니다: {got}"
    # 끌어낸 탭이 dock에 그대로 남아 있으면 같은 화면이 두 곳에 뜬다.
    assert got["dockCollapsed"], f"끌어냈는데 dock이 안 접혔다: {got}"
    assert not got["stillInDock"], f"dock 본문에 큐가 남았다: {got}"
    assert got["overflow"] == 0, f"패널 내용이 pane 밖으로 넘쳤다: {got}"


def test_dock_소스컨트롤_탭이_저장소_상태를_읽어온다(page):
    """40 §3 — 머리말(저장소·브랜치)과 목록이 실제 API 응답으로 채워지는가."""
    _open_dock(page, "변경")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    # 머리말(status)과 커밋 기록(log)은 **서로 다른 요청**이라 따로 도착한다.
    # 패널이 진짜 지연 청크가 된 뒤로는 둘 사이 간격이 눈에 띄게 벌어져서,
    # 기다리지 않으면 log가 0인 순간을 잡는다(실측 3회 중 1회).
    # 기다림과 읽기를 한 번에 한다 — 행이 보인 뒤 따로 읽으면, 그 사이 패널이 목록을 다시 그리며 잠깐 비운 순간을 잡았다(CI 2026-10-19).
    got = page.wait_for_function(
        """() => {
             const log = document.querySelectorAll('#vt-dock-scm .vt-vw-crow').length;
             const head = document.querySelector('#vt-scm-head')?.innerText || '';
             if (!log || !head.trim()) return null;
             return { head, log, inDock: !!document.querySelector('#vt-dock .vt-dock-body #vt-dock-scm') };
           }""",
        timeout=15000,
    ).json_value()
    assert got["inDock"], "소스컨트롤 패널이 dock 밖에 있다"
    assert got["log"] > 0, f"커밋 기록이 비었다: {got}"
    assert got["head"].strip(), "머리말이 비었다 — 저장소를 못 찾았다"


def test_dock_소스컨트롤_stage_commit은_실동작하고_push_PR은_렌더되지_않는다(page):
    """40 §5 · ADR-27 — 2.1.1: stage/unstage/커밋은 ambient git로 실제 동작.
    push·PR/MR 버튼과 계정 칩은 렌더 자체가 없다(disabled로도 남기지 않는다)."""
    _open_dock(page, "변경")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    # 머리말(status)과 커밋 기록(log)은 **서로 다른 요청**이라 따로 도착한다.
    # 패널이 진짜 지연 청크가 된 뒤로는 둘 사이 간격이 눈에 띄게 벌어져서,
    # 기다리지 않으면 log가 0인 순간을 잡는다(실측 3회 중 1회).
    page.wait_for_selector("#vt-dock-scm .vt-vw-crow", timeout=15000)
    got = page.evaluate(
        """() => {
             const texts = [...document.querySelectorAll('#vt-dock-scm button')]
               .map((b) => b.textContent.trim().toLowerCase());
             const gact = document.querySelector('#vt-dock-scm .vt-vw-gact');
             return {
               texts,
               hasAccountChip: !!document.querySelector('.vt-scm-account'),
               hasFoot: !!document.querySelector('.vt-scm-foot'),
               hasCommitBox: !!document.querySelector('#vt-dock-scm .vt-vw-gmsg'),
               stageDisabled: gact ? gact.disabled : null,
             };
           }"""
    )
    assert not any("push" in t for t in got["texts"]), got["texts"]
    assert not any("pr" in t and "만들기" in t for t in got["texts"]), got["texts"]
    assert not got["hasAccountChip"], "ADR-27로 폐기된 계정 칩이 남아있다"
    assert not got["hasFoot"], "ADR-27로 폐기된 push/PR 버튼 줄(.vt-scm-foot)이 남아있다"
    assert got["hasCommitBox"], "커밋 입력창이 없다 — readOnly가 아직 true인 듯하다"
    if got["stageDisabled"] is not None:
        assert got["stageDisabled"] is False, "스테이지 +/− 버튼이 아직 비활성이다"


def test_dock_소스컨트롤_파일_클릭시_뷰어로_연다(page):
    """40 §5 — 파일 행의 「뷰어」 버튼이 파일을 연다. ADR-44부터 표면 설정을 따르므로
    기본값(sheet)에서는 오버레이로 열린다. 변경된 파일이 없으면 건너뛴다."""
    _open_dock(page, "변경")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    # 머리말(status)과 커밋 기록(log)은 **서로 다른 요청**이라 따로 도착한다.
    # 패널이 진짜 지연 청크가 된 뒤로는 둘 사이 간격이 눈에 띄게 벌어져서,
    # 기다리지 않으면 log가 0인 순간을 잡는다(실측 3회 중 1회).
    page.wait_for_selector("#vt-dock-scm .vt-vw-crow", timeout=15000)
    open_btns = page.locator("#vt-dock-scm .vt-vw-gopen")
    if open_btns.count() == 0:
        pytest.skip("작업 트리가 깨끗해 열어볼 파일이 없다")
    # 2026-09-17 — 행 액션은 hover/focus에서만 드러난다(visibility). 사람이 하는
    # 순서 그대로 행에 마우스를 올리고 누른다 — 검사를 느슨하게 바꾸는 게
    # 아니라, "hover하면 진짜로 드러나고 눌린다"를 하나 더 본다.
    open_btns.first.locator("xpath=..").hover()
    expect(open_btns.first).to_be_visible()
    open_btns.first.click()
    page.wait_for_selector("#vt-file .vt-vw-code-pane", timeout=15000)


def test_dock_소스컨트롤_diff_줄에서_큐_코멘트가_열린다(page):
    """40 §3의 「줄 클릭 → 큐에 코멘트」. 2.0 뷰어의 같은 코드를 dock에서
    그대로 쓰는지 실제 클릭으로 확인한다 — 변경된 파일이 없으면 건너뛴다."""
    _open_dock(page, "변경")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    # 머리말(status)과 커밋 기록(log)은 **서로 다른 요청**이라 따로 도착한다.
    # 패널이 진짜 지연 청크가 된 뒤로는 둘 사이 간격이 눈에 띄게 벌어져서,
    # 기다리지 않으면 log가 0인 순간을 잡는다(실측 3회 중 1회).
    page.wait_for_selector("#vt-dock-scm .vt-vw-crow", timeout=15000)
    rows = page.locator("#vt-dock-scm .vt-vw-gsec .vt-vw-grow")
    if rows.count() == 0:
        pytest.skip("작업 트리가 깨끗해 diff를 열 파일이 없다")
    rows.first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-dl", timeout=15000)
    page.locator("#vt-dock-scm .vt-vw-dl").first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-annotate", timeout=5000)
    # 되돌아가기 — dock은 한 탭 안에서 상태↔diff를 오간다(모달을 새로 안 띄운다)
    page.locator("#vt-dock-scm .vt-vw-cback").first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)


def test_사이드바_폭_리사이저가_범위를_지킨다(page):
    """240~480. 드래그로 그 밖으로 못 나간다 — 나가면 칸이 죽거나 사이드바가 빈다."""
    _open_dock(page, "큐")
    box = page.locator("#vt-side .vt-side-resizer").bounding_box()
    assert box, "리사이저가 렌더되지 않았다"
    for dx in (900, -600):   # 넓히는 쪽 · 좁히는 쪽 둘 다 끝까지
        page.mouse.move(box["x"] + 3, box["y"] + 200)
        page.mouse.down()
        page.mouse.move(box["x"] + 3 + dx, box["y"] + 200, steps=8)
        page.mouse.up()
        page.wait_for_timeout(150)
        w = page.evaluate("() => Math.round(document.getElementById('vt-side').getBoundingClientRect().width)")
        assert 239 <= w <= 481, f"사이드바 폭이 범위를 벗어났다: {w}px (dx={dx})"
        box = page.locator("#vt-side .vt-side-resizer").bounding_box()


# 리디자인 1단계 — 폭을 끄는 동안 터미널 fit을 미루는가(옛 레일·dock 리사이저는 onStart가 비어 있어
# 매 프레임 refit·WS resize가 나갔다), 활동을 바꿔도 칸 크기가 그대로인가(폭 공유).
# WS resize는 페이지의 WebSocket.send를 감싸 `{"type":"resize"}` 프레임만 소켓 URL별로 센다.
_WS_RESIZE_SPY = """
(() => {
  if (window.__vtResizeSpy) return;
  const counts = {};
  window.__vtResizeSpy = counts;
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    try {
      if (typeof data === 'string' && data.includes('"resize"')) {
        const m = JSON.parse(data);
        if (m && m.type === 'resize') counts[this.url] = (counts[this.url] || 0) + 1;
      }
    } catch (_) {}
    return send.call(this, data);
  };
})();
"""


def _resize_counts(page) -> dict:
    return page.evaluate("() => ({ ...(window.__vtResizeSpy || {}) })")


def _open_terminal_with_spy(page):
    """세션 하나를 칸에 올리고 첫 fit이 끝날 때까지 기다린다. 반환: 만든 세션 id들."""
    page.evaluate(_WS_RESIZE_SPY)
    page.evaluate("() => window.createSession && window.createSession()")
    page.wait_for_selector(".vt-pane .vt-pane-body .xterm", timeout=15000)
    page.wait_for_function("() => Object.keys(window.__vtResizeSpy || {}).length > 0", timeout=15000)
    page.wait_for_timeout(800)   # PTY 연결 + 첫 fit + 부팅 뒤 늦은 배치가 가라앉을 때까지
    return page.evaluate("() => Object.keys(window.allSessions())")


def _cleanup_sessions(page, session_ids):
    for sid in session_ids:
        page.evaluate("(id) => window.removeSession && window.removeSession(id)", sid)
    page.wait_for_timeout(300)


def test_사이드바_폭을_끌어도_칸마다_WS_resize는_놓은_뒤_한_번이다(isolated_page):
    page = isolated_page
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'open'", timeout=10000)
    session_ids = _open_terminal_with_spy(page)
    try:
        before = _resize_counts(page)
        box = page.locator("#vt-side .vt-side-resizer").bounding_box()
        assert box, "리사이저가 없다"
        page.mouse.move(box["x"] + 3, box["y"] + 300)
        page.mouse.down()
        # 20px씩 6프레임 — 매 프레임 fit하던 옛 동작이면 칸마다 여러 번 나간다.
        for i in range(1, 7):
            page.mouse.move(box["x"] + 3 - 20 * i, box["y"] + 300)
            page.wait_for_timeout(40)
        during = _resize_counts(page)
        page.mouse.up()
        page.wait_for_timeout(700)
        after = _resize_counts(page)
        panes = page.evaluate("() => document.querySelectorAll('.vt-pane .vt-pane-body .xterm').length")
        sent = {u: after.get(u, 0) - before.get(u, 0) for u in after}
        assert during == before, f"끄는 동안 resize가 나갔다: {before} → {during}"
        assert all(n <= 1 for n in sent.values()), f"칸마다 최대 1번이어야 한다: {sent}"
        assert sum(sent.values()) >= 1, f"폭이 120px 줄었는데 resize가 한 번도 안 나갔다: {sent}"
        assert len([n for n in sent.values() if n]) <= panes, (sent, panes)
        print(f"WS resize(드래그): {sent}, 칸 {panes}")
    finally:
        _cleanup_sessions(page, session_ids)


def test_활동을_바꿔도_사이드바_폭이_같아_WS_resize가_없다(isolated_page):
    page = isolated_page
    page.wait_for_function("() => document.documentElement.dataset.vtSide === 'open'", timeout=10000)
    session_ids = _open_terminal_with_spy(page)
    try:
        before = _resize_counts(page)
        widths = []
        for label in ("큐", "변경", "첨부", "세션", "시스템", "세션"):
            _dock_tab(page, label).click()
            page.wait_for_timeout(250)
            widths.append(page.evaluate("() => Math.round(document.getElementById('vt-side').getBoundingClientRect().width)"))
        page.wait_for_timeout(500)
        after = _resize_counts(page)
        sent = {u: after.get(u, 0) - before.get(u, 0) for u in after}
        assert len(set(widths)) == 1, f"활동마다 사이드바 폭이 달랐다: {widths}"
        assert sum(sent.values()) == 0, f"활동 전환에 resize가 나갔다: {sent}"
        print(f"WS resize(활동 전환 6회): {sent}, 폭 {widths}")
    finally:
        _cleanup_sessions(page, session_ids)


# ── 2.1.2: 화면(뷰) · dock 파일 탭(50 §4) — 뷰 탭 줄은 2026-11에 없어졌다 ────────────────────────
#
# 2026-09-19 — 탭을 여는 전역 함수 이름과 시그니처가 두 번 바뀌었다:
# `openWorktreeTab(id, label)`(D4) → `openGroupTab({groupId, …})`(ADR-29 D) →
# `openGroupTab({sessionId, worktreeId, hostId, label})`(그룹 재정의). 이 잡이
# CI에서 한 번도 실행되지 못한 사이(setup-python 캐시 에러로 10일간 막혀 있었다)
# 테스트만 옛 이름에 남아 있었다. 탭의 DOM 구조 자체는 그대로다.

_BARS_SHAPE = """() => {
  const r = (el) => { const b = el.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), left: Math.round(b.left), width: Math.round(b.width), height: Math.round(b.height) }; };
  const pal = document.getElementById('vt-tb-palette');
  const crumb = document.querySelector('#vt-titlebar .vt-tb-crumb');
  const cs = getComputedStyle(document.body);
  return {
    vw: window.innerWidth, vh: window.innerHeight,
    title: r(document.getElementById('vt-titlebar')),
    status: r(document.getElementById('vt-hud')),
    wb: r(document.getElementById('vt-workbench')),
    term: r(document.getElementById('terminal-container')),
    pal: pal ? r(pal) : null,
    crumb: crumb ? getComputedStyle(crumb).display : null,
    padTop: cs.paddingTop, padBottom: cs.paddingBottom,
    topbar: getComputedStyle(document.getElementById('topbar')).display,
    paletteToggleIn: document.getElementById('palette-toggle').parentElement.id,
    menuIn: document.querySelector('.vt-mb-menu')?.closest('#vt-titlebar, #vt-hud')?.id || null,
    leadIn: document.getElementById('vt-mb-lead').closest('#vt-titlebar')?.id || null,
    toolsIn: document.getElementById('vt-mb-tools').closest('#vt-titlebar')?.id || null,
    transitions: [document.getElementById('vt-titlebar'), document.getElementById('vt-hud')]
      .map((el) => getComputedStyle(el).transitionProperty + ' ' + getComputedStyle(el).transitionDuration),
  };
}"""


def _wait_bars(page):
    page.wait_for_selector("#vt-titlebar .vt-mb-menu", timeout=10000)
    page.wait_for_selector("#vt-titlebar #vt-tb-palette", timeout=10000)


def test_뷰_탭_줄은_없고_위는_제목_막대_아래는_상태_막대다(page):
    """2026-11 — 뷰 탭 줄(#vt-wtabs)을 없앴다. 리디자인 2단계 — 넓은 화면(>=720px)의 맨 위는 제목 막대 36(FarShell 메뉴 ·
    호스트 · 경로 · 가운데 팔레트 입력 360), 맨 아래는 상태 막대 24(#vt-hud)다. #topbar는 숨고 ⌘K 버튼은 팔레트 입력이
    대신해 옮겨 오지 않는다. 작업대·본문은 두 막대 사이에만 걸린다."""
    assert page.evaluate("() => document.getElementById('vt-wtabs')") is None
    _wait_bars(page)
    s = page.evaluate(_BARS_SHAPE)
    assert s["topbar"] == "none", s
    assert s["title"]["top"] == 0 and s["title"]["height"] == 36 and s["title"]["width"] == s["vw"], s
    assert s["status"]["bottom"] == s["vh"] and s["status"]["height"] == 24 and s["status"]["width"] == s["vw"], s
    assert s["padTop"] == "36px" and s["padBottom"] == "24px", s
    assert s["wb"]["top"] == 36 and s["wb"]["bottom"] == s["vh"] - 24, f"작업대가 두 막대 사이에 있지 않다: {s}"
    assert s["term"]["top"] >= 36 and s["term"]["bottom"] <= s["vh"] - 24, s
    assert s["pal"]["width"] == 360 and s["pal"]["height"] == 24, s
    assert abs((s["pal"]["left"] + s["pal"]["width"] / 2) - s["vw"] / 2) <= 1, f"팔레트 입력이 가운데가 아니다: {s}"
    assert s["paletteToggleIn"] == "topbar", s
    assert s["menuIn"] == "vt-titlebar" and s["leadIn"] == "vt-titlebar" and s["toolsIn"] == "vt-titlebar", s
    # 막대 높이는 칸 크기를 정한다 — transition이 걸리면 그 프레임마다 refit·SIGWINCH가 난다.
    assert all(t.endswith(" 0s") for t in s["transitions"]), s


def test_regular_폭에서는_팔레트_입력이_280이고_경로가_숨는다(page):
    """시안 D04(1024×768) — 경로는 숨기고 팔레트 입력은 280. 상태 막대는 그대로 맨 아래 24."""
    page.set_viewport_size({"width": 1024, "height": 768})
    _wait_bars(page)
    page.wait_for_timeout(150)
    s = page.evaluate(_BARS_SHAPE)
    assert s["pal"]["width"] == 280, s
    assert abs((s["pal"]["left"] + s["pal"]["width"] / 2) - s["vw"] / 2) <= 1, s
    assert s["crumb"] in (None, "none"), s
    assert s["title"]["height"] == 36 and s["status"]["height"] == 24 and s["status"]["bottom"] == 768, s


def test_compact에서는_제목_막대도_상태_막대도_없다(page):
    """compact(<720)는 그대로 — #topbar 52 + 하단 내비 52. 두 막대는 display:none이고 막대 높이 변수도 0이다."""
    page.set_viewport_size(COMPACT)
    page.wait_for_timeout(200)
    got = page.evaluate("""() => ({
      title: getComputedStyle(document.getElementById('vt-titlebar')).display,
      status: getComputedStyle(document.getElementById('vt-hud')).display,
      topbar: getComputedStyle(document.getElementById('topbar')).display,
      palette: document.getElementById('palette-toggle').parentElement.id,
      vars: ['--titlebar-h', '--statusbar-h', '--menubar-h'].map((v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()),
    })""")
    assert got["title"] == "none" and got["status"] == "none", got
    assert got["topbar"] != "none" and got["palette"] == "topbar", got
    assert got["vars"] == ["0px", "0px", "0px"], got


def test_상태_막대는_에이전트_칩이_맨_왼쪽이고_토스트_메시지가_그_오른쪽에_뜬다(page):
    """info/success 토스트는 상태 막대의 메시지 칸(#vt-hud-msg, role=status)에 뜬다. 에이전트 칩은 그동안에도 남는다.
    실제 에이전트 칩은 에이전트 상태가 있어야 생기므로 같은 마크업의 칩을 하나 꽂아 배치 규칙(CSS order)만 본다."""
    _wait_bars(page)
    page.evaluate("""() => {
      const chip = document.createElement('span');
      chip.className = 'vt-hud-chip'; chip.dataset.chip = 'agents'; chip.textContent = '개입 1';
      document.getElementById('vt-hud').appendChild(chip);
    }""")
    page.evaluate("() => window.showToast('복사했습니다', 'success')")
    page.wait_for_selector("#vt-hud-msg:not([hidden])", timeout=5000)
    got = page.evaluate("""() => {
      const msg = document.getElementById('vt-hud-msg');
      const agents = document.querySelector('#vt-hud .vt-hud-chip[data-chip="agents"]');
      return { role: msg.getAttribute('role'), live: msg.getAttribute('aria-live'),
               agentsShown: getComputedStyle(agents).display !== 'none',
               agentsRight: agents.getBoundingClientRect().right, msgLeft: msg.getBoundingClientRect().left,
               barRole: document.getElementById('vt-hud').getAttribute('role') };
    }""")
    assert got["role"] == "status" and got["live"] == "polite" and got["barRole"] == "region", got
    assert got["agentsShown"], f"토스트가 에이전트 칩을 가렸다: {got}"
    assert got["agentsRight"] <= got["msgLeft"] + 1, f"메시지가 에이전트 칩 오른쪽이 아니다: {got}"


def test_제목_막대와_상태_막대를_써도_칸_크기가_그대로라_WS_resize가_없다(isolated_page):
    """리디자인 2단계 — 두 막대의 높이는 CSS(--titlebar-h/--statusbar-h)로 정해져 지연 청크가 늦게 와도 칸이 다시 맞춰지지
    않는다. 첫 fit 뒤에 막대를 쓰는 동작(상태 막대 메시지, FarShell 메뉴 열고 닫기, 팔레트 입력으로 팔레트 열고 닫기,
    상태 막대 설정 열고 닫기)이 칸 크기를 바꾸지 않는지 — WS resize 0번, 칸 사각형 그대로 — 를 본다."""
    page = isolated_page
    _wait_bars(page)
    session_ids = _open_terminal_with_spy(page)
    try:
        rect = ("() => { const b = document.querySelector('.vt-pane .vt-pane-body').getBoundingClientRect();"
                " return [b.top, b.left, b.width, b.height].map(Math.round); }")
        before, r0 = _resize_counts(page), page.evaluate(rect)
        page.evaluate("() => window.showToast('복사했습니다', 'success')")
        page.wait_for_timeout(200)
        page.locator("#vt-titlebar .vt-mb-menu").click()
        page.wait_for_selector(".vt-mb-pop", timeout=5000)
        page.keyboard.press("Escape")
        page.locator("#vt-tb-palette").click()
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.locator("#vt-titlebar .vt-mb-menu").click()
        page.get_by_role("menuitem", name="상태 막대 설정").click()
        page.wait_for_selector("#vt-settings .vt-hudcfg", timeout=15000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        after, r1 = _resize_counts(page), page.evaluate(rect)
        sent = {u: after.get(u, 0) - before.get(u, 0) for u in after}
        assert r1 == r0, f"칸 크기가 바뀌었다: {r0} → {r1}"
        assert sum(sent.values()) == 0, f"막대를 썼는데 resize가 나갔다: {sent}"
        print(f"WS resize(막대 사용): {sent}, 칸 {r0}")
    finally:
        _cleanup_sessions(page, session_ids)


def test_뷰는_탭_줄_없이도_열리고_자기_pane_트리를_가진다(page):
    """뷰 개념은 그대로다(묶음·세션에 붙은 파일 뷰어 칸·닫은 묶음 기억이 뷰 단위) — 탭 줄만 없다.
    새 뷰는 빈 한 칸에서 시작하고, 분할은 그 뷰 안에서만 일어난다."""
    panes = lambda: page.evaluate("() => document.querySelectorAll('#terminal-container .vt-pane').length")
    page.evaluate("() => window.openGroupTab({ worktreeId: 'wt-b', label: 'repo/b' })")
    page.wait_for_timeout(250)
    assert panes() == 1, "새 뷰는 빈 한 칸에서 시작해야 한다"
    page.evaluate("() => window.splitActivePane('row')")
    page.wait_for_timeout(200)
    assert panes() >= 2, "분할이 그 뷰 안에서 일어나야 한다"


def test_dock_파일_탭이_dock_안에_마운트되고_용량_게이지를_그린다(page):
    """50 §4 — 파일 탭은 백엔드만 있고 UI가 없던 자리다. 지연 청크(panels.js)를
    받아 dock 본문에 붙는지, 푸터 게이지까지 그리는지 실제로 확인한다."""
    # 업로드 탭은 「첨부」다(4039376에서 「파일」은 디렉토리 트리로 넘어갔다).
    _open_dock(page, "첨부")
    page.wait_for_selector("#vt-dock #vt-files", timeout=15000)
    # 칩은 공용 `.vt-chip`으로 그린다(전용 클래스 `.vt-fl-chip`은 없다) —
    # 컨테이너 id로 좁힌다.
    page.wait_for_selector("#vt-dock #vt-fl-chips .vt-chip", timeout=10000)
    chips = page.evaluate(
        "() => [...document.querySelectorAll('#vt-fl-chips .vt-chip')].map(e => e.textContent.trim())")
    assert any(c.startswith("전체") for c in chips), chips
    page.wait_for_selector("#vt-dock .vt-fl-footer .vt-fl-gauge", timeout=10000)
    # 모달이 아니라 dock 안이어야 한다(패널이 backdrop으로 새면 이 검사가 잡는다).
    assert page.evaluate("() => !!document.querySelector('#vt-dock #vt-files.vt-dock-panel')")


def test_파일을_패널에_끌어다_놓으면_올라간다(page):
    """R6 — OS 파일 드롭 업로드.

    실브라우저로만 볼 수 있는 것: `DataTransfer`에 실린 **진짜 파일**이
    `FormData`로 서버까지 가서 저장소에 편입되는가. jsdom 테스트
    (`upload.test.js`)는 fetch를 가짜로 바꿔 "몇 번 어떤 순서로 보내는가"만
    보므로 이 경로 전체는 여기서만 닫힌다.

    파일 패널에 놓았으므로 **터미널에 경로를 넣지 않는다**(사용자 확정 규칙).
    터미널 칸 드롭은 살아 있는 PTY가 있어야 해서 이 스위트의 범위 밖이다.
    """
    # 업로드 탭은 「첨부」다(4039376에서 「파일」은 디렉토리 트리로 넘어갔다).
    _open_dock(page, "첨부")
    page.wait_for_selector("#vt-dock #vt-files", timeout=15000)
    # `before`는 **서버 저장소**에서 센다. 목록은 패널이 뜬 뒤 비동기로 그려지므로
    # 패널이 보이자마자 DOM 행을 세면 첫 목록이 오기 전의 0을 읽는다 — 앞 테스트
    # (터미널 칸 드롭)가 올린 파일이 그 뒤에 그려져 "행 2개"로 오판했다(실측: 목록
    # 응답을 0.8초 늦추면 매번 재현). 화면이 그 수를 다 그린 뒤에 드롭한다.
    count_store = """async () => {
      const r = await fetch('/api/files?filter=all', { credentials: 'same-origin' });
      return ((await r.json()).items || []).map((f) => f.name || '');
    }"""
    before = len(page.evaluate(count_store))
    page.wait_for_function(
        "(n) => document.querySelectorAll('#vt-dock .vt-fl-row').length === n",
        arg=before, timeout=15000,
    )

    name = f"e2e-drop-{int(time.time())}.txt"
    page.evaluate(
        """([name]) => {
          const dt = new DataTransfer();
          dt.items.add(new File(['drop me'], name, { type: 'text/plain' }));
          const root = document.getElementById('vt-files');
          for (const type of ['dragover', 'drop']) {
            root.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
          }
        }""",
        [name],
    )

    page.wait_for_function(
        "(n) => [...document.querySelectorAll('#vt-dock .vt-fl-name')].some(e => e.textContent.includes(n))",
        arg=name, timeout=15000,
    )
    # 업로드 중 행(.vt-fl-row-uploading)이 서버 레코드 행으로 바뀐 뒤에 읽는다 — 크기는 완료 행의 .vt-fl-meta 에 있다.
    page.wait_for_function(
        """(n) => {
          const r = [...document.querySelectorAll('#vt-dock .vt-fl-row')]
            .find(x => x.querySelector('.vt-fl-name')?.textContent.includes(n));
          return !!r && !r.classList.contains('vt-fl-row-uploading') && !!r.querySelector('.vt-fl-meta');
        }""",
        arg=name, timeout=20000,
    )
    got = page.evaluate(
        """(n) => {
          const rows = [...document.querySelectorAll('#vt-dock .vt-fl-row')];
          const mine = rows.find(r => r.querySelector('.vt-fl-name')?.textContent.includes(n));
          return { rows: rows.length, sub: mine?.querySelector('.vt-fl-meta')?.textContent.trim() || '' };
        }""",
        name,
    )
    # 드롭 한 번 = 업로드 한 번 — 화면이 아니라 저장소로도 본다(두 번 올라가면 이름이 같은 레코드가 둘 생긴다).
    stored = page.evaluate(count_store)
    assert stored.count(name) == 1, f"드롭 한 번에 한 번만 올라가야 한다: {stored}"
    assert len(stored) == before + 1, f"저장소에 하나만 늘어야 한다: {stored}"
    assert got["rows"] == before + 1, f"행이 하나 늘어야 한다: {got}"
    assert "7B" in got["sub"], f"크기가 안 맞는다(내용 'drop me' = 7바이트): {got}"


def test_모바일_플릿_홈에_호스트_스위처가_있다(page):
    """C1 — 호스트 칩은 좌측 레일에만 있었는데 compact(<720px)에서는 레일이
    통째로 숨는다. 즉 **폰에서는 호스트를 바꿀 방법이 아예 없었다.** 플릿 홈
    상단 sticky 헤더가 그 진입점이다.

    `/api/hosts`를 두 호스트로 가로챈다 — 실제 페어링 없이 "칩이 그려지는가 /
    골랐을 때 목록이 그 호스트의 세션으로 바뀌는가"를 볼 수 있다. 호스트가
    하나뿐이면 안 그리는 규칙(레일과 동일)도 같이 확인한다.
    """
    page.route(
        "**/api/hosts*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"hosts":[{"id":"local","label":"이 맥","online":true,"sessions":[]},'
                 '{"id":"gpu","label":"gpu-box","online":true,"latencyMs":12,'
                 '"sessions":[{"name":"train","status":"working"}]}]}',
        ),
    )
    # 원격을 고르면 페이지가 새로고침되고 부팅 직후 `verifyRelayHost`가 그 호스트로 릴레이한
    # `/api/capabilities`를 확인한다. 가짜 호스트 `gpu`는 서버에 등록돼 있지 않아 릴레이가 거절하면
    # 앱이 이유를 알리고 이 맥으로 되돌아가 버린다(그래서 「train」이 영영 안 나왔다). 릴레이 쪽도 같이
    # 스텁한다 — 이 테스트가 보는 건 스위처의 동작이지 릴레이 자체가 아니다.
    page.route(
        "**/api/hosts/gpu/relay/api/capabilities*",
        lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
    )
    page.route(
        "**/api/hosts/gpu/relay/api/tmux/sessions*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='[{"name":"train","windows":1,"attached":false,"command":"python"}]',
        ),
    )
    # dock은 **기기 설정**에 마지막 활성 탭과 접힘 여부를 남긴다. 진짜 폰은
    # 자기만의 기기 id를 갖고, 폰 폭에서는 접힘이 기본이라 부팅 직후 dock
    # 패널이 뜨지 않는다. 그런데 이 스위트는 컨텍스트마다 쿠키가 없어 **모든
    # 테스트가 같은 기기("local")로 잡힌다** — 앞서 데스크톱 폭에서 dock 탭을
    # 연 테스트가 `collapsed=false`를 남기면, compact에서 그 탭이 자동으로
    # 열리면서 풀스크린 시트(panel.js는 compact에서 backdrop으로 폴백한다)가
    # 플릿 홈을 통째로 덮는다. 폰의 기본 상태를 명시해 그 전이를 끊는다.
    page.evaluate("() => window.vtSettingsSet('ui.side.collapsed', true)")
    page.wait_for_timeout(300)
    page.set_viewport_size(COMPACT)
    page.reload(wait_until="load")
    page.wait_for_function(
        "() => document.documentElement.dataset.appBooted === 'true'", timeout=20000)
    # 하단 내비의 선택도 같은 기기 설정에 남는다(위 주석과 같은 이유) — 앞
    # 테스트가 「터미널」을 골라 뒀으면 플릿 홈이 숨어 있다. 명시적으로 연다.
    page.click('#vt-mnav-bottom .vt-mnav-btn[data-mnav="fleet"]')

    bar = page.wait_for_selector("#vt-fleet .vt-fleet-hostbar", timeout=15000)
    assert bar.is_visible(), "폰에서 호스트 칩이 보여야 한다"

    page.click("#vt-fleet .vt-fleet-host")
    page.wait_for_selector("#vt-fleet .vt-fleet-hostmenu", timeout=5000)
    labels = page.evaluate(
        "() => [...document.querySelectorAll('#vt-fleet .vt-fleet-hostitem-name')]"
        ".map(e => e.textContent.replace('✓ ', '').trim())")
    assert labels == ["이 맥", "gpu-box"], labels

    page.locator("#vt-fleet .vt-fleet-hostitem", has_text="gpu-box").click()
    # 원격을 고르면 그 호스트의 세션 목록으로 바뀐다(스위처는 필터일 뿐이다).
    page.wait_for_function(
        "() => [...document.querySelectorAll('#vt-fleet .vt-fleet-name')]"
        ".some(e => e.textContent.trim() === 'train')",
        timeout=10000,
    )
    # 그리고 그 선택이 남는다 — 호스트 전환은 기기 설정(`ui.activeHostId`)이 아니라 브라우저 저장소의
    # `vt.relayHost`와 새로고침으로 한다(core/host-route.js `switchHost`).
    assert page.evaluate("() => window.vtRelayHost()") == "gpu"


def _open_legacy_mcp_workspace(page, root="#vt-mcp"):
    """통합 관리자 화면에서 「기존 MCP 보관함·상세 관리」로 들어가 세 탭 작업면을 연다."""
    page.get_by_role("button", name="기존 MCP 보관함·상세 관리").click(timeout=10000)
    # 옛 보관함은 들어온 뒤에 설정 파일을 훑어 그린다. 캐시가 빈 첫 실행(CI)은 10초를 넘길 수 있다.
    page.wait_for_selector(f"{root} .vt-mcp-worktabs", timeout=30000)


def test_mcp_workspace_tabs_fit_desktop_and_mobile(page):
    """실제 브라우저에서 세 탭과 보관함 미리보기 진입면의 가로 넘침을 잡는다."""
    page.locator("#vt-activitybar .vt-act-mcp").click()
    _open_legacy_mcp_workspace(page)
    assert page.locator("#vt-mcp .vt-mcp-worktab").all_text_contents() == [
        "보관함", "도구 설정", "현재 세션"]
    page.locator('#vt-mcp [data-page="library"]').click()
    assert page.locator("#vt-mcp .vt-mcp-library").is_visible()
    assert "FarShell 보관함" in page.locator("#vt-mcp .vt-mcp-librarybar").inner_text()
    assert "설치된 스킬" not in page.locator("#vt-mcp .vt-mcp-library").inner_text()
    page.locator('#vt-mcp [data-page="session"]').click()
    assert page.locator("#vt-mcp .vt-mcp-session").is_visible()
    page.screenshot(path="/tmp/fsh-mcp-desktop.png")
    page.locator("#vt-mcp .vt-vw-x").click()
    page.set_viewport_size(COMPACT)
    page.evaluate("window.openPalette()")
    page.locator("#vt-qo-input").fill("MCP · 스킬")
    page.get_by_text("MCP · 스킬 관리", exact=True).click()
    _open_legacy_mcp_workspace(page, "#vt-mcp.vt-mobile-route")
    page.wait_for_timeout(300)  # 모바일 시트 진입 애니메이션이 끝난 뒤 기하를 잰다.
    page.screenshot(path="/tmp/fsh-mcp-mobile.png")
    layout = page.evaluate("""() => {
      const body = document.querySelector('#vt-mcp .vt-vw-body');
      const card = document.querySelector('#vt-mcp .vt-viewer-card').getBoundingClientRect();
      const nav = document.querySelector('#vt-mnav-bottom').getBoundingClientRect();
      return {scrollWidth: body.scrollWidth, clientWidth: body.clientWidth,
        cardBottom: card.bottom, navTop: nav.top};
    }""")
    assert layout["scrollWidth"] <= layout["clientWidth"] + 1, layout
    assert layout["cardBottom"] <= layout["navTop"] + 1, layout


# ── 파일 개편 단계 5 — 사이드바 「파일」 트리(files/FileTree.tsx) ─────────────────────────────


def _tree_dir(name: str) -> Path:
    """격리 서버의 둘째 열람 경계 아래 트리 시험 폴더. 외부 서버면 건너뛴다."""
    d = Path(_viewer_file(name))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _open_tree_at(page, d: Path) -> None:
    """「파일」 활동을 열고 트리 최상단을 d로 옮긴다(경로 입력 + Enter)."""
    _open_dock(page, "파일")
    page.wait_for_selector("#vt-dock #vt-dir .vt-ft-path", timeout=15000)
    inp = page.locator("#vt-dir .vt-ft-path")
    inp.fill(str(d))
    inp.press("Enter")
    page.wait_for_function(
        # 경로는 앞을 말줄임(rtl)하므로 LRM(‎)으로 감싸 그린다 — 그 문자를 빼고 비교한다.
        "(p) => document.querySelector('#vt-dir .vt-ft-host-p')?.textContent.replace(/\\u200e/g, '') === p",
        arg=str(d), timeout=10000)


def test_파일_트리는_보이는_행만_그리고_페이지와_aria_tree와_키보드가_된다(page):
    d = _tree_dir("tree-big")
    for i in range(1500):
        (d / f"n{i:04d}.txt").write_text("x")
    _open_tree_at(page, d)
    page.wait_for_function(
        "() => document.querySelector('#vt-dir .vt-ft-spacer')?.style.height === '" + f"{1001 * 28}px" + "'", timeout=10000)
    shape = page.evaluate("""() => {
      const tree = document.querySelector('#vt-dir .vt-ft-scroll');
      return { role: tree.getAttribute('role'), rows: tree.querySelectorAll('.vt-ft-row').length,
        height: tree.querySelector('.vt-ft-spacer').style.height,
        setsize: tree.querySelector('.vt-ft-row.k-file').getAttribute('aria-setsize'),
        rowH: Math.round(tree.querySelector('.vt-ft-row').getBoundingClientRect().height) };
    }""")
    assert shape["role"] == "tree", shape
    assert shape["rows"] <= 80, f"보이는 구간만 DOM에 있어야 한다: {shape}"
    assert shape["height"] == f"{1001 * 28}px", shape   # 첫 페이지 1000개 + 「더 불러오기」
    assert shape["setsize"] == "1500" and shape["rowH"] == 28, shape
    # 「더 불러오기」 — 끝까지 스크롤해(가상 행이라 그때 DOM에 생긴다) 누른다.
    page.evaluate("() => { const t = document.querySelector('#vt-dir .vt-ft-scroll'); t.scrollTop = t.scrollHeight; }")
    page.wait_for_selector("#vt-dir .vt-ft-row.k-more", timeout=5000)
    page.locator("#vt-dir .vt-ft-row.k-more").click()
    page.wait_for_function(
        "() => document.querySelector('#vt-dir .vt-ft-spacer').style.height === '" + f"{1500 * 28}px" + "'", timeout=10000)
    # 키보드 — 초점은 스크롤 상자가 갖고 aria-activedescendant가 행을 가리킨다.
    tree = page.locator("#vt-dir .vt-ft-scroll")
    tree.focus()
    tree.press("Home")
    tree.press("ArrowDown")
    active = lambda: page.evaluate(  # noqa: E731
        "() => { const t = document.querySelector('#vt-dir .vt-ft-scroll');"
        " const id = t.getAttribute('aria-activedescendant'); return id ? document.getElementById(id)?.dataset.key : null; }")
    assert active().endswith("/n0001.txt"), active()
    tree.press("End")
    page.wait_for_function(
        "() => { const t = document.querySelector('#vt-dir .vt-ft-scroll'); const id = t.getAttribute('aria-activedescendant');"
        " return id && document.getElementById(id)?.dataset.key.endsWith('/n1499.txt'); }", timeout=5000)
    # 이름 필터.
    page.locator("#vt-dir .vt-ft-filter input").fill("n0042")
    page.wait_for_function("() => document.querySelectorAll('#vt-dir .vt-ft-row').length === 1", timeout=5000)
    assert page.locator("#vt-dir .vt-ft-cnt").inner_text() == "1개"
    # 행 메뉴 — 오른쪽 클릭. 다운로드·새 파일은 이 단계에 없다.
    page.locator("#vt-dir .vt-ft-row.k-file").click(button="right")
    page.wait_for_selector('.vt-ft-menu[role="menu"]', timeout=5000)
    labels = page.locator('.vt-ft-menu [role="menuitem"] .vt-ft-mi-l').all_text_contents()
    assert labels == ["열기", "터미널에 경로 삽입", "경로 복사", "공유 링크 만들기…", "새로고침"], labels
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('.vt-ft-menu')", timeout=5000)


def test_파일_트리는_루트_감시_하나로_바뀐_폴더만_다시_받고_바뀜_점을_찍는다(page):
    d = _tree_dir("tree-watch")
    for i in range(5):
        (d / f"sub{i}").mkdir(exist_ok=True)
        (d / f"sub{i}" / "a.txt").write_text("1")
    _open_tree_at(page, d)
    for i in range(5):
        page.locator(f'#vt-dir .vt-ft-row.k-dir[data-key$="/sub{i}"]').click()
    page.wait_for_function("() => document.querySelectorAll('#vt-dir .vt-ft-row.k-dir.open').length === 5", timeout=10000)
    page.wait_for_timeout(1500)   # 감시가 자리를 잡고(구독 왕복) 위 생성 이벤트가 가라앉게
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "/api/fs/tree" in r.url else None)
    (d / "sub3" / "new.txt").write_text("hello")
    page.wait_for_selector('#vt-dir .vt-ft-row[data-key$="/sub3/new.txt"] .vt-ft-chg', timeout=15000)
    page.wait_for_timeout(800)
    assert len(seen) == 1, f"바뀐 폴더 하나만 다시 받아야 한다: {seen}"
    assert "depth=0" in seen[0] and "sub3" in seen[0], seen
    # 조상 폴더는 빈 원(안쪽에 바뀐 것이 있다).
    assert page.locator('#vt-dir .vt-ft-row.k-dir[data-key$="/sub3"] .vt-ft-chg.ring').count() == 1
    # 열면 점이 사라진다.
    page.locator('#vt-dir .vt-ft-row[data-key$="/sub3/new.txt"]').click()
    page.wait_for_function(
        "() => !document.querySelector('#vt-dir .vt-ft-row[data-key$=\"/sub3/new.txt\"] .vt-ft-chg')", timeout=5000)


def test_폰_파일_탭은_트리가_기본이고_파일_첨부를_오가며_길게_누르면_시트(page):
    d = _tree_dir("tree-phone")
    (d / "a.txt").write_text("1")
    (d / "b.txt").write_text("2")
    page.set_viewport_size(COMPACT)
    tab = page.locator('#vt-mnav-bottom [data-mnav="files"]')
    assert tab.get_attribute("aria-label") == "파일"
    assert page.locator("#vt-mnav-bottom #vt-mnav-badge-files").count() == 1
    tab.click()
    page.wait_for_selector("#vt-dir.vt-mobile-route .vt-mfiles-seg", timeout=15000)
    sel = page.locator('#vt-dir .vt-mfiles-seg [role="tab"][aria-selected="true"]').inner_text()
    assert sel.strip() == "파일"
    inp = page.locator("#vt-dir .vt-ft-path")
    inp.fill(str(d))
    inp.press("Enter")
    page.wait_for_selector('#vt-dir .vt-ft-row[data-key$="/a.txt"]', timeout=10000)
    h = page.evaluate("() => Math.round(document.querySelector('#vt-dir .vt-ft-row').getBoundingClientRect().height)")
    assert h == 44, h
    # 길게 누르면 아래 시트(항목 44).
    page.evaluate("""() => {
      const r = document.querySelector('#vt-dir .vt-ft-row[data-key$="/a.txt"]');
      const b = r.getBoundingClientRect();
      r.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch', clientX: b.left + 40, clientY: b.top + 10 }));
    }""")
    page.wait_for_selector(".vt-ft-menu.sheet", timeout=3000)
    ih = page.evaluate("() => Math.round(document.querySelector('.vt-ft-menu.sheet [role=menuitem]').getBoundingClientRect().height)")
    assert ih == 44, ih
    page.locator(".vt-ft-menu-cancel").click()
    # 「첨부」로 바꾸면 첨부 화면, 탭을 다시 누르면 마지막에 본 쪽(첨부)이 열린다.
    page.locator('#vt-dir .vt-mfiles-seg [data-seg="attach"]').click()
    page.wait_for_selector("#vt-files.vt-mobile-route .vt-mfiles-seg", timeout=15000)
    assert page.locator("#vt-dir").count() == 0
    page.locator('#vt-mnav-bottom [data-mnav="fleet"]').click()
    tab.click()
    page.wait_for_selector("#vt-files.vt-mobile-route", timeout=15000)
    page.locator('#vt-files .vt-mfiles-seg [data-seg="tree"]').click()
    page.wait_for_selector("#vt-dir.vt-mobile-route", timeout=15000)
