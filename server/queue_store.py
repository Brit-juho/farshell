"""프롬프트 큐 — 에이전트가 작업 중일 때 지시를 쌓아뒀다가 순차 투입 (P4).

존재 이유는 음성 모드다. 지금은 Claude가 작업 중일 때 말해도 그냥 씹히는데,
큐가 있으면 걸어가면서 3개를 던져놓고 순서대로 실행시킬 수 있다.

설계상 지켜야 할 것 4가지:

  1. **순서 보장.** 사용자가 넣은 순서 그대로 나가야 한다. 순서가 섞이면
     "테스트 고쳐줘 → 커밋해줘"가 뒤집혀 커밋 먼저 나간다.
  2. **동시 쓰기.** 웹·CLI·음성이 동시에 add 할 수 있다. atomic replace 만으로는
     read-modify-write 사이에 lost update 가 난다 → flock 으로 직렬화한다.
  3. **묵시적 유실 금지.** 상한(50)을 넘으면 조용히 버리지 말고 거부한다.
     타깃 세션이 사라져도 큐는 남긴다 — 사용자가 넣은 지시를 임의로 지우지 않는다.
  4. **위험 명령 차단.** 투입 직전 safe_mode 를 통과해야 한다. 막히면 큐에
     남겨두고 상태만 blocked 로 바꾼다(조용히 사라지면 안 된다).

저장은 ~/.vt/queue.json. auth._write_json_secure 와 같은 규칙(0700 디렉토리 +
0600 파일 + atomic replace)을 쓴다. workspace.py 는 권한 보호가 없으므로 베끼지 않는다.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ITEMS = 50
MAX_TEXT_LEN = 8000

STATUS_PENDING = "pending"
STATUS_BLOCKED = "blocked"      # safe_mode 에 막힘 — 큐에 남는다


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "queue.json"


def _lock_path() -> Path:
    return _state_dir() / "queue.lock"


@contextmanager
def _locked():
    """파일 락. add/remove 가 겹쳐도 항목이 유실되지 않게 직렬화한다."""
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    lp = _lock_path()
    fd = os.open(str(lp), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _normalize_target(target) -> dict | None:
    """N6(60 §4): `target: {worktree?|session?}`.

    2.1.0엔 워크트리 데이터 모델이 없다(ADR-20 — N8은 2.1.1). 그래서 여기서
    실제로 채워지는 건 session 키뿐이고, worktree 키는 스키마 자리만 받아둔다
    (넘어와도 무시 — 2.1.1에서 워크트리 레일이 생기면 그쪽 키로 pane을 결정하게
    확장). 레거시 호출부(CLI `vt queue add "..." <세션>`, 기존 queue.json의
    문자열 target)는 계속 plain string으로 넘어오므로 여기서 dict로 승격한다.
    """
    if not target:
        return None
    if isinstance(target, str):
        s = target.strip()
        return {"session": s} if s else None
    if isinstance(target, dict):
        out = {}
        session = str(target.get("session") or "").strip()
        worktree = str(target.get("worktree") or "").strip()
        # A3(80 §1 3단계) — 원격 호스트 투입. host가 없으면 언제나 로컬이다.
        # **세션 이름만으로는 부족하다**: 같은 이름(`dev`)의 세션이 맥에도
        # gpu-box에도 있는 게 정상이라, host를 안 적으면 큐가 엉뚱한 기계에
        # 명령을 친다.
        host = str(target.get("host") or "").strip()
        if session:
            out["session"] = session
        if worktree:
            out["worktree"] = worktree
        if host and host != "local":
            out["host"] = host
        return out or None
    return None


def target_session(item: dict) -> str | None:
    """항목의 target에서 세션 이름을 뽑는다 — 2.1.0엔 이것만 실제로 resolve된다."""
    t = item.get("target")
    return t.get("session") if isinstance(t, dict) else None


def target_host(item: dict) -> str:
    """항목이 어느 호스트로 가야 하는가. 명시가 없으면 항상 로컬(A3).

    `"local"`은 예약 id라 원격이 이 값을 가질 수 없다(host_store.normalize_id).
    """
    t = item.get("target")
    if isinstance(t, dict):
        h = str(t.get("host") or "").strip()
        if h and h != "local":
            return h
    return "local"


def _read_unlocked() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"큐 파일 읽기 실패({e}) — 빈 큐로 시작")
        return []
    if not isinstance(data, list):
        return []
    items = [x for x in data if isinstance(x, dict) and x.get("text")]
    # 구 형식(target이 plain string) 마이그레이션 — 디스크 재기록은 다음 쓰기 때.
    for x in items:
        if isinstance(x.get("target"), str):
            x["target"] = _normalize_target(x["target"])
    return items


def _write_unlocked(items: list[dict]) -> None:
    """0600 원자적 저장 (auth._write_json_secure 와 동일 규칙)."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- 공개 API ---------------------------------------------------------------


