"""재접속 재생본 필터(server/replay_filter.py) 회귀.

재생본에 질의가 남으면 재접속마다 xterm.js가 옛 질의에 다시 답하고, 그 답이 PTY 입력으로
들어가 프롬프트에 `2026;0$y` 같은 글자가 쌓였다. 이 파일은 (1) 응답·부작용을 부르는 시퀀스만
빠지고 (2) 화면을 그리는 바이트는 한 바이트도 바뀌지 않으며 (3) 청크를 어디서 잘라도 결과가
같은지를 본다.
"""

import pytest

from pty_manager import PTYManager, PTYSession
from replay_filter import OSC_HOLD_MAX, ReplayFilter


def run(data: bytes) -> bytes:
    return ReplayFilter().feed(data)


def run_split(data: bytes, size: int) -> bytes:
    f = ReplayFilter()
    return b"".join(f.feed(data[i:i + size]) for i in range(0, len(data), size))


DROPPED = [
    b"\x1b[c", b"\x1b[0c", b"\x1b[>c", b"\x1b[>0c", b"\x1b[=c",          # DA1 · DA2 · DA3
    b"\x1b[5n", b"\x1b[6n", b"\x1b[?6n", b"\x1b[?996n",                  # DSR · DECXCPR · 색 모드
    b"\x1b[?2026$p", b"\x1b[?1016$p", b"\x1b[4$p",                       # DECRQM
    b"\x1b[>q", b"\x1b[>0q",                                             # XTVERSION
    b"\x1b[14t", b"\x1b[16t", b"\x1b[18t", b"\x1b[21t",                  # 창 보고 질의
    b"\x1b[?u",                                                          # kitty 키보드 질의
    b"\x1b[?1;1S",                                                       # XTSMGRAPHICS
    b"\x1b[x", b"\x1b[1$w", b"\x1b[1$u", b"\x1b[1;1;1;1;1;1*y",          # DECREQTPARM · DECRQPSR · DECRQTSR · DECRQCRA
    b"\x1bZ",                                                            # DECID
    b"\x1b]10;?\x1b\\", b"\x1b]11;?\x07", b"\x1b]4;1;?\x07", b"\x1b]7501;?\x1b\\",
    b"\x1b]52;c;aGVsbG8=\x07", b"\x1b]52;c;?\x1b\\",                     # 클립보드 쓰기·읽기
    b"\x1bP$qm\x1b\\", b"\x1bP+q544e\x1b\\",                             # DECRQSS · XTGETTCAP
    b"\x07",                                                             # BEL
]

KEPT = [
    b"plain text \xed\x95\x9c\xea\xb8\x80\r\n",
    b"\x1b[31mred\x1b[0m", b"\x1b[38;2;1;2;3m", b"\x1b[2J\x1b[H", b"\x1b[1;24r", b"\x1b[5;1H",
    b"\x1b[?2026h", b"\x1b[?2026l", b"\x1b[?1049h", b"\x1b[?2004h", b"\x1b[?1000h\x1b[?1006h",
    b"\x1b[2 q",                       # DECSCUSR(커서 모양) — `q`지만 질의가 아니다
    b"\x1b[>4;1m", b"\x1b[>1u", b"\x1b[<u", b"\x1b[=1;1u",  # modifyOtherKeys · kitty 켜기/끄기
    b"\x1b[8;24;80t", b"\x1b[22;0t", b"\x1b[23;0t",          # 창 조작·제목 스택(보고 아님)
    b"\x1b]0;title\x07", b"\x1b]2;title\x1b\\", b"\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\",
    b"\x1b]133;A\x07", b"\x1b]7;file://host/tmp\x07", b"\x1b]104\x07", b"\x1b]110\x1b\\",
    b"\x1bPq#0;2;0;0;0~-\x1b\\",       # sixel DCS
    b"\x1b_Gf=24;AAAA\x1b\\",          # APC(kitty 그래픽)
    b"\x1b7\x1b8", b"\x1b(B", b"\x1b=", b"\x1b>", b"\x1bc", b"\x1bM",
]


@pytest.mark.parametrize("seq", DROPPED)
def test_query_and_side_effect_sequences_are_removed(seq):
    assert run(b"a" + seq + b"b") == b"ab"


@pytest.mark.parametrize("seq", KEPT)
def test_drawing_sequences_are_kept_byte_for_byte(seq):
    assert run(b"a" + seq + b"b") == b"a" + seq + b"b"


def _mixed() -> bytes:
    parts = []
    for i, k in enumerate(KEPT):
        parts.append(k)
        parts.append(DROPPED[i % len(DROPPED)])
    return b"".join(parts)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 64])
def test_result_does_not_depend_on_chunk_boundaries(size):
    data = _mixed()
    assert run_split(data, size) == run(data) == b"".join(KEPT)


