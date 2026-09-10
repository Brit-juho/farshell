"""스크롤백 검색 (N40) — 팔레트 `~` 모드(60-settings-palette.md §3).

새 저장소를 만들지 않는다: pty_manager.py의 세션당 scrollback 링버퍼(재접속 시
최대 256KB 복원에 쓰는 바로 그 버퍼, `PTYManager.get_scrollback`)를 그대로
grep한다. 2.1.2에서 영속 파일까지 확장될 예정(80-multihost-agents.md §3)이지만
2.1.0은 살아있는 세션의 메모리 버퍼만 본다 — 서버 재시작·세션 종료로 사라지는
데이터는 검색 대상에서도 자연히 빠진다(별도 처리 불필요).

blocking 작업 없음: scrollback은 이미 메모리에 있고(디코드+문자열 검색만),
git 서브프로세스나 디스크 I/O가 없어 asyncio.to_thread offload가 필요 없다
(routes/files.py의 파일 검색과 다른 점).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from deps import pty_mgr, session_store

router = APIRouter()

# 팔레트가 한 화면에 감당할 수 있는 상한 — 여러 세션 합쳐서.
MAX_SCROLLBACK_RESULTS = 50
# 세션 하나가 결과를 독점하지 않도록 세션당 상한도 둔다.
MAX_PER_SESSION = 20
CONTEXT_LINES = 3


def _session_display_name(session_id: str) -> str:
    info = session_store.get(session_id)
    if not info:
        return session_id
    return info.tmux_name or info.name or session_id


def _session_lines(session_id: str) -> list[str]:
    chunks = pty_mgr.get_scrollback(session_id)
    text = b"".join(chunks).decode("utf-8", errors="replace")
    # PTY 출력은 \r\n 혼용이라 두 경계 모두 줄바꿈으로 취급한다.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _search_session(session_id: str, q: str) -> list[dict]:
    lines = _session_lines(session_id)
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
    """세션들의 scrollback 링버퍼를 grep한다.

    `sessions`: "all"(기본, 전부) 또는 콤마로 구분된 session_id 목록.
    질의가 빈 문자열이면 빈 결과 — /api/fs/search와 같은 관행(빈 입력에서
    전체 버퍼를 훑지 않는다).
    """
    q = q.strip()
    if not q:
        return {"results": [], "truncated": False}

    all_ids = list(pty_mgr.sessions.keys())
    if sessions != "all":
        wanted = {s.strip() for s in sessions.split(",") if s.strip()}
        target_ids = [sid for sid in all_ids if sid in wanted]
    else:
        target_ids = all_ids

    results: list[dict] = []
    truncated = False
    for sid in target_ids:
        try:
            found = _search_session(sid, q)
        except ValueError:
            # 검색 도중 세션이 종료됐다 — 그 세션만 건너뛴다.
            continue
        results.extend(found)
        if len(results) >= MAX_SCROLLBACK_RESULTS:
            truncated = True

    if len(results) > MAX_SCROLLBACK_RESULTS:
        results = results[:MAX_SCROLLBACK_RESULTS]
        truncated = True

    return {"results": results, "truncated": truncated}
