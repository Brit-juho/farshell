"""파일 변경 감시 — 코드 뷰어 자동 갱신(`/ws-fs-watch`).

열린 파일·파일 트리의 펼친 디렉토리·소스컨트롤 탭의 저장소를 브라우저가
구독하면, 디스크에서 바뀌었을 때 "그 구독이 바뀌었다"는 사실만 알려준다.
내용은 보내지 않는다 — 브라우저가 기존 `/api/fs/*`·`/api/git/*`로 다시 읽는다.
그래서 열람 경계는 여전히 그 라우트들과 fsguard가 지킨다.

## 구독 종류
- `file` — 파일 하나. 파일 자체가 아니라 **부모 디렉토리**를 비재귀로 본다.
  편집기·에이전트는 대개 새 파일을 쓰고 rename으로 바꿔치기하므로 원래
  inode를 보면 첫 저장 뒤로 이벤트가 끊긴다.
- `dir` — 디렉토리의 바로 아래 항목(비재귀). 트리의 펼친 디렉토리용.
- `repo` — 작업 디렉토리 전체(재귀). `.git` 안은 `index`·`HEAD`·`refs/`만
  센다 — `git status` 자신이 건드리는 `index.lock`·`objects/`에 반응하면 갱신이
  갱신을 부른다. `fsguard.EXCLUDE_DIRS`(node_modules 등) 아래 변경은 버린다.

## 왜 전역 허브 하나인가
`watchfiles.awatch`는 감시 하나마다 anyio 기본 스레드 풀(40개, Starlette의 동기
라우트와 공유)에서 스레드를 하나 붙잡는다. 연결마다 감시를 따로 두면 탭 수만큼
풀이 줄어든다. 그래서 모든 연결의 구독을 합쳐 **비재귀·재귀 감시 두 개**로만
돌리고, 합집합이 바뀔 때만 다시 띄운다.

## 보안
- 구독 경로는 전부 `fsguard.resolve_under_roots`를 통과해야 한다(AGENTS.md 계약 2 —
  경로 판정은 fsguard 한 곳). 거부 목록 이름(`.env` 등)의 변경은 알리지 않는다.
- 알림에는 클라이언트가 보낸 구독 문자열만 되돌려준다. 바뀐 파일의 실제 경로는
  내보내지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import fsguard

logger = logging.getLogger(__name__)

KINDS = ("file", "dir", "repo")
# 한 연결이 동시에 구독할 수 있는 수. 트리를 많이 펼쳐도 이 정도면 충분하다.
MAX_SUBS_PER_CLIENT = 64
# 편집기의 "임시 파일 쓰기 → rename" 한 번이 여러 이벤트로 오므로 묶는다.
DEBOUNCE_MS = 300
# `.git` 안에서 상태(git status)를 바꾸는 것만.
_GIT_STATE_FILES = {"index", "HEAD"}


@dataclass(frozen=True)
class Sub:
    kind: str
    raw: str      # 클라이언트가 보낸 문자열 — 알림에 그대로 돌려준다
    path: Path    # fsguard가 해석한 절대경로

    def key(self) -> dict:
        return {"kind": self.kind, "path": self.raw}


def resolve_subs(items: Iterable) -> tuple[list[Sub], list[dict]]:
    """구독 요청을 검증한다. 동기 I/O(resolve·stat)라 호출부가 to_thread로 부른다."""
    subs: list[Sub] = []
    rejected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kind, raw = it.get("kind"), it.get("path")
        if kind not in KINDS or not isinstance(raw, str) or (kind, raw) in seen:
            continue
        seen.add((kind, raw))
        if len(subs) >= MAX_SUBS_PER_CLIENT:
            rejected.append({"kind": kind, "path": raw, "reason": "too many"})
            continue
        try:
            p = fsguard.resolve_under_roots(raw)
        except fsguard.FsDenied as e:
            rejected.append({"kind": kind, "path": raw, "reason": e.reason})
            continue
        # file은 지금 없어도 된다(rename 저장 도중일 수 있다). 부모만 있으면 본다.
        ok = p.parent.is_dir() if kind == "file" else p.is_dir()
        if not ok:
            rejected.append({"kind": kind, "path": raw, "reason": "not found"})
            continue
        if kind == "repo":
            top = _repo_top(p)
            if top is None:
                # 저장소가 아닌 곳(~/GitHub 전체 같은)을 재귀로 보면 감시 대상이
                # 끝없이 넓어진다. 소스컨트롤 탭도 거기서는 보여줄 게 없다.
                rejected.append({"kind": kind, "path": raw, "reason": "not a git repository"})
                continue
            p = top
        subs.append(Sub(kind, raw, p))
    return subs, rejected


def _repo_top(p: Path) -> Path | None:
    """p를 품는 작업 트리의 최상위(`.git`이 있는 곳). 세션 cwd가 하위 디렉토리여도
    `.git/index`를 봐야 스테이지 변화가 잡힌다. 열람 경계를 벗어나면 멈춘다 —
    경계 밖 디렉토리를 감시 대상으로 삼지 않는다."""
    roots = fsguard.get_roots()
    for cand in (p, *p.parents):
        if not any(fsguard._is_within(cand, r) for r in roots):
            return None
        if (cand / ".git").exists():
            return cand
    return None


def watch_targets(subs: Iterable[Sub]) -> tuple[frozenset[Path], frozenset[Path]]:
    """(비재귀로 볼 디렉토리, 재귀로 볼 디렉토리)."""
    flat: set[Path] = set()
    deep: set[Path] = set()
    for s in subs:
        if s.kind == "file":
            flat.add(s.path.parent)
        elif s.kind == "dir":
            flat.add(s.path)
        else:
            deep.add(s.path)
    return frozenset(flat), frozenset(deep)


def _repo_relevant(rel: tuple[str, ...]) -> bool:
    if not rel:
        return False
    if rel[0] == ".git":
        return (len(rel) == 2 and rel[1] in _GIT_STATE_FILES) or (len(rel) >= 2 and rel[1] == "refs")
    return not any(part in fsguard.EXCLUDE_DIRS for part in rel)


def match(subs: Iterable[Sub], changed: Iterable[str]) -> list[Sub]:
    """바뀐 경로 목록에 영향받는 구독만 고른다."""
    paths = []
    for raw in changed:
        cp = Path(raw)
        # 거부 목록 이름의 변경은 "바뀌었다"는 사실조차 알리지 않는다.
        if fsguard._is_denied_name(cp.name):
            continue
        paths.append(cp)
    hit: list[Sub] = []
    for s in subs:
        for cp in paths:
            if s.kind == "file":
                ok = cp == s.path
            elif s.kind == "dir":
                ok = cp.parent == s.path
            else:
                try:
                    ok = _repo_relevant(cp.relative_to(s.path).parts)
                except ValueError:
                    ok = False
            if ok:
                hit.append(s)
                break
    return hit


Send = Callable[[dict], Awaitable[None]]


class Hub:
    """모든 연결의 구독을 합쳐 감시 두 개(비재귀·재귀)로 돌린다."""

    def __init__(self):
        self._clients: dict[int, tuple[Send, list[Sub]]] = {}
        self._targets: tuple[frozenset[Path], frozenset[Path]] = (frozenset(), frozenset())
        self._tasks: list[tuple[asyncio.Task, asyncio.Event]] = []
        self._lock = asyncio.Lock()

    async def set_subs(self, client_id: int, send: Send, items) -> list[dict]:
        subs, rejected = await asyncio.to_thread(resolve_subs, items)
        async with self._lock:
            self._clients[client_id] = (send, subs)
            await self._reconcile()
        return rejected

    async def remove(self, client_id: int) -> None:
        async with self._lock:
            if self._clients.pop(client_id, None) is not None:
                await self._reconcile()

    async def _reconcile(self) -> None:
        flat: set[Path] = set()
        deep: set[Path] = set()
        for _, subs in self._clients.values():
            f, d = watch_targets(subs)
            flat |= f
            deep |= d
        # 재귀 감시가 이미 덮는 디렉토리는 비재귀로 한 번 더 볼 필요가 없다.
        flat = {p for p in flat if not any(p == r or p.is_relative_to(r) for r in deep)}
        targets = (frozenset(flat), frozenset(deep))
        if targets == self._targets:
            return
        self._targets = targets
        await self._stop()
        for paths, recursive in ((flat, False), (deep, True)):
            if paths:
                ev = asyncio.Event()
                task = asyncio.create_task(self._run(sorted(paths), recursive, ev))
                self._tasks.append((task, ev))

    async def _stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task, ev in tasks:
            ev.set()
            task.cancel()
        for task, _ in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — 끝내는 중이다
                pass

    async def _run(self, paths: list[Path], recursive: bool, stop: asyncio.Event) -> None:
        import watchfiles

        # 구독 사이에 디렉토리가 지워졌을 수 있다 — 없는 경로가 하나라도 있으면
        # awatch가 시작부터 FileNotFoundError를 던진다.
        live = [str(p) for p in await asyncio.to_thread(lambda: [p for p in paths if p.is_dir()])]
        if not live:
            return
        try:
            async for changes in watchfiles.awatch(
                *live, recursive=recursive, stop_event=stop, debounce=DEBOUNCE_MS,
                watch_filter=None, ignore_permission_denied=True,
            ):
                await self._dispatch([p for _, p in changes])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 감시 실패는 자동 갱신만 잃는다(수동 새로고침은 남는다)
            logger.warning(f"파일 감시 실패({'재귀' if recursive else '비재귀'}): {e}")
            await self._broadcast({"type": "error", "reason": "watch failed"})

    async def _dispatch(self, changed: list[str]) -> None:
        for send, subs in list(self._clients.values()):
            hit = match(subs, changed)
            if hit:
                await _safe_send(send, {"type": "changed", "subs": [s.key() for s in hit]})

    async def _broadcast(self, msg: dict) -> None:
        for send, _ in list(self._clients.values()):
            await _safe_send(send, msg)


async def _safe_send(send: Send, msg: dict) -> None:
    try:
        await send(msg)
    except Exception:  # noqa: BLE001 — 끊긴 연결은 수신 루프가 정리한다
        pass


hub = Hub()