def list_items() -> list[dict]:
    with _locked():
        return _read_unlocked()


def add(text: str, target: str | dict | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "reason": "빈 내용은 넣을 수 없습니다"}
    if len(text) > MAX_TEXT_LEN:
        return {"ok": False, "error": "too_long",
                "reason": f"내용이 너무 깁니다 (최대 {MAX_TEXT_LEN}자)"}
    with _locked():
        items = _read_unlocked()
        if len(items) >= MAX_ITEMS:
            # 조용히 버리지 않는다 — 넣은 줄 알았는데 없는 것이 최악이다.
            return {"ok": False, "error": "full",
                    "reason": f"큐가 가득 찼습니다 (최대 {MAX_ITEMS}개)"}
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "target": _normalize_target(target),
            "status": STATUS_PENDING,
            "created_at": time.time(),
        }
        items.append(item)
        _write_unlocked(items)
    return {"ok": True, "item": item, "count": len(items)}


def remove(item_id: str) -> dict:
    with _locked():
        items = _read_unlocked()
        rest = [x for x in items if x.get("id") != item_id]
        if len(rest) == len(items):
            return {"ok": False, "error": "not_found", "reason": "항목이 없습니다"}
        _write_unlocked(rest)
    return {"ok": True, "count": len(rest)}


def clear() -> dict:
    with _locked():
        n = len(_read_unlocked())
        _write_unlocked([])
    return {"ok": True, "removed": n}


def pop_next(session: str | None = None, *, session_scoped: bool = False) -> dict | None:
    """맨 앞의 pending 항목을 꺼낸다(FIFO). blocked 는 건너뛴다.

    session_scoped=True(자동 드레인, stop 훅)면 다른 세션을 타깃한 항목은
    건너뛰고 큐에 그대로 남긴다 — "멈춘 세션"과 다른 세션 몫이 잘못 흘러가지
    않게 막는다. 타깃 미지정 항목은 여전히 대상이다(기존 전역 폴백 규칙 유지).
    수동 실행(vt queue run / 웹 "지금 실행")은 session_scoped=False로 어떤
    항목이든 맨 앞부터 그대로 꺼낸다 — 사용자가 명시적으로 누른 동작이라
    타깃 매칭 없이 그 항목 자신의 타깃(_resolve_pane)으로 보내면 된다.
    """
    with _locked():
        items = _read_unlocked()
        for i, x in enumerate(items):
            if x.get("status") != STATUS_PENDING:
                continue
            if session_scoped:
                target = target_session(x)
                if target and target != session:
                    continue
            items.pop(i)
            _write_unlocked(items)
            return x
    return None


def mark_blocked(item: dict, reason: str) -> None:
    """safe_mode 에 막힌 항목을 큐에 되돌려 놓는다.

    맨 앞(index 0)에 항상 꽂으면, A 가 B보다 먼저 막혔을 때 최종 순서가
    [B, A] 로 뒤집힌다 — "제출 순서대로 처리한다"는 큐의 설계 원칙 위반이다.
    이미 blocked 상태인 마지막 항목 바로 뒤에 꽂아 blocked 항목끼리의
    상대 순서를 제출 순서로 유지한다. blocked 항목이 아직 없으면 기존과
    동일하게 맨 앞에 넣는다(뒤에 남은 pending 항목보다는 앞이어야 한다).
    """
    with _locked():
        items = _read_unlocked()
        item = {**item, "status": STATUS_BLOCKED, "blocked_reason": reason}
        insert_at = 0
        for i, x in enumerate(items):
            if x.get("status") == STATUS_BLOCKED:
                insert_at = i + 1
        items.insert(insert_at, item)
        _write_unlocked(items)


