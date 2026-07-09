"""scrollback 메모리 회귀: 세션당 저장량이 바이트 예산으로 제한돼야 한다.

예전엔 deque(maxlen=5000) × 4KB = 세션당 최대 19.5MB를 저장했는데, 재접속 시 실제
전송은 SCROLLBACK_MAX_BYTES(256KB)뿐이라 78배 과다 저장 → 세션 몇 개만 열려도
수백 MB로 폭증했다. 이제 SCROLLBACK_STORE_BYTES(512KB) + 청크 수 상한으로 제한한다.
"""

import pty_manager
from pty_manager import PTYManager, PTYSession


def _session():
    return PTYSession(session_id="t", pid=1, fd=1)


def test_scrollback_capped_by_bytes():
    mgr = PTYManager()
    s = _session()
    # 예전 같으면 19.5MB가 쌓일 입력
    for _ in range(5000):
        mgr._append_scrollback(s, b"x" * 4096)
    assert s._scrollback_bytes <= PTYManager.SCROLLBACK_STORE_BYTES
    # 저장 바이트 카운터와 실제 deque 합계가 일치해야 함(누적 트리밍 정확성)
    assert s._scrollback_bytes == sum(len(c) for c in s._scrollback)


def test_scrollback_capped_by_chunk_count_on_tiny_chunks():
    mgr = PTYManager()
    s = _session()
    # 1바이트 청크 폭주 — 바이트 예산엔 한참 못 미치지만 항목 수가 폭발할 수 있다.
    for _ in range(200_000):
        mgr._append_scrollback(s, b"z")
    assert len(s._scrollback) <= PTYManager.SCROLLBACK_MAX_CHUNKS
    assert s._scrollback_bytes == sum(len(c) for c in s._scrollback)


def test_store_cap_covers_send_cap():
    # 저장 상한은 전송 상한보다 커야 재접속 시 항상 꽉 찬 256KB를 보낼 수 있다.
    assert PTYManager.SCROLLBACK_STORE_BYTES >= PTYManager.SCROLLBACK_MAX_BYTES


def test_get_scrollback_returns_within_send_cap():
    mgr = PTYManager()
    # get_scrollback은 _get으로 세션을 조회하므로 등록이 필요 — 내부 dict에 직접 넣는다.
    s = _session()
    s.session_id = "reg"
    mgr._sessions["reg"] = s
    for _ in range(5000):
        mgr._append_scrollback(s, b"x" * 4096)
    out = mgr.get_scrollback("reg")
    assert sum(len(c) for c in out) <= PTYManager.SCROLLBACK_MAX_BYTES