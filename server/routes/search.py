"""스크롤백 검색 (N40) — 팔레트 `~` 모드(60-settings-palette.md §3).

새 저장소를 만들지 않는다: pty_manager.py의 세션당 scrollback 링버퍼(재접속 시
최대 256KB 복원에 쓰는 바로 그 버퍼, `PTYManager.get_scrollback`)를 그대로
grep한다.

2.1.2~2.3.0 사이엔 영속 로그(디스크)까지 함께 봤지만 ADR-43(2.3.0)으로 그
기능 자체를 없앴다 — 옵트인 토글이 한 번도 켜진 적이 없어(실측 0건) "위험만
있고 값어치는 없는" 기능이었다.
그래서 **소스는 이제 하나뿐이다**: 살아있는 세션의 인메모리 링버퍼.

결과의 `source` 필드는 원격 피어(구버전 호스트)와의 응답 형태 호환을 위해
**`"live"` 고정값으로 남긴다** — 필드 자체를 없애면 옛 필드를 읽는 호출부가
깨진다.
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


def _to_lines(text: str) -> list[str]:
    # PTY 출력은 \r\n 혼용이라 두 경계 모두 줄바꿈으로 취급한다.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _session_lines(session_id: str) -> list[str]:
    chunks = pty_mgr.get_scrollback(session_id)
    return _to_lines(b"".join(chunks).decode("utf-8", errors="replace"))


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
            # ADR-43 이후 항상 "live" — 필드는 피어 호환을 위해 남긴다(모듈 docstring).
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
    """살아있는 세션들의 scrollback을 grep한다.

    `sessions`: "all"(기본, 전부) 또는 콤마로 구분된 session_id 목록.
    질의가 빈 문자열이면 빈 결과 — /api/fs/search와 같은 관행(빈 입력에서
    전체 버퍼를 훑지 않는다).

    대상은 **살아있는 세션뿐**이다(ADR-43 — 영속 로그 제거로 이미 끝난 세션·
    서버 재시작 이전의 출력은 더 이상 찾을 수 없다. 실제로 쓰인 적 없는
    휴면 능력이었다).
    """
    q = q.strip()
    if not q:
        return {"results": [], "truncated": False}

    # `peer-<host>-<screen>`은 원격에서 붙은 화면의 **거울 PTY**다(routes/peer.py).
    # 같은 tmux 세션의 출력을 한 벌 더 들고 있을 뿐이라, 검색에 넣으면 같은 줄이
    # 두 번 나오고 세션 이름 자리에 사람이 본 적 없는 내부 id가 뜬다. 실측:
    # 원격 검색을 켜자마자 결과가 통째로 자기 거울 버퍼로 채워졌다.
    live_ids = [sid for sid in pty_mgr.sessions.keys() if not sid.startswith("peer-")]
    if sessions != "all":
        wanted = {s.strip() for s in sessions.split(",") if s.strip()}
        target_ids = [sid for sid in live_ids if sid in wanted]
    else:
        target_ids = live_ids

    results: list[dict] = []
    truncated = False
    for sid in target_ids:
        try:
            found = _search_lines(sid, _session_lines(sid), q, "live")
        except (ValueError, OSError):
            # 검색 도중 세션이 종료됐다 — 그것만 건너뛴다.
            continue
        results.extend(found)
        if len(results) >= MAX_SCROLLBACK_RESULTS:
            truncated = True
            break

    if len(results) > MAX_SCROLLBACK_RESULTS:
        results = results[:MAX_SCROLLBACK_RESULTS]
        truncated = True

    return {"results": results, "truncated": truncated}
