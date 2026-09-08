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

`VT_E2E_URL`을 주면 이미 떠 있는 서버에 붙는다(그때는 `VT_E2E_TOKEN`도 같이).
안 주면 이 파일이 **격리된 서버를 직접 띄운다** — 실 사용자의 워크스페이스·tmux
소켓·인증 토큰을 절대 건드리지 않기 위해서다(아래 `_isolated_env` 주석 참고).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright 미설치 — pip install -r requirements-dev.txt")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# 반응형 3구간 — frontend/js/layout/breakpoints.js와 같은 경계값.
COMPACT = {"width": 390, "height": 780}
REGULAR = {"width": 900, "height": 800}
WIDE = {"width": 1440, "height": 900}

SKINS = ["farshell", "macos", "catppuccin", "windows", "vscode", "notepad"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _isolated_env(port: int, token: str, tmp: Path) -> dict:
    """실 사용자 상태와 완전히 분리된 서버 환경.

    세 개를 **같이** 줘야 한다. 하나라도 빠지면 이 테스트가 사용자의 실제 상태를
    건드린다:

      VT_AUTH_TOKEN     안 주면 서버가 ~/.vt.env의 토큰을 상속해, 테스트가 모르는
                        값으로 인증이 걸려 401이 난다.
      VT_WORKSPACE_PATH 안 주면 사용자의 실제 workspace.json에 테스트가 만든
                        레이아웃·설정이 저장된다.
      VT_TMUX_SOCKET    안 주면 사용자의 `-L vt` 소켓을 그대로 본다 — 진짜 작업
                        세션이 목록에 뜨고, 조작하면 실제로 영향을 준다.

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
        VT_WORKSPACE_PATH=str(tmp / "workspace.json"),
        VT_TMUX_SOCKET=f"fsh-e2e-{port}",
        VT_NETWORK_MODE="localhost",
        VT_CONFIG=str(tmp / "no-such.env"),   # ~/.vt.env를 읽지 않게
        PYTHONUNBUFFERED="1",
    )
    return env


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """(base_url, token). VT_E2E_URL이 있으면 그걸 쓰고, 없으면 직접 띄운다."""
    external = os.environ.get("VT_E2E_URL")
    if external:
        yield external.rstrip("/"), os.environ.get("VT_E2E_TOKEN", "")
        return

    tmp = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    token = "e2e-smoke-token"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT / "server"),
        env=_isolated_env(port, token, tmp),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"서버가 뜨기 전에 죽었다:\n{proc.stdout.read() if proc.stdout else ''}")
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


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except PlaywrightError as e:
            pytest.skip(f"chromium 없음 — `playwright install chromium` 필요 ({e})")
        yield b
        b.close()


# 레이아웃 실패는 로그만으로 재구성하기 어려워서 실패 시 화면을 남긴다 —
# CI가 /tmp/e2e-*.png를 아티팩트로 올린다. `stash_failed`를 세우는 훅은
# conftest.py에 있다(테스트 모듈 안의 훅은 pytest가 수집하지 않는다).
@pytest.fixture
def page(browser, server, request):
    base, token = server
    ctx = browser.new_context(viewport=WIDE)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    _boot(pg, f"{base}/?token={token}")
    pg.console_errors = errors  # 개별 테스트가 필요하면 본다
    yield pg
    if getattr(request.node, "stash_failed", False):
        safe = "".join(c if c.isalnum() else "_" for c in request.node.name)[:80]
        try:
            pg.screenshot(path=f"/tmp/e2e-{safe}.png", full_page=True)
        except Exception:  # 스크린샷 실패가 테스트 결과를 덮으면 안 된다
            pass
    ctx.close()


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


def _open_rail(pg, item: str = "session") -> None:
    """rail 패널을 연다 — **이미 열려 있으면 클릭하지 않는다.**

    무조건 클릭하면 안 되는 이유: rail은 열림 상태를 `/api/workspace`에 저장하고
    부팅 때 복원한다(ADR-5). 이 파일의 서버는 세션 스코프라 workspace가 테스트
    사이에 공유되므로, 앞 테스트가 패널을 열어놨으면 다음 테스트는 열린 채로
    부팅되고 클릭이 그걸 **닫아버린다**. 실제로 그렇게 실패했다.
    """
    btn = f"#vt-rail-{item}"
    if not pg.evaluate(f"() => document.querySelector('{btn}')?.classList.contains('active')"):
        pg.click(btn)
    pg.wait_for_function(
        f"() => document.querySelector('{btn}')?.classList.contains('active')", timeout=5000
    )


# ── 부팅 ──────────────────────────────────────────────────────────────────

def test_페이지가_JS_에러_없이_뜬다(page):
    page.wait_for_timeout(1200)
    # 서버가 인증을 요구하는 401/403은 없어야 하고, 모듈 평가 에러도 없어야 한다.
    fatal = [e for e in page.console_errors if "favicon" not in e.lower()]
    assert fatal == [], "콘솔 에러:\n  " + "\n  ".join(fatal)


def test_세션이_없으면_온보딩이_보인다(page):
    # CI에는 tmux 세션이 없다. 그 상태에서 빈 화면이 아니라 안내가 떠야 한다.
    page.wait_for_selector(".vt-onboarding", timeout=10000)
    assert page.locator(".vt-onboarding").is_visible()


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
_CARD_CLASSES = ["vt-card", "vt-rail-session-row", "vt-rail-session-card", "card-preview"]


def test_세션_카드_클래스_조합이_아직_코드에_있다(page, server):
    """아래 봉쇄 테스트가 검사하는 마크업이 실제 코드와 안 어긋나는지 본다.

    테스트가 마크업을 들고 있으면 코드가 바뀔 때 조용히 무의미해진다 — 이 저장소가
    이미 겪은 실패다(폐지된 #grid-toggle을 검사하던 옛 E2E). 최소한의 결합 확인.
    """
    base, _ = server
    bundle = page.request.get(f"{base}/static/dist/app.js").text()
    missing = [c for c in _CARD_CLASSES if c not in bundle]
    assert missing == [], f"번들에 없는 클래스: {missing} — 테스트 마크업이 낡았다"


def test_세션_카드_레이아웃이_카드_안에_갇힌다(page):
    """긴 경로가 든 프리뷰가 카드 밖으로 나가지 않는가.

    `.card-preview`는 `white-space:pre-wrap` + 마스크가 걸린 요소라, 안 끊기는 긴
    문자열 하나가 부모의 min-width 계산을 밀어올린다. 그게 그날의 실제 데이터였다.
    """
    _open_rail(page, "session")
    page.wait_for_selector(".vt-rail-session-list", timeout=10000)
    overflow = page.evaluate(
        """() => {
          const list = document.querySelector('.vt-rail-session-list');
          const card = document.createElement('div');
          card.className = 'vt-card vt-rail-session-row vt-rail-session-card';
          card.innerHTML = `
            <div class="card-head">
              <span class="card-agent"></span>
              <span class="card-title">dev</span>
              <span class="card-cmd">claude</span>
            </div>
            <pre class="card-preview">/Users/x/GitHub/side_project/tools/farshell/very/long/path/that/never/wraps/anywhere</pre>`;
          list.appendChild(card);
          const c = card.getBoundingClientRect();
          const bad = [];
          for (const child of card.querySelectorAll('*')) {
            const r = child.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            if (r.left < c.left - 1 || r.right > c.right + 1) {
              bad.push(`${child.className}: ${Math.round(r.left)}~${Math.round(r.right)} (카드 ${Math.round(c.left)}~${Math.round(c.right)})`);
            }
          }
          card.remove();
          return bad;
        }"""
    )
    assert overflow == [], "카드 밖으로 넘친 자식:\n  " + "\n  ".join(overflow)


def test_열린_표면의_내용이_그_표면을_넘지_않는다(page):
    """CI에서 실제로 열리는 표면들에 대한 일반 봉쇄 검사.

    데이터가 없어도 되고, 새 UI가 생겨도 선택자만 늘리면 된다. 세션 카드처럼
    특정 조합을 못 집는 대신 같은 **종류**의 실패를 넓게 잡는다.
    """
    _open_rail(page, "settings")
    page.wait_for_selector("#vt-rail-panel", timeout=5000)
    page.wait_for_timeout(300)
    bad = page.evaluate(
        """() => {
          const surfaces = ['#vt-rail-panel', '.vt-onboarding', '#topbar'];
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

def test_rail은_compact에서_숨고_그_위에서_보인다(page):
    page.set_viewport_size(COMPACT)
    page.wait_for_timeout(300)
    assert not page.locator("#vt-rail").is_visible(), "compact에서는 팔레트·바텀시트가 대신한다"

    page.set_viewport_size(REGULAR)
    page.wait_for_timeout(300)
    assert page.locator("#vt-rail").is_visible()


# ── 스킨 ──────────────────────────────────────────────────────────────────

def test_6스킨이_실제_계산값에_반영된다(page):
    """data-skin만 바뀌고 색은 안 바뀌는 상태(스타일시트 누락 등)를 잡는다."""
    bars = {}
    for skin in SKINS:
        page.evaluate("(s) => document.documentElement.setAttribute('data-skin', s)", skin)
        bars[skin] = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--bar').trim()"
        )
    assert all(bars.values()), f"--bar가 비었다: {bars}"
    assert len(set(bars.values())) == len(SKINS), f"스킨별 --bar가 안 갈린다: {bars}"


def test_테마_칩_6개가_서로_다른_색을_보인다(page):
    """T9 회귀 방지 — 칩 색을 인라인 style에서 토큰 상수로 옮겼다.

    `--color-acc`는 현재 스킨 것만 알려주므로, 칩이 그걸 쓰면 6개가 전부 같은
    색이 된다. 정적 검사(인라인 style이 없는가)만으로는 이 실패를 못 본다.
    """
    colors = page.eval_on_selector_all(
        ".theme-chip .dot", "els => els.map(e => getComputedStyle(e).backgroundColor)"
    )
    assert len(colors) == len(SKINS), f"칩 개수가 {len(colors)}개"
    assert len(set(colors)) == len(SKINS), f"칩 색이 안 갈린다: {colors}"


# ── 상태 표시가 색 단독이 아닌가 ──────────────────────────────────────────

def test_rail_활성_항목에_막대가_실제로_그려진다(page):
    """T3 회귀 방지. CSS에 규칙이 있는 것과 실제로 렌더되는 건 다르다 —
    부모의 overflow나 stacking에 막혀 안 보일 수 있다."""
    _open_rail(page, "session")
    bar = page.evaluate(
        """() => {
          const el = document.querySelector('#vt-rail-session');
          if (!el || !el.classList.contains('active')) return null;
          const s = getComputedStyle(el, '::before');
          return { width: s.width, bg: s.backgroundColor, content: s.content };
        }"""
    )
    assert bar is not None, "rail 세션 버튼이 active가 되지 않았다"
    assert bar["width"] == "3px", f"막대 폭이 {bar['width']}"
    assert bar["bg"] not in ("", "rgba(0, 0, 0, 0)"), f"막대가 투명하다: {bar}"
