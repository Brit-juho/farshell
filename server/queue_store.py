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

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import store_lock
from i18n import tr
import vt_paths

logger = logging.getLogger(__name__)

MAX_ITEMS = 50
MAX_TEXT_LEN = 8000

STATUS_PENDING = "pending"
STATUS_BLOCKED = "blocked"      # safe_mode 에 막힘 — 큐에 남는다
# 투입 중 — 드레인이 집어 들었지만 아직 결과가 안 난 항목. 예전엔 꺼내서(pop) 파일에서
# 지운 뒤 보냈으므로, 그 사이 삭제 요청은 404가 나고 실패하면 새 dict로 다시 꽂혔다.
# 이제 자리에 남겨 두고 표시만 바꾼다 — 이 상태의 항목은 삭제·비우기를 거부한다
# (규칙: 나가는 중인 지시를 사용자가 지웠다고 믿게 만들면 안 된다).
STATUS_SENDING = "sending"

# 재시도 — 일시적인 이유(승인 대기·사람 입력 중·세션 일시 부재·tmux 실패)로 막힌
# 항목은 sweep()이 백오프 간격으로 pending에 되돌린다. MAX_ATTEMPTS번 막히면 더는
# 자동으로 풀지 않고 사람에게 알린다(조용히 무한 재시도하지 않는다).
RETRY_BASE_SEC = 5
RETRY_MAX_SEC = 300
MAX_ATTEMPTS = 5
# sending이 이보다 오래 남아 있으면 드레인이 도중에 죽은 것이다. 이미 나갔을 수도
# 있으므로 자동 재전송하지 않고 blocked(재시도 없음)로 돌려 사람이 판단하게 한다.
SENDING_STALE_SEC = 30

TRANSITIONS_MAX_BYTES = 1_000_000


def _state_dir() -> Path:
    # P2-4(ADR-41) — 예전엔 `Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()`.
    # `VT_STATE_DIR=""`은 폴백이 안 걸려 서버 CWD가 됐고 공백 패딩은 안 잘렸다. 단일 해석기로 교체한다.
    return vt_paths.state_dir()


def _path() -> Path:
    return _state_dir() / "queue.json"


def _lock_path() -> Path:
    return _state_dir() / "queue.lock"


def _transitions_path() -> Path:
    return _state_dir() / "queue_transitions.jsonl"


def _log_transition(item: dict, frm: str | None, to: str, reason: str = "",
                    actor: str = "") -> None:
    """상태 전이를 한 줄씩 덧붙인다.

    큐 락 안에서 불린다 — 같은 락이 순서를 보장한다. 기록 실패가 큐 동작을 막으면
    안 되므로 예외는 경고로만 남긴다. 상한을 넘으면 `.1` 하나만 남기고 돌린다.
    """
    entry = {"ts": time.time(), "id": item.get("id"), "from": frm, "to": to}
    if reason:
        entry["reason"] = reason[:200]
    if actor:
        entry["actor"] = actor
    p = _transitions_path()
    try:
        if p.is_file() and p.stat().st_size > TRANSITIONS_MAX_BYTES:
            os.replace(str(p), str(p.with_name(p.name + ".1")))
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"큐 전이 기록 실패: {e}")


def transitions(item_id: str | None = None, limit: int = 50) -> list[dict]:
    """최근 전이 기록. item_id는 접두어로 맞춘다(CLI가 8자 id를 쓴다)."""
    out: list[dict] = []
    p = _transitions_path()
    for path in (p.with_name(p.name + ".1"), p):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if item_id and not str(e.get("id", "")).startswith(item_id):
                        continue
                    out.append(e)
        except OSError:
            continue
    return out[-limit:] if limit > 0 else out


@contextmanager
def _locked():
    """파일 락. add/remove 가 겹쳐도 항목이 유실되지 않게 직렬화한다."""
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(_lock_path()):
        yield


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