def test_tmux_attach_probe_bytes_are_cleaned():
    # tmux 3.7c가 붙을 때 실제로 내보낸 질의(격리 tmux에서 잡은 바이트)
    attach = (b"\x1b[?1049h\x1b[?996n\x1b[c\x1b[>c\x1b[>q"
              b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\\x1b[H\x1b[2Jprompt% ")
    assert run(attach) == b"\x1b[?1049h\x1b[H\x1b[2Jprompt% "


def test_large_clipboard_write_is_dropped_without_buffering_it_all():
    payload = b"A" * (2 * 1024 * 1024)
    f = ReplayFilter()
    out = f.feed(b"x\x1b]52;c;") + f.feed(payload) + f.feed(b"\x07y")
    assert out == b"xy"
    assert len(f._pending) == 0


def test_large_non_query_osc_passes_once_past_the_hold_limit():
    body = b"\x1b]1337;File=inline=1:" + b"B" * (OSC_HOLD_MAX * 3) + b"\x07"
    assert run_split(b"<" + body + b">", 1000) == b"<" + body + b">"


def test_bel_inside_osc_is_the_terminator_not_a_bell():
    assert run(b"\x1b]0;t\x07\x07") == b"\x1b]0;t\x07"


def test_string_cut_by_another_escape_is_closed_explicitly():
    # 종결자 없이 새 ESC가 오면 문자열은 거기서 끝난다 — 남기는 문자열은 ST로 닫고 ESC는 한 번만 나온다
    assert run(b"\x1b]0;t\x1b[31mX") == b"\x1b]0;t\x1b\\\x1b[31mX"
    assert run(b"\x1b]10;?\x1b[31mX") == b"\x1b[31mX"


def test_cancelled_sequences_are_discarded_like_xterm_does():
    assert run(b"\x1b[1\x18Z") == b"\x18Z"
    assert run(b"\x1b(\x1aZ") == b"\x1aZ"
    assert run(b"\x1b\x18Z") == b"\x18Z"
    assert run(b"\x1b]0;t\x1aZ") == b"\x1b]0;t\x1aZ"   # 문자열은 취소 바이트가 남아 재생에서도 취소된다


@pytest.mark.parametrize("data", [
    b"\x1b\x1b[$wZ",          # 혼자 남은 ESC + (뺀 질의) + Z → ESC Z(DECID)가 되면 안 된다
    b"\x1b\x1b[18tZ",
    b"\x1b\x1b[$p[$p",        # → ESC [ $ p
    b"\x1b[\x1b[$p?u",        # 끊긴 CSI + (뺀 질의) + ?u → ESC [ ? u
    b"\x1b]10;\x1b[c?\x07",   # 끊긴 OSC + (뺀 질의) + ?BEL → OSC 10;? 가 되면 안 된다
])
def test_removing_a_query_never_glues_leftovers_into_a_new_query(data):
    once = run(data + b"\x1b[m")
    assert sorted(run(once)) == sorted(once), once


def test_output_contains_no_query_after_one_pass():
    # 한 번 거른 결과를 다시 걸러도 빠지는 바이트가 없어야 한다(제어 문자 자리만 앞당겨질 수 있다)
    import random

    rng = random.Random(7)
    alphabet = b"\x1b\x07\x18\x1a\r\n[]P_X^Z\\?$>=<*; 0123456789cnpqtuSxwyhmHK;ab\xea\xb0\x80"
    for _ in range(3000):
        parts = [rng.choice(KEPT + DROPPED) if rng.random() < 0.4
                 else bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
                 for _ in range(rng.randint(1, 25))]
        once = run(b"".join(parts) + b"\x1b[m")
        assert sorted(run(once)) == sorted(once), once


def test_unfinished_sequence_is_held_until_completed():
    f = ReplayFilter()
    assert f.feed(b"ab\x1b[?20") == b"ab"
    assert f.feed(b"26") == b""
    assert f.feed(b"h") == b"\x1b[?2026h"
    assert f.feed(b"\x1b[?20") == b""
    assert f.feed(b"26$p!") == b"!"


def test_overlong_csi_passes_through():
    seq = b"\x1b[" + b"1;" * 200 + b"m"
    assert run(seq) == seq


def test_manager_stores_filtered_copy_but_broadcasts_raw_output():
    mgr = PTYManager()
    s = PTYSession(session_id="rf", pid=0, fd=-1)
    mgr._sessions["rf"] = s
    got = []
    s._subscribers.add(got.append)
    raw = b"hi\x1b[?2026$p\x1b]52;c;eA==\x07\x07there"
    s._out_buf.extend(raw)
    mgr._flush_session("rf")
    assert got == [raw]                                   # 실시간은 그대로
    assert b"".join(mgr.get_scrollback("rf")) == b"hithere"   # 재생본은 정화


def test_random_splits_and_noise_give_the_same_result_as_one_chunk():
    # 무작위 잡음(ESC·BEL·CAN·문자열 시작이 섞인 바이트)에서도 어디서 자르든 한 번에 넣은 결과와 같아야 한다
    import random

    rng = random.Random(20261125)
    alphabet = b"\x1b\x07\x18\x1a\r\n[]P_X^Z\\?$>=<*; 0123456789cnpqtuSxwyhmHK;ab\xea\xb0\x80"
    for _ in range(3000):
        parts = [rng.choice(KEPT + DROPPED) if rng.random() < 0.5
                 else bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
                 for _ in range(rng.randint(1, 20))]
        data = b"".join(parts)
        whole = run(data)
        f = ReplayFilter()
        pieces, pos = [], 0
        while pos < len(data):
            step = rng.randint(1, 9)
            pieces.append(f.feed(data[pos:pos + step]))
            pos += step
        assert b"".join(pieces) == whole, data
