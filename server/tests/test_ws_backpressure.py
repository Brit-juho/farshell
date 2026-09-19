"""터미널 WS 송신 큐 백프레셔 회귀 테스트."""

import asyncio

import routes.pty as pty_route


def test_drained_send_queue_resumes_paused_pty():
    """PTY가 멈춘 뒤에는 새 출력 콜백이 없으므로 send worker가 재개해야 한다."""
    queue = asyncio.Queue()
    resumed = []

    paused = pty_route._resume_queue_backpressure_if_drained(
        queue, True, lambda: resumed.append(True)
    )

    assert paused is False
    assert resumed == [True]


def test_queue_above_low_watermark_stays_paused():
    queue = asyncio.Queue()
    for _ in range(pty_route.WS_QUEUE_LOW):
        queue.put_nowait(b"x")
    resumed = []

    paused = pty_route._resume_queue_backpressure_if_drained(
        queue, True, lambda: resumed.append(True)
    )

    assert paused is True
    assert resumed == []


def test_unpaused_queue_does_not_emit_duplicate_resume():
    queue = asyncio.Queue()
    resumed = []

    paused = pty_route._resume_queue_backpressure_if_drained(
        queue, False, lambda: resumed.append(True)
    )

    assert paused is False
    assert resumed == []
