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
import re
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
        # dock 소스컨트롤 탭이 볼 저장소를 이 저장소 자신으로 고정한다. 기본값
        # (~/GitHub)은 CI에 없고 로컬에선 사람마다 달라 결과가 흔들린다. 열람은
        # 읽기 전용이고 경계도 여기로 좁아진다.
        VT_BROWSE_ROOTS=str(ROOT),
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


def _open_dock(pg, label: str) -> None:
    """dock 탭을 연다 — **이미 그 탭이 열려 있으면 클릭하지 않는다.**
    같은 탭 재클릭은 접기라서, 앞 테스트가 남긴 상태(dock은 활성 탭·접힘을
    localStorage에 저장한다) 위에서 무조건 클릭하면 닫아버린다.

    선택자를 `:has-text()`로 쓰지 않는 이유: 그건 playwright 전용 문법이라
    `page.evaluate` 안의 querySelector에서는 SyntaxError가 난다(실측). 클릭은
    locator로, 상태 확인은 textContent 비교로 나눠 쓴다.
    """
    pg.wait_for_selector("#vt-dock .vt-dock-tab", timeout=10000)
    is_active = (
        "(label) => [...document.querySelectorAll('#vt-dock .vt-dock-tab')]"
        ".some((t) => t.textContent.trim().startsWith(label) && t.classList.contains('active'))"
    )
    if not pg.evaluate(is_active, label):
        pg.locator("#vt-dock .vt-dock-tab", has_text=label).first.click()
    pg.wait_for_function(is_active, arg=label, timeout=5000)


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
# shell/Rail.tsx가 행에 얹는 클래스 조합(N36). 이게 바뀌면 아래 테스트가
# 엉뚱한 걸 검사하게 되므로 번들에서 실제로 확인한다 — 레일은 지연 청크
# (shell.js)에 있으므로 app.js가 아니라 그 파일을 본다.
_CARD_CLASSES = ["vt-wgrail-row", "vt-wgrail-bar", "vt-wgrail-name", "vt-wgrail-row-sub"]


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
          row.className = 'vt-wgrail-row';
          row.innerHTML = `
            <span class="vt-wgrail-bar tone-waiting"></span>
            <div class="vt-wgrail-row-main">
              <div class="vt-wgrail-row-top">
                <span class="vt-wgrail-name">feat/very-long-branch-name-that-never-wraps-anywhere-at-all</span>
                <span class="vt-wgrail-diff">파일 7</span>
              </div>
              <div class="vt-wgrail-row-sub">/Users/x/GitHub/side_project/tools/farshell/very/long/path/that/never/wraps</div>
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
          const surfaces = ['#vt-wgrail', '#vt-dock', '.vt-onboarding', '#topbar'];
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

def test_레일과_dock은_compact에서_숨고_그_위에서_보인다(page):
    """N35·N36 — 티어 표(10 §3): compact는 레일·dock 둘 다 없다(하단 내비가
    자리를 쓴다), regular부터 둘 다 보인다."""
    page.set_viewport_size(COMPACT)
    page.wait_for_timeout(300)
    assert not page.locator("#vt-wgrail").is_visible(), "compact에서는 팔레트·바텀시트가 대신한다"
    assert not page.locator("#vt-dock").is_visible(), "compact에서는 하단 내비가 대신한다"

    page.set_viewport_size(REGULAR)
    page.wait_for_timeout(300)
    assert page.locator("#vt-wgrail").is_visible()
    assert page.locator("#vt-dock").is_visible()


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

def test_레일_상태막대가_색_단독이_아니고_실제로_그려진다(page):
    """T3 회귀 방지. CSS에 규칙이 있는 것과 실제로 렌더되는 건 다르다 —
    부모의 overflow나 stacking에 막혀 안 보일 수 있다. N36에서 그 자리는
    rail 버튼의 ::before가 아니라 행 왼쪽의 `.vt-wgrail-bar`다."""
    page.wait_for_selector("#vt-wgrail .vt-wgrail-body", timeout=10000)
    bars = page.evaluate(
        """() => {
          const list = document.querySelector('#vt-wgrail .vt-wgrail-body');
          const out = {};
          for (const tone of ['waiting', 'working', 'error', 'idle']) {
            const row = document.createElement('div');
            row.className = 'vt-wgrail-row';
            row.innerHTML = `<span class="vt-wgrail-bar tone-${tone}"></span><div class="vt-wgrail-row-main"><div class="vt-wgrail-row-top"><span class="vt-wgrail-name">x</span></div></div>`;
            list.appendChild(row);
            const b = row.querySelector('.vt-wgrail-bar').getBoundingClientRect();
            const cs = getComputedStyle(row.querySelector('.vt-wgrail-bar'));
            out[tone] = { w: Math.round(b.width), h: Math.round(b.height), bg: cs.backgroundColor };
            row.remove();
          }
          return out;
        }"""
    )
    for tone, v in bars.items():
        assert v["w"] >= 3, f"{tone} 막대 폭이 {v['w']}px"
        assert v["h"] > 0, f"{tone} 막대가 높이 0이다"
        assert v["bg"] not in ("", "rgba(0, 0, 0, 0)"), f"{tone} 막대가 투명하다: {v}"
    assert len({v["bg"] for v in bars.values()}) == len(bars), f"상태별 색이 겹친다: {bars}"


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
          const top = (x, y) => document.elementFromPoint(x, y)?.closest('#vt-wgrail, #vt-dock, .vt-onboarding')?.id || 'onboarding';
          const rail = document.getElementById('vt-wgrail').getBoundingClientRect();
          const dock = document.getElementById('vt-dock').getBoundingClientRect();
          return {
            rail: top(rail.left + rail.width / 2, 400),
            dock: top(dock.left + dock.width / 2, 400),
          };
        }"""
    )
    assert hit["rail"] == "vt-wgrail", f"온보딩이 레일을 덮었다: {hit}"
    assert hit["dock"] == "vt-dock", f"온보딩이 dock을 덮었다: {hit}"


def test_파일이_모달이_아니라_페인으로_열린다(page):
    """N35 §6 — 모달 코드 뷰어는 제거됐다. 파일은 pane 트리의 leaf(kind:viewer)로
    열리고, 그 내용이 pane 상자 안에 갇혀 있어야 한다(밖으로 새면 표면 레이어의
    터미널 위를 덮는다)."""
    target = str(ROOT / "VERSION")
    page.evaluate("(p) => window.openFileInPane(p)", target)
    page.wait_for_selector(".vt-pane-viewer .vt-vw-cl", timeout=15000)
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
            lines: body.querySelectorAll('.vt-vw-cl').length,
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


def test_레일_파일_버튼은_사라지고_팔레트가_그_자리다(page):
    """§6 — 「파일」 진입점은 팔레트로 옮겨갔다. 옛 rail 버튼이 남아 있으면
    누를 대상이 없는 버튼이 된다."""
    assert page.evaluate("() => !document.getElementById('vt-rail-file')")
    page.evaluate("() => window.getAction('viewer.show')()")
    page.wait_for_selector("#vt-qopen", timeout=5000)
    page.keyboard.press("Escape")


# ── N43 §8 리사이즈 오버레이 ────────────────────────────────────────────────

def test_리사이즈_오버레이가_분할선_드래그_중에만_보인다(page, server):
    """§8 — 드래그 중 픽셀 크기 + 칸 수가 뜨고, 놓으면 200ms 뒤 사라진다.
    CI에는 tmux 세션이 없으므로 일반 터미널 세션을 직접 만든다.

    이 테스트가 만드는 세션은 **서버 쪽 상태**(session_store)라 브라우저
    컨텍스트를 닫아도 안 없어진다 — 뒤 테스트가 "세션이 이미 있다" 경로로
    부팅되며 실제로 깨졌다(스모크 하네스가 공유 서버 위에서 순서대로 돈다).
    끝나면 만든 세션을 DELETE로 정리한다.
    """
    base, token = server
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
            page.request.delete(f"{base}/api/sessions/{sid}", params={"token": token})


# ── N35 §6 dock ───────────────────────────────────────────────────────────

def test_dock_탭_행이_문서대로_그려진다(page):
    """소스컨트롤 | 큐 | 포트 | 사용량 — capability가 없는 탭은 아예 없다."""
    page.wait_for_selector("#vt-dock .vt-dock-tab", timeout=10000)
    labels = page.locator("#vt-dock .vt-dock-tab").all_text_contents()
    assert labels[:1] == ["소스컨트롤"], f"첫 탭이 소스컨트롤이 아니다: {labels}"
    assert "큐" in labels, labels


def test_dock은_wide에서_접힘이_기본이고_본문_패딩이_접힘을_따라간다(page):
    """§5에서 실제로 났던 버그의 dock 판: 접었는데 body padding이 펼친 폭
    그대로 남으면 화면 오른쪽에 빈 띠가 생긴다. 두 값을 같이 본다."""
    page.set_viewport_size(WIDE)   # 1440 — xwide(1600) 미만이라 접힘 기본
    page.wait_for_timeout(300)
    got = page.evaluate(
        """() => ({
             collapsed: document.getElementById('vt-dock').classList.contains('collapsed'),
             dockW: Math.round(document.getElementById('vt-dock').getBoundingClientRect().width),
             padR: getComputedStyle(document.body).paddingRight,
           })"""
    )
    assert got["collapsed"], f"wide에서는 접힘이 기본이어야 한다: {got}"
    assert got["dockW"] == 36, got
    assert got["padR"] == "36px", f"본문 패딩이 접힘 폭을 안 따라간다: {got}"


def test_dock_탭을_누르면_그_패널이_dock_안에_마운트된다(page):
    """모달 backdrop이 아니라 dock 본문에 들어가는가(패널 렌더러 재사용의 핵심).
    같은 탭을 다시 누르면 접힌다 — 진입점을 이중화하지 않기 위한 규칙."""
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

    # 같은 탭 재클릭 = 접기
    page.locator("#vt-dock .vt-dock-tab", has_text="큐").first.click()
    page.wait_for_function(
        "() => document.getElementById('vt-dock').classList.contains('collapsed')", timeout=5000
    )
    assert page.evaluate("() => !document.getElementById('vt-queue')"), "접었는데 패널이 남았다"


def test_dock_소스컨트롤_탭이_저장소_상태를_읽어온다(page):
    """40 §3 — 머리말(저장소·브랜치)과 목록이 실제 API 응답으로 채워지는가."""
    _open_dock(page, "소스컨트롤")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    got = page.evaluate(
        """() => ({
             head: document.querySelector('#vt-scm-head')?.innerText || '',
             log: document.querySelectorAll('#vt-dock-scm .vt-vw-crow').length,
             inDock: !!document.querySelector('#vt-dock .vt-dock-body #vt-dock-scm'),
           })"""
    )
    assert got["inDock"], "소스컨트롤 패널이 dock 밖에 있다"
    assert got["log"] > 0, f"커밋 기록이 비었다: {got}"
    assert got["head"].strip(), "머리말이 비었다 — 저장소를 못 찾았다"


def test_dock_소스컨트롤_쓰기_버튼은_비활성이고_이유를_말한다(page):
    """40 §5 — 2.1.0은 읽기 전용. 숨기지 않고 disabled + 사유 툴팁으로 둔다."""
    _open_dock(page, "소스컨트롤")
    page.wait_for_selector("#vt-dock-scm .vt-scm-foot button", timeout=15000)
    btns = page.evaluate(
        """() => [...document.querySelectorAll('.vt-scm-foot button')]
                 .map((b) => [b.textContent, b.disabled, b.title])"""
    )
    assert [b[0] for b in btns] == ["커밋", "push", "PR 만들기"], btns
    assert all(b[1] for b in btns), f"쓰기 버튼이 열려 있다: {btns}"
    assert all("2.1.1" in b[2] for b in btns), f"이유 툴팁이 없다: {btns}"


def test_dock_소스컨트롤_diff_줄에서_큐_코멘트가_열린다(page):
    """40 §3의 「줄 클릭 → 큐에 코멘트」. 2.0 뷰어의 같은 코드를 dock에서
    그대로 쓰는지 실제 클릭으로 확인한다 — 변경된 파일이 없으면 건너뛴다."""
    _open_dock(page, "소스컨트롤")
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=15000)
    rows = page.locator("#vt-dock-scm .vt-vw-gsec .vt-vw-grow")
    if rows.count() == 0:
        pytest.skip("작업 트리가 깨끗해 diff를 열 파일이 없다")
    rows.first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-dl", timeout=15000)
    page.locator("#vt-dock-scm .vt-vw-dl").first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-annotate", timeout=5000)
    # 되돌아가기 — dock은 한 탭 안에서 상태↔diff를 오간다(모달을 새로 안 띄운다)
    page.locator("#vt-dock-scm .vt-vw-cback").first.click()
    page.wait_for_selector("#vt-dock-scm .vt-vw-git", timeout=10000)


def test_dock_폭_리사이저가_범위를_지킨다(page):
    """320~560. 드래그로 그 밖으로 못 나간다 — 나가면 pane이 죽거나 dock이 빈다."""
    _open_dock(page, "큐")
    box = page.locator("#vt-dock .vt-dock-resizer").bounding_box()
    assert box, "리사이저가 렌더되지 않았다"
    for dx in (-600, 900):   # 넓히는 쪽 · 좁히는 쪽 둘 다 끝까지
        page.mouse.move(box["x"] + 3, box["y"] + 200)
        page.mouse.down()
        page.mouse.move(box["x"] + 3 + dx, box["y"] + 200, steps=8)
        page.mouse.up()
        page.wait_for_timeout(150)
        w = page.evaluate("() => Math.round(document.getElementById('vt-dock').getBoundingClientRect().width)")
        assert 320 <= w <= 560, f"dock 폭이 범위를 벗어났다: {w}px (dx={dx})"
        box = page.locator("#vt-dock .vt-dock-resizer").bounding_box()
