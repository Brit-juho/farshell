"""스크롤백 검색 (N40) — 팔레트 `~` 모드(60-settings-palette.md §3).

새 저장소를 만들지 않는다: pty_manager.py의 세션당 scrollback 링버퍼(재접속 시
최대 256KB 복원에 쓰는 바로 그 버퍼, `PTYManager.get_scrollback`)를 그대로
grep한다.

2.1.2(80-multihost-agents.md §3)에서 **영속 로그까지** 넓혔다. 규칙은 하나다:
**세션 하나당 한 소스만 본다.** 그 세션에 영속 로그가 있으면 로그를(링버퍼는
그 로그의 꼬리라서 겹친다), 없으면 링버퍼를 본다. 두 곳을 다 보면 같은 줄이
두 번 나오고, 어느 쪽 줄 번호가 맞는지도 말할 수 없게 된다. 결과의 `source`
필드가 어느 쪽을 봤는지 밝힌다.

영속 로그 덕에 **이미 끝난 세션·서버 재시작 이전의 출력**도 찾힌다 — 그게
이 확장의 핵심이다(그전에는 링버퍼가 사라지면 그대로 없는 데이터였다).

디스크 I/O가 생겼으므로 로그를 읽는 부분만 `asyncio.to_thread`로 뺀다. 그리고
로그는 세션당 꼬리 MAX_LOG_SCAN_BYTES까지만 읽는다 — 20MB 로그 여러 개를 매
타자마다 통째로 읽으면 팔레트가 멈춘다. 잘렸으면 응답의 `truncated`로 알린다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

import scrollback_persist
from deps import pty_mgr, session_store

router = APIRouter()

# 팔레트가 한 화면에 감당할 수 있는 상한 — 여러 세션 합쳐서.
MAX_SCROLLBACK_RESULTS = 50
# 세션 하나가 결과를 독점하지 않도록 세션당 상한도 둔다.
MAX_PER_SESSION = 20
CONTEXT_LINES = 3
# 세션당 영속 로그를 읽는 최대 바이트(꼬리부터). 4MB면 보통 수십만 줄이다.
MAX_LOG_SCAN_BYTES = 4 * 1024 * 1024


def _session_display_name(session_id: str) -> str:
    info = session_store.get(session_id)
    if not info:
        return session_id
    return info.tmux_name or info.name or session_id


def _to_lines(text: str) -> list[str]:
    # PTY 출력은 \r\n 혼용이라 두 경계 모두 줄바꿈으로 취급한다.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _session_lines(session_id: str) -> list[str]:
    chunks = pty_mgr.get_scrollback(session_id)
    return _to_lines(b"".join(chunks).decode("utf-8", errors="replace"))


def _log_lines(session_id: str) -> tuple[list[str], bool]:
    blob, cut = scrollback_persist.read_tail(session_id, MAX_LOG_SCAN_BYTES)
    return _to_lines(blob.decode("utf-8", errors="replace")), cut


def _search_lines(session_id: str, lines: list[str], q: str, source: str) -> list[dict]:
    ql = q.lower()
    name = _session_display_name(session_id)
    out: list[dict] = []
    for i, line in enumerate(lines):
        if ql not in line.lower():
            continue
        before = lines[max(0, i - CONTEXT_LINES):i]
        after = lines[i + 1:i + 1 + CONTEXT_LINES]
        out.append({
            "session_id": session_id,
            "session_name": name,
            # 어느 소스를 봤는가 — "live"(링버퍼) 또는 "log"(영속 로그).
            "source": source,
            "line_no": i,
            "line": line,
            "context_before": before,
            "context_after": after,
        })
        if len(out) >= MAX_PER_SESSION:
            break
    return out


@router.get("/api/search/scrollback")
async def search_scrollback(q: str = Query(...), sessions: str = Query("all")):
    """세션들의 scrollback을 grep한다.

    `sessions`: "all"(기본, 전부) 또는 콤마로 구분된 session_id 목록.
    질의가 빈 문자열이면 빈 결과 — /api/fs/search와 같은 관행(빈 입력에서
    전체 버퍼를 훑지 않는다).

    대상은 **살아있는 세션 + 영속 로그가 남은 세션**이다. 후자 덕에 이미 끝난
    세션이나 서버 재시작 이전의 출력도 찾힌다(파일 상단 주석 참고).
    """
    q = q.strip()
    if not q:
        return {"results": [], "truncated": False}

    live_ids = list(pty_mgr.sessions.keys())
    logged_ids = await asyncio.to_thread(scrollback_persist.logged_session_ids)
    # 순서: 살아있는 세션 먼저 — 상한에 걸리더라도 지금 보고 있는 세션의 결과가
    # 먼저 남는 게 맞다.
    all_ids = live_ids + [sid for sid in logged_ids if sid not in set(live_ids)]
    if sessions != "all":
        wanted = {s.strip() for s in sessions.split(",") if s.strip()}
        target_ids = [sid for sid in all_ids if sid in wanted]
    else:
        target_ids = all_ids

    logged = set(logged_ids)
    results: list[dict] = []
    truncated = False
    for sid in target_ids:
        try:
            if sid in logged:
                # 로그가 있으면 로그만 본다 — 링버퍼는 그 로그의 꼬리라서 겹친다.
                lines, cut = await asyncio.to_thread(_log_lines, sid)
                truncated = truncated or cut
                found = _search_lines(sid, lines, q, "log")
            else:
                found = _search_lines(sid, _session_lines(sid), q, "live")
        except (ValueError, OSError):
            # 검색 도중 세션이 종료됐거나 로그가 지워졌다 — 그것만 건너뛴다.
            continue
        results.extend(found)
        if len(results) >= MAX_SCROLLBACK_RESULTS:
            truncated = True
            break

    if len(results) > MAX_SCROLLBACK_RESULTS:
        results = results[:MAX_SCROLLBACK_RESULTS]
        truncated = True

    return {"results": results, "truncated": truncated}