def blocked_text(reason: str) -> str:
    """막힘 사유 코드(권한 재평가가 남기는 안정 코드)의 사람용 문구. 코드가 아니면 빈 문자열(원문 사유를 그대로 쓴다)."""
    if reason == "grant_revoked":
        return tr("보내는 쪽의 권한이 회수되어 입력하지 않았습니다")
    if reason == "delegation_closed":
        return tr("위임이 닫혀 입력하지 않았습니다")
    if reason == "relationship_gone":
        return tr("위임 관계가 사라져 입력하지 않았습니다")
    if reason == "authz_unavailable":
        return tr("권한 엔진을 쓸 수 없어 입력하지 않았습니다 — 잠시 뒤 다시 시도합니다")
    return ""


def _stored_reason_text(reason: str) -> str:
    """파일에 한국어 원문으로 저장된 고정 사유의 현재 언어 문구. 저장 시점의 언어를 박지 않으려고 읽을 때 번역한다
    (한국어에서는 원문과 같다). 해당하지 않으면 빈 문자열."""
    if reason == "타깃이 승인 대기 중":
        return tr("타깃이 승인 대기 중")
    if reason == "원격 항목에 세션이 없음":
        return tr("원격 항목에 세션이 없음")
    if reason == "원격 전송 실패":
        return tr("원격 전송 실패")
    if reason == "타깃 tmux 세션 없음":
        return tr("타깃 tmux 세션 없음")
    if reason == "사용자가 입력하는 중":
        return tr("사용자가 입력하는 중")
    if reason == "tmux 전송 실패":
        return tr("tmux 전송 실패")
    if reason == "서버 재시작으로 전송이 중단됨 — 이미 들어갔는지 확인 후 해제하세요":
        return tr("서버 재시작으로 전송이 중단됨 — 이미 들어갔는지 확인 후 해제하세요")
    if reason == "전송이 끝나지 않았습니다 — 들어갔는지 확인 후 해제하세요":
        return tr("전송이 끝나지 않았습니다 — 들어갔는지 확인 후 해제하세요")
    return ""


def display_reason(reason: str) -> str:
    """막힘 사유를 사람에게 보일 문구로 — 안정 코드 → 고정 사유 → 원문 그대로."""
    return blocked_text(reason) or _stored_reason_text(reason) or reason


def with_blocked_text(item: dict) -> dict:
    """막힘 코드가 있는 항목이면 `blocked_text` 를 더한 복사본, 아니면 그대로. 저장된 항목은 바꾸지 않는다."""
    reason = str(item.get("blocked_reason") or "")
    t = blocked_text(reason) or _stored_reason_text(reason)
    return {**item, "blocked_text": t} if t and t != reason else item


def list_items() -> list[dict]:
    with _locked():
        return _read_unlocked()


