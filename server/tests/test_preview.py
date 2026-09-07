"""preview.py — 라이브 프리뷰 캡처. 2026-09-07 L6 실브라우저 검증 중 발견한
실측 버그(_capture_no_cache의 "끝에서부터 lines줄" 자르기가, 창 높이가
lines보다 크고 실제 내용이 위쪽 몇 줄뿐이면 내용이 아니라 뒤쪽 빈 줄만
남기던 문제) 회귀 테스트. tmux_target 테스트와 같은 패턴(격리 소켓 +
monkeypatch)을 쓴다 — 실제 tmux 프로세스로 재현해야 의미가 있다(off-by-one류
버그는 실측 없이는 잡히지 않는다는 게 이번에 발견된 방식 자체가 증거).
"""

import subprocess
import time

import pytest

import preview
import tmux_runner

SOCKET = "vt-test-preview"
BASE = ["tmux", "-L", SOCKET]


def _tmux(*args: str) -> str:
    r = subprocess.run(BASE + list(args), capture_output=True, text=True, timeout=5, check=True)
    return r.stdout.strip()


@pytest.fixture
def tmux_server(monkeypatch, tmp_path):
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", SOCKET)
    monkeypatch.setattr(tmux_runner, "VT_TMUX_CONF", None)
    yield tmp_path
    subprocess.run(BASE + ["kill-server"], capture_output=True, timeout=5)


def test_capture_survives_tall_window_with_short_content(tmux_server):
    """실측 재현: 창이 lines(20)보다 훨씬 크고(50행) 내용은 위쪽 몇 줄뿐이면,
    옛 코드는 끝에서부터 20줄을 잘라 전부 빈 줄만 반환했다."""
    _tmux("new-session", "-d", "-s", "tall", "-x", "80", "-y", "50")
    _tmux("send-keys", "-t", "tall", "echo VT_PREVIEW_MARKER", "Enter")
    for _ in range(20):
        if "VT_PREVIEW_MARKER" in (_tmux("capture-pane", "-t", "tall", "-p") or ""):
            break
        time.sleep(0.25)
    else:
        pytest.fail("echo가 시간 내에 반영되지 않았다(환경 문제 — 버그 재현과 무관)")

    text = preview._capture_no_cache("tall", lines=20)
    assert text is not None
    assert "VT_PREVIEW_MARKER" in text, (
        f"실제 내용이 잘려나갔다(재현된 버그) — got: {text!r}"
    )


def test_capture_still_truncates_long_scrolling_output(tmux_server):
    """화면을 꽉 채우는 진짜 긴 출력(빈 줄 없음)에서는 여전히 마지막 lines줄만
    남아야 한다 — 회귀 수정이 "항상 전체를 반환"으로 과교정되지 않았는지 확인."""
    _tmux("new-session", "-d", "-s", "busy", "-x", "80", "-y", "50")
    time.sleep(0.3)
    _tmux("send-keys", "-t", "busy", "for i in $(seq 1 100); do echo LINE-$i; done", "Enter")
    # 고정 sleep 대신 완료를 폴링한다 — 전체 스위트와 같이 돌 때 머신 부하에 따라
    # 100줄 루프가 1.5초 안에 안 끝나 간헐적으로 실패했다(실측).
    for _ in range(40):
        if "LINE-100" in (_tmux("capture-pane", "-t", "busy", "-p") or ""):
            break
        time.sleep(0.25)
    else:
        pytest.fail("루프가 시간 내에 끝나지 않았다(환경 문제 — 버그 재현과 무관)")

    text = preview._capture_no_cache("busy", lines=20)
    assert text is not None
    lines = text.split("\n")
    assert len(lines) <= 20
    # 마지막 줄들이어야 한다 — 초반 줄(LINE-1 등)은 잘려나가고 최신 줄만 남는다.
    assert "LINE-100" in text or "LINE-9" in text
    assert "LINE-1\n" not in text and not text.startswith("LINE-1 ")


def test_capture_no_cache_missing_session_returns_none(tmux_server):
    assert preview._capture_no_cache("does-not-exist", lines=20) is None