def unblock(item_id: str) -> dict:
    with _locked():
        items = _read_unlocked()
        found = False
        for x in items:
            if x.get("id") == item_id:
                x["status"] = STATUS_PENDING
                x.pop("blocked_reason", None)
                found = True
                break
        if not found:
            return {"ok": False, "error": "not_found"}
        _write_unlocked(items)
    return {"ok": True}


def pending_count() -> int:
    return sum(1 for x in list_items() if x.get("status") == STATUS_PENDING)


# --- CLI (bin/fsh 가 서버 없이 직접 호출한다 — `python -m auth` 와 같은 방식) ----


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("list", "ls"):
        items = list_items()
        print()
        if not items:
            print("  큐가 비어 있습니다")
            print()
            print("  추가: vt queue add \"테스트 고쳐줘\"")
        else:
            pend = sum(1 for x in items if x.get("status") == STATUS_PENDING)
            print(f"  📋 프롬프트 큐 — {len(items)}건 (대기 {pend})")
            print()
            for i, x in enumerate(items, 1):
                mark = "⏸" if x.get("status") == STATUS_BLOCKED else f"{i}."
                ts = target_session(x)
                target = f" [{ts}]" if ts else ""
                text = x["text"].replace("\n", " ⏎ ")
                if len(text) > 60:
                    text = text[:57] + "..."
                print(f"    {mark:<3} {x['id'][:8]}{target}  {text}")
                if x.get("blocked_reason"):
                    print(f"         └ 차단됨: {x['blocked_reason']} — 해제: vt queue unblock {x['id'][:8]}")
            print()
            print("  실행: vt queue run   삭제: vt queue rm <id>   비우기: vt queue clear")
        print()
        return 0

    if cmd == "add":
        if not rest:
            print("  ✗ 내용이 없습니다. 사용법: vt queue add \"내용\" [세션]", file=sys.stderr)
            return 2
        # 마지막 인자가 tmux 세션 이름이면 타깃으로 쓴다(vt queue add "..." dev)
        r = add(rest[0], rest[1] if len(rest) > 1 else None)
        if not r.get("ok"):
            print(f"  ✗ {r['reason']}", file=sys.stderr)
            return 1
        print(f"  ✓ 큐에 추가 ({r['count']}건) — {r['item']['id'][:8]}")
        return 0

    if cmd in ("rm", "remove", "delete"):
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        prefix = rest[0]
        match = [x for x in list_items() if x["id"].startswith(prefix)]
        if not match:
            print(f"  ✗ '{prefix}' 로 시작하는 항목이 없습니다", file=sys.stderr)
            return 1
        if len(match) > 1:
            print(f"  ✗ '{prefix}' 가 {len(match)}건과 일치합니다 — 더 길게 지정하세요", file=sys.stderr)
            return 1
        remove(match[0]["id"])
        print(f"  ✓ 삭제됨: {match[0]['text'][:40]}")
        return 0

    if cmd == "clear":
        r = clear()
        print(f"  ✓ {r['removed']}건 삭제")
        return 0

    if cmd == "unblock":
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        match = [x for x in list_items() if x["id"].startswith(rest[0])]
        if len(match) != 1:
            print(f"  ✗ '{rest[0]}' 로 특정되는 항목이 없습니다", file=sys.stderr)
            return 1
        unblock(match[0]["id"])
        print(f"  ✓ 재개됨: {match[0]['text'][:40]}")
        return 0

    if cmd == "run":
        import queue_runner
        r = queue_runner.drain_once()
        if not r.get("ok"):
            print(f"  ✗ {r.get('reason', r.get('error'))}", file=sys.stderr)
            return 1
        if r["drained"] == 0:
            print("  ⓘ 큐가 비었습니다")
            return 0
        print(f"  ✓ 투입 → {r['pane']} ({r['mode']}), 남은 {r['remaining']}건")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
