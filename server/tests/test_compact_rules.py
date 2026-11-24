"""정리 규칙(session_history/compact_rules.py) — 순수 함수. 규칙이 건드린 줄만 바뀌고 나머지는 바이트 그대로다."""
import base64
import json
import os

import pytest

from session_history import compact_rules as rules

IMG = base64.b64encode(os.urandom(6000)).decode()


def line(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def claude_tool_line(text="결과 본문 " * 80, tur=None, extra_blocks=()):
    content = [{"type": "tool_result", "tool_use_id": "t1", "content": text}, *extra_blocks]
    obj = {"type": "user", "uuid": "u1", "parentUuid": "p1", "message": {"role": "user", "content": content}}
    if tur is not None:
        obj["toolUseResult"] = tur
    return line(obj), obj


def test_lines_without_a_matching_rule_keep_their_exact_bytes():
    raw = b'{"type":  "assistant",  "message": {"content": [{"type": "text", "text": "hi"}]}}\n'   # 공백이 특이한 줄
    stats = rules.new_stats()
    assert rules.claude_line(raw, ("tur", "img", "big"), stats) == raw
    assert rules.claude_line(b"not json at all\n", ("tur", "img", "big"), stats) == b"not json at all\n"
    big_text, _ = claude_tool_line()
    assert rules.claude_line(big_text, (), stats) == big_text, "규칙을 하나도 안 켜면 그대로"


def test_a_duplicated_tool_use_result_is_dropped_and_counted():
    body = "alpha beta gamma " * 60
    raw, _ = claude_tool_line(text=body, tur={"stdout": body, "stderr": "", "interrupted": False})
    stats = rules.new_stats()
    out = json.loads(rules.claude_line(raw, ("tur",), stats))
    assert "toolUseResult" not in out and out["message"]["content"][0]["content"] == body
    assert stats["tur_dropped_bytes"] > 900 and stats["tur_kept_bytes"] == 0


def test_a_tool_use_result_that_carries_other_information_is_kept():
    body = "alpha beta gamma " * 60
    raw, _ = claude_tool_line(text=body, tur={"structuredPatch": [{"oldStart": 3, "lines": ["-a", "+b"]}], "filePath": "/x/y.py"})
    stats = rules.new_stats()
    out = rules.claude_line(raw, ("tur",), stats)
    assert out == raw and stats["tur_kept_bytes"] > 0 and stats["tur_dropped_bytes"] == 0


def test_an_image_block_becomes_a_text_block_so_the_line_stays_valid():
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMG}}
    raw = line({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "봐"}, block]}})
    stats = rules.new_stats()
    out = json.loads(rules.claude_line(raw, ("img",), stats))
    content = out["message"]["content"]
    assert content[0] == {"type": "text", "text": "봐"}
    assert content[1]["type"] == "text" and content[1]["text"].startswith("[이미지 생략: image/png")
    assert IMG not in json.dumps(out) and stats["img_count"] == 1 and stats["img_bytes"] == len(IMG)


def test_an_image_inside_a_tool_result_is_replaced_and_a_small_one_is_not():
    big = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": IMG}}
    tiny = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    raw, _ = claude_tool_line(text=[{"type": "text", "text": "shot"}, big, tiny])
    stats = rules.new_stats()
    out = json.loads(rules.claude_line(raw, ("img",), stats))
    parts = out["message"]["content"][0]["content"]
    assert parts[1]["type"] == "text" and parts[2] == tiny and stats["img_count"] == 1


def test_a_huge_tool_output_keeps_its_head_and_tail():
    text = "H" * rules.BIG_HEAD + "M" * 70000 + "T" * rules.BIG_TAIL
    raw, _ = claude_tool_line(text=text)
    stats = rules.new_stats()
    out = json.loads(rules.claude_line(raw, ("big",), stats))
    kept = out["message"]["content"][0]["content"]
    assert kept.startswith("H" * 100) and kept.endswith("T" * 100) and "생략" in kept and len(kept) < 12000
    assert stats["big_count"] == 1 and stats["big_saved_bytes"] > 60000
    small, _ = claude_tool_line(text="x" * 30000)
    assert rules.claude_line(small, ("big",), rules.new_stats()) == small, "32KB 이하는 그대로"


def test_thinking_is_counted_but_never_changed():
    raw = line({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "음…" * 50, "signature": "sig" * 40}, {"type": "text", "text": "답"}]}})
    stats = rules.new_stats()
    assert rules.claude_line(raw, ("tur", "img", "big"), stats) == raw
    assert stats["thinking_count"] == 1 and stats["thinking_bytes"] > 300


def test_codex_data_urls_and_huge_outputs():
    url = "data:image/png;base64," + IMG
    raw = line({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "McpToolCall", "result": {"content": [{"type": "image", "data": url}]}}}})
    stats = rules.new_stats()
    out = json.loads(rules.codex_line(raw, ("img",), stats))
    assert "base64," not in json.dumps(out) and "[이미지 생략: image/png" in json.dumps(out, ensure_ascii=False) and stats["img_count"] == 1
    other = line({"type": "response_item", "payload": {"type": "message", "content": "data:image is just text here"}})
    assert rules.codex_line(other, ("img",), rules.new_stats()) == other
    out_raw = line({"type": "response_item", "payload": {"type": "function_call_output", "output": "x" * 90000}})
    shortened = json.loads(rules.codex_line(out_raw, ("big",), rules.new_stats()))
    assert len(shortened["payload"]["output"]) < 12000


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_trim_line_dispatches_by_provider(provider):
    raw = b'{"type":"x"}\n'
    assert rules.trim_line(provider, raw, ("tur", "img", "big"), rules.new_stats()) == raw
