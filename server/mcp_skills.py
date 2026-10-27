"""MCP 팝업의 스킬 목록과 Codex 로컬 스킬 활성화.

클라이언트는 경로를 보내지 않고 스캔에서 받은 불투명 ID만 보낸다. 프로젝트
경로는 mcp_scan.find_worktree()를 거치며 세션 경로 허용 판단은 fsguard만 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import config_io
import mcp_adapters
import mcp_scan
import mcp_write
from i18n import tr


def _id(tool: str, path: Path) -> str:
    return hashlib.sha256(f"{tool}\0{path}".encode()).hexdigest()[:24]


def _roots(tool: str, wt: dict | None) -> list[tuple[Path, str, str]]:
    home = mcp_adapters._home()
    out: list[tuple[Path, str, str]] = []
    if tool == "claude":
        out.append((mcp_adapters.claude_settings_dir() / "skills", "user", tr("직접 설치")))
        if wt:
            out.append((Path(wt["path"]) / ".claude" / "skills", "project", tr("직접 설치")))
    elif tool == "codex":
        out += [(home / ".agents" / "skills", "user", tr("직접 설치")),
                (mcp_adapters.codex_paths()["global"].parent / "skills", "user", tr("직접 설치"))]
        if wt:
            root = Path(wt["path"])
            cwd = Path(wt.get("cwd") or wt["path"])
            for directory in (cwd, *cwd.parents):
                if directory == root.parent:
                    break
                out.append((directory / ".agents" / "skills", "project", tr("직접 설치")))
                if directory == root:
                    break
    return out


def _codex_disabled() -> set[str]:
    raw, err = mcp_adapters._read_toml(mcp_adapters.codex_paths()["global"])
    if err or not raw:
        return set()
    configs = (raw.get("skills") or {}).get("config") or []
    if not isinstance(configs, list):
        return set()
    return {str(x.get("path")) for x in configs
            if isinstance(x, dict) and x.get("enabled") is False}


def scan(worktree_id: str | None = None) -> dict:
    wt = mcp_scan.find_worktree(worktree_id)
    disabled = _codex_disabled()
    skills: list[dict] = []
    errors: list[dict] = []
    for tool in ("claude", "codex"):
        for root, level, provider in _roots(tool, wt):
            try:
                children = sorted(root.iterdir(), key=lambda p: p.name.casefold()) if root.is_dir() else []
            except OSError as exc:
                errors.append({"source": str(root), "reason": exc.__class__.__name__})
                continue
            for folder in children[:500]:
                # 사용자 지정 symlink를 따라가며 허용 경계를 넓히지 않는다.
                if folder.is_symlink() or not folder.is_dir() or not (folder / "SKILL.md").is_file():
                    continue
                path = folder / "SKILL.md"
                skills.append({"id": _id(tool, path), "name": folder.name,
                               "tool": tool, "level": level, "source": str(path),
                               "provider": provider,
                               "enabled": str(path) not in disabled if tool == "codex" else None,
                               "writable": tool == "codex", "worktree_id": wt.get("id") if wt else None})
    return {"worktree": mcp_scan._public(wt), "skills": skills, "errors": errors}


# Claude Code는 스킬을 `skillOverrides`(스킬 이름 → on·name-only·user-invocable-only·off)로
# 숨긴다. 키 단위로 local > project > user 순서가 이긴다(2026-10-27 실측: 전역 off인
# 스킬을 프로젝트 settings.local.json의 "on"이 되살렸다). 플러그인 스킬은 대상이 아니다.
CLAUDE_OVERRIDE_STATES = ("on", "name-only", "user-invocable-only", "off")


def claude_override_target(project: str | None) -> Path:
    """켬·끔을 쓸 파일. 프로젝트 기준이면 개인용 local, 아니면 사용자 설정."""
    if project:
        return Path(project) / ".claude" / "settings.local.json"
    return mcp_adapters.claude_settings_dir() / "settings.json"


def _claude_overrides(path: Path) -> tuple[dict | None, str | None]:
    raw, err = mcp_adapters._read_json(path)
    if err:
        return None, err
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return None, tr("설정 파일의 형식이 잘못됐습니다")
    overrides = raw.get("skillOverrides", {})
    if not isinstance(overrides, dict):
        return None, tr("skillOverrides 형식이 잘못됐습니다")
    return overrides, None


def claude_skill_key(skill_md: Path) -> tuple[str | None, str | None]:
    """`skillOverrides`에 쓸 이름. frontmatter `name`이 폴더 이름과 다르면 어느 쪽이
    맞는지 확정할 수 없으므로 쓰지 않는다."""
    folder = skill_md.parent.name
    try:
        head = skill_md.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError as exc:
        return None, exc.__class__.__name__
    match = re.match(r"---\s*\n(.*?)\n---", head, re.S)
    if match:
        found = re.search(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", match.group(1), re.M)
        if found and found.group(1).strip() != folder:
            return None, tr("frontmatter 이름이 폴더 이름과 달라 켬·끔 대상을 확정할 수 없습니다")
    return folder, None


def claude_state(name: str, project: str | None) -> tuple[bool | None, str | None]:
    """다음 세션에서 보일지(켬). `off`만 끔이다. 읽을 수 없는 파일이 끼면 확인 불가."""
    paths = [mcp_adapters.claude_settings_dir() / "settings.json"]
    if project:
        paths += [Path(project) / ".claude" / "settings.json", Path(project) / ".claude" / "settings.local.json"]
    value = "on"
    for path in paths:
        overrides, err = _claude_overrides(path)
        if err:
            return None, err
        if name in overrides:
            value = overrides[name]
    if value not in CLAUDE_OVERRIDE_STATES:
        return None, tr("알 수 없는 skillOverrides 값입니다")
    return value != "off", None


def set_claude_enabled(path: Path, name: str, enabled: bool, *, local: bool) -> dict:
    """`skillOverrides[name]`만 바꾸고 나머지 키는 그대로 둔다.

    사용자 설정에서 켜기는 항목을 지운다(없으면 on). 프로젝트 local에서 켜기는 전역 off를
    덮어써야 하므로 "on"을 명시한다. 끄기는 어디서나 "off".
    """
    with config_io.locked(path):
        stamp = config_io.stamp(path)
        raw, err = mcp_adapters._read_json(path)
        if err:
            raise config_io.ConfigWriteRefused(err)
        data = raw if raw is not None else {}
        if not isinstance(data, dict) or not isinstance(data.get("skillOverrides", {}), dict):
            raise config_io.ConfigWriteRefused(tr("설정 파일의 형식이 잘못됐습니다"))
        overrides = dict(data.get("skillOverrides", {}))
        if not enabled:
            overrides[name] = "off"
        elif local:
            overrides[name] = "on"
        else:
            overrides.pop(name, None)
        if overrides == data.get("skillOverrides", {}):
            return {"status": "ok", "changed": False}
        updated = dict(data)
        if overrides:
            updated["skillOverrides"] = overrides
        else:
            updated.pop("skillOverrides", None)
        config_io.ensure_unchanged(path, stamp)
        config_io.atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return {"status": "ok", "changed": True}


def _find(skill_id: str, worktree_id: str | None) -> dict | None:
    return next((x for x in scan(worktree_id)["skills"] if x["id"] == skill_id), None)


def _import_target(source: dict, tool: str, scope: str, worktree_id: str | None) -> tuple[Path | None, str | None]:
    if tool not in ("claude", "codex") or scope not in ("global", "local"):
        return None, tr("이 도구와 범위에는 스킬을 가져올 수 없습니다")
    wt = mcp_scan.find_worktree(worktree_id)
    if scope == "local" and not wt:
        return None, tr("프로젝트 기준이 없습니다")
    roots = _roots(tool, wt)
    destination_root = (next((root for root, level, _ in roots if level == "user"), None)
                        if scope == "global" else
                        next((root for root, level, _ in roots if level == "project"), None))
    if destination_root is None:
        return None, tr("대상 위치를 찾지 못했습니다")
    return destination_root / source["name"], None


def _source_digest(src: Path) -> tuple[str | None, int, str | None]:
    digest = hashlib.sha256()
    count = 0
    try:
        paths = sorted(src.rglob("*"), key=lambda p: str(p.relative_to(src)))
        if len(paths) > 500:
            return None, 0, tr("스킬 파일이 너무 많습니다")
        for path in paths:
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                return None, 0, tr("심볼릭 링크나 특수 파일이 있는 스킬은 가져올 수 없습니다")
            relative = str(path.relative_to(src)).encode()
            digest.update(len(relative).to_bytes(4, "big") + relative)
            if path.is_file():
                count += 1
                if path.stat().st_size > 20 * 1024 * 1024:
                    return None, 0, tr("스킬 파일이 너무 큽니다")
                digest.update(path.read_bytes())
        return digest.hexdigest(), count, None
    except OSError as exc:
        return None, 0, exc.__class__.__name__


def preview_import(skill_id: str, tool: str, scope: str, worktree_id: str | None) -> dict:
    source = _find(skill_id, worktree_id)
    if not source:
        return {"status": "failed", "reason": tr("원본 스킬을 찾지 못했습니다")}
    destination, err = _import_target(source, tool, scope, worktree_id)
    if err:
        return {"status": "failed", "reason": err}
    if destination.exists() or destination.is_symlink():
        return {"status": "failed", "reason": tr("같은 이름이 대상에 이미 있습니다"), "conflict": True}
    digest, count, err = _source_digest(Path(source["source"]).parent)
    if err:
        return {"status": "failed", "reason": err}
    token = hashlib.sha256(json.dumps([skill_id, tool, scope, worktree_id, str(destination), digest]).encode()).hexdigest()
    return {"status": "ok", "name": source["name"], "tool": tool, "scope": scope,
            "target": str(destination), "file_count": count, "token": token,
            "apply": tr("다음 세션부터 반영됩니다")}


def _set_codex_config(text: str, path: str, enabled: bool) -> str:
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == "[[skills.config]]"]
    for start in starts:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        block = "".join(lines[start:end])
        match = re.search(r'^\s*path\s*=\s*("(?:\\.|[^"\\])*"|\'[^\']*\')', block, re.M)
        if not match:
            continue
        try:
            candidate = json.loads(match.group(1)) if match.group(1).startswith('"') else match.group(1)[1:-1]
        except ValueError:
            continue
        if candidate != path:
            continue
        for i in range(start + 1, end):
            if re.match(r"^\s*enabled\s*=", lines[i]):
                lines[i] = re.sub(r"=\s*(?:true|false)", f"= {'true' if enabled else 'false'}", lines[i], count=1)
                return "".join(lines)
        lines.insert(start + 1, f"enabled = {'true' if enabled else 'false'}\n")
        return "".join(lines)
    if enabled:
        return text  # 명시 설정이 없으면 기본값이 켜짐이다.
    prefix = "" if not text or text.endswith("\n") else "\n"
    return text + prefix + "\n[[skills.config]]\npath = " + json.dumps(path) + "\nenabled = false\n"


def set_enabled(skill_id: str, enabled: bool, worktree_id: str | None) -> dict:
    skill = _find(skill_id, worktree_id)
    if not skill:
        return {"status": "failed", "reason": tr("스킬을 찾지 못했습니다")}
    if skill["tool"] != "codex":
        return {"status": "failed", "reason": tr("이 도구의 스킬 활성화 방식은 확인되지 않았습니다")}
    config = mcp_adapters.codex_paths()["global"]
    try:
        with mcp_write._locked(config):
            stamp = mcp_write._stamp(config)
            before, err = mcp_adapters._read_toml(config)
            if err:
                raise mcp_write.WriteRefused(err)
            text = config.read_text(encoding="utf-8") if config.exists() else ""
            updated = _set_codex_config(text, skill["source"], enabled)
            if updated == text:
                return {"status": "ok", "changed": False}
            parsed, perr = mcp_adapters._read_toml_text(updated)
            if perr or parsed is None:
                raise mcp_write.WriteRefused(perr or tr("TOML 파싱 실패"))
            if stamp != mcp_write._stamp(config):
                raise mcp_write.WriteRefused(tr("설정 파일이 바뀌었습니다. 다시 시도하세요"))
            mcp_write._atomic_write(config, updated)
        state = _find(skill_id, worktree_id)
        if not state or state["enabled"] != enabled:
            raise mcp_write.WriteUnverified(tr("쓴 뒤 상태를 확인하지 못했습니다"))
        return {"status": "ok", "changed": True}
    except mcp_write.WriteRefused as exc:
        return {"status": "failed", "reason": str(exc)}
    except mcp_write.WriteUnverified as exc:
        return {"status": "unknown", "reason": str(exc)}
    except OSError as exc:
        return {"status": "failed", "reason": exc.__class__.__name__}


def import_skill(skill_id: str, tool: str, scope: str, worktree_id: str | None,
                 token: str) -> dict:
    fresh = preview_import(skill_id, tool, scope, worktree_id)
    if fresh["status"] != "ok":
        return fresh
    if not token or token != fresh["token"]:
        return {"status": "failed", "reason": tr("미리보기 뒤 스킬이 바뀌었습니다. 다시 확인하세요")}
    source = _find(skill_id, worktree_id)
    if not source:
        return {"status": "failed", "reason": tr("원본 스킬을 찾지 못했습니다")}
    destination = Path(fresh["target"])
    destination_root = destination.parent
    if destination.exists() or destination.is_symlink():
        return {"status": "failed", "reason": tr("같은 이름이 대상에 이미 있습니다"), "conflict": True}
    src = Path(source["source"]).parent
    try:
        if any(p.is_symlink() for p in src.rglob("*")):
            return {"status": "failed", "reason": tr("심볼릭 링크가 있는 스킬은 가져올 수 없습니다")}
        destination_root.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=".fsh-skill-", dir=destination_root))
        try:
            for child in src.iterdir():
                if child.is_symlink():
                    raise mcp_write.WriteRefused(tr("심볼릭 링크가 있는 스킬은 가져올 수 없습니다"))
                target = temp / child.name
                if child.is_dir():
                    shutil.copytree(child, target, symlinks=True)
                else:
                    shutil.copy2(child, target, follow_symlinks=False)
            if any(p.is_symlink() for p in temp.rglob("*")):
                raise mcp_write.WriteRefused(tr("심볼릭 링크가 있는 스킬은 가져올 수 없습니다"))
            if not (temp / "SKILL.md").is_file():
                raise mcp_write.WriteRefused(tr("복사한 스킬을 확인하지 못했습니다"))
            copied_digest, _, digest_err = _source_digest(temp)
            source_digest, _, source_err = _source_digest(src)
            if digest_err or source_err or copied_digest != source_digest:
                raise mcp_write.WriteRefused(tr("복사 중 원본 스킬이 바뀌었습니다. 다시 확인하세요"))
            with mcp_write._locked(destination):
                if destination.exists() or destination.is_symlink():
                    raise mcp_write.WriteRefused(tr("같은 이름이 대상에 이미 있습니다"))
                # 완성된 디렉터리만 노출한다. 기존 대상을 덮어쓰지 않도록
                # lock 아래에서 확인한 뒤 같은 파일시스템 안에서 이동한다.
                os.rename(temp, destination)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
        return {"status": "ok", "changed": True, "target": str(destination)}
    except (OSError, mcp_write.WriteRefused) as exc:
        return {"status": "failed", "reason": str(exc)}
