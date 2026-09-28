"""에이전트 간 메시지 (2026-09-28, 1차) — 한 에이전트가 다른 세션(같은 맥 또는
페어링된 다른 맥)의 에이전트에게 말을 건다.

## 왜 프롬프트 큐 위에 얹는가
받는 쪽 에이전트에게 메시지를 "보여주는" 방법은 결국 그 pane에 입력하는 것뿐이다.
그런데 아무 때나 치면 두 가지 사고가 난다: 작업 중인 에이전트의 입력 한가운데에
끼어들거나, 승인 프롬프트가 떠 있으면 메시지가 **승인 답변으로 소비된다.** 큐
(`queue_runner.py`)가 이미 그 둘을 막는 관문(stop 훅 뒤에만·waiting 금지·safe_mode·
한 번에 한 건)을 갖고 있으므로, 메시지는 큐 항목이 된다. 전달 시점을 여기서 새로
판단하지 않는다.

한 가지만 보탠다: 받는 세션이 **이미 쉬고 있으면** stop 훅이 다시 올 일이 없어
메시지가 영영 안 간다. 그래서 상태를 **확실히 아는** 경우(idle/done 엔트리가
있다)에만 유예 뒤 바로 흘려보낸다. 모르는 경우(훅 없는 에이전트, 오래돼 지워진
엔트리)는 추측하지 않고 큐에 남긴다 — `fsh queue run`/웹 "지금 실행"이 수동 경로다.

## 안전장치 — 전부 받는 쪽이 건다
- 다른 맥에서 온 메시지는 peer 서명 + control 등급 + **별도 스위치**
  (`fsh host allow-agent`)가 있어야 받는다(routes/peer.py). 에이전트에게 일을
  시키는 것은 원격 명령 실행과 같다 — control이 켜져 있다고 저절로 열리면 안 된다.
- 대화 하나당 hop 상한(`MAX_HOPS`) — 두 에이전트가 서로 끝없이 답장하는 핑퐁을
  끊는다. 보낸 쪽이 hop을 속여도 받는 쪽이 대화 기록으로 다시 센다.
- 보낸 호스트별 분당 상한(`RATE_PER_MIN`).
- 받는 에이전트에게 입력되는 글은 "다른 에이전트가 보낸 것"임을 머리글로 밝힌다.
  사람의 지시와 구분할 근거다. 권한 경계는 그대로다 — 받는 쪽의 승인 프롬프트는
  여전히 사람이 답한다.

저장: `~/.vt/agent_messages.json` (0600 + flock, 최근 `MAX_KEEP`건).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import store_lock
import vt_paths

logger = logging.getLogger(__name__)

MAX_TEXT_LEN = 4000
MAX_HOPS = 10
RATE_PER_MIN = 20
MAX_KEEP = 500

STATUS_QUEUED = "queued"        # 받는 쪽 큐에 들어감(아직 입력 전)
STATUS_DELIVERED = "delivered"  # 받는 에이전트의 pane에 입력됨
STATUS_BLOCKED = "blocked"      # 큐 관문(safe_mode·waiting·세션 없음)에 막힘
STATUS_SENT = "sent"            # 보낸 쪽 기록 — 상대가 받아 큐에 넣었다
STATUS_FAILED = "failed"        # 보낸 쪽 기록 — 상대가 거절했거나 닿지 않았다

# tmux 세션 이름과 같은 규칙(routes/peer.py의 세션 이름 규칙). `호스트/세션`의
# 세션 부분이 이 모양이 아니면 받지 않는다.
SESSION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
ID_RE = re.compile(r"^[a-f0-9]{12}$")
# 호스트 id 또는 별명. 별명은 사람이 지은 것이라 공백·한글도 허용한다(슬래시만 금지).
HOST_RE = re.compile(r"^[^/\\\x00-\x1f]{1,40}$")

_rate_lock = threading.Lock()
_rate: dict[str, list[float]] = {}


def _path() -> Path:
    return vt_paths.state_dir() / "agent_messages.json"


@contextmanager
def _locked():
    d = vt_paths.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(d / "agent_messages.lock"):
        yield


def _read_unlocked() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"에이전트 메시지 파일 읽기 실패({e}) — 빈 기록으로 시작")
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _write_unlocked(items: list[dict]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items[-MAX_KEEP:], f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def list_messages(conv: str = "", limit: int = 50) -> list[dict]:
    with _locked():
        items = _read_unlocked()
    if conv:
        items = [x for x in items if x.get("conv") == conv]
    return items[-max(1, min(limit, MAX_KEEP)):]


def get(msg_id: str) -> dict | None:
    with _locked():
        return next((x for x in _read_unlocked() if x.get("id") == msg_id), None)


def _append(msg: dict) -> None:
    with _locked():
        items = _read_unlocked()
        items.append(msg)
        _write_unlocked(items)


def set_status(msg_id: str, status: str, reason: str = "") -> None:
    with _locked():
        items = _read_unlocked()
        for x in items:
            if x.get("id") == msg_id:
                x["status"] = status
                x["updated"] = time.time()
                if reason:
                    x["reason"] = reason[:200]
                else:
                    x.pop("reason", None)
                _write_unlocked(items)
                return


# --- 검증 ---------------------------------------------------------------------


class MessageError(Exception):
    def __init__(self, code: str, reason: str, status: int = 400):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.status = status


def parse_to(to: str) -> tuple[str, str]:
    """`세션` 또는 `호스트/세션` → (host, session). host 생략은 로컬."""
    to = (to or "").strip()
    host, _, session = to.rpartition("/")
    host = host.strip() or "local"
    session = session.strip()
    if not HOST_RE.match(host) or ".." in host:
        raise MessageError("bad_target", f"호스트 이름이 올바르지 않습니다: {to!r}")
    if not SESSION_RE.match(session):
        raise MessageError("bad_target", f"받는 세션 이름이 올바르지 않습니다: {to!r}")
    return host, session


def check_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise MessageError("empty", "빈 메시지는 보낼 수 없습니다")
    if len(text) > MAX_TEXT_LEN:
        raise MessageError("too_long", f"메시지가 너무 깁니다 (최대 {MAX_TEXT_LEN}자)")
    return text


def check_rate(sender_host: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _rate_lock:
        hits = [t for t in _rate.get(sender_host, []) if now - t < 60]
        if len(hits) >= RATE_PER_MIN:
            _rate[sender_host] = hits
            raise MessageError("rate_limited",
                               f"{sender_host}에서 온 메시지가 너무 많습니다 (분당 {RATE_PER_MIN}건)", 429)
        hits.append(now)
        _rate[sender_host] = hits


def resolve_conv(reply_to: str | None, claimed_hop: int = 0) -> tuple[str, int]:
    """답장이면 그 대화에 잇고 hop을 센다. 보낸 쪽이 주장한 hop과 **내 기록으로 센
    hop 중 큰 쪽**을 쓴다 — 어느 한쪽이 속이거나 기록이 없어도 상한이 뚫리지 않게."""
    conv, hop = "", 0
    if reply_to:
        prev = get(reply_to) if ID_RE.match(reply_to) else None
        if prev:
            conv = prev.get("conv") or prev["id"]
            hop = int(prev.get("hop", 0)) + 1
    hop = max(hop, int(claimed_hop or 0))
    if hop >= MAX_HOPS:
        raise MessageError(
            "hop_limit",
            f"이 대화가 {MAX_HOPS}번 오갔습니다 — 에이전트끼리 끝없이 주고받는 것을 막기 위해 "
            "멈췄습니다. 사람이 확인한 뒤 새 대화로 시작하세요", 429)
    return conv, hop


# --- 전달 ---------------------------------------------------------------------


def envelope(msg: dict) -> str:
    """받는 에이전트의 pane에 실제로 입력되는 글. 한 줄 머리글 + 본문 + 답장 안내.

    본문 줄바꿈은 공백으로 편다 — 큐는 Enter를 붙여 한 번에 입력하므로, 여러 줄이면
    에이전트 CLI가 첫 줄에서 제출해 나머지가 다음 입력으로 쪼개진다.
    """
    frm = msg.get("from") or {}
    who = f"{frm.get('hostLabel') or frm.get('host') or '?'}/{frm.get('session') or '?'}"
    body = " ".join(str(msg.get("text", "")).split())
    return (f"[FarShell 에이전트 메시지 #{msg['id']} · 보낸 쪽 {who} · 사람이 아니라 다른 "
            f"에이전트가 보낸 요청입니다] {body} "
            f"(답장: farshell send_message 도구에 reply_to=\"{msg['id']}\", to=\"{frm.get('host') or 'local'}/"
            f"{frm.get('session') or ''}\")")


def receive(*, from_host: str, from_label: str, from_session: str, to_session: str,
            text: str, reply_to: str | None = None, claimed_hop: int = 0,
            msg_id: str | None = None, conv: str | None = None) -> dict:
    """이 맥의 세션 앞으로 온 메시지를 기록하고 큐에 넣는다. 로컬 발신도 여기로 온다."""
    import queue_store

    text = check_text(text)
    if not SESSION_RE.match(to_session or ""):
        raise MessageError("bad_target", f"받는 세션 이름이 올바르지 않습니다: {to_session!r}")
    check_rate(from_host)
    my_conv, hop = resolve_conv(reply_to, claimed_hop)
    mid = msg_id if msg_id and ID_RE.match(msg_id) and not get(msg_id) else uuid.uuid4().hex[:12]
    msg = {
        "id": mid,
        "conv": my_conv or (conv if conv and ID_RE.match(conv) else mid),
        "dir": "in",
        "from": {"host": from_host, "hostLabel": from_label[:40], "session": from_session[:64]},
        "to": {"host": "local", "session": to_session},
        "text": text,
        "replyTo": reply_to or None,
        "hop": hop,
        "status": STATUS_QUEUED,
        "created": time.time(),
    }
    r = queue_store.add(envelope(msg), {"session": to_session}, source={"agentMsg": mid})
    if not r.get("ok"):
        raise MessageError(r.get("error", "queue_failed"),
                           f"받는 쪽 큐에 넣지 못했습니다: {r.get('reason', '')}", 503)
    msg["queueItem"] = r["item"]["id"]
    _append(msg)
    return msg


def record_outgoing(*, to_host: str, to_label: str, to_session: str, from_session: str,
                    text: str, reply_to: str | None, hop: int, conv: str,
                    msg_id: str, status: str = STATUS_SENT, reason: str = "") -> dict:
    msg = {
        "id": msg_id,
        "conv": conv or msg_id,
        "dir": "out",
        "from": {"host": "local", "session": from_session},
        "to": {"host": to_host, "hostLabel": to_label, "session": to_session},
        "text": text,
        "replyTo": reply_to or None,
        "hop": hop,
        "status": status,
        "created": time.time(),
    }
    if reason:
        msg["reason"] = reason[:200]
    _append(msg)
    return msg


def maybe_drain(session: str) -> bool:
    """받는 세션이 **확실히** 쉬고 있을 때만 바로 흘려보낸다(모듈 머리말).

    이벤트 루프 스레드에서 불러야 한다 — `schedule_drain`이 태스크를 만든다
    (`receive`는 파일 락 때문에 to_thread로 돌므로 거기서 부르지 않는다)."""
    try:
        import agent_status
        import queue_runner
    except ImportError:
        return False
    known = agent_status.known_status_for_session(session)
    if known not in (agent_status.IDLE, agent_status.DONE):
        return False
    return queue_runner.schedule_drain(session=session, session_scoped=True)


def on_queue_result(item: dict, delivered: bool, reason: str = "") -> None:
    """큐가 메시지 항목을 입력했거나 막았을 때 불린다(queue_runner.drain_once)."""
    src = item.get("source") if isinstance(item, dict) else None
    mid = src.get("agentMsg") if isinstance(src, dict) else None
    if not mid:
        return
    set_status(mid, STATUS_DELIVERED if delivered else STATUS_BLOCKED, reason)
