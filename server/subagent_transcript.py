"""Bounded JSONL projection. Never publishes reasoning, raw paths or arbitrary JSON."""
from __future__ import annotations

import base64
import hashlib
import json
import os

import fsguard

MAX_BYTES = 1024 * 1024
MAX_LINE = 256 * 1024
MAX_ITEMS = 200


class BadCursor(ValueError):
    pass


class Unsupported(ValueError):
    pass


def decode_cursor(token):
    if token is None:
        return None
    if not isinstance(token, str) or not 1 <= len(token) <= 256:
        raise BadCursor
    try:
        v = json.loads(base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True))
        if (not isinstance(v, list) or len(v) != 3 or v[0] != 1
                or not isinstance(v[1], str) or len(v[1]) > 100
                or type(v[2]) is not int or not 0 <= v[2] <= 2**63 - 1):
            raise BadCursor
        return v[1], v[2]
    except (ValueError, TypeError, UnicodeError):
        raise BadCursor from None


def _cursor(generation, offset):
    return base64.urlsafe_b64encode(json.dumps([1, generation, offset], separators=(",", ":")).encode()).decode().rstrip("=")


def _text(v):
    return v[:2000] if isinstance(v, str) else ""


def _summary(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError, RecursionError):
            return _text(v)
    if isinstance(v, dict):
        for k in ("command", "file_path", "path", "pattern", "query", "url", "description"):
            if isinstance(v.get(k), str):
                return _text(v[k])
    return ""


def _content(content):
    if isinstance(content, str):
        return _text(content)
    if isinstance(content, list):
        return "\n".join(_text(b.get("text")) for b in content if isinstance(b, dict)
                         and b.get("type") in ("text", "input_text", "output_text"))[:2000]
    return ""


def project(record, cli):
    if not isinstance(record, dict):
        return []
    if cli == "codex":
        if record.get("type") != "response_item":
            return []
        p = record.get("payload")
        if not isinstance(p, dict):
            return []
        kind = p.get("type")
        if kind == "message" and p.get("role") in ("user", "assistant"):
            value = _content(p.get("content"))
            return [{"kind": p["role"], "text": value}] if value else []
        if kind in ("function_call", "custom_tool_call"):
            return [{"kind": "tool_call", "tool": _text(p.get("name")),
                     "text": _summary(p.get("arguments", p.get("input")))}]
        if kind in ("function_call_output", "custom_tool_call_output"):
            return [{"kind": "tool_result", "text": _content(p.get("output"))}]
        return []
    message = record.get("message")
    if record.get("type") not in ("user", "assistant") or not isinstance(message, dict):
        return []
    role = message.get("role", record["type"])
    if role not in ("user", "assistant"):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"kind": role, "text": _text(content)}]
    out = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            out.append({"kind": role, "text": _text(block.get("text"))})
        elif kind == "tool_use":
            out.append({"kind": "tool_call", "tool": _text(block.get("name")), "text": _summary(block.get("input"))})
        elif kind == "tool_result":
            out.append({"kind": "tool_result", "text": _content(block.get("content"))})
    return out


def read(row: dict, parent_id: str, token=None):
    cursor = decode_cursor(token)
    result = {"items": [], "next_cursor": token, "reset": False, "truncated": False, "pending": False,
              "last_message": row.get("_last_message", ""), "label": row["label"]}
    try:
        path = fsguard.subagent_transcript_path(row["cli"], parent_id, row["id"], row.get("_transcript"), row.get("_parent_transcript"))
        stream = fsguard.open_subagent_file(path)
    except FileNotFoundError:
        result["pending"] = True
        return result
    with stream as f:
        st = os.fstat(f.fileno())
        prefix = f.read(min(128, st.st_size))
        generation = f"{st.st_dev:x}-{st.st_ino:x}-{hashlib.sha256(prefix).hexdigest()[:16]}"
        f.seek(0)
        if row["cli"] == "codex":
            first = f.readline(MAX_LINE + 1)
            if len(first) <= MAX_LINE and not first.endswith(b"\n"):
                result["pending"] = True
                return result
            try:
                meta_record = json.loads(first)
                meta = meta_record["payload"]
                source = meta["source"]["subagent"]["thread_spawn"]
                if (meta_record.get("type") != "session_meta" or meta.get("id") != row["id"]
                        or source.get("parent_thread_id") != parent_id):
                    raise Unsupported
                result["label"] = _text(meta.get("agent_nickname") or meta.get("agent_role"))[:200] or row["label"]
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                raise Unsupported from None
        else:
            meta_path = path.with_suffix(".meta.json")
            try:
                with fsguard.open_subagent_file(meta_path) as m:
                    raw = m.read(16385)
                    if len(raw) <= 16384:
                        meta = json.loads(raw)
                        if isinstance(meta, dict) and isinstance(meta.get("description"), str):
                            result["label"] = meta["description"][:200]
            except (OSError, ValueError, RecursionError, fsguard.FsDenied):
                pass
        offset = cursor[1] if cursor and cursor[0] == generation and cursor[1] <= st.st_size else None
        result["reset"] = cursor is not None and offset is None
        if offset is None or st.st_size - offset > MAX_BYTES:
            if offset is not None:
                result["reset"] = True
            offset = max(0, st.st_size - MAX_BYTES)
            result["truncated"] = offset > 0
        f.seek(max(0, offset - 1))
        boundary = offset == 0 or f.read(1) == b"\n"
        f.seek(offset)
        data = f.read(min(MAX_BYTES, st.st_size - offset))
        if not boundary:
            first_end = data.find(b"\n")
            if first_end < 0:
                result["next_cursor"] = _cursor(generation, offset)
                result["truncated"] = True
                return result
            offset += first_end + 1
            data = data[first_end + 1:]
        items = []
        consumed = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            line_at = offset + consumed
            consumed += len(line)
            if len(line) > MAX_LINE:
                result["truncated"] = True
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            for i, item in enumerate(project(record, row["cli"])):
                item["id"] = f"{generation}:{line_at}:{i}"
                items.append(item)
        if len(items) > MAX_ITEMS:
            result.update(truncated=True, reset=bool(cursor))
        result["items"] = items[-MAX_ITEMS:]
        result["next_cursor"] = _cursor(generation, offset + consumed)
        return result
