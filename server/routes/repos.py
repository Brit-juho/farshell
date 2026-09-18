"""저장소 API — 2.1 D1 "저장소 1급화" 1단계.

`GET /api/repos`는 `worktree.list_worktrees()`가 내놓는 평평한 워크트리
목록을 저장소 단위로 묶어 낸다. **`GET /api/worktrees`는 그대로 둔다** —
레일은 아직 그 평평한 모양을 쓰고(4단계에서 탭=저장소로 바뀔 때 갈아탄다),
이 라우트는 그 전에 저장소를 1급 개체로 다룰 수 있는 화면(설정 시트, 나중의
탭 구현)이 붙을 자리를 미리 만들어 둔 것이다.

숨김 판정은 `/api/worktrees`와 같은 `repo_store`를 그대로 쓴다 — 두 라우트가
서로 다른 숨김 목록을 보게 되는 것(더블 소스)을 피하기 위함이다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

import fsguard
import repo_store
import worktree

router = APIRouter()


def _group_by_repo(items: list[dict]) -> list[dict]:
    """평평한 워크트리 목록 → 저장소별로 묶은 목록.

    저장소의 이름·원격·host는 메인 워크트리 항목에서만 가져온다(그게 저장소
    자체를 대표하는 행이다) — `list_worktrees()`가 정렬해 준 순서를 그대로
    따르므로 반환 순서도 안정적이다.
    """
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    mains: dict[str, dict] = {}
    for w in items:
        repo = w.get("repo")
        if not repo:
            continue
        if repo not in groups:
            groups[repo] = []
            order.append(repo)
        groups[repo].append(w)
        if w.get("isMain"):
            mains[repo] = w

    out: list[dict] = []
    for repo in order:
        wts = groups[repo]
        main = mains.get(repo) or wts[0]
        host = main.get("host") or repo_store.DEFAULT_HOST
        out.append({
            "id": repo_store.repo_id(host, repo),
            "host": host,
            "path": repo,
            "name": main.get("repoName") or repo,
            "remote": main.get("remote"),
            "worktrees": wts,
        })
    return out


@router.get("/api/repos")
async def list_repos(include_hidden: int = 0):
    items, hidden = await asyncio.gather(
        asyncio.to_thread(worktree.list_worktrees),
        asyncio.to_thread(repo_store.hidden_set),
    )
    grouped = _group_by_repo(items)
    for r in grouped:
        r["hidden"] = repo_store.is_hidden({"path": r["path"]}, hidden)

    hidden_count = sum(1 for r in grouped if r["hidden"])
    if not include_hidden:
        grouped = [r for r in grouped if not r["hidden"]]

    return {
        "repos": grouped,
        "truncated": worktree.last_scan_truncated(),
        "hiddenCount": hidden_count,
        "roots": [str(p) for p in fsguard.get_roots()],
    }
