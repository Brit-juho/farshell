"""A2 — pane_resolve의 3단 해석(pane → cwd → 포기) + $TMUX 소켓 검증.

가장 중요한 두 가지:
  1. **우리 소켓이 아닌 tmux의 pane id는 신뢰하지 않는다.** `%12`는 소켓마다
     따로 매겨지므로, 사용자의 개인 tmux(default 소켓)에서 온 `%12`가 우리
     소켓의 전혀 다른 pane과 우연히 같을 수 있다. 그 상태로 매칭하면 엉뚱한
     세션에 배지가 뜨고 큐가 거기로 투입된다.
  2. **모호하면 포기한다.** 같은 cwd에 세션이 둘이면 None. A2는 이 규칙을
     없애는 게 아니라, pane 자기보고를 앞에 붙여 모호해지는 경우를 줄인다.
"""

import pytest

import pane_resolve
import tmux_runner
from tmux_runner import PaneInfo


@pytest.fixture
def panes(monkeypatch):
    """가짜 tmux pane 목록. 테스트가 실제 tmux 서버에 의존하지 않게 한다."""
    data: list[PaneInfo] = []
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: list(data))
    return data


@pytest.fixture(autouse=True)
def _socket(monkeypatch):
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", "fsh")
    monkeypatch.delenv("VT_TMUX_SOCKET", raising=False)


# ── 소켓 검증 ─────────────────────────────────────────────────────────────
def test_our_socket_is_trusted():
    assert pane_resolve.is_our_socket("/private/tmp/tmux-501/fsh,12345,0") is True


def test_foreign_socket_is_not_trusted():
    """사용자 개인 tmux(default 소켓) — pane id를 믿으면 안 된다."""
    assert pane_resolve.is_our_socket("/private/tmp/tmux-501/default,12345,0") is False


def test_missing_tmux_env_is_not_trusted():
    assert pane_resolve.is_our_socket(None) is False
    assert pane_resolve.is_our_socket("") is False


def test_custom_socket_from_env_is_trusted(monkeypatch):
    monkeypatch.setenv("VT_TMUX_SOCKET", "vt-test")
    assert pane_resolve.is_our_socket("/tmp/tmux-501/vt-test,1,0") is True


# ── 3단 해석 ──────────────────────────────────────────────────────────────
def test_pane_id_wins_even_when_cwd_is_ambiguous(panes):
    """A2의 핵심 실증 — 같은 $HOME에 세션 둘이어도 정확히 하나만 특정한다."""
    panes += [
        PaneInfo(session="a", command="claude", pid=1, path="/Users/x", pane_id="%1"),
        PaneInfo(session="b", command="bash", pid=2, path="/Users/x", pane_id="%2"),
    ]
    session, how = pane_resolve.resolve("%1", "/tmp/tmux-501/fsh,1,0", "/Users/x")
    assert (session, how) == ("a", "pane")


def test_same_cwd_two_sessions_without_pane_gives_up(panes):
    """A2 이전의 동작 — pane 단서가 없으면 여전히 포기한다(엉뚱한 카드 금지)."""
    panes += [
        PaneInfo(session="a", command="claude", pid=1, path="/Users/x", pane_id="%1"),
        PaneInfo(session="b", command="bash", pid=2, path="/Users/x", pane_id="%2"),
    ]
    assert pane_resolve.resolve(None, None, "/Users/x") == (None, "ambiguous")


def test_cwd_fallback_when_unique(panes):
    panes += [PaneInfo(session="solo", command="claude", pid=1, path="/repo", pane_id="%9")]
    assert pane_resolve.resolve(None, None, "/repo") == ("solo", "cwd")


def test_foreign_tmux_pane_is_discarded_and_falls_back_to_cwd(panes):
    """개인 tmux의 %1이 우리 %1과 겹쳐도 그걸로 매칭하지 않는다."""
    panes += [
        PaneInfo(session="ours", command="claude", pid=1, path="/repo", pane_id="%1"),
        PaneInfo(session="other", command="bash", pid=2, path="/elsewhere", pane_id="%2"),
    ]
    session, how = pane_resolve.resolve("%1", "/tmp/tmux-501/default,1,0", "/elsewhere")
    assert (session, how) == ("other", "cwd"), "pane id가 아니라 cwd로 판정돼야 한다"


def test_foreign_tmux_without_cwd_match_reports_reason(panes):
    panes += [PaneInfo(session="ours", command="claude", pid=1, path="/repo", pane_id="%1")]
    assert pane_resolve.resolve("%1", "/tmp/tmux-501/default,1,0", "/nowhere") == (None, "foreign-tmux")


def test_unknown_pane_id_falls_back_to_cwd(panes):
    """훅이 보고한 pane이 이미 죽었을 때(세션 종료 등)."""
    panes += [PaneInfo(session="solo", command="claude", pid=1, path="/repo", pane_id="%9")]
    assert pane_resolve.resolve("%404", "/tmp/tmux-501/fsh,1,0", "/repo") == ("solo", "cwd")


def test_no_clues_at_all(panes):
    assert pane_resolve.resolve(None, None, None) == (None, "none")


def test_cwd_with_no_matching_pane(panes):
    panes += [PaneInfo(session="a", command="bash", pid=1, path="/other", pane_id="%1")]
    assert pane_resolve.resolve(None, None, "/repo") == (None, "none")


def test_pane_info_parses_pane_id_from_tmux_output(monkeypatch):
    """get_all_panes의 포맷 문자열에 #{pane_id}가 실제로 붙어 파싱되는지."""
    monkeypatch.setattr(
        tmux_runner, "run_text",
        lambda *a, **k: "dev\tclaude\t123\t/repo\t%7\nother\tbash\t124\t/tmp\t%8",
    )
    panes = tmux_runner.get_all_panes()
    assert [p.pane_id for p in panes] == ["%7", "%8"]
    assert panes[0].session == "dev" and panes[0].path == "/repo"


def test_pane_info_tolerates_old_format_without_pane_id(monkeypatch):
    """tmux가 예상 밖 출력을 줘도 죽지 않는다(pane_id는 빈 문자열)."""
    monkeypatch.setattr(tmux_runner, "run_text", lambda *a, **k: "dev\tclaude\t123\t/repo")
    panes = tmux_runner.get_all_panes()
    assert panes[0].pane_id == ""
