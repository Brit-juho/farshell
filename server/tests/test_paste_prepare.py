"""N24(2.1.5 2/n) — 붙여넣기 페이로드 준비. 순수 함수 테스트."""

from __future__ import annotations

import pytest

import paste_prepare as pp


def test_control_chars_whitelist_keeps_tab_lf_ff_cr_ctrlv():
    raw = "a\tb\nc\rd\x0ce\x16f"
    out = pp.strip_unsafe_control_chars(raw)
    assert out == raw, "화이트리스트 안의 제어문자는 그대로 남아야 한다"


def test_control_chars_strips_everything_else():
    raw = "a\x01b\x03c\x1bd\x7fe"  # SOH, ETX, ESC, DEL
    out = pp.strip_unsafe_control_chars(raw)
    assert out == "abcde"


def test_control_char_strip_preserves_printable_unicode():
    raw = "한글 émoji 🎉"
    assert pp.strip_unsafe_control_chars(raw) == raw


def test_normalize_newlines_collapses_all_forms_to_cr():
    assert pp.normalize_newlines("a\r\nb\nc\rd") == "a\rb\rc\rd"


def test_normalize_newlines_noop_without_newlines():
    assert pp.normalize_newlines("hello") == "hello"


def test_neutralize_escape_replaces_esc_only():
    assert pp.neutralize_escape("a\x1bb\x1bc") == "a␛b␛c"
    assert pp.neutralize_escape("no escape here") == "no escape here"


def test_prepare_payload_empty_text_is_empty_bytes():
    assert pp.prepare_paste_payload("", bracket=True) == b""
    assert pp.prepare_paste_payload("", bracket=False) == b""


def test_prepare_payload_bracket_true_wraps_and_neutralizes_esc():
    payload = pp.prepare_paste_payload("echo \x1b[0c hi", bracket=True)
    assert payload.startswith(pp.PASTE_START)
    assert payload.endswith(pp.PASTE_END)
    assert b"\x1b" not in payload[len(pp.PASTE_START):-len(pp.PASTE_END)], \
        "감싼 본문 안의 ESC는 무력화돼야 한다(마커 조기 종료 방어)"
    assert "␛".encode() in payload


def test_prepare_payload_bracket_false_does_not_wrap_but_still_strips_esc():
    # ESC는 iTerm2 화이트리스트에도 없다 — bracket=False라 무력화(→␛)는
    # 안 하지만, 위험 제어문자 제거 규칙(결정 2)이 그대로 걸려 사라진다.
    payload = pp.prepare_paste_payload("echo \x1b[0c hi", bracket=False)
    assert pp.PASTE_START not in payload
    assert pp.PASTE_END not in payload
    assert b"\x1b" not in payload
    assert "␛".encode() not in payload, "무력화(치환)는 bracket=True에서만 일어난다 — 여기선 그냥 삭제"
    assert payload == b"echo [0c hi"


def test_prepare_payload_strips_dangerous_control_and_normalizes_newlines_regardless_of_bracket():
    for bracket in (True, False):
        payload = pp.prepare_paste_payload("a\x01b\r\nc", bracket=bracket)
        assert b"\x01" not in payload
        assert b"\r\n" not in payload
        assert b"\rc" in payload


def test_prepare_payload_multiline_paste_end_to_end_bracketed():
    text = "line1\nline2\r\nline3"
    payload = pp.prepare_paste_payload(text, bracket=True)
    body = payload[len(pp.PASTE_START):-len(pp.PASTE_END)]
    assert body == b"line1\rline2\rline3"


# ── N26(2.1.5 4/n) — 정규 모드 한 줄 한계 ───────────────────────────────────

def test_check_canonical_line_limits_passes_under_the_limit():
    pp.check_canonical_line_limits("x" * 100, max_line=1024)  # 안 던지면 통과


def test_check_canonical_line_limits_exact_boundary_passes():
    # 개행 포함해서 정확히 한계 — 실측(input_mode 1/n)상 여기까지는 통과한다.
    pp.check_canonical_line_limits("x" * 1023 + "\n", max_line=1024)


def test_check_canonical_line_limits_one_over_raises():
    with pytest.raises(pp.LineTooLong) as exc:
        pp.check_canonical_line_limits("x" * 1024 + "\n", max_line=1024)
    assert exc.value.max_line == 1024
    assert exc.value.line_length == 1025


def test_check_canonical_line_limits_checks_every_line_independently():
    # 첫 줄은 짧고 둘째 줄이 넘친다 — 둘째 줄 인덱스로 잡아야 한다.
    text = "short\n" + ("y" * 2000) + "\n"
    with pytest.raises(pp.LineTooLong) as exc:
        pp.check_canonical_line_limits(text, max_line=1024)
    assert exc.value.line_index == 1


def test_check_canonical_line_limits_ignores_trailing_partial_line_correctly():
    # 마지막 줄에 개행이 없으면(아직 안 끝난 줄) 그 줄 자체 길이만 잰다.
    pp.check_canonical_line_limits("x" * 1024, max_line=1024)  # 개행 없어서 1024 그대로 — 통과
    with pytest.raises(pp.LineTooLong):
        pp.check_canonical_line_limits("x" * 1025, max_line=1024)
