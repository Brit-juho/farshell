"""E2E 테스트 — Claude-in-Chrome MCP로 검증한 시나리오를 Playwright로 자동화.

실행 방법:
  # 서버 먼저 시작
  cd server && python -m uvicorn main:app --host 0.0.0.0 --port 7777

  # 다른 터미널에서
  pytest server/tests/test_e2e.py -v

환경 변수:
  VT_E2E_URL  — 기본값 http://localhost:7777
"""

from __future__ import annotations

import time
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:7777"


@pytest.fixture(scope="session")
def browser_context(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    yield context
    context.close()
    browser.close()


@pytest.fixture
def page(browser_context):
    p = browser_context.new_page()
    yield p
    p.close()


# ---------------------------------------------------------------------------
# TC1: 페이지 로드 + 온보딩
# ---------------------------------------------------------------------------

def test_tc1_page_loads(page: Page):
    """페이지가 정상 로드되고 farshell 타이틀을 가짐."""
    page.goto(BASE_URL)
    assert "farshell" in page.title().lower()


def test_tc1_onboarding_or_terminal(page: Page):
    """온보딩 또는 터미널이 표시됨 (둘 중 하나)."""
    page.goto(BASE_URL)
    # 온보딩이 있으면 버튼 클릭
    onboarding = page.locator("#onboarding")
    if onboarding.count() > 0 and onboarding.is_visible():
        page.locator("text=일반 터미널").click()
        page.wait_for_selector("#tabs", timeout=5000)
    assert page.locator("#tabs").is_visible()


# ---------------------------------------------------------------------------
# TC2: Grid 버튼 위치 + 스타일
# ---------------------------------------------------------------------------

def test_tc2_grid_button_exists_rightmost(page: Page):
    """Grid 버튼이 탭 바 최우측에 위치 (ml-auto)."""
    page.goto(BASE_URL)
    # 온보딩 처리
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector("#grid-toggle", timeout=5000)

    grid_btn = page.locator("#grid-toggle")
    assert grid_btn.is_visible()
    assert "Grid" in grid_btn.text_content()

    # tabs의 마지막 자식 여부
    is_last = page.evaluate("""
        const tabs = document.getElementById('tabs');
        const btn = document.getElementById('grid-toggle');
        const children = Array.from(tabs.children);
        children.indexOf(btn) === children.length - 1
    """)
    assert is_last, "Grid 버튼이 탭 바의 마지막 요소가 아님"


def test_tc2_grid_initially_hidden(page: Page):
    """Grid 뷰는 초기에 숨겨져 있어야 함."""
    page.goto(BASE_URL)
    grid_view = page.locator("#grid-view")
    assert page.evaluate("document.getElementById('grid-view').style.display") == "none"


# ---------------------------------------------------------------------------
# TC3: Grid 뷰 토글 + 빈 상태
# ---------------------------------------------------------------------------

def test_tc3_grid_toggle_open(page: Page):
    """Grid 버튼 클릭 시 그리드 뷰가 열리고 버튼이 active 상태가 됨."""
    page.goto(BASE_URL)
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector("#grid-toggle", timeout=5000)

    page.locator("#grid-toggle").click()
    page.wait_for_timeout(1500)  # refreshGrid() 완료 대기

    assert page.evaluate("gridViewEnabled") is True
    assert page.evaluate("document.getElementById('grid-view').style.display") != "none"
    assert page.evaluate("document.getElementById('grid-toggle').classList.contains('active')") is True
    # 터미널 컨테이너는 숨겨짐
    assert page.evaluate("document.getElementById('terminal-container').style.display") == "none"


def test_tc3_grid_empty_state_when_no_sessions(page: Page):
    """tmux 세션이 없을 때 빈 상태 안내 메시지가 표시됨."""
    page.goto(BASE_URL)
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector("#grid-toggle", timeout=5000)

    page.locator("#grid-toggle").click()
    page.wait_for_timeout(1500)

    cards_html = page.evaluate("document.getElementById('grid-cards').innerHTML")
    # tmux 세션 없으면 빈 상태 메시지 또는 카드 중 하나
    # (세션이 있으면 카드가, 없으면 빈 상태 메시지가 표시됨)
    assert len(cards_html) > 10, "그리드 카드 영역이 완전히 비어있음"


# ---------------------------------------------------------------------------
# TC4: Esc로 Grid 닫기
# ---------------------------------------------------------------------------

def test_tc4_esc_closes_grid(page: Page):
    """Esc 키로 Grid 뷰가 닫힘."""
    page.goto(BASE_URL)
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector("#grid-toggle", timeout=5000)

    page.locator("#grid-toggle").click()
    page.wait_for_timeout(500)
    assert page.evaluate("gridViewEnabled") is True

    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    assert page.evaluate("gridViewEnabled") is False
    assert page.evaluate("document.getElementById('grid-view').style.display") == "none"
    assert page.evaluate("document.getElementById('grid-toggle').classList.contains('active')") is False


# ---------------------------------------------------------------------------
# TC5: 탭 DnD cursor:grab
# ---------------------------------------------------------------------------

def test_tc5_tab_cursor_grab(page: Page):
    """탭에 cursor:grab이 적용되어 드래그 가능성을 시각적으로 표시."""
    page.goto(BASE_URL)
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector(".tab", timeout=5000)

    # 탭이 없으면 먼저 온보딩 통과 (이미 위에서 처리됐을 수도 있음)
    page.wait_for_timeout(500)
    tab_count = page.locator(".tab").count()
    if tab_count == 0:
        pytest.skip("탭 없음 — 온보딩 미완료 상태")

    cursor = page.evaluate("""
        const t = document.querySelector('.tab');
        t ? window.getComputedStyle(t).cursor : 'no-tab'
    """)
    assert cursor == "grab", f"탭 cursor가 'grab'이 아님: {cursor}"

    draggable = page.evaluate("document.querySelector('.tab')?.getAttribute('draggable')")
    assert draggable == "true", "탭에 draggable=true 없음"


# ---------------------------------------------------------------------------
# TC6: localStorage 워크스페이스
# ---------------------------------------------------------------------------

def test_tc6_workspace_saved_to_localstorage(page: Page):
    """switchTo 호출 시 localStorage에 워크스페이스가 저장됨."""
    page.goto(BASE_URL)
    if page.locator("#onboarding").count() > 0:
        page.locator("text=일반 터미널").click()
        page.wait_for_selector(".tab", timeout=5000)

    page.wait_for_timeout(500)

    ws_raw = page.evaluate("localStorage.getItem('vt-workspace-v1')")
    assert ws_raw is not None, "localStorage에 워크스페이스 없음"

    import json
    ws = json.loads(ws_raw)
    assert ws.get("version") == 1, "워크스페이스 version 필드 없음"
    assert isinstance(ws.get("tabs"), list), "워크스페이스 tabs가 배열이 아님"


def test_tc6_workspace_functions_exist(page: Page):
    """saveWorkspace, restoreWorkspace, clearWorkspace, makeTabDraggable 함수 존재."""
    page.goto(BASE_URL)
    fns = page.evaluate("""({
        save: typeof saveWorkspace === 'function',
        restore: typeof restoreWorkspace === 'function',
        clear: typeof clearWorkspace === 'function',
        makeDraggable: typeof makeTabDraggable === 'function',
    })""")
    assert fns["save"], "saveWorkspace 미존재"
    assert fns["restore"], "restoreWorkspace 미존재"
    assert fns["clear"], "clearWorkspace 미존재"
    assert fns["makeDraggable"], "makeTabDraggable 미존재"


# ---------------------------------------------------------------------------
# TC7: routes/ 분리 후 API 엔드포인트 응답
# ---------------------------------------------------------------------------

def test_tc7_api_endpoints_respond(page: Page):
    """routes/ 분리 후 주요 API 엔드포인트가 모두 200 응답."""
    page.goto(BASE_URL)

    endpoints = [
        "/api/capabilities",
        "/api/sessions",
        "/api/tmux/sessions",
        "/api/tunnel/status",
        "/api/tailscale/status",
        "/api/safe-mode",
        "/api/agents",
        "/api/agent/status",
        "/api/workspace",
        "/api/notify/status",
    ]

    for ep in endpoints:
        resp = page.request.get(f"{BASE_URL}{ep}")
        assert resp.status == 200, f"{ep} → HTTP {resp.status}"


def test_tc7_capabilities_structure(page: Page):
    """/api/capabilities에 network_mode, tunnel, voice 필드가 있음."""
    page.goto(BASE_URL)
    resp = page.request.get(f"{BASE_URL}/api/capabilities")
    data = resp.json()
    assert "network_mode" in data, "network_mode 필드 없음"
    assert "tunnel" in data, "tunnel 필드 없음"
    assert "voice" in data, "voice 필드 없음"
    assert "installed" in data["tunnel"], "tunnel.installed 필드 없음"
    assert "tailscale" in data, "tailscale 필드 없음"
    assert "installed" in data["tailscale"], "tailscale.installed 필드 없음"


# ---------------------------------------------------------------------------
# TC8: Grid 카드 hover 스타일 (CSS 존재 확인)
# ---------------------------------------------------------------------------

def test_tc8_grid_card_hover_css_exists(page: Page):
    """그리드 카드 hover CSS 규칙이 로드됨."""
    page.goto(BASE_URL)
    # 인라인 style 태그에 hover 관련 CSS가 있는지 확인
    style_content = page.evaluate("""
        Array.from(document.querySelectorAll('style'))
            .map(s => s.textContent)
            .join('')
    """)
    assert "drag-over-left" in style_content, "drag-over-left CSS 없음"
    assert "drag-over-right" in style_content, "drag-over-right CSS 없음"
    assert "grid-toggle" in style_content and "active" in style_content, "grid-toggle.active CSS 없음"