def add(text: str, target: str | dict | None = None, source: dict | None = None,
        preface: str | None = None) -> dict:
    """`source`는 항목을 만든 쪽이 결과를 되돌려 받을 표식이다(예: 에이전트 메시지
    id, agent_msg.py). 큐 자신은 뜻을 보지 않고 그대로 싣는다."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "reason": tr("빈 내용은 넣을 수 없습니다")}
    if len(text) > MAX_TEXT_LEN:
        return {"ok": False, "error": "too_long",
                "reason": tr("내용이 너무 깁니다 (최대 {max}자)", max=MAX_TEXT_LEN)}
    with _locked():
        items = _read_unlocked()
        if len(items) >= MAX_ITEMS:
            # 조용히 버리지 않는다 — 넣은 줄 알았는데 없는 것이 최악이다.
            return {"ok": False, "error": "full",
                    "reason": tr("큐가 가득 찼습니다 (최대 {max}개)", max=MAX_ITEMS)}
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "target": _normalize_target(target),
            "status": STATUS_PENDING,
            "created_at": time.time(),
        }
        if source:
            item["source"] = source
        if preface:
            # 투입 때 붙여넣기 앞에 타이핑하는 한 줄(tmux_target.send_to_tmux). 받는 에이전트가
            # 따라야 하는 지시(위임)에만 쓴다.
            item["preface"] = " ".join(str(preface).split())[:300]
        items.append(item)
        _write_unlocked(items)
        _log_transition(item, None, STATUS_PENDING, actor="add")
    return {"ok": True, "item": item, "count": len(items)}


def remove(item_id: str) -> dict:
    with _locked():
        items = _read_unlocked()
        target = next((x for x in items if x.get("id") == item_id), None)
        if target is None:
            return {"ok": False, "error": "not_found", "reason": tr("항목이 없습니다")}
        if target.get("status") == STATUS_SENDING:
            return {"ok": False, "error": "item_sending",
                    "reason": tr("지금 전송 중인 항목이라 삭제할 수 없습니다")}
        rest = [x for x in items if x.get("id") != item_id]
        _write_unlocked(rest)
        _log_transition(target, target.get("status"), "removed", actor="remove")
    return {"ok": True, "count": len(rest)}


def clear() -> dict:
    """전송 중 항목은 남긴다 — 나머지만 비운다(remove와 같은 보호 규칙)."""
    with _locked():
        items = _read_unlocked()
        keep = [x for x in items if x.get("status") == STATUS_SENDING]
        for x in items:
            if x.get("status") != STATUS_SENDING:
                _log_transition(x, x.get("status"), "removed", actor="clear")
        _write_unlocked(keep)
    return {"ok": True, "removed": len(items) - len(keep), "kept_sending": len(keep)}


def claim_next(session: str | None = None, *, session_scoped: bool = False,
               targeted_only: bool = False) -> dict | None:
    """맨 앞의 pending 항목을 sending으로 표시하고 돌려준다(FIFO). blocked는 건너뛴다.

    항목은 파일에 그대로 남는다 — 결과는 complete()/mark_blocked()가 정한다.
    같은 flock 안에서 표시하므로 서버와 CLI가 동시에 드레인해도 한 항목을 두 번
    집지 않는다.

    session_scoped=True(자동 드레인, stop 훅)면 다른 세션을 타깃한 항목은
    건너뛰고 큐에 그대로 남긴다 — "멈춘 세션"과 다른 세션 몫이 잘못 흘러가지
    않게 막는다. 타깃 미지정 항목은 여전히 대상이다(기존 전역 폴백 규칙 유지).
    수동 실행(vt queue run / 웹 "지금 실행")은 session_scoped=False로 어떤
    항목이든 맨 앞부터 그대로 꺼낸다 — 사용자가 명시적으로 누른 동작이라
    타깃 매칭 없이 그 항목 자신의 타깃(_resolve_pane)으로 보내면 된다.
    """
    with _locked():
        items = _read_unlocked()
        for x in items:
            if x.get("status") != STATUS_PENDING:
                continue
            if session_scoped:
                target = target_session(x)
                if target and target != session:
                    continue
                # targeted_only — "이 세션을 **지목한** 항목만". 세션이 막 열렸거나
                # 메시지가 도착해 흘려보낼 때 쓴다: 타깃 미지정 항목은 "먼저 끝난
                # 에이전트"에게 가는 게 규칙인데, 에이전트를 새로 띄우거나 메시지를
                # 받았다는 이유로 그걸 가져가면 안 된다.
                if targeted_only and not target:
                    continue
            x["status"] = STATUS_SENDING
            x["sending_at"] = time.time()
            _write_unlocked(items)
            _log_transition(x, STATUS_PENDING, STATUS_SENDING, actor="drain")
            return dict(x)
    return None


def complete(item: dict) -> None:
    """투입 성공 — 큐에서 지운다."""
    with _locked():
        items = _read_unlocked()
        rest = [x for x in items if x.get("id") != item.get("id")]
        if len(rest) != len(items):
            _write_unlocked(rest)
        _log_transition(item, STATUS_SENDING, "sent", actor="drain")


def _retry_delay(attempts: int) -> float:
    return min(RETRY_MAX_SEC, RETRY_BASE_SEC * (2 ** max(0, attempts - 1)))


def mark_blocked(item: dict, reason: str, *, retriable: bool = False) -> dict:
    """투입하지 못한 항목을 제자리에서 blocked로 바꾼다.

    claim_next가 항목을 자리에 남겨 두므로 순서는 그대로다(예전엔 꺼냈다가 다시
    꽂느라 blocked끼리의 순서를 따로 맞춰야 했다). 그 사이 사라졌으면(옛 형식
    호출 등) 다른 blocked 항목 바로 뒤에 다시 넣는다 — 지시를 조용히 잃지 않는다.

    retriable=True면 sweep()이 백오프 뒤 pending으로 되돌린다. 시도 횟수가
    MAX_ATTEMPTS에 닿으면 재시도를 멈추고 `exhausted`를 단다. 반환값은 갱신된 항목.
    """
    with _locked():
        items = _read_unlocked()
        cur = next((x for x in items if x.get("id") == item.get("id")), None)
        if cur is None:
            cur = {**item}
            insert_at = 0
            for i, x in enumerate(items):
                if x.get("status") == STATUS_BLOCKED:
                    insert_at = i + 1
            items.insert(insert_at, cur)
        frm = cur.get("status") or STATUS_SENDING
        cur["status"] = STATUS_BLOCKED
        cur["blocked_reason"] = reason
        cur.pop("sending_at", None)
        cur.pop("retry_at", None)
        cur.pop("exhausted", None)
        if retriable:
            attempts = int(cur.get("attempts") or 0) + 1
            cur["attempts"] = attempts
            if attempts >= MAX_ATTEMPTS:
                cur["exhausted"] = True
            else:
                cur["retry_at"] = time.time() + _retry_delay(attempts)
        _write_unlocked(items)
        _log_transition(cur, frm, STATUS_BLOCKED, reason, actor="drain")
        return dict(cur)


def unblock(item_id: str) -> dict:
    """사람이 직접 푼다 — 재시도 횟수도 처음부터."""
    with _locked():
        items = _read_unlocked()
        found = None
        for x in items:
            if x.get("id") == item_id:
                found = x
                break
        if found is None:
            return {"ok": False, "error": "not_found"}
        if found.get("status") == STATUS_SENDING:
            return {"ok": False, "error": "item_sending",
                    "reason": tr("지금 전송 중인 항목입니다")}
        frm = found.get("status")
        found["status"] = STATUS_PENDING
        for k in ("blocked_reason", "attempts", "retry_at", "exhausted"):
            found.pop(k, None)
        _write_unlocked(items)
        _log_transition(found, frm, STATUS_PENDING, actor="unblock")
    return {"ok": True}


def recover_interrupted() -> int:
    """서버 기동 시 1회 — 지난 프로세스가 투입 도중 죽어 sending으로 남은 항목.

    이미 나갔을 수도 있으므로 다시 보내지 않는다(같은 지시가 두 번 실행되는 것이
    한 번 빠지는 것보다 나쁘다). 재시도 없는 blocked로 돌려 사람이 판단한다.
    """
    n = 0
    with _locked():
        items = _read_unlocked()
        for x in items:
            if x.get("status") == STATUS_SENDING:
                x["status"] = STATUS_BLOCKED
                x["blocked_reason"] = "서버 재시작으로 전송이 중단됨 — 이미 들어갔는지 확인 후 해제하세요"
                x.pop("sending_at", None)
                _log_transition(x, STATUS_SENDING, STATUS_BLOCKED, x["blocked_reason"], actor="recover")
                n += 1
        if n:
            _write_unlocked(items)
    return n


def sweep(now: float | None = None) -> dict:
    """주기 점검(멈춤 점검).

    1. 너무 오래 sending인 항목 → 재시도 없는 blocked(recover_interrupted와 같은 이유).
    2. 재시도 시각이 지난 retriable blocked → pending.
    반환: {"stale": [...], "retried": [...]} — 호출부가 알림·드레인 예약에 쓴다.
    """
    now = time.time() if now is None else now
    stale: list[dict] = []
    retried: list[dict] = []
    with _locked():
        items = _read_unlocked()
        for x in items:
            st = x.get("status")
            if st == STATUS_SENDING and now - float(x.get("sending_at") or 0) > SENDING_STALE_SEC:
                x["status"] = STATUS_BLOCKED
                x["blocked_reason"] = "전송이 끝나지 않았습니다 — 들어갔는지 확인 후 해제하세요"
                x.pop("sending_at", None)
                _log_transition(x, STATUS_SENDING, STATUS_BLOCKED, x["blocked_reason"], actor="sweep")
                stale.append(dict(x))
            elif (st == STATUS_BLOCKED and x.get("retry_at")
                  and now >= float(x["retry_at"])):
                x["status"] = STATUS_PENDING
                x.pop("retry_at", None)
                _log_transition(x, STATUS_BLOCKED, STATUS_PENDING,
                                x.get("blocked_reason", ""), actor="sweep")
                x.pop("blocked_reason", None)
                retried.append(dict(x))
        if stale or retried:
            _write_unlocked(items)
    return {"stale": stale, "retried": retried}


def sources() -> list[dict]:
    """대기·전송 중·막힌 항목의 source 목록 — 위임 재전송이 중복을 피할 때 쓴다."""
    return [x["source"] for x in list_items() if isinstance(x.get("source"), dict)]


def replace_pending_by_source(match, text: str, source: dict) -> bool:
    """source가 match에 맞는 pending 항목이 있으면 내용을 바꾼다(위임 결과 합치기). 바꿨으면 True.

    전송 중·막힌 항목은 건드리지 않는다 — 이미 나가는 중이거나 사람이 볼 대상이다."""
    text = (text or "").strip()
    if not text or len(text) > MAX_TEXT_LEN:
        return False
    with _locked():
        items = _read_unlocked()
        for x in items:
            src = x.get("source")
            if x.get("status") == STATUS_PENDING and isinstance(src, dict) and match(src):
                x["text"] = text
                x["source"] = source
                _write_unlocked(items)
                _log_transition(x, STATUS_PENDING, STATUS_PENDING, "합침", actor="replace")
                return True
    return False


def remove_pending_by_source(match) -> int:
    """source가 match에 맞는 pending·blocked 항목을 지운다(위임 닫기). 전송 중은 남긴다."""
    with _locked():
        items = _read_unlocked()
        keep, n = [], 0
        for x in items:
            src = x.get("source")
            if (x.get("status") in (STATUS_PENDING, STATUS_BLOCKED) and isinstance(src, dict) and match(src)):
                _log_transition(x, x.get("status"), "removed", actor="delegation_close")
                n += 1
                continue
            keep.append(x)
        if n:
            _write_unlocked(keep)
    return n


def pending_count() -> int:
    return sum(1 for x in list_items() if x.get("status") == STATUS_PENDING)


# --- CLI (bin/fsh 가 서버 없이 직접 호출한다 — `python -m auth` 와 같은 방식) ----


def _run_via_server() -> dict | None:
    """서버의 `/api/queue/run`을 부른다. 서버에 닿지 못하면 None."""
    try:
        import mcp_farshell
    except ImportError:
        return None
    status, data = mcp_farshell._api("POST", "/api/queue/run")
    if status == 0:
        return None
    if status == 401:
        return {"ok": False, "reason": tr("서버 인증 실패 — 'fsh doctor'로 토큰을 확인하세요")}
    return data


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("list", "ls"):
        items = list_items()
        print()
        if not items:
            print("  " + tr("큐가 비어 있습니다"))
            print()
            print("  " + tr("추가: vt queue add \"테스트 고쳐줘\""))
        else:
            pend = sum(1 for x in items if x.get("status") == STATUS_PENDING)
            print("  📋 " + tr("프롬프트 큐 — {total}건 (대기 {pending})", n=len(items), total=len(items), pending=pend))
            print()
            for i, x in enumerate(items, 1):
                st = x.get("status")
                mark = "⏸" if st == STATUS_BLOCKED else ("→" if st == STATUS_SENDING else f"{i}.")
                ts = target_session(x)
                target = f" [{ts}]" if ts else ""
                text = x["text"].replace("\n", " ⏎ ")
                if len(text) > 60:
                    text = text[:57] + "..."
                print(f"    {mark:<3} {x['id'][:8]}{target}  {text}")
                if x.get("blocked_reason"):
                    if x.get("retry_at"):
                        left = max(0, int(float(x["retry_at"]) - time.time()))
                        tail = tr("{sec}초 뒤 재시도 ({tries}/{max})", sec=left, tries=x.get('attempts', 0), max=MAX_ATTEMPTS)
                    else:
                        tail = tr("해제: vt queue unblock {id}", id=x['id'][:8])
                    print("         └ " + tr("차단됨: {reason} — {tail}", reason=display_reason(x['blocked_reason']), tail=tail))
            print()
            print("  " + tr("실행: vt queue run   삭제: vt queue rm <id>   비우기: vt queue clear   기록: vt queue log [id]"))
        print()
        return 0

    if cmd == "add":
        if not rest:
            print("  ✗ " + tr("내용이 없습니다. 사용법: vt queue add \"내용\" [세션]"), file=sys.stderr)
            return 2
        # 마지막 인자가 tmux 세션 이름이면 타깃으로 쓴다(vt queue add "..." dev)
        r = add(rest[0], rest[1] if len(rest) > 1 else None)
        if not r.get("ok"):
            print(f"  ✗ {r['reason']}", file=sys.stderr)
            return 1
        print("  ✓ " + tr("큐에 추가 ({count}건) — {id}", n=r['count'], count=r['count'], id=r['item']['id'][:8]))
        return 0

    if cmd in ("rm", "remove", "delete"):
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        prefix = rest[0]
        match = [x for x in list_items() if x["id"].startswith(prefix)]
        if not match:
            print("  ✗ " + tr("'{prefix}' 로 시작하는 항목이 없습니다", prefix=prefix), file=sys.stderr)
            return 1
        if len(match) > 1:
            print("  ✗ " + tr("'{prefix}' 가 {count}건과 일치합니다 — 더 길게 지정하세요", n=len(match), prefix=prefix, count=len(match)), file=sys.stderr)
            return 1
        r = remove(match[0]["id"])
        if not r.get("ok"):
            print(f"  ✗ {r.get('reason', r.get('error'))}", file=sys.stderr)
            return 1
        print("  ✓ " + tr("삭제됨: {text}", text=match[0]['text'][:40]))
        return 0

    if cmd == "clear":
        r = clear()
        kept = tr(" (전송 중 {count}건은 남김)", n=r['kept_sending'], count=r['kept_sending']) if r.get("kept_sending") else ""
        print("  ✓ " + tr("{count}건 삭제", n=r['removed'], count=r['removed']) + kept)
        return 0

    if cmd == "unblock":
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        match = [x for x in list_items() if x["id"].startswith(rest[0])]
        if len(match) != 1:
            print("  ✗ " + tr("'{prefix}' 로 특정되는 항목이 없습니다", prefix=rest[0]), file=sys.stderr)
            return 1
        r = unblock(match[0]["id"])
        if not r.get("ok"):
            print(f"  ✗ {r.get('reason', r.get('error'))}", file=sys.stderr)
            return 1
        print("  ✓ " + tr("재개됨: {text}", text=match[0]['text'][:40]))
        return 0

    if cmd == "log":
        rows = transitions(rest[0] if rest else None, limit=50)
        if not rows:
            print("  " + tr("기록이 없습니다"))
            return 0
        for e in rows:
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(e.get("ts", 0)))
            why = f"  {e['reason']}" if e.get("reason") else ""
            print(f"  {ts}  {str(e.get('id'))[:8]}  {e.get('from') or '-'} → {e.get('to')}"
                  f"  [{e.get('actor', '')}]{why}")
        return 0

    if cmd == "run":
        # 서버가 떠 있으면 서버에 맡긴다 — pane 입력 락과 사람 입력 확인은 서버
        # 프로세스 안에만 있다. 서버가 없을 때만 여기서 직접 투입한다.
        r = _run_via_server()
        if r is None:
            import queue_runner
            r = queue_runner.drain_once()
        if not r.get("ok"):
            print(f"  ✗ {r.get('reason', r.get('error'))}", file=sys.stderr)
            return 1
        if r["drained"] == 0:
            print("  ⓘ " + tr("큐가 비었습니다"))
            return 0
        print("  ✓ " + tr("투입 → {pane} ({mode}), 남은 {count}건", n=r['remaining'], pane=r['pane'], mode=r['mode'], count=r['remaining']))
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
