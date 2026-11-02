"""Bounded source checks, source-preserving edits and existing sync integration."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
import time
import urllib.request
import git_safe
from pathlib import Path

import agent_config as manager
import agent_config_context as contexts
import config_io
import mcp_adapters
import mcp_plans
import mcp_sources
import vt_paths
import mcp_write
from i18n import tr

MAX_CONTENT = 256 * 1024


def _toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return str(value).lower()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    raise ValueError(tr("MCP 필드의 형식이 올바르지 않습니다"))


def _patch_codex(text, name, patch):
    """Replace only complete leaf assignments; preserve all unrelated TOML text."""
    import tomllib
    before = tomllib.loads(text)
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if mcp_write._codex_header_re(name).match(line)]
    if len(starts) != 1:
        raise ValueError(tr("Codex 테이블을 안전하게 특정할 수 없습니다"))
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if mcp_write._TOML_TABLE_RE.match(lines[i])), len(lines))
    block = lines[start + 1:end]
    for key, value in patch.items():
        matches = [i for i, line in enumerate(block) if re.match(r"^\s*(?:" + re.escape(key) + r'|"' + re.escape(key) + r'"|\x27' + re.escape(key) + r"\x27)\s*=", line)]
        assignment = key + " = " + _toml_value(value) + "\n"
        if matches:
            first = matches[0]
            if len(matches) != 1:
                raise ValueError(tr("설정 파일의 형식이 잘못됐습니다"))
            last = first + 1
            while last <= len(block):
                try:
                    parsed = tomllib.loads("".join(block[first:last]))
                    if key in parsed:
                        break
                except tomllib.TOMLDecodeError:
                    last += 1
            else:
                raise ValueError(tr("MCP 필드의 형식이 올바르지 않습니다"))
            block[first:last] = [assignment]
        else:
            if block and not block[-1].endswith("\n"):
                block[-1] += "\n"
            block.append(assignment)
    updated = "".join(lines[:start + 1] + block + lines[end:])
    expected = json.loads(json.dumps(before, default=str))
    target = expected["mcp_servers"][name]
    for key, value in patch.items():
        target[key] = {**target.get(key, {}), **value} if key in ("env", "headers") else value
    after = tomllib.loads(updated)
    if json.loads(json.dumps(after, default=str)) != expected:
        raise ValueError(tr("플러그인 이외의 설정이 바뀌어 중단했습니다"))
    return updated


def editor(item_id, session, view):
    row = manager.locate(item_id, session, view)
    if row["kind"] == "skill":
        path = Path(row["source"])
        if path.stat().st_size > MAX_CONTENT or path.is_symlink() or path.parent.is_symlink():
            raise ValueError(tr("이 스킬 파일은 편집할 수 없습니다"))
        return {"kind": "skill", "content": path.read_text(), "revision": row["revision"]}
    if row["kind"] == "mcp":
        # Values in arguments, URLs, env and headers may be credentials. An edit
        # is a write-only patch; untouched fields are preserved on the server.
        with contexts.use(row["context"]):
            raw, error = mcp_sources.read_entry(row, row.get("project"))
        if error or raw is None:
            raise ValueError(tr("원본 정의를 읽지 못했습니다"))
        return {"kind": "mcp", "fields": sorted(raw), "revision": row["revision"],
                "note": tr("변경할 필드만 JSON으로 입력하세요. 생략한 필드와 기존 비밀값은 유지합니다")}
    raise ValueError(tr("플러그인은 설정 범위별 토글과 부모 묶음으로 관리합니다"))


def preview_edit(item_id, session, view, revision, content):
    row = manager.locate(item_id, session, view)
    if row["revision"] != revision:
        raise ValueError(tr("설정 파일이 바뀌었습니다. 다시 검토하세요"))
    if not isinstance(content, str) or len(content.encode()) > MAX_CONTENT or "\0" in content:
        raise ValueError(tr("편집 내용이 너무 크거나 올바르지 않습니다"))
    if row["kind"] == "skill":
        if not content.strip():
            raise ValueError(tr("빈 스킬은 저장할 수 없습니다"))
        # Recheck editor eligibility, including links, before issuing a plan.
        editor(item_id, session, view)
        after = content
    elif row["kind"] == "mcp" and row.get("editable", row.get("writable")):
        patch = json.loads(content)
        allowed = {"command", "args", "url", "type", "env", "headers", "env_vars", "bearer_token_env_var"}
        if not isinstance(patch, dict) or not patch or set(patch) - allowed:
            raise ValueError(tr("지원하지 않는 MCP 편집 필드입니다"))
        for key in ("command", "url", "type", "bearer_token_env_var"):
            if key in patch and (not isinstance(patch[key], str) or not patch[key] or len(patch[key]) > 8192):
                raise ValueError(tr("MCP 필드의 형식이 올바르지 않습니다"))
        for key in ("args", "env_vars"):
            if key in patch and (not isinstance(patch[key], list) or len(patch[key]) > 128 or any(not isinstance(x, str) or len(x) > 8192 for x in patch[key])):
                raise ValueError(tr("MCP 필드의 형식이 올바르지 않습니다"))
        for key in ("env", "headers"):
            if key in patch and (not isinstance(patch[key], dict) or len(patch[key]) > 64 or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 8192 for k, v in patch[key].items())):
                raise ValueError(tr("MCP 필드의 형식이 올바르지 않습니다"))
        with contexts.use(row["context"]):
            raw, error = mcp_sources.read_entry(row, row.get("project"))
        if error or raw is None:
            raise ValueError(tr("원본 정의를 읽지 못했습니다"))
        after = {**raw, **patch}
        # env/header edits merge individual keys, retaining unknown secrets.
        for key in ("env", "headers"):
            if key in patch:
                after[key] = {**raw.get(key, {}), **patch[key]}
        known = {k: v for k, v in after.items() if k in {"command", "args", "url", "type", "env", "headers"}}
        if row["tool"] == "codex":
            known = {k: v for k, v in after.items() if k in {"command", "args", "url", "env_vars", "bearer_token_env_var", "enabled"}}
        if mcp_adapters.validate_definition(row["tool"], known):
            raise ValueError(tr("수정 결과가 올바른 MCP 정의가 아닙니다"))
        if row["tool"] == "codex":
            if set(patch) & {"type", "headers"}:
                raise ValueError(tr("지원하지 않는 MCP 편집 필드입니다"))
            # Merged map values preserve undisclosed credentials.
            after = _patch_codex(Path(row["source"]).read_text(), row["name"],
                                 {k: after[k] for k in patch})
        elif row["tool"] != "claude":
            raise ValueError(tr("이 도구의 정의 편집은 원본 형식 보존을 위해 CLI에서 수행하세요"))
    else:
        raise ValueError(tr("이 항목은 편집할 수 없습니다"))
    return {**mcp_plans.issue("agent-config-edit", {"row": row, "content": after}),
            "source": row["source"], "kind": row["kind"], "name": row["name"],
            "fields": sorted(patch) if row["kind"] == "mcp" else [],
            "note": tr("원본 위치에 저장합니다. 내려받은 자료의 수정은 업데이트와 충돌할 수 있습니다")}


def apply_edit(token):
    plan = mcp_plans.take(token, "agent-config-edit")
    if not plan:
        raise ValueError(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
    row = plan["row"]
    path = Path(row["source"])
    with contexts.use(row["context"]), config_io.locked(path):
        if mcp_adapters.file_revision(path) != row["revision"]:
            raise ValueError(tr("설정 파일이 바뀌었습니다. 다시 검토하세요"))
        stamp = config_io.stamp(path)
        if row["kind"] == "skill":
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError(tr("이 스킬 파일은 편집할 수 없습니다"))
            updated = plan["content"]
        elif row["tool"] == "codex":
            updated = plan["content"]
        else:
            data, error = mcp_adapters._read_json(path)
            if error or not isinstance(data, dict):
                raise ValueError(tr("원본 정의를 읽지 못했습니다"))
            block = data
            if row["scope"] == "local" and not row.get("shared"):
                block = data["projects"][row["project"]]
            block["mcpServers"][row["name"]] = plan["content"]
            updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        manager._backup(path)
        config_io.ensure_unchanged(path, stamp)
        config_io.atomic_write(path, updated)
        if path.read_text() != updated:
            return {"status": "unknown", "runtime_state": "unknown"}
    return {"status": "ok", "restart_required": True, "runtime_state": "unknown"}


def _git(path, *args, strip=True):
    result = subprocess.run(git_safe.argv(*args, cwd=path), capture_output=True, text=True, timeout=3,
                            env=git_safe.env())
    if result.returncode or len(result.stdout) > MAX_CONTENT:
        return None
    return result.stdout.strip() if strip else result.stdout


def check_update(item_id, session, view):
    row = manager.locate(item_id, session, view)
    path = Path(row["source"]).parent
    # A project's own Git history is not a skill/plugin download source. Only a
    # dedicated checkout rooted at that component is eligible for remote checks.
    root = _git(path, "rev-parse", "--show-toplevel") if row["kind"] == "skill" else None
    result = {"state": "unavailable", "checked_at": time.time(),
              "reason": tr("업데이트 출처를 확인하지 못했습니다 — 설치 도구에서 확인하세요")}
    if row["kind"] == "plugin" and row["tool"] == "claude":
        with contexts.use(row["context"]):
            known_path = mcp_adapters.claude_settings_dir() / "plugins" / "known_marketplaces.json"
            known, error = mcp_adapters._read_json(known_path)
        market = (known or {}).get(row.get("marketplace"), {}) if not error else {}
        location = market.get("installLocation") if isinstance(market, dict) else None
        if isinstance(location, str):
            manifest, error = mcp_adapters._read_json(Path(location) / ".claude-plugin" / "marketplace.json")
            entries = (manifest or {}).get("plugins", [])
            entry = next((p for p in entries if isinstance(p, dict) and p.get("name") == row.get("plugin")), None) if isinstance(entries, list) else None
            if entry and isinstance(entry.get("version"), str) and row.get("version"):
                result = {"state": "current" if entry["version"] == row["version"] else "available",
                          "checked_at": time.time(), "installed": row["version"], "latest": entry["version"],
                          "evidence": "cached_marketplace", "catalog_at": known_path.stat().st_mtime,
                          "reason": tr("로컬 마켓플레이스 목록 기준입니다. 원격 최신 여부는 설치 도구에서 확인하세요")}
    if root and Path(root).resolve() == path.resolve():
        remote = _git(path, "remote", "get-url", "origin") or ""
        match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", remote)
        branch = _git(path, "symbolic-ref", "--short", "HEAD")
        revision = _git(path, "rev-parse", "HEAD")
        # `git status` may run a downloaded checkout's clean/fsmonitor helpers.
        # Compare the skill itself directly with its Git object instead.
        original = _git(path, "show", "HEAD:SKILL.md", strip=False)
        dirty = original is None or Path(row["source"]).stat().st_size > MAX_CONTENT or original != Path(row["source"]).read_text()
        if dirty:
            result.update(state="modified", reason=tr("로컬 수정이 있어 자동으로 덮어쓰지 않습니다"))
        elif match and branch and re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", branch):
            from urllib.parse import quote
            url = f"https://api.github.com/repos/{match[1]}/{match[2]}/commits/{quote(branch, safe='')}"
            # Fixed public API only. Never reuse CLI OAuth/credentials or follow
            # redirects from a downloaded repository URL.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FarShell", "Accept": "application/vnd.github+json"})
                with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as response:
                    raw = response.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise ValueError("response too large")
                sha = json.loads(raw).get("sha")
                if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha):
                    result = {"state": "current" if sha == revision else "available", "checked_at": time.time(),
                              "installed": revision, "latest": sha, "source": f"github.com/{match[1]}/{match[2]}"}
            except (OSError, ValueError):
                result["reason"] = tr("업데이트 서버를 확인하지 못했습니다")
    with manager.transaction() as data:
        data.setdefault("updates", {})[item_id] = result
    return result


def peer_sync_status():
    base = Path.home() / ".local" / "share" / "peer-sync"
    schedulers = []
    folder = Path.home() / "Library" / "LaunchAgents"
    if folder.is_dir():
        for path in sorted(folder.glob("*peer-sync*.plist"))[:8]:
            try:
                data = plistlib.loads(path.read_bytes())
                label = data.get("Label")
                args = data.get("ProgramArguments", [])
                if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", label) or not isinstance(args, list):
                    continue
                script = base / "bin" / "peer-sync"
                if str(script) not in args or not script.is_file():
                    continue
                proc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, timeout=2)
                if proc.returncode == 0:
                    schedulers.append({"id": manager._id(str(path)), "label": label})
            except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
                continue
    logs = sorted((base / "logs").glob("sync-*.log")) if (base / "logs").is_dir() else []
    conflicts = base / "state" / "mcp-conflicts"
    return {"installed": (base / "bin" / "peer-sync").is_file(), "runner": bool(schedulers),
            "schedulers": schedulers, "running": (base / "lock" / "pid").is_file(),
            "last_log_at": logs[-1].stat().st_mtime if logs else None,
            "conflict_count": sum(1 for p in conflicts.iterdir() if p.is_file()) if conflicts.is_dir() else 0,
            "note": tr("기존 peer-sync 실행 주체만 실행합니다. 로그인·캐시와 FarShell 비밀값은 복제하지 않습니다")}


def run_peer_sync(scheduler_id):
    state = peer_sync_status()
    entry = next((e for e in state["schedulers"] if e["id"] == scheduler_id), None)
    if not entry or state["running"]:
        raise ValueError(tr("이 호스트에서 동기화를 시작할 수 없습니다"))
    proc = subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{entry['label']}"], capture_output=True, timeout=3)
    if proc.returncode:
        raise ValueError(tr("동기화 실행 요청에 실패했습니다"))
    return {"status": "requested", "note": tr("실행 요청을 보냈습니다. 완료 여부는 동기화 상태에서 확인하세요")}
